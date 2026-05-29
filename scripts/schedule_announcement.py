"""
Schedule an announcement to auto-publish in N minutes and watch the worker do it.

Creates a HIDDEN announcement + a PRIMARY SCHEDULED publication at now + N minutes
(via the ORM, so no HTTP/proxy involved), then polls the DB until the background
scheduler publishes it (flips is_hidden=False, sets published_at, marks the
publication PUBLISHED).

Requires the app to be running (its lifespan starts the publish scheduler).

Run from the REPO ROOT with the project's venv active:

    python scripts/schedule_announcement.py            # 2 minutes (default)
    python scripts/schedule_announcement.py 5          # 5 minutes

Tip: for a snappier demo set PUBLISH_POLL_INTERVAL=10 in .env and restart the app
(default poll is 60s, so publication lands within ~60s after the target time).
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid
from datetime import timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "app"))


def _load_env() -> None:
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
    print("WARNING: no .env found; set SMOKE_ENV_FILE=path\\to\\.env")


_load_env()

from sqlalchemy import select  # noqa: E402

from dependencies.database import async_session  # noqa: E402
from models.announcement import Announcement, AnnouncementCategoryEnum, get_astana_time  # noqa: E402
from models.publication import (  # noqa: E402
    AnnouncementPublication,
    PublicationKindEnum,
    PublicationStatusEnum,
)

SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def seed(minutes: int) -> tuple[uuid.UUID, uuid.UUID, object]:
    target = get_astana_time() + timedelta(minutes=minutes)
    async with async_session() as s:
        ann = Announcement(
            title=f"Авто-публикация в {target.strftime('%H:%M:%S')}",
            category=AnnouncementCategoryEnum.NEW,
            text="Тестовый анонс с отложенной публикацией.",
            is_hidden=True,
            is_ai=True,
            created_by=SYSTEM_USER_ID,
        )
        s.add(ann)
        await s.flush()
        pub = AnnouncementPublication(
            announcement_id=ann.id,
            kind=PublicationKindEnum.PRIMARY,
            publish_at=target,
            status=PublicationStatusEnum.SCHEDULED,
        )
        s.add(pub)
        await s.flush()
        await s.commit()
        return ann.id, pub.id, target


async def fetch(ann_id: uuid.UUID, pub_id: uuid.UUID):
    async with async_session() as s:
        ann = (await s.execute(select(Announcement).where(Announcement.id == ann_id))).scalar_one()
        pub = (await s.execute(select(AnnouncementPublication).where(AnnouncementPublication.id == pub_id))).scalar_one()
        return ann.is_hidden, ann.published_at, pub.status.value


async def main(minutes: int) -> int:
    ann_id, pub_id, target = await seed(minutes)
    print(f"\nScheduled announcement {ann_id}")
    print(f"  publication {pub_id}")
    print(f"  publish_at  {target.isoformat()}  (in ~{minutes} min)")
    print("\nWaiting for the scheduler to publish it (Ctrl+C to stop watching)...\n")

    # poll until published, with margin for the (default 60s) poll interval
    deadline = minutes * 60 + 150
    waited = 0
    while waited <= deadline:
        is_hidden, published_at, status = await fetch(ann_id, pub_id)
        ts = get_astana_time().strftime("%H:%M:%S")
        print(f"  [{ts}] is_hidden={is_hidden}  status={status}  published_at={published_at}")
        if status == "PUBLISHED" and is_hidden is False:
            print(f"\n✅ Published! Announcement {ann_id} is now visible (published_at={published_at}).")
            return 0
        await asyncio.sleep(15)
        waited += 15

    print("\n❌ Not published within the wait window. Is the app (with the publish scheduler) running?")
    print("   Check the app logs for 'Publish scheduler started' / 'Published N scheduled announcements'.")
    return 1


if __name__ == "__main__":
    mins = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    raise SystemExit(asyncio.run(main(mins)))
