"""Oddzielny proces ORBIT: nocny odczyt MS oraz projekcja co pięć minut."""

import argparse
import json
import logging
import time
from pathlib import Path

from sqlalchemy import create_engine, select

from app.core.config import SETTINGS_ENV_FILE, settings
from app.services import orbit
from app.services.telemetry.config import check_test_host
from app.telemetry_worker import DailyLog, scheduled_slot, worker_lock

logger = logging.getLogger(__name__)


def validate():
    """Blokuje domyślnie moduł i dostęp testowego procesu do produkcyjnych baz."""
    if not settings.shipping_orbit_enabled:
        raise ValueError("orbit_disabled")
    if settings.is_test_runtime:
        if settings.pg_database != "ctip_test" or not settings.sms_test_mode:
            raise ValueError("unsafe_test_database")
        check_test_host(settings.pg_host)
        check_test_host(settings.fb_host)
    elif Path(SETTINGS_ENV_FILE).name != ".env":
        raise ValueError("production_requires_env")


def cycle(engine, *, import_ms=False, dry_run=False, machine_ids=None, project_only=False):
    """Odczyt kontrolny nie zapisuje nawet statusu; zapis jest atomowy po pełnym odczycie."""
    from app.services.orbit_source import load_orbit_snapshot

    if dry_run:
        snapshot = load_orbit_snapshot(only_machine_ids=machine_ids)
        return {
            "status": "dry_run",
            "devices": len(snapshot["devices"]),
            "facts": len(snapshot["facts"]),
        }
    with worker_lock(engine) as acquired:
        if not acquired:
            return {"status": "already_running"}
        slot = scheduled_slot()
        run_name = "ms" if machine_ids is None else "ms_pilot"
        device_ids = (
            [f"ms:{identifier}" for identifier in machine_ids] if machine_ids is not None else None
        )
        with engine.connect() as connection:
            known = list(connection.execute(select(orbit.DEVICES.c.ms_machine_id)).scalars())
            last = (
                connection.execute(select(orbit.RUNS).where(orbit.RUNS.c.name == "ms"))
                .mappings()
                .first()
            )
        due = not project_only and (
            import_ms
            or last is None
            or last["status"] != "ok"
            or last["counts"].get("slot", "") < slot
        )
        snapshot, source_error = None, None
        if due:
            try:
                snapshot = load_orbit_snapshot(machine_ids=known, only_machine_ids=machine_ids)
            except Exception as error:
                source_error = type(error).__name__
                logger.warning("MS niedostępny; zachowano poprzednie dane: %s", source_error)
        with engine.begin() as connection:
            if snapshot is not None:
                try:
                    with connection.begin_nested():
                        result = orbit.ingest_snapshot(connection, snapshot)
                        orbit.put(
                            connection,
                            orbit.RUNS,
                            {"name": run_name},
                            dict(
                                finished_at=orbit.utcnow(),
                                status="ok",
                                counts={
                                    **result,
                                    "slot": slot if machine_ids is None else "",
                                    "pilot": machine_ids is not None,
                                },
                                error_code=None,
                            ),
                        )
                except Exception as error:
                    source_error = type(error).__name__
                    logger.warning("Odrzucono niepoprawny import MS: %s", source_error)
            if source_error:
                orbit.put(
                    connection,
                    orbit.RUNS,
                    {"name": run_name},
                    dict(
                        finished_at=orbit.utcnow(),
                        status="error",
                        counts=dict(last["counts"]) if last else {},
                        error_code=source_error,
                    ),
                )
            orbit.ingest_shipments(connection, device_ids=device_ids)
            result = orbit.project(
                connection,
                device_ids=device_ids,
            )
            orbit.put(
                connection,
                orbit.RUNS,
                {"name": "worker"},
                dict(
                    finished_at=orbit.utcnow(),
                    status="partial" if source_error else "ok",
                    counts=result,
                    error_code=source_error,
                ),
            )
            return {"status": "partial" if source_error else "ok", **result}


def main(argv=None):
    """Wykonuje pilotaż, jednorazowy przebieg albo niezależną pętlę harmonogramu."""
    parser = argparse.ArgumentParser(description="Raporty urządzeń KP ORBIT")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument(
        "--import-ms", action="store_true", help="Wymusza pełną historię MS objętej floty"
    )
    parser.add_argument("--dry-run", action="store_true", help="Nie zapisuje zmian")
    parser.add_argument(
        "--project-only",
        action="store_true",
        help="Odświeża wyłącznie projekcję i Shipping, bez importu MS",
    )
    parser.add_argument(
        "--machine-id", action="append", type=int, help="Identyfikator urządzenia do pilotażu"
    )
    args = parser.parse_args(argv)
    if args.machine_id and (args.loop or not args.import_ms):
        parser.error("Pilotaż wymaga --once --import-ms.")
    if args.dry_run and args.loop:
        parser.error("Tryb kontrolny wymaga --once.")
    if args.project_only and (args.import_ms or args.dry_run or args.machine_id):
        parser.error(
            "Tryb projekcji nie łączy się z importem, pilotażem ani odczytem kontrolnym MS."
        )
    try:
        validate()
    except ValueError as error:
        print(json.dumps({"status": "configuration_error", "code": str(error)}))
        return 2
    handlers = [logging.StreamHandler()]
    if not args.dry_run:
        directory = Path(__file__).resolve().parents[1] / "docs" / "LOG"
        directory.mkdir(parents=True, exist_ok=True)
        handlers.append(DailyLog(directory))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s ORBIT %(message)s", handlers=handlers
    )
    engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
    try:
        while True:
            try:
                result = cycle(
                    engine,
                    import_ms=args.import_ms,
                    dry_run=args.dry_run,
                    machine_ids=args.machine_id,
                    project_only=args.project_only,
                )
            except Exception as error:
                result = {"status": "error", "code": type(error).__name__}
                logger.error("Przebieg nieudany: %s", type(error).__name__)
                if not args.dry_run:
                    with engine.begin() as connection:
                        orbit.put(
                            connection,
                            orbit.RUNS,
                            {"name": "worker"},
                            dict(
                                finished_at=orbit.utcnow(),
                                status="error",
                                counts={},
                                error_code=type(error).__name__,
                            ),
                        )
            logger.info("Wynik: %s", json.dumps(result))
            if args.once:
                if result["status"] == "already_running":
                    return 3
                return 1 if result["status"] in {"error", "partial"} else 0
            args.import_ms = False
            time.sleep(300)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
