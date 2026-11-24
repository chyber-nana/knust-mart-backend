import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import DirectMessage, Friendship, Product, ProductImage, User
from ..security import current_user

router = APIRouter(prefix="/dm", tags=["direct messages"])
TTL = timedelta(hours=24)


class DMIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def live():
    return or_(DirectMessage.expires_at.is_(None), DirectMessage.expires_at > func.now())


def view(m: DirectMessage) -> dict:
    return {"id": m.id, "sender_id": str(m.sender_id), "receiver_id": str(m.receiver_id), "kind": m.kind,
            "body": m.body, "created_at": m.created_at.isoformat(),
            "expires_at": m.expires_at.isoformat() if m.expires_at else None}

async def hydrate(db: AsyncSession, rows: list[DirectMessage]) -> list[dict]:
    pids = {m.product_id for m in rows if m.product_id}
    prods, imgs = {}, {}
    if pids:
        prods = {p.id: p for p in (await db.execute(select(Product).where(Product.id.in_(pids)))).scalars()}
        for pid, url in (await db.execute(select(ProductImage.product_id, ProductImage.image_url)
                                          .where(ProductImage.product_id.in_(pids))
                                          .order_by(ProductImage.id))).all():
            imgs.setdefault(pid, url)
    out = []
    for m in rows:
        v = view(m)
        p = prods.get(m.product_id) if m.product_id else None
        v["hint_status"] = m.hint_status
        v["product"] = {"id": str(p.id), "title": p.title, "price": float(p.price), "image": imgs.get(p.id),
                        "status": p.status, "stock": p.stock} if p else None
        out.append(v)
    return out

async def friend_ids(db: AsyncSession, uid: uuid.UUID) -> set[uuid.UUID]:
    rows = (await db.execute(select(Friendship.requester_id, Friendship.addressee_id).where(
        Friendship.status == "accepted",
        or_(Friendship.requester_id == uid, Friendship.addressee_id == uid)))).all()
    return {b if a == uid else a for a, b in rows}


async def require_friend(db: AsyncSession, user: User, friend_id: uuid.UUID) -> User:
    if friend_id not in await friend_ids(db, user.id):
        raise HTTPException(403, "You can only message your friends.")
    friend = await db.get(User, friend_id)
    if friend is None:
        raise HTTPException(404, "User not found.")
    return friend


@router.get("/threads")
async def threads(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    friends = await friend_ids(db, user.id)
    if not friends:
        return []
    other = case((DirectMessage.sender_id == user.id, DirectMessage.receiver_id), else_=DirectMessage.sender_id)
    ranked = select(
        DirectMessage.id.label("mid"), other.label("cid"),
        func.row_number().over(partition_by=other,
                               order_by=(DirectMessage.created_at.desc(), DirectMessage.id.desc())).label("pos"),
    ).where(or_(DirectMessage.sender_id == user.id, DirectMessage.receiver_id == user.id), live()).subquery()
    rows = (await db.execute(select(DirectMessage).join(ranked, ranked.c.mid == DirectMessage.id)
                             .where(ranked.c.pos == 1))).scalars().all()
    unread = {sid: n for sid, n in (await db.execute(
        select(DirectMessage.sender_id, func.count()).where(
            DirectMessage.receiver_id == user.id, DirectMessage.read_at.is_(None), live())
        .group_by(DirectMessage.sender_id))).all()}
    out = []
    for m in rows:
        cid = m.receiver_id if m.sender_id == user.id else m.sender_id
        if cid in friends:
            out.append({"friend_id": str(cid), "last_message": m.body if m.kind == "text" else "🎁 Hint",
                        "created_at": m.created_at.isoformat(), "unread_count": unread.get(cid, 0)})
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


@router.get("/with/{friend_id}")
async def get_thread(friend_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    friend = await require_friend(db, user, friend_id)
    await db.execute(update(DirectMessage).where(
        DirectMessage.sender_id == friend_id, DirectMessage.receiver_id == user.id,
        DirectMessage.read_at.is_(None)).values(read_at=datetime.now(timezone.utc)))
    await db.commit()
    rows = (await db.execute(select(DirectMessage).where(
        or_((DirectMessage.sender_id == user.id) & (DirectMessage.receiver_id == friend_id),
            (DirectMessage.sender_id == friend_id) & (DirectMessage.receiver_id == user.id)),
        live()).order_by(DirectMessage.created_at, DirectMessage.id))).scalars().all()
    return {"friend": {"id": str(friend.id), "username": friend.username, "name": friend.full_name},
            "messages": await hydrate(db, rows)}


@router.post("/with/{friend_id}", status_code=201)
async def send(friend_id: uuid.UUID, data: DMIn, user: User = Depends(current_user),
               db: AsyncSession = Depends(get_db)):
    await require_friend(db, user, friend_id)
    text = data.message.strip()
    if not text:
        raise HTTPException(422, "Message cannot be blank.")
    now = datetime.now(timezone.utc)
    m = DirectMessage(sender_id=user.id, receiver_id=friend_id, kind="text", body=text,
                      created_at=now, expires_at=now + TTL)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return view(m)


@router.post("/with/{friend_id}/read")
async def mark_read(friend_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(update(DirectMessage).where(
        DirectMessage.sender_id == friend_id, DirectMessage.receiver_id == user.id,
        DirectMessage.read_at.is_(None)).values(read_at=datetime.now(timezone.utc)))
    await db.commit()
    return {"ok": True}

class HintIn(BaseModel):
    friend_id: uuid.UUID
    product_id: uuid.UUID
    note: str | None = Field(default=None, max_length=200)


@router.post("/hints", status_code=201)
async def send_hint(data: HintIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await require_friend(db, user, data.friend_id)
    product = await db.get(Product, data.product_id)
    if product is None or product.status != "active":
        raise HTTPException(409, "This listing isn't available.")
    if product.seller_id in (user.id, data.friend_id):
        raise HTTPException(409, "You can't send a Hint for your own or their own listing.")
    pending = (await db.execute(select(DirectMessage.product_id).where(
        DirectMessage.sender_id == user.id, DirectMessage.receiver_id == data.friend_id,
        DirectMessage.kind == "hint", DirectMessage.hint_status == "pending"))).scalars().all()
    if product.id in pending:
        raise HTTPException(409, "You already sent this item as a Hint.")
    if len(pending) >= 10:
        raise HTTPException(429, "Too many pending Hints for this friend.")
    m = DirectMessage(sender_id=user.id, receiver_id=data.friend_id, kind="hint", product_id=product.id,
                      hint_status="pending", body=(data.note or "").strip() or None,
                      created_at=datetime.now(timezone.utc), expires_at=None)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return (await hydrate(db, [m]))[0]


@router.post("/hints/{hint_id}/decline")
async def decline_hint(hint_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    m = await db.get(DirectMessage, hint_id)
    if m is None or m.kind != "hint" or m.receiver_id != user.id or m.hint_status != "pending":
        raise HTTPException(404, "Hint not found.")
    m.hint_status = "declined"
    await db.commit()
    return {"ok": True}