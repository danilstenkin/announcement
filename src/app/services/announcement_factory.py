import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies.minio import get_minio_client
from logger import get_logger
from models.announcement import Announcement, AnnouncementCategoryEnum
from models.attachments import Attachments
from models.email_attachment import EmailAttachment
from models.review_ticket import ReviewTicket

logger = get_logger(__name__)

SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")
SYSTEM_USER_NAME = "AiAnons"
SYSTEM_USER_EMAIL = "aiNews@fortebanks.com"


async def create_announcement_from_ticket(
    ticket: ReviewTicket, session: AsyncSession, *, hidden: bool
) -> Announcement:
    """Create an Announcement from a review ticket and transfer its email's
    attachments. Sets ticket.announcement_id. Caller commits."""
    ann = Announcement(
        title=ticket.title,
        category=AnnouncementCategoryEnum.NEW,
        text=ticket.body or "",
        script_ru=ticket.script_ru,
        script_kz=ticket.script_kz,
        is_hidden=hidden,
        created_by=SYSTEM_USER_ID,
        name=SYSTEM_USER_NAME,
        email=SYSTEM_USER_EMAIL,
        is_ai=True,
        source=ticket.source,
    )
    session.add(ann)
    await session.flush()
    ticket.announcement_id = ann.id
    await _transfer_attachments(ticket, session, ann.id)
    return ann


async def _transfer_attachments(ticket: ReviewTicket, session: AsyncSession, announcement_id: UUID) -> None:
    result = await session.execute(
        select(EmailAttachment).where(
            EmailAttachment.email_id == ticket.email_id,
            EmailAttachment.is_inline.is_(False),  # inline-картинки в анонс не переносим
        )
    )
    email_atts = result.scalars().all()
    if not email_atts:
        return
    minio = get_minio_client()
    for ea in email_atts:
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            new_key = f"announcements/{announcement_id}/{ts}_{os.path.basename(ea.filename)}"
            await minio.copy_file(ea.object_key, new_key)
            session.add(Attachments(
                announcement_id=announcement_id,
                filename=ea.filename,
                object_key=new_key,
                file_size=ea.file_size,
                content_type=ea.content_type,
            ))
        except Exception as err:
            logger.error("Failed to transfer attachment {f}: {err}", f=ea.filename, err=err)
