"""Testy zabezpieczeń skryptu kanonizacji historii DPD."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def load_module():
    """Ładuje skrypt bez uruchamiania połączenia z bazą."""
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "dpd_infoservices_dedupe.py"
    spec = importlib.util.spec_from_file_location(
        "dpd_infoservices_dedupe_test_module",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_raport_apply_jest_wyszukiwany_po_dokladnym_run_id(tmp_path: Path) -> None:
    """Rollback nie może użyć przypadkowego raportu innego przebiegu."""
    script = load_module()
    run_id = "2fd4778c-8276-4dc7-a799-ff063b0fe601"
    path = tmp_path / f"dpd_infoservices_dedupe_apply_{run_id}.json"
    path.write_text(
        json.dumps(
            {
                "mode": "apply",
                "run_id": run_id,
                "rollback_state": [{"id": 1}],
            }
        ),
        encoding="utf-8",
    )

    report = script._load_apply_report(tmp_path, run_id)

    assert report["rollback_state"] == [{"id": 1}]


def test_raport_o_niezgodnej_tozsamosci_jest_odrzucany(tmp_path: Path) -> None:
    """Nazwa pliku nie wystarcza do autoryzacji rollbacku."""
    script = load_module()
    run_id = "2fd4778c-8276-4dc7-a799-ff063b0fe601"
    path = tmp_path / f"dpd_infoservices_dedupe_apply_{run_id}.json"
    path.write_text(
        json.dumps({"mode": "apply", "run_id": "74a7fe49-a9ef-4f06-834a-e600685858ec"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="niezgodną tożsamość"):
        script._load_apply_report(tmp_path, run_id)


def test_run_id_spoza_formatu_uuid_jest_odrzucany(tmp_path: Path) -> None:
    """Identyfikator rollbacku nie może umożliwiać przejścia poza katalog raportów."""
    script = load_module()

    with pytest.raises(ValueError):
        script._load_apply_report(tmp_path, "../../dowolny-plik")
