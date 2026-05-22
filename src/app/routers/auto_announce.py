import re
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from dependencies.minio import get_minio_client
from models.ai_analysis import AIEmail
from models.email_attachment import EmailAttachment
from models.incoming_emails import EmailStatusEnum, IncomingEmail

_IMG_RE = re.compile(r'<img\s[^>]*/?>', re.IGNORECASE | re.DOTALL)
_PLACEHOLDER = '<span style="display:inline-block;width:20px;height:20px;background:#E0E0E0;border-radius:4px;text-align:center;line-height:20px;font-size:12px;color:#999;">img</span>'


def _strip_images(html: str) -> str:
    return _IMG_RE.sub(_PLACEHOLDER, html)

router = APIRouter(prefix="/auto-announce", tags=["auto-announce"])

_SOURCE_MAP = {
    "sd_info@Fortebank.com": "ServiceDesk",
    "komek@Fortebank.com": "komek",
}


# ── Schemas ─────────────────────────────────────────────

class AttachmentOut(BaseModel):
    id: UUID
    filename: str
    object_key: str
    content_type: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class IncomingEmailOut(BaseModel):
    id: UUID
    sender_email: Optional[str] = None
    source: str = "other"
    status: str
    received_at: Optional[str] = None
    created_at: Optional[str] = None
    ai_title: Optional[str] = None
    ai_email: Optional[str] = None
    ai_summary: Optional[str] = None
    script_ru: Optional[str] = None
    script_kz: Optional[str] = None
    in_knowledge_base: Optional[bool] = None
    attachments: list[AttachmentOut] = []

    model_config = ConfigDict(from_attributes=True)


# ── Endpoints ───────────────────────────────────────────

@router.get("/health")
async def health_check(request: Request):
    return {"status": "healthy"}


@router.get("/gpt")
async def gpt_complition(request: Request, msg: str):
    gpt = await request.app.state.gpt.format_email(
        email_body=msg,
        template="ответь",
        prompt="привет",
    )

    print(msg)
    return {"responce": gpt}


@router.get("/emails/{email_id}/original")
async def get_original_email(email_id: UUID, session: AsyncSession = Depends(get_db)):
    """Возвращает оригинальный HTML письма."""
    result = await session.execute(
        select(IncomingEmail).where(IncomingEmail.id == email_id)
    )
    email = result.scalar_one_or_none()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    if not email.original_html_key:
        raise HTTPException(status_code=404, detail="Original HTML not available")

    minio = get_minio_client()
    response = minio.client.get_object(minio.bucket_name, email.original_html_key)
    html_content = response.read().decode("utf-8")
    response.close()
    response.release_conn()

    return HTMLResponse(content=_strip_images(html_content))


@router.get("/emails", response_model=list[IncomingEmailOut])
async def get_pending_emails(session: AsyncSession = Depends(get_db)):
    """Возвращает письма со статусом != DONE и != PROCESSING с AI анализом и вложениями."""
    result = await session.execute(
        select(IncomingEmail)
        .where(IncomingEmail.status.notin_([EmailStatusEnum.DONE, EmailStatusEnum.PROCESSING]))
        .order_by(IncomingEmail.created_at.desc())
    )
    emails = result.scalars().all()

    email_ids = [e.id for e in emails]
    if not email_ids:
        return []

    # AI анализ
    ai_result = await session.execute(
        select(AIEmail).where(AIEmail.email_id.in_(email_ids))
    )
    ai_map = {a.email_id: a for a in ai_result.scalars().all()}

    # Вложения
    att_result = await session.execute(
        select(EmailAttachment).where(EmailAttachment.email_id.in_(email_ids))
    )
    att_map: dict[UUID, list] = {}
    for att in att_result.scalars().all():
        att_map.setdefault(att.email_id, []).append(att)

    response = []
    for email in emails:
        ai = ai_map.get(email.id)
        response.append(IncomingEmailOut(
            id=email.id,
            sender_email=email.sender_email,
            source=_SOURCE_MAP.get(email.sender_email, "other"),
            status=email.status.value if email.status else "UNKNOWN",
            received_at=email.received_at.isoformat() if email.received_at else None,
            created_at=email.created_at.isoformat() if email.created_at else None,
            ai_title=ai.ai_title if ai else None,
            ai_email=ai.ai_email if ai else None,
            ai_summary=ai.ai_summary if ai else None,
            script_ru=ai.script_ru if ai else None,
            script_kz=ai.script_kz if ai else None,
            in_knowledge_base=ai.in_knowledge_base if ai else None,
            attachments=[AttachmentOut.model_validate(a) for a in att_map.get(email.id, [])],
        ))

    return response
