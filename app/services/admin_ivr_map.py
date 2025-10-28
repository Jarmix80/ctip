"""Operacje na tabeli ctip.ivr_map udostępniane w panelu administratora."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IvrMap


async def list_entries(session: AsyncSession) -> Sequence[IvrMap]:
    """Zwraca wszystkie wpisy mapowania IVR."""
    stmt = select(IvrMap).order_by(IvrMap.ext)
    result = await session.execute(stmt)
    return result.scalars().all()


async def fetch_entry(session: AsyncSession, ext: str) -> IvrMap | None:
    """Pobiera pojedynczy wpis mapowania."""
    return await session.get(IvrMap, ext)


async def upsert_entry(
    session: AsyncSession,
    *,
    ext: str,
    digit: int,
    sms_text: str,
    enabled: bool,
) -> IvrMap:
    """Tworzy lub aktualizuje wpis mapowania."""
    ext_normalized = ext.strip()
    entry = await session.get(IvrMap, ext_normalized)
    if entry is None:
        entry = IvrMap(ext=ext_normalized, digit=digit, sms_text=sms_text, enabled=enabled)
        session.add(entry)
    else:
        entry.digit = digit
        entry.sms_text = sms_text
        entry.enabled = enabled
    await session.flush()
    return entry


async def delete_entry(session: AsyncSession, ext: str) -> None:
    """Usuwa wpis mapowania IVR."""
    stmt = delete(IvrMap).where(IvrMap.ext == ext)
    await session.execute(stmt)
    await session.flush()
