from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Notification, User
from ..security import current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    unread_count = await db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
        )
    )
    items = (await db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(50)
    )).all()
    return {
        "unread_count": unread_count or 0,
        "items": [{
            "id": item.id,
            "type": item.notification_type,
            "title": item.title,
            "body": item.body,
            "link": item.link,
            "created_at": item.created_at.isoformat(),
            "read_at": item.read_at.isoformat() if item.read_at else None,
        } for item in items],
    }


@router.patch("/{notification_id}/read")
async def mark_read(
    notification_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=datetime.now(timezone.utc))
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Notification not found.")
    await db.commit()
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"ok": True}
