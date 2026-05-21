from ast import Await
from datetime import datetime, timezone
import os
from uuid import UUID

from dependencies.minio import get_minio_client

from sqlalchemy import select,update
from sqlalchemy.ext.asyncio import AsyncSession
from models.announcement import Announcement, AnnouncementCategoryEnum
from pipeline.context import PipelineContext

from logger import get_logger
from models import attachments
from models.email_attachment import EmailAttachment
from models.incoming_emails import EmailStatusEnum, IncomingEmail


from services.notifications import NotificationsService
from config import settings
from services import notifications

logger = get_logger(__name__)

SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")
SYSTEM_USER_NAME = "AiAnons"
SYSTEM_USER_EMAIL = "aiNews@fortebanks.com"

async def publish(ctx: PipelineContext, session: AsyncSession):
    if not ctx.ai_email:
        logger.warning("Skipping publish: ai_email is empty")
        return

    if not ctx.ai_title:
        logger.warning("Skipping publish: ai_title is empty (GPT analysis may have failed)")
        return

    announcment = Announcement(
        title=ctx.ai_title,
        category=AnnouncementCategoryEnum.NEW,
        text=ctx.ai_email,
        script_ru=ctx.script_ru,
        script_kz=ctx.script_kz,
        is_hidden=False,
        created_by=SYSTEM_USER_ID,
        name=SYSTEM_USER_NAME,
        email=SYSTEM_USER_EMAIL,
        is_ai=True,
        source="Servise"
    )

    session.add(announcment)
    await session.flush()

    ctx.ai_emails_db_id = announcment.id
    ctx.auto_publish = True

    # await _transfer_attachments(ctx,session, announcment.id)
    if ctx.email_db_id:
        await session.execute(
            update(IncomingEmail).where(IncomingEmail.id == ctx.email_db_id).values(status=EmailStatusEnum.DONE)
        )

    try:
        notifications = NotificationsService(settings.EVENTS_WEBHOOK_URL)
        await notifications.publish_new_announcement(announcment)
        logger.info("EVENT SENDER AIIIII")
    except Exception as e:
        logger.error("Failed to send SSE notification: {err}", err=e)



async def _transfer_attachments(
        ctx: PipelineContext,
        session: AsyncSession,
        announcement_id: UUID,
) -> None:

    if not ctx.email_db_id:
        return

    result = await session.execute(
        select(EmailAttachment).where(EmailAttachment.email_id == ctx.email_db_id)
    )

    email_attachments = result.scalars().all()

    if not email_attachments:
        return

    minio = get_minio_client()
    transferred = 0

    for ea in email_attachments:
        try:
            timestapm = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safename = os.path.basename(ea.filename)
            new_key = f"announcement/{announcement_id}/{timestapm}_{safename}"

            await minio.copy_file(ea.object_key, new_key)

            attachment = attachments.Attachments(
                announcement_id=announcement_id,
                filename=ea.filename,
                object_key=new_key,
                file_size=ea.file_size,
                content_type=ea.content_type,
            )
            session.add(attachment)

            await minio.delete_file(ea.object_key)
            await session.delete(ea)

            transferred += 1
        except Exception as err:
            logger.error(
                "Failed to transfer arrachment {f}: {err}",
                f=ea.filename, err = err
            )
    if transferred:
        await session.flush()
        logger.info(
            "transferred {count} attachments to announcrement {aid}",
            count=transferred, aid=announcement_id
        )
