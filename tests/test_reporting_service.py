import uuid
from datetime import timedelta

from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.review_ticket import ReviewTicket, TicketStatusEnum
from models.announcement import get_astana_time
from services.reporting import ReportingService


async def _email(session, *, status, sender="sd_info@Fortebank.com", days_ago=0):
    e = IncomingEmail(
        outlook_id=f"o-{uuid.uuid4()}", subject="s", body="b",
        sender_email=sender, received_at=get_astana_time() - timedelta(days=days_ago),
        status=status,
    )
    session.add(e); await session.flush()
    return e


def _range():
    return ((get_astana_time() - timedelta(days=1)).date(),
            (get_astana_time() + timedelta(days=1)).date())


async def test_email_without_ticket_is_auto(session):
    e = await _email(session, status=EmailStatusEnum.DONE)
    d0, d1 = _range()
    rows = await ReportingService(session).get_message_rows(date_from=d0, date_to=d1)
    row = next(r for r in rows if r.email_id == e.id)
    assert row.processing_type == "AUTO"
    assert row.source == "ServiceDesk"
    assert row.addressed is True


async def test_email_with_open_ticket_is_manual_unaddressed(session):
    e = await _email(session, status=EmailStatusEnum.RED)
    t = ReviewTicket(email_id=e.id, status=TicketStatusEnum.IN_REVIEW, title="t")
    session.add(t); await session.flush()
    d0, d1 = _range()
    rows = await ReportingService(session).get_message_rows(date_from=d0, date_to=d1)
    row = next(r for r in rows if r.email_id == e.id)
    assert row.processing_type == "MANUAL"
    assert row.addressed is False
    assert row.idle_hours is not None and row.idle_hours >= 0
    assert row.cc_scope == "для КЦ"


async def test_period_filter_excludes_outside(session):
    e = await _email(session, status=EmailStatusEnum.DONE, days_ago=10)
    d0, d1 = _range()
    rows = await ReportingService(session).get_message_rows(date_from=d0, date_to=d1)
    assert all(r.email_id != e.id for r in rows)


async def test_processing_filter(session):
    await _email(session, status=EmailStatusEnum.DONE)
    d0, d1 = _range()
    rows = await ReportingService(session).get_message_rows(
        date_from=d0, date_to=d1, processing="MANUAL")
    assert all(r.processing_type == "MANUAL" for r in rows)


async def test_source_filter(session):
    await _email(session, status=EmailStatusEnum.DONE, sender="someone@example.com")
    d0, d1 = _range()
    rows = await ReportingService(session).get_message_rows(
        date_from=d0, date_to=d1, source=["ServiceDesk"])
    assert all(r.source == "ServiceDesk" for r in rows)
