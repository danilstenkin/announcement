from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, File, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from models.review_ticket import (
    ReviewTicket, ReviewHistory,
    TicketStatusEnum, ReviewActionEnum,
)
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.email_attachment import EmailAttachment
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)
from services.announcement_factory import create_announcement_from_ticket
from services.ticket_status import compute_display_status
from services.publication import execute_publication
from services.notifications import NotificationsService
from dependencies.minio import get_minio_client
from models.announcement import get_astana_time
from config import settings

from datetime import datetime

from urllib.parse import unquote
from logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auto-announce/tickets", tags=["review-tickets"])

MAX_TICKET_ATTACHMENTS = 5
MAX_TICKET_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB per file


# ── Schemas ─────────────────────────────────────────────

class HistoryOut(BaseModel):
    action: str
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    comment: Optional[str] = None
    changes: Optional[dict] = None
    created_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TicketListOut(BaseModel):
    id: UUID
    email_id: UUID
    status: str
    assignee_id: Optional[UUID] = None
    assignee_name: Optional[str] = None
    sender_email: Optional[str] = None
    source: Optional[str] = None
    title: Optional[str] = None
    ai_summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    display_status: Optional[str] = None
    recommended_publish_at: Optional[str] = None
    publish_at: Optional[str] = None
    publish_confirmed: bool = False
    announcement_id: Optional[UUID] = None
    pending_assignee_id: Optional[UUID] = None
    pending_assignee_name: Optional[str] = None
    transfer_requested_by_name: Optional[str] = None
    transfer_requested_at: Optional[str] = None
    transfer_reason: Optional[str] = None
    history: list[HistoryOut] = []

    model_config = ConfigDict(from_attributes=True)


class AttachmentOut(BaseModel):
    id: UUID
    filename: str
    object_key: str
    content_type: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TicketDetailOut(TicketListOut):
    body: Optional[str] = None
    script_ru: Optional[str] = None
    script_kz: Optional[str] = None
    original_html_key: Optional[str] = None
    attachments: list[AttachmentOut] = []


class AssignRequest(BaseModel):
    assignee_id: UUID
    assignee_name: str


class RevisionRequest(BaseModel):
    assignee_id: UUID
    assignee_name: str
    comment: str


class EditRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    script_ru: Optional[str] = None
    script_kz: Optional[str] = None


class RejectRequest(BaseModel):
    comment: Optional[str] = None


class ConfirmDateRequest(BaseModel):
    publish_at: Optional[datetime] = None


class TransferRequest(BaseModel):
    assignee_id: UUID
    assignee_name: str
    reason: str


# ── Helpers ─────────────────────────────────────────────

def _get_user(
    x_user_id: str = Header(None, alias="X-User-Id"),
    x_user_name: str = Header(None, alias="X-User-Name"),
):
    return {"id": x_user_id, "name": unquote(x_user_name) if x_user_name else x_user_name}


