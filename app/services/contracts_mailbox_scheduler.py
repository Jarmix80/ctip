"""Scheduler automatycznej synchronizacji mailboxa umów."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.audit import record_audit
from app.services.contracts_mailbox_sync_runtime import (
    parse_mailbox_sync_summary,
    run_mailbox_sync_subprocess,
)

logger = logging.getLogger(__name__)
_scheduler_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def _tail_text(value: str, *, max_lines: int = 80, max_chars: int = 4000) -> str:
    lines = value.splitlines()
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        return tail[-max_chars:]
    return tail


async def _record_scheduler_audit(payload: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        await record_audit(
            session,
            user_id=None,
            action="contracts_mailbox_sync_scheduler",
            client_ip="scheduler",
            payload=payload,
        )
        await session.commit()


async def contracts_mailbox_scheduler_tick() -> dict[str, Any]:
    """Wykonuje pojedynczy przebieg automatycznej synchronizacji mailboxa."""
    started_at = datetime.now(UTC)
    if not settings.mailbox_imap_host or not settings.mailbox_email_address:
        payload = {
            "result": "skipped",
            "reason": "missing_mailbox_config",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
        }
        return payload

    try:
        result = await asyncio.to_thread(
            run_mailbox_sync_subprocess,
            limit=max(1, int(settings.contracts_mailbox_sync_limit)),
            folder=str(settings.contracts_mailbox_sync_folder or "INBOX"),
            reprocess=bool(settings.contracts_mailbox_sync_reprocess),
            dry_run=False,
            timeout_seconds=max(30, int(settings.contracts_mailbox_sync_timeout_seconds)),
        )
        payload = {
            "result": "ok" if result.returncode == 0 else "error",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "exit_code": result.returncode,
            "summary": parse_mailbox_sync_summary(result.stdout or ""),
            "stdout_tail": _tail_text(result.stdout or ""),
            "stderr_tail": _tail_text(result.stderr or ""),
            "limit": int(settings.contracts_mailbox_sync_limit),
            "folder": str(settings.contracts_mailbox_sync_folder or "INBOX"),
            "reprocess": bool(settings.contracts_mailbox_sync_reprocess),
            "timeout_seconds": int(settings.contracts_mailbox_sync_timeout_seconds),
        }
    except subprocess.TimeoutExpired as exc:
        payload = {
            "result": "timeout",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "error": str(exc),
            "limit": int(settings.contracts_mailbox_sync_limit),
            "folder": str(settings.contracts_mailbox_sync_folder or "INBOX"),
            "reprocess": bool(settings.contracts_mailbox_sync_reprocess),
            "timeout_seconds": int(settings.contracts_mailbox_sync_timeout_seconds),
        }
    except Exception as exc:  # noqa: BLE001
        payload = {
            "result": "exception",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "error": str(exc),
            "limit": int(settings.contracts_mailbox_sync_limit),
            "folder": str(settings.contracts_mailbox_sync_folder or "INBOX"),
            "reprocess": bool(settings.contracts_mailbox_sync_reprocess),
            "timeout_seconds": int(settings.contracts_mailbox_sync_timeout_seconds),
        }

    try:
        await _record_scheduler_audit(payload)
    except Exception:  # noqa: BLE001
        logger.exception("Nie udalo sie zapisac audytu przebiegu scheduler'a mailboxa.")

    if payload.get("result") != "ok":
        logger.warning("Scheduler mailboxa zakonczyl przebieg: %s", payload.get("result"))

    return payload


async def _scheduler_loop() -> None:
    assert _stop_event is not None
    while not _stop_event.is_set():
        await contracts_mailbox_scheduler_tick()
        try:
            await asyncio.wait_for(
                _stop_event.wait(),
                timeout=max(60, int(settings.contracts_mailbox_sync_interval_seconds)),
            )
        except TimeoutError:
            continue


async def start_contracts_mailbox_scheduler() -> None:
    """Uruchamia scheduler mailboxa, jeżeli nie działa."""
    global _scheduler_task, _stop_event  # noqa: PLW0603
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(
        _scheduler_loop(),
        name="contracts-mailbox-scheduler",
    )
    logger.info(
        "Uruchomiono scheduler mailboxa (interval=%ss, limit=%s, folder=%s, reprocess=%s).",
        max(60, int(settings.contracts_mailbox_sync_interval_seconds)),
        int(settings.contracts_mailbox_sync_limit),
        str(settings.contracts_mailbox_sync_folder or "INBOX"),
        bool(settings.contracts_mailbox_sync_reprocess),
    )


async def stop_contracts_mailbox_scheduler() -> None:
    """Zatrzymuje scheduler mailboxa."""
    global _scheduler_task, _stop_event  # noqa: PLW0603
    if _scheduler_task is None:
        return
    if _stop_event is not None:
        _stop_event.set()
    await _scheduler_task
    _scheduler_task = None
    _stop_event = None
