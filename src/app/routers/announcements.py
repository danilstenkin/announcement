"""
Announcements router.
Handles all announcement CRUD operations and status management.
"""

import mimetypes
from typing import List, Optional
from urllib.parse import quote, unquote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from dependencies import get_db
from dependencies.minio import get_minio_client
from logger import get_logger
from schemas import (
    AnnouncementCreate,
    AnnouncementListResponse,
    AnnouncementMonthResponse,
    AnnouncementResponse,
    AnnouncementRevokeRequest,
    AnnouncementUpdate,
    MarkAsReadRequest,
    ReadStatusResponse,
    RevokeResponse,
    UnreadCounterResponse,
    UpdateResponse,
)
from services import AnnouncementsService, NotificationsService

from sqlalchemy import desc, func, select

from models.announcement import Announcement

logger = get_logger(__name__)

router = APIRouter(prefix="/announcements", tags=["announcements"])


# ==================== SHARED DEPENDENCY ====================


class CurrentUser:
    """Extracts and groups the three user identity headers shared by every endpoint.

    X-User-Id is required — all endpoints operate on behalf of a specific user.
    Missing header -> 401 Unauthorized.
    """

    def __init__(
        self,
        x_user_id: Optional[UUID] = Header(None, alias="X-User-Id"),
        x_username: Optional[str] = Header(None, alias="X-User-Name"),
        x_email: Optional[str] = Header(None, alias="X-User-Email"),
    ):
        if x_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-User-Id header",
            )
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
    attachment: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementResponse:

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
                    detail=(
                        f"File size exceeds maximum allowed size of "
                        f"{settings.MAX_FILE_SIZE / 1024 / 1024}MB"
                    ),
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
                    detail=(
                        f"File extension .{file_ext} not allowed. "
                        f"Allowed: {', '.join(settings.ALLOWED_FILE_EXTENSIONS)}"
                    ),
                )

            logger.info(f"File validation passed: {attachment.filename}")
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
        )

        announcement = await service.create_announcement(
            announcement_data,
            user_id=current_user.id,
            username=current_user.username,
            email=current_user.email,
        )
        logger.info(
            f"Created announcement (flushed) ID={announcement.id} "
            f"by user {current_user.id}"
        )

        # ---------- 3) Upload file to MinIO (if any), then set path in DB ----------
        if attachment and file_content:
            logger.info(
                f"Starting file upload to MinIO for announcement {announcement.id}..."
            )
            minio_client = get_minio_client()

            object_key = await minio_client.upload_file(
                file_content=file_content,
                filename=attachment.filename,
                announcement_id=str(announcement.id),
            )
            logger.info(f"File uploaded successfully: {object_key}")

            announcement.attachment_path = object_key

        # ---------- 4) Commit ONCE ----------
        await db.commit()
        await db.refresh(announcement)

        # ---------- 5) Notify AFTER commit (only if published) ----------
        if not announcement.is_hidden:
            notifications_service = NotificationsService(settings.EVENTS_WEBHOOK_URL)
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
                logger.info(f"Cleaned up MinIO object after DB failure: {object_key}")
            except Exception as cleanup_err:
                logger.warning(
                    f"Failed to cleanup MinIO object {object_key}: {cleanup_err}"
                )

        logger.error(f"Error creating announcement: {e}")
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
    month_expr = func.date_trunc("month", Announcement.created_at).label("month")

    stmt = (
        select(
            func.to_char(month_expr, "YYYY-MM").label("key"),
            month_expr,
            func.count(Announcement.id).label("count"),
        )
        .group_by(month_expr)
        .order_by(desc(month_expr))
    )

    rows = (await db.execute(stmt)).all()

    result = []
    for key, _month_dt, count in rows:
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
    service = AnnouncementsService(db)

    logger.debug(f"User {current_user.id} fetched announcements list")

    rows = await service.get_all_announcements(user_id=current_user.id, month=month)

    return [
        AnnouncementListResponse.from_announcement(announcement, is_read)
        for announcement, is_read in rows
    ]


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
        for announcement, _is_read in rows
    ]

    return UnreadCounterResponse(
        unread_count=len(announcements_response),
        announcement=announcements_response,
    )


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

        quoted = quote(filename)

        return StreamingResponse(
            obj,
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"
            },
        )

    except Exception as e:
        logger.error(f"Failed to download attachment for {announcement_id}: {e}")
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
) -> UpdateResponse:
    service = AnnouncementsService(db)
    announcement = await service.get_announcement_by_id(announcement_id)

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    updated_announcement, changes = await service.update_announcement(
        announcement, update_data
    )

    await db.commit()
    await db.refresh(updated_announcement)

    if changes:
        notifications_service = NotificationsService(settings.EVENTS_WEBHOOK_URL)
        await notifications_service.publish_updated_announcement(updated_announcement)

    return UpdateResponse(
        id=updated_announcement.id,
        title=updated_announcement.title,
        changes=changes,
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
) -> RevokeResponse:
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

    notifications_service = NotificationsService(settings.EVENTS_WEBHOOK_URL)
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
    service = AnnouncementsService(db)
    announcement = await service.get_announcement_by_id(announcement_id)

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    if announcement.is_hidden:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    status_record = await service.mark_as_read(
        current_user.id, announcement_id, read_data.is_read
    )

    await db.commit()
    await db.refresh(status_record)

    return ReadStatusResponse(
        user_id=status_record.user_id,
        announcement_id=status_record.announcement_id,
        is_read=status_record.is_read,
    )


# ==================== DELETE ANNOUNCEMENT ====================


@router.delete(
    "/{announcement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an announcement",
    description="Permanently deletes an announcement and its associated data (read statuses, etc.)",
)
async def delete_announcement(
    announcement_id: UUID,
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    service = AnnouncementsService(db)

    announcement = await service.get_announcement_by_id(announcement_id)
    if not announcement:
        logger.warning(
            f"User {current_user.id} tried to delete non-existent "
            f"announcement {announcement_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Announcement {announcement_id} not found",
        )

    logger.info(
        f"User {current_user.username} ({current_user.id}) is deleting "
        f"announcement {announcement_id}"
    )

    # Сохраняем данные до удаления для уведомления
    title = announcement.title
    category = announcement.category
    ann_id = announcement.id

    await service.delete_announcement(announcement_id)
    await db.commit()

    notifications_service = NotificationsService(settings.EVENTS_WEBHOOK_URL)
    # Создаём временный объект для уведомления
    from types import SimpleNamespace
    deleted = SimpleNamespace(id=ann_id, title=title, category=category)

    if announcement.attachment_path:
        minio = get_minio_client()
        await minio.delete_file(announcement.attachment_path)
        
    await notifications_service.publish_deleted_announcement(deleted)

    return None
