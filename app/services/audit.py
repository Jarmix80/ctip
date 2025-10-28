"""Narzędzia do rejestrowania działań administratorów."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminAuditLog


async def record_audit(
    session: AsyncSession,
    *,
    user_id: int | None,
    action: str,
    payload: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> None:
    """Dodaje wpis do dziennika audytowego."""
    entry = AdminAuditLog(
        user_id=user_id,
        action=action,
        payload=payload,
        client_ip=client_ip,
        created_at=datetime.now(UTC),
    )
    session.add(entry)
