from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "firebird_sync_model_plik.py"
SPEC = importlib.util.spec_from_file_location("firebird_sync_model_plik", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_build_target_url_uzywa_slugu_modelu_i_prefiksu_ran(tmp_path: Path) -> None:
    imgdev_dir = tmp_path / "imgdev"
    imgdev_dir.mkdir()
    target = imgdev_dir / "ran_ricoh_mp_c307.png"
    target.write_bytes(b"png")

    result = MODULE.build_target_url("MP C307", imgdev_dir, "https://ksero-partner.com.pl/imgdev/")

    assert result == (
        "https://ksero-partner.com.pl/imgdev/ran_ricoh_mp_c307.png",
        "ran_ricoh_mp_c307.png",
    )


def test_build_database_candidates_dla_sciezki_dodaje_alias_fallback() -> None:
    result = MODULE.build_database_candidates("inbox/firebird/menadzer_serwisu.fdb", "BAZAMS_TEST")

    assert result[0].endswith("/inbox/firebird/menadzer_serwisu.fdb")
    assert result[1] == "BAZAMS_TEST"


def test_build_database_candidates_dla_aliasu_nie_dubluje_wartosci() -> None:
    result = MODULE.build_database_candidates("BAZAMS_TEST", "BAZAMS_TEST")

    assert result == ["BAZAMS_TEST"]


def test_resolve_default_host_wymusza_localhost_dla_trybu_local() -> None:
    assert MODULE.resolve_default_host("local", "192.168.0.8") == "127.0.0.1"


def test_classify_row_wybiera_finalny_plik_po_modelu_gdy_stary_url_byl_wspolny(
    tmp_path: Path,
) -> None:
    imgdev_dir = tmp_path / "imgdev"
    imgdev_dir.mkdir()
    (imgdev_dir / "ran_ricoh_im_2500.png").write_bytes(b"png")

    row = MODULE.ModelRow(
        id_model=455,
        marka="Ricoh",
        model="IM 2500",
        plik="https://ksero-partner.com.pl/imgdev/ricoh_im_c2000.png",
    )

    result = MODULE.classify_row(row, imgdev_dir, "https://ksero-partner.com.pl/imgdev/")

    assert result.status == "ready_update"
    assert result.plik_new == "https://ksero-partner.com.pl/imgdev/ran_ricoh_im_2500.png"


def test_classify_row_pomija_model_bez_finalnego_pliku(tmp_path: Path) -> None:
    imgdev_dir = tmp_path / "imgdev"
    imgdev_dir.mkdir()

    row = MODULE.ModelRow(
        id_model=30002486,
        marka="Ricoh",
        model="1805",
        plik="https://ksero-partner.com.pl/imgdev/ricoh_mp2000.png",
    )

    result = MODULE.classify_row(row, imgdev_dir, "https://ksero-partner.com.pl/imgdev/")

    assert result.status == "skip_missing_target"


def test_classify_row_pomija_marke_poza_rodzina_ricoh(tmp_path: Path) -> None:
    imgdev_dir = tmp_path / "imgdev"
    imgdev_dir.mkdir()
    (imgdev_dir / "ran_ricoh_mp_c307.png").write_bytes(b"png")

    row = MODULE.ModelRow(
        id_model=230,
        marka="Konica Minolta",
        model="+C 35",
        plik="https://ksero-partner.com.pl/imgdev/konica_c25.png",
    )

    result = MODULE.classify_row(row, imgdev_dir, "https://ksero-partner.com.pl/imgdev/")

    assert result.status == "skip_family"
