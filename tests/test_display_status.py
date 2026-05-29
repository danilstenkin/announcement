from services.ticket_status import compute_display_status


def test_published():
    assert compute_display_status("PUBLISHED", True, ["PUBLISHED"]) == "PUBLISHED"


def test_no_date_when_unconfirmed():
    assert compute_display_status("PENDING_REVIEW", False, []) == "NO_DATE"
    assert compute_display_status("IN_REVIEW", False, []) == "NO_DATE"


def test_in_progress_confirmed_but_not_agreed():
    # confirmed a date, still in review, no publications yet
    assert compute_display_status("IN_REVIEW", True, []) == "IN_PROGRESS"
    assert compute_display_status("REVISION", True, []) == "IN_PROGRESS"


def test_agreed_with_scheduled():
    assert compute_display_status("AGREED", True, ["SCHEDULED"]) == "AGREED"


def test_overdue_after_cancel():
    # cancel-publication set ticket back to IN_REVIEW, confirmed stays True, pub CANCELED
    assert compute_display_status("IN_REVIEW", True, ["CANCELED"]) == "OVERDUE"


def test_on_approval_passthrough():
    # ON_APPROVAL is a future status (roles block); function should pass it through
    assert compute_display_status("ON_APPROVAL", True, []) == "ON_APPROVAL"
