import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Friendship, User
from ..notifications import add_notification
from ..security import current_user, require_student

router = APIRouter(prefix="/friends", tags=["friends"])
MAX_PENDING_OUT = 30


def person(u: User) -> dict:
    return {"id": str(u.id), "username": u.username, "name": u.full_name}


async def pair(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> Friendship | None:
    return await db.scalar(select(Friendship).where(or_(
        (Friendship.requester_id == a) & (Friendship.addressee_id == b),
        (Friendship.requester_id == b) & (Friendship.addressee_id == a))))


class RequestIn(BaseModel):
    username: str


@router.get("")
async def my_friends(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    other = case((Friendship.requester_id == user.id, Friendship.addressee_id), else_=Friendship.requester_id)
    rows = (await db.execute(
        select(Friendship, User).join(User, User.id == other)
        .where(or_(Friendship.requester_id == user.id, Friendship.addressee_id == user.id),
               Friendship.status != "blocked")
        .order_by(User.username))).all()
    out = {"friends": [], "incoming": [], "outgoing": []}
    for f, u in rows:
        item = {**person(u), "request_id": f.id}
        if f.status == "accepted":
            out["friends"].append(item)
        elif f.addressee_id == user.id:
            out["incoming"].append(item)
        else:
            out["outgoing"].append(item)
    return out


@router.get("/search")
async def search(q: str = Query(..., min_length=2, max_length=21),
                 user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    require_student(user)
    needle = re.sub(r"[^a-z0-9_.]", "", q.lower()).replace("_", "\\_")
    if len(needle) < 2:
        return []
    users = (await db.execute(
        select(User).where(func.lower(User.username).like(needle + "%", escape="\\"),
                           User.id != user.id, User.restriction.is_(None),
                           User.user_type == "student")
        .order_by(User.username).limit(8))).scalars().all()
    hits = []
    for u in users:
        f = await pair(db, user.id, u.id)
        if f and f.status == "blocked":
            continue
        rel = "none"
        if f:
            rel = "friends" if f.status == "accepted" else ("pending_out" if f.requester_id == user.id else "pending_in")
        hits.append({**person(u), "relation": rel})
    return hits


@router.post("/request", status_code=201)
async def send_request(data: RequestIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    require_student(user)
    if not user.username:
        raise HTTPException(409, "Set a username first.")
    name = data.username.strip().lstrip("@").lower()
    target = await db.scalar(select(User).where(
        func.lower(User.username) == name, User.restriction.is_(None), User.user_type == "student"))
    if target is None or target.id == user.id:
        raise HTTPException(404, "No user with that username.")
    f = await pair(db, user.id, target.id)
    if f:
        if f.status == "accepted":
            raise HTTPException(409, "You're already friends.")
        if f.status == "pending" and f.addressee_id == user.id:  # they already asked us: accept
            f.status, f.responded_at = "accepted", datetime.now(timezone.utc)
            add_notification(db, target.id, "friend_accepted", "Friend request accepted",
                             f"@{user.username} is now your friend.", "/inbox?tab=friends")
            await db.commit()
            return {"status": "accepted"}
        if f.status == "pending":
            raise HTTPException(409, "Request already sent.")
        raise HTTPException(403, "You can't send a request to this user.")
    sent = await db.scalar(select(func.count(Friendship.id)).where(
        Friendship.requester_id == user.id, Friendship.status == "pending"))
    if sent >= MAX_PENDING_OUT:
        raise HTTPException(429, "Too many pending requests. Wait for replies first.")
    db.add(Friendship(requester_id=user.id, addressee_id=target.id))
    add_notification(db, target.id, "friend_request", "New friend request",
                     f"@{user.username} wants to be your friend.", "/inbox?tab=friends")
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Request already exists.")
    return {"status": "pending"}


@router.post("/block/{user_id}")
async def block(user_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if user_id == user.id or await db.get(User, user_id) is None:
        raise HTTPException(404, "User not found.")
    f = await pair(db, user.id, user_id)
    if f:
        f.requester_id, f.addressee_id, f.status = user.id, user_id, "blocked"
    else:
        db.add(Friendship(requester_id=user.id, addressee_id=user_id, status="blocked"))
    await db.commit()
    return {"ok": True}


@router.post("/unblock/{user_id}")
async def unblock(user_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    f = await pair(db, user.id, user_id)
    if f and f.status == "blocked" and f.requester_id == user.id:
        await db.delete(f)
        await db.commit()
    return {"ok": True}


@router.post("/{request_id}/accept")
async def accept(request_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    f = await db.get(Friendship, request_id)
    if f is None or f.addressee_id != user.id or f.status != "pending":
        raise HTTPException(404, "Request not found.")
    f.status, f.responded_at = "accepted", datetime.now(timezone.utc)
    add_notification(db, f.requester_id, "friend_accepted", "Friend request accepted",
                     f"@{user.username or 'Someone'} is now your friend.", "/inbox?tab=friends")
    await db.commit()
    return {"ok": True}


@router.post("/{request_id}/decline")  # also used to cancel a request you sent
async def decline(request_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    f = await db.get(Friendship, request_id)
    if f is None or user.id not in (f.requester_id, f.addressee_id) or f.status != "pending":
        raise HTTPException(404, "Request not found.")
    await db.delete(f)
    await db.commit()
    return {"ok": True}


@router.delete("/{user_id}")
async def unfriend(user_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    f = await pair(db, user.id, user_id)
    if f and f.status == "accepted":
        await db.delete(f)
        await db.commit()
    return {"ok": True}
