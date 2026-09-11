"""Samodzielny worker danych urządzeń, bez harmonogramów WWW i zapisów do źródeł."""

import argparse
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.services.telemetry.config import TelemetrySettings, validate_runtime
from app.services.telemetry.runner import ImportRunner

LOCK_ID = 804320260911


@contextmanager
def worker_lock(engine):
    """Utrzymuje blokadę sesyjną PostgreSQL przez cały przebieg, również między transakcjami."""
    with engine.connect() as connection:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID}
        ).scalar()
        connection.commit()
        try:
            yield bool(acquired)
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_ID})
                connection.commit()


def main(argv=None) -> int:
    """Wykonuje pojedynczy przebieg; harmonogram pozostaje zadaniem systemowym."""
    parser = argparse.ArgumentParser(description="Pozyskiwanie danych urządzeń CTIP")
    parser.add_argument("--once", action="store_true", help="Pojedynczy przebieg")
    parser.add_argument("--backfill", action="store_true", help="Pełny przegląd dostępnej historii")
    parser.add_argument(
        "--dry-run", action="store_true", help="Odczyt bez bazy docelowej i zmian plików"
    )
    args = parser.parse_args(argv)
    config = TelemetrySettings()
    try:
        validate_runtime(config)
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "code": str(error) if isinstance(error, ValueError) else type(error).__name__,
                }
            )
        )
        return 2
    handlers = [logging.StreamHandler()]
    if not args.dry_run:
        directory = Path(__file__).resolve().parents[1] / "docs" / "LOG"
        directory.mkdir(exist_ok=True, parents=True)
        handlers.append(
            logging.FileHandler(
                directory / f"telemetry_{datetime.now():%Y-%m-%d}.log", encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers
    )
    if args.dry_run:
        summary = ImportRunner(config, dry_run=True, backfill=args.backfill).run()
    else:
        engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
        try:
            with worker_lock(engine) as acquired:
                if not acquired:
                    print(json.dumps({"status": "already_running"}))
                    return 0
                summary = ImportRunner(config, engine, backfill=args.backfill).run()
        finally:
            engine.dispose()
    logging.info("Wynik importu: %s", json.dumps(summary, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if any(value.get("errors") for value in summary.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
