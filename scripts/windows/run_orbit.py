"""Jawny bootstrap zadania ORBIT na produkcyjnym Windows."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main():
    """Ładuje prywatne środowisko wyłącznie procesu wcześniej zatwierdzonego zadania."""
    root = Path(__file__).resolve().parents[2]
    if os.name != "nt" or not (root / ".env").is_file():
        raise RuntimeError("Wymagany produkcyjny Windows i plik .env.")
    os.chdir(root)
    sys.path.insert(0, str(root))
    load_dotenv(root / ".env", override=True)
    os.environ["CTIP_ENV_FILE"] = str(root / ".env")
    os.environ["CTIP_RUNTIME_PROFILE"] = "production"
    from app.orbit_worker import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
