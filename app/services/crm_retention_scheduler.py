"""Cykliczna retencja spraw Centrum Obsługi."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.crm_cases import purge_expired_cases

logger = logging.getLogger(__name__)
_task: asyncio.Task[None] | None = None


async def _loop() -> None:
    while True:
        try:
            async with AsyncSessionLocal() as session:
                cases, events = await purge_expired_cases(session)
                await session.commit()
                if cases:
                    logger.info(
                        "Retencja CRM usunęła %s spraw i %s zdarzeń",
                        cases,
                        events,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retencja CRM nie powiodła się")
        await asyncio.sleep(max(300, settings.crm_retention_interval_seconds))


async def start_crm_retention_scheduler() -> None:
    """Uruchamia pojedynczą pętlę retencji, gdy moduł jest aktywny."""
    global _task
    if (
        settings.crm_enabled
        and settings.crm_retention_scheduler_enabled
        and (_task is None or _task.done())
    ):
        _task = asyncio.create_task(_loop())


async def stop_crm_retention_scheduler() -> None:
    """Kończy działającą pętlę retencji."""
    global _task
    if _task is None:
        return
    _task.cancel()
    await asyncio.gather(_task, return_exceptions=True)
    _task = None
