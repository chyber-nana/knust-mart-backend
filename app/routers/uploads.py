import os, uuid, asyncio
import re
import boto3
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from ..models import User
from ..security import current_user

router = APIRouter(prefix="/uploads", tags=["uploads"])
TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAGIC = {".jpg": b"\xff\xd8\xff", ".png": b"\x89PNG", ".webp": b"RIFF"}
MAX_BYTES = 3 * 1024 * 1024

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    region_name="auto",
)
BUCKET = os.environ["R2_BUCKET"]
PUBLIC_BASE = os.environ["R2_PUBLIC_URL"].rstrip("/")
UPLOADED_IMAGE_NAME = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp)\Z")


def is_uploaded_image_url(url: str) -> bool:
    if url.startswith("/media/"):
        return True
    prefix = f"{PUBLIC_BASE}/"
    if not url.startswith(prefix):
        return False
    return UPLOADED_IMAGE_NAME.fullmatch(url[len(prefix):]) is not None

async def store_image(file: UploadFile) -> str:
    ext = TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(415, "Use a JPG, PNG or WebP image.")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Image must be under 3 MB.")
    if not data.startswith(MAGIC[ext]):
        raise HTTPException(415, "That file isn't a valid image.")
    name = f"{uuid.uuid4().hex}{ext}"
    await asyncio.to_thread(s3.put_object, Bucket=BUCKET, Key=name, Body=data,
                            ContentType=file.content_type)
    return f"{PUBLIC_BASE}/{name}"


@router.post("", status_code=201)
async def upload(file: UploadFile = File(...), user: User = Depends(current_user)):
    return {"url": await store_image(file)}