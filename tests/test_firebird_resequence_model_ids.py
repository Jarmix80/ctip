from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "firebird_resequence_model_ids.py"
)
SPEC = importlib.util.spec_from_file_location("firebird_resequence_model_ids", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_resolve_database_value_dla_sciezki_windowsowej_nie_dodaje_repo_prefixu() -> None:
    value, local_path = MODULE.resolve_database_value("D:/BAZA_MS_KP/BAZAMS.FDB")

    assert value == "D:/BAZA_MS_KP/BAZAMS.FDB"
    assert local_path is None


def test_build_model_id_mapping_przenosi_duze_id_na_kolejne_numery() -> None:
    mapping = MODULE.build_model_id_mapping(
        [33, 631, 30002486, 30002496, 30002523],
        max_stable_id=631,
    )

    assert mapping == {
        30002486: 632,
        30002496: 633,
        30002523: 634,
    }


def test_build_model_id_mapping_pomija_id_ktore_juz_sa_w_zakresie_docelowym() -> None:
    mapping = MODULE.build_model_id_mapping(
        [33, 631, 632, 633, 700],
        max_stable_id=631,
    )

    assert mapping == {700: 634}


def test_temp_model_id_zwraca_ujemny_id_bezpieczny_dla_przejsciowego_update() -> None:
    assert MODULE.temp_model_id(30002523) == -30002523
    assert MODULE.temp_model_id(-12) == -12
