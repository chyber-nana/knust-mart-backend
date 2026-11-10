import os
import secrets
from html import escape
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..mailer import send_email
from ..models import EmailVerification, User
from ..notifications import add_notification
from ..schema import UserRegisterSchema as RegisterIn
from ..security import current_user, enforce_rate_limit, make_token, pwd

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_ATTEMPTS = 5

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

class VerifyIn(BaseModel):
    email: EmailStr
    otp_code: str = Field(pattern=r"^\d{6}$")

class VerifyLinkIn(BaseModel):
    verification_token: str = Field(min_length=32, max_length=128)

class ResendIn(BaseModel):
    email: EmailStr

def rate_limit_subjects(request: Request, email: str) -> list[str]:
    return [f"email:{email}"]

async def issue_otp(db: AsyncSession, email: str) -> None:
    await db.execute(delete(EmailVerification).where(EmailVerification.email == email))
    code = f"{secrets.randbelow(10**6):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        raise RuntimeError(f"Cannot issue a verification code for missing user {email}.")
    user.is_verified = False
    user.is_email_verified = False
    user.otp_code = code
    user.otp_expires_at = expires_at
    db.add(EmailVerification(email=email, otp_code=code, expires_at=expires_at))
    await db.commit()
    send_email(
        to=email,
        subject="KNUST Mart - Verify Your Student Email",
        body=f"Your verification code is: {code}. It expires in 10 minutes.",
        html_body=f"<p>Your verification code is: <strong>{code}</strong></p>",
    )


async def issue_email_link(db: AsyncSession, email: str) -> None:
    frontend_url = os.environ.get("FRONTEND_URL")
    if not frontend_url:
        raise RuntimeError("Set FRONTEND_URL in backend/.env")
    token = secrets.token_urlsafe(32)
    await db.execute(delete(EmailVerification).where(EmailVerification.email == email))
    db.add(EmailVerification(
        email=email,
        otp_code=None,
        verification_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    await db.commit()
    link = f"{frontend_url.rstrip('/')}/verify?token={token}"
    send_email(
        email,
        "Verify your KNUST Mart email",
        f"Confirm your email address: {link}\nThis link expires in 24 hours.",
        html_body=(
            "<p>Confirm your email address to finish creating your KNUST Mart account.</p>"
            f'<p><a href="{escape(link, quote=True)}">Verify email address</a></p>'
            "<p>This link expires in 24 hours.</p>"
        ),
    )


@router.post("/register", status_code=201)
async def register(data: RegisterIn, db: AsyncSession = Depends(get_db)):
    email = data.email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(409, "An account with this email already exists.")
    user = User(full_name=data.full_name, email=email, password_hash=pwd.hash(data.password),
                user_type=data.account_type, hostel_or_business_address=data.hostel_or_business_address,
                store_name=data.store_name)
    db.add(user)
    await db.flush()
    add_notification(
        db, user.id, "account_created", "Account created",
        "Verify your email address to finish setting up your account.",
        f"/verify?email={email}" if data.account_type == "student" else "/login",
    )
    await db.commit()
    if data.account_type == "student":
        await issue_otp(db, email)
    else:
        await issue_email_link(db, email)
    return {"detail": "Verification instructions sent."}

async def verify_otp(email: str, code: str, request: Request, db: AsyncSession):
    email = email.lower()
    await enforce_rate_limit(
        db, "otp_verify", rate_limit_subjects(request, email), 10, 15 * 60
    )
    rec = (await db.execute(select(EmailVerification).where(EmailVerification.email == email))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if (
        not rec
        or not rec.otp_code
        or rec.expires_at.replace(tzinfo=timezone.utc) < now
    ):
        raise HTTPException(400, "Code expired. Request a new one.")
    if rec.attempts >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many attempts. Request a new code.")
    if not secrets.compare_digest(rec.otp_code, code):
        rec.attempts += 1
        await db.commit()
        raise HTTPException(400, "Incorrect code.")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        raise HTTPException(400, "Verification code is invalid.")
    if not user.otp_code or not secrets.compare_digest(user.otp_code, code):
        raise HTTPException(400, "Verification code is invalid.")
    if (
        user.otp_expires_at is None
        or user.otp_expires_at.replace(tzinfo=timezone.utc) < now
    ):
        raise HTTPException(400, "Code expired. Request a new one.")
    user.is_email_verified = True
    user.is_verified_student = user.user_type == "student"
    user.is_verified = True
    user.otp_code = None
    user.otp_expires_at = None
    add_notification(
        db, user.id, "account_verified", "Email verified",
        "Your KNUST Mart account is ready to use.", "/",
    )
    await db.delete(rec)
    await db.commit()
    return {"access_token": make_token(user.email), "user_type": user.user_type}


@router.post("/verify")
async def verify(data: VerifyIn, request: Request, db: AsyncSession = Depends(get_db)):
    return await verify_otp(data.email, data.otp_code, request, db)


@router.post("/verify-email")
async def verify_email(
    email: EmailStr,
    request: Request,
    code: str = Query(pattern=r"^\d{6}$"),
    db: AsyncSession = Depends(get_db),
):
    return await verify_otp(str(email), code, request, db)

@router.post("/verify-link")
async def verify_link(data: VerifyLinkIn, db: AsyncSession = Depends(get_db)):
    rec = (await db.execute(
        select(EmailVerification).where(
            EmailVerification.verification_token == data.verification_token
        )
    )).scalar_one_or_none()
    if not rec or rec.expires_at < datetime.now(timezone.utc):
        raise HTTPException(400, "Verification link expired or invalid. Request a new one.")
    user = (await db.execute(select(User).where(User.email == rec.email))).scalar_one_or_none()
    if user is None:
        raise HTTPException(400, "Verification link expired or invalid. Request a new one.")
    user.is_email_verified = True
    user.is_verified = True
    add_notification(
        db, user.id, "account_verified", "Email verified",
        "Your KNUST Mart account is ready to use.", "/",
    )
    await db.delete(rec)
    await db.commit()
    return {"access_token": make_token(user.email), "user_type": user.user_type}


@router.post("/resend")
async def resend(data: ResendIn, request: Request, db: AsyncSession = Depends(get_db)):
    email = data.email.lower()
    await enforce_rate_limit(
        db, "otp_resend", rate_limit_subjects(request, email), 3, 60 * 60
    )
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user and not user.is_email_verified:
        if user.user_type == "student":
            await issue_otp(db, email)
        else:
            await issue_email_link(db, email)
    return {"detail": "If the account exists, a new code was sent."}

@router.post("/login")
async def login(data: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    email = data.email.lower()
    await enforce_rate_limit(
        db, "login", rate_limit_subjects(request, email), 10, 15 * 60
    )
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user or not pwd.verify(data.password, user.password_hash):
        raise HTTPException(401, "Email or password is incorrect.")
    if not user.is_email_verified:
        raise HTTPException(403, "Verify your email first.")
    if user.is_banned:
        return {
            "access_token": make_token({"sub": user.email, "scope": "appeal"}),
            "scope": "appeal",
        }
    return {"access_token": make_token(user.email)}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {"id": str(user.id), "full_name": user.full_name, "user_type": user.user_type,
            "is_courier": user.is_courier, "is_verified_student": user.is_verified_student,
            "is_admin": user.is_admin}


@router.post("/change-password")
async def change_password(
    data: ChangePasswordIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not pwd.verify(data.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    if pwd.verify(data.new_password, user.password_hash):
        raise HTTPException(400, "Choose a password different from your current one.")
    user.password_hash = pwd.hash(data.new_password)
    await db.commit()
    return {"detail": "Password updated."}
