from datetime import datetime
from uuid import uuid4

from services.reporting import MessageRow, compute_summary


def _row(**kw):
    base = dict(
        email_id=uuid4(), received_at=datetime(2026, 6, 1), sender_email="x",
        source="ServiceDesk", subject="s", email_status="DONE",
        processing_type="AUTO", ticket_status=None, assignee_name=None,
        publish_at=None, addressed=True, idle_hours=None, announcement_id=None,
        announcement_title=None, published_at=None, cc_scope="для КЦ",
    )
    base.update(kw)
    return MessageRow(**base)


def test_summary_counts():
    rows = [
        _row(source="ServiceDesk", email_status="DONE", processing_type="AUTO", addressed=True, cc_scope="для КЦ"),
        _row(source="komek", email_status="RED", processing_type="MANUAL", addressed=False, cc_scope="для КЦ"),
        _row(source="other", email_status="GREEN", processing_type="MANUAL", addressed=True, cc_scope="не для КЦ"),
    ]
    s = compute_summary(rows)
    assert s.total == 3
    assert s.by_source["ServiceDesk"] == 1 and s.by_source["komek"] == 1
    assert s.by_status["DONE"] == 1 and s.by_status["RED"] == 1 and s.by_status["GREEN"] == 1
    assert s.auto == 1 and s.manual == 2
    assert s.unaddressed_total == 1
    assert s.unaddressed_by_source["komek"] == 1
    assert s.cc_yes == 2 and s.cc_no == 1
