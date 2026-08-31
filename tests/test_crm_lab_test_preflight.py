"""Testy zabezpieczeń runtime CRM i LAB."""

from pathlib import Path

from app.core.config import settings
from scripts.crm_lab_test_preflight import validate_test_runtime


def _enable_safe_runtime(monkeypatch) -> None:
    monkeypatch.setattr(settings, "crm_enabled", True)
    monkeypatch.setattr(settings, "crm_lab_mode", True)
    monkeypatch.setattr(settings, "crm_public_prototype_mode", True)
    monkeypatch.setattr(settings, "pg_database", "ctip_test")
    monkeypatch.setattr(settings, "pg_host", "postgres")
    monkeypatch.setattr(settings, "pbx_host", "mock-ctip")
    monkeypatch.setattr(settings, "sms_test_mode", True)
    monkeypatch.setattr(settings, "block_client_communications", True)
    monkeypatch.setattr(settings, "fb_allow_writes", False)
    monkeypatch.setattr(settings, "fb_mode", "local")
    monkeypatch.setattr(settings, "fb_host", "firebird")
    monkeypatch.setattr(settings, "fb_database", "/data/BAZAMS_TEST.FDB")


def test_preflight_accepts_isolated_test_runtime(monkeypatch) -> None:
    _enable_safe_runtime(monkeypatch)

    assert validate_test_runtime() == []


def test_preflight_rejects_production_hosts_and_writes(monkeypatch) -> None:
    _enable_safe_runtime(monkeypatch)
    monkeypatch.setattr(settings, "pg_host", "192.168.0.8")
    monkeypatch.setattr(settings, "pbx_host", "192.168.0.11")
    monkeypatch.setattr(settings, "fb_allow_writes", True)

    errors = validate_test_runtime()

    assert any("PGHOST" in error for error in errors)
    assert any("PBX_HOST" in error for error in errors)
    assert any("FB_ALLOW_WRITES" in error for error in errors)


def test_compose_runs_preflight_as_module() -> None:
    """Compose uruchamia preflight z katalogiem głównym obrazu w sys.path."""
    compose = Path("compose.crm-lab.test.yml").read_text(encoding="utf-8")

    assert compose.count("python -m scripts.crm_lab_test_preflight") == 2
    assert "python scripts/crm_lab_test_preflight.py" not in compose
    assert "name: ctip-test_ctip_test_edge" in compose
    assert "name: ctip-test_ctip_test_internal" in compose