async def _get_ticket(ticket_id: UUID, session: AsyncSession) -> ReviewTicket:
    result = await session.execute(
        select(ReviewTicket).where(ReviewTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _build_history(history_items) -> list[HistoryOut]:
    return [
        HistoryOut(
            action=h.action.value,
            actor_id=h.actor_id,
            actor_name=h.actor_name,
            comment=h.comment,
            changes=h.changes,
            created_at=h.created_at.isoformat() if h.created_at else None,
        )
        for h in history_items
    ]


async def _serialize_ticket_rows(tickets, session: AsyncSession) -> list[TicketListOut]:
    """Map ReviewTicket rows to TicketListOut, batch-loading sender email and
    publication statuses. Shared by the ticket list and the transfer-queue list."""
    email_ids = [t.email_id for t in tickets]
    if email_ids:
        emails_result = await session.execute(
            select(IncomingEmail).where(IncomingEmail.id.in_(email_ids))
        )
        email_map = {e.id: e for e in emails_result.scalars().all()}
    else:
        email_map = {}

    ann_ids = [t.announcement_id for t in tickets if t.announcement_id]
    pub_status_map: dict = {}
    if ann_ids:
        pubs = (await session.execute(
            select(AnnouncementPublication.announcement_id, AnnouncementPublication.status)
            .where(AnnouncementPublication.announcement_id.in_(ann_ids))
        )).all()
        for ann_id, st in pubs:
            pub_status_map.setdefault(ann_id, []).append(st.value if hasattr(st, "value") else str(st))

    response = []
    for t in tickets:
        email = email_map.get(t.email_id)
        response.append(TicketListOut(
            id=t.id,
            email_id=t.email_id,
            status=t.status.value,
            assignee_id=t.assignee_id,
            assignee_name=t.assignee_name,
            sender_email=email.sender_email if email else None,
            source=t.source,
            title=t.title,
            ai_summary=t.ai_summary,
            created_at=t.created_at.isoformat() if t.created_at else None,
            updated_at=t.updated_at.isoformat() if t.updated_at else None,
            display_status=compute_display_status(
                t.status.value, t.publish_confirmed,
                pub_status_map.get(t.announcement_id, []) if t.announcement_id else [],
            ),
            recommended_publish_at=t.recommended_publish_at.isoformat() if t.recommended_publish_at else None,
            publish_at=t.publish_at.isoformat() if t.publish_at else None,
            publish_confirmed=t.publish_confirmed,
            announcement_id=t.announcement_id,
            pending_assignee_id=t.pending_assignee_id,
            pending_assignee_name=t.pending_assignee_name,
            transfer_requested_by_name=t.transfer_requested_by_name,
            transfer_requested_at=t.transfer_requested_at.isoformat() if t.transfer_requested_at else None,
            transfer_reason=t.transfer_reason,
            history=_build_history(t.history),
        ))
    return response


async def _notify_ticket(event_type: str, ticket_id: str, **kwargs):
    """Send ticket event to webhook → Redis pub/sub → SSE."""
    try:
        notifications = NotificationsService(settings.EVENTS_WEBHOOK_URL)
        await notifications.publish_ticket_event(
            event_type=event_type, ticket_id=ticket_id, **kwargs,
        )
    except Exception as e:
        logger.error("Failed to send ticket event: {err}", err=e)


# ── List & Detail ───────────────────────────────────────

@router.get("", response_model=list[TicketListOut])
async def list_tickets(
    status: Optional[str] = None,
    assignee_id: Optional[UUID] = None,
    session: AsyncSession = Depends(get_db),
):
    query = select(ReviewTicket).order_by(ReviewTicket.created_at.desc())

    if status:
        query = query.where(ReviewTicket.status == TicketStatusEnum(status))
    if assignee_id:
        query = query.where(ReviewTicket.assignee_id == assignee_id)

    if not status:
        query = query.where(
            ReviewTicket.status.notin_([TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED])
        )

    result = await session.execute(query)
    tickets = result.scalars().all()
    return await _serialize_ticket_rows(tickets, session)


@router.get("/transfers/pending", response_model=list[TicketListOut])
async def list_pending_transfers(
    session: AsyncSession = Depends(get_db),
):
    """Queue for the head of training: tickets with a transfer awaiting approval.
    These are the tickets to approve (`/{id}/transfer-approve`) or reject
    (`/{id}/transfer-reject`). Oldest request first."""
    query = (
        select(ReviewTicket)
        .where(ReviewTicket.pending_assignee_id.isnot(None))
        .order_by(ReviewTicket.transfer_requested_at.asc())
    )
    tickets = (await session.execute(query)).scalars().all()
    return await _serialize_ticket_rows(tickets, session)


@router.get("/{ticket_id}", response_model=TicketDetailOut)
async def get_ticket(
    ticket_id: UUID,
    session: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket(ticket_id, session)

    email_result = await session.execute(
        select(IncomingEmail).where(IncomingEmail.id == ticket.email_id)
    )
    email = email_result.scalar_one_or_none()

    att_result = await session.execute(
        select(EmailAttachment).where(
            EmailAttachment.email_id == ticket.email_id,
            EmailAttachment.is_inline.is_(False),  # inline-картинки фронту не отдаём
        )
    )
    atts = [AttachmentOut.model_validate(a) for a in att_result.scalars().all()]

    pub_statuses = []
    if ticket.announcement_id:
        rows = (await session.execute(
            select(AnnouncementPublication.status)
            .where(AnnouncementPublication.announcement_id == ticket.announcement_id)
        )).scalars().all()
        pub_statuses = [s.value if hasattr(s, "value") else str(s) for s in rows]

    return TicketDetailOut(
        id=ticket.id,
        email_id=ticket.email_id,
        status=ticket.status.value,
        assignee_id=ticket.assignee_id,
        assignee_name=ticket.assignee_name,
        sender_email=email.sender_email if email else None,
        source=ticket.source,
        title=ticket.title,
        body=ticket.body,
        script_ru=ticket.script_ru,
        script_kz=ticket.script_kz,
        ai_summary=ticket.ai_summary,
        original_html_key=email.original_html_key if email else None,
        created_at=ticket.created_at.isoformat() if ticket.created_at else None,
        updated_at=ticket.updated_at.isoformat() if ticket.updated_at else None,
        display_status=compute_display_status(
            ticket.status.value, ticket.publish_confirmed, pub_statuses,
        ),
        recommended_publish_at=ticket.recommended_publish_at.isoformat() if ticket.recommended_publish_at else None,
        publish_at=ticket.publish_at.isoformat() if ticket.publish_at else None,
        publish_confirmed=ticket.publish_confirmed,
        announcement_id=ticket.announcement_id,
        pending_assignee_id=ticket.pending_assignee_id,
        pending_assignee_name=ticket.pending_assignee_name,
        transfer_requested_by_name=ticket.transfer_requested_by_name,
        transfer_requested_at=ticket.transfer_requested_at.isoformat() if ticket.transfer_requested_at else None,
        transfer_reason=ticket.transfer_reason,
        history=_build_history(ticket.history),
        attachments=atts,
    )


# ── Actions ─────────────────────────────────────────────

@router.post("/{ticket_id}/assign")
async def assign_ticket(
    ticket_id: UUID,
    req: AssignRequest,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED):
        raise HTTPException(400, "Ticket is already closed")

    ticket.assignee_id = req.assignee_id
    ticket.assignee_name = req.assignee_name
    ticket.status = TicketStatusEnum.IN_REVIEW

    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.ASSIGNED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment=f"Назначено на {req.assignee_name}",
    ))
    await session.commit()
    await _notify_ticket(
        event_type="ASSIGNED", ticket_id=str(ticket.id),
        actor_name=user["name"], assignee_id=str(req.assignee_id),
        assignee_name=req.assignee_name, title=ticket.title, status="IN_REVIEW",
    )
    return {"status": "assigned"}


@router.post("/{ticket_id}/take")
async def take_ticket(
    ticket_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED):
        raise HTTPException(400, "Ticket is already closed")

    ticket.assignee_id = UUID(user["id"]) if user["id"] else None
    ticket.assignee_name = user["name"]
    ticket.status = TicketStatusEnum.IN_REVIEW

    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.TAKEN,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
    ))
    await session.commit()
    await _notify_ticket(
        event_type="TAKEN", ticket_id=str(ticket.id),
        actor_name=user["name"], assignee_id=user["id"],
        assignee_name=user["name"], title=ticket.title, status="IN_REVIEW",
    )
    return {"status": "taken"}


