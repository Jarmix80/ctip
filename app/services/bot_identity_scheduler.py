"""Okresowe, tylko do odczytu odświeżanie katalogu tożsamości z Firebirda."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.bot_identity_directory import sync_firebird_directory

logger = logging.getLogger(__name__)
_task: asyncio.Task[None] | None = None


async def _loop() -> None:
    while True:
        try:
            async with AsyncSessionLocal() as session:
                result = await sync_firebird_directory(session)
                if result.status != "completed":
                    logger.warning(
                        "Synchronizacja katalogu botów zakończona statusem %s",
                        result.status,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Synchronizacja katalogu botów nie powiodła się")
        await asyncio.sleep(max(60, settings.bot_identity_sync_interval_seconds))


async def start_bot_identity_scheduler() -> None:
    global _task
    if settings.bot_identity_enabled and (_task is None or _task.done()):
        _task = asyncio.create_task(_loop())


async def stop_bot_identity_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    await asyncio.gather(_task, return_exceptions=True)
    _task = None
