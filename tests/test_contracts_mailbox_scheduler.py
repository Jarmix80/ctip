"""Testy pomocniczych funkcji scheduler'a synchronizacji mailboxa."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.services import contracts_mailbox_scheduler as scheduler


def test_cleanup_due_is_true_when_last_run_is_missing() -> None:
    now = datetime(2026, 5, 13, 14, 0, 0, tzinfo=UTC)
    assert scheduler._cleanup_due(now, None, 3600) is True


def test_cleanup_due_is_false_when_interval_not_elapsed() -> None:
    now = datetime(2026, 5, 13, 14, 0, 0, tzinfo=UTC)
    last_run = now - timedelta(minutes=10)
    assert scheduler._cleanup_due(now, last_run, 3600) is False


def test_cleanup_due_is_true_when_interval_elapsed() -> None:
    now = datetime(2026, 5, 13, 14, 0, 0, tzinfo=UTC)
    last_run = now - timedelta(hours=2)
    assert scheduler._cleanup_due(now, last_run, 3600) is True


def test_normalize_cleanup_settings_applies_safety_bounds() -> None:
    previous = (
        settings.contracts_mailbox_audit_cleanup_interval_seconds,
        settings.contracts_mailbox_audit_compact_after_days,
        settings.contracts_mailbox_audit_compact_max_chars,
        settings.contracts_mailbox_audit_delete_after_days,
    )
    try:
        settings.contracts_mailbox_audit_cleanup_interval_seconds = -1
        settings.contracts_mailbox_audit_compact_after_days = -7
        settings.contracts_mailbox_audit_compact_max_chars = 0
        settings.contracts_mailbox_audit_delete_after_days = -90

        interval_seconds, compact_after_days, compact_max_chars, delete_after_days = (
            scheduler._normalize_cleanup_settings()
        )

        assert interval_seconds == 300
        assert compact_after_days == 1
        assert compact_max_chars == 200
        assert delete_after_days == 0
    finally:
        (
            settings.contracts_mailbox_audit_cleanup_interval_seconds,
            settings.contracts_mailbox_audit_compact_after_days,
            settings.contracts_mailbox_audit_compact_max_chars,
            settings.contracts_mailbox_audit_delete_after_days,
        ) = previous


def test_scheduler_skips_when_mailbox_processing_is_disabled() -> None:
    previous = settings.contracts_mailbox_processing_enabled
    try:
        settings.contracts_mailbox_processing_enabled = False

        result = asyncio.run(scheduler.contracts_mailbox_scheduler_tick())

        assert result["result"] == "skipped"
        assert result["reason"] == "mailbox_processing_disabled"
    finally:
        settings.contracts_mailbox_processing_enabled = previous
