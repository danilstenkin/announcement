from datetime import timedelta

import pytest
from sqlalchemy import select

from models.announcement import Announcement, get_astana_time
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)
from models.review_ticket import ReviewTicket, TicketStatusEnum


@pytest.fixture(autouse=True)
def no_minio(monkeypatch):
    async def _noop(ticket, session, announcement_id):
        return
    monkeypatch.setattr(
        "services.announcement_factory._transfer_attachments", _noop
    )


async def _seed(session, confirmed, publish_at=None):
    email = IncomingEmail(outlook_id=f"m-{get_astana_time().timestamp()}", subject="s",
                          body="b", received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.IN_REVIEW,
                     title="Title", body="Body", source="ServiceDesk",
                     publish_confirmed=confirmed, publish_at=publish_at)
    session.add(t)
    await session.flush()
    return t


async def test_approve_requires_confirmed_date(client, session):
    t = await _seed(session, confirmed=False)
    r = await client.post(f"/auto-announce/tickets/{t.id}/approve")
    assert r.status_code == 400, r.text


async def test_approve_schedules_future_publication(client, session):
    when = get_astana_time() + timedelta(days=1)
    t = await _seed(session, confirmed=True, publish_at=when)
    r = await client.post(f"/auto-announce/tickets/{t.id}/approve")
    assert r.status_code == 200, r.text

    await session.refresh(t)
    assert t.status == TicketStatusEnum.AGREED
    assert t.announcement_id is not None

    ann = (await session.execute(
        select(Announcement).where(Announcement.id == t.announcement_id)
    )).scalar_one()
    assert ann.is_hidden is True          # scheduled, not yet visible
    assert ann.published_at is None

    pub = (await session.execute(
        select(AnnouncementPublication).where(
            AnnouncementPublication.announcement_id == ann.id
        )
    )).scalar_one()
    assert pub.kind == PublicationKindEnum.PRIMARY
    assert pub.status == PublicationStatusEnum.SCHEDULED
    assert pub.publish_at == t.publish_at


async def test_approve_twice_is_rejected(client, session):
    when = get_astana_time() + timedelta(days=1)
    t = await _seed(session, confirmed=True, publish_at=when)

    r1 = await client.post(f"/auto-announce/tickets/{t.id}/approve")
    assert r1.status_code == 200, r1.text

    r2 = await client.post(f"/auto-announce/tickets/{t.id}/approve")
    assert r2.status_code == 400, r2.text

    await session.refresh(t)
    pubs = (await session.execute(
        select(AnnouncementPublication).where(
            AnnouncementPublication.announcement_id == t.announcement_id
        )
    )).scalars().all()
    assert len(pubs) == 1
