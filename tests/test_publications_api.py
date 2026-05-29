import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    async def _noop(self, announcement):
        return
    monkeypatch.setattr(
        "services.publication.NotificationsService.publish_repeat_announcement", _noop, raising=False
    )


async def _ann(session, hidden=False):
    a = Announcement(title="t", category=AnnouncementCategoryEnum.NEW, text="b",
                     is_hidden=hidden, created_by=uuid.UUID(int=0))
    session.add(a)
    await session.flush()
    return a


async def _pub(session, ann_id, kind, status, publish_at=None):
    p = AnnouncementPublication(
        announcement_id=ann_id, kind=kind, status=status,
        publish_at=publish_at or get_astana_time(),
    )
    session.add(p)
    await session.flush()
    return p


async def test_history_lists_ordered(client, session):
    a = await _ann(session)
    await _pub(session, a.id, PublicationKindEnum.PRIMARY, PublicationStatusEnum.PUBLISHED)
    await _pub(session, a.id, PublicationKindEnum.REPEAT, PublicationStatusEnum.SCHEDULED,
               publish_at=get_astana_time() + timedelta(days=1))
    r = await client.get(f"/announcements/{a.id}/publications")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 2
    assert data[0]["kind"] == "PRIMARY"
    assert data[1]["kind"] == "REPEAT"


async def test_republish_future_schedules(client, session):
    a = await _ann(session)
    when = (get_astana_time() + timedelta(days=1))
    r = await client.post(f"/announcements/{a.id}/republish", json={"publish_at": when.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"
    pubs = (await session.execute(
        select(AnnouncementPublication).where(AnnouncementPublication.announcement_id == a.id)
    )).scalars().all()
    assert len(pubs) == 1
    assert pubs[0].kind == PublicationKindEnum.REPEAT
    assert pubs[0].status == PublicationStatusEnum.SCHEDULED


async def test_republish_now_publishes(client, session):
    a = await _ann(session)
    when = (get_astana_time() - timedelta(minutes=1))
    r = await client.post(f"/announcements/{a.id}/republish", json={"publish_at": when.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "republished"
    pub = (await session.execute(
        select(AnnouncementPublication).where(AnnouncementPublication.announcement_id == a.id)
    )).scalar_one()
    assert pub.status == PublicationStatusEnum.PUBLISHED
    assert pub.executed_at is not None


async def test_republish_unknown_announcement_404(client, session):
    r = await client.post(f"/announcements/{uuid.uuid4()}/republish",
                          json={"publish_at": get_astana_time().isoformat()})
    assert r.status_code == 404, r.text


async def test_reschedule_only_when_scheduled(client, session):
    a = await _ann(session)
    p = await _pub(session, a.id, PublicationKindEnum.REPEAT, PublicationStatusEnum.SCHEDULED,
                   publish_at=get_astana_time() + timedelta(days=1))
    new_when = (get_astana_time() + timedelta(days=3)).replace(microsecond=0)
    r = await client.patch(f"/publications/{p.id}", json={"publish_at": new_when.isoformat()})
    assert r.status_code == 200, r.text
    await session.refresh(p)
    assert p.publish_at.replace(microsecond=0) == new_when

    p2 = await _pub(session, a.id, PublicationKindEnum.PRIMARY, PublicationStatusEnum.PUBLISHED)
    r2 = await client.patch(f"/publications/{p2.id}", json={"publish_at": new_when.isoformat()})
    assert r2.status_code == 400, r2.text


async def test_cancel_row(client, session):
    a = await _ann(session)
    p = await _pub(session, a.id, PublicationKindEnum.REPEAT, PublicationStatusEnum.SCHEDULED,
                   publish_at=get_astana_time() + timedelta(days=1))
    r = await client.post(f"/publications/{p.id}/cancel")
    assert r.status_code == 200, r.text
    await session.refresh(p)
    assert p.status == PublicationStatusEnum.CANCELED
    assert p.canceled_at is not None

    r2 = await client.post(f"/publications/{p.id}/cancel")
    assert r2.status_code == 400, r2.text
