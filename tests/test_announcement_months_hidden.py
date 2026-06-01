import uuid

from models.announcement import Announcement, AnnouncementCategoryEnum


async def _add(session, *, hidden: bool, title: str) -> Announcement:
    ann = Announcement(
        title=title, category=AnnouncementCategoryEnum.NEW, text="b",
        is_hidden=hidden, created_by=uuid.UUID(int=0),
    )
    session.add(ann)
    await session.flush()
    return ann


async def test_months_count_excludes_hidden(client, session):
    await _add(session, hidden=False, title="visible")
    await _add(session, hidden=True, title="hidden-scheduled")
    await session.flush()

    r = await client.get("/announcements/months", headers={"X-User-Id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text

    total = sum(bucket["count"] for bucket in r.json())
    assert total == 1
