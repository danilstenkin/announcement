import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)
from workers.publish_worker import publish_due_once


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    async def _noop_new(self, announcement):
        return
    async def _noop_repeat(self, announcement):
        return
    monkeypatch.setattr("services.publication.NotificationsService.publish_new_announcement", _noop_new)
    monkeypatch.setattr("services.publication.NotificationsService.publish_repeat_announcement", _noop_repeat, raising=False)


async def _ann(session):
    a = Announcement(title="t", category=AnnouncementCategoryEnum.NEW, text="b",
                     is_hidden=True, created_by=uuid.UUID(int=0))
    session.add(a)
    await session.flush()
    return a


async def _pub(session, ann_id, status, publish_at, kind=PublicationKindEnum.PRIMARY):
    p = AnnouncementPublication(announcement_id=ann_id, kind=kind, status=status, publish_at=publish_at)
    session.add(p)
    await session.flush()
    return p


async def test_publishes_only_due_scheduled(session):
    now = get_astana_time()
    a = await _ann(session)
    due = await _pub(session, a.id, PublicationStatusEnum.SCHEDULED, now - timedelta(minutes=1))
    future = await _pub(session, a.id, PublicationStatusEnum.SCHEDULED, now + timedelta(hours=1))
    already = await _pub(session, a.id, PublicationStatusEnum.PUBLISHED, now - timedelta(minutes=5))

    count = await publish_due_once(session, now)
    assert count == 1

    await session.refresh(due); await session.refresh(future); await session.refresh(already)
    assert due.status == PublicationStatusEnum.PUBLISHED
    assert future.status == PublicationStatusEnum.SCHEDULED
    assert already.status == PublicationStatusEnum.PUBLISHED  # unchanged


async def test_one_failure_does_not_block_others(session, monkeypatch):
    now = get_astana_time()
    a = await _ann(session)
    p1 = await _pub(session, a.id, PublicationStatusEnum.SCHEDULED, now - timedelta(minutes=2))
    p2 = await _pub(session, a.id, PublicationStatusEnum.SCHEDULED, now - timedelta(minutes=1))

    real_ids = {p1.id, p2.id}
    bad_id = p1.id

    async def fake_execute(pub_id, sess):
        if pub_id == bad_id:
            raise RuntimeError("boom")
        pub = (await sess.execute(
            select(AnnouncementPublication).where(AnnouncementPublication.id == pub_id)
        )).scalar_one()
        pub.status = PublicationStatusEnum.PUBLISHED

    monkeypatch.setattr("workers.publish_worker.execute_publication", fake_execute)

    count = await publish_due_once(session, now)
    # one failed, one succeeded
    assert count == 1
    await session.refresh(p1); await session.refresh(p2)
    assert p2.status == PublicationStatusEnum.PUBLISHED
    assert p1.status == PublicationStatusEnum.SCHEDULED  # failed, unchanged


async def test_partial_mutation_failure_is_rolled_back(session, monkeypatch):
    from sqlalchemy import select as _select
    now = get_astana_time()
    a = await _ann(session)
    bad = await _pub(session, a.id, PublicationStatusEnum.SCHEDULED, now - timedelta(minutes=2))
    good = await _pub(session, a.id, PublicationStatusEnum.SCHEDULED, now - timedelta(minutes=1))

    bad_id = bad.id

    async def fake_execute(pub_id, sess):
        # mutate the row, THEN fail — must be rolled back by the per-row savepoint
        pub = (await sess.execute(
            _select(AnnouncementPublication).where(AnnouncementPublication.id == pub_id)
        )).scalar_one()
        pub.status = PublicationStatusEnum.PUBLISHED
        await sess.flush()
        if pub_id == bad_id:
            raise RuntimeError("boom after mutation")

    monkeypatch.setattr("workers.publish_worker.execute_publication", fake_execute)

    count = await publish_due_once(session, now)
    assert count == 1
    await session.refresh(bad); await session.refresh(good)
    assert bad.status == PublicationStatusEnum.SCHEDULED   # rolled back
    assert good.status == PublicationStatusEnum.PUBLISHED   # committed by savepoint
