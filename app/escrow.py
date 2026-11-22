"""Escrow payments: the buyer pays once through Paystack, KNUST Mart holds the money,
and the seller is paid by MoMo when the handover is confirmed. Couriers are paid weekly in one batch.

Env (all optional):
  PAYSTACK_RATE            default 0.0195   Paystack's cut of everything the buyer pays
  TRANSFER_FEE_GHS         default 1        cost of one MoMo payout
  AUTO_RELEASE_HOURS       default 72       pickup orders the buyer never confirms are released after this
"""
import asyncio
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .billing import CURRENCY, _paystack
from .models import CourierPayout, Delivery, Notification, Product, User
from .notifications import add_notification
from .stock import release_unit

PURPOSE_ORDER = "order_payment"
MOMO_CODES = {"mtn": "MTN", "vodafone": "VOD", "airteltigo": "ATL"}


def _num(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.environ.get(name, default))
    except Exception:
        return Decimal(default)


def pesewas(amount: Decimal) -> int:
    return int((Decimal(amount) * 100).to_integral_value())


def quote(item: Decimal, delivery_fee: Decimal, courier: bool) -> dict:
    """Everything the buyer pays. The service fee covers Paystack's % on the whole total plus one
    MoMo payout to the seller (and one to the courier on delivery orders), rounded up to the pesewa."""
    rate = _num("PAYSTACK_RATE", "0.0195")
    payout = pesewas(_num("TRANSFER_FEE_GHS", "1"))
    base = pesewas(item) + (pesewas(delivery_fee) if courier else 0)
    fixed = payout * (2 if courier else 1)
    total = math.ceil((base + fixed) / (1 - float(rate)))
    return {"item": item, "delivery": delivery_fee if courier else Decimal("0.00"),
            "service_fee": Decimal(total - base) / 100, "total_pesewas": total, "total": Decimal(total) / 100}


async def advance_paid_order(db: AsyncSession, d: Delivery) -> None:
    """Called once the buyer's payment is confirmed: the order moves on exactly as it used to after the seller's OK."""
    if d.status != "awaiting_payment":
        return
    product = await db.get(Product, d.product_id)
    title = product.title if product else "your order"
    now = datetime.now(timezone.utc)
    d.paid_at = now
    if d.fulfillment_type == "pickup":
        d.status = "ready_for_pickup"
        add_notification(db, d.buyer_id, "order_ready_for_pickup", "Payment received: ready for pickup",
                         f"{title} is ready for you at {d.dropoff_location}. Your money is held safely until you collect it.",
                         f"/orders/{d.id}")
    else:
        d.status = "pending"
        d.courier_available_at = now + timedelta(seconds=10)
        ids = await db.scalars(select(User.id).where(User.is_courier.is_(True), User.is_verified_student.is_(True),
                                                      User.is_banned.is_(False), User.id != d.buyer_id))
        for cid in ids:
            add_notification(db, cid, "delivery_available", "Delivery ready to accept",
                             f"{title} is paid for. You can accept it in 10 seconds.", "/courier")
        add_notification(db, d.buyer_id, "order_confirmed", "Payment received",
                         f"{title} is paid for and a courier can accept it shortly. Your money is held until you give the courier your code.",
                         f"/orders/{d.id}")
    if product:
        add_notification(db, product.seller_id, "order_paid", "Buyer has paid",
                         f"The buyer paid for {title}. You'll receive GH₵{d.item_amount:.2f} by MoMo once the handover is confirmed.",
                         f"/orders/{d.id}")


async def refund_order(db: AsyncSession, d: Delivery, reason: str) -> bool:
    """Refund the buyer in full through Paystack. Safe to call on unpaid or already-refunded orders."""
    if d.paid_at is None or d.refunded_at is not None or d.seller_paid_at is not None or not d.payment_reference:
        return False
    await _paystack("POST", "/refund", json={"transaction": d.payment_reference, "merchant_note": reason[:200]})
    d.refunded_at = datetime.now(timezone.utc)
    product = await db.get(Product, d.product_id)
    add_notification(db, d.buyer_id, "order_refunded", "Refund on its way",
                     f"GH₵{Decimal(d.amount_paid_pesewas) / 100:.2f} for {product.title if product else 'your order'} is being refunded to you in full. Paystack usually takes a few days.",
                     f"/orders/{d.id}")
    return True


