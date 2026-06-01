import uuid

from models.announcement import Announcement, AnnouncementCategoryEnum
from services.announcement import AnnouncementsService


async def _add(session, *, hidden: bool, title: str) -> Announcement:
    ann = Announcement(
        title=title, category=AnnouncementCategoryEnum.NEW, text="b",
        is_hidden=hidden, created_by=uuid.UUID(int=0),
    )
    session.add(ann)
    await session.flush()
    return ann


async def test_hidden_announcements_are_excluded_from_list(session):
    user_id = uuid.uuid4()
    visible = await _add(session, hidden=False, title="visible")
    await _add(session, hidden=True, title="hidden-scheduled")

    rows = await AnnouncementsService(session).get_all_announcements(user_id=user_id)

    returned_ids = {ann.id for ann, _is_read in rows}
    assert visible.id in returned_ids
    assert all(ann.is_hidden is False for ann, _ in rows)
    assert len(returned_ids) == 1
