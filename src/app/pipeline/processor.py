from pipeline.context import PipelineContext

from dependencies.database import async_session

from pipeline.steps.save_email import save_email #1
from pipeline.steps.ai_analysis import analyze #2
from pipeline.steps.publish import publish



async def run_pipeline(ctx: PipelineContext):
    async with async_session() as session:
        try:
            await save_email(ctx, session)
            if ctx.is_duplicate or ctx.email_db_id is None:
                return ctx
            await analyze(ctx, session)
            await publish(ctx, session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return ctx
