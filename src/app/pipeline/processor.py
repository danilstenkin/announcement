from sqlalchemy import update

from pipeline.context import PipelineContext
from models.incoming_emails import EmailStatusEnum, IncomingEmail

from dependencies.database import async_session

from pipeline.steps.save_email import save_email #1
from pipeline.steps.ai_analysis import analyze #2
from pipeline.steps.publish import publish

from logger import get_logger

logger = get_logger(__name__)


async def run_pipeline(ctx: PipelineContext):
    async with async_session() as session:
        try:
            await save_email(ctx, session)
            if ctx.is_duplicate or ctx.email_db_id is None:
                return ctx
            await session.commit()
        except Exception:
            await session.rollback()
            return ctx
        try:
            await analyze(ctx, session)
            if ctx.status == EmailStatusEnum.RED:
                logger.info("Default prompt → status RED, skipping publish for email={eid}", eid=ctx.email_db_id)
                await session.execute(
                    update(IncomingEmail).where(IncomingEmail.id == ctx.email_db_id).values(status=EmailStatusEnum.RED)
                )
            else:
                await publish(ctx, session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return ctx
