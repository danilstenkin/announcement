# Reporting Export (Excel) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `GET /announcements/reports/messages.xlsx` endpoint that returns an Excel report (summary sheet + per-message detail sheet) for a date range, with source and auto/manual filters.

**Architecture:** Three thin layers — `resolve_source` helper (shared with the worker), `ReportingService` (one LEFT-JOIN query + all derivation + summary aggregation), `report_export` (XlsxWriter rendering), and a router that streams the file. Selection and rendering are separate so each is independently testable.

**Tech Stack:** FastAPI, SQLAlchemy async, XlsxWriter (render), openpyxl (read-back in tests), pytest + docker-compose.test.

**Design doc:** `docs/plans/2026-06-02-reporting-export-design.md`

---

## Conventions for every task

**Run tests** (full command, used everywhere below — substitute the test path):

```bash
docker compose -f docker-compose.test.yml run --rm tests bash -c \
  "apt-get update -qq && apt-get install -y -qq gcc >/dev/null && \
   pip install -q -r requirements-test.txt && pytest <TEST_PATH> -v"
```

Follow @superpowers:test-driven-development: write the failing test, watch it fail for the right reason, write minimal code, watch it pass, commit. All timestamps in output are converted to `Asia/Almaty`.

---

## Task 0: Test dependencies

**Files:**
- Modify: `requirements-test.txt`

**Step 1:** Add two lines to `requirements-test.txt` (keep pins in sync with `requirements.txt`):

```
XlsxWriter==3.2.9
openpyxl==3.1.5
```

**Step 2: Commit**

```bash
git add requirements-test.txt
git commit -m "test: add XlsxWriter and openpyxl for report export tests"
```

---

## Task 1: `resolve_source` helper (shared with worker)

**Files:**
- Create: `src/app/pipeline/source.py`
- Modify: `src/app/workers/outlook_worker.py:349-356`
- Test: `tests/test_resolve_source.py`

**Step 1: Write the failing test** (`tests/test_resolve_source.py`):

```python
from pipeline.source import resolve_source


def test_known_addresses_map_to_sources():
    assert resolve_source("sd_info@Fortebank.com") == "ServiceDesk"
    assert resolve_source("komek@Fortebank.com") == "komek"
    assert resolve_source("DAStenkin@Fortebank.com") == "ServiceDesk"


def test_case_insensitive():
    assert resolve_source("SD_INFO@fortebank.com") == "ServiceDesk"


def test_unknown_and_none_default_to_other():
    assert resolve_source("someone@example.com") == "other"
    assert resolve_source(None) == "other"
    assert resolve_source("") == "other"
```

**Step 2: Run test to verify it fails**

Run the test command with `tests/test_resolve_source.py`. Expected: FAIL `ModuleNotFoundError: pipeline.source`.

**Step 3: Write minimal implementation** (`src/app/pipeline/source.py`):

```python
"""Single source of truth for mapping a sender email to its source system."""

_SOURCE_MAP = {
    "sd_info@fortebank.com": "ServiceDesk",
    "komek@fortebank.com": "komek",
    "aaaskarova@fortebank.com": "ServiceDesk",
    "dastenkin@fortebank.com": "ServiceDesk",
}


def resolve_source(sender_email: str | None) -> str:
    """Resolve the source system from a sender email. Unknown/empty -> 'other'."""
    if not sender_email:
        return "other"
    return _SOURCE_MAP.get(sender_email.strip().lower(), "other")
```

**Step 4: Run test to verify it passes.** Expected: PASS.

**Step 5: Refactor the worker to use it.** In `src/app/workers/outlook_worker.py`, add at top with the other imports:

```python
from pipeline.source import resolve_source
```

Replace the inline block (lines ~349-356, the `_SOURCE_MAP = {...}` dict and `source = _SOURCE_MAP.get(...)`) with:

```python
            source = resolve_source(sender_email)
```

**Step 6: Run the full suite** (`pytest` with no path) to confirm nothing broke. Expected: all PASS.

**Step 7: Commit**

```bash
git add src/app/pipeline/source.py src/app/workers/outlook_worker.py tests/test_resolve_source.py
git commit -m "refactor(source): extract resolve_source helper shared by worker and reporting"
```

---

## Task 2: `ReportingService.get_message_rows` — query + derivation

**Files:**
- Create: `src/app/services/reporting.py`
- Test: `tests/test_reporting_service.py`

**MessageRow fields:** `email_id, received_at, sender_email, source, subject,
email_status, processing_type, ticket_status, assignee_name, publish_at,
addressed, idle_hours, announcement_id, announcement_title, published_at, cc_scope`.

**Step 1: Write the failing test** (`tests/test_reporting_service.py`). Helper builders create an email with/without a ticket; assert derivation:

```python
import uuid
from datetime import timedelta

from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.review_ticket import ReviewTicket, TicketStatusEnum
from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time
from services.reporting import ReportingService


async def _email(session, *, status, sender="sd_info@Fortebank.com", days_ago=0):
    e = IncomingEmail(
        outlook_id=f"o-{uuid.uuid4()}", subject="s", body="b",
        sender_email=sender, received_at=get_astana_time() - timedelta(days=days_ago),
        status=status,
    )
    session.add(e); await session.flush()
    return e


async def test_email_without_ticket_is_auto(session):
    e = await _email(session, status=EmailStatusEnum.DONE)
    rows = await ReportingService(session).get_message_rows(
        date_from=(get_astana_time() - timedelta(days=1)).date(),
        date_to=(get_astana_time() + timedelta(days=1)).date(),
    )
    row = next(r for r in rows if r.email_id == e.id)
    assert row.processing_type == "AUTO"
    assert row.source == "ServiceDesk"
    assert row.addressed is True


async def test_email_with_open_ticket_is_manual_unaddressed(session):
    e = await _email(session, status=EmailStatusEnum.RED)
    t = ReviewTicket(email_id=e.id, status=TicketStatusEnum.IN_REVIEW, title="t")
    session.add(t); await session.flush()
    rows = await ReportingService(session).get_message_rows(
        date_from=(get_astana_time() - timedelta(days=1)).date(),
        date_to=(get_astana_time() + timedelta(days=1)).date(),
    )
    row = next(r for r in rows if r.email_id == e.id)
    assert row.processing_type == "MANUAL"
    assert row.addressed is False
    assert row.idle_hours is not None and row.idle_hours >= 0
    assert row.cc_scope == "для КЦ"


async def test_period_filter_excludes_outside(session):
    await _email(session, status=EmailStatusEnum.DONE, days_ago=10)
    rows = await ReportingService(session).get_message_rows(
        date_from=(get_astana_time() - timedelta(days=1)).date(),
        date_to=(get_astana_time() + timedelta(days=1)).date(),
    )
    assert rows == [] or all(r.received_at.date() >= (get_astana_time() - timedelta(days=1)).date() for r in rows)


async def test_processing_filter(session):
    await _email(session, status=EmailStatusEnum.DONE)
    rows = await ReportingService(session).get_message_rows(
        date_from=(get_astana_time() - timedelta(days=1)).date(),
        date_to=(get_astana_time() + timedelta(days=1)).date(),
        processing="MANUAL",
    )
    assert all(r.processing_type == "MANUAL" for r in rows)
```

**Step 2: Run test to verify it fails** — `ModuleNotFoundError: services.reporting`.

**Step 3: Write minimal implementation** (`src/app/services/reporting.py`):

```python
from __future__ import annotations

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
    cc_scope: str                   # «для КЦ» | «не для КЦ» | «—»


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

            if email.status == EmailStatusEnum.GREEN:
                cc_scope = "не для КЦ"
            elif email.status == EmailStatusEnum.RED or ticket is not None or ann is not None:
                cc_scope = "для КЦ"
            else:
                cc_scope = "—"

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
                cc_scope=cc_scope,
            ))
        return rows
```

**Step 4: Run test to verify it passes.** Expected: PASS.

**Step 5: Commit**

```bash
git add src/app/services/reporting.py tests/test_reporting_service.py
git commit -m "feat(reporting): ReportingService.get_message_rows with derivation"
```

---

## Task 3: `compute_summary` — aggregates

**Files:**
- Modify: `src/app/services/reporting.py`
- Test: `tests/test_reporting_summary.py`

**Step 1: Write the failing test** (`tests/test_reporting_summary.py`). Build `MessageRow`s directly (no DB) and assert counts:

```python
from datetime import datetime
from uuid import uuid4

from services.reporting import MessageRow, compute_summary


def _row(**kw):
    base = dict(
        email_id=uuid4(), received_at=datetime(2026, 6, 1), sender_email="x",
        source="ServiceDesk", subject="s", email_status="DONE",
        processing_type="AUTO", ticket_status=None, assignee_name=None,
        publish_at=None, addressed=True, idle_hours=None, announcement_id=None,
        announcement_title=None, published_at=None, cc_scope="для КЦ",
    )
    base.update(kw)
    return MessageRow(**base)


def test_summary_counts():
    rows = [
        _row(source="ServiceDesk", email_status="DONE", processing_type="AUTO", addressed=True, cc_scope="для КЦ"),
        _row(source="komek", email_status="RED", processing_type="MANUAL", addressed=False, cc_scope="для КЦ"),
        _row(source="other", email_status="GREEN", processing_type="MANUAL", addressed=True, cc_scope="не для КЦ"),
    ]
    s = compute_summary(rows)
    assert s.total == 3
    assert s.by_source["ServiceDesk"] == 1 and s.by_source["komek"] == 1
    assert s.by_status["DONE"] == 1 and s.by_status["RED"] == 1 and s.by_status["GREEN"] == 1
    assert s.auto == 1 and s.manual == 2
    assert s.unaddressed_total == 1
    assert s.unaddressed_by_source["komek"] == 1
    assert s.cc_yes == 2 and s.cc_no == 1
```

