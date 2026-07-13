"""Ochrona punktów logowania przed atakami słownikowymi."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AdminAuditLog
from app.services.audit import record_audit


def normalize_login_email(value: str) -> str:
    """Normalizuje adres e-mail używany do kontroli prób logowania."""
    return str(value or "").strip().lower()


def login_email_hash(value: str) -> str:
    """Zwraca skrót adresu e-mail bez zapisywania go w audycie."""
    return hashlib.sha256(normalize_login_email(value).encode("utf-8")).hexdigest()


def request_client_ip(request) -> str | None:  # noqa: ANN001
    """Zwraca adres klienta ustalony przez serwer ASGI."""
    return request.client.host if request.client else None


async def login_is_rate_limited(
    session: AsyncSession,
    *,
    email: str,
    client_ip: str | None,
) -> bool:
    """Sprawdza liczbę nieudanych prób dla adresu IP i konta."""
    since = datetime.now(UTC) - timedelta(minutes=settings.login_failure_window_minutes)
    stmt = select(AdminAuditLog.payload).where(
        AdminAuditLog.action == "security_login_failed",
        AdminAuditLog.client_ip == client_ip,
        AdminAuditLog.created_at >= since,
    )
    rows = (await session.execute(stmt)).scalars().all()
    email_hash = login_email_hash(email)
    failures = sum(
        1
        for payload in rows
        if isinstance(payload, dict) and payload.get("email_hash") == email_hash
    )
    return failures >= settings.login_failure_limit


async def record_login_failure(
    session: AsyncSession,
    *,
    email: str,
    client_ip: str | None,
    channel: str,
    reason: str,
    user_id: int | None = None,
) -> None:
    """Rejestruje odrzuconą próbę bez danych uwierzytelniających."""
    await record_audit(
        session,
        user_id=user_id,
        action="security_login_failed",
        client_ip=client_ip,
        payload={
            "email_hash": login_email_hash(email),
            "channel": channel,
            "reason": reason,
        },
    )


async def record_login_rate_limit(
    session: AsyncSession,
    *,
    email: str,
    client_ip: str | None,
    channel: str,
) -> None:
    """Rejestruje zablokowanie logowania po przekroczeniu limitu."""
    await record_audit(
        session,
        user_id=None,
        action="security_login_rate_limited",
        client_ip=client_ip,
        payload={"email_hash": login_email_hash(email), "channel": channel},
    )


__all__ = [
    "login_is_rate_limited",
    "record_login_failure",
    "record_login_rate_limit",
    "request_client_ip",
]
