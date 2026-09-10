"""Testy kontrolowanej naprawy pełnego zamknięcia zleceń Shipping w MS."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "repair_shipping_ms_closure.py"
SPEC = importlib.util.spec_from_file_location("repair_shipping_ms_closure", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _candidate():
    return MODULE.ClosureCandidate(
        order_table_id=83691,
        order_id=18691,
        order_year=2026,
        closed_date=date(2026, 9, 8),
        operator_name="Marcin Jarmuszkiewicz",
        tracking_number="1050613059113U",
        rw_id=38952,
        rw_number="RW / 2213 / 2026",
        wz_id=None,
        wz_number=None,
        invoice_id=None,
        invoice_number=None,
    )


def _snapshot(*, complete: bool = False):
    return MODULE.FirebirdOrderSnapshot(
        order_table_id=83691,
        order_id=18691,
        order_year=2026,
        state="Z",
        closed_date=date(2026, 9, 8) if complete else None,
        operator_text=(
            "Utworzył: JoannaG, Zamknął :Marcin Jarmuszkiewicz" if complete else "Utworzył: JoannaG"
        ),
        tracking_number="1050613059113U",
        rw_id=38952,
        wz_id=None,
        invoice_id=None,
        document_number="RW / 2213 / 2026",
    )


def _args(*, apply: bool = False, confirmation: str = "", report_dir: Path | None = None):
    return argparse.Namespace(
        apply=apply,
        confirmation=confirmation,
        report_dir=report_dir or Path("runtime/repairs"),
    )


def test_walidacja_rozpoznaje_niepelne_i_poprawne_zamkniecie() -> None:
    status, operator = MODULE.validate_candidate(_candidate(), _snapshot())
    assert status == "do_naprawy"
    assert operator.endswith("Zamknął :Marcin Jarmuszkiewicz")

    status, operator = MODULE.validate_candidate(_candidate(), _snapshot(complete=True))
    assert status == "już_poprawne"
    assert operator == _snapshot(complete=True).operator_text


def test_walidacja_blokuje_rozbieznosc_dokumentu() -> None:
    snapshot = replace(_snapshot(), document_number="RW / 9999 / 2026")

    with pytest.raises(MODULE.RepairValidationError, match="inne powiązanie dokumentu"):
        MODULE.validate_candidate(_candidate(), snapshot)


def test_apply_uzupelnia_wylacznie_date_i_operatora() -> None:
    connection = MagicMock()
    with patch.object(
        MODULE,
        "load_firebird_order",
        side_effect=[_snapshot(), _snapshot(complete=True)],
    ):
        result = MODULE.repair_candidates(connection, (_candidate(),), apply=True)

    update = connection.cursor.return_value.execute.call_args
    assert "SET DATA_Z = ?, OPERATOR = ?" in update.args[0]
    assert update.args[1] == (
        date(2026, 9, 8),
        "Utworzył: JoannaG, Zamknął :Marcin Jarmuszkiewicz",
        83691,
    )
    assert result["repair_required_count"] == 1
    assert result["repaired_count"] == 1


def test_brakujace_zlecenie_jest_raportowane_bez_bledu() -> None:
    connection = MagicMock()
    with patch.object(MODULE, "load_firebird_order", return_value=None):
        result = MODULE.repair_candidates(connection, (_candidate(),), apply=False)

    assert result["missing_count"] == 1
    assert result["rows"][0]["status"] == "brak_w_firebird"
    connection.cursor.return_value.execute.assert_not_called()


def test_dry_run_wykonuje_rollback_bez_zapisu(tmp_path: Path) -> None:
    connection = MagicMock()
    report_path = tmp_path / "report.json"
    with (
        patch.object(MODULE, "parse_args", return_value=_args(report_dir=tmp_path)),
        patch.object(MODULE, "load_candidates", new=AsyncMock(return_value=(_candidate(),))),
        patch.object(
            MODULE,
            "repair_candidates",
            return_value={
                "candidate_count": 1,
                "repair_required_count": 1,
                "repaired_count": 0,
                "already_correct_count": 0,
                "missing_count": 0,
                "rows": [],
            },
        ),
        patch.object(MODULE, "write_report", return_value=report_path),
        patch("app.services.firebird_runtime.firebird_connection", return_value=connection),
    ):
        assert MODULE.run() == 0

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_apply_wymaga_dokladnej_frazy_potwierdzajacej() -> None:
    with patch.object(MODULE, "parse_args", return_value=_args(apply=True)):
        with pytest.raises(SystemExit, match="Niepoprawna fraza"):
            MODULE.run()