async def safe_refund(db: AsyncSession, d: Delivery, reason: str) -> None:
    try:
        await refund_order(db, d, reason)
    except HTTPException:
        add_notification(db, d.buyer_id, "order_refunded", "Refund pending",
                         "We couldn't start your refund automatically. Our team has been alerted and will complete it.", f"/orders/{d.id}")


def valid_momo(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if digits.startswith("233"):
        digits = "0" + digits[3:]
    if not re.fullmatch(r"0\d{9}", digits):
        raise HTTPException(422, "Enter a valid Ghana MoMo number, e.g. 0241234567.")
    return digits


async def verify_and_save_momo(user: User, network: str, number: str) -> dict:
    code = MOMO_CODES.get(network)
    if code is None:
        raise HTTPException(422, "Choose MTN, Vodafone or AirtelTigo.")
    number = valid_momo(number)
    info = await _paystack("GET", f"/bank/resolve?account_number={number}&bank_code={code}")
    name = info.get("account_name")
    if not name:
        raise HTTPException(422, "We couldn't confirm that MoMo number. Check the network and number.")
    rec = await _paystack("POST", "/transferrecipient", json={
        "type": "mobile_money", "name": name, "account_number": number, "bank_code": code, "currency": CURRENCY})
    user.momo_network, user.momo_number, user.momo_name = network, number, name
    user.momo_recipient_code = rec.get("recipient_code")
    return {"momo_network": network, "momo_number": number, "momo_name": name}


async def _transfer(recipient: str, amount_pesewas: int, reference: str, reason: str) -> None:
    data = await _paystack("POST", "/transfer", json={"source": "balance", "amount": amount_pesewas, "recipient": recipient,
                                                      "reference": reference, "reason": reason[:100], "currency": CURRENCY})
    if data.get("status") in ("otp", "abandoned", "blocked", "rejected", "failed"):
        # Paystack is holding or refusing it (e.g. OTP still on): not paid, so it stays queued for a retry
        raise HTTPException(502, "Payout was not released by the payment provider.")


async def release_to_seller(db: AsyncSession, d: Delivery) -> bool:
    """Pay the seller the item price. Idempotent: the reference is fixed per order, so a retry can never double-pay."""
    if d.paid_at is None or d.refunded_at is not None or d.seller_paid_at is not None or d.status != "delivered":
        return False
    product = await db.get(Product, d.product_id)
    seller = await db.get(User, product.seller_id) if product else None
    if seller is None or not seller.momo_recipient_code:
        if seller:
            add_notification(db, seller.id, "payout_pending", "Add your MoMo number to get paid",
                             f"GH₵{d.item_amount:.2f} is waiting for you. Add your MoMo number in Settings.", "/settings")
        return False
    await _transfer(seller.momo_recipient_code, pesewas(d.item_amount), f"kmart_po_{d.id.hex}", f"KNUST Mart order {str(d.id)[:8]}")
    d.seller_paid_at = datetime.now(timezone.utc)
    add_notification(db, seller.id, "payout_sent", "You've been paid",
                     f"GH₵{d.item_amount:.2f} for {product.title} was sent to your MoMo ({seller.momo_number}).", "/sales")
    return True


async def pay_courier_batch(db: AsyncSession) -> int:
    """One transfer per courier for everything they've earned since the last batch."""
    rows = (await db.execute(select(CourierPayout).where(CourierPayout.status == "pending"))).scalars().all()
    by_courier: dict = {}
    for r in rows:
        by_courier.setdefault(r.courier_id, []).append(r)
    sent = 0
    for cid, items in by_courier.items():
        courier = await db.get(User, cid)
        if courier is None or not courier.momo_recipient_code:
            continue
        total = sum((i.amount for i in items), Decimal("0"))
        ref = f"kmart_cb_{uuid.uuid4().hex}"
        try:
            await _transfer(courier.momo_recipient_code, pesewas(total), ref, "KNUST Mart weekly courier payout")
        except HTTPException:
            continue
        for i in items:
            i.status, i.transfer_reference = "paid", ref
        add_notification(db, cid, "payout_sent", "Weekly payout sent",
                         f"GH₵{total:.2f} for {len(items)} deliver{'y' if len(items) == 1 else 'ies'} was sent to your MoMo ({courier.momo_number}).", "/courier")
        await db.commit()
        sent += 1
    return sent


ANNOUNCE = "payments_announcement"


async def send_announcement(db: AsyncSession) -> int:
    """One notification per user (idempotent). Sellers, couriers and buyers each get the part that concerns them."""
    already = set((await db.scalars(select(Notification.user_id).where(Notification.notification_type == ANNOUNCE))).all())
    seller_ids = set((await db.scalars(select(Product.seller_id).distinct())).all())
    users = (await db.execute(select(User).where(User.is_banned.is_(False)))).scalars().all()
    n = 0
    for u in users:
        if u.id in already:
            continue
        parts = ["Buyers: you now pay once, inside KNUST Mart, after the seller confirms the item is available. You see the item price, delivery fee and a small service fee (it covers Paystack and MoMo payout costs) before you pay. Your money is held safely and only released to the seller when you give the courier your code, or confirm pickup. Not happy? Don't give the code and report it, or cancel before handover for a full refund."]
        if u.id in seller_ids or u.store_live:
            parts.append("Sellers: add and confirm your MoMo number in Settings. You can't confirm orders without it. You receive the full item price by MoMo as soon as the handover is confirmed. The buyer pays the fees, not you.")
        if u.is_courier:
            parts.append("Couriers: buyers no longer pay you in person. Your full delivery fee is paid by MoMo once the buyer gives you their code. Earnings are sent in one weekly batch to the MoMo number you confirm in Settings. Courier price offers are replaced by the fixed delivery fee.")
        add_notification(db, u.id, ANNOUNCE, "Payments are changing: how you pay and get paid", " ".join(parts), "/settings")
        n += 1
    await db.commit()
    return n


async def sweeper() -> None:
    """Hourly: retry seller payouts, auto-release unconfirmed pickups, and pay couriers on Mondays."""
    from .db import Session
    last_batch = None
    while True:
        try:
            async with Session() as db:
                hours = int(_num("AUTO_RELEASE_HOURS", "72"))
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                stale = (await db.execute(select(Delivery).where(
                    Delivery.status == "ready_for_pickup", Delivery.paid_at.is_not(None), Delivery.paid_at < cutoff,
                    Delivery.refunded_at.is_(None)))).scalars().all()
                for d in stale:
                    d.status, d.completed_at = "delivered", datetime.now(timezone.utc)
                await db.commit()
                unpaid = (await db.execute(select(Delivery).where(
                    Delivery.status == "awaiting_payment",
                    Delivery.seller_responded_at < datetime.now(timezone.utc) - timedelta(hours=2)))).scalars().all()
                for d in unpaid:
                    d.status = "cancelled"
                    prod = await db.get(Product, d.product_id)
                    if prod:
                        release_unit(prod)
                    add_notification(db, d.buyer_id, "order_cancelled", "Order cancelled", "You didn't pay within 2 hours, so the item was released.", f"/orders/{d.id}")
                await db.commit()
                due = (await db.execute(select(Delivery).where(
                    Delivery.status == "delivered", Delivery.paid_at.is_not(None),
                    Delivery.seller_paid_at.is_(None), Delivery.refunded_at.is_(None)))).scalars().all()
                for d in due:
                    try:
                        await release_to_seller(db, d)
                    except HTTPException:
                        pass
                    await db.commit()
                today = datetime.now(timezone.utc).date()
                if today.weekday() == 0 and last_batch != today:
                    await pay_courier_batch(db)
                    last_batch = today
        except Exception:
            pass
        await asyncio.sleep(3600)
