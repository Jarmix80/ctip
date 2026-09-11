"""Wznawialne migawki dzienne dopasowanych urządzeń i niezależne zdarzenia."""

import copy
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import telemetry as tables
from app.services.telemetry import sources
from app.services.telemetry.parsers import Reading, fingerprint, number, serial_number, timestamp
from app.services.telemetry.store import TelemetryStore

POLICY = "daily2"
logger = logging.getLogger(__name__)


def years_ago(today, years):
    """Wyznacza kalendarzowy zakres także dla 29 lutego."""
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def event_entries(value, path=""):
    """Wydobywa dostępne historie i komunikaty bez interpretowania surowych tablic SNMP."""
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key).lower()
            current = f"{path}.{key}".strip(".")
            if any(word in name for word in ("history", "event_log", "error_log", "jam_log")):
                for entry in child if isinstance(child, list) else [child]:
                    if entry:
                        yield current, entry, True
            elif name in {"active_messages", "messages", "errors", "error", "notatki"} and child:
                for entry in child if isinstance(child, list) else [child]:
                    if entry:
                        yield current, entry, False
            elif name not in {"raw_rows", "ricoh_counter_rows", "web_artifact"}:
                yield from event_entries(child, current)


def event_readings(kind, serial, row, reading):
    """Deduplikuje wpisy historii; stan bez daty wystąpienia pozostaje obserwacją."""
    payload = row.get("summary_json") or row.get("payload_json") or row
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    result = []
    for path, entry, historic in event_entries(payload):
        fields = entry if isinstance(entry, dict) else {}
        raw_date = next(
            (
                fields[key]
                for key in ("occurred_at", "event_time", "timestamp", "datetime", "date")
                if fields.get(key)
            ),
            "",
        )
        observed, precision, basis = timestamp(str(raw_date), zone="Europe/Warsaw")
        event_kind = "device_event" if historic else "event_observation"
        data = {"policy": POLICY, "category": path, "entry": entry}
        key = fingerprint([kind, serial, event_kind, data])
        result.append(
            Reading(
                key,
                event_kind,
                serial,
                data,
                observed,
                precision,
                basis,
                received_at=reading.observed_at,
                semantic_key=key,
                origin_key=f"{reading.external_key}/{path}/{fingerprint(entry)}",
            )
        )
    return result


def merge_daily(current, incoming):
    """Wybiera ostatni poprawny składnik dnia, niezależnie od kolejności stron źródła."""
    result = copy.deepcopy(current or incoming)
    result["components"] = copy.deepcopy((current or {}).get("components", {}))
    for key, component in incoming["components"].items():
        previous = result["components"].get(key)
        if previous is None or (component["time"], component["key"]) > (
            previous["time"],
            previous["key"],
        ):
            result["components"][key] = component
    return result


def daily_reading(payload):
    """Buduje jedną logiczną migawkę z jawnymi czasami składników."""
    components = payload["components"]
    newest = max(components.values(), key=lambda value: (value["time"], value["key"]))
    key = f"daily:{payload['serial']}:{payload['day']}"
    result = Reading(
        key,
        "daily_snapshot",
        payload["serial"],
        payload,
        datetime.fromisoformat(newest["time"]),
        newest.get("precision", "second"),
        newest.get("time_basis", "Europe/Warsaw"),
        semantic_key=fingerprint([payload["source"], key]),
    )
    ordered = sorted(components.values(), key=lambda value: (value["time"], value["key"]))
    for component in ordered:
        result.measurements.update(component["measurements"])
    return result


