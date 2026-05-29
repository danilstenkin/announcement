# Publication Scheduling & Notifications — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add scheduled publication of announcements: confirm a publish date, "agree" to schedule, an internal worker publishes at the due time, plus re-send and a publication history.

**Architecture:** A new `announcement_publications` table is both the scheduler queue and the publication history. The `Announcement` row is created (hidden) when a ticket is agreed; a single `execute_publication(publication_id)` function makes it visible and notifies operators. An internal asyncio worker (like the existing IMAP watcher) polls the table every 60s and publishes due rows. Immediate "publish" calls the same function synchronously.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async, asyncpg), Alembic, PostgreSQL (schema `cchub_announcements`), pytest + pytest-asyncio, Docker Compose for the test Postgres.

**Design doc:** `docs/plans/2026-05-29-publication-scheduling-design.md`

**Conventions in this repo (read before starting):**
- App code lives in `src/app`; imports are rootless (e.g. `from config import settings`) because `src/app` is on `sys.path`. Tests must replicate this.
- All ORM models inherit `models.base.Base` whose metadata is `MetaData(schema="cchub_announcements")`.
- Enums are created as Postgres enum types **in the schema**: `sa.Enum(..., name='...', schema='cchub_announcements')`.
- Times use `models.announcement.get_astana_time` (`Asia/Almaty`, tz-aware).
- Alembic `env.py` rewrites `+asyncpg` → `+psycopg2` and reads `DATABASE_URL` from env.
- Notifications go out via `services.notifications.NotificationsService` (HTTP webhook), always wrapped in try/except.

**Refinement vs design doc:** Attachment transfer (email → announcement, MinIO copy) happens when the `Announcement` row is created (in `approve` / `publish`), reusing the existing logic. `execute_publication` does NOT touch MinIO — it only flips visibility, sets timestamps/statuses, and notifies. This keeps the worker and publication path testable without mocking MinIO.

---

## Task 0: Test harness (Docker + Postgres)

**Files:**
- Create: `docker-compose.test.yml`
- Create: `requirements-test.txt`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `pytest.ini`
- Create: `tests/test_harness_smoke.py`

**Step 1: Add test deps**

Create `requirements-test.txt`:
```
-r requirements.txt
pytest==8.3.4
pytest-asyncio==0.25.2
aiosqlite==0.20.0
```
(aiosqlite is unused now but harmless; real tests use Postgres.)

**Step 2: pytest config**

Create `pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
pythonpath = src/app
```

**Step 3: Test compose file**

Create `docker-compose.test.yml`:
```yaml
services:
  test-db:
    image: postgres:16
    environment:
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
      POSTGRES_DB: test
    tmpfs:
      - /var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U test"]
      interval: 2s
      timeout: 3s
      retries: 20

  tests:
    image: python:3.12-slim
    working_dir: /app
    depends_on:
      test-db:
        condition: service_healthy
    environment:
      DATABASE_URL: "postgresql+asyncpg://test:test@test-db:5432/test"
      MINIO_ENDPOINT: "test:9000"
      MINIO_ACCESS_KEY: "x"
      MINIO_SECRET_KEY: "x"
      MINIO_BUCKET: "x"
      GPT_URL: "http://localhost"
      GPT_API_KEY: "x"
      WEAVIATE_HOST: "localhost"
      WEAVIATE_PORT: "8080"
      WEAVIATE_GRPS: "50051"
      EVENTS_WEBHOOK_URL: ""
      APP_ENV: "development"
    volumes:
      - .:/app
    command: >
      bash -c "apt-get update -qq && apt-get install -y -qq gcc >/dev/null &&
      pip install -q -r requirements-test.txt &&
      pytest -v"
```
Note: `python-multipart`, `pymupdf` etc. install fine on slim with gcc. If `pymupdf` slows the build, it is only imported lazily inside `parse_attachments`, so it is NOT needed for these tests — but it is in requirements.txt. If install is too slow, create a trimmed `requirements-test.txt` listing only: sqlalchemy, asyncpg, fastapi, pydantic, pydantic-settings, pytz, httpx, openai, loguru, alembic, python-multipart, prometheus-fastapi-instrumentator, pytest, pytest-asyncio. Prefer the trimmed list.

**Step 4: conftest with DB fixtures**

Create `tests/conftest.py`:
```python
import os
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# src/app is on sys.path via pytest.ini pythonpath
from models.base import Base
import models  # noqa: F401  (register all mappers)

TEST_DB_URL = os.environ["DATABASE_URL"]


@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, future=True)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS cchub_announcements"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    """Function-scoped session wrapped in a transaction that is rolled back."""
    connection = await engine.connect()
    trans = await connection.begin()
    maker = async_sessionmaker(bind=connection, expire_on_commit=False, class_=AsyncSession)
    sess = maker()
    try:
        yield sess
    finally:
        await sess.close()
        await trans.rollback()
        await connection.close()
```
Important: inside tests, call `await session.flush()` (not `commit`) so the rollback teardown keeps the DB clean. Code under test that calls `session.commit()` will commit within the outer transaction — acceptable because the outer transaction still rolls back at teardown when using a bound connection. If a test needs real commit isolation, document it then.

**Step 5: Smoke test**

