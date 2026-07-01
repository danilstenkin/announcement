import pytest
from pydantic import ValidationError


def test_announcement_update_accepts_valid_ai_category():
    from schemas.announcement import AnnouncementUpdate

    assert AnnouncementUpdate(ai_category="Инциденты").ai_category == "Инциденты"


def test_announcement_update_allows_null_ai_category():
    from schemas.announcement import AnnouncementUpdate

    assert AnnouncementUpdate().ai_category is None
    assert AnnouncementUpdate(ai_category=None).ai_category is None


def test_announcement_update_rejects_invalid_ai_category():
    from schemas.announcement import AnnouncementUpdate

    with pytest.raises(ValidationError):
        AnnouncementUpdate(ai_category="Ерунда")


def test_ticket_edit_request_accepts_valid_and_null():
    from routers.tickets import EditRequest

    assert EditRequest(ai_category="Качество работы").ai_category == "Качество работы"
    assert EditRequest().ai_category is None


def test_ticket_edit_request_rejects_invalid_ai_category():
    from routers.tickets import EditRequest

    with pytest.raises(ValidationError):
        EditRequest(ai_category="nope")
