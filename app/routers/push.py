import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import PushSubscription, User
from ..push import push_enabled
from ..security import current_user

router = APIRouter(prefix="/push", tags=["push"])


class Keys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=300)
    auth: str = Field(min_length=10, max_length=100)


class SubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000, pattern=r"^https://")
    keys: Keys


class UnsubscribeIn(BaseModel):
    endpoint: str


@router.get("/public-key")
async def public_key():
    if not push_enabled():
        raise HTTPException(503, "Phone notifications are not set up on this server yet.")
    return {"key": os.environ["VAPID_PUBLIC_KEY"]}


@router.post("/subscribe", status_code=201)
async def subscribe(data: SubscribeIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == data.endpoint))).scalar_one_or_none()
    if existing:
        existing.user_id, existing.p256dh, existing.auth = user.id, data.keys.p256dh, data.keys.auth
    else:
        db.add(PushSubscription(user_id=user.id, endpoint=data.endpoint, p256dh=data.keys.p256dh, auth=data.keys.auth))
    await db.commit()
    return {"subscribed": True}


@router.post("/unsubscribe")
async def unsubscribe(data: UnsubscribeIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(PushSubscription).where(PushSubscription.endpoint == data.endpoint, PushSubscription.user_id == user.id))
    await db.commit()
    return {"subscribed": False}