Create `tests/test_harness_smoke.py`:
```python
async def test_schema_and_tables_exist(session):
    from models.review_ticket import ReviewTicket
    # table is creatable / queryable
    result = await session.execute(__import__("sqlalchemy").text(
        "SELECT 1 FROM cchub_announcements.review_tickets LIMIT 0"
    ))
    assert result is not None
```

**Step 6: Run it**

Run: `docker compose -f docker-compose.test.yml run --rm tests`
Expected: build + install, then `test_schema_and_tables_exist PASSED`.

Define a shorthand for later tasks:
```
TEST="docker compose -f docker-compose.test.yml run --rm tests pytest"
```

**Step 7: Commit**
```bash
git add docker-compose.test.yml requirements-test.txt pytest.ini tests/
git commit -m "test: add docker postgres test harness"
```

---

## Task 1: Models & enums

**Files:**
- Create: `src/app/models/publication.py`
- Modify: `src/app/models/review_ticket.py`
- Modify: `src/app/models/announcement.py:31-54` (add `published_at`)
- Modify: `src/app/models/ai_analysis.py` (add `recommended_publish_date`)
- Modify: `src/app/models/__init__.py` (register new model)
- Test: `tests/test_models.py`

**Step 1: Write failing test**

Create `tests/test_models.py`:
```python
import uuid
from datetime import datetime, timedelta

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
    # new statuses exist
    assert TicketStatusEnum.AGREED.value == "AGREED"
    assert TicketStatusEnum.PUBLISHED.value == "PUBLISHED"
```

**Step 2: Run to verify failure**

Run: `$TEST tests/test_models.py -v`
Expected: ImportError / AttributeError (module + fields missing).

**Step 3: Implement `models/publication.py`**
```python
from enum import Enum
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from models.announcement import get_astana_time
from models.base import Base


class PublicationKindEnum(str, Enum):
    PRIMARY = "PRIMARY"
    REPEAT = "REPEAT"


class PublicationStatusEnum(str, Enum):
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    CANCELED = "CANCELED"


class AnnouncementPublication(Base):
    __tablename__ = "announcement_publications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    announcement_id = Column(
        UUID(as_uuid=True),
        ForeignKey("announcements.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    kind = Column(SQLEnum(PublicationKindEnum, schema="cchub_announcements"), nullable=False)
    publish_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(
        SQLEnum(PublicationStatusEnum, schema="cchub_announcements"),
        default=PublicationStatusEnum.SCHEDULED, nullable=False,
    )
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_astana_time, nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
```

**Step 4: Extend `models/review_ticket.py`**

Add to `TicketStatusEnum`:
```python
    AGREED = "AGREED"
    PUBLISHED = "PUBLISHED"
```
Add to `ReviewActionEnum`:
```python
    SCHEDULED = "SCHEDULED"
    RESCHEDULED = "RESCHEDULED"
    PUBLICATION_CANCELED = "PUBLICATION_CANCELED"
    PUBLISHED = "PUBLISHED"
```
Add columns to `ReviewTicket` (after `source`):
```python
    recommended_publish_at = Column(DateTime(timezone=True), nullable=True)
    publish_at = Column(DateTime(timezone=True), nullable=True)
    publish_confirmed = Column(Boolean, default=False, nullable=False)
```
Add `Boolean` to the sqlalchemy import line.

**Step 5: Extend `models/announcement.py`**

Add column (after `updated_at`):
```python
    published_at = Column(DateTime(timezone=True), nullable=True)
```

**Step 6: Extend `models/ai_analysis.py`**

Add column to `AIEmail`:
```python
    recommended_publish_date = Column(DateTime(timezone=True), nullable=True)
```

**Step 7: Register model**

In `src/app/models/__init__.py`, ensure `from models.publication import AnnouncementPublication  # noqa` is present (so `import models` registers the mapper for metadata.create_all and Alembic autogenerate). Check current `__init__.py` first and append.

**Step 8: Run tests**

Run: `$TEST tests/test_models.py -v`
Expected: both tests PASS.

**Step 9: Commit**
```bash
git add src/app/models tests/test_models.py
git commit -m "feat(models): add announcement_publications + ticket publish fields"
```

---

## Task 2: Alembic migration

**Files:**
- Create: `migrations/versions/<rev>_add_publication_scheduling.py`
- Test: reuse `tests/test_models.py` (create_all already validates schema); migration validated by running it against test-db.

**Step 1: Find current head**

Run: `cd "<repo>" && grep -rl "down_revision" migrations/versions | xargs grep -L "" ` — simpler: inspect `migrations/versions/` and the latest `Revises` chain. The current head is the most recent revision (check `1df6cdada05a_merge_review_tickets_with_add_column.py` and `a1b2c3d4e5f6_add_original_html_key...`). Determine head with the running DB later; for the file, set `down_revision` to the actual head id you find.

**Step 2: Write migration**

