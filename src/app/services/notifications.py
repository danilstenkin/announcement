"""
Notifications service.
Sends announcement events via HTTP webhook.
"""

import json
from datetime import datetime, timezone
from pydoc import text

import httpx

from models.announcement import Announcement
from schemas import NotificationEvent
from logger import get_logger

logger = get_logger(__name__)


class NotificationsService:
    """Service for sending notification events via HTTP."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def publish_new_announcement(self, announcement: Announcement) -> None:
        notification = NotificationEvent(
            event_type="NEW_ANNOUNCEMENT",
            title=announcement.title,
            message=f"Новый анонс! «{announcement.title}»",
            category=announcement.category,
            announcement_id=announcement.id,
            topic=announcement.topic,
            product=announcement.product,
            text=announcement.text,
            timestamp=datetime.now(timezone.utc),
        )
        await self._send_event(notification)
        logger.info(
            f"New announcement notification sent: "
            f"ID={announcement.id}, Title='{announcement.title}'"
        )

    async def publish_updated_announcement(self, announcement: Announcement) -> None:
        notification = NotificationEvent(
            event_type="UPDATED_ANNOUNCEMENT",
            title=announcement.title,
            message=f"Внимание! Изменения в анонсе «{announcement.title}»",
            category=announcement.category,
            announcement_id=announcement.id,
            topic=announcement.topic,
            product=announcement.product,
            text=announcement.text,
            timestamp=datetime.now(timezone.utc),
        )
        await self._send_event(notification)
        logger.info(
            f"Updated announcement notification sent: "
            f"ID={announcement.id}, Title='{announcement.title}'"
        )

    async def publish_revoked_announcement(self, announcement: Announcement) -> None:
        notification = NotificationEvent(
            event_type="REVOKED_ANNOUNCEMENT",
            title=announcement.title,
            message=f"Внимание! Анонс «{announcement.title}» больше не действует",
            category=announcement.category,
            announcement_id=announcement.id,
            topic=announcement.topic,
            product=announcement.product,
            text=announcement.text,
            timestamp=datetime.now(timezone.utc),
        )
        await self._send_event(notification)
        logger.info(
            f"Revoked announcement notification sent: "
            f"ID={announcement.id}, Title='{announcement.title}'"
        )

    async def publish_deleted_announcement(self, announcement: Announcement) -> None:
        notification = NotificationEvent(
            event_type="DELETED_ANNOUNCEMENT",
            title=announcement.title,
            message=f"Анонс «{announcement.title}» был удалён",
            category=announcement.category,
            announcement_id=announcement.id,
            topic=announcement.topic,
            product=announcement.product,
            text=announcement.text,
            timestamp=datetime.now(timezone.utc),
        )
        await self._send_event(notification)
        logger.info(
            f"Deleted announcement notification sent: "
            f"ID={announcement.id}, Title='{announcement.title}'"
        )

    async def _send_event(self, notification: NotificationEvent) -> None:
        payload = {
            "event_type": notification.event_type,
            "title": notification.title,
            "message": notification.message,
            "category": notification.category,
            "announcement_id": str(notification.announcement_id),
            "timestamp": notification.timestamp.isoformat(),
        }

        try:
            logger.debug(f"Sending {notification.event_type} to {self.webhook_url}")
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                response = await client.post(self.webhook_url, json=payload)
                response.raise_for_status()
            logger.log("EVENT", f"Event sent to {self.webhook_url}: {notification.event_type}")
        except Exception as e:
            logger.error(
                f"Failed to send event {notification.event_type}: "
                f"{type(e).__name__}: {e!r} | URL={self.webhook_url}"
            )
