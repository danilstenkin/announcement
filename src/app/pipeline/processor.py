from pipeline.context import PipelineContext

from dependencies.database import async_session
from pipeline.steps.save_email import save_email


async def run_pipeline(ctx: PipelineContext):
    try:
        async with async_session() as session:
            await save_email(ctx, session)
            await session.commit()
    except Exception:
        await session.rollback()
        raise
