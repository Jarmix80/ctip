"""
Diagnostyka pywin32 dla środowiska .venv na Windows.

Uruchom w katalogu instalacyjnym (np. D:\CTIP) po aktywacji .venv:
    .\.venv\Scripts\python.exe scripts\windows\pywin_check.py

Skrypt wypisuje ścieżki pywin32 i próbuje załadować DLL-e oraz moduły win32api/win32serviceutil.
"""

from __future__ import annotations

import ctypes
import os
import site
import sys
from pathlib import Path


def main() -> int:
    base = Path(site.getsitepackages()[0])
    paths = [
        base / "pywin32_system32",
        base / "win32",
        base / "pythonwin",
    ]

    print(f"python: {sys.executable}")
    print(f"site-packages: {base}")
    print("paths:", paths)

    for p in paths:
        if p.is_dir():
            try:
                os.add_dll_directory(p)
                print(f"add_dll_directory OK: {p}")
            except Exception as exc:  # pragma: no cover - diagnostyka
                print(f"add_dll_directory FAIL: {p} -> {exc}")
        else:
            print(f"missing path: {p}")

    print("Load DLLs via ctypes...")
    try:
        ctypes.WinDLL(base / "pywin32_system32" / "pywintypes311.dll")
        ctypes.WinDLL(base / "pywin32_system32" / "pythoncom311.dll")
        print("DLL load OK")
    except Exception as exc:  # pragma: no cover - diagnostyka
        print(f"DLL load FAILED: {exc}")
        return 1

    try:
        import win32api  # type: ignore
    except Exception as exc:  # pragma: no cover - diagnostyka
        print(f"Import FAILED: {exc}")
        return 2

    print(f"win32api: {win32api.__file__}")  # type: ignore
    print("pywin32 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
