from datetime import datetime, timedelta

import pytz
from models.announcement import get_astana_time
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.review_ticket import ReviewTicket, TicketStatusEnum

ALMATY = pytz.timezone("Asia/Almaty")


async def _seed_ticket(session, recommended=None):
    email = IncomingEmail(outlook_id=f"m-{datetime.now().timestamp()}", subject="s", body="b",
                          received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.IN_REVIEW,
                     recommended_publish_at=recommended)
    session.add(t)
    await session.flush()
    return t


async def test_confirm_date_falls_back_to_recommended(client, session):
    rec = get_astana_time() + timedelta(days=1)
    t = await _seed_ticket(session, recommended=rec)
    r = await client.post(f"/auto-announce/tickets/{t.id}/confirm-date", json={})
    assert r.status_code == 200, r.text
    await session.refresh(t)
    assert t.publish_confirmed is True
    assert t.publish_at is not None


async def test_confirm_date_explicit_time(client, session):
    t = await _seed_ticket(session, recommended=None)
    when = (get_astana_time() + timedelta(days=2)).replace(microsecond=0)
    r = await client.post(
        f"/auto-announce/tickets/{t.id}/confirm-date",
        json={"publish_at": when.isoformat()},
    )
    assert r.status_code == 200, r.text
    await session.refresh(t)
    assert t.publish_confirmed is True
    assert t.publish_at is not None


async def test_confirm_date_no_date_available_400(client, session):
    t = await _seed_ticket(session, recommended=None)
    r = await client.post(f"/auto-announce/tickets/{t.id}/confirm-date", json={})
    assert r.status_code == 400, r.text
