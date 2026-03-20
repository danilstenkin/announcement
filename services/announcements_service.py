"""
Announcements service.
Handles business logic for announcement CRUD operations and status management.

Commit ownership rule:
    Service methods only flush (so the router controls the transaction boundary).
    Every caller (router) is responsible for calling db.commit() and db.refresh().
"""

from datetime import datetime
from typing import List, Dict, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased
from fastapi import HTTPException
from uuid import UUID

from app.models import (
    Announcement,
    AnnouncementReadStatus,
    AnnouncementCategoryEnum,
)
from schemas import (
    AnnouncementCreate,
    AnnouncementUpdate,
    AnnouncementResponse,
    AnnouncementListResponse,
)
from logger import get_logger

logger = get_logger(__name__)


class AnnouncementsService:
    """Service for managing announcements."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def create_announcement(
        self, announcement_data: AnnouncementCreate, user_id: UUID, username: str, email: str
    ) -> Announcement:
        """
        Create a new announcement.

        Flushes to get the DB-assigned ID without committing.
        The caller is responsible for committing the transaction.

        Args:
            announcement_data: Announcement creation data
            user_id: ID of the user creating the announcement
            username: Display name of the creator
            email: Email of the creator

        Returns:
            Flushed (not yet committed) Announcement instance
        """
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

        logger.info(
            f"Announcement created: ID={announcement.id}, Title='{announcement.title}', "
            f"Created by User={user_id}, Hidden={announcement.is_hidden}"
        )

        return announcement

    async def get_announcement_by_id(self, announcement_id: str) -> Optional[Announcement]:
        """
        Get announcement by ID.

        Args:
            announcement_id: ID of announcement (UUID string or UUID object)

        Returns:
            Announcement or None if not found
        """
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
        """
        Convert a 'YYYY-MM' string to a half-open datetime range [start, end).

        Args:
            month: Month string in 'YYYY-MM' format

        Returns:
            Tuple of (start_of_month, start_of_next_month)
        """
        try:
            start = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

        if start.month == 12:
            end = datetime(start.year + 1, 1, 1)
        else:
            end = datetime(start.year, start.month + 1, 1)

        return start, end

    async def update_announcement(
        self, announcement: Announcement, update_data: AnnouncementUpdate
    ) -> Tuple[Announcement, Dict[str, Tuple]]:
        """
        Update an announcement and track changes.

        Flushes the change without committing.
        The caller is responsible for committing the transaction.

        Category promotion rule:
            If any field changed and the caller did not explicitly pass a new
            category, the category is automatically set to CHANGE. This signals
            that the content has been modified since it was first published.

        Args:
            announcement: Announcement to update
            update_data: Update data

        Returns:
            Tuple of (updated announcement, dict of changes {field: (old_value, new_value)})
        """
        changes = {}

        update_fields = {
            "title": announcement.title,
            "category": announcement.category,
            "product": announcement.product,
            "text": announcement.text,
            "instruction": announcement.instruction,
            "topic": announcement.topic,
            "resource_link": announcement.resource_link,
            "script_kz": announcement.script_kz,
            "script_ru": announcement.script_ru,
            "attachment_path": announcement.attachment_path,
            "is_hidden": announcement.is_hidden,
        }

        for field, current_value in update_fields.items():
            new_value = getattr(update_data, field, None)
            if new_value is not None and new_value != current_value:
                changes[field] = (current_value, new_value)
                setattr(announcement, field, new_value)

        # If no explicit category change was requested and there are other changes,
        # automatically set category to CHANGE to signal that it has been modified.
        if changes and "category" not in changes:
            changes["category"] = (AnnouncementCategoryEnum.NEW, AnnouncementCategoryEnum.CHANGE)
            announcement.category = AnnouncementCategoryEnum.CHANGE

        announcement.updated_at = datetime.utcnow()
        await self.db.flush()

        if changes:
            logger.info(
                f"Announcement updated: ID={announcement.id}, Fields changed: {list(changes.keys())}"
            )

        return announcement, changes

    async def revoke_announcement(
        self, announcement: Announcement, revoked_link: str
    ) -> Announcement:
        """
        Revoke an announcement.

        Flushes the change without committing.
        The caller is responsible for committing the transaction.

        Args:
            announcement: Announcement to revoke
            revoked_link: Link to FAQ or resolution

        Returns:
            Revoked announcement (not yet committed)
        """
        announcement.is_revoked = True
        announcement.revoked_link = revoked_link
        announcement.category = AnnouncementCategoryEnum.REVOKED
        announcement.updated_at = datetime.utcnow()

        await self.db.flush()

        logger.info(
            f"Announcement revoked: ID={announcement.id}, Title='{announcement.title}', "
            f"Link='{revoked_link}'"
        )

        return announcement

    async def publish_announcement(self, announcement: Announcement) -> Announcement:
        """
        Publish a hidden announcement.

        Flushes the change without committing.
        The caller is responsible for committing the transaction.

        Args:
            announcement: Hidden announcement to publish

        Returns:
            Published announcement (not yet committed)
        """
        announcement.is_hidden = False
        announcement.updated_at = datetime.utcnow()

        await self.db.flush()

        logger.info(
            f"Announcement published: ID={announcement.id}, Title='{announcement.title}'"
        )

        return announcement

    async def get_all_announcements(
        self,
        user_id: UUID,
        month: Optional[str] = None,
    ) -> List[Tuple[Announcement, Optional[bool]]]:
        """
        Return all announcements with the read status for the given user.

        Uses a single LEFT OUTER JOIN on AnnouncementReadStatus to avoid
        N+1 queries (one query total instead of one per announcement).

        Order: hidden first, then newest first.
        If month is provided, results are filtered to that calendar month.

        Args:
            user_id: ID of the requesting user (used to resolve read status)
            month: Optional 'YYYY-MM' filter

        Returns:
            List of (Announcement, is_read) tuples.
            is_read is None when no read-status row exists for the user.
        """
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
        )

        if month:
            start, end = self.month_to_range(month)
            stmt = stmt.filter(
                Announcement.created_at >= start,
                Announcement.created_at < end,
            )

        stmt = stmt.order_by(Announcement.is_hidden.desc(), Announcement.updated_at.desc())

        result = await self.db.execute(stmt)
        rows = result.all()

        logger.debug(f"Retrieved {len(rows)} announcements (user={user_id}, month={month})")
        return rows

    async def mark_as_read(
        self, user_id: str, announcement_id: str, is_read: bool = True
    ) -> AnnouncementReadStatus:
        """
        Mark an announcement as read/unread for a user.

        Flushes the change without committing.
        The caller is responsible for committing the transaction.

        Args:
            user_id: ID of user (UUID string)
            announcement_id: ID of announcement (UUID string)
            is_read: Whether to mark as read (True) or unread (False)

        Returns:
            AnnouncementReadStatus record (not yet committed)
        """
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
            logger.debug(
                f"New read status created: User={user_id}, Announcement={announcement_id}, "
                f"IsRead={is_read}"
            )
        else:
            status.is_read = is_read
            logger.debug(
                f"Read status updated: User={user_id}, Announcement={announcement_id}, "
                f"IsRead={is_read}"
            )

        await self.db.flush()

        return status

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
        rows = result.all()

        return rows

    async def delete_announcement(self, announcement_id: UUID) -> None:
        """
        Permanently deletes an announcement from the database.
        """
        stmt = select(Announcement).where(Announcement.id == announcement_id)
        result = await self.db.execute(stmt)
        announcement = result.scalar_one_or_none()

        if announcement:
            await self.db.delete(announcement)
            # Мы не делаем здесь commit, так как он обычно делается в роутере
            await self.db.flush() 
            logger.info(f"Announcement {announcement_id} deleted from DB")