**Step 2: Run test to verify it fails** — `ImportError: cannot import name 'compute_summary'`.

**Step 3: Add to `src/app/services/reporting.py`:**

```python
from collections import Counter


@dataclass
class Summary:
    total: int
    by_source: dict[str, int]
    by_status: dict[str, int]
    auto: int
    manual: int
    unaddressed_total: int
    unaddressed_by_source: dict[str, int]
    cc_yes: int
    cc_no: int


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
        cc_yes=sum(1 for r in rows if r.cc_scope == "для КЦ"),
        cc_no=sum(1 for r in rows if r.cc_scope == "не для КЦ"),
    )
```

**Step 4: Run test to verify it passes.** Expected: PASS.

**Step 5: Commit**

```bash
git add src/app/services/reporting.py tests/test_reporting_summary.py
git commit -m "feat(reporting): compute_summary aggregates"
```

---

## Task 4: `report_export.build_report_xlsx`

**Files:**
- Create: `src/app/services/report_export.py`
- Test: `tests/test_report_export.py`

**Step 1: Write the failing test** (`tests/test_report_export.py`):

```python
import io
from datetime import datetime
from uuid import uuid4

from openpyxl import load_workbook

from services.reporting import MessageRow, Summary
from services.report_export import build_report_xlsx


def _row():
    return MessageRow(
        email_id=uuid4(), received_at=datetime(2026, 6, 1, 10, 0),
        sender_email="sd_info@fortebank.com", source="ServiceDesk", subject="Тема",
        email_status="DONE", processing_type="AUTO", ticket_status=None,
        assignee_name=None, publish_at=None, addressed=True, idle_hours=None,
        announcement_id=None, announcement_title=None, published_at=None,
        cc_scope="для КЦ",
    )


def test_xlsx_has_two_sheets_and_data():
    summary = Summary(total=1, by_source={"ServiceDesk": 1}, by_status={"DONE": 1},
                      auto=1, manual=0, unaddressed_total=0,
                      unaddressed_by_source={}, cc_yes=1, cc_no=0)
    data = build_report_xlsx([_row()], summary)
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Сводка", "Сообщения"]
    detail = wb["Сообщения"]
    assert detail["A1"].value == "ID письма"
    assert detail["F2"].value == "DONE"          # email_status column
    summary_sheet = wb["Сводка"]
    flat = [c.value for col in summary_sheet.iter_cols() for c in col]
    assert "Всего сообщений:" in flat
    assert 1 in flat
```

**Step 2: Run test to verify it fails** — `ModuleNotFoundError: services.report_export`.

**Step 3: Write minimal implementation** (`src/app/services/report_export.py`):

```python
import io

import xlsxwriter

from services.reporting import MessageRow, Summary

_HEADERS = [
    "ID письма", "Дата получения", "Отправитель", "Источник", "Тема",
    "Статус письма", "Тип обработки", "Статус тикета", "Исполнитель",
    "План публикации", "Отработано", "Длительность простоя (ч)",
    "ID анонса", "Заголовок анонса", "Опубликован", "Содержание (КЦ)",
]


def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def build_report_xlsx(rows: list[MessageRow], summary: Summary) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    bold = wb.add_format({"bold": True})

    s = wb.add_worksheet("Сводка")
    r = 0
    s.write(r, 0, "Всего сообщений:", bold); s.write(r, 1, summary.total); r += 2
    s.write(r, 0, "По источникам:", bold); r += 1
    for k, v in summary.by_source.items():
        s.write(r, 0, k); s.write(r, 1, v); r += 1
    r += 1
    s.write(r, 0, "По статусам:", bold); r += 1
    for k, v in summary.by_status.items():
        s.write(r, 0, k); s.write(r, 1, v); r += 1
    r += 1
    s.write(r, 0, "Авто / Вручную:", bold); r += 1
    s.write(r, 0, "авто"); s.write(r, 1, summary.auto); r += 1
    s.write(r, 0, "вручную"); s.write(r, 1, summary.manual); r += 2
    s.write(r, 0, "Неотработанные:", bold); s.write(r, 1, summary.unaddressed_total); r += 1
    for k, v in summary.unaddressed_by_source.items():
        s.write(r, 0, k); s.write(r, 1, v); r += 1
    r += 1
    s.write(r, 0, "Для КЦ:", bold); s.write(r, 1, summary.cc_yes); r += 1
    s.write(r, 0, "Не для КЦ:", bold); s.write(r, 1, summary.cc_no)

    d = wb.add_worksheet("Сообщения")
    for col, h in enumerate(_HEADERS):
        d.write(0, col, h, bold)
    for i, row in enumerate(rows, start=1):
        d.write(i, 0, str(row.email_id))
        d.write(i, 1, _fmt_dt(row.received_at))
        d.write(i, 2, row.sender_email or "")
        d.write(i, 3, row.source)
        d.write(i, 4, row.subject)
        d.write(i, 5, row.email_status)
        d.write(i, 6, row.processing_type)
        d.write(i, 7, row.ticket_status or "")
        d.write(i, 8, row.assignee_name or "")
        d.write(i, 9, _fmt_dt(row.publish_at))
        d.write(i, 10, "да" if row.addressed else "нет")
        d.write(i, 11, "" if row.idle_hours is None else row.idle_hours)
        d.write(i, 12, str(row.announcement_id) if row.announcement_id else "")
        d.write(i, 13, row.announcement_title or "")
        d.write(i, 14, _fmt_dt(row.published_at))
        d.write(i, 15, row.cc_scope)

    wb.close()
    return buf.getvalue()
```

