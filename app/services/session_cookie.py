"""Pomocnicza obsluga ciasteczka sesji administratora."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Response

from app.core.config import settings


def _normalize_expiration(value: datetime) -> datetime:
    """Normalizuje czas wygasniecia do strefy UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def set_admin_session_cookie(response: Response, *, token: str, expires_at: datetime) -> None:
    """Ustawia bezpieczne ciasteczko sesji kompatybilne z obecnym frontendem."""
    expires_at_utc = _normalize_expiration(expires_at)
    max_age = max(int((expires_at_utc - datetime.now(UTC)).total_seconds()), 0)
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=max_age,
        expires=expires_at_utc,
        path=settings.auth_cookie_path,
        domain=settings.auth_cookie_domain,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def clear_admin_session_cookie(response: Response) -> None:
    """Czyści ciasteczko sesji administratora."""
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path=settings.auth_cookie_path,
        domain=settings.auth_cookie_domain,
    )
