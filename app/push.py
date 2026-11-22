"""Web Push delivery. Silent no-op unless VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are set.

Env: VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY (from generate_vapid_keys.py), VAPID_SUBJECT (e.g. mailto:you@example.com)
"""
import asyncio
import json
import logging
import os
import uuid

from sqlalchemy import delete, select

log = logging.getLogger("knustmart.push")
_tasks: set[asyncio.Task] = set()


def push_enabled() -> bool:
    return bool(os.environ.get("VAPID_PUBLIC_KEY") and os.environ.get("VAPID_PRIVATE_KEY"))


def _send_one(sub: dict, payload: str) -> int | None:
    """Returns an HTTP status when the push service says the subscription is gone, else None."""
    from pywebpush import WebPushException, webpush
    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=payload,
            vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:admin@knustmart.site")},
            ttl=86400,  # keep trying for a day if the phone is offline
            headers={"Urgency": "high"},  # ask the push service to wake the phone now instead of batching while it is idle
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("push rejected (status %s): %s", status, str(exc)[:200])
        return status if status in (404, 410) else None
    except Exception as exc:  # never let a push problem affect the request that triggered it
        log.warning("push failed: %s", str(exc)[:200])
        return None
    return None


async def notify_user(user_id: uuid.UUID, title: str, body: str, link: str | None) -> None:
    if not push_enabled():
        return
    from .db import Session
    from .models import PushSubscription
    async with Session() as db:
        rows = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))).scalars().all()
        subs = [{"id": r.id, "endpoint": r.endpoint, "p256dh": r.p256dh, "auth": r.auth} for r in rows]
        if not subs:
            return
        payload = json.dumps({"title": title, "body": body, "link": link or "/"})
        dead: list[int] = []
        for sub in subs:
            if await asyncio.to_thread(_send_one, sub, payload) is not None:
                dead.append(sub["id"])
        if dead:
            await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead)))
            await db.commit()


def schedule(loop: asyncio.AbstractEventLoop, user_id: uuid.UUID, title: str, body: str, link: str | None) -> None:
    def _start() -> None:
        task = loop.create_task(notify_user(user_id, title, body, link))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    loop.call_soon_threadsafe(_start)