Create `migrations/versions/aa11bb22cc33_add_publication_scheduling.py`:
```python
"""add publication scheduling

Revision ID: aa11bb22cc33
Revises: <CURRENT_HEAD>
Create Date: 2026-05-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "aa11bb22cc33"
down_revision: Union[str, None] = "<CURRENT_HEAD>"
branch_labels = None
depends_on = None

SCHEMA = "cchub_announcements"


def upgrade() -> None:
    # new enum types
    op.execute(f"CREATE TYPE {SCHEMA}.publicationkindenum AS ENUM ('PRIMARY','REPEAT')")
    op.execute(f"CREATE TYPE {SCHEMA}.publicationstatusenum AS ENUM ('SCHEDULED','PUBLISHED','CANCELED')")

    # extend existing enums (ALTER TYPE ADD VALUE must be outside a txn block in PG;
    # alembic runs each migration in a txn, so use IF NOT EXISTS and commit autocommit)
    with op.get_context().autocommit_block():
        for val in ("AGREED", "PUBLISHED"):
            op.execute(f"ALTER TYPE {SCHEMA}.ticketstatusenum ADD VALUE IF NOT EXISTS '{val}'")
        for val in ("SCHEDULED", "RESCHEDULED", "PUBLICATION_CANCELED", "PUBLISHED"):
            op.execute(f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS '{val}'")

    # new columns on review_tickets
    op.add_column("review_tickets", sa.Column("recommended_publish_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.add_column("review_tickets", sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.add_column("review_tickets", sa.Column("publish_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()), schema=SCHEMA)

    # announcements.published_at
    op.add_column("announcements", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)

    # ai_emails.recommended_publish_date
    op.add_column("ai_emails", sa.Column("recommended_publish_date", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)

    # publications table
    op.create_table(
        "announcement_publications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("announcement_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.announcements.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.Enum(name="publicationkindenum", schema=SCHEMA, create_type=False), nullable=False),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("status", sa.Enum(name="publicationstatusenum", schema=SCHEMA, create_type=False), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("announcement_publications", schema=SCHEMA)
    op.drop_column("ai_emails", "recommended_publish_date", schema=SCHEMA)
    op.drop_column("announcements", "published_at", schema=SCHEMA)
    op.drop_column("review_tickets", "publish_confirmed", schema=SCHEMA)
    op.drop_column("review_tickets", "publish_at", schema=SCHEMA)
    op.drop_column("review_tickets", "recommended_publish_at", schema=SCHEMA)
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.publicationstatusenum")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.publicationkindenum")
    # NOTE: enum values added to ticketstatusenum/reviewactionenum are not removed
    # (Postgres cannot drop enum values); acceptable.
```

**Step 3: Validate migration against a fresh DB**

Run:
```bash
docker compose -f docker-compose.test.yml run --rm \
  -e DATABASE_URL="postgresql+asyncpg://test:test@test-db:5432/test" \
  tests bash -c "pip install -q -r requirements-test.txt && \
    psql -h test-db -U test -d test -c 'CREATE SCHEMA IF NOT EXISTS cchub_announcements' || true; \
    alembic upgrade head && alembic downgrade -1 && alembic upgrade head"
```
(If `alembic upgrade head` requires base tables that the test-db lacks, run against a DB seeded by all prior migrations — i.e. start from empty and upgrade the full chain. The migration chain must be complete. If the head detection is wrong, alembic will error "Can't locate revision" — fix `down_revision`.)
Expected: upgrade → downgrade → upgrade all succeed.

**Step 4: Commit**
```bash
git add migrations/versions/aa11bb22cc33_add_publication_scheduling.py
git commit -m "feat(db): migration for publication scheduling"
```

---

## Task 3: Recommended publish date from GPT

**Files:**
- Modify: `src/app/dependencies/gpt.py` (add `recommended_publish_date` to json_schema, optional)
- Create: `src/app/pipeline/recommend_date.py` (parse helper)
- Modify: `src/app/pipeline/steps/ai_analysis.py` (wire into ctx + AIEmail)
- Modify: `src/app/pipeline/context.py` (add field)
- Modify: `src/app/pipeline/processor.py` (set ticket.recommended_publish_at)
- Test: `tests/test_recommend_date.py`

**Step 1: Write failing test**

Create `tests/test_recommend_date.py`:
```python
from datetime import datetime
import pytz
from pipeline.recommend_date import parse_recommended_date

ALMATY = pytz.timezone("Asia/Almaty")


def test_parses_iso_date_to_midnight_almaty():
    dt = parse_recommended_date("2026-06-01", received_at=datetime(2026, 5, 20, tzinfo=pytz.UTC))
    assert dt.tzinfo is not None
    assert dt.hour == 0 and dt.minute == 0
    assert dt.year == 2026 and dt.month == 6 and dt.day == 1


def test_none_falls_back_to_received_day():
    received = ALMATY.localize(datetime(2026, 5, 20, 14, 30))
    dt = parse_recommended_date(None, received_at=received)
    assert dt.year == 2026 and dt.month == 5 and dt.day == 20
    assert dt.hour == 0 and dt.minute == 0


def test_garbage_falls_back():
    received = ALMATY.localize(datetime(2026, 5, 20, 14, 30))
    dt = parse_recommended_date("не раньше пятницы", received_at=received)
    assert dt.day == 20 and dt.hour == 0
```

**Step 2: Run to verify failure**

Run: `$TEST tests/test_recommend_date.py -v` → ImportError.

**Step 3: Implement `pipeline/recommend_date.py`**
```python
from __future__ import annotations
from datetime import datetime
import pytz

ALMATY = pytz.timezone("Asia/Almaty")


def parse_recommended_date(value: str | None, received_at: datetime) -> datetime:
    """Parse an ISO-ish date string to 00:00 Asia/Almaty.
    Falls back to the received day at 00:00 on None/unparseable input."""
    if value:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                naive = datetime.strptime(value.strip()[:len(datetime.now().strftime(fmt))], fmt)
                return ALMATY.localize(datetime(naive.year, naive.month, naive.day, 0, 0))
            except (ValueError, TypeError):
                continue
    # fallback: received day at midnight Almaty
    if received_at.tzinfo is None:
        received_at = ALMATY.localize(received_at)
    local = received_at.astimezone(ALMATY)
    return ALMATY.localize(datetime(local.year, local.month, local.day, 0, 0))
```
(Keep parsing strict & simple — GPT returns ISO `YYYY-MM-DD`. Natural-language goes to fallback by design.)

**Step 4: Add GPT schema field**

In `dependencies/gpt.py` `format_email`, add to `properties`:
```python
            "recommended_publish_date": {
                "type": ["string", "null"],
                "description": "Рекомендуемая дата публикации в формате YYYY-MM-DD из текста (например из 'Начало работ'), или null",
            },
```
and append `"recommended_publish_date"` to `required` (strict schema requires all keys listed).

**Step 5: Wire into pipeline**

- `pipeline/context.py`: add `recommended_publish_at: datetime | None = None`.
- `pipeline/steps/ai_analysis.py`: after parsing GPT result:
```python
    from pipeline.recommend_date import parse_recommended_date
    ctx.recommended_publish_at = parse_recommended_date(
        result.get("recommended_publish_date"), ctx.received_at
    )
```
  and pass `recommended_publish_date=ctx.recommended_publish_at` into the `AIEmail(...)` constructor.
- `pipeline/processor.py`: when creating the `ReviewTicket`, add `recommended_publish_at=ctx.recommended_publish_at`.

**Step 6: Run tests**

Run: `$TEST tests/test_recommend_date.py -v` → PASS.

**Step 7: Commit**
```bash
git add src/app/pipeline src/app/dependencies/gpt.py tests/test_recommend_date.py
git commit -m "feat(pipeline): GPT recommended publish date with fallback"
```

---

## Task 4: `execute_publication` core

**Files:**
- Create: `src/app/services/publication.py`
- Test: `tests/test_execute_publication.py`

This function is the single publication path. It does NOT touch MinIO.

**Step 1: Write failing test**

Create `tests/test_execute_publication.py`:
```python
import uuid
from datetime import timedelta

import pytest
from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time
from models.publication import AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum
from services.publication import execute_publication


@pytest.fixture
def captured_events(monkeypatch):
    events = []

    async def fake_new(self, announcement):
        events.append(("NEW", announcement.id))

    async def fake_repeat(self, announcement):
        events.append(("REPEAT", announcement.id))

    monkeypatch.setattr("services.publication.NotificationsService.publish_new_announcement", fake_new)
    monkeypatch.setattr("services.publication.NotificationsService.publish_repeat_announcement", fake_repeat, raising=False)
    return events


async def _make(session, kind, hidden=True):
    ann = Announcement(title="t", category=AnnouncementCategoryEnum.NEW, text="b",
                       is_hidden=hidden, created_by=uuid.UUID(int=0))
    session.add(ann)
    await session.flush()
    pub = AnnouncementPublication(
        announcement_id=ann.id, kind=kind,
        publish_at=get_astana_time(), status=PublicationStatusEnum.SCHEDULED,
    )
    session.add(pub)
    await session.flush()
    return ann, pub


async def test_primary_makes_visible_and_notifies(session, captured_events):
    ann, pub = await _make(session, PublicationKindEnum.PRIMARY, hidden=True)
    await execute_publication(pub.id, session)
    await session.refresh(ann); await session.refresh(pub)
    assert ann.is_hidden is False
    assert ann.published_at is not None
    assert pub.status == PublicationStatusEnum.PUBLISHED
    assert pub.executed_at is not None
    assert ("NEW", ann.id) in captured_events


async def test_repeat_keeps_visibility_and_sends_repeat(session, captured_events):
    ann, pub = await _make(session, PublicationKindEnum.REPEAT, hidden=False)
    await execute_publication(pub.id, session)
    await session.refresh(ann); await session.refresh(pub)
    assert ann.is_hidden is False
    assert pub.status == PublicationStatusEnum.PUBLISHED
    assert ("REPEAT", ann.id) in captured_events


async def test_already_published_is_noop(session, captured_events):
    ann, pub = await _make(session, PublicationKindEnum.PRIMARY)
    pub.status = PublicationStatusEnum.PUBLISHED
    await session.flush()
    await execute_publication(pub.id, session)
    assert captured_events == []
```

**Step 2: Run to verify failure**

Run: `$TEST tests/test_execute_publication.py -v` → ImportError.

