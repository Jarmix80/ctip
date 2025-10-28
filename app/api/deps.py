"""Zależności używane w trasach FastAPI."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models import AdminSession, AdminUser


async def get_db_session(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AsyncSession:
    """Umożliwia wstrzykiwanie sesji SQLAlchemy."""
    return session


async def get_current_user_id(
    x_user_id: int | None = Header(default=None, alias="X-User-Id")
) -> int:
    """Prosta kontrola tożsamości użytkownika na bazie nagłówka X-User-Id."""
    if x_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Brak nagłówka X-User-Id",
        )
    return x_user_id


async def get_admin_session_context(
    token: str | None = Header(default=None, alias="X-Admin-Session"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> tuple[AdminSession, AdminUser]:
    """Weryfikuje token sesji administratora i zwraca (sesja, użytkownik)."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Brak nagłówka X-Admin-Session",
        )

    stmt = (
        select(AdminSession)
        .options(selectinload(AdminSession.user))
        .where(AdminSession.token == token)
    )
    result = await session.execute(stmt)
    admin_session = result.scalar_one_or_none()
    if admin_session is None or admin_session.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesja administratora nieaktywna",
        )
    now = datetime.now(UTC)
    expires_at = admin_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    else:
        expires_at = expires_at.astimezone(UTC)
    if admin_session.revoked_at is not None or expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesja administratora nieaktywna",
        )
    admin_user = admin_session.user
    if not admin_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto administratora jest zablokowane",
        )

    return admin_session, admin_user
