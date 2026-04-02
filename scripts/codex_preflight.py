"""Preflight lokalnego środowiska testowego przed startem Codexa."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import psycopg
from dotenv import dotenv_values

PRODUCTION_DB_HOST = "192.168.0.8"
PRODUCTION_PBX_HOST = "192.168.0.11"
LOCAL_TEST_DATABASE = "ctip_test"
DEFAULT_SESSION_NAME = "ctip-stack-test"
DEFAULT_APP_PORT = 8000


@dataclass(slots=True)
class PreflightResult:
    """Zbiorczy wynik kontroli środowiska testowego."""

    env_file: Path
    test_mode_issues: list[str] = field(default_factory=list)
    service_issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tmux_windows: list[str] = field(default_factory=list)
    postgres_ok: bool = False
    health_ok: bool = False
    pbx_ok: bool = False

    @property
    def status(self) -> str:
        """Zwraca skondensowany status preflightu."""
        if self.test_mode_issues:
            return "ambiguous"
        if self.service_issues:
            return "stopped"
        return "ready"


def load_effective_env(env_file: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Buduje końcową konfigurację z pliku dotenv oraz nadpisujących zmiennych procesu."""
    values = {
        key: value for key, value in dotenv_values(env_file).items() if key and value is not None
    }
    process_env = environ or os.environ
    for key, value in process_env.items():
        if value is not None:
            values[key] = value
    return values


def assess_test_mode(env_file: Path, cfg: dict[str, str]) -> list[str]:
    """Sprawdza, czy konfiguracja jednoznacznie wskazuje na tryb testowy."""
    issues: list[str] = []
    if env_file.name != ".env.test":
        issues.append(
            f"Uruchomiono preflight na niestandardowym pliku środowiskowym: {env_file.name}."
        )
    if cfg.get("PBX_HOST") == PRODUCTION_PBX_HOST:
        issues.append("PBX_HOST wskazuje na produkcyjną centralę 192.168.0.11.")
    if cfg.get("PGHOST") == PRODUCTION_DB_HOST:
        issues.append("PGHOST wskazuje na produkcyjną bazę PostgreSQL 192.168.0.8.")
    if cfg.get("FB_HOST") == PRODUCTION_DB_HOST:
        issues.append("FB_HOST wskazuje na produkcyjną bazę Firebird 192.168.0.8.")
    if cfg.get("FB_V_HOST") == PRODUCTION_DB_HOST:
        issues.append("FB_V_HOST wskazuje na produkcyjną bazę Firebird v-maintenance 192.168.0.8.")
    if cfg.get("PGDATABASE") != LOCAL_TEST_DATABASE:
        issues.append(
            f"PGDATABASE ma wartość {cfg.get('PGDATABASE') or '<brak>'}, oczekiwano {LOCAL_TEST_DATABASE}."
        )
    if str(cfg.get("SMS_TEST_MODE", "")).strip().lower() != "true":
        issues.append("SMS_TEST_MODE nie ma wartości true.")
    return issues