@router.post("/{ticket_id}/revision")
async def send_to_revision(
    ticket_id: UUID,
    req: RevisionRequest,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED):
        raise HTTPException(400, "Ticket is already closed")

    ticket.assignee_id = req.assignee_id
    ticket.assignee_name = req.assignee_name
    ticket.status = TicketStatusEnum.REVISION

    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.SENT_TO_REVISION,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment=req.comment,
    ))
    await session.commit()
    await _notify_ticket(
        event_type="SENT_TO_REVISION", ticket_id=str(ticket.id),
        actor_name=user["name"], assignee_id=str(req.assignee_id),
        assignee_name=req.assignee_name, title=ticket.title,
        comment=req.comment, status="REVISION",
    )
    return {"status": "revision"}


@router.post("/{ticket_id}/edit")
async def edit_ticket(
    ticket_id: UUID,
    req: EditRequest,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED):
        raise HTTPException(400, "Ticket is already closed")

    changes = {}
    if req.title is not None and req.title != ticket.title:
        changes["title"] = {"old": ticket.title, "new": req.title}
        ticket.title = req.title
    if req.body is not None and req.body != ticket.body:
        changes["body"] = {"old": "...", "new": "..."}
        ticket.body = req.body
    if req.script_ru is not None and req.script_ru != ticket.script_ru:
        changes["script_ru"] = {"old": ticket.script_ru, "new": req.script_ru}
        ticket.script_ru = req.script_ru
    if req.script_kz is not None and req.script_kz != ticket.script_kz:
        changes["script_kz"] = {"old": ticket.script_kz, "new": req.script_kz}
        ticket.script_kz = req.script_kz

    if changes:
        session.add(ReviewHistory(
            ticket_id=ticket.id,
            action=ReviewActionEnum.EDITED,
            actor_id=UUID(user["id"]) if user["id"] else None,
            actor_name=user["name"],
            changes=changes,
        ))

    await session.commit()
    if changes:
        await _notify_ticket(
            event_type="EDITED", ticket_id=str(ticket.id),
            actor_name=user["name"], title=ticket.title, status=ticket.status.value,
        )
    return {"status": "edited", "changed_fields": list(changes.keys())}


