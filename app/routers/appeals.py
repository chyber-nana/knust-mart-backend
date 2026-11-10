import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..mailer import send_email
from ..models import Appeal, User
from ..security import admin_user, appeal_user

router = APIRouter(tags=["appeals"])


class AppealIn(BaseModel):
    message: str = Field(min_length=10, max_length=3000)


class ReviewAppealIn(BaseModel):
    status: Literal["approved", "rejected"]
    rejection_reason: str | None = Field(default=None, min_length=3, max_length=1000)


def appeal_view(appeal: Appeal) -> dict:
    return {
        "id": str(appeal.id),
        "message": appeal.message,
        "status": appeal.status,
        "rejection_reason": appeal.rejection_reason,
        "created_at": appeal.created_at.isoformat(),
        "reviewed_at": appeal.reviewed_at.isoformat() if appeal.reviewed_at else None,
    }


@router.post("/appeals", status_code=201)
async def create_appeal(
    data: AppealIn,
    user: User = Depends(appeal_user),
    db: AsyncSession = Depends(get_db),
):
    appeal = Appeal(user_id=user.id, message=data.message.strip())
    db.add(appeal)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "You already have an appeal awaiting review.")
    await db.refresh(appeal)
    return appeal_view(appeal)


@router.get("/appeals")
async def my_appeals(
    user: User = Depends(appeal_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(Appeal).where(Appeal.user_id == user.id).order_by(Appeal.created_at.desc())
    )).scalars().all()
    return [appeal_view(appeal) for appeal in rows]


@router.get("/admin/appeals")
async def list_appeals(
    _: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(Appeal, User)
        .join(User, User.id == Appeal.user_id)
        .order_by(Appeal.created_at.asc())
    )).all()
    return [
        {
            **appeal_view(appeal),
            "user_id": str(user.id),
            "full_name": user.full_name,
            "email": user.email,
        }
        for appeal, user in rows
    ]


@router.post("/admin/appeals/{appeal_id}/review")
async def review_appeal(
    appeal_id: uuid.UUID,
    data: ReviewAppealIn,
    admin: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    appeal = (await db.execute(
        select(Appeal).where(Appeal.id == appeal_id).with_for_update()
    )).scalar_one_or_none()
    if appeal is None:
        raise HTTPException(404, "Appeal not found.")
    if appeal.status != "pending":
        raise HTTPException(409, "This appeal has already been reviewed.")
    if data.status == "rejected" and (
        not data.rejection_reason or not data.rejection_reason.strip()
    ):
        raise HTTPException(422, "A reason is required when rejecting an appeal.")

    user = await db.get(User, appeal.user_id)
    if user is None:
        raise HTTPException(404, "Appeal owner not found.")
    appeal.status = data.status
    appeal.reviewed_by = admin.id
    appeal.reviewed_at = datetime.now(timezone.utc)
    appeal.rejection_reason = data.rejection_reason.strip() if data.rejection_reason else None
    if data.status == "approved":
        user.is_banned = False
        user.is_courier = False
    await db.commit()

    if data.status == "approved":
        send_email(
            user.email,
            "Your KNUST Mart appeal was approved",
            "Your account suspension has been lifted. Courier mode remains off; you can enable it again if eligible.",
        )
    else:
        send_email(
            user.email,
            "Your KNUST Mart appeal was not approved",
            f"Reason: {appeal.rejection_reason}",
        )
    return appeal_view(appeal)