**Step 3: Implement `services/publication.py`**
```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from logger import get_logger
from models.announcement import Announcement, get_astana_time
from models.publication import (
    AnnouncementPublication, PublicationKindEnum, PublicationStatusEnum,
)
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.review_ticket import ReviewTicket, ReviewHistory, ReviewActionEnum, TicketStatusEnum
from services.notifications import NotificationsService

logger = get_logger(__name__)


async def execute_publication(publication_id: UUID, session: AsyncSession) -> None:
    """Single publication path. Makes the announcement visible (PRIMARY),
    marks the publication PUBLISHED, notifies operators. No MinIO here."""
    pub = (await session.execute(
        select(AnnouncementPublication).where(AnnouncementPublication.id == publication_id)
    )).scalar_one_or_none()
    if pub is None or pub.status != PublicationStatusEnum.SCHEDULED:
        logger.info("execute_publication skipped: id={id} status={s}",
                    id=publication_id, s=getattr(pub, "status", None))
        return

    ann = (await session.execute(
        select(Announcement).where(Announcement.id == pub.announcement_id)
    )).scalar_one()

    now = get_astana_time()
    if pub.kind == PublicationKindEnum.PRIMARY:
        ann.is_hidden = False
        ann.published_at = now

    pub.status = PublicationStatusEnum.PUBLISHED
    pub.executed_at = now

    # advance the related ticket + email for PRIMARY
    if pub.kind == PublicationKindEnum.PRIMARY:
        await _finalize_ticket_and_email(ann, session)

    await session.flush()

    try:
        notifications = NotificationsService(settings.EVENTS_WEBHOOK_URL)
        if pub.kind == PublicationKindEnum.PRIMARY:
            await notifications.publish_new_announcement(ann)
        else:
            await notifications.publish_repeat_announcement(ann)
    except Exception as e:
        logger.error("Publication notify failed: {err}", err=e)


async def _finalize_ticket_and_email(ann: Announcement, session: AsyncSession) -> None:
    """Best-effort: mark the originating ticket PUBLISHED and email DONE.
    Linked via the email/ticket created earlier in approve()."""
    # Ticket linkage: see Task 6 — we look up the ticket whose announcement we created.
    # For now linkage is by a ticket in AGREED with this announcement; resolved in Task 6.
    return
```
Note: `_finalize_ticket_and_email` is fleshed out in Task 6 once the ticket↔announcement link is decided. For Task 4, the two PRIMARY/REPEAT/no-op behaviours and notifications are what the tests assert.

**Step 4: Add `publish_repeat_announcement` to notifications**

In `services/notifications.py`, add a method mirroring `publish_new_announcement` but with `event_type="REPEAT_ANNOUNCEMENT"` and message `f"Напоминание: «{announcement.title}»"`.

**Step 5: Run tests**

Run: `$TEST tests/test_execute_publication.py -v` → 3 PASS.

**Step 6: Commit**
```bash
git add src/app/services/publication.py src/app/services/notifications.py tests/test_execute_publication.py
git commit -m "feat(publish): execute_publication core + REPEAT notification"
```

---

## Task 5: Ticket ↔ announcement link + confirm-date endpoint

**Decision:** Add `announcement_id` (nullable UUID) to `review_tickets` so a ticket knows its created announcement. This is the cleanest link for `_finalize_ticket_and_email` and for the UI.

**Files:**
- Modify: `src/app/models/review_ticket.py` (add `announcement_id` column)
- Modify: migration `aa11bb22cc33...` (add column) OR a follow-up migration — prefer editing Task 2 migration if not yet applied to any real DB; otherwise new migration. Default: add to the Task 2 migration file (both upgrade add_column + downgrade drop_column).
- Modify: `src/app/routers/tickets.py` (add `POST /{ticket_id}/confirm-date`)
- Test: `tests/test_tickets_confirm_date.py`

**Step 1: Add column + migration entry**

Model: `announcement_id = Column(UUID(as_uuid=True), nullable=True)` on `ReviewTicket`.
Migration: `op.add_column("review_tickets", sa.Column("announcement_id", UUID(as_uuid=True), nullable=True), schema=SCHEMA)` and matching `drop_column` in downgrade.

**Step 2: Write failing test**

Create `tests/test_tickets_confirm_date.py` using FastAPI `httpx.AsyncClient` against the app with `get_db` overridden to the test `session`. Add an app-client fixture to `conftest.py`:
```python
@pytest_asyncio.fixture
async def client(session):
    import httpx
    from main import app
    from dependencies import get_db
    async def _override():
        yield session
    app.dependency_overrides[get_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/announce") as ac:
        yield ac
    app.dependency_overrides.clear()
```
WARNING: importing `main` triggers `lifespan` only on startup events, not on import, and starts no workers under ASGITransport unless you trigger lifespan. Use `httpx.ASGITransport(app=app)` WITHOUT lifespan so the IMAP/publish workers do NOT start during tests. Confirm no import-time side effects (engine creation is import-time but harmless; `init_minio`/workers run only in lifespan).

Test body:
```python
async def test_confirm_date_sets_publish_at_and_flag(client, session):
    # seed email + ticket
    from models.incoming_emails import IncomingEmail, EmailStatusEnum
    from models.review_ticket import ReviewTicket, TicketStatusEnum
    from models.announcement import get_astana_time
    email = IncomingEmail(outlook_id="m2", subject="s", body="b",
                          received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email); await session.flush()
    t = ReviewTicket(email_id=email.id, status=TicketStatusEnum.IN_REVIEW,
                     recommended_publish_at=get_astana_time())
    session.add(t); await session.flush()

    r = await client.post(f"/auto-announce/tickets/{t.id}/confirm-date", json={})
    assert r.status_code == 200
    await session.refresh(t)
    assert t.publish_confirmed is True
    assert t.publish_at is not None  # falls back to recommended

async def test_confirm_date_with_explicit_time(client, session):
    ...  # post {"publish_at": "2026-06-02T09:00:00+05:00"} and assert it is stored
```

