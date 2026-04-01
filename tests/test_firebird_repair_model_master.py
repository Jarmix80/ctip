from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "firebird_repair_model_master.py"
SPEC = importlib.util.spec_from_file_location("firebird_repair_model_master", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_normalize_brand_family_mapuje_legacy_ricoh() -> None:
    assert MODULE.normalize_brand_family("Nashuatec") == "RICOH"
    assert MODULE.normalize_brand_family("Gestetner") == "RICOH"


def test_resolve_database_value_zostawia_sciezke_windowsowa_bez_repo_prefixu() -> None:
    value, local_path = MODULE.resolve_database_value("D:/BAZA_MS_KP/BAZAMS.FDB")

    assert value == "D:/BAZA_MS_KP/BAZAMS.FDB"
    assert local_path is None


def test_choose_reference_id_wybiera_match_po_rodzinie_gdy_brak_exact() -> None:
    reference_rows = {
        353: {"ID_MODEL": 353, "MARKA": "Ricoh", "MODEL": "MP 2555"},
    }
    exact_map, family_map = MODULE.build_reference_maps(reference_rows)

    target_id, reason = MODULE.choose_reference_id(
        {"ID_MODEL": 3001, "MARKA": "Nashuatec", "MODEL": "MP 2555"},
        exact_map,
        family_map,
    )

    assert target_id == 353
    assert reason == "family"


def test_build_extra_plans_oznacza_referenced_extra_jako_remap_delete() -> None:
    current_rows = {
        353: {"ID_MODEL": 353, "MARKA": "Ricoh", "MODEL": "MP 2555"},
        3001: {"ID_MODEL": 3001, "MARKA": "Nashuatec", "MODEL": "MP 2555"},
    }
    reference_rows = {
        353: {"ID_MODEL": 353, "MARKA": "Ricoh", "MODEL": "MP 2555"},
    }
    current_ref_counts = {
        "MASZYNA": {3001: 2},
        "MAGAZYN": {},
        "CENNIK": {},
        "MZ": {},
    }

    plans = MODULE.build_extra_plans(current_rows, reference_rows, current_ref_counts)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.id_model == 3001
    assert plan.action == "remap_delete"
    assert plan.target_id_model == 353


def test_build_extra_plans_oznacza_brak_mapowania_z_referencjami_jako_unresolved() -> None:
    current_rows = {
        3002: {"ID_MODEL": 3002, "MARKA": "Unknown", "MODEL": "XYZ-123"},
    }
    reference_rows = {
        353: {"ID_MODEL": 353, "MARKA": "Ricoh", "MODEL": "MP 2555"},
    }
    current_ref_counts = {
        "MASZYNA": {3002: 1},
        "MAGAZYN": {},
        "CENNIK": {},
        "MZ": {},
    }

    plans = MODULE.build_extra_plans(current_rows, reference_rows, current_ref_counts)

    assert len(plans) == 1
    assert plans[0].action == "unresolved_referenced"