**Step 4: Run test to verify it passes.** Expected: PASS.

**Step 5: Commit**

```bash
git add src/app/services/report_export.py tests/test_report_export.py
git commit -m "feat(reporting): build_report_xlsx renderer (summary + detail sheets)"
```

---

## Task 5: Router endpoint

**Files:**
- Create: `src/app/routers/reports.py`
- Modify: `src/app/routers/__init__.py`
- Test: `tests/test_reports_api.py`

**Step 1: Write the failing test** (`tests/test_reports_api.py`):

```python
import io
import uuid
from datetime import timedelta

from openpyxl import load_workbook

from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.announcement import get_astana_time

HDR = {"X-User-Id": str(uuid.uuid4())}


async def test_requires_auth(client):
    r = await client.get("/announcements/reports/messages.xlsx",
                         params={"date_from": "2026-06-01", "date_to": "2026-06-30"})
    assert r.status_code == 401


async def test_bad_period_returns_400(client):
    r = await client.get("/announcements/reports/messages.xlsx",
                         params={"date_from": "2026-06-30", "date_to": "2026-06-01"}, headers=HDR)
    assert r.status_code == 400


async def test_returns_xlsx(client, session):
    e = IncomingEmail(outlook_id=f"o-{uuid.uuid4()}", subject="s", body="b",
                      sender_email="sd_info@Fortebank.com",
                      received_at=get_astana_time(), status=EmailStatusEnum.DONE)
    session.add(e); await session.flush()
    r = await client.get("/announcements/reports/messages.xlsx",
                         params={"date_from": (get_astana_time() - timedelta(days=1)).date().isoformat(),
                                 "date_to": (get_astana_time() + timedelta(days=1)).date().isoformat()},
                         headers=HDR)
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Сводка", "Сообщения"]
```

**Step 2: Run test to verify it fails** — 404 (route not registered).

**Step 3: Write the endpoint** (`src/app/routers/reports.py`):

```python
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
```

**Step 4: Register the router.** In `src/app/routers/__init__.py` add the import and include:

```python
from routers.reports import router as reports_router
...
router.include_router(reports_router)
```

**Step 5: Run test to verify it passes.** Expected: PASS.

**Step 6: Run the full suite** (`pytest`, no path). Expected: all PASS.

**Step 7: Commit**

```bash
git add src/app/routers/reports.py src/app/routers/__init__.py tests/test_reports_api.py
git commit -m "feat(reporting): GET /announcements/reports/messages.xlsx endpoint"
```

---

## Task 6: Runtime dependency check

**Files:**
- Verify: `requirements.txt` already contains `XlsxWriter==3.2.9` (it does — confirm).

**Step 1:** Confirm `grep -i xlsxwriter requirements.txt` returns the pinned line. If missing, add `XlsxWriter==3.2.9` and commit. Otherwise no change.

---

## Done criteria

- All tasks committed, full suite green.
- `GET /announcements/reports/messages.xlsx?date_from&date_to[&source][&processing]`
  returns a 2-sheet Excel; 400 on bad period; 401 without `X-User-Id`.
- Worker and report share `resolve_source`.

## Out of scope (phase 2)

- «Выяснение у оунеров» field + UI/action and its report column/count.
- `announcements.email_id` FK to enrich AUTO rows.
- CSV format, dashboards, JSON API.
