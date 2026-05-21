from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from dependencies.minio import get_minio_client
from models.incoming_emails import IncomingEmail

router = APIRouter(prefix="/auto-announce", tags=["auto-announce"])


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

    return HTMLResponse(content=html_content)