@router.post("/{ticket_id}/confirm-date")
async def confirm_date(
    ticket_id: UUID,
    req: ConfirmDateRequest,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED, TicketStatusEnum.PUBLISHED):
        raise HTTPException(400, "Ticket is already closed")
    chosen = req.publish_at or ticket.recommended_publish_at
    if chosen is None:
        raise HTTPException(400, "No publish date provided and no recommendation available")
    ticket.publish_at = chosen
    ticket.publish_confirmed = True
    session.add(ReviewHistory(
        ticket_id=ticket.id, action=ReviewActionEnum.EDITED,
        actor_id=UUID(user["id"]) if user["id"] else None, actor_name=user["name"],
        comment=f"Дата публикации подтверждена: {chosen.isoformat()}",
    ))
    await session.commit()
    return {"status": "confirmed", "publish_at": chosen.isoformat()}


# ── Approve & Reject ────────────────────────────────────

@router.post("/{ticket_id}/approve")
async def approve_ticket(
    ticket_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (
        TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED, TicketStatusEnum.PUBLISHED,
    ):
        raise HTTPException(400, "Ticket is already closed")
    if ticket.status == TicketStatusEnum.AGREED:
        raise HTTPException(400, "Ticket already has a scheduled publication")

    if not ticket.title:
        raise HTTPException(400, "Cannot approve: title is empty")

    if not ticket.publish_confirmed or ticket.publish_at is None:
        raise HTTPException(400, "Publish date must be confirmed first")

    ann = await create_announcement_from_ticket(ticket, session, hidden=True)

    session.add(AnnouncementPublication(
        announcement_id=ann.id,
        kind=PublicationKindEnum.PRIMARY,
        publish_at=ticket.publish_at,
        status=PublicationStatusEnum.SCHEDULED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
    ))

    ticket.status = TicketStatusEnum.AGREED

    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.APPROVED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment=f"Запланирована публикация на {ticket.publish_at.isoformat()}",
    ))

    await session.commit()

    await _notify_ticket(
        event_type="APPROVED", ticket_id=str(ticket.id),
        actor_name=user["name"], title=ticket.title, status="AGREED",
    )

    return {
        "status": "agreed",
        "announcement_id": str(ann.id),
        "publish_at": ticket.publish_at.isoformat(),
    }


