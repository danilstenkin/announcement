"""
Server-Sent Events (SSE) router.
Handles real-time notifications via SSE with Redis Pub/Sub backend.
"""
 
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, status, Header
from fastapi.responses import StreamingResponse
import redis
 
from redis_client import get_redis, REDIS_CHANNELS
from logger import get_logger
from uuid import UUID
from typing import Optional, List

logger = get_logger(__name__)
 
router = APIRouter(prefix="/sse", tags=["sse"])
 
 
@router.get(
    "/announcements",
    summary="SSE: Announcements stream",
    description="Server-Sent Events stream for real-time announcements notifications. Supports auto-reconnect."
)
async def announcements_stream(
    x_user_id: UUID = Header(None, alias="X-User-Id"),
    x_username: Optional[str] = Header(None, alias="X-User-Name"),
    x_email: Optional[str] = Header(None, alias="X-User-Email"),
    redis_client: redis.Redis = Depends(get_redis),
):
    """
    Server-Sent Events stream for real-time announcements.
   
    Emits events:
    - **NEW_ANNOUNCEMENT**: New announcement published
      - Text: "Новый анонс! «{title}»"
      - Color hint: green=NEW, red=TECH_QUESTION
   
    - **UPDATED_ANNOUNCEMENT**: Announcement was updated
      - Text: "Внимание! Изменения в анонсе «{title}»"
   
    - **REVOKED_ANNOUNCEMENT**: Announcement was revoked
      - Text: "Внимание! Анонс «{title}» больше не действует"
   
    The client can reconnect automatically if the connection is lost.
    Each reconnection will miss events from the offline period (events in Redis expire).
   
    Usage in frontend:
    ```javascript
    const eventSource = new EventSource('/sse/announcements', {
        headers: { 'X-User-ID': userId }
    });
   
    eventSource.addEventListener('NEW_ANNOUNCEMENT', (event) => {
        const data = JSON.parse(event.data);
        console.log(data.message);  // "Новый анонс! «title»"
    });
   
    eventSource.addEventListener('UPDATED_ANNOUNCEMENT', (event) => {
        const data = JSON.parse(event.data);
        console.log(data.message);  // "Внимание! Изменения в анонсе «title»"
    });
   
    eventSource.addEventListener('REVOKED_ANNOUNCEMENT', (event) => {
        const data = JSON.parse(event.data);
        console.log(data.message);  // "Внимание! Анонс «title» больше не действует"
    });
   
    eventSource.onerror = () => {
        // Reconnect will happen automatically by the browser
        console.log('Connection closed, will auto-reconnect...');
    };
    ```
    """
   
    # Create a new Pub/Sub connection for this SSE stream
    pubsub = redis_client.pubsub()
    channel = REDIS_CHANNELS["announcements"]
    pubsub.subscribe(channel)
   
    async def event_generator():
        """Generate SSE events from Redis Pub/Sub."""
        try:
            # Send initial connection message
            yield "data: {\"status\": \"connected\"}\n\n"
           
            # Listen for messages
            while True:
                # Get message with timeout to allow periodic checks
                message = pubsub.get_message()
               
                if message and message["type"] == "message":
                    try:
                        # Parse the event data
                        event_data = json.loads(message["data"])
                        event_type = event_data.get("event_type", "UNKNOWN")
                       
                        # Format as SSE event
                        # SSE format: "event: TYPE\ndata: {json}\n\n"
                        event_json = json.dumps(event_data)
                        yield f"event: {event_type}\ndata: {event_json}\n\n"
                   
                    except json.JSONDecodeError:
                        # Log invalid JSON but don't crash
                        yield f"data: {{\"error\": \"Invalid event data\"}}\n\n"
                else:
                    # No message, wait a bit before checking again
                    # This allows for graceful shutdown and reduces CPU usage
                    await asyncio.sleep(0.1)
       
        except asyncio.CancelledError:
            # Client disconnected
            pass
        finally:
            # Clean up Redis subscription
            pubsub.unsubscribe(channel)
            pubsub.close()
   
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )