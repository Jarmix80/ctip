"""Samodzielny worker urządzeń z kontrolowaną archiwizacją, bez harmonogramów WWW."""

import argparse
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.services.telemetry.config import TelemetrySettings, validate_runtime
from app.services.telemetry.runner import ImportRunner
from app.services.telemetry.store import TelemetryStore

LOCK_ID = 804320260911


class DailyLog(logging.Handler):
    """Zmienia plik o północy również podczas długiego pobrania historii."""

    def __init__(self, directory):
        super().__init__()
        self.directory = directory
        self.current = None
        self.destination = None

    def emit(self, record):
        day = datetime.now(ZoneInfo("Europe/Warsaw")).date().isoformat()
        if day != self.current:
            if self.destination:
                self.destination.close()
            self.destination = logging.FileHandler(
                self.directory / f"telemetry_{day}.log", encoding="utf-8"
            )
            self.destination.setFormatter(self.formatter)
            self.current = day
        self.destination.emit(record)

    def close(self):
        if self.destination:
            self.destination.close()
        super().close()


def scheduled_slot(now=None):
    """Zwraca ostatni należny przebieg o 23:55 czasu polskiego."""
    now = now or datetime.now(ZoneInfo("Europe/Warsaw"))
    now = now.astimezone(ZoneInfo("Europe/Warsaw"))
    day = now.date() if (now.hour, now.minute) >= (23, 55) else now.date() - timedelta(days=1)
    return day.isoformat()


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
        "--drain", action="store_true", help="Wszystkie zaległe porcje bez rozszerzania zakresu"
    )
    parser.add_argument(
        "--scheduled", action="store_true", help="Wykonaj wyłącznie zaległy przebieg dzienny"
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=[
            "remote_dplac",
            "remote_toner",
            "remote_all_supplies",
            "remote_reporting",
            "remote_mail",
            "vmaintenance",
            "printradar",
            "ms_cpc",
            "ms_identity",
        ],
        help="Wybrane źródło; opcję można powtórzyć",
    )
    parser.add_argument(
        "--limit", type=int, help="Limit plików, wiadomości lub stron baz w pilocie"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Odczyt bez bazy docelowej i zmian plików"
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("Limit musi być dodatni.")
    if args.scheduled and (args.source or args.limit or args.backfill):
        parser.error("Harmonogram wymaga kompletnego, nieograniczonego przebiegu.")
    config = TelemetrySettings()
    if settings.shipping_orbit_enabled:
        config.ms_cpc_enabled = False
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
        handlers.append(DailyLog(directory))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers
    )
    options = {
        "backfill": args.backfill,
        "selected": args.source,
        "item_limit": args.limit,
        "drain": args.drain or args.scheduled,
    }
    if args.dry_run:
        summary = ImportRunner(config, dry_run=True, **options).run()
    else:
        engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
        try:
            with worker_lock(engine) as acquired:
                if not acquired:
                    print(json.dumps({"status": "already_running"}))
                    return 3
                control, slot = None, scheduled_slot()
                if args.scheduled:
                    with engine.begin() as connection:
                        control = TelemetryStore(connection).ensure_source("scheduler", "control")
                    if control["checkpoint"].get("completed_slot", "") >= slot:
                        print(json.dumps({"status": "already_completed", "slot": slot}))
                        return 0
                summary = ImportRunner(config, engine, **options).run()
                if control and not any(value.get("errors") for value in summary.values()):
                    with engine.begin() as connection:
                        TelemetryStore(connection).checkpoint(
                            control["id"], {"completed_slot": slot}
                        )
        finally:
            engine.dispose()
    logging.info("Wynik importu: %s", json.dumps(summary, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if any(value.get("errors") for value in summary.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
