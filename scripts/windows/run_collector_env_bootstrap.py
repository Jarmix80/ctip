"""Bootstrap kolektora CTIP z jawnie wczytanym plikiem `.env`."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Wczytuje prosty plik `.env` i zwraca mapę kompletnych wpisów."""

    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def resolve_paths() -> tuple[Path, Path]:
    """Ustala katalog repozytorium oraz ścieżkę do `.env`."""

    repo_root = Path(__file__).resolve().parents[2]
    env_file = Path(os.environ.get("CTIP_ENV_FILE") or repo_root / ".env")
    return repo_root, env_file


def ensure_repo_on_syspath(repo_root: Path) -> None:
    """Dopina katalog repozytorium do `sys.path` dla importów kolektora."""

    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def main() -> None:
    """Ładuje `.env` i uruchamia `collector_full.py` jako główny program."""

    repo_root, env_file = resolve_paths()
    os.environ.update(load_env_file(env_file))
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    ensure_repo_on_syspath(repo_root)

    collector_path = repo_root / "collector_full.py"
    os.chdir(repo_root)
    sys.argv = [str(collector_path), *sys.argv[1:]]
    runpy.run_path(str(collector_path), run_name="__main__")


if __name__ == "__main__":
    main()
