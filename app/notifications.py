import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Notification


def add_notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    notification_type: str,
    title: str,
    body: str,
    link: str | None = None,
) -> None:
    db.add(Notification(
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
    ))
