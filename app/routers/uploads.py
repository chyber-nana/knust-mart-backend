import os, uuid, asyncio
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

@router.post("", status_code=201)
async def upload(file: UploadFile = File(...), user: User = Depends(current_user)):
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
    return {"url": f"{PUBLIC_BASE}/{name}"}