def prepare_page(kind, table, rows, identities, mapping, start, today):
    """Filtruje tożsamość przed zapisem, zachowując osobno zdarzenia i jakość danych."""
    snapshots, events = {}, []
    counts = {"rows_read": len(rows), "excluded": 0}
    for row in rows:
        reading = (
            sources.vm_reading(table, row, identities)
            if kind == "vmaintenance"
            else sources.printradar_reading(table, row, identities)
        )
        if len(mapping.get(reading.serial, [])) != 1:
            counts["excluded"] += 1
            continue
        observed = reading.observed_at
        if observed is None:
            if table in {"device_fingerprints", "MASZYNY"}:
                reading.kind = "device_state"
                events.append(reading)
            else:
                counts["invalid_time"] = counts.get("invalid_time", 0) + 1
            continue
        day = observed.astimezone(ZoneInfo("Europe/Warsaw")).date()
        if not start <= day <= today or observed > datetime.now(UTC):
            counts["excluded"] += 1
            continue
        for event in event_readings(kind, reading.serial, row, reading):
            if (
                event.observed_at is None
                or start <= event.observed_at.astimezone(ZoneInfo("Europe/Warsaw")).date() <= today
            ):
                events.append(event)
        if table in {"WEZWANIE", "CPC", "DODAJ", "MAGAZYNY", "device_fingerprints"}:
            reading.origin_key = reading.external_key
            events.append(reading)
            continue
        valid = row.get("source_status") != "error" and row.get("status") != "error"
        if not valid:
            counts["invalid_readings"] = counts.get("invalid_readings", 0) + 1
            continue
        if kind == "printradar" and table == "service_snapshots":
            summary = row.get("summary_json") or {}
            if isinstance(summary, str):
                import json

                summary = json.loads(summary)
            for color, value in summary.get("materials", {}).get("toners", {}).items():
                percent = number(value.get("percent"))
                if percent is not None:
                    reading.measurements[f"toner.{color}.percent"] = percent
        if table == "material_readings":
            material, color = sources.material_identity(row)
            component_key = f"material:{material}:{color}:{row.get('supply_key', '')}"
        else:
            component_key = (
                "counters"
                if table in {"MASZYNY", "MASZYNY_STATS", "raw_counter_samples"}
                else "service"
            )
        payload = {
            "policy": POLICY,
            "source": kind,
            "serial": reading.serial,
            "day": day.isoformat(),
            "components": {
                component_key: {
                    "key": reading.external_key,
                    "time": observed.isoformat(),
                    "precision": reading.precision,
                    "time_basis": reading.time_basis,
                    "data": reading.payload,
                    "measurements": reading.measurements,
                }
            },
        }
        key = (reading.serial, day.isoformat())
        snapshots[key] = merge_daily(snapshots.get(key), payload)
    return snapshots, events, counts


def vm_daily_page(table, after, limit, config, identifiers, start):
    """Pobiera V po indeksowanym kluczu, ograniczając zakres dat i identyfikatory maszyn."""
    if not identifiers:
        return []
    primary = sources.VM_TABLES[table]
    date_column = (
        "DATA_LICZNIKA" if table == "MASZYNY" else "DATA_ROZLICZENIA" if table == "CPC" else "DATA"
    )
    with sources.firebird_connection(True, config) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"SELECT FIRST {int(limit)} * FROM {table} WHERE {primary}>? "
            f"AND ID_MASZYNA IN ({','.join('?' for _ in identifiers)}) "
            f"AND ({date_column}>=? OR {date_column} IS NULL) ORDER BY {primary}",
            [after or 0, *identifiers, start],
        )
        names = [column[0].strip() for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def pr_daily_page(table, after, limit, config, fingerprints, start):
    """Ogranicza ciężką projekcję JSON do porcji wybranej po kluczu źródłowym."""
    if not fingerprints:
        return []
    primary = sources.PR_TABLES[table]
    conditions, params = ["fingerprint_key=ANY(%s)"], [fingerprints]
    if after is not None:
        conditions.append(f"{primary}>%s")
        params.append(after)
    if table != "device_fingerprints":
        conditions.append("(collected_at>=%s OR collected_at IS NULL OR collected_at='')")
        params.append((start - timedelta(days=1)).isoformat())
    projection = "to_jsonb(data)"
    if table in {"raw_counter_samples", "service_snapshots"}:
        projection = "(to_jsonb(data)-'payload_json') || jsonb_build_object('payload_json',payload_json::jsonb-'raw_rows'-'ricoh_counter_rows'-'web_artifact')"
    with sources.printradar_connection(config.printradar_dsn.get_secret_value()) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"SELECT {projection} FROM (SELECT * FROM {table} WHERE {' AND '.join(conditions)} "
            f"ORDER BY {primary} LIMIT %s) data",
            [*params, limit],
        )
        return [row[0] for row in cursor.fetchall()]


