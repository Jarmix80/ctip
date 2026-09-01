"""Testy bezpiecznego mechanizmu wdrożeń CTIP na Windows."""

from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.deploy_windows_prod import (
    CommandResult,
    command_succeeded,
    decode_windows_output,
    encode_powershell,
    execute,
    log_since_current_start,
    materialize_remote_file_script,
    merge_environment,
    normalize_text_bytes,
    parse_ssh_command,
    read_exact_env_value,
    redact_text,
)

CURRENT = "1" * 40
RELEASE = "2" * 40


def test_read_exact_env_value_ignores_nonstandard_lines(tmp_path: Path) -> None:
    """Parser odczytuje wyłącznie wskazany klucz bez ładowania całego `.env`."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "niestandardowa linia bez przypisania\n"
        'ssh_serv_link=ssh -i "/home/user/Klucz test" Administrator@serwer\n'
        "BROKEN='bez końca\n",
        encoding="utf-8",
    )

    assert read_exact_env_value(env_file, "ssh_serv_link") == (
        'ssh -i "/home/user/Klucz test" Administrator@serwer'
    )


def test_parse_ssh_command_preserves_quoted_argument() -> None:
    """Cudzysłowy są interpretowane dopiero przez `shlex.split`."""
    assert parse_ssh_command('ssh -i "/home/user/Klucz test" admin@host') == [
        "ssh",
        "-i",
        "/home/user/Klucz test",
        "admin@host",
    ]


def test_redaction_hides_assignments_and_explicit_secret() -> None:
    """Raport nie może ujawnić haseł, tokenów ani jawnie wskazanej wartości."""
    result = redact_text("PGPASSWORD=abc TOKEN_X=sekret prywatne", ["prywatne"])
    assert "abc" not in result
    assert "sekret" not in result
    assert "prywatne" not in result
    assert result.count("<ukryte>") == 3


def test_encoded_command_rejects_large_script() -> None:
    """Duży skrypt nie może zostać przesłany przez `EncodedCommand`."""
    with pytest.raises(ValueError, match="zbyt długie"):
        encode_powershell("x" * 5000, limit=100)


def test_native_command_accepts_alembic_info_on_stderr() -> None:
    """Komunikat INFO na stderr nie oznacza błędu przy kodzie zero."""
    result = CommandResult(0, "e4a8c1d9f2b7 (head)\n", "INFO  [alembic.runtime]\n")
    assert command_succeeded(result) is True


def test_nssm_environment_has_priority() -> None:
    """Działająca konfiguracja NSSM nadpisuje nieaktualny plik `.env`."""
    merged = merge_environment(
        {"PGHOST": "stary", "PGDATABASE": "ctip"},
        {"PGHOST": "127.0.0.1"},
    )
    assert merged == {"PGHOST": "127.0.0.1", "PGDATABASE": "ctip"}


def test_windows_output_supports_utf8_and_cp852() -> None:
    """Wyjście Windows jest czytelne dla UTF-8 i CP852."""
    text = "Zażółć gęślą jaźń"
    assert decode_windows_output(text.encode()) == text
    assert decode_windows_output(text.encode("cp852")) == text


def test_normalize_text_bytes_unifies_crlf_and_lf() -> None:
    """Hash pliku nie zależy od zakończeń linii ani BOM."""
    assert normalize_text_bytes(b"a\r\nb\r\n") == b"a\nb\n"
    assert normalize_text_bytes(b"\xef\xbb\xbfa\nb") == b"a\nb\n"


def test_materializacja_git_jest_binarna_i_miesci_sie_w_limicie() -> None:
    """Plik z commita nie może przechodzić przez dekodowanie konsoli Windows."""
    script = materialize_remote_file_script(
        install_dir=r"D:\CTIP",
        revision=RELEASE,
        repository_path="scripts/windows/CtipDeployment.Common.psm1",
        destination=r"D:\CTIP\.deploy\common.psm1",
        expected_hash="a" * 64,
    )

    assert "archive --format=zip" in script
    assert "Text.UTF8Encoding($false,$true)" in script
    assert '.Replace("`r`n","`n")' in script
    assert "Get-FileHash" in script
    assert "Text.UTF8Encoding($true)" in script
    assert "git show" not in script
    encode_powershell(script)


def test_log_since_current_start_ignores_historical_traceback() -> None:
    """Kontrola logu analizuje wyłącznie ostatni start serwera."""
    lines = [
        "2026-08-01T10:00:00 Traceback (most recent call last)",
        "INFO Started server process [100]",
        "INFO Application startup complete",
    ]
    assert log_since_current_start(lines) == lines[1:]

    timestamped = [
        "2026-08-01T10:00:00 Traceback",
        "2026-08-01T11:00:00 Application startup complete",
    ]
    assert log_since_current_start(
        timestamped,
        process_started_at=datetime(2026, 8, 1, 10, 30, tzinfo=UTC),
    ) == [timestamped[1]]


class DryRunRunner:
    """Kontrolowany wykonawca symulujący odczytowy przebieg wdrożenia."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.remote_encoded_calls = 0

    def __call__(self, command, *, cwd=None) -> CommandResult:
        del cwd
        command = list(command)
        self.commands.append(command)
        if command[:2] == ["git", "cat-file"]:
            return CommandResult(0, "", "")
        if command[:3] == ["git", "merge-base", "--is-ancestor"]:
            return CommandResult(0, "", "")
        if command[:3] == ["git", "diff", "--name-only"]:
            return CommandResult(0, "scripts/windows/deploy_windows_release.ps1\n", "")
        if command[:2] == ["git", "show"]:
            return CommandResult(0, "Write-Host 'test'\n", "")
        if command[0] == "ip":
            return CommandResult(0, "host via 192.168.0.1 src 192.168.0.9\n", "")
        if "-G" in command:
            return CommandResult(0, "hostname windows-prod\n", "")
        if "-EncodedCommand" in command:
            self.remote_encoded_calls += 1
            if self.remote_encoded_calls == 1:
                return CommandResult(0, f'{{"head":"{CURRENT}","clean":true}}\n', "")
            return CommandResult(0, "", "")
        if "-File" in command:
            return CommandResult(0, '{"mode":"dry-run"}\n', "INFO kontrolne\n")
        raise AssertionError(f"Nieobsłużone polecenie: {command}")


