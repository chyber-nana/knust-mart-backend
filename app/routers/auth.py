import os
import secrets
from html import escape
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..mailer import send_email
from ..models import EmailVerification, User
from ..notifications import add_notification
from ..security import current_user, enforce_rate_limit, make_token, pwd

router = APIRouter(prefix="/auth", tags=["auth"])
STUDENT_DOMAINS = ("@st.knust.edu.gh", "@idl.knust.edu.gh")
MAX_ATTEMPTS = 5

class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=8)
    account_type: str = Field(pattern="^(student|external)$")
    hostel_or_business_address: str | None = None
    store_name: str | None = None

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
    db.add(EmailVerification(email=email, otp_code=code, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    await db.commit()
    send_email(email, "Your KNUST Mart code", f"Your verification code is {code}. It expires in 10 minutes.")


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
    is_student_email = email.endswith(STUDENT_DOMAINS)
    if data.account_type == "student" and not is_student_email:
        raise HTTPException(422, "Student accounts need an @st.knust.edu.gh or @idl.knust.edu.gh email.")
    if data.account_type == "external" and is_student_email:
        raise HTTPException(422, "Use a student account for KNUST student emails.")
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

@router.post("/verify")
async def verify(data: VerifyIn, request: Request, db: AsyncSession = Depends(get_db)):
    email = data.email.lower()
    await enforce_rate_limit(
        db, "otp_verify", rate_limit_subjects(request, email), 10, 15 * 60
    )
    rec = (await db.execute(select(EmailVerification).where(EmailVerification.email == email))).scalar_one_or_none()
    if not rec or not rec.otp_code or rec.expires_at < datetime.now(timezone.utc):
        raise HTTPException(400, "Code expired. Request a new one.")
    if rec.attempts >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many attempts. Request a new code.")
    if not secrets.compare_digest(rec.otp_code, data.otp_code):
        rec.attempts += 1
        await db.commit()
        raise HTTPException(400, "Incorrect code.")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    user.is_email_verified = True
    user.is_verified_student = user.user_type == "student"
    add_notification(
        db, user.id, "account_verified", "Email verified",
        "Your KNUST Mart account is ready to use.", "/",
    )
    await db.delete(rec)
    await db.commit()
    return {"access_token": make_token(user.email), "user_type": user.user_type}


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
    if user:
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