**Step 3: Run to verify failure** → 404/route missing.

**Step 4: Implement endpoint** in `routers/tickets.py`:
```python
class ConfirmDateRequest(BaseModel):
    publish_at: Optional[datetime] = None

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
```
Add `from datetime import datetime` import.

**Step 5: Run tests** → PASS.

**Step 6: Commit**
```bash
git add src/app/models/review_ticket.py migrations tests/conftest.py tests/test_tickets_confirm_date.py src/app/routers/tickets.py
git commit -m "feat(tickets): confirm-date endpoint + ticket->announcement link"
```

---

## Task 6: Repurpose `approve` → schedule (and finalize linkage)

**Files:**
- Modify: `src/app/routers/tickets.py` (`approve_ticket`)
- Modify: `src/app/services/publication.py` (`_finalize_ticket_and_email`)
- Create: `src/app/services/announcement_factory.py` (shared "create announcement from ticket + transfer attachments")
- Test: `tests/test_tickets_approve.py`

**Step 1: Extract shared announcement creation**

Create `services/announcement_factory.py` with `create_announcement_from_ticket(ticket, session, hidden: bool) -> Announcement` that:
- builds the `Announcement` (title/body/scripts/source, `is_hidden=hidden`, system user fields),
- flushes,
- sets `ticket.announcement_id = ann.id`,
- transfers email attachments to the announcement (move the existing `_transfer_attachments` logic from `pipeline/steps/publish.py` here; reuse for both auto-publish and ticket approve). MinIO calls live here, not in `execute_publication`.

**Step 2: Write failing test**

`tests/test_tickets_approve.py`:
```python
async def test_approve_requires_confirmed_date(client, session):
    # ticket without publish_confirmed -> 400
    ...

async def test_approve_schedules_future_publication(client, session, monkeypatch):
    # patch announcement_factory attachment transfer to no-op (no MinIO in tests)
    # seed ticket with publish_confirmed=True, publish_at in the future
    # POST approve -> 200
    # assert: announcement created hidden, ticket.status == AGREED,
    #         a SCHEDULED PRIMARY publication exists with publish_at == ticket.publish_at,
    #         announcement NOT visible (is_hidden True)
    ...
```
Patch attachments: `monkeypatch.setattr("services.announcement_factory._transfer_attachments", async_noop)`.

**Step 3: Run to verify failure.**

**Step 4: Rewrite `approve_ticket`**
```python
@router.post("/{ticket_id}/approve")
async def approve_ticket(ticket_id, session=Depends(get_db), user=Depends(_get_user)):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (TicketStatusEnum.APPROVED, TicketStatusEnum.REJECTED, TicketStatusEnum.PUBLISHED):
        raise HTTPException(400, "Ticket is already closed")
    if not ticket.title:
        raise HTTPException(400, "Cannot approve: title is empty")
    if not ticket.publish_confirmed or ticket.publish_at is None:
        raise HTTPException(400, "Publish date must be confirmed first")

    ann = await create_announcement_from_ticket(ticket, session, hidden=True)
    pub = AnnouncementPublication(
        announcement_id=ann.id, kind=PublicationKindEnum.PRIMARY,
        publish_at=ticket.publish_at, status=PublicationStatusEnum.SCHEDULED,
        actor_id=UUID(user["id"]) if user["id"] else None, actor_name=user["name"],
    )
    session.add(pub)
    ticket.status = TicketStatusEnum.AGREED
    session.add(ReviewHistory(ticket_id=ticket.id, action=ReviewActionEnum.APPROVED,
                              actor_id=..., actor_name=user["name"],
                              comment=f"Запланирована публикация на {ticket.publish_at.isoformat()}"))
    await session.commit()
    await _notify_ticket(event_type="APPROVED", ticket_id=str(ticket.id),
                         actor_name=user["name"], title=ticket.title, status="AGREED")
    return {"status": "agreed", "announcement_id": str(ann.id), "publish_at": ticket.publish_at.isoformat()}
```
Remove the old immediate-publish/attachment block from `approve_ticket` (now in factory + execute_publication).

**Step 5: Finalize `_finalize_ticket_and_email`** in `services/publication.py`:
```python
async def _finalize_ticket_and_email(ann, session):
    ticket = (await session.execute(
        select(ReviewTicket).where(ReviewTicket.announcement_id == ann.id)
    )).scalar_one_or_none()
    if ticket:
        ticket.status = TicketStatusEnum.PUBLISHED
        session.add(ReviewHistory(ticket_id=ticket.id, action=ReviewActionEnum.PUBLISHED,
                                  actor_name="system", comment="Опубликовано по расписанию"))
        await session.execute(
            update(IncomingEmail).where(IncomingEmail.id == ticket.email_id)
            .values(status=EmailStatusEnum.DONE)
        )
```
Add `from sqlalchemy import update` import.

**Step 6: Run tests** → PASS. Re-run Task 4 tests to confirm no regression: `$TEST tests/test_execute_publication.py tests/test_tickets_approve.py -v`.

**Step 7: Commit**
```bash
git add src/app/routers/tickets.py src/app/services tests/test_tickets_approve.py
git commit -m "feat(tickets): approve now schedules publication instead of instant publish"
```

---

## Task 7: Publish-now endpoint

