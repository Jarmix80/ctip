"""Bezpieczny orkiestrator wdrożenia CTIP na produkcyjnym serwerze Windows."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
POWERSHELL_ENCODED_COMMAND_LIMIT = 6000
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)\b([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY)[A-Z0-9_]*)\s*=\s*([^\s;]+)"
)


@dataclass(frozen=True)
class CommandResult:
    """Wynik polecenia bez założenia, że stderr oznacza błąd."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Kontrakt wykonawcy poleceń używany przez testy jednostkowe."""

    def __call__(self, command: Sequence[str], *, cwd: Path | None = None) -> CommandResult: ...


def decode_windows_output(payload: bytes) -> str:
    """Dekoduje odpowiedź Windows jako UTF-8 albo CP852 z bezpiecznym fallbackiem."""
    for encoding in ("utf-8", "cp852"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def subprocess_runner(command: Sequence[str], *, cwd: Path | None = None) -> CommandResult:
    """Uruchamia polecenie i dekoduje wyjście zgodnie z kodowaniem hosta Windows."""
    completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True)
    return CommandResult(
        returncode=completed.returncode,
        stdout=decode_windows_output(completed.stdout),
        stderr=decode_windows_output(completed.stderr),
    )


def read_exact_env_value(path: Path, key: str) -> str:
    """Odczytuje jeden klucz bez parsowania pozostałej zawartości pliku `.env`."""
    key_pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=(.*)$")
    matches: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = key_pattern.match(line)
        if match:
            matches.append(match.group(1).strip())
    if not matches or not matches[-1]:
        raise ValueError(f"Brak klucza {key} w {path}.")
    return matches[-1]


def parse_ssh_command(raw_value: str) -> list[str]:
    """Dzieli zapis SSH, pozostawiając obsługę cytowanych argumentów funkcji `shlex`."""
    command = shlex.split(raw_value, posix=True)
    if not command or Path(command[0]).name.lower() not in {"ssh", "ssh.exe"}:
        raise ValueError("ssh_serv_link musi rozpoczynać się poleceniem ssh.")
    if any("\n" in item or "\r" in item for item in command):
        raise ValueError("ssh_serv_link zawiera niedozwolony znak nowej linii.")
    return command


def redact_text(value: str, secrets: Sequence[str] = ()) -> str:
    """Usuwa z raportu wartości sekretów i typowe przypisania poufne."""
    redacted = SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<ukryte>", value)
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        redacted = redacted.replace(secret, "<ukryte>")
    return redacted


def normalize_text_bytes(payload: bytes) -> bytes:
    """Normalizuje BOM i końce linii przed porównaniem hashy Linux/Windows."""
    text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return f"{text.rstrip(chr(10))}\n".encode()


def normalized_sha256(payload: bytes) -> str:
    """Zwraca SHA-256 tekstu po kontrolowanej normalizacji końców linii."""
    return hashlib.sha256(normalize_text_bytes(payload)).hexdigest()


def encode_powershell(command: str, *, limit: int = POWERSHELL_ENCODED_COMMAND_LIMIT) -> str:
    """Koduje wyłącznie krótkie polecenie kontrolne i blokuje nadmierny transport."""
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    if len(encoded) > limit:
        raise ValueError(
            "Polecenie PowerShell jest zbyt długie dla EncodedCommand; użyj pliku `.ps1`."
        )
    return encoded


def command_succeeded(result: CommandResult) -> bool:
    """Ocenia polecenie natywne po kodzie wyjścia, nie po obecności stderr."""
    return result.returncode == 0


def merge_environment(dotenv: dict[str, str], nssm: dict[str, str]) -> dict[str, str]:
    """Łączy konfigurację tak, aby NSSM zawsze nadpisywał plik `.env`."""
    return {**dotenv, **nssm}


def log_since_current_start(
    lines: Sequence[str],
    *,
    process_started_at: datetime | None = None,
) -> list[str]:
    """Odcina historyczne błędy przed ostatnim startem bieżącego procesu."""
    marker_index = -1
    for index, line in enumerate(lines):
        if "Started server process" in line:
            marker_index = index
    if marker_index >= 0:
        return list(lines[marker_index:])
    if process_started_at is None:
        return list(lines)
    current: list[str] = []
    for line in lines:
        match = re.match(r"^\[?(\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+)", line)
        if not match:
            continue
        try:
            timestamp = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        except ValueError:
            continue
        comparison_start = process_started_at
        if timestamp.tzinfo is None and comparison_start.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=comparison_start.tzinfo)
        elif timestamp.tzinfo is not None and comparison_start.tzinfo is None:
            comparison_start = comparison_start.replace(tzinfo=timestamp.tzinfo)
        if timestamp >= comparison_start:
            current.append(line)
    return current


