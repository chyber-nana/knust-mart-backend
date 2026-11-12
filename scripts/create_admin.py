import asyncio
import os

from sqlalchemy import select

from app.db import Session
from app.models import User
from app.security import pwd


async def create_admin() -> None:
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    full_name = os.environ.get("ADMIN_FULL_NAME", "").strip()
    if not email or "@" not in email:
        raise RuntimeError("Set ADMIN_EMAIL to the administrator's email address.")
    if len(password) < 12:
        raise RuntimeError("Set ADMIN_PASSWORD to a unique password of at least 12 characters.")
    if len(full_name) < 2:
        raise RuntimeError("Set ADMIN_FULL_NAME to the administrator's name.")

    async with Session() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is not None:
            if not pwd.verify(password, user.password_hash):
                raise RuntimeError("ADMIN_EMAIL already exists; ADMIN_PASSWORD must match that account.")
            user.is_admin = True
            user.is_email_verified = True
            if user.user_type == "student":
                user.is_verified_student = True
        else:
            user = User(
                full_name=full_name,
                email=email,
                password_hash=pwd.hash(password),
                user_type="external",
                is_email_verified=True,
                is_admin=True,
            )
            db.add(user)
        await db.commit()
    print(f"Admin account ready for {email}.")


if __name__ == "__main__":
    asyncio.run(create_admin())
