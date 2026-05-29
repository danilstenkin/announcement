import uuid
from datetime import timedelta

from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.publication import AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum
from models.review_ticket import ReviewTicket, TicketStatusEnum


async def test_detail_reports_agreed(client, session):
    email = IncomingEmail(outlook_id=f"m-{get_astana_time().timestamp()}", subject="s",
                          body="b", received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email); await session.flush()
    ann = Announcement(title="t", category=AnnouncementCategoryEnum.NEW, text="b",
                       is_hidden=True, created_by=uuid.UUID(int=0))
    session.add(ann); await session.flush()
    session.add(AnnouncementPublication(
        announcement_id=ann.id, kind=PublicationKindEnum.PRIMARY,
        status=PublicationStatusEnum.SCHEDULED, publish_at=get_astana_time() + timedelta(days=1),
    ))
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.AGREED, title="t",
                     announcement_id=ann.id, publish_confirmed=True,
                     publish_at=get_astana_time() + timedelta(days=1))
    session.add(t); await session.flush()

    r = await client.get(f"/auto-announce/tickets/{t.id}")
    assert r.status_code == 200, r.text
    assert r.json()["display_status"] == "AGREED"


async def test_list_reports_no_date(client, session):
    email = IncomingEmail(outlook_id=f"m2-{get_astana_time().timestamp()}", subject="s",
                          body="b", received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email); await session.flush()
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.PENDING_REVIEW, title="t")
    session.add(t); await session.flush()

    r = await client.get("/auto-announce/tickets")
    assert r.status_code == 200, r.text
    mine = [x for x in r.json() if x["id"] == str(t.id)]
    assert mine and mine[0]["display_status"] == "NO_DATE"
