import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi.responses import StreamingResponse
from sqlalchemy import event
from sqlalchemy.orm import Session

from .models import ChatMessage, Notification
from . import push


class Hub:
    def __init__(self) -> None:
        self._channels: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, channel: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._channels.setdefault(channel, set()).add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        subscribers = self._channels.get(channel)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._channels.pop(channel, None)

    def publish(self, channel: str, payload: dict) -> None:
        for queue in list(self._channels.get(channel, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass


hub = Hub()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def notification_payload(item: Notification) -> dict:
    return {
        "type": "notification",
        "item": {
            "id": item.id,
            "type": item.notification_type,
            "title": item.title,
            "body": item.body,
            "link": item.link,
            "created_at": _iso(item.created_at) or datetime.now(timezone.utc).isoformat(),
            "read_at": _iso(item.read_at),
        },
    }


def message_payload(message: ChatMessage) -> dict:
    return {
        "type": "message",
        "message": {
            "id": message.id,
            "product_id": str(message.product_id),
            "sender_id": str(message.sender_id),
            "receiver_id": str(message.receiver_id),
            "message": message.message,
            "sent_at": _iso(message.sent_at) or datetime.now(timezone.utc).isoformat(),
        },
    }


@event.listens_for(Session, "after_flush")
def _collect_realtime(session: Session, _ctx) -> None:
    events = session.info.setdefault("realtime_events", [])
    pushes = session.info.setdefault("push_events", [])
    for obj in session.new:
        if isinstance(obj, Notification) and obj.id is not None:
            events.append((f"user:{obj.user_id}", notification_payload(obj)))
            pushes.append((obj.user_id, obj.title, obj.body, obj.link))
        elif isinstance(obj, ChatMessage) and obj.id is not None:
            pushes.append((obj.receiver_id, "New message", "You have a new message on KNUST Mart.", "/inbox"))
            payload = message_payload(obj)
            events.append((f"user:{obj.receiver_id}", payload))
            events.append((f"user:{obj.sender_id}", payload))


@event.listens_for(Session, "after_commit")
def _publish_realtime(session: Session) -> None:
    events = session.info.pop("realtime_events", None)
    pushes = session.info.pop("push_events", None)
    if not events and not pushes:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for channel, payload in events or ():
        loop.call_soon_threadsafe(hub.publish, channel, payload)
    for user_id, title, body, link in pushes or ():
        push.schedule(loop, user_id, title, body, link)


@event.listens_for(Session, "after_rollback")
def _clear_realtime(session: Session) -> None:
    session.info.pop("realtime_events", None)
    session.info.pop("push_events", None)


async def user_event_stream(request, user_id: uuid.UUID):
    channel = f"user:{user_id}"
    queue = hub.subscribe(channel)

    async def generate():
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(payload, default=str)}\n\n"
        finally:
            hub.unsubscribe(channel, queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
