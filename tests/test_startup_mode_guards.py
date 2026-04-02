"""Testy zabezpieczen trybu startowego skryptow uruchomieniowych."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def _run_script(
    script_name: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Uruchamia skrypt bash z kontrolowanym srodowiskiem testowym."""
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    return subprocess.run(
        ["/bin/bash", str(ROOT_DIR / script_name)],
        cwd=ROOT_DIR,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_run_stack_tmux_requires_explicit_production_confirmation() -> None:
    """Skrypt produkcyjny musi domyslnie odmawiac startu bez jawnej zgody."""
    result = _run_script("run_stack_tmux.sh", env={"ALLOW_PRODUCTION_START": "false"})

    assert result.returncode == 1
    assert "Start produkcyjny zostal zablokowany domyslnie." in result.stderr
    assert "./ctiptest" in result.stderr
    assert "./run_test_stack_tmux.sh" in result.stderr
    assert "ALLOW_PRODUCTION_START=true ./run_stack_tmux.sh" in result.stderr


def test_run_server_with_firebird_requires_confirmation_for_non_test_env() -> None:
    """Wariant z domyslnym .env nie moze wystartowac bez jawnej zgody."""
    result = _run_script(
        "run_server_with_firebird.sh",
        env={
            "ENV_FILE": str(ROOT_DIR / ".env"),
            "ALLOW_PRODUCTION_START": "false",
        },
    )

    assert result.returncode == 1
    assert "Start na pliku srodowiskowym" in result.stderr
    assert "Domyslny tryb uruchomienia calego systemu to srodowisko testowe." in result.stderr
    assert "ALLOW_PRODUCTION_START=true ./run_server_with_firebird.sh" in result.stderr


def test_run_server_with_firebird_allows_test_env_without_production_flag() -> None:
    """Srodowisko .env.test powinno ominac blokade produkcyjna."""
    empty_path_dir = ROOT_DIR / ".cache" / "test-bin-empty"
    empty_path_dir.mkdir(parents=True, exist_ok=True)
    tmux_stub = empty_path_dir / "tmux"
    tmux_stub.write_text(
        "#!/bin/sh\n"
        'if [ "${1:-}" = "has-session" ]; then\n'
        "  exit 1\n"
        "fi\n"
        'echo "tmux-stub" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    tmux_stub.chmod(0o755)
    docker_stub = empty_path_dir / "docker"
    docker_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker_stub.chmod(0o755)

    result = _run_script(
        "run_server_with_firebird.sh",
        env={
            "ENV_FILE": str(ROOT_DIR / ".env.test"),
            "ALLOW_PRODUCTION_START": "false",
            "START_FIREBIRD": "never",
            "SESSION_NAME": "ctip-guard-test",
            "PATH": f"{empty_path_dir}:/bin",
        },
    )

    assert result.returncode == 1
    assert "Start na pliku srodowiskowym" not in result.stderr
    assert "tmux-stub" in result.stderr


def test_ctiptest_keeps_reload_opt_in() -> None:
    """Domyslny start testowy nie powinien wlaczac reload watchera."""
    script_content = (ROOT_DIR / "ctiptest").read_text(encoding="utf-8")

    assert 'TEST_UVICORN_PORT="${TEST_UVICORN_PORT:-8000}"' in script_content
    assert 'TEST_UVICORN_RELOAD="${TEST_UVICORN_RELOAD:-false}"' in script_content
    assert "uvicorn_cmd" in script_content
    assert "app.main:app --reload --host" not in script_content
    assert "app.main:app --host 0.0.0.0 --port ${TEST_UVICORN_PORT}" in script_content
    assert 'uvicorn_cmd="${uvicorn_cmd} --reload"' in script_content


def test_ctiptest_publishes_lan_urls() -> None:
    """Skrypt testowy powinien nadpisywac URL-e publiczne adresem hosta."""
    script_content = (ROOT_DIR / "ctiptest").read_text(encoding="utf-8")

    assert 'ENV_FILE="${ENV_FILE:-${WORKDIR}/.env.test}"' in script_content
    assert 'TEST_PUBLIC_HOST="${TEST_PUBLIC_HOST:-}"' in script_content
    assert (
        'TEST_PUBLIC_BASE_URL="http://${TEST_PUBLIC_HOST_VALUE}:${TEST_UVICORN_PORT}"'
        in script_content
    )
    assert (
        "export ADMIN_PANEL_URL='${TEST_ADMIN_PANEL_URL}' FORM_PUBLIC_BASE_URL='${TEST_PUBLIC_BASE_URL}'"
        in script_content
    )
    assert "publiczny adres WWW" in script_content
    assert (
        'assert_env_value_not_production "PGHOST" "${PRODUCTION_DB_HOST}" "bazę PostgreSQL"'
        in script_content
    )
    assert (
        'assert_env_value_not_production "FB_HOST" "${PRODUCTION_DB_HOST}" "bazę Firebird"'
        in script_content
    )
    assert 'assert_env_value_equals "PGDATABASE" "${LOCAL_TEST_DATABASE}"' in script_content
    assert 'assert_env_value_equals "SMS_TEST_MODE" "true"' in script_content


def test_run_test_stack_tmux_publishes_lan_urls() -> None:
    """Alternatywny skrypt testowy musi publikowac adres WWW dla LAN."""
    script_content = (ROOT_DIR / "run_test_stack_tmux.sh").read_text(encoding="utf-8")

    assert 'ENV_FILE="${ENV_FILE:-${WORKDIR}/.env.test}"' in script_content
    assert 'TEST_UVICORN_PORT="${TEST_UVICORN_PORT:-8000}"' in script_content
    assert 'TEST_PUBLIC_HOST="${TEST_PUBLIC_HOST:-}"' in script_content
    assert (
        'TEST_PUBLIC_BASE_URL="http://${TEST_PUBLIC_HOST_VALUE}:${TEST_UVICORN_PORT}"'
        in script_content
    )
    assert (
        "export ADMIN_PANEL_URL='${TEST_ADMIN_PANEL_URL}' FORM_PUBLIC_BASE_URL='${TEST_PUBLIC_BASE_URL}'"
        in script_content
    )
    assert "publiczny adres WWW" in script_content
    assert (
        'assert_env_value_not_production "PGHOST" "${PRODUCTION_DB_HOST}" "bazę PostgreSQL"'
        in script_content
    )
    assert (
        'assert_env_value_not_production "FB_HOST" "${PRODUCTION_DB_HOST}" "bazę Firebird"'
        in script_content
    )
    assert 'assert_env_value_equals "PGDATABASE" "${LOCAL_TEST_DATABASE}"' in script_content


def test_run_server_with_firebird_defaults_to_env_test() -> None:
    """Skrypt Firebird ma lokalnie domyslnie startowac na .env.test."""
    script_content = (ROOT_DIR / "run_server_with_firebird.sh").read_text(encoding="utf-8")

    assert 'ENV_FILE="${ENV_FILE:-${WORKDIR}/.env.test}"' in script_content
    assert 'SESSION_NAME="${SESSION_NAME:-ctip-stack-test}"' in script_content
    assert (
        'assert_env_value_not_production "PGHOST" "${PRODUCTION_DB_HOST}" "bazę PostgreSQL"'
        in script_content
    )
    assert 'assert_env_value_equals "PGDATABASE" "${LOCAL_TEST_DATABASE}"' in script_content
