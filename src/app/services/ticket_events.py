"""
In-process SSE event bus for review tickets.
Subscribers connect via /tickets/stream, events are pushed on any ticket action.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID

from logger import get_logger

logger = get_logger(__name__)


class TicketEventBus:
    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        logger.info("SSE subscriber connected, total={n}", n=len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.remove(q)
        logger.info("SSE subscriber disconnected, total={n}", n=len(self._subscribers))

    async def publish(
        self,
        event_type: str,
        ticket_id: str,
        actor_name: str | None = None,
        assignee_id: str | None = None,
        assignee_name: str | None = None,
        title: str | None = None,
        comment: str | None = None,
        status: str | None = None,
    ):
        payload = {
            "event_type": event_type,
            "ticket_id": ticket_id,
            "actor_name": actor_name,
            "assignee_id": assignee_id,
            "assignee_name": assignee_name,
            "title": title,
            "comment": comment,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        data = json.dumps(payload, ensure_ascii=False)
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    async def stream(self, q: asyncio.Queue) -> AsyncGenerator[str, None]:
        try:
            while True:
                data = await q.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass


# Singleton
ticket_events = TicketEventBus()
