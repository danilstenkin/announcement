from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header
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
from models.announcement import get_astana_time
from config import settings

from datetime import datetime

from urllib.parse import unquote
from logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auto-announce/tickets", tags=["review-tickets"])


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
    history: list[HistoryOut] = []

    model_config = ConfigDict(from_attributes=True)


class AttachmentOut(BaseModel):
    id: UUID
    filename: str
    object_key: str
    content_type: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class LinkItem(BaseModel):
    title: Optional[str] = None
    url: str

    model_config = ConfigDict(from_attributes=True)


class TicketDetailOut(TicketListOut):
    body: Optional[str] = None
    script_ru: Optional[str] = None
    script_kz: Optional[str] = None
    category: Optional[str] = None
    product: Optional[str] = None
    instruction: Optional[str] = None
    topic: Optional[str] = None
    links: Optional[list[LinkItem]] = None
    documents: Optional[list[LinkItem]] = None
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
    category: Optional[str] = None
    product: Optional[str] = None
    instruction: Optional[str] = None
    topic: Optional[str] = None
    links: Optional[list[LinkItem]] = None
    documents: Optional[list[LinkItem]] = None


class RejectRequest(BaseModel):
    comment: Optional[str] = None


class ConfirmDateRequest(BaseModel):
    publish_at: Optional[datetime] = None


class ReturnApprovalRequest(BaseModel):
    comment: Optional[str] = None
    assignee_id: Optional[UUID] = None
    assignee_name: Optional[str] = None


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
            history=_build_history(t.history),
        ))

    return response


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
        select(EmailAttachment).where(EmailAttachment.email_id == ticket.email_id)
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
        category=ticket.category,
        product=ticket.product,
        instruction=ticket.instruction,
        topic=ticket.topic,
        links=ticket.links,
        documents=ticket.documents,
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
    if req.category is not None and req.category != ticket.category:
        changes["category"] = {"old": ticket.category, "new": req.category}
        ticket.category = req.category
    if req.product is not None and req.product != ticket.product:
        changes["product"] = {"old": ticket.product, "new": req.product}
        ticket.product = req.product
    if req.instruction is not None and req.instruction != ticket.instruction:
        changes["instruction"] = {"old": "...", "new": "..."}
        ticket.instruction = req.instruction
    if req.topic is not None and req.topic != ticket.topic:
        changes["topic"] = {"old": ticket.topic, "new": req.topic}
        ticket.topic = req.topic
    if req.links is not None:
        new_links = [item.model_dump() for item in req.links]
        if new_links != (ticket.links or []):
            changes["links"] = {"old": "...", "new": "..."}
            ticket.links = new_links
    if req.documents is not None:
        new_documents = [item.model_dump() for item in req.documents]
        if new_documents != (ticket.documents or []):
            changes["documents"] = {"old": "...", "new": "..."}
            ticket.documents = new_documents

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


@router.post("/{ticket_id}/submit-approval")
async def submit_for_approval(
    ticket_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    """Send the ticket to a manager for approval (IN_REVIEW/REVISION → ON_APPROVAL).

    Same preconditions as approve (title set, publish date confirmed) since the
    manager approves the already-prepared announcement.
    """
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (
        TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED,
        TicketStatusEnum.PUBLISHED, TicketStatusEnum.AGREED,
    ):
        raise HTTPException(400, "Ticket is already closed")
    if ticket.status == TicketStatusEnum.ON_APPROVAL:
        raise HTTPException(400, "Ticket is already on approval")
    if not ticket.title:
        raise HTTPException(400, "Cannot submit: title is empty")
    if not ticket.publish_confirmed or ticket.publish_at is None:
        raise HTTPException(400, "Publish date must be confirmed first")

    ticket.status = TicketStatusEnum.ON_APPROVAL
    session.add(ReviewHistory(
        ticket_id=ticket.id,
        action=ReviewActionEnum.SENT_TO_APPROVAL,
        actor_id=UUID(user["id"]) if user["id"] else None,
        actor_name=user["name"],
        comment="Отправлено на согласование",
    ))
    await session.commit()
    await _notify_ticket(
        event_type="SENT_TO_APPROVAL", ticket_id=str(ticket.id),
        actor_name=user["name"], title=ticket.title, status="ON_APPROVAL",
    )
    return {"status": "on_approval"}


@router.post("/{ticket_id}/return-approval")
async def return_from_approval(
    ticket_id: UUID,
    req: ReturnApprovalRequest,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(_get_user),
):
    """Manager returns a ticket from approval back to the author (ON_APPROVAL → REVISION)."""
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status != TicketStatusEnum.ON_APPROVAL:
        raise HTTPException(400, "Ticket is not on approval")

    if req.assignee_id is not None:
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
        actor_name=user["name"],
        assignee_id=str(ticket.assignee_id) if ticket.assignee_id else None,
        assignee_name=ticket.assignee_name, title=ticket.title,
        comment=req.comment, status="REVISION",
    )
    return {"status": "revision"}


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