def _to_int(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def probe_tmux(session_name: str) -> tuple[bool, list[str], str | None]:
    """Sprawdza, czy działa oczekiwana sesja tmux i zwraca listę okien."""
    if shutil_which("tmux") is None:
        return False, [], "Brak polecenia tmux w PATH."
    result = subprocess.run(
        ["tmux", "list-windows", "-t", session_name, "-F", "#{window_name}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return False, [], f"Sesja tmux {session_name} nie działa."
    windows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return True, windows, None


def probe_tcp(host: str, port: int, *, timeout: float = 1.5) -> tuple[bool, str | None]:
    """Sprawdza otwarcie gniazda TCP."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, None
    except OSError as exc:
        return False, str(exc)


def probe_postgres(cfg: dict[str, str]) -> tuple[bool, str | None]:
    """Wykonuje test połączenia z lokalną bazą PostgreSQL."""
    try:
        with psycopg.connect(
            host=cfg.get("PGHOST", "127.0.0.1"),
            port=_to_int(cfg.get("PGPORT"), 5432),
            dbname=cfg.get("PGDATABASE", LOCAL_TEST_DATABASE),
            user=cfg.get("PGUSER", LOCAL_TEST_DATABASE),
            password=cfg.get("PGPASSWORD", LOCAL_TEST_DATABASE),
            sslmode=cfg.get("PGSSLMODE", "disable"),
            connect_timeout=2,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True, None
    except Exception as exc:  # pragma: no cover - zależne od środowiska
        return False, str(exc)


def probe_health(app_port: int) -> tuple[bool, str | None]:
    """Sprawdza lokalny endpoint /health aplikacji WWW."""
    url = f"http://127.0.0.1:{app_port}/health"
    try:
        with urlopen(url, timeout=2) as response:  # noqa: S310 - lokalny endpoint
            body = json.loads(response.read().decode("utf-8"))
        if body.get("status") != "ok":
            return False, f"Endpoint /health zwrócił status {body!r}."
        return True, None
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, str(exc)


def run_preflight(
    env_file: Path,
    *,
    session_name: str = DEFAULT_SESSION_NAME,
    app_port: int = DEFAULT_APP_PORT,
    environ: dict[str, str] | None = None,
) -> PreflightResult:
    """Wykonuje pełną kontrolę środowiska testowego."""
    cfg = load_effective_env(env_file, environ=environ)
    result = PreflightResult(env_file=env_file)
    result.test_mode_issues.extend(assess_test_mode(env_file, cfg))

    tmux_ok, windows, tmux_error = probe_tmux(session_name)
    result.tmux_windows = windows
    if tmux_ok:
        expected = {"mock-ctip", "collector", "uvicorn", "sms-sender"}
        missing = sorted(expected.difference(windows))
        if missing:
            result.service_issues.append(
                f"Sesja tmux {session_name} działa, ale brakuje okien: {', '.join(missing)}."
            )
    elif tmux_error:
        result.notes.append(tmux_error)

    postgres_ok, postgres_error = probe_postgres(cfg)
    result.postgres_ok = postgres_ok
    if not postgres_ok and postgres_error:
        result.service_issues.append(f"Brak połączenia z PostgreSQL: {postgres_error}")

    health_ok, health_error = probe_health(app_port)
    result.health_ok = health_ok
    if not health_ok and health_error:
        result.service_issues.append(f"Aplikacja WWW nie odpowiada na /health: {health_error}")

    pbx_host = cfg.get("PBX_HOST", "127.0.0.1")
    pbx_port = _to_int(cfg.get("PBX_PORT"), 5525)
    pbx_ok, pbx_error = probe_tcp(pbx_host, pbx_port)
    result.pbx_ok = pbx_ok
    if not pbx_ok and pbx_error:
        result.service_issues.append(
            f"Mock CTIP albo testowa centrala nie nasłuchuje na {pbx_host}:{pbx_port}: {pbx_error}"
        )

    return result


def print_result(result: PreflightResult) -> None:
    """Wypisuje czytelny raport tekstowy na STDOUT."""
    print(f"[preflight] Plik środowiskowy: {result.env_file}")
    if result.tmux_windows:
        print(f"[preflight] Okna tmux: {', '.join(result.tmux_windows)}")

    for note in result.notes:
        print(f"[preflight] Uwaga: {note}")
    for issue in result.test_mode_issues:
        print(f"[preflight] Niejasność trybu testowego: {issue}")
    for issue in result.service_issues:
        print(f"[preflight] Problem modułu: {issue}")

    if result.status == "ready":
        print("[preflight] System testowy jest uruchomiony.")
    elif result.status == "stopped":
        print("[preflight] System testowy nie jest w pełni uruchomiony.")
    else:
        print("[preflight] Wykryto niejasności w konfiguracji trybu testowego.")


def shutil_which(binary: str) -> str | None:
    """Lokalny wrapper na shutil.which bez importu globalnego."""
    from shutil import which

    return which(binary)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parsuje argumenty linii poleceń."""
    parser = argparse.ArgumentParser(description="Kontrola lokalnego środowiska testowego CTIP.")
    parser.add_argument("--env-file", default=".env.test", help="Ścieżka do pliku dotenv.")
    parser.add_argument("--session-name", default=DEFAULT_SESSION_NAME, help="Nazwa sesji tmux.")
    parser.add_argument(
        "--app-port",
        type=int,
        default=int(os.getenv("TEST_UVICORN_PORT", DEFAULT_APP_PORT)),
        help="Port lokalnego uvicorn.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Uruchamia preflight i zwraca odpowiedni kod zakończenia."""
    args = parse_args(argv)
    env_file = Path(args.env_file)
    if not env_file.exists():
        print(f"[preflight] Brak pliku środowiskowego: {env_file}", file=sys.stderr)
        return 2

    result = run_preflight(
        env_file,
        session_name=args.session_name,
        app_port=args.app_port,
    )
    print_result(result)
    if result.status == "ready":
        return 0
    if result.status == "stopped":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
