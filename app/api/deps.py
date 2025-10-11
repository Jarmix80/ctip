"""Zależności używane w trasach FastAPI."""

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session


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
