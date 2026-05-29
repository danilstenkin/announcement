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
def no_minio_no_http(monkeypatch):
    async def _noop_transfer(ticket, session, announcement_id):
        return
    async def _noop_notify(self, announcement):
        return
    monkeypatch.setattr("services.announcement_factory._transfer_attachments", _noop_transfer)
    monkeypatch.setattr(
        "services.publication.NotificationsService.publish_new_announcement", _noop_notify
    )


async def _seed(session):
    email = IncomingEmail(outlook_id=f"m-{get_astana_time().timestamp()}", subject="s",
                          body="b", received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.IN_REVIEW,
                     title="Title", body="Body", source="ServiceDesk")
    session.add(t)
    await session.flush()
    return t, email


async def test_publish_now_makes_visible_immediately(client, session):
    t, email = await _seed(session)
    r = await client.post(f"/auto-announce/tickets/{t.id}/publish")
    assert r.status_code == 200, r.text

    await session.refresh(t)
    assert t.status == TicketStatusEnum.PUBLISHED
    assert t.announcement_id is not None

    ann = (await session.execute(
        select(Announcement).where(Announcement.id == t.announcement_id)
    )).scalar_one()
    assert ann.is_hidden is False
    assert ann.published_at is not None

    pub = (await session.execute(
        select(AnnouncementPublication).where(AnnouncementPublication.announcement_id == ann.id)
    )).scalar_one()
    assert pub.kind == PublicationKindEnum.PRIMARY
    assert pub.status == PublicationStatusEnum.PUBLISHED
    assert pub.executed_at is not None

    await session.refresh(email)
    assert email.status == EmailStatusEnum.DONE


async def test_publish_now_requires_title(client, session):
    email = IncomingEmail(outlook_id=f"m2-{get_astana_time().timestamp()}", subject="s",
                          body="b", received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.IN_REVIEW, title=None)
    session.add(t)
    await session.flush()
    r = await client.post(f"/auto-announce/tickets/{t.id}/publish")
    assert r.status_code == 400, r.text


async def test_publish_now_on_agreed_uses_existing_publication(client, session):
    when = get_astana_time() + timedelta(days=1)
    email = IncomingEmail(outlook_id=f"m3-{get_astana_time().timestamp()}", subject="s",
                          body="b", received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.IN_REVIEW,
                     title="Title", body="Body", source="ServiceDesk",
                     publish_confirmed=True, publish_at=when)
    session.add(t)
    await session.flush()

    # approve → AGREED, creates hidden announcement + SCHEDULED PRIMARY pub
    r_approve = await client.post(f"/auto-announce/tickets/{t.id}/approve")
    assert r_approve.status_code == 200, r_approve.text
    await session.refresh(t)
    assert t.status == TicketStatusEnum.AGREED
    original_ann_id = t.announcement_id
    assert original_ann_id is not None

    # publish-now should reuse the existing announcement + publication
    r_pub = await client.post(f"/auto-announce/tickets/{t.id}/publish")
    assert r_pub.status_code == 200, r_pub.text

    await session.refresh(t)
    assert t.announcement_id == original_ann_id  # unchanged: no new announcement
    assert t.status == TicketStatusEnum.PUBLISHED

    pubs = (await session.execute(
        select(AnnouncementPublication).where(
            AnnouncementPublication.announcement_id == original_ann_id,
            AnnouncementPublication.kind == PublicationKindEnum.PRIMARY,
        )
    )).scalars().all()
    assert len(pubs) == 1
    assert pubs[0].status == PublicationStatusEnum.PUBLISHED

    ann = (await session.execute(
        select(Announcement).where(Announcement.id == original_ann_id)
    )).scalar_one()
    assert ann.is_hidden is False
