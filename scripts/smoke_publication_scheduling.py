"""
End-to-end smoke test for the publication-scheduling feature.

Runs against a LIVE instance of the app + the real database it points at.
- HTTP via httpx with trust_env=False, so it ignores HTTP(S)_PROXY / corporate proxy
  (no --noproxy needed).
- Seeds a review ticket directly through the ORM (no psql required).
- Exercises every new endpoint, the scheduler worker, and the double-create guard.
- Cleans up the data it created at the end (best effort).

Run from the REPO ROOT (so config picks up .env) with the project's venv active:

    python scripts/smoke_publication_scheduling.py

Optional env:
    SMOKE_BASE_URL   default http://localhost:8004
    SMOKE_WORKER     "1" to also test the background scheduler (waits up to ~130s)
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid
from datetime import timedelta

# Make the app importable (rootless imports like `from config import settings`).
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "app"))


def _load_env() -> None:
    """Load a .env into os.environ before importing config.

    config.Settings reads ".env" relative to CWD; the file may instead live in
    src/app (next to where the app is started). Search common spots and inject
    keys that aren't already set. Override with SMOKE_ENV_FILE=path/to/.env.
    """
    candidates = []
    if os.environ.get("SMOKE_ENV_FILE"):
        candidates.append(pathlib.Path(os.environ["SMOKE_ENV_FILE"]))
    candidates += [ROOT / ".env", ROOT / "src" / "app" / ".env", pathlib.Path.cwd() / ".env"]
    for p in candidates:
        try:
            if not p or not p.is_file():
                continue
        except OSError:
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        print(f"Loaded env from {p}")
        return
    print(
        "WARNING: no .env found (looked in repo root, src/app, cwd). "
        "Set SMOKE_ENV_FILE=path\\to\\.env or run from the folder containing .env."
    )


_load_env()

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from dependencies.database import async_session  # noqa: E402
from models.announcement import Announcement, get_astana_time  # noqa: E402
from models.incoming_emails import EmailStatusEnum, IncomingEmail  # noqa: E402
from models.publication import (  # noqa: E402
    AnnouncementPublication,
    PublicationKindEnum,
    PublicationStatusEnum,
)
from models.review_ticket import ReviewTicket, TicketStatusEnum  # noqa: E402

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8004")
USER_ID = "00000000-0000-0000-0000-000000000001"
HEADERS = {"X-User-Id": USER_ID, "X-User-Name": "Smoke"}
TEST_WORKER = os.environ.get("SMOKE_WORKER") == "1"

_results: list[tuple[str, bool, str]] = []
_created_announcements: list[str] = []
_seeded_email_ids: list[uuid.UUID] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))


# ----------------------------- ORM helpers --------------------------------- #

async def seed_ticket(title: str = "Smoke ticket") -> tuple[uuid.UUID, uuid.UUID]:
    """Insert an incoming email + an IN_REVIEW ticket with a recommended date."""
    async with async_session() as s:
        email = IncomingEmail(
            outlook_id=f"smoke-{uuid.uuid4()}",
            subject="Smoke",
            body="Smoke body",
            sender_email="smoke@fortebank.com",
            received_at=get_astana_time(),
            status=EmailStatusEnum.RED,
        )
        s.add(email)
        await s.flush()
        ticket = ReviewTicket(
            email_id=email.id,
            status=TicketStatusEnum.IN_REVIEW,
            title=title,
            body="Smoke announcement body",
            source="ServiceDesk",
            publish_confirmed=False,
            recommended_publish_at=get_astana_time() + timedelta(days=1),
        )
        s.add(ticket)
        await s.flush()
        await s.commit()
        _seeded_email_ids.append(email.id)
        return ticket.id, email.id


async def db_email_status(email_id: uuid.UUID) -> str | None:
    async with async_session() as s:
        e = (await s.execute(
            select(IncomingEmail).where(IncomingEmail.id == email_id)
        )).scalar_one_or_none()
        return e.status.value if e and e.status else None


async def db_pub_statuses(announcement_id: str) -> list[str]:
    async with async_session() as s:
        rows = (await s.execute(
            select(AnnouncementPublication.status).where(
                AnnouncementPublication.announcement_id == uuid.UUID(announcement_id)
            )
        )).scalars().all()
        return [r.value if hasattr(r, "value") else str(r) for r in rows]


# ----------------------------- HTTP tracks --------------------------------- #

async def track_health(c: httpx.AsyncClient) -> None:
    r = await c.get("/health")
    check("health 200", r.status_code == 200, f"status={r.status_code}")


async def track_publications(c: httpx.AsyncClient) -> None:
    # create an announcement
    r = await c.post("/announcements", data={"title": "Smoke", "category": "NEW", "text": "hello"}, headers=HEADERS)
    ok = r.status_code == 201
    ann = r.json().get("id") if ok else None
    if ann:
        _created_announcements.append(ann)
    check("create announcement 201", ok, f"status={r.status_code} id={ann}")
    if not ann:
        return

    r = await c.get(f"/announcements/{ann}/publications")
    check("history empty initially", r.status_code == 200 and r.json() == [], f"body={r.text[:120]}")

    future = (get_astana_time() + timedelta(days=1)).isoformat()
    r = await c.post(f"/announcements/{ann}/republish", json={"publish_at": future})
    body = r.json() if r.status_code == 200 else {}
    pub = body.get("publication_id")
    check("republish future -> scheduled", r.status_code == 200 and body.get("status") == "scheduled", f"body={r.text[:160]}")

    r = await c.get(f"/announcements/{ann}/publications")
    rows = r.json() if r.status_code == 200 else []
    one_repeat = len(rows) == 1 and rows[0]["kind"] == "REPEAT" and rows[0]["status"] == "SCHEDULED"
    check("history shows 1 REPEAT/SCHEDULED", one_repeat, f"rows={rows}")

    if pub:
        new_when = (get_astana_time() + timedelta(days=3)).isoformat()
        r = await c.patch(f"/publications/{pub}", json={"publish_at": new_when})
        check("reschedule scheduled row", r.status_code == 200, f"status={r.status_code}")

        r = await c.post(f"/publications/{pub}/cancel")
        check("cancel scheduled row", r.status_code == 200, f"status={r.status_code}")

        r = await c.post(f"/publications/{pub}/cancel")
        check("cancel again -> 400", r.status_code == 400, f"status={r.status_code}")

    past = (get_astana_time() - timedelta(minutes=5)).isoformat()
    r = await c.post(f"/announcements/{ann}/republish", json={"publish_at": past})
    body = r.json() if r.status_code == 200 else {}
    check("republish past -> republished now", r.status_code == 200 and body.get("status") == "republished", f"body={r.text[:160]}")

    statuses = await db_pub_statuses(ann)
    check("a REPEAT row is PUBLISHED", statuses.count("PUBLISHED") >= 1, f"db statuses={statuses}")


async def track_ticket_publish_flow(c: httpx.AsyncClient) -> None:
    ticket_id, email_id = await seed_ticket("Smoke publish flow")

    r = await c.get(f"/auto-announce/tickets/{ticket_id}")
    ds = r.json().get("display_status") if r.status_code == 200 else None
    check("ticket display_status NO_DATE", r.status_code == 200 and ds == "NO_DATE", f"display_status={ds}")

    r = await c.post(f"/auto-announce/tickets/{ticket_id}/confirm-date", json={}, headers=HEADERS)
    check("confirm-date 200 (fallback to recommended)", r.status_code == 200, f"status={r.status_code} body={r.text[:120]}")

    r = await c.post(f"/auto-announce/tickets/{ticket_id}/approve", headers=HEADERS)
    body = r.json() if r.status_code == 200 else {}
    ann = body.get("announcement_id")
    if ann:
        _created_announcements.append(ann)
    check("approve -> agreed", r.status_code == 200 and body.get("status") == "agreed", f"body={r.text[:160]}")

    r = await c.get(f"/auto-announce/tickets/{ticket_id}")
    ds = r.json().get("display_status") if r.status_code == 200 else None
    check("ticket display_status AGREED", ds == "AGREED", f"display_status={ds}")

    if ann:
        r = await c.get(f"/announcements/{ann}", headers=HEADERS)
        hidden = r.json().get("is_hidden") if r.status_code == 200 else None
        check("scheduled announcement is hidden", hidden is True, f"is_hidden={hidden}")

    # double-approve must be rejected (no second announcement)
    r = await c.post(f"/auto-announce/tickets/{ticket_id}/approve", headers=HEADERS)
    check("double approve -> 400", r.status_code == 400, f"status={r.status_code}")

    # publish now (reuses the existing scheduled publication)
    r = await c.post(f"/auto-announce/tickets/{ticket_id}/publish", headers=HEADERS)
    check("publish now -> 200", r.status_code == 200, f"status={r.status_code} body={r.text[:160]}")

    if ann:
        r = await c.get(f"/announcements/{ann}", headers=HEADERS)
        j = r.json() if r.status_code == 200 else {}
        check("published announcement visible", j.get("is_hidden") is False and j.get("published_at"), f"is_hidden={j.get('is_hidden')} published_at={j.get('published_at')}")
        statuses = await db_pub_statuses(ann)
        check("exactly one PRIMARY publication, PUBLISHED", statuses == ["PUBLISHED"], f"db statuses={statuses}")

    r = await c.get(f"/auto-announce/tickets/{ticket_id}")
    ds = r.json().get("display_status") if r.status_code == 200 else None
    check("ticket display_status PUBLISHED", ds == "PUBLISHED", f"display_status={ds}")

    email_status = await db_email_status(email_id)
    check("source email marked DONE", email_status == "DONE", f"email status={email_status}")


async def track_ticket_cancel_flow(c: httpx.AsyncClient) -> None:
    ticket_id, _ = await seed_ticket("Smoke cancel flow")
    await c.post(f"/auto-announce/tickets/{ticket_id}/confirm-date", json={}, headers=HEADERS)
    r = await c.post(f"/auto-announce/tickets/{ticket_id}/approve", headers=HEADERS)
    ann = r.json().get("announcement_id") if r.status_code == 200 else None
    if ann:
        _created_announcements.append(ann)

    r = await c.post(f"/auto-announce/tickets/{ticket_id}/cancel-publication", headers=HEADERS)
    check("cancel-publication 200", r.status_code == 200, f"status={r.status_code}")

    r = await c.get(f"/auto-announce/tickets/{ticket_id}")
    j = r.json() if r.status_code == 200 else {}
    check("ticket back to IN_REVIEW + OVERDUE", j.get("status") == "IN_REVIEW" and j.get("display_status") == "OVERDUE", f"status={j.get('status')} display_status={j.get('display_status')}")

    if ann:
        statuses = await db_pub_statuses(ann)
        check("publication is CANCELED", statuses == ["CANCELED"], f"db statuses={statuses}")
        r = await c.get(f"/announcements/{ann}", headers=HEADERS)
        check("announcement stays hidden after cancel", r.json().get("is_hidden") is True, f"is_hidden={r.json().get('is_hidden')}")


async def track_worker(c: httpx.AsyncClient) -> None:
    """Optional: schedule a publication a few seconds out and wait for the worker."""
    r = await c.post("/announcements", data={"title": "Smoke worker", "category": "NEW", "text": "x"}, headers=HEADERS)
    if r.status_code != 201:
        check("worker: create announcement", False, f"status={r.status_code}")
        return
    ann = r.json()["id"]
    _created_announcements.append(ann)
    when = (get_astana_time() + timedelta(seconds=10)).isoformat()
    r = await c.post(f"/announcements/{ann}/republish", json={"publish_at": when})
    pub = r.json().get("publication_id") if r.status_code == 200 else None
    check("worker: scheduled +10s", r.status_code == 200 and r.json().get("status") == "scheduled", f"body={r.text[:140]}")
    if not pub:
        return
    print("    waiting for scheduler (poll interval is 60s; up to ~130s)...")
    deadline = 130
    waited = 0
    published = False
    while waited < deadline:
        await asyncio.sleep(10)
        waited += 10
        statuses = await db_pub_statuses(ann)
        if "PUBLISHED" in statuses:
            published = True
            break
        print(f"    ...{waited}s, statuses={statuses}")
    check("worker auto-published the scheduled row", published, f"after {waited}s")


# ----------------------------- cleanup ------------------------------------- #

async def cleanup(c: httpx.AsyncClient) -> None:
    for ann in _created_announcements:
        try:
            await c.delete(f"/announcements/{ann}", headers=HEADERS)  # cascades publications
        except Exception:
            pass
    # deleting an email cascades its review ticket (FK ondelete=CASCADE)
    async with async_session() as s:
        for eid in _seeded_email_ids:
            try:
                e = (await s.execute(select(IncomingEmail).where(IncomingEmail.id == eid))).scalar_one_or_none()
                if e:
                    await s.delete(e)
            except Exception:
                pass
        await s.commit()


async def main() -> int:
    print(f"Smoke test against {BASE_URL}\n")
    # trust_env=False => ignore HTTP(S)_PROXY/NO_PROXY (corporate proxy on loopback)
    async with httpx.AsyncClient(base_url=BASE_URL, trust_env=False, timeout=30.0) as c:
        try:
            await track_health(c)
            print("\n--- Track A: publications API ---")
            await track_publications(c)
            print("\n--- Track B: ticket publish flow ---")
            await track_ticket_publish_flow(c)
            print("\n--- Track C: ticket cancel flow ---")
            await track_ticket_cancel_flow(c)
            if TEST_WORKER:
                print("\n--- Track D: scheduler worker ---")
                await track_worker(c)
            else:
                print("\n(Skipping worker track. Set SMOKE_WORKER=1 to test it.)")
        finally:
            print("\n--- cleanup ---")
            await cleanup(c)

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\n==== {passed} passed, {failed} failed ====")
    if failed:
        print("Failed checks:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
