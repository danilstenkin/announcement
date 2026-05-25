from sqlalchemy import update

from pipeline.context import PipelineContext
from models.incoming_emails import EmailStatusEnum, IncomingEmail
from models.review_ticket import ReviewTicket, ReviewHistory, TicketStatusEnum, ReviewActionEnum

from dependencies.database import async_session

from pipeline.steps.save_email import save_email #1
from pipeline.steps.ai_analysis import analyze #2
from pipeline.steps.publish import publish

from services.ticket_events import ticket_events
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
                logger.info("Default prompt → status RED, creating review ticket for email={eid}", eid=ctx.email_db_id)
                await session.execute(
                    update(IncomingEmail).where(IncomingEmail.id == ctx.email_db_id).values(status=EmailStatusEnum.RED)
                )
                ticket = ReviewTicket(
                    email_id=ctx.email_db_id,
                    status=TicketStatusEnum.PENDING_REVIEW,
                    title=ctx.ai_title,
                    body=ctx.ai_email,
                    script_ru=ctx.script_ru,
                    script_kz=ctx.script_kz,
                    ai_summary=ctx.ai_summary,
                    source=ctx.source,
                )
                session.add(ticket)
                await session.flush()

                history = ReviewHistory(
                    ticket_id=ticket.id,
                    action=ReviewActionEnum.CREATED,
                    actor_name="system",
                    comment=f"Автоматически создан из письма от {ctx.sender_email}",
                )
                session.add(history)
                logger.info("Created review ticket {tid} for email {eid}", tid=ticket.id, eid=ctx.email_db_id)
                await ticket_events.publish(
                    event_type="CREATED", ticket_id=str(ticket.id),
                    actor_name="system", title=ctx.ai_title, status="PENDING_REVIEW",
                )
            else:
                await publish(ctx, session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return ctx
