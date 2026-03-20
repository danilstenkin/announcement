"""
Announcements router.
Handles all announcement CRUD operations and status management.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status, Query, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from typing import List, Optional
from app.db import get_db
from app.models import Announcement, AnnouncementReadStatus
from schemas import (
    AnnouncementCreate,
    AnnouncementUpdate,
    AnnouncementResponse,
    AnnouncementMonthResponse,
    AnnouncementListResponse,
    AnnouncementRevokeRequest,
    RevokeResponse,
    UpdateResponse,
    MarkAsReadRequest,
    ReadStatusResponse,
    UnreadCounterResponse,
)
from app.services.announcements_service import AnnouncementsService
from services.notifications_service import NotificationsService
from redis_client import get_redis
from minio_client import get_minio_client
from config import settings
from logger import get_logger
import redis
from uuid import UUID

from fastapi.responses import StreamingResponse
from urllib.parse import quote, unquote
import mimetypes

logger = get_logger(__name__)

router = APIRouter(prefix="/announcements", tags=["announcements"])


# ==================== SHARED DEPENDENCY ====================


class CurrentUser:
    """Extracts and groups the three user identity headers shared by every endpoint."""

    def __init__(
        self,
        x_user_id: UUID = Header(None, alias="X-User-Id"),
        x_username: str = Header(None, alias="X-User-Name"),
        x_email: Optional[str] = Header(None, alias="X-User-Email"),
    ):
        self.id = x_user_id
        self.username = unquote(x_username) if x_username else None
        self.email = x_email


# ==================== CREATE ANNOUNCEMENT ====================


@router.post(
    "",
    response_model=AnnouncementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create announcement",
    description="Create a new announcement with optional file attachment. TRAINING_CENTER only.",
)
async def create_announcement(
    current_user: CurrentUser = Depends(CurrentUser),
    title: str = Form(...),
    category: str = Form(...),
    text: str = Form(...),
    script_kz: Optional[str] = Form(None),
    script_ru: Optional[str] = Form(None),
    product: Optional[str] = Form(None),
    instruction: Optional[str] = Form(None),
    topic: Optional[str] = Form(None),
    resource_link: Optional[str] = Form(None),
    #is_hidden: bool = Form(True),
    attachment: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
) -> AnnouncementResponse:
    """
    Create a new announcement with optional file attachment.

    Transaction rules:
    - DB commit happens once, at the end.
    - If MinIO upload fails -> DB rollback (announcement is not saved).
    - If DB commit fails after MinIO upload -> attempt MinIO cleanup.
    """
    service = AnnouncementsService(db)

    file_content: Optional[bytes] = None
    object_key: Optional[str] = None

    try:
        # ---------- 1) Validate & read file (if any) ----------
        if attachment:
            logger.info(f"File upload detected: {attachment.filename}")

            file_content = await attachment.read()
            file_size = len(file_content)
            logger.info(f"File size: {file_size / 1024:.2f} KB")

            if file_size > settings.MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File size exceeds maximum allowed size of {settings.MAX_FILE_SIZE / 1024 / 1024}MB",
                )

            file_ext = (
                attachment.filename.rsplit(".", 1)[-1].lower()
                if attachment.filename and "." in attachment.filename
                else ""
            )
            logger.info(f"File extension: .{file_ext}")

            if file_ext not in settings.ALLOWED_FILE_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File extension .{file_ext} not allowed. Allowed: {', '.join(settings.ALLOWED_FILE_EXTENSIONS)}",
                )

            logger.info(f"✅ File validation passed: {attachment.filename}")
        else:
            logger.info("No file attachment in request")

        # ---------- 2) Create announcement in DB (flush inside service) ----------
        announcement_data = AnnouncementCreate(
            title=title,
            category=category,
            text=text,
            script_kz=script_kz,
            script_ru=script_ru,
            product=product,
            instruction=instruction,
            topic=topic,
            resource_link=resource_link,
            # attachment_path is set later after MinIO upload (if any)
        )

        announcement = await service.create_announcement(
            announcement_data,
            user_id=current_user.id,
            username=current_user.username,
            email=current_user.email,
        )
        logger.info(f"Created announcement (flushed) ID={announcement.id} by user {current_user.id}")

        # ---------- 3) Upload file to MinIO (if any), then set path in DB ----------
        if attachment and file_content:
            logger.info(f"Starting file upload to MinIO for announcement {announcement.id}...")
            minio_client = get_minio_client()

            object_key = await minio_client.upload_file(
                file_content=file_content,
                filename=attachment.filename,
                announcement_id=str(announcement.id),
            )
            logger.info(f"✅ File uploaded successfully: {object_key}")

            announcement.attachment_path = object_key

        # ---------- 4) Commit ONCE ----------
        await db.commit()
        await db.refresh(announcement)

        # ---------- 5) Notify AFTER commit (only if published) ----------
        if not announcement.is_hidden:
            notifications_service = NotificationsService(redis_client)
            await notifications_service.publish_new_announcement(announcement)

        # ---------- 6) Return response ----------
        return AnnouncementResponse.model_validate(announcement)

    except HTTPException:
        await db.rollback()
        raise

    except Exception as e:
        await db.rollback()

        if object_key:
            try:
                minio_client = get_minio_client()
                minio_client.client.remove_object(minio_client.bucket_name, object_key)
                logger.info(f"🧹 Cleaned up MinIO object after DB failure: {object_key}")
            except Exception as cleanup_err:
                logger.warning(f"Failed to cleanup MinIO object {object_key}: {cleanup_err}", exc_info=True)

        logger.error(f"Error creating announcement: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create announcement: {str(e)}",
        )


# ==================== GET ANNOUNCEMENTS MONTHS ====================


@router.get(
    "/months",
    response_model=List[AnnouncementMonthResponse],
    summary="Get announcement months",
    description="Return months that contain announcements (for left menu grouping).",
)
async def get_announcement_months(
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
) -> List[AnnouncementMonthResponse]:
    filters = []

    month_expr = func.date_trunc("month", Announcement.created_at).label("month")

    stmt = (
        select(
            func.to_char(month_expr, "YYYY-MM").label("key"),  # "2026-02"
            month_expr,
            func.count(Announcement.id).label("count"),
        )
        .where(*filters)
        .group_by(month_expr)
        .order_by(desc(month_expr))
    )

    rows = (await db.execute(stmt)).all()

    result = []
    for key, month_dt, count in rows:
        result.append(
            {
                "key": key,
                "title": key,
                "count": count,
            }
        )

    return result


# ==================== GET ANNOUNCEMENTS LIST ====================


@router.get(
    "",
    response_model=List[AnnouncementListResponse],
    summary="Get announcements list",
    description="Get announcements list. Different views for TRAINING_CENTER and EMPLOYEE.",
)
async def get_announcements(
    month: Optional[str] = Query(None, description="Filter by month in YYYY-MM"),
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
) -> List[AnnouncementListResponse]:
    """
    Get announcements list.

    - All announcements
    - Hidden announcements shown first
    - Then by newest first
    - Can see creator info
    """
    service = AnnouncementsService(db)

    logger.debug(f"User {current_user.id} fetched announcements list")

    # Single query with LEFT JOIN for read status — no N+1
    rows = await service.get_all_announcements(user_id=current_user.id, month=month)

    return [
        AnnouncementListResponse.from_announcement(announcement, is_read)
        for announcement, is_read in rows
    ]


# ==================== GET SINGLE ANNOUNCEMENT ====================


@router.get(
    "/{announcement_id}",
    response_model=AnnouncementResponse,
    summary="Get announcement by ID",
    description="Get a specific announcement by ID.",
)
async def get_announcement(
    announcement_id: UUID,
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementResponse:
    """Get a specific announcement by ID."""
    service = AnnouncementsService(db)
    announcement = await service.get_announcement_by_id(announcement_id)

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    response = AnnouncementResponse.model_validate(announcement)
    data = response.model_dump()

    data["attachment_path"] = (
        f"/announcements/{announcement.id}/attachment"
        if announcement.attachment_path
        else None
    )

    return AnnouncementResponse(**data)


# ==================== DOWNLOAD ATTACHMENT ====================


@router.get(
    "/{announcement_id}/attachment",
    summary="Download announcement attachment",
    description="Download attachment file for an announcement.",
)
async def download_attachment(
    announcement_id: UUID,
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    service = AnnouncementsService(db)
    announcement = await service.get_announcement_by_id(announcement_id)

    if not announcement or not announcement.attachment_path:
        raise HTTPException(status_code=404, detail="Attachment not found")

    try:
        minio_client = get_minio_client()
        obj = minio_client.client.get_object(
            minio_client.bucket_name,
            announcement.attachment_path,
        )

        filename = announcement.attachment_path.split("/")[-1]
        media_type, _ = mimetypes.guess_type(filename)
        media_type = media_type or "application/octet-stream"

        # корректно для кириллицы
        quoted = quote(filename)

        return StreamingResponse(
            obj,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )

    except Exception as e:
        logger.error(f"Failed to download attachment for {announcement_id}: {e}", exc_info=True)
        raise HTTPException(status_code=404, detail="Attachment not found")


# ==================== UPDATE ANNOUNCEMENT ====================


@router.put(
    "/{announcement_id}",
    response_model=UpdateResponse,
    summary="Update announcement",
    description="Update an announcement. TRAINING_CENTER only. Only active (non-revoked) announcements.",
)
async def update_announcement(
    announcement_id: UUID,
    update_data: AnnouncementUpdate,
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
) -> UpdateResponse:
    """
    Update an announcement.

    - Only TRAINING_CENTER users can update
    - Cannot update revoked announcements
    - Changes are tracked and returned
    - Employees receive notification: "Внимание! Изменения в анонсе «{title}»" if announcement is published
    - If is_hidden changes from True to False, employees are notified of the new announcement
    """
    service = AnnouncementsService(db)
    announcement = await service.get_announcement_by_id(announcement_id)

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    updated_announcement, changes = await service.update_announcement(announcement, update_data)

    await db.commit()
    await db.refresh(updated_announcement)

    if changes:
        notifications_service = NotificationsService(redis_client)
        await notifications_service.publish_updated_announcement(updated_announcement)

    # # Send notifications based on visibility status
    # notifications_service = NotificationsService(redis_client)

    # # If transitioning from hidden (draft) to visible (published)
    # if was_hidden and not updated_announcement.is_hidden:
    #     await notifications_service.publish_new_announcement(updated_announcement)
    # # If already published, notify about changes
    # elif not updated_announcement.is_hidden:
    #     await notifications_service.publish_updated_announcement(updated_announcement)

    return UpdateResponse(
        id=updated_announcement.id, title=updated_announcement.title, changes=changes
    )


# ==================== REVOKE ANNOUNCEMENT ====================


@router.post(
    "/{announcement_id}/revoke",
    response_model=RevokeResponse,
    summary="Revoke announcement",
    description="Revoke an announcement. TRAINING_CENTER only.",
)
async def revoke_announcement(
    announcement_id: UUID,
    revoke_data: AnnouncementRevokeRequest,
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
) -> RevokeResponse:
    """
    Revoke an announcement.

    - Only TRAINING_CENTER users can revoke
    - Cannot revoke already revoked announcements
    - Requires a link to the actual FAQ or resolution
    - Employees receive notification: "Внимание! Анонс «{title}» больше не действует"
    - In the list, revoked announcements show:
      - Title and revoked_link only
      - No checkbox to mark as read
      - Text is hidden
    """
    service = AnnouncementsService(db)
    announcement = await service.get_announcement_by_id(announcement_id)

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    revoked = await service.revoke_announcement(announcement, revoke_data.revoked_link)

    await db.commit()
    await db.refresh(revoked)

    notifications_service = NotificationsService(redis_client)
    await notifications_service.publish_revoked_announcement(revoked)

    return RevokeResponse(
        id=revoked.id,
        title=revoked.title,
        is_revoked=revoked.is_revoked,
        revoked_link=revoked.revoked_link,
    )


# ==================== MARK AS READ ====================


@router.post(
    "/{announcement_id}/read",
    response_model=ReadStatusResponse,
    summary="Mark announcement as read",
    description="Mark an announcement as read/unread. EMPLOYEE only.",
)
async def mark_as_read(
    announcement_id: UUID,
    read_data: MarkAsReadRequest,
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
) -> ReadStatusResponse:
    """
    Mark an announcement as read or unread.

    - Only EMPLOYEE users can mark as read
    - is_read=True: mark as read
    - is_read=False: mark as unread
    """
    service = AnnouncementsService(db)
    announcement = await service.get_announcement_by_id(announcement_id)

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    # Employees cannot interact with hidden announcements
    if announcement.is_hidden:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    status_record = await service.mark_as_read(current_user.id, announcement_id, read_data.is_read)

    await db.commit()
    await db.refresh(status_record)

    return ReadStatusResponse(
        user_id=status_record.user_id,
        announcement_id=status_record.announcement_id,
        is_read=status_record.is_read,
    )


# ==================== GET UNREAD COUNTER ====================


@router.get(
    "/me/unread",
    response_model=UnreadCounterResponse,
)
async def get_unread_counter(
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
) -> UnreadCounterResponse:
    service = AnnouncementsService(db)

    rows = await service.get_unread_announcements(current_user.id)

    announcements_response = [
        AnnouncementListResponse.from_announcement(announcement, is_read=False)
        for announcement, is_read in rows
    ]

    return UnreadCounterResponse(
        unread_count=len(announcements_response),
        announcement=announcements_response,
    )


# ==================== DELETE ANNOUNCEMENT ====================


@router.delete(
    "/{announcement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an announcement",
    description="Permanently deletes an announcement and its associated data (read statuses, etc.)"
)
async def delete_announcement(
    announcement_id: UUID,
    current_user: CurrentUser = Depends(CurrentUser), 
    db: AsyncSession = Depends(get_db),
):
    """
    Deletes an announcement. 
    Note: Based on your models, related read statuses will be deleted automatically 
    due to ondelete="CASCADE".
    """
    service = AnnouncementsService(db)

    # 1. Сначала проверяем существование анонса
    announcement = await service.get_announcement_by_id(announcement_id)
    if not announcement:
        logger.warning(f"User {current_user.id} tried to delete non-existent announcement {announcement_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    logger.info(f"User {current_user.username} ({current_user.id}) is deleting announcement {announcement_id}")

    await service.delete_announcement(announcement_id)

    await db.commit()

    return None 