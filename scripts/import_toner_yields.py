"""Kontrolowany import lokalnego pakietu badań wydajności, domyślnie bez zapisu."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run(args):
    """Importuje pakiet w jednej transakcji; brak --apply oznacza rollback."""
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal, engine
    from app.services.toner_yields import import_yields

    if (settings.ctip_runtime_profile == "production") != args.production:
        raise ValueError("Profil środowiska nie odpowiada przełącznikowi --production.")
    document = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        async with AsyncSessionLocal() as session:
            counts = await import_yields(session, document)
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
        return {"mode": "apply" if args.apply else "dry-run", **counts}
    finally:
        await engine.dispose()


def main():
    """Wczytuje jawny plik środowiskowy i nie wypisuje sekretów przy błędzie."""
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.test")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("Nie znaleziono wskazanego pliku środowiskowego.")
    if args.env_file.name == ".env" and not args.production:
        parser.error("Środowisko produkcyjne wymaga --production.")
    os.environ["CTIP_ENV_FILE"] = str(args.env_file.resolve())
    load_dotenv(args.env_file, override=True)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))
    except Exception as error:
        print(f"Import nie został zatwierdzony: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
