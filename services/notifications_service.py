"""
Notifications service.
Handles broadcasting of SSE events through Redis Pub/Sub.
"""
 
import json
from datetime import datetime
from typing import Optional
import redis
 
from app.models import Announcement, AnnouncementCategoryEnum
from schemas import NotificationEvent
from redis_client import REDIS_CHANNELS
from logger import get_logger
 
logger = get_logger(__name__)
 
 
class NotificationsService:
    """Service for managing notification events."""
 
    def __init__(self, redis_client: redis.Redis):
        """Initialize with Redis client."""
        self.redis = redis_client
        self.channel = REDIS_CHANNELS["announcements"]
 
    async def publish_new_announcement(self, announcement: Announcement) -> None:
        """
        Publish a new announcement notification.
 
        Args:
            announcement: The announcement that was created/published
        """
        notification = NotificationEvent(
            event_type="NEW_ANNOUNCEMENT",
            title=announcement.title,
            message=f"Новый анонс! «{announcement.title}»",
            category=announcement.category,
            announcement_id=announcement.id,
            timestamp=datetime.utcnow(),
        )
 
        await self._publish_event(notification)
        logger.info(f"New announcement notification published: ID={announcement.id}, Title='{announcement.title}'")
 
    async def publish_updated_announcement(self, announcement: Announcement) -> None:
        """
        Publish an announcement update notification.
 
        Args:
            announcement: The announcement that was updated
        """
        notification = NotificationEvent(
            event_type="UPDATED_ANNOUNCEMENT",
            title=announcement.title,
            message=f"Внимание! Изменения в анонсе «{announcement.title}»",
            category=announcement.category,
            announcement_id=announcement.id,
            timestamp=datetime.utcnow(),
        )
 
        await self._publish_event(notification)
        logger.info(f"Updated announcement notification published: ID={announcement.id}, Title='{announcement.title}'")
 
    async def publish_revoked_announcement(self, announcement: Announcement) -> None:
        """
        Publish an announcement revocation notification.
 
        Args:
            announcement: The announcement that was revoked
        """
        notification = NotificationEvent(
            event_type="REVOKED_ANNOUNCEMENT",
            title=announcement.title,
            message=f"Внимание! Анонс «{announcement.title}» больше не действует",
            category=announcement.category,
            announcement_id=announcement.id,
            timestamp=datetime.utcnow(),
        )
 
        await self._publish_event(notification)
        logger.info(f"Revoked announcement notification published: ID={announcement.id}, Title='{announcement.title}'")
 
    async def _publish_event(self, notification: NotificationEvent) -> None:
        """
        Publish an event to Redis pub/sub.
 
        Args:
            notification: The notification event to publish
        """
        # Convert to JSON for transmission
        event_data = json.dumps(
            {
                "event_type": notification.event_type,
                "title": notification.title,
                "message": notification.message,
                "category": notification.category,
                "announcement_id": str(notification.announcement_id),
                "timestamp": notification.timestamp.isoformat(),
            }
        )
 
        # Publish to Redis channel
        self.redis.publish(self.channel, event_data)
        logger.debug(f"Event published to Redis channel '{self.channel}': {notification.event_type}")
 
    def get_channel(self) -> str:
        """Get the Redis channel name for announcements."""
        return self.channel