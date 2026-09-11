"""Wznawialny import źródeł oraz archiwizacja wyłącznie zatwierdzonych raportów."""

import copy
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update

from app.models import telemetry as tables
from app.services.telemetry import sources
from app.services.telemetry.billing import cpc_reading
from app.services.telemetry.csv_import import archive_committed as archive_committed
from app.services.telemetry.store import TelemetryStore

logger = logging.getLogger(__name__)


def file_signature(path: Path):
    """Pobiera parametry stabilności bez modyfikowania pliku."""
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


class ImportRunner:
    """Koordynuje źródła; awaria jednego nie zatrzymuje pozostałych."""

    def __init__(
        self,
        config,
        engine=None,
        *,
        dry_run=False,
        backfill=False,
        selected=None,
        item_limit=None,
        drain=False,
    ):
        """Przyjmuje jawny zakres źródeł i ograniczenie kontrolowanego pilota."""
        self.config = config
        self.engine = engine
        self.dry_run = dry_run
        self.backfill = backfill
        self.summary = {}
        self.selected = set(selected or [])
        self.item_limit = item_limit
        self.drain = drain
        self.active_mapping = None

    def source(self, name, kind):
        """Otwiera rejestr źródła albo jego nietrwały odpowiednik kontrolny."""
        self.summary.setdefault(name, {})
        if self.dry_run:
            return {"id": name, "name": name, "kind": kind, "checkpoint": {}}
        with self.engine.begin() as connection:
            return TelemetryStore(connection).ensure_source(name, kind)

    def failure(self, source, locator, error):
        """Zapisuje wyłącznie bezpieczny typ błędu; poświadczenia nie trafiają do logów."""
        code = (
            str(error)
            if isinstance(error, ValueError) and str(error).replace("_", "").isalnum()
            else type(error).__name__
        )
        self.summary.setdefault(source["name"], {}).setdefault("errors", []).append(code)
        logger.warning("Źródło %s: %s", source["name"], code)
        if not self.dry_run:
            with self.engine.begin() as connection:
                TelemetryStore(connection).failure(source["id"], locator, code)

    def save(self, source, locator, readings, *, checkpoint=None, **kwargs):
        """Zatwierdza porcję i jej kursor razem; tryb kontrolny tylko zlicza rekordy."""
        if self.dry_run:
            result = {"new": len(readings), "duplicates": 0}
        else:
            with self.engine.begin() as connection:
                store = TelemetryStore(connection)
                result = store.ingest(source["id"], locator, readings, **kwargs)
                if checkpoint is not None:
                    store.checkpoint(source["id"], checkpoint)
        summary = self.summary.setdefault(source["name"], {})
        for key in ("new", "duplicates"):
            summary[key] = summary.get(key, 0) + result[key]
        return result

    def csv_jobs(self):
        """Rozdziela pozostawiane DPLAC od trzech archiwizowanych katalogów raportów."""
        jobs = []
        if self.config.dplac_root:
            archive = self.config.archive_root_csv
            jobs.append(
                (
                    "remote_dplac",
                    Path(self.config.dplac_root),
                    "*.csv" if archive else "DPLAC*.csv",
                    archive,
                )
            )
        if self.config.report_root:
            for name in ("Toner", "All Supplies", "Reporting"):
                jobs.append(
                    (
                        "remote_" + name.lower().replace(" ", "_"),
                        Path(self.config.report_root) / name,
                        "*.csv",
                        True,
                    )
                )
        return [job for job in jobs if not self.selected or job[0] in self.selected]

    def run_csv(self):
        """Deleguje import katalogów i archiwów do wspólnego koordynatora CSV."""
        from app.services.telemetry.csv_import import run_csv

        return run_csv(self)

    def get_active_mapping(self):
        """Pobiera jedną aktualną mapę aktywnych umów; błędu źródła nie uznaje za pustą mapę."""
        if self.active_mapping is None:
            self.active_mapping = sources.active_identity_map(self.config)
        return self.active_mapping

    def run_mail(self):
        """Deleguje klasyfikację i kolejkę przeniesień do importera pocztowego."""
        from app.services.telemetry.mail_import import run_mail

        return run_mail(self)

    def run_database(self, kind):
        """Rozdziela miesięczne okresy MS od dziennych migawek pozostałych baz."""
        if kind != "ms_cpc":
            from app.services.telemetry.daily import run_daily

            try:
                return run_daily(self, kind)
            except Exception as error:
                self.failure(self.source(kind, "database"), kind, error)
                return None
        source = self.source(kind, "database")
        checkpoint = copy.deepcopy(source["checkpoint"])
        today = datetime.now(UTC).date().isoformat()
        identities = sources.ms_cpc_serials(self.config)
        state = checkpoint.setdefault("CPC", {})
        full = self.backfill or state.get("reconciled") != today
        after = state.get("scan") if full else state.get("watermark")
        pages = 0
        try:
            while self.backfill or self.drain or pages < self.config.max_pages:
                if self.item_limit is not None and pages >= self.item_limit:
                    break
                rows = sources.ms_cpc_page(after, self.config.page_size, self.config)
                readings = [cpc_reading(row, identities) for row in rows]
                candidate = copy.deepcopy(checkpoint)
                state = candidate.setdefault("CPC", {})
                if rows:
                    after = rows[-1]["ID_CPC_TABLE"]
                    state["watermark"] = max(after, state.get("watermark") or 0)
                if full:
                    state["scan"] = after
                if len(rows) < self.config.page_size:
                    state["scan"] = None
                    state["reconciled"] = today
                self.save(source, f"CPC/{after}", readings, checkpoint=candidate)
                checkpoint = candidate
                pages += 1
                if len(rows) < self.config.page_size:
                    break
        except Exception as error:
            self.failure(source, "CPC", error)

    def run(self):
        """Uruchamia wyłącznie skonfigurowane źródła oraz opcjonalne dopasowanie MS."""
        self.run_csv()
        for enabled, name, callback in (
            (self.config.ms_cpc_enabled, "ms_cpc", lambda: self.run_database("ms_cpc")),
            (self.config.mail_enabled, "remote_mail", self.run_mail),
            (self.config.vm_enabled, "vmaintenance", lambda: self.run_database("vmaintenance")),
            (
                bool(self.config.printradar_dsn.get_secret_value()),
                "printradar",
                lambda: self.run_database("printradar"),
            ),
        ):
            if not self.dry_run:
                with self.engine.begin() as connection:
                    connection.execute(
                        update(tables.source)
                        .where(tables.source.c.name == name)
                        .values(enabled=enabled)
                    )
            if enabled and (not self.selected or name in self.selected):
                try:
                    callback()
                except Exception as error:
                    self.failure(
                        self.source(name, "imap" if name == "remote_mail" else "database"),
                        name,
                        error,
                    )
        if self.config.ms_enabled and (not self.selected or "ms_identity" in self.selected):
            source = self.source("ms_identity", "database")
            try:
                mapping = sources.ms_identity_map(self.config)
                if not self.dry_run:
                    with self.engine.begin() as connection:
                        store = TelemetryStore(connection)
                        identifiers = connection.execute(select(tables.source.c.id)).scalars().all()
                        for identifier in identifiers:
                            source_name = connection.execute(
                                select(tables.source.c.name).where(tables.source.c.id == identifier)
                            ).scalar_one()
                            effective = (
                                self.get_active_mapping()
                                if source_name in {"remote_mail", "vmaintenance", "printradar"}
                                else mapping
                            )
                            store.bind_devices(identifier, effective)
                        store.checkpoint(source["id"], {"serials": len(mapping)})
                self.summary["ms_identity"] = {"serials": len(mapping)}
            except Exception as error:
                self.failure(source, "MASZYNA", error)
        if not self.dry_run:
            with self.engine.begin() as connection:
                for name, summary in self.summary.items():
                    values = (
                        {"last_error": summary["errors"][-1]}
                        if summary.get("errors")
                        else {"last_error": None, "last_success_at": datetime.now(UTC)}
                    )
                    connection.execute(
                        update(tables.source).where(tables.source.c.name == name).values(**values)
                    )
        return self.summary
