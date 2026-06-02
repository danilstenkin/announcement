from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import pytz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.announcement import Announcement, get_astana_time
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.review_ticket import ReviewTicket, TicketStatusEnum
from pipeline.source import resolve_source

ALMATY = pytz.timezone("Asia/Almaty")
_CLOSED = {TicketStatusEnum.PUBLISHED, TicketStatusEnum.REJECTED}


@dataclass
class MessageRow:
    email_id: UUID
    received_at: datetime
    sender_email: str | None
    source: str
    subject: str
    email_status: str
    processing_type: str            # AUTO | MANUAL
    ticket_status: str | None
    assignee_name: str | None
    publish_at: datetime | None
    addressed: bool
    idle_hours: float | None
    announcement_id: UUID | None
    announcement_title: str | None
    published_at: datetime | None


@dataclass
class Summary:
    total: int
    by_source: dict[str, int]
    by_status: dict[str, int]
    auto: int
    manual: int
    unaddressed_total: int
    unaddressed_by_source: dict[str, int]


def _almaty(dt: datetime | None) -> datetime | None:
    return dt.astimezone(ALMATY) if dt else None


class ReportingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_message_rows(
        self,
        date_from: date,
        date_to: date,
        source: list[str] | None = None,
        processing: str | None = None,
    ) -> list[MessageRow]:
        start = ALMATY.localize(datetime(date_from.year, date_from.month, date_from.day, 0, 0))
        end = ALMATY.localize(datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59))

        stmt = (
            select(IncomingEmail, ReviewTicket, Announcement)
            .outerjoin(ReviewTicket, ReviewTicket.email_id == IncomingEmail.id)
            .outerjoin(Announcement, Announcement.id == ReviewTicket.announcement_id)
            .where(IncomingEmail.received_at >= start)
            .where(IncomingEmail.received_at <= end)
            .order_by(IncomingEmail.received_at.desc())
        )

        now = get_astana_time()
        rows: list[MessageRow] = []
        for email, ticket, ann in (await self.db.execute(stmt)).all():
            src = resolve_source(email.sender_email)
            if source and src not in source:
                continue
            ptype = "MANUAL" if ticket is not None else "AUTO"
            if processing and ptype != processing:
                continue

            if ptype == "MANUAL":
                addressed = ticket.status in _CLOSED
            else:
                addressed = email.status == EmailStatusEnum.DONE

            idle_hours = None if addressed else round(
                (now - email.received_at).total_seconds() / 3600, 1
            )

            rows.append(MessageRow(
                email_id=email.id,
                received_at=_almaty(email.received_at),
                sender_email=email.sender_email,
                source=src,
                subject=email.subject,
                email_status=email.status.value,
                processing_type=ptype,
                ticket_status=ticket.status.value if ticket else None,
                assignee_name=ticket.assignee_name if ticket else None,
                publish_at=_almaty(ticket.publish_at) if ticket else None,
                addressed=addressed,
                idle_hours=idle_hours,
                announcement_id=ann.id if ann else None,
                announcement_title=ann.title if ann else None,
                published_at=_almaty(ann.published_at) if ann else None,
            ))
        return rows


def compute_summary(rows: list[MessageRow]) -> Summary:
    by_source = Counter(r.source for r in rows)
    by_status = Counter(r.email_status for r in rows)
    unaddressed = [r for r in rows if not r.addressed]
    return Summary(
        total=len(rows),
        by_source=dict(by_source),
        by_status=dict(by_status),
        auto=sum(1 for r in rows if r.processing_type == "AUTO"),
        manual=sum(1 for r in rows if r.processing_type == "MANUAL"),
        unaddressed_total=len(unaddressed),
        unaddressed_by_source=dict(Counter(r.source for r in unaddressed)),
    )
