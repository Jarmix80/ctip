"""Scheduler przypomnień o końcach umów GRENKE."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.audit import record_audit
from app.services.delivery import send_grenke_contract_end_reminders

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


async def delivery_notifications_tick() -> dict[str, int]:
    """Wykonuje pojedynczy przebieg przypomnień o końcach umów."""
    async with AsyncSessionLocal() as session:
        result = await send_grenke_contract_end_reminders(session)
        await record_audit(
            session,
            user_id=None,
            action="grenke_contract_end_reminders_scheduler",
            client_ip="scheduler",
            payload=result,
        )
        await session.commit()
        return result


async def _scheduler_loop() -> None:
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            await delivery_notifications_tick()
        except Exception:  # noqa: BLE001
            logger.exception("Nie udało się wykonać przypomnień końców umów GRENKE.")
        try:
            await asyncio.wait_for(
                _stop_event.wait(),
                timeout=max(3600, settings.delivery_notifications_interval_seconds),
            )
        except TimeoutError:
            continue


async def start_delivery_notifications_scheduler() -> None:
    """Uruchamia scheduler przypomnień, jeżeli jest włączony."""
    global _scheduler_task, _stop_event
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(_scheduler_loop(), name="delivery-notifications")


async def stop_delivery_notifications_scheduler() -> None:
    """Zatrzymuje scheduler przypomnień."""
    global _scheduler_task, _stop_event
    if _scheduler_task is None:
        return
    if _stop_event is not None:
        _stop_event.set()
    await _scheduler_task
    _scheduler_task = None
    _stop_event = None


__all__ = [
    "delivery_notifications_tick",
    "start_delivery_notifications_scheduler",
    "stop_delivery_notifications_scheduler",
]