def path_is_allowed(path: str, allowed_paths: Sequence[str]) -> bool:
    """Sprawdza, czy ścieżka wydania mieści się w jednym z prefiksów planu."""
    normalized = PurePosixPath(path).as_posix().lstrip("./")
    for allowed in allowed_paths:
        prefix = PurePosixPath(allowed).as_posix().lstrip("./").rstrip("/")
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True
    return False


def _require_success(result: CommandResult, label: str) -> str:
    if not command_succeeded(result):
        details = redact_text(result.stderr or result.stdout)
        raise RuntimeError(f"{label} zakończyło się kodem {result.returncode}: {details}")
    return result.stdout


def _git_output(runner: CommandRunner, arguments: Sequence[str]) -> str:
    result = runner(["git", *arguments], cwd=PROJECT_ROOT)
    return _require_success(result, f"git {' '.join(arguments)}").strip()


def validate_release(
    *,
    runner: CommandRunner,
    expected_current: str,
    release: str,
    allowed_paths: Sequence[str],
) -> list[str]:
    """Sprawdza lokalnie pełne SHA, pochodzenie commita i zakres plików."""
    for label, revision in (("expected-current", expected_current), ("release", release)):
        if not FULL_SHA_RE.fullmatch(revision):
            raise ValueError(f"{label} musi być pełnym SHA-1 commita.")
        _git_output(runner, ["cat-file", "-e", f"{revision}^{{commit}}"])
    ancestor = runner(
        ["git", "merge-base", "--is-ancestor", expected_current, release], cwd=PROJECT_ROOT
    )
    if ancestor.returncode != 0:
        raise ValueError("Commit release nie jest potomkiem bieżącego commita produkcji.")
    changed_output = _git_output(
        runner,
        ["diff", "--name-only", f"{expected_current}..{release}"],
    )
    changed_paths = [line for line in changed_output.splitlines() if line]
    rejected = [path for path in changed_paths if not path_is_allowed(path, allowed_paths)]
    if rejected:
        raise ValueError(f"Release zmienia niedozwolone ścieżki: {', '.join(rejected)}")
    return changed_paths


