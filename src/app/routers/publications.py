from datetime import datetime
from typing import Optional
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from models.announcement import Announcement, get_astana_time
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)
from services.publication import execute_publication

router = APIRouter(tags=["publications"])


def _actor(
    x_user_id: str = Header(None, alias="X-User-Id"),
    x_user_name: str = Header(None, alias="X-User-Name"),
):
    return {"id": x_user_id, "name": unquote(x_user_name) if x_user_name else x_user_name}


class PublicationOut(BaseModel):
    id: UUID
    kind: str
    publish_at: Optional[str] = None
    status: str
    executed_at: Optional[str] = None
    canceled_at: Optional[str] = None
    actor_name: Optional[str] = None
    created_at: Optional[str] = None


class RepublishRequest(BaseModel):
    publish_at: datetime


class RescheduleRequest(BaseModel):
    publish_at: datetime


def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("/announcements/{announcement_id}/publications", response_model=list[PublicationOut])
async def list_publications(announcement_id: UUID, session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(
        select(AnnouncementPublication)
        .where(AnnouncementPublication.announcement_id == announcement_id)
        .order_by(AnnouncementPublication.created_at.asc())
    )).scalars().all()
    return [
        PublicationOut(
            id=p.id, kind=p.kind.value, publish_at=_iso(p.publish_at),
            status=p.status.value, executed_at=_iso(p.executed_at),
            canceled_at=_iso(p.canceled_at), actor_name=p.actor_name,
            created_at=_iso(p.created_at),
        )
        for p in rows
    ]


@router.post("/announcements/{announcement_id}/republish")
async def republish(
    announcement_id: UUID,
    req: RepublishRequest,
    session: AsyncSession = Depends(get_db),
    actor: dict = Depends(_actor),
):
    ann = (await session.execute(
        select(Announcement).where(Announcement.id == announcement_id)
    )).scalar_one_or_none()
    if ann is None:
        raise HTTPException(404, "Announcement not found")

    pub = AnnouncementPublication(
        announcement_id=announcement_id,
        kind=PublicationKindEnum.REPEAT,
        publish_at=req.publish_at,
        status=PublicationStatusEnum.SCHEDULED,
        actor_id=UUID(actor["id"]) if actor["id"] else None,
        actor_name=actor["name"],
    )
    session.add(pub)
    await session.flush()

    if req.publish_at <= get_astana_time():
        await execute_publication(pub.id, session)
        await session.commit()
        return {"status": "republished", "publication_id": str(pub.id)}

    await session.commit()
    return {"status": "scheduled", "publication_id": str(pub.id)}


@router.patch("/publications/{pub_id}")
async def reschedule(pub_id: UUID, req: RescheduleRequest, session: AsyncSession = Depends(get_db)):
    pub = (await session.execute(
        select(AnnouncementPublication).where(AnnouncementPublication.id == pub_id)
    )).scalar_one_or_none()
    if pub is None:
        raise HTTPException(404, "Publication not found")
    if pub.status != PublicationStatusEnum.SCHEDULED:
        raise HTTPException(400, "Only scheduled publications can be rescheduled")
    pub.publish_at = req.publish_at
    await session.commit()
    return {"status": "rescheduled", "publish_at": req.publish_at.isoformat()}


@router.post("/publications/{pub_id}/cancel")
async def cancel_publication_row(pub_id: UUID, session: AsyncSession = Depends(get_db)):
    pub = (await session.execute(
        select(AnnouncementPublication).where(AnnouncementPublication.id == pub_id)
    )).scalar_one_or_none()
    if pub is None:
        raise HTTPException(404, "Publication not found")
    if pub.status != PublicationStatusEnum.SCHEDULED:
        raise HTTPException(400, "Only scheduled publications can be canceled")
    pub.status = PublicationStatusEnum.CANCELED
    pub.canceled_at = get_astana_time()
    await session.commit()
    return {"status": "canceled"}
