"""
Announcements service.
Handles business logic for announcement CRUD operations and status management.

Commit ownership rule:
    Service methods only flush (so the router controls the transaction boundary).
    Every caller (router) is responsible for calling db.commit() and db.refresh().
"""

from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from uuid import UUID


def _utcnow() -> datetime:
    """Timezone-aware UTC now (replaces deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, func, literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models.announcement import Announcement, AnnouncementCategoryEnum
from models.read_status import AnnouncementReadStatus
from schemas import AnnouncementCreate, AnnouncementUpdate
from logger import get_logger

logger = get_logger(__name__)


class AnnouncementsService:
    """Service for managing announcements."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- CREATE ----------

    async def create_announcement(
        self,
        announcement_data: AnnouncementCreate,
        user_id: UUID,
        username: str,
        email: str,
    ) -> Announcement:
        announcement = Announcement(
            title=announcement_data.title,
            category=announcement_data.category,
            product=announcement_data.product,
            text=announcement_data.text,
            instruction=announcement_data.instruction,
            topic=announcement_data.topic,
            resource_link=announcement_data.resource_link,
            script_kz=announcement_data.script_kz,
            script_ru=announcement_data.script_ru,
            attachment_path=announcement_data.attachment_path,
            is_hidden=False,
            created_by=user_id,
            name=username,
            email=email,
        )

        self.db.add(announcement)
        await self.db.flush()

        logger.log("EVENT",
            f"Announcement created: ID={announcement.id}, Title='{announcement.title}', "
            f"Created by User={user_id}, Hidden={announcement.is_hidden}"
        )
        return announcement

    # ---------- READ ----------

    async def get_announcement_by_id(self, announcement_id) -> Optional[Announcement]:
        if isinstance(announcement_id, str):
            try:
                announcement_id = UUID(announcement_id)
            except (ValueError, TypeError):
                logger.warning(f"Invalid UUID format for announcement_id: {announcement_id}")
                return None

        result = await self.db.execute(
            select(Announcement).filter(Announcement.id == announcement_id)
        )
        announcement = result.scalar_one_or_none()
        if not announcement:
            logger.debug(f"Announcement not found: ID={announcement_id}")
        return announcement

    def month_to_range(self, month: str) -> tuple[datetime, datetime]:
        try:
            start = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

        if start.month == 12:
            end = datetime(start.year + 1, 1, 1)
        else:
            end = datetime(start.year, start.month + 1, 1)

        return start, end

    async def get_all_announcements(
        self,
        user_id: UUID,
        month: Optional[str] = None,
    ) -> List[Tuple[Announcement, Optional[bool]]]: 
        ars = aliased(AnnouncementReadStatus)

        stmt = (
            select(Announcement, func.coalesce(ars.is_read, literal(False)).label("is_read"))
            .outerjoin(
                ars,
                and_(
                    ars.announcement_id == Announcement.id,
                    ars.user_id == user_id,
                ),
            )
            .filter(Announcement.is_hidden.is_(False))
        )

        if month:
            start, end = self.month_to_range(month)
            stmt = stmt.filter(
                Announcement.created_at >= start,
                Announcement.created_at < end,
            )

        stmt = stmt.order_by(
            Announcement.updated_at.desc(),
        )

        result = await self.db.execute(stmt)
        rows = result.all()

        logger.log("EVENT", f"Retrieved {len(rows)} announcements (user={user_id}, month={month})")
        return rows

    async def get_unread_announcements(self, user_id: UUID):
        ars = aliased(AnnouncementReadStatus)

        stmt = (
            select(Announcement, ars.is_read)
            .outerjoin(
                ars,
                and_(
                    ars.announcement_id == Announcement.id,
                    ars.user_id == user_id,
                ),
            )
            .where(
                and_(
                    Announcement.is_hidden.is_(False),
                    or_(ars.user_id.is_(None), ars.is_read.is_(False)),
                )
            )
            .order_by(Announcement.created_at.desc())
        )

        result = await self.db.execute(stmt)
        return result.all()

    # ---------- UPDATE ----------

    async def update_announcement(
        self,
        announcement: Announcement,
        update_data: AnnouncementUpdate,
    ) -> Tuple[Announcement, Dict[str, Tuple]]:
        changes: Dict[str, Tuple] = {}

        update_fields = {
            "title": announcement.title,
            "category": announcement.category,
            "ai_category": announcement.ai_category,
            "product": announcement.product,
            "text": announcement.text,
            "instruction": announcement.instruction,
            "topic": announcement.topic,
            "resource_link": announcement.resource_link,
            "script_kz": announcement.script_kz,
            "script_ru": announcement.script_ru,
            "attachment_path": announcement.attachment_path,
        }

        for field, current_value in update_fields.items():
            new_value = getattr(update_data, field, None)
            if new_value is not None and new_value != current_value:
                changes[field] = (current_value, new_value)
                setattr(announcement, field, new_value)

        # Auto-promote category to CHANGE if something was modified
        # and the caller did not explicitly change the category.
        if changes and "category" not in changes:
            changes["category"] = (
                AnnouncementCategoryEnum.NEW,
                AnnouncementCategoryEnum.CHANGE,
            )
            announcement.category = AnnouncementCategoryEnum.CHANGE

        announcement.updated_at = _utcnow()
        await self.db.flush()

        if changes:
            logger.log("EVENT",
                f"Announcement updated: ID={announcement.id}, "
                f"Fields changed: {list(changes.keys())}"
            )

        return announcement, changes

    # ---------- REVOKE / PUBLISH ----------

    async def revoke_announcement(
        self,
        announcement: Announcement,
        revoked_link: str,
    ) -> Announcement:
        announcement.is_revoked = True
        announcement.revoked_link = revoked_link
        announcement.category = AnnouncementCategoryEnum.REVOKED
        announcement.updated_at = _utcnow()

        await self.db.flush()

        logger.log("EVENT",
            f"Announcement revoked: ID={announcement.id}, "
            f"Title='{announcement.title}', Link='{revoked_link}'"
        )
        return announcement

    async def publish_announcement(self, announcement: Announcement) -> Announcement:
        announcement.is_hidden = False
        announcement.updated_at = _utcnow()
        await self.db.flush()
        logger.log("EVENT",
            f"Announcement published: ID={announcement.id}, "
            f"Title='{announcement.title}'"
        )
        return announcement

    # ---------- READ STATUS ----------

    async def mark_as_read(
        self,
        user_id,
        announcement_id,
        is_read: bool = True,
    ) -> AnnouncementReadStatus:
        stmt = select(AnnouncementReadStatus).filter(
            and_(
                AnnouncementReadStatus.user_id == user_id,
                AnnouncementReadStatus.announcement_id == announcement_id,
            )
        )
        result = await self.db.execute(stmt)
        status = result.scalar_one_or_none()

        if not status:
            status = AnnouncementReadStatus(
                user_id=user_id,
                announcement_id=announcement_id,
                is_read=is_read,
            )
            self.db.add(status)
            logger.log("EVENT",
                f"New read status created: User={user_id}, "
                f"Announcement={announcement_id}, IsRead={is_read}"
            )
        else:
            status.is_read = is_read
            logger.log("EVENT",
                f"Read status updated: User={user_id}, "
                f"Announcement={announcement_id}, IsRead={is_read}"
            )

        await self.db.flush()
        return status

    # ---------- DELETE ----------

    async def delete_announcement(self, announcement_id: UUID) -> None:
        stmt = select(Announcement).where(Announcement.id == announcement_id)
        result = await self.db.execute(stmt)
        announcement = result.scalar_one_or_none()

        if announcement:
            await self.db.delete(announcement)
            await self.db.flush()



            logger.log("EVENT", f"Announcement {announcement_id} deleted from DB")


# Backward-compatible alias for any code still importing the old name
AnnouncementService = AnnouncementsService
