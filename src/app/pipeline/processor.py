from sqlalchemy import update

from config import settings
from pipeline.context import PipelineContext
from models.incoming_emails import EmailStatusEnum, IncomingEmail
from models.review_ticket import ReviewTicket, ReviewHistory, TicketStatusEnum, ReviewActionEnum

from dependencies.database import async_session

from pipeline.steps.save_email import save_email #1
from pipeline.steps.ai_analysis import analyze #2
from pipeline.steps.publish import publish

from services.notifications import NotificationsService
from logger import get_logger

logger = get_logger(__name__)


def should_create_review_ticket(ctx: PipelineContext) -> bool:
    return settings.FORCE_REVIEW_TICKETS or ctx.status == EmailStatusEnum.RED


async def run_pipeline(ctx: PipelineContext):
    async with async_session() as session:
        try:
            await save_email(ctx, session)
            if ctx.is_duplicate:
                logger.info("Duplicate email, skipping pipeline for uid={uid}", uid=ctx.uid)
                return ctx
            if ctx.email_db_id is None:
                logger.warning("save_email returned no email_db_id for uid={uid}", uid=ctx.uid)
                return ctx
            await session.commit()
            logger.info("Email saved, email_db_id={eid} uid={uid}", eid=ctx.email_db_id, uid=ctx.uid)
        except Exception:
            logger.exception("save_email failed for uid={uid}", uid=ctx.uid)
            await session.rollback()
            return ctx
        try:
            await analyze(ctx, session)
            if should_create_review_ticket(ctx):
                if settings.FORCE_REVIEW_TICKETS and ctx.status != EmailStatusEnum.RED:
                    logger.info(
                        "FORCE_REVIEW_TICKETS enabled, creating review ticket for email={eid}",
                        eid=ctx.email_db_id,
                    )
                else:
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
                    ai_category=ctx.ai_category,
                    source=ctx.source,
                    recommended_publish_at=ctx.recommended_publish_at,
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
                try:
                    notifications = NotificationsService(settings.EVENTS_WEBHOOK_URL)
                    await notifications.publish_ticket_event(
                        event_type="CREATED", ticket_id=str(ticket.id),
                        actor_name="system", title=ctx.ai_title, status="PENDING_REVIEW",
                    )
                except Exception as e:
                    logger.error("Failed to send ticket CREATED event: {err}", err=e)
            else:
                await publish(ctx, session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return ctx
