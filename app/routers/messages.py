import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..db import get_db
from ..models import ChatMessage, Delivery, Product, User
from ..notifications import add_notification
from ..security import current_user

router = APIRouter(prefix="/messages", tags=["messages"])


class MessageIn(BaseModel):
    product_id: uuid.UUID
    receiver_id: uuid.UUID
    message: str = Field(min_length=1, max_length=2000)


def message_view(message: ChatMessage) -> dict:
    return {
        "id": message.id,
        "product_id": str(message.product_id),
        "sender_id": str(message.sender_id),
        "receiver_id": str(message.receiver_id),
        "message": message.message,
        "sent_at": message.sent_at.isoformat(),
    }


@router.get("/unread-count")
async def unread_count(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    count = (await db.execute(
        select(func.count(ChatMessage.id)).where(
            ChatMessage.receiver_id == user.id,
            ChatMessage.read_at.is_(None),
        )
    )).scalar_one()
    return {"count": count}


@router.get("/threads")
async def threads(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    counterpart_id = case(
        (ChatMessage.sender_id == user.id, ChatMessage.receiver_id),
        else_=ChatMessage.sender_id,
    )
    ranked = select(
        ChatMessage.id.label("message_id"),
        ChatMessage.product_id.label("product_id"),
        counterpart_id.label("counterpart_id"),
        func.row_number().over(
            partition_by=(ChatMessage.product_id, counterpart_id),
            order_by=(ChatMessage.sent_at.desc(), ChatMessage.id.desc()),
        ).label("position"),
    ).where(or_(ChatMessage.sender_id == user.id, ChatMessage.receiver_id == user.id)).subquery()
    unread_message = aliased(ChatMessage)
    unread = select(func.count(unread_message.id)).where(
        unread_message.product_id == ranked.c.product_id,
        unread_message.sender_id == ranked.c.counterpart_id,
        unread_message.receiver_id == user.id,
        unread_message.read_at.is_(None),
    ).scalar_subquery()
    rows = (await db.execute(
        select(ChatMessage, Product, User, unread.label("unread_count"))
        .join(ranked, ranked.c.message_id == ChatMessage.id)
        .join(Product, Product.id == ChatMessage.product_id)
        .join(User, User.id == ranked.c.counterpart_id)
        .where(ranked.c.position == 1)
        .order_by(ChatMessage.sent_at.desc(), ChatMessage.id.desc())
    )).all()
    return [
        {
            "product_id": str(product.id),
            "product_title": product.title,
            "counterpart_id": str(counterpart.id),
            "counterpart_name": counterpart.store_name or counterpart.full_name,
            "last_message": latest.message,
            "sent_at": latest.sent_at.isoformat(),
            "unread_count": unread_count,
        }
        for latest, product, counterpart, unread_count in rows
    ]


@router.get("/{product_id}/{counterpart_id}")
async def thread(
    product_id: uuid.UUID,
    counterpart_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if product is None or counterpart_id == user.id:
        raise HTTPException(404, "Conversation not found.")
    if user.id != product.seller_id and counterpart_id != product.seller_id:
        raise HTTPException(403, "Conversations for a listing are with its seller.")
    if user.id == product.seller_id:
        started = await db.scalar(
            select(ChatMessage.id).where(
                ChatMessage.product_id == product.id,
                ChatMessage.sender_id == counterpart_id,
                ChatMessage.receiver_id == user.id,
            ).limit(1)
        )
        has_order = await db.scalar(
            select(Delivery.id).where(
                Delivery.product_id == product.id,
                Delivery.buyer_id == counterpart_id,
            ).limit(1)
        )
        if started is None and has_order is None:
            raise HTTPException(404, "Conversation not found.")
    counterpart = await db.get(User, counterpart_id)
    if counterpart is None:
        raise HTTPException(404, "Conversation not found.")

    await db.execute(
        update(ChatMessage)
        .where(
            ChatMessage.product_id == product.id,
            ChatMessage.sender_id == counterpart_id,
            ChatMessage.receiver_id == user.id,
            ChatMessage.read_at.is_(None),
        )
        .values(read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    rows = (await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.product_id == product.id,
            or_(
                (ChatMessage.sender_id == user.id) & (ChatMessage.receiver_id == counterpart_id),
                (ChatMessage.sender_id == counterpart_id) & (ChatMessage.receiver_id == user.id),
            ),
        )
        .order_by(ChatMessage.sent_at, ChatMessage.id)
    )).scalars().all()
    return {
        "product": {"id": str(product.id), "title": product.title},
        "counterpart": {
            "id": str(counterpart.id),
            "name": counterpart.store_name or counterpart.full_name,
        },
        "messages": [message_view(message) for message in rows],
    }


@router.post("", status_code=201)
async def send_message(
    data: MessageIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    text = data.message.strip()
    if not text:
        raise HTTPException(422, "Message cannot be blank.")
    product = await db.get(Product, data.product_id)
    receiver = await db.get(User, data.receiver_id)
    if product is None or receiver is None or data.receiver_id == user.id:
        raise HTTPException(404, "Conversation not found.")
    if user.id != product.seller_id:
        if data.receiver_id != product.seller_id:
            raise HTTPException(403, "Messages about this listing must go to its seller.")
    else:
        started = await db.scalar(
            select(ChatMessage.id).where(
                ChatMessage.product_id == product.id,
                ChatMessage.sender_id == data.receiver_id,
                ChatMessage.receiver_id == user.id,
            ).limit(1)
        )
        has_order = await db.scalar(
            select(Delivery.id).where(
                Delivery.product_id == product.id,
                Delivery.buyer_id == data.receiver_id,
            ).limit(1)
        )
        if started is None and has_order is None:
            raise HTTPException(403, "You can reply only to an existing conversation.")

    message = ChatMessage(
        product_id=product.id,
        sender_id=user.id,
        receiver_id=receiver.id,
        message=text,
    )
    db.add(message)
    add_notification(
        db,
        receiver.id,
        "message_received",
        "New message",
        f"{user.store_name or user.full_name}: {text[:160]}",
        f"/chat/{product.id}/{user.id}",
    )
    await db.commit()
    await db.refresh(message)
    return message_view(message)
