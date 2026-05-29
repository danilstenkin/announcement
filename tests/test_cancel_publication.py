from datetime import timedelta

from sqlalchemy import select

from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)
from models.review_ticket import ReviewTicket, TicketStatusEnum
import uuid


async def _seed_agreed(session):
    """Seed a ticket in AGREED state with a hidden announcement + SCHEDULED PRIMARY pub."""
    email = IncomingEmail(outlook_id=f"m-{get_astana_time().timestamp()}", subject="s",
                          body="b", received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    ann = Announcement(title="t", category=AnnouncementCategoryEnum.NEW, text="b",
                       is_hidden=True, created_by=uuid.UUID(int=0))
    session.add(ann)
    await session.flush()
    pub = AnnouncementPublication(
        announcement_id=ann.id, kind=PublicationKindEnum.PRIMARY,
        publish_at=get_astana_time() + timedelta(days=1),
        status=PublicationStatusEnum.SCHEDULED,
    )
    session.add(pub)
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.AGREED,
                     title="t", announcement_id=ann.id, publish_confirmed=True,
                     publish_at=pub.publish_at)
    session.add(t)
    await session.flush()
    return t, ann, pub


async def test_cancel_publication(client, session):
    t, ann, pub = await _seed_agreed(session)
    r = await client.post(f"/auto-announce/tickets/{t.id}/cancel-publication")
    assert r.status_code == 200, r.text

    await session.refresh(pub); await session.refresh(ann); await session.refresh(t)
    assert pub.status == PublicationStatusEnum.CANCELED
    assert pub.canceled_at is not None
    assert ann.is_hidden is True            # NOT published
    assert ann.published_at is None
    assert t.status == TicketStatusEnum.IN_REVIEW


async def test_cancel_when_nothing_scheduled_400(client, session):
    # ticket with no scheduled publication
    email = IncomingEmail(outlook_id=f"m2-{get_astana_time().timestamp()}", subject="s",
                          body="b", received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.IN_REVIEW, title="t")
    session.add(t)
    await session.flush()
    r = await client.post(f"/auto-announce/tickets/{t.id}/cancel-publication")
    assert r.status_code == 400, r.text