@router.post("/{ticket_id}/publish")
async def publish_now(
    ticket_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status == TicketStatusEnum.PUBLISHED:
        raise HTTPException(400, "Already published")
    if ticket.status == TicketStatusEnum.REJECTED:
        raise HTTPException(400, "Ticket is rejected")
    if not ticket.title:
        raise HTTPException(400, "Cannot publish: title is empty")

    now = get_astana_time()

    # If the ticket was already approved/scheduled, publish the EXISTING
    # scheduled publication instead of creating a duplicate announcement.
    if ticket.announcement_id is not None:
        existing_pub = (await session.execute(
            select(AnnouncementPublication).where(
                AnnouncementPublication.announcement_id == ticket.announcement_id,
                AnnouncementPublication.kind == PublicationKindEnum.PRIMARY,
                AnnouncementPublication.status == PublicationStatusEnum.SCHEDULED,
            )
        )).scalar_one_or_none()
        if existing_pub is not None:
            existing_pub.publish_at = now
            await session.flush()
            await execute_publication(existing_pub.id, session)
            await session.commit()
            return {"status": "published", "announcement_id": str(ticket.announcement_id)}

    ann = await create_announcement_from_ticket(ticket, session, hidden=True)
    pub = AnnouncementPublication(
        announcement_id=ann.id,
        kind=PublicationKindEnum.PRIMARY,
        publish_at=now,
        status=PublicationStatusEnum.SCHEDULED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
    )
    session.add(pub)
    ticket.publish_at = now
    ticket.publish_confirmed = True
    await session.flush()

    await execute_publication(pub.id, session)
    await session.commit()
    return {"status": "published", "announcement_id": str(ann.id)}


@router.post("/{ticket_id}/cancel-publication")
async def cancel_publication(
    ticket_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.announcement_id is None:
        raise HTTPException(400, "No scheduled publication to cancel")
    pub = (await session.execute(
        select(AnnouncementPublication).where(
            AnnouncementPublication.announcement_id == ticket.announcement_id,
            AnnouncementPublication.kind == PublicationKindEnum.PRIMARY,
            AnnouncementPublication.status == PublicationStatusEnum.SCHEDULED,
        )
    )).scalar_one_or_none()
    if pub is None:
        raise HTTPException(400, "No scheduled publication to cancel")

    pub.status = PublicationStatusEnum.CANCELED
    pub.canceled_at = get_astana_time()
    ticket.status = TicketStatusEnum.IN_REVIEW
    session.add(ReviewHistory(
        ticket_id=ticket.id, action=ReviewActionEnum.PUBLICATION_CANCELED,
        actor_id=UUID(user["id"]) if user["id"] else None, actor_name=user["name"],
        comment="Публикация отменена",
    ))
    await session.commit()
    return {"status": "publication_canceled"}


@router.post("/{ticket_id}/reject")
async def reject_ticket(
    ticket_id: UUID,
    req: RejectRequest,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED):
        raise HTTPException(400, "Ticket is already closed")

    ticket.status = TicketStatusEnum.REJECTED

    await session.execute(
        update(IncomingEmail)
        .where(IncomingEmail.id == ticket.email_id)
        .values(status=EmailStatusEnum.DONE)
    )

    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.REJECTED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment=req.comment,
    ))

    await session.commit()
    await _notify_ticket(
        event_type="REJECTED", ticket_id=str(ticket.id),
        actor_name=user["name"], title=ticket.title, status="REJECTED",
    )
    return {"status": "rejected"}


# ── Transfer to another trainer (with head approval) ────

@router.post("/{ticket_id}/transfer-request")
async def request_transfer(
    ticket_id: UUID,
    req: TransferRequest,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    """Trainer requests to hand the ticket to another trainer. Does NOT reassign —
    waits for a head-of-training approval. Requires a justification (reason)."""
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (
        TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED, TicketStatusEnum.PUBLISHED,
    ):
        raise HTTPException(400, "Ticket is already closed")
    if ticket.pending_assignee_id is not None:
        raise HTTPException(400, "A transfer is already pending approval")
    if not req.reason.strip():
        raise HTTPException(400, "Reason is required")

    ticket.pending_assignee_id = req.assignee_id
    ticket.pending_assignee_name = req.assignee_name
    ticket.transfer_requested_by_id = UUID(user["id"]) if user["id"] else None
    ticket.transfer_requested_by_name = user["name"]
    ticket.transfer_requested_at = get_astana_time()
    ticket.transfer_reason = req.reason.strip()

    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.TRANSFER_REQUESTED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment=f"Запрос передачи на {req.assignee_name}: {req.reason.strip()}",
    ))
    await session.commit()
    await _notify_ticket(
        event_type="TRANSFER_REQUESTED", ticket_id=str(ticket.id),
        actor_name=user["name"], title=ticket.title, status=ticket.status.value,
    )
    return {"status": "transfer_requested", "pending_assignee_name": req.assignee_name}


@router.post("/{ticket_id}/transfer-approve")
async def approve_transfer(
    ticket_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    """Head of training approves the pending transfer → ticket is reassigned."""
    ticket = await _get_ticket(ticket_id, session)
    if ticket.pending_assignee_id is None:
        raise HTTPException(400, "No pending transfer to approve")

    new_id = ticket.pending_assignee_id
    new_name = ticket.pending_assignee_name
    ticket.assignee_id = new_id
    ticket.assignee_name = new_name
    ticket.status = TicketStatusEnum.IN_REVIEW
    ticket.pending_assignee_id = None
    ticket.pending_assignee_name = None
    ticket.transfer_requested_by_id = None
    ticket.transfer_requested_by_name = None
    ticket.transfer_requested_at = None
    ticket.transfer_reason = None

    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.TRANSFER_APPROVED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment=f"Передача подтверждена, назначено на {new_name}",
    ))
    await session.commit()
    await _notify_ticket(
        event_type="TRANSFER_APPROVED", ticket_id=str(ticket.id),
        actor_name=user["name"], assignee_id=str(new_id) if new_id else None,
        assignee_name=new_name, title=ticket.title, status="IN_REVIEW",
    )
    return {"status": "transferred", "assignee_id": str(new_id) if new_id else None,
            "assignee_name": new_name}


