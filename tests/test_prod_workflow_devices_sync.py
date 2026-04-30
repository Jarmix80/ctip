"""Testy helperow skryptu operacyjnego sync urzadzen FLOW."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prod_workflow_devices_sync.py"
SPEC = spec_from_file_location("prod_workflow_devices_sync", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Nie udalo sie zaladowac skryptu: {SCRIPT_PATH}")
MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

derive_brand_model = MODULE.derive_brand_model
normalize_firebird_token = MODULE.normalize_firebird_token
parse_serial_and_model = MODULE.parse_serial_and_model


def test_parse_serial_and_model_extracts_sn_and_cleans_suffix() -> None:
    serial, model = parse_serial_and_model("Ricoh IMC 300 S/N:3930P300261")
    assert serial == "3930P300261"
    assert model == "Ricoh IMC 300"


def test_parse_serial_and_model_handles_variant_spacing() -> None:
    serial, model = parse_serial_and_model("Ricoh MP 3055 S/N : C358P400700")
    assert serial == "C358P400700"
    assert model == "Ricoh MP 3055"


def test_derive_brand_model_handles_konica_minolta_prefix() -> None:
    brand, model = derive_brand_model("Konica Minolta C258 S/N:A7R0021020380")
    assert brand == "Konica Minolta"
    assert model == "C258"


def test_normalize_firebird_token_removes_separators() -> None:
    assert normalize_firebird_token("KP/4301/BNP/SRS") == "KP4301BNPSRS"