def run_daily(runner, kind):
    """Wznawia porcje i koryguje ostatni tydzień bez utraty historii nowych umów."""
    mapping = runner.get_active_mapping()
    source = runner.source(kind, "database")
    checkpoint = copy.deepcopy(source["checkpoint"])
    today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    years = (
        runner.config.vm_history_years
        if kind == "vmaintenance"
        else runner.config.printradar_history_years
    )
    earliest = years_ago(today, years)
    signature = fingerprint(mapping)
    state = checkpoint.get(POLICY, {})
    if state.get("active_signature") != signature:
        state = {}
    if state.get("in_progress"):
        start = max(earliest, date.fromisoformat(state["start"]))
    else:
        completed = state.get("completed")
        start = (
            earliest
            if runner.backfill or not completed
            else max(
                earliest,
                date.fromisoformat(completed) - timedelta(days=runner.config.reconcile_days),
            )
        )
        state = {
            "start": start.isoformat(),
            "tables": {},
            "active_signature": signature,
            "in_progress": True,
        }
    if kind == "vmaintenance":
        identities = sources.vm_serials(runner.config)
        identifiers = [
            key for key, serial in identities.items() if len(mapping.get(serial, [])) == 1
        ]
        tables_map = {
            name: sources.VM_TABLES[name]
            for name in ("MASZYNY", "MASZYNY_STATS", "WEZWANIE", "CPC")
        }
    else:
        identities, after = {}, None
        while True:
            rows = sources.printradar_page(
                runner.config.printradar_dsn.get_secret_value(),
                "device_fingerprints",
                after,
                runner.config.page_size,
            )
            if not rows:
                break
            identities.update(
                {row["fingerprint_key"]: serial_number(row.get("serial_number")) for row in rows}
            )
            after = rows[-1]["fingerprint_key"]
        identifiers = [
            key for key, serial in identities.items() if len(mapping.get(serial, [])) == 1
        ]
        tables_map = sources.PR_TABLES
    page_count = 0
    for table, primary in tables_map.items():
        table_state = state["tables"].setdefault(table, {})
        if table_state.get("done"):
            continue
        while True:
            if runner.item_limit is not None and page_count >= runner.item_limit:
                return
            if not runner.backfill and not runner.drain and page_count >= runner.config.max_pages:
                return
            rows = (
                vm_daily_page(
                    table,
                    table_state.get("after"),
                    runner.config.page_size,
                    runner.config,
                    identifiers,
                    start,
                )
                if kind == "vmaintenance"
                else pr_daily_page(
                    table,
                    table_state.get("after"),
                    runner.config.page_size,
                    runner.config,
                    identifiers,
                    start,
                )
            )
            snapshots, events, counts = prepare_page(
                kind, table, rows, identities, mapping, start, today
            )
            candidate = copy.deepcopy(state)
            candidate_table = candidate["tables"][table]
            if rows:
                candidate_table["after"] = rows[-1][primary]
            candidate_table["done"] = len(rows) < runner.config.page_size
            if all(candidate["tables"].get(name, {}).get("done") for name in tables_map):
                candidate.update(in_progress=False, completed=today.isoformat())
            checkpoint[POLICY] = candidate
            if runner.dry_run:
                result = {"new": len(snapshots) + len(events), "duplicates": 0}
            else:
                with runner.engine.begin() as connection:
                    readings = list(events)
                    for payload in snapshots.values():
                        key = f"daily:{payload['serial']}:{payload['day']}"
                        previous = connection.execute(
                            select(tables.record.c.payload)
                            .where(
                                tables.record.c.source_id == source["id"],
                                tables.record.c.external_key == key,
                            )
                            .order_by(tables.record.c.imported_at.desc())
                            .limit(1)
                        ).scalar()
                        readings.append(daily_reading(merge_daily(previous, payload)))
                    store = TelemetryStore(connection)
                    result = store.ingest(
                        source["id"], f"{POLICY}/{table}/{candidate_table.get('after')}", readings
                    )
                    store.checkpoint(source["id"], checkpoint)
            for key, value in {
                **counts,
                "new": result["new"],
                "duplicates": result["duplicates"],
            }.items():
                runner.summary[kind][key] = runner.summary[kind].get(key, 0) + value
            state, table_state = candidate, candidate_table
            page_count += 1
            logger.info(
                "Źródło %s, tabela %s: wiersze=%s nowe=%s powtórzenia=%s",
                kind,
                table,
                len(rows),
                result["new"],
                result["duplicates"],
            )
            if table_state["done"]:
                break
