from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "codex_preflight.py"
SPEC = importlib.util.spec_from_file_location("codex_preflight", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_assess_test_mode_akceptuje_lokalna_konfiguracje() -> None:
    env_file = Path(".env.test")
    cfg = {
        "PBX_HOST": "127.0.0.1",
        "PGHOST": "127.0.0.1",
        "PGDATABASE": "ctip_test",
        "FB_HOST": "127.0.0.1",
        "FB_V_HOST": "127.0.0.1",
        "SMS_TEST_MODE": "true",
    }

    assert MODULE.assess_test_mode(env_file, cfg) == []


def test_assess_test_mode_wykrywa_produkcyjne_adresy_i_zla_baze() -> None:
    env_file = Path(".env")
    cfg = {
        "PBX_HOST": "192.168.0.11",
        "PGHOST": "192.168.0.8",
        "PGDATABASE": "ctip",
        "FB_HOST": "192.168.0.8",
        "FB_V_HOST": "192.168.0.8",
        "SMS_TEST_MODE": "false",
    }

    issues = MODULE.assess_test_mode(env_file, cfg)

    assert any("niestandardowym pliku środowiskowym" in issue for issue in issues)
    assert any("PBX_HOST wskazuje" in issue for issue in issues)
    assert any("PGHOST wskazuje" in issue for issue in issues)
    assert any("FB_HOST wskazuje" in issue for issue in issues)
    assert any("FB_V_HOST wskazuje" in issue for issue in issues)
    assert any("PGDATABASE ma wartość ctip" in issue for issue in issues)
    assert any("SMS_TEST_MODE nie ma wartości true" in issue for issue in issues)


def test_load_effective_env_nadpisuje_plikiem_i_srodowiskiem(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.test"
    env_file.write_text("PGHOST=127.0.0.1\nPGDATABASE=ctip_test\n", encoding="utf-8")

    result = MODULE.load_effective_env(
        env_file,
        environ={
            "PGHOST": "localhost",
            "SMS_TEST_MODE": "true",
        },
    )

    assert result["PGHOST"] == "localhost"
    assert result["PGDATABASE"] == "ctip_test"
    assert result["SMS_TEST_MODE"] == "true"


def test_preflight_result_status_rozroznia_niejasnosc_i_brak_modulow() -> None:
    ready = MODULE.PreflightResult(env_file=Path(".env.test"))
    stopped = MODULE.PreflightResult(
        env_file=Path(".env.test"),
        service_issues=["HTTP nie dziala"],
    )
    ambiguous = MODULE.PreflightResult(
        env_file=Path(".env.test"),
        test_mode_issues=["PGHOST wskazuje na produkcje"],
    )

    assert ready.status == "ready"
    assert stopped.status == "stopped"
    assert ambiguous.status == "ambiguous"