def test_dry_run_does_not_request_apply_or_fetch(tmp_path: Path) -> None:
    """Dry-run wykonuje kontrole bez żądania mutacji produkcji."""
    env_file = tmp_path / ".env"
    env_file.write_text('ssh_serv_link=ssh -i "klucz test" admin@host\n', encoding="utf-8")
    runner = DryRunRunner()
    args = argparse.Namespace(
        release=RELEASE,
        expected_current=CURRENT,
        alembic_before="e4a8c1d9f2b7",
        alembic_after="e4a8c1d9f2b7",
        allowed_path=["scripts/windows"],
        service=["CTIP-Web"],
        endpoint=[{"label": "health", "url": "http://127.0.0.1:8000/health", "status": 200}],
        env_file=env_file,
        install_dir=r"D:\CTIP",
        dry_run=True,
        apply=False,
    )

    report = execute(args, runner=runner)

    assert report["mode"] == "dry-run"
    invocation = next(command for command in runner.commands if "-File" in command)
    assert "-Apply" not in invocation
    assert not any("fetch" in command for command in runner.commands)
    encoded_scripts = [
        base64.b64decode(command[command.index("-EncodedCommand") + 1]).decode("utf-16-le")
        for command in runner.commands
        if "-EncodedCommand" in command
    ]
    assert any(r"D:\CTIP\.git\ctip-release-" in script for script in encoded_scripts)
    assert not any(r"D:\CTIP\.deploy\ctip-release-" in script for script in encoded_scripts)


def test_powershell_contains_healthcheck_rollback() -> None:
    """Błąd healthchecku musi prowadzić do powrotu na poprzedni commit i startu usług."""
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "windows" / "deploy_windows_release.ps1"
    ).read_text(encoding="utf-8")
    assert "Assert-Endpoints" in script
    assert '"checkout", "--detach", $expectedCurrent' in script
    assert "Start-Service -Name $serviceName" in script
    assert '$rollbackServices = $services -join ","' in script
    assert "Start-Service $rollbackServices" in script


def test_powershell_healthcheck_message_delimits_variable_before_colon() -> None:
    """Dwukropek po zmiennej nie może być interpretowany jako zakres PowerShell."""
    module = (
        Path(__file__).resolve().parents[1] / "scripts" / "windows" / "CtipDeployment.Common.psm1"
    ).read_text(encoding="utf-8")
    assert "${ExpectedStatus}: $Url" in module
    assert "$ExpectedStatus: $Url" not in module


def test_powershell_native_runner_does_not_promote_stderr_to_failure() -> None:
    """Stderr polecenia natywnego nie może przerwać odczytu jego kodu wyjścia."""
    module = (
        Path(__file__).resolve().parents[1] / "scripts" / "windows" / "CtipDeployment.Common.psm1"
    ).read_text(encoding="utf-8")
    assert '$ErrorActionPreference = "Continue"' in module
    assert "$nativeSucceeded = $?" in module
    assert "$exitCode = $LASTEXITCODE" in module
    assert "$ErrorActionPreference = $previousErrorActionPreference" in module


def test_legacy_update_scripts_are_blocked() -> None:
    """Historyczne aktualizatory nie mogą ominąć kanonicznego wdrożenia."""
    repository = Path(__file__).resolve().parents[1]
    for relative_path in (
        "scripts/windows/update_ctip.ps1",
        "scripts/windows/update_ctip_easy.ps1",
        "docs/instal/ctip_windows_service_package/scripts/windows/update_ctip.ps1",
    ):
        prefix = (repository / relative_path).read_text(encoding="utf-8")[:1200]
        assert "jest zablokowany" in prefix
