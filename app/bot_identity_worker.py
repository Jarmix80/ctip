"""Samodzielny worker synchronizacji katalogu tożsamości botów."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.services.bot_identity_scheduler import (
    start_bot_identity_scheduler,
    stop_bot_identity_scheduler,
)

logger = logging.getLogger(__name__)


def _validate_environment() -> None:
    """Blokuje start workera przy konfiguracji umożliwiającej zapis do Firebird."""
    if not settings.bot_identity_enabled:
        raise RuntimeError("BOT_IDENTITY_ENABLED musi mieć wartość true.")
    if settings.fb_allow_writes:
        raise RuntimeError("Worker katalogu wymaga FB_ALLOW_WRITES=false.")
    if settings.pg_database == "ctip_test" and not settings.is_safe_test_firebird:
        raise RuntimeError("Worker testowy wymaga ctip_test oraz lokalnej testowej bazy Firebird.")


async def _run() -> None:
    """Uruchamia wyłącznie scheduler katalogu i czeka na zatrzymanie procesu."""
    _validate_environment()
    await start_bot_identity_scheduler()
    logger.info("Worker katalogu tożsamości został uruchomiony.")
    try:
        await asyncio.Event().wait()
    finally:
        await stop_bot_identity_scheduler()


def main() -> None:
    """Uruchamia worker jako osobny proces."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
