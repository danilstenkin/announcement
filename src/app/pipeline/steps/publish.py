from re import A
from turtle import title
from unicodedata import category
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from models.announcement import Announcement, AnnouncementCategoryEnum
from pipeline.context import PipelineContext

from logger import get_logger

logger = get_logger(__name__)

SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")
SYSTEM_USER_NAME = "AiAnons"
SYSTEM_USER_EMAIL = "aiNews@fortebanks.com"

async def publish(ctx: PipelineContext, session: AsyncSession):
    if not ctx.ai_email:
        logger.warning("Skipping publish: ai_email is empty")
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
    )

    session.add(announcment)
    await session.flush()

    ctx.ai_emails_db_id = announcment.id
    ctx.auto_publish = True

    logger.log(
        "EVENT",
        f"Auto-Publish"
    )