**Files:**
- Modify: `src/app/routers/tickets.py` (`POST /{ticket_id}/publish`)
- Test: `tests/test_tickets_publish_now.py`

**Step 1: Write failing test**
```python
async def test_publish_now_makes_visible_immediately(client, session, monkeypatch):
    # patch attachment transfer + notifications
    # ticket with publish_confirmed True
    # POST /publish -> 200
    # assert announcement is_hidden False, published_at set,
    #        ticket.status PUBLISHED, publication PUBLISHED
```

**Step 2: Run to verify failure.**

**Step 3: Implement**
```python
@router.post("/{ticket_id}/publish")
async def publish_now(ticket_id, session=Depends(get_db), user=Depends(_get_user)):
    ticket = await _get_ticket(ticket_id, session)
    if ticket.status in (TicketStatusEnum.PUBLISHED,):
        raise HTTPException(400, "Already published")
    if not ticket.title:
        raise HTTPException(400, "Cannot publish: title is empty")
    # publish-now does not require prior date confirmation; it IS the confirmation
    now = get_astana_time()
    ann = await create_announcement_from_ticket(ticket, session, hidden=True)
    pub = AnnouncementPublication(announcement_id=ann.id, kind=PublicationKindEnum.PRIMARY,
                                  publish_at=now, status=PublicationStatusEnum.SCHEDULED,
                                  actor_id=..., actor_name=user["name"])
    session.add(pub)
    ticket.publish_at = now
    ticket.publish_confirmed = True
    await session.flush()
    await execute_publication(pub.id, session)   # synchronous
    await session.commit()
    return {"status": "published", "announcement_id": str(ann.id)}
```
Import `get_astana_time`, `execute_publication`.

**Step 4: Run tests** → PASS.

**Step 5: Commit**
```bash
git add src/app/routers/tickets.py tests/test_tickets_publish_now.py
git commit -m "feat(tickets): publish-now endpoint via unified path"
```

---

## Task 8: Cancel scheduled publication

**Files:**
- Modify: `src/app/routers/tickets.py` (`POST /{ticket_id}/cancel-publication`)
- Test: `tests/test_cancel_publication.py`

**Behaviour:** find the ticket's SCHEDULED PRIMARY publication → `CANCELED` + `canceled_at`; keep announcement hidden; ticket back to `IN_REVIEW`; history `PUBLICATION_CANCELED`.

**Step 1: Write failing test** — assert publication CANCELED, announcement still hidden, ticket IN_REVIEW.

**Step 2: Run to verify failure.**

**Step 3: Implement** (select publication by `announcement_id == ticket.announcement_id` and `status==SCHEDULED`; set canceled; set ticket.status = IN_REVIEW; add history; commit).

**Step 4: Run tests** → PASS.

**Step 5: Commit**
```bash
git add src/app/routers/tickets.py tests/test_cancel_publication.py
git commit -m "feat(tickets): cancel scheduled publication"
```

---

## Task 9: Publications history, re-send, reschedule, cancel-row

**Files:**
- Modify: `src/app/routers/announcements.py` (or a new `routers/publications.py`; prefer new router mounted in `routers/__init__.py`)
- Test: `tests/test_publications_api.py`

**Endpoints:**
- `GET /announcements/{id}/publications` → list rows (kind, publish_at, status, executed_at, canceled_at, actor_name), sorted by created_at.
- `POST /announcements/{id}/republish` body `{publish_at}` → create REPEAT SCHEDULED row; if `publish_at <= now` call `execute_publication` synchronously.
- `PATCH /publications/{pub_id}` body `{publish_at}` → only if SCHEDULED; update publish_at; add ReviewHistory(RESCHEDULED) if a linked ticket exists (else skip history).
- `POST /publications/{pub_id}/cancel` → SCHEDULED→CANCELED.

**Step 1: Write failing tests** covering: history returns primary+repeat ordered; republish future creates SCHEDULED REPEAT; republish now publishes (REPEAT notification); reschedule changes time only when SCHEDULED; cancel sets CANCELED.

**Step 2..4:** implement each, run tests green.

**Step 5: Commit**
```bash
git add src/app/routers tests/test_publications_api.py
git commit -m "feat(publications): history, re-send, reschedule, cancel"
```

---

## Task 10: Derived `display_status`

**Files:**
- Create: `src/app/services/ticket_status.py` (`compute_display_status(ticket, publications) -> str`)
- Modify: `src/app/routers/tickets.py` (include `display_status` in list/detail; fetch publications per ticket)
- Test: `tests/test_display_status.py`

**Rules (pure function, unit-tested without DB):**
| display_status | condition |
|---|---|
| `NO_DATE` | not publish_confirmed |
| `IN_PROGRESS` | ticket IN_REVIEW or REVISION |
| `ON_APPROVAL` | ticket ON_APPROVAL (future; not yet used) |
| `AGREED` | ticket AGREED and a SCHEDULED publication exists |
| `OVERDUE` | publish_confirmed, no SCHEDULED publication, exists a CANCELED one, not PUBLISHED |
| `PUBLISHED` | ticket PUBLISHED |

**Step 1: Write failing test** with a small fake (namedtuple-like) ticket + list of publication statuses, asserting each branch.

**Step 2: Run to verify failure.**

**Step 3: Implement** `compute_display_status` taking `(status, publish_confirmed, pub_statuses: list[str])`.

