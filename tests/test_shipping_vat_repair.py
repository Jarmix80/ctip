"""Testy celowanej korekty stawki VAT dokumentów Shipping."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "repair_shipping_invoice_vat_rate.py"
)
SPEC = importlib.util.spec_from_file_location("repair_shipping_invoice_vat_rate", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _expectation():
    return MODULE.RepairExpectation(
        invoice_number="5348/KPSK/2026",
        invoice_id=64590,
        wz_id=38830,
        expected_lines=4,
        expected_vat_id=1,
        source_rate="23.0 %",
        target_rate="23 %",
    )


def _line(row_id: int, warehouse_item_id: int, rate_text: str):
    return MODULE.VatLine(
        row_id=row_id,
        warehouse_item_id=warehouse_item_id,
        rate_text=rate_text,
        vat_id=1,
        net_value=Decimal("100.00"),
        vat_value=Decimal("23.00"),
    )


def _snapshot(rate_text: str = "23.0 %", *, ksef_number: str | None = None):
    warehouse_ids = (13980, 13981, 13982, 14078)
    return MODULE.RepairSnapshot(
        invoice_id=64590,
        invoice_number="5348/KPSK/2026",
        document_kind="KPSK",
        wz_id=38830,
        total_net=Decimal("400.00"),
        total_vat=Decimal("92.00"),
        total_gross=Decimal("492.00"),
        invoice_lines=tuple(
            _line(290931 + offset, item_id, rate_text)
            for offset, item_id in enumerate(warehouse_ids)
        ),
        wz_lines=tuple(
            _line(102700 + offset, item_id, rate_text)
            for offset, item_id in enumerate(warehouse_ids)
        ),
        warehouse_rates=tuple((item_id, "23 %", 1) for item_id in warehouse_ids),
        ksef_attempts=(
            MODULE.KsefAttempt(
                status_code="450",
                ksef_number=ksef_number,
                reference_number="20260901-EE-TEST",
            ),
        ),
    )


def _args(*, apply: bool = False, confirmation: str = "") -> argparse.Namespace:
    return argparse.Namespace(
        invoice_number="5348/KPSK/2026",
        expected_invoice_id=64590,
        expected_wz_id=38830,
        expected_lines=4,
        expected_vat_id=1,
        from_rate="23.0 %",
        to_rate="23 %",
        confirmation=confirmation,
        apply=apply,
        report_dir=Path("runtime/repairs"),
    )


def test_walidacja_rozpoznaje_dokument_gotowy_i_juz_poprawiony() -> None:
    assert MODULE.validate_snapshot(_snapshot(), _expectation()) == "ready"
    assert MODULE.validate_snapshot(_snapshot("23 %"), _expectation()) == "already_corrected"


def test_walidacja_odmawia_korekty_faktury_z_numerem_ksef() -> None:
    with pytest.raises(MODULE.RepairValidationError, match="ma już numer KSeF"):
        MODULE.validate_snapshot(_snapshot(ksef_number="NUMER-KSEF"), _expectation())


def test_walidacja_odmawia_mieszanych_stawek() -> None:
    snapshot = _snapshot()
    mixed_lines = list(snapshot.invoice_lines)
    mixed_lines[0] = _line(290931, 13980, "23 %")
    snapshot = replace(snapshot, invoice_lines=tuple(mixed_lines))
    with pytest.raises(MODULE.RepairValidationError, match="mieszane"):
        MODULE.validate_snapshot(snapshot, _expectation())


def test_apply_aktualizuje_wylacznie_wskazana_fv_i_wz() -> None:
    connection = MagicMock()
    corrected = _snapshot("23 %")
    with patch.object(MODULE, "load_snapshot", return_value=corrected):
        result = MODULE.apply_repair(connection, _expectation())

    assert result == corrected
    calls = connection.cursor.return_value.execute.call_args_list
    assert len(calls) == 2
    assert "UPDATE FPOZYCJA" in calls[0].args[0]
    assert calls[0].args[1] == ("23 %", 64590, "23.0 %", 1)
    assert "UPDATE ZAKPOZYCJA" in calls[1].args[0]
    assert calls[1].args[1] == ("23 %", 38830, "23.0 %", 1)


def test_odczyt_wz_uzywa_kolumny_id_magazyn() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = [(1, 13980, "23.0 %", 1, 100, 23)]

    lines = MODULE._load_lines(
        cursor,
        table="ZAKPOZYCJA",
        parent_column="ID_ZAKUPY",
        parent_id=38830,
    )

    assert lines[0].warehouse_item_id == 13980
    assert "ID_MAGAZYN" in cursor.execute.call_args.args[0]
    assert "ID_MAGPOZ" not in cursor.execute.call_args.args[0]


def test_dry_run_wykonuje_rollback_i_nie_wykonuje_apply(tmp_path: Path) -> None:
    connection = MagicMock()
    report_path = tmp_path / "report.json"
    with (
        patch.object(MODULE, "parse_args", return_value=_args()),
        patch.object(MODULE, "load_snapshot", return_value=_snapshot()),
        patch.object(MODULE, "apply_repair") as apply_repair,
        patch.object(MODULE, "write_report", return_value=report_path),
        patch(
            "app.services.firebird_runtime.firebird_connection",
            return_value=connection,
        ),
    ):
        assert MODULE.run() == 0

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    apply_repair.assert_not_called()


def test_apply_wymaga_dokladnej_frazy_potwierdzajacej() -> None:
    with patch.object(MODULE, "parse_args", return_value=_args(apply=True)):
        with pytest.raises(SystemExit, match="Niepoprawna fraza"):
            MODULE.run()
