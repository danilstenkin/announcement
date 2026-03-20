"""
Redis client setup for Pub/Sub and caching.
Manages connections to Redis and provides pub/sub functionality.
"""
 
import redis
from typing import Optional
from app.config import settings
 
 
class RedisClient:
    """Redis client for pub/sub and caching operations."""
 
    _instance: Optional[redis.Redis] = None
 
    @classmethod
    def get_client(cls) -> redis.Redis:
        """Get or create Redis client (singleton pattern)."""
        if cls._instance is None:
            cls._instance = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
            )
        return cls._instance
 
    @classmethod
    async def close(cls):
        """Close Redis connection."""
        if cls._instance is not None:
            await cls._instance.aclose()
            cls._instance = None
 
 
# Get the Redis client instance
redis_client = RedisClient.get_client()
 
 
# Redis channel names for pub/sub
REDIS_CHANNELS = {
    "announcements": "announcements:events",
    "notifications": "announcements:notifications",
}
 
 
async def get_redis() -> redis.Redis:
    """Dependency to get Redis client."""
    return RedisClient.get_client()