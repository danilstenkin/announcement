from __future__ import annotations

from datetime import datetime

import pytz

ALMATY = pytz.timezone("Asia/Almaty")

_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S")


def parse_recommended_date(value: str | None, received_at: datetime) -> datetime:
    """Parse an ISO-ish date string to 00:00 Asia/Almaty.
    Falls back to the received day at 00:00 on None/unparseable input."""
    if value:
        text = value.strip()
        for fmt in _FORMATS:
            try:
                naive = datetime.strptime(text, fmt)
            except ValueError:
                continue
            return ALMATY.localize(datetime(naive.year, naive.month, naive.day, 0, 0))
    # fallback: received day at midnight Almaty
    if received_at.tzinfo is None:
        local = ALMATY.localize(received_at)
    else:
        local = received_at.astimezone(ALMATY)
    return ALMATY.localize(datetime(local.year, local.month, local.day, 0, 0))
