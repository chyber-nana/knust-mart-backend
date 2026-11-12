from uuid import UUID
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import CampusAd, User
from ..security import admin_user
from .uploads import store_image

router = APIRouter(prefix="/ads", tags=["campus ads"])


def ad_payload(ad: CampusAd) -> dict:
    return {
        "id": str(ad.id),
        "image_url": ad.image_url,
        "alt_text": ad.alt_text,
        "link_url": ad.link_url,
        "is_active": ad.is_active,
        "created_at": ad.created_at,
    }


def validate_link_url(value: str | None) -> str | None:
    link = (value or "").strip()
    if not link:
        return None
    parsed = urlsplit(link)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, "Use a valid https:// or http:// flyer link.")
    return link


@router.get("")
async def list_active_ads(db: AsyncSession = Depends(get_db)):
    ads = (await db.execute(
        select(CampusAd)
        .where(CampusAd.is_active.is_(True))
        .order_by(CampusAd.created_at.desc())
    )).scalars().all()
    return [ad_payload(ad) for ad in ads]


@router.get("/admin")
async def list_admin_ads(
    _: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    ads = (await db.execute(
        select(CampusAd).order_by(CampusAd.created_at.desc())
    )).scalars().all()
    return [ad_payload(ad) for ad in ads]


@router.post("/admin", status_code=201)
async def create_ad(
    file: UploadFile = File(...),
    alt_text: str = Form(..., min_length=1, max_length=160),
    link_url: str | None = Form(default=None, max_length=2048),
    admin: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    description = alt_text.strip()
    if not description:
        raise HTTPException(422, "Add text describing the flyer.")
    destination = validate_link_url(link_url)
    image_url = await store_image(file)
    ad = CampusAd(
        image_url=image_url,
        alt_text=description,
        link_url=destination,
        created_by=admin.id,
    )
    db.add(ad)
    await db.commit()
    await db.refresh(ad)
    return ad_payload(ad)


@router.delete("/admin/{ad_id}", status_code=204)
async def delete_ad(
    ad_id: UUID,
    _: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    ad = await db.get(CampusAd, ad_id)
    if ad is None:
        raise HTTPException(404, "Flyer not found.")
    await db.delete(ad)
    await db.commit()
