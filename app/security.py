from __future__ import annotations

import os
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from dotenv import load_dotenv
from .db import get_db
from .models import AuthRateLimit

if TYPE_CHECKING:
    from .models import User

load_dotenv()

SECRET = os.environ.get("JWT_SECRET")
if not SECRET or len(SECRET) < 32 or SECRET.lower().startswith(
    ("your_", "generate-", "replace-")
):
    raise RuntimeError("Set JWT_SECRET in backend/.env to a random value of at least 32 characters.")
ALGORITHM = "HS256"

# Drop-in replacement wrapper for passlib's 'pwd' object
class DirectBcryptHasher:
    def hash(self, password: str) -> str:
        pwd_bytes = password.encode('utf-8')[:72]
        hashed = bcrypt.hashpw(pwd_bytes, bcrypt.gensalt())
        return hashed.decode('utf-8')

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        pwd_bytes = plain_password.encode('utf-8')[:72]
        try:
            return bcrypt.checkpw(pwd_bytes, hashed_password.encode('utf-8'))
        except Exception:
            return False

pwd = DirectBcryptHasher()


async def enforce_rate_limit(
    db: AsyncSession,
    action: str,
    subjects: list[str],
    limit: int,
    window_seconds: int,
) -> None:
    now = datetime.now(timezone.utc)
    window_epoch = int(now.timestamp()) // window_seconds * window_seconds
    window_start = datetime.fromtimestamp(window_epoch, timezone.utc)
    exceeded = False

    for subject in set(subjects):
        statement = insert(AuthRateLimit).values(
            action=action,
            subject=subject,
            window_start=window_start,
            attempts=1,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["action", "subject", "window_start"],
            set_={"attempts": AuthRateLimit.attempts + 1},
        ).returning(AuthRateLimit.attempts)
        attempts = (await db.execute(statement)).scalar_one()
        exceeded = exceeded or attempts > limit

    await db.execute(
        delete(AuthRateLimit).where(
            AuthRateLimit.window_start < now - timedelta(days=1)
        )
    )
    await db.commit()
    if exceeded:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")


def make_token(data: any, expires_delta: timedelta = timedelta(days=1)) -> str:
    if isinstance(data, dict):
        to_encode = data.copy()
    else:
        to_encode = {"sub": str(data)}
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET, algorithm=ALGORITHM)

# Authentication dependency for protected routes
security_scheme = HTTPBearer()

async def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db)
):
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not isinstance(email, str) or payload.get("scope") is not None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    from .models import User  # Imported here to prevent circular import issues
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_email_verified:
        raise credentials_exception
    if user.is_banned:
        raise HTTPException(status_code=403, detail="This account is suspended.")
    if user.user_type == "student" and not user.is_verified_student:
        raise HTTPException(status_code=403, detail="Verify your student account first.")
    return user


async def appeal_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate appeal credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not isinstance(email, str) or payload.get("scope") != "appeal":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    from .models import User

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.is_banned or not user.is_email_verified:
        raise credentials_exception
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user