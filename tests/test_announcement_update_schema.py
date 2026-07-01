import pytest
from pydantic import ValidationError

from schemas.announcement import AnnouncementUpdate


def test_empty_script_kz_is_allowed():
    """Editing an announcement with an empty KZ script must not fail validation.

    The frontend sends script_kz='' when the field is left blank; that used to
    raise string_too_short and surface as a 500 on PUT /announcements/{id}.
    """
    upd = AnnouncementUpdate(script_kz="")
    assert upd.script_kz == ""


def test_empty_script_ru_is_allowed():
    upd = AnnouncementUpdate(script_ru="")
    assert upd.script_ru == ""


def test_empty_title_still_rejected():
    """Required-content fields keep their min_length guard."""
    with pytest.raises(ValidationError):
        AnnouncementUpdate(title="")


def test_empty_text_still_rejected():
    with pytest.raises(ValidationError):
        AnnouncementUpdate(text="")
