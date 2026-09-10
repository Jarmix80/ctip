"""Regresja wspólnego uruchamiania harmonogramów produkcyjnych i rozszerzeń testowych."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import main


@pytest.mark.parametrize("reconciliation_enabled", [False, True])
def test_lifespan_preserves_test_extensions_and_shipping(monkeypatch, reconciliation_enabled):
    """Scalenie Shipping nie usuwa zadań CRM, Bot Identity ani ich poprawnego zamknięcia."""
    for setting in (
        "backup_scheduler_enabled",
        "workflow_sheet_status_cache_scheduler_enabled",
        "contracts_workflow_maintenance_scheduler_enabled",
        "contracts_mailbox_scheduler_enabled",
        "device_sheet_outbox_scheduler_enabled",
        "dpd_info_enabled",
        "delivery_notifications_scheduler_enabled",
    ):
        monkeypatch.setattr(main.settings, setting, False)
    for setting in (
        "shipping_enabled",
        "bot_identity_enabled",
        "crm_enabled",
        "crm_retention_scheduler_enabled",
    ):
        monkeypatch.setattr(main.settings, setting, True)
    monkeypatch.setattr(main.settings, "shipping_ms_reconcile_enabled", reconciliation_enabled)
    monkeypatch.setattr(main, "ensure_workflow_sheet_status_cache_table", AsyncMock())
    calls = {}
    for scheduler in (
        "device_audit",
        "bot_identity",
        "crm_retention",
        "shipping_ms_reconciliation",
    ):
        for operation in ("start", "stop"):
            name = f"{operation}_{scheduler}_scheduler"
            calls[name] = AsyncMock()
            monkeypatch.setattr(main, name, calls[name])

    async def run_lifespan():
        async with main._app_lifespan(None):
            for name, call in calls.items():
                if name.startswith("stop_"):
                    call.assert_not_awaited()

    asyncio.run(run_lifespan())

    for name, call in calls.items():
        if "shipping_ms_reconciliation" in name and not reconciliation_enabled:
            call.assert_not_awaited()
        else:
            call.assert_awaited_once()
