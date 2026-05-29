from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from logger import get_logger
from models.announcement import Announcement, get_astana_time
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.review_ticket import (
    ReviewTicket, ReviewHistory, ReviewActionEnum, TicketStatusEnum,
)
from services.notifications import NotificationsService

logger = get_logger(__name__)


async def execute_publication(publication_id: UUID, session: AsyncSession) -> None:
    """Single publication path. Makes the announcement visible (PRIMARY),
    marks the publication PUBLISHED, notifies operators. No MinIO here."""
    # Lock the publication row so the synchronous publish-now path and the
    # background worker cannot both pass the SCHEDULED check and double-publish.
    # If the worker holds the lock, this read blocks until it commits, then we
    # re-read status=PUBLISHED below and return (idempotent).
    pub = (await session.execute(
        select(AnnouncementPublication)
        .where(AnnouncementPublication.id == publication_id)
        .with_for_update()
    )).scalar_one_or_none()
    if pub is None or pub.status != PublicationStatusEnum.SCHEDULED:
        logger.info("execute_publication skipped: id={id} status={s}",
                    id=publication_id, s=getattr(pub, "status", None))
        return

    ann = (await session.execute(
        select(Announcement).where(Announcement.id == pub.announcement_id)
    )).scalar_one()

    now = get_astana_time()
    if pub.kind == PublicationKindEnum.PRIMARY:
        ann.is_hidden = False
        ann.published_at = now
        await _finalize_ticket_and_email(ann, session)

    pub.status = PublicationStatusEnum.PUBLISHED
    pub.executed_at = now
    await session.flush()

    try:
        notifications = NotificationsService(settings.EVENTS_WEBHOOK_URL)
        if pub.kind == PublicationKindEnum.PRIMARY:
            await notifications.publish_new_announcement(ann)
        else:
            await notifications.publish_repeat_announcement(ann)
    except Exception as e:
        logger.error("Publication notify failed: {err}", err=e)


async def _finalize_ticket_and_email(ann: Announcement, session: AsyncSession) -> None:
    """Mark the originating ticket PUBLISHED and its email DONE."""
    ticket = (await session.execute(
        select(ReviewTicket).where(ReviewTicket.announcement_id == ann.id)
    )).scalar_one_or_none()
    if ticket is None:
        return
    ticket.status = TicketStatusEnum.PUBLISHED
    session.add(ReviewHistory(
        ticket_id=ticket.id, action=ReviewActionEnum.PUBLISHED,
        actor_name="system", comment="Опубликовано по расписанию",
    ))
    await session.execute(
        update(IncomingEmail).where(IncomingEmail.id == ticket.email_id)
        .values(status=EmailStatusEnum.DONE)
    )
