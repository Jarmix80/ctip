"""Zgodność pętli zdarzeń asyncio dla uruchomień na Windows."""

from __future__ import annotations

import asyncio
import sys


def configure_asyncio_for_windows() -> None:
    """Ustawia pętlę zgodną z asynchronicznym sterownikiem PostgreSQL na Windows."""
    if sys.platform != "win32":
        return

    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return

    if isinstance(asyncio.get_event_loop_policy(), selector_policy):
        return

    asyncio.set_event_loop_policy(selector_policy())


__all__ = ["configure_asyncio_for_windows"]
