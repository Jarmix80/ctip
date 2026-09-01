"""Testy odbioru świeżej synchronizacji Bot Identity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from scripts.check_bot_identity_test_runtime import (
    is_ctip_v1_capabilities,
    is_recent_completed,
)


def test_capabilities_require_complete_ctip_v1_contract() -> None:
    """Kontrakt wymaga wersji, kategorii i wszystkich aktywnych funkcji."""

    payload = {
        "service": "ctip",
        "contract_version": "1.0",
        "categories": [
            "sales",
            "service",
            "accounting",
            "other",
            "contracts_settlements",
        ],
        "customer_resolution": True,
        "sms_verification": True,
        "masked_devices": True,
        "idempotent_sms": True,
        "idempotent_cases": True,
    }

    assert is_ctip_v1_capabilities(payload)
    payload["idempotent_cases"] = False
    assert not is_ctip_v1_capabilities(payload)


def test_recent_completed_accepts_fresh_success() -> None:
    """Świeży zakończony przebieg spełnia warunek odbioru."""

    now = datetime.now(UTC)
    run = SimpleNamespace(status="completed", ended_at=now)

    assert is_recent_completed(run, earliest=now - timedelta(minutes=1))


def test_recent_completed_rejects_failure_and_old_success() -> None:
    """Błąd albo historyczny sukces nie maskuje awarii bieżącego workera."""

    now = datetime.now(UTC)
    failed = SimpleNamespace(status="failed", ended_at=now)
    old = SimpleNamespace(status="completed", ended_at=now - timedelta(minutes=10))

    assert not is_recent_completed(failed, earliest=now - timedelta(minutes=1))
    assert not is_recent_completed(old, earliest=now - timedelta(minutes=1))
