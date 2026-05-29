import uuid
from datetime import timedelta

import pytz
from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)


async def test_create_publication_row(session):
    ann = Announcement(
        title="t", category=AnnouncementCategoryEnum.NEW, text="body",
        is_hidden=True, created_by=uuid.UUID(int=0),
    )
    session.add(ann)
    await session.flush()

    pub = AnnouncementPublication(
        announcement_id=ann.id,
        kind=PublicationKindEnum.PRIMARY,
        publish_at=get_astana_time() + timedelta(hours=1),
        status=PublicationStatusEnum.SCHEDULED,
        actor_name="trainer",
    )
    session.add(pub)
    await session.flush()

    assert pub.id is not None
    assert pub.status == PublicationStatusEnum.SCHEDULED
    assert ann.published_at is None


async def test_ticket_publish_fields_default(session):
    from models.incoming_emails import IncomingEmail, EmailStatusEnum
    from models.review_ticket import ReviewTicket, TicketStatusEnum

    email = IncomingEmail(outlook_id="m1", subject="s", body="b",
                          received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()

    ticket = ReviewTicket(email_id=email.id, status=TicketStatusEnum.PENDING_REVIEW)
    session.add(ticket)
    await session.flush()

    assert ticket.publish_confirmed is False
    assert ticket.publish_at is None
    assert ticket.recommended_publish_at is None
    assert TicketStatusEnum.AGREED.value == "AGREED"
    assert TicketStatusEnum.PUBLISHED.value == "PUBLISHED"
