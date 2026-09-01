"""Testy bezpiecznego uzgadniania znacznika testowego Alembic."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.reconcile_test_alembic_state import (
    expected_model_columns,
    schema_errors,
    verify_backup_manifest,
)


def test_expected_model_columns_includes_critical_branches() -> None:
    """Manifest modeli obejmuje tabele linii dostaw, Bot Identity i CRM."""

    expected = expected_model_columns()

    assert "delivery_case" in expected
    assert "device_ref" in expected["bot_identity_device"]
    assert "category" in expected["crm_case"]
    assert "crm_sales_sms_enabled" in expected["admin_user"]


def test_schema_errors_reports_only_missing_elements() -> None:
    """Dodatkowe kolumny bazy nie blokują kontroli zgodności wstecznej."""

    errors = schema_errors(
        {"crm_case": {"id", "category"}, "delivery_case": {"id"}},
        {"crm_case": {"id", "legacy_extra"}},
    )

    assert errors == [
        "ctip.crm_case: brak kolumn category",
        "brak tabeli ctip.delivery_case",
    ]


def test_verify_backup_manifest_accepts_matching_pair(tmp_path: Path) -> None:
    """Manifest musi potwierdzać obie kopie wymagane przed uzgodnieniem."""

    files = {
        "ctip_test.dump": b"postgres",
        "BAZAMS_TEST.FDB": b"firebird",
    }
    lines = []
    for filename, content in files.items():
        (tmp_path / filename).write_bytes(content)
        lines.append(f"{hashlib.sha256(content).hexdigest()}  {filename}")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    verify_backup_manifest(manifest)


def test_verify_backup_manifest_rejects_changed_file(tmp_path: Path) -> None:
    """Zmiana pliku po backupie blokuje modyfikację znacznika."""

    (tmp_path / "ctip_test.dump").write_bytes(b"changed")
    (tmp_path / "BAZAMS_TEST.FDB").write_bytes(b"firebird")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        f"{'0' * 64}  ctip_test.dump\n"
        f"{hashlib.sha256(b'firebird').hexdigest()}  BAZAMS_TEST.FDB\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Niezgodna suma SHA-256"):
        verify_backup_manifest(manifest)
