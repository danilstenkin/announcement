from sqlalchemy.ext.asyncio import AsyncSession

from logger import get_logger
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.email_attachment import EmailAttachment
from pipeline.context import PipelineContext

logger = get_logger(__name__)


async def save_email(ctx: PipelineContext, session: AsyncSession) -> None:
    record = IncomingEmail(
        outlook_id=ctx.message_id,
        subject=ctx.subject,
        body=ctx.body,
        sender_email=ctx.sender_email,
        received_at=ctx.received_at,
        status=EmailStatusEnum.PROCESSING,
        original_html_key=ctx.raw_html,
    )
    session.add(record)
    try:
        await session.flush()
        ctx.email_db_id = record.id
        logger.info(
            "Saved email id={id} source={source}",
            id=record.id, source=ctx.source,
        )
    except Exception as e:
        await session.rollback()
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            ctx.is_duplicate = True
            logger.warning("Duplicate message_id={mid}", mid=ctx.message_id)
        else:
            raise
        return

    # Сохраняем метаданные вложений в БД (файлы уже в MinIO — загружены воркером)
    for att in ctx.attachments:
        attachment = EmailAttachment(
            email_id=ctx.email_db_id,
            filename=att["filename"],
            object_key=att["object_key"],       # ключ из MinIO
            file_size=att.get("size"),
            content_type=att.get("content_type"),
        )
        session.add(attachment)

    if ctx.attachments:
        await session.flush()
        logger.info(
            "Saved {count} attachment records for email_id={eid}",
            count=len(ctx.attachments), eid=ctx.email_db_id,
        )
