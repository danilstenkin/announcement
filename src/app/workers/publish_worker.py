import asyncio
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from dependencies.database import async_session
from logger import get_logger
from models.announcement import get_astana_time
from models.publication import AnnouncementPublication, PublicationStatusEnum
from services.publication import execute_publication

logger = get_logger(__name__)


async def publish_due_once(session: AsyncSession, now: datetime) -> int:
    """Publish all SCHEDULED publications due at/before `now`. Returns the count
    of successful publishes. Per-row failures are logged and skipped."""
    pub_ids = (await session.execute(
        select(AnnouncementPublication.id)
        .where(AnnouncementPublication.status == PublicationStatusEnum.SCHEDULED)
        .where(AnnouncementPublication.publish_at <= now)
        .order_by(AnnouncementPublication.publish_at)
        .with_for_update(skip_locked=True)
    )).scalars().all()

    count = 0
    for pub_id in pub_ids:
        try:
            await execute_publication(pub_id, session)
            count += 1
        except Exception:
            logger.exception("Failed to publish publication id={id}", id=pub_id)
    return count


async def run_publish_scheduler() -> None:
    """Background loop: every PUBLISH_POLL_INTERVAL seconds, publish due rows."""
    interval = settings.PUBLISH_POLL_INTERVAL
    logger.info("Publish scheduler started, interval={i}s", i=interval)
    while True:
        try:
            async with async_session() as session:
                async with session.begin():
                    n = await publish_due_once(session, get_astana_time())
                if n:
                    logger.info("Published {n} scheduled announcements", n=n)
        except asyncio.CancelledError:
            logger.info("Publish scheduler cancelled")
            raise
        except Exception:
            logger.exception("Publish scheduler tick crashed")
        await asyncio.sleep(interval)
