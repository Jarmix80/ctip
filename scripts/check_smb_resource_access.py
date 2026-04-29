#!/usr/bin/env python3
"""Test dostepu do zasobu SMB zdefiniowanego w .env.

Domyslnie skrypt:
1. laczy sie do udzialu,
2. listuje pliki,
3. probuje odczytac wskazany plik testowy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import smbclient


def _read_env_file(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    if not path.exists():
        return output
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        output[key.strip()] = value.strip()
    return output


def _pick_value(env_data: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value.strip()
    for key in keys:
        value = env_data.get(key)
        if value:
            return value.strip()
    return None


def _normalize_unc(path_value: str) -> tuple[str, str]:
    path_text = path_value.replace("/", "\\")
    if not path_text.startswith("\\\\"):
        if path_text.startswith("\\"):
            path_text = "\\" + path_text
        else:
            path_text = "\\\\" + path_text.lstrip("\\")

    match = re.match(r"^\\\\([^\\]+)\\([^\\]+)(.*)$", path_text)
    if match is None:
        raise ValueError(f"Nieprawidlowy format UNC: {path_value}")

    server = match.group(1)
    share = match.group(2)
    rest = match.group(3).strip("\\")
    unc = f"\\\\{server}\\{share}"
    if rest:
        unc += "\\" + rest
    return server, unc


def _list_entries(unc_path: str) -> list[str]:
    entries = smbclient.listdir(unc_path)
    return sorted(str(item) for item in entries)


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    env_data = _read_env_file(Path(args.env_file))
    share_path = _pick_value(env_data, "sciezka_dok_umow", "SCIEZKA_DOK_UMOW", "SMB_SHARE_PATH")
    username = _pick_value(env_data, "login_dok_umow", "LOGIN_DOK_UMOW", "SMB_USERNAME")
    password = _pick_value(env_data, "pass_dok_umow", "PASS_DOK_UMOW", "SMB_PASSWORD")

    report: dict[str, Any] = {
        "ok": False,
        "share_path": share_path,
        "username": username,
        "password_set": bool(password),
    }

    if not share_path or not username or not password:
        report["error"] = (
            "Brak wymaganych danych (sciezka_dok_umow/login_dok_umow/pass_dok_umow) "
            "w .env lub zmiennych srodowiskowych."
        )
        return 2, report

    try:
        server, unc_path = _normalize_unc(share_path)
    except ValueError as exc:
        report["error"] = str(exc)
        return 2, report

    report["normalized_unc"] = unc_path
    report["server"] = server

    try:
        smbclient.register_session(server=server, username=username, password=password, port=445)
        entries = _list_entries(unc_path)
        report["entries_count"] = len(entries)
        report["entries_preview"] = entries[:100]

        if args.read_file:
            target = args.read_file
            file_unc = unc_path.rstrip("\\") + "\\" + target
            with smbclient.open_file(
                file_unc, mode="r", encoding="utf-8", errors="replace"
            ) as handle:
                content = handle.read(args.max_chars)
            report["read_file"] = target
            report["read_ok"] = True
            report["read_content_head"] = content

        report["ok"] = True
        return 0, report
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        return 2, report
    finally:
        smbclient.reset_connection_cache()


def build_parser() -> argparse.ArgumentParser:
    """Buduje parser argumentow CLI."""
    parser = argparse.ArgumentParser(description="Test dostepu do zasobu SMB.")
    parser.add_argument("--env-file", default=".env", help="Sciezka do pliku .env.")
    parser.add_argument(
        "--read-file",
        default="test.txt",
        help="Nazwa pliku do odczytu po poprawnym polaczeniu.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=2000,
        help="Maksymalna liczba znakow odczytywanego podgladu pliku.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Opcjonalna sciezka pliku JSON z raportem.",
    )
    return parser


def main() -> int:
    """Punkt startowy CLI."""
    parser = build_parser()
    args = parser.parse_args()
    code, report = run(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
