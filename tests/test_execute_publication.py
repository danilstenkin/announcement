import uuid

import pytest
from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time
from models.publication import AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum
from services.publication import execute_publication


@pytest.fixture
def captured_events(monkeypatch):
    events = []

    async def fake_new(self, announcement):
        events.append(("NEW", announcement.id))

    async def fake_repeat(self, announcement):
        events.append(("REPEAT", announcement.id))

    monkeypatch.setattr(
        "services.publication.NotificationsService.publish_new_announcement", fake_new
    )
    monkeypatch.setattr(
        "services.publication.NotificationsService.publish_repeat_announcement",
        fake_repeat, raising=False,
    )
    return events


async def _make(session, kind, hidden=True):
    ann = Announcement(title="t", category=AnnouncementCategoryEnum.NEW, text="b",
                       is_hidden=hidden, created_by=uuid.UUID(int=0))
    session.add(ann)
    await session.flush()
    pub = AnnouncementPublication(
        announcement_id=ann.id, kind=kind,
        publish_at=get_astana_time(), status=PublicationStatusEnum.SCHEDULED,
    )
    session.add(pub)
    await session.flush()
    return ann, pub


async def test_primary_makes_visible_and_notifies(session, captured_events):
    ann, pub = await _make(session, PublicationKindEnum.PRIMARY, hidden=True)
    await execute_publication(pub.id, session)
    await session.refresh(ann); await session.refresh(pub)
    assert ann.is_hidden is False
    assert ann.published_at is not None
    assert pub.status == PublicationStatusEnum.PUBLISHED
    assert pub.executed_at is not None
    assert ("NEW", ann.id) in captured_events


async def test_repeat_keeps_visibility_and_sends_repeat(session, captured_events):
    ann, pub = await _make(session, PublicationKindEnum.REPEAT, hidden=False)
    await execute_publication(pub.id, session)
    await session.refresh(ann); await session.refresh(pub)
    assert ann.is_hidden is False
    assert pub.status == PublicationStatusEnum.PUBLISHED
    assert ("REPEAT", ann.id) in captured_events


async def test_already_published_is_noop(session, captured_events):
    ann, pub = await _make(session, PublicationKindEnum.PRIMARY)
    pub.status = PublicationStatusEnum.PUBLISHED
    await session.flush()
    await execute_publication(pub.id, session)
    assert captured_events == []