@router.post("/{ticket_id}/transfer-reject")
async def reject_transfer(
    ticket_id: UUID,
    req: RejectRequest,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    """Head of training declines the pending transfer → ticket stays with current trainer."""
    ticket = await _get_ticket(ticket_id, session)
    if ticket.pending_assignee_id is None:
        raise HTTPException(400, "No pending transfer to reject")

    rejected_name = ticket.pending_assignee_name
    ticket.pending_assignee_id = None
    ticket.pending_assignee_name = None
    ticket.transfer_requested_by_id = None
    ticket.transfer_requested_by_name = None
    ticket.transfer_requested_at = None
    ticket.transfer_reason = None

    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.TRANSFER_REJECTED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment=req.comment or f"Передача на {rejected_name} отклонена",
    ))
    await session.commit()
    await _notify_ticket(
        event_type="TRANSFER_REJECTED", ticket_id=str(ticket.id),
        actor_name=user["name"], title=ticket.title, status=ticket.status.value,
    )
    return {"status": "transfer_rejected"}


# ── Attachments ─────────────────────────────────────────

def _ensure_attachments_editable(ticket: ReviewTicket) -> None:
    """Attachments live on the email and are copied into the announcement at approve.
    So they can only be changed while the ticket is still open and not yet converted."""
    if ticket.announcement_id is not None or ticket.status in (
        TicketStatusEnum.AGREED, TicketStatusEnum.APPROVED,
        TicketStatusEnum.REJECTED, TicketStatusEnum.PUBLISHED,
    ):
        raise HTTPException(400, "Attachments can only be changed before the ticket is approved")


@router.post("/{ticket_id}/attachments", response_model=list[AttachmentOut])
async def upload_attachments(
    ticket_id: UUID,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    """Attach files to a ticket. Stored on the ticket's email, transferred to the
    announcement on approve. Multipart field name: `files`."""
    ticket = await _get_ticket(ticket_id, session)
    _ensure_attachments_editable(ticket)

    if not files:
        raise HTTPException(400, "No files provided")
    if len(files) > MAX_TICKET_ATTACHMENTS:
        raise HTTPException(400, f"Too many files (max {MAX_TICKET_ATTACHMENTS})")

    minio = get_minio_client()
    created = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_TICKET_ATTACHMENT_BYTES:
            raise HTTPException(
                400, f"File {f.filename} exceeds {MAX_TICKET_ATTACHMENT_BYTES} bytes"
            )
        object_key = await minio.upload_file(
            file_content=data,
            filename=f.filename or "file",
            announcement_id=f"emails/{ticket.email_id}",
        )
        att = EmailAttachment(
            email_id=ticket.email_id,
            filename=f.filename or "file",
            object_key=object_key,
            file_size=len(data),
            content_type=f.content_type,
        )
        session.add(att)
        created.append(att)

    await session.flush()
    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.EDITED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment="Добавлены вложения: " + ", ".join(a.filename for a in created),
    ))
    await session.commit()
    return [AttachmentOut.model_validate(a) for a in created]


@router.delete("/{ticket_id}/attachments/{attachment_id}")
async def delete_attachment(
    ticket_id: UUID,
    attachment_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    """Delete a ticket attachment (DB row + MinIO object)."""
    ticket = await _get_ticket(ticket_id, session)
    _ensure_attachments_editable(ticket)

    att = (await session.execute(
        select(EmailAttachment).where(
            EmailAttachment.id == attachment_id,
            EmailAttachment.email_id == ticket.email_id,
        )
    )).scalar_one_or_none()
    if att is None:
        raise HTTPException(404, "Attachment not found")

    object_key, filename = att.object_key, att.filename
    try:
        await get_minio_client().delete_file(object_key)
    except Exception:
        logger.error("Failed to delete MinIO object {k}", k=object_key)

    await session.delete(att)
    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.EDITED,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment=f"Удалено вложение: {filename}",
    ))
    await session.commit()
    return {"status": "deleted"}