def _powershell_quoted(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _ssh_powershell_command(script: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encode_powershell(script),
    ]


def run_remote_powershell(
    runner: CommandRunner,
    ssh_command: Sequence[str],
    script: str,
) -> CommandResult:
    """Uruchamia krótkie polecenie PowerShell przez SSH."""
    return runner([*ssh_command, *_ssh_powershell_command(script)])


def git_blob(runner: CommandRunner, revision: str, path: str) -> bytes:
    """Pobiera plik tekstowy z konkretnego commita do kontroli integralności."""
    result = runner(["git", "show", f"{revision}:{path}"], cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"Nie można odczytać {path} z commita {revision}.")
    return result.stdout.encode("utf-8")


def materialize_remote_file_script(
    *,
    install_dir: str,
    revision: str,
    repository_path: str,
    destination: str,
    expected_hash: str,
) -> str:
    """Buduje krótki skrypt pobierający plik przez `git show` i sprawdzający SHA-256."""
    return (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
        "$OutputEncoding=[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);"
        f"$repo={_powershell_quoted(install_dir)};$dst={_powershell_quoted(destination)};"
        f"$spec={_powershell_quoted(f'{revision}:{repository_path}')};"
        "$lines=@(& git -C $repo show $spec);if($LASTEXITCODE-ne 0){throw 'git show failed'};"
        '$text=(($lines-join "`n").TrimEnd("`n")+"`n");'
        "$dir=Split-Path -Parent $dst;New-Item -ItemType Directory -Path $dir -Force|Out-Null;"
        "$utf8=New-Object Text.UTF8Encoding($false);[IO.File]::WriteAllText($dst,$text,$utf8);"
        "$hasher=[Security.Cryptography.SHA256]::Create();try{$hash=$hasher.ComputeHash($utf8.GetBytes($text))}finally{$hasher.Dispose()};"
        "$sha=[BitConverter]::ToString($hash).Replace('-','').ToLower();"
        f"if($sha-ne {_powershell_quoted(expected_hash)}){{throw 'SHA-256 mismatch'}}"
    )


def _parse_endpoint(value: str) -> dict[str, object]:
    parts = value.split("|", maxsplit=2)
    if len(parts) not in {2, 3}:
        raise argparse.ArgumentTypeError("Endpoint ma format etykieta|URL[|kod].")
    return {"label": parts[0], "url": parts[1], "status": int(parts[2]) if len(parts) == 3 else 200}


def build_parser() -> argparse.ArgumentParser:
    """Buduje interfejs polecenia wdrożeniowego."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--alembic-before", required=True)
    parser.add_argument("--alembic-after", required=True)
    parser.add_argument("--allowed-path", action="append", required=True)
    parser.add_argument("--service", action="append", default=["CTIP-Web"])
    parser.add_argument(
        "--endpoint",
        action="append",
        type=_parse_endpoint,
        default=[{"label": "CTIP health", "url": "http://127.0.0.1:8000/health", "status": 200}],
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--install-dir", default=r"D:\CTIP")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser


def _remote_destination(ssh_command: Sequence[str], runner: CommandRunner) -> str:
    result = runner([ssh_command[0], "-G", *ssh_command[1:]])
    output = _require_success(result, "ssh -G")
    for line in output.splitlines():
        key, _, value = line.partition(" ")
        if key.lower() == "hostname" and value.strip():
            return value.strip()
    raise RuntimeError("Nie udało się ustalić hosta docelowego SSH.")


def _route_report(host: str, runner: CommandRunner) -> str:
    result = runner(["ip", "route", "get", host])
    return _require_success(result, "Kontrola trasy do serwera").strip()


def execute(
    args: argparse.Namespace, *, runner: CommandRunner = subprocess_runner
) -> dict[str, object]:
    """Wykonuje kontrolę lub wdrożenie z raportem pozbawionym sekretów."""
    changed_paths = validate_release(
        runner=runner,
        expected_current=args.expected_current,
        release=args.release,
        allowed_paths=args.allowed_path,
    )
    raw_ssh = read_exact_env_value(args.env_file, "ssh_serv_link")
    ssh_command = parse_ssh_command(raw_ssh)
    host = _remote_destination(ssh_command, runner)
    route = _route_report(host, runner)

    remote_preflight = (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
        f"$repo={_powershell_quoted(args.install_dir)};"
        "$head=(& git -C $repo rev-parse HEAD).Trim();if($LASTEXITCODE-ne 0){throw 'git rev-parse failed'};"
        "$dirty=@(& git -C $repo status --porcelain);if($LASTEXITCODE-ne 0){throw 'git status failed'};"
        "[pscustomobject]@{head=$head;clean=($dirty.Count-eq 0)}|ConvertTo-Json -Compress"
    )
    preflight = run_remote_powershell(runner, ssh_command, remote_preflight)
    remote_state = json.loads(_require_success(preflight, "Zdalny preflight").strip())
    if remote_state.get("head") != args.expected_current or not remote_state.get("clean"):
        raise RuntimeError("Zdalna produkcja ma nieoczekiwany HEAD albo lokalne zmiany.")

    plan = {
        "expected_current": args.expected_current,
        "release": args.release,
        "alembic_before": args.alembic_before,
        "alembic_after": args.alembic_after,
        "allowed_paths": args.allowed_path,
        "services": list(dict.fromkeys(args.service)),
        "endpoints": args.endpoint,
    }
    plan_base64 = base64.b64encode(json.dumps(plan, separators=(",", ":")).encode()).decode()
    remote_dir = rf"{args.install_dir}\.deploy\ctip-release-{args.release[:12]}"
    remote_module = rf"{remote_dir}\CtipDeployment.Common.psm1"
    remote_script = rf"{remote_dir}\deploy_windows_release.ps1"
    files = (
        ("scripts/windows/CtipDeployment.Common.psm1", remote_module),
        ("scripts/windows/deploy_windows_release.ps1", remote_script),
    )
    try:
        for repository_path, destination in files:
            payload = git_blob(runner, args.release, repository_path)
            materialize = materialize_remote_file_script(
                install_dir=args.install_dir,
                revision=args.release,
                repository_path=repository_path,
                destination=destination,
                expected_hash=normalized_sha256(payload),
            )
            _require_success(
                run_remote_powershell(runner, ssh_command, materialize),
                f"Materializacja {repository_path}",
            )

        invocation = [
            *ssh_command,
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            remote_script,
            "-InstallDir",
            args.install_dir,
            "-PlanJsonBase64",
            plan_base64,
        ]
        if args.apply:
            invocation.append("-Apply")
        deployment = runner(invocation)
        output = _require_success(deployment, "Wdrożenie Windows")
    finally:
        cleanup = f"Remove-Item -LiteralPath {_powershell_quoted(remote_dir)} -Recurse -Force -ErrorAction SilentlyContinue"
        run_remote_powershell(runner, ssh_command, cleanup)

    return {
        "mode": "apply" if args.apply else "dry-run",
        "release": args.release,
        "expected_current": args.expected_current,
        "changed_paths": changed_paths,
        "route": redact_text(route),
        "remote_output": redact_text(output),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Uruchamia wdrożenie i drukuje krótki raport JSON bez sekretów."""
    args = build_parser().parse_args(argv)
    try:
        report = execute(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[BŁĄD] {redact_text(str(exc))}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
