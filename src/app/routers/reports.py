from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from routers.announcements import CurrentUser
from services.reporting import ReportingService, compute_summary
from services.report_export import build_report_xlsx

router = APIRouter(prefix="/announcements/reports", tags=["reports"])


@router.get("/messages.xlsx")
async def export_messages_xlsx(
    date_from: date = Query(...),
    date_to: date = Query(...),
    source: list[str] | None = Query(None),
    processing: str | None = Query(None),
    current_user: CurrentUser = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    if date_from > date_to:
        raise HTTPException(400, "date_from must be <= date_to")

    rows = await ReportingService(db).get_message_rows(date_from, date_to, source, processing)
    data = build_report_xlsx(rows, compute_summary(rows))

    filename = f"messages_{date_from}_{date_to}.xlsx"
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
