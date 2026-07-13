"""Zależności używane w trasach FastAPI."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import get_session
from app.models import AdminSession, AdminUser
from app.services import section_permissions
from app.services.security import hash_session_token


async def get_db_session(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AsyncSession:
    """Umożliwia wstrzykiwanie sesji SQLAlchemy."""
    return session


async def get_admin_session_context(
    token_header: str | None = Header(default=None, alias="X-Admin-Session"),
    token_cookie: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> tuple[AdminSession, AdminUser]:
    """Weryfikuje sesję użytkownika z nagłówka lub ciasteczka."""
    token = token_header or token_cookie
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Brak aktywnej sesji administratora.",
        )

    stmt = (
        select(AdminSession)
        .options(selectinload(AdminSession.user))
        .where(AdminSession.token == hash_session_token(token))
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


async def get_operator_user(
    admin_context: tuple[AdminSession, AdminUser] = Depends(
        get_admin_session_context
    ),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminUser:
    """Wymaga aktywnej sesji użytkownika z dostępem do sekcji operatora."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Brak uprawnień operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "operator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnień do sekcji operatora.",
        )
    return admin_user