**Step 4: Wire into list/detail** — add `display_status: str` to `TicketListOut`/`TicketDetailOut`; in `list_tickets`/`get_ticket` load publications for the relevant announcement(s) and compute. For list, batch-load publications by `announcement_id in (...)`.

**Step 5: Run tests** → PASS.

**Step 6: Commit**
```bash
git add src/app/services/ticket_status.py src/app/routers/tickets.py tests/test_display_status.py
git commit -m "feat(tickets): derived display_status for UI"
```

---

## Task 11: Scheduler worker

**Files:**
- Create: `src/app/workers/publish_worker.py`
- Modify: `src/app/main.py` (start task in lifespan)
- Modify: `src/app/config.py` (add `PUBLISH_POLL_INTERVAL: int = 60`)
- Test: `tests/test_publish_worker.py` (test the single-pass function, not the infinite loop)

**Step 1: Write failing test**

Test a `publish_due_once(session, now)` helper (NOT the infinite loop) so it is deterministic:
```python
async def test_publishes_only_due_scheduled(session, monkeypatch):
    # patch notifications to no-op
    # create 3 publications: due+SCHEDULED, future+SCHEDULED, due+PUBLISHED
    # call publish_due_once(session, now=<between>)
    # assert only the due+SCHEDULED became PUBLISHED
async def test_one_failure_does_not_block_others(session, monkeypatch):
    # make execute_publication raise for one id; assert others still PUBLISHED
```

**Step 2: Run to verify failure.**

**Step 3: Implement `workers/publish_worker.py`**
```python
import asyncio
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from dependencies.database import async_session
from logger import get_logger
from models.announcement import get_astana_time
from models.publication import AnnouncementPublication, PublicationStatusEnum
from services.publication import execute_publication

logger = get_logger(__name__)


async def publish_due_once(session: AsyncSession, now: datetime) -> int:
    rows = (await session.execute(
        select(AnnouncementPublication.id)
        .where(AnnouncementPublication.status == PublicationStatusEnum.SCHEDULED)
        .where(AnnouncementPublication.publish_at <= now)
        .order_by(AnnouncementPublication.publish_at)
        .with_for_update(skip_locked=True)
    )).scalars().all()
    count = 0
    for pub_id in rows:
        try:
            await execute_publication(pub_id, session)
            count += 1
        except Exception:
            logger.exception("Failed to publish publication id={id}", id=pub_id)
    return count


async def run_publish_scheduler():
    interval = settings.PUBLISH_POLL_INTERVAL
    logger.info("Publish scheduler started, interval={i}s", i=interval)
    while True:
        try:
            async with async_session() as session:
                async with session.begin():
                    n = await publish_due_once(session, get_astana_time())
                if n:
                    logger.info("Published {n} scheduled announcements", n=n)
        except asyncio.CancelledError:
            logger.info("Publish scheduler cancelled")
            raise
        except Exception:
            logger.exception("Publish scheduler tick crashed")
        await asyncio.sleep(interval)
```
Note for tests: in the test, call `publish_due_once(session, now)` directly within the test transaction; `with_for_update(skip_locked=True)` works on Postgres. For the "one failure" test, monkeypatch `services.publication.execute_publication`? It is imported by name in the worker — patch `workers.publish_worker.execute_publication`.

**Step 4: Wire into lifespan** in `main.py`:
```python
from workers.publish_worker import run_publish_scheduler
...
    publish_task = asyncio.create_task(run_publish_scheduler())
    logger.info("Publish scheduler started")
...
    publish_task.cancel()
    try:
        await publish_task
    except asyncio.CancelledError:
        pass
```
Add `PUBLISH_POLL_INTERVAL: int = 60` to `config.Settings`.

**Step 5: Run tests** → PASS.

**Step 6: Commit**
```bash
git add src/app/workers/publish_worker.py src/app/main.py src/app/config.py tests/test_publish_worker.py
git commit -m "feat(worker): scheduled publication worker"
```

---

## Task 12: Full suite + cleanup

**Step 1:** Run the whole suite: `$TEST -v`. Expected: all green.
**Step 2:** Re-read `routers/__init__.py` to ensure any new router is mounted.
**Step 3:** Grep for the old instant-publish path in `pipeline/steps/publish.py` — confirm the auto-publish (GREEN) flow still works and now optionally reuses `create_announcement_from_ticket`/`execute_publication` if desired (out of scope to refactor; just verify it is not broken by the attachment-transfer move). If `_transfer_attachments` was moved, update `pipeline/steps/publish.py` to import from the new factory.
**Step 4:** Commit any cleanups.
```bash
git commit -am "chore: wire up routers, verify auto-publish path"
```

---

## Out of scope (later blocks)
- Roles/permissions (trainer vs manager), `ON_APPROVAL` status, "send for approval".
- Audience targeting.
- Knowledge base + versions.
- Archive view, draft autosave.

## Risks / watch-outs
- `ALTER TYPE ... ADD VALUE` must run in an autocommit block (handled via `op.get_context().autocommit_block()`).
- Test client must NOT start the lifespan (workers) — use `ASGITransport(app=app)` without lifespan.
- `session.commit()` inside endpoints during tests commits into the outer transaction bound to a single connection; teardown rollback still cleans up. If cross-connection visibility is needed, switch that test to the truncate strategy.
- Keep `execute_publication` free of MinIO so the worker is testable.
