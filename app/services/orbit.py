"""Projekcja ORBIT i odczyty PostgreSQL bez połączeń ze źródłami podczas HTTP."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, insert, or_, select, update

from app.core.config import settings
from app.models import (
    OrbitDevice,
    OrbitEvent,
    OrbitEvidence,
    OrbitRun,
    ShippingCase,
    ShippingItem,
    ShippingShipment,
    TonerYield,
)
from app.models import telemetry as telemetry
from app.services.orbit_calculations import (
    build_narrative,
    counter_series,
    estimate_toners,
    purchase_cost,
)
from app.services.telemetry.billing import cpc_reading
from app.services.telemetry.parsers import Reading, fingerprint, number, serial_number
from app.services.telemetry.store import TelemetryStore, utcnow

DEVICES = OrbitDevice.__table__
EVENTS = OrbitEvent.__table__
RUNS = OrbitRun.__table__
PROJECTION_VERSION = "orbit-20260913-r1"
PUBLIC_FIELDS = {
    "model",
    "model_id",
    "color",
    "item_id",
    "item_name",
    "quantity",
    "issued_quantity",
    "returned_quantity",
    "customer",
    "customer_id",
    "contract_id",
    "contract_number",
    "start",
    "end",
    "period_start",
    "period_end",
    "order_id",
    "order_year",
    "document_id",
    "invoice_id",
    "document_number",
    "document_kind",
    "tracking_number",
    "is_contract",
    "status",
    "counter_reset",
    "replacement_confirmed",
    "assignment_basis",
    "contract_source",
    "other_contract_id",
    "service_type",
    "source_state",
    "direction",
    "event_code",
    "event_description",
    "item_row_id",
    "evidence_count",
    "shipment_status",
    "observed_at",
    "time_precision",
}
COUNTER_KEYS = {"mono", "color", "total", "scans", "scan_input", "mono_a3", "color_a3"}
COLORS = ("black", "cyan", "magenta", "yellow")
TITLES = {
    "billing_period": "Miesięczne rozliczenie CPC",
    "contract": "Umowa CPC",
    "service": "Zlecenie serwisowe",
    "material_issue": "Wydanie materiału",
    "shipment": "Wysyłka materiału",
    "invoice": "Pozycja faktury",
    "correction": "Korekta dokumentu",
    "reading": "Odczyt urządzenia",
    "daily_snapshot": "Migawka dzienna",
    "supply_event": "Alert materiałowy",
    "device_event": "Zdarzenie urządzenia",
    "event_observation": "Obserwacja komunikatu",
    "unavailable": "Brak komunikacji z urządzeniem",
    "request": "Zgłoszenie",
}


def instant(value):
    """Normalizuje czas techniczny; dokładność biznesową przechowujemy oddzielnie."""
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def put(connection, table, key, values):
    """Aktualizuje projekcję w transakcji workera zabezpieczonej blokadą."""
    condition = and_(*(table.c[name] == value for name, value in key.items()))
    if not connection.execute(update(table).where(condition).values(**values)).rowcount:
        connection.execute(insert(table).values(**key, **values))


def safe_data(data):
    """Biała lista chroni również dowody i zagnieżdżone pola przed wyciekiem kwot."""
    result = {
        key: value
        for key, value in data.items()
        if key in PUBLIC_FIELDS and isinstance(value, (str, int, float, bool, type(None)))
    }
    for key, fields in (("counters", COUNTER_KEYS), ("toner_levels", COLORS)):
        values = data.get(key)
        if isinstance(values, dict):
            result[key] = {name: number(value) for name, value in values.items() if name in fields}
    if isinstance(data.get("billing"), dict):
        result["billing"] = {
            key: value
            for key, value in data["billing"].items()
            if key
            in {"start", "end", "invoice_linked", "mono", "color", "scans", "mono_a3", "color_a3"}
            and isinstance(value, (str, int, float, bool, type(None)))
        }
    if isinstance(data.get("period"), dict):
        result["period"] = {key: safe_day(data["period"].get(key)) for key in ("start", "end")}
    if isinstance(data.get("billing_counters"), dict):
        result["billing_counters"] = {
            metric: {boundary: number(value.get(boundary)) for boundary in ("start", "end")}
            for metric, value in data["billing_counters"].items()
            if metric in COUNTER_KEYS and isinstance(value, dict)
        }
    for key in ("replacements", "bottle_pages"):
        if isinstance(data.get(key), dict):
            result[key] = {
                color: number(value) for color, value in data[key].items() if color in COLORS
            }
    if isinstance(data.get("items"), list):
        result["items"] = [
            {
                key: item.get(key)
                for key in ("item_id", "item_row_id", "item_name", "color", "quantity", "model")
                if isinstance(item.get(key), (str, int, float, type(None)))
            }
            for item in data["items"]
            if isinstance(item, dict)
        ]
    if isinstance(data.get("components"), list):
        result["components"] = [
            safe_data({key: value for key, value in item.items() if key != "components"})
            for item in data["components"]
            if isinstance(item, dict)
        ]
    if isinstance(data.get("issues"), list):
        result["issues"] = [
            value
            for value in data["issues"]
            if isinstance(value, str) and value.replace("_", "").isalnum()
        ]
    return result


def safe_day(value):
    """Akceptuje wyłącznie dzień ISO, nie obiekt źródła ani opis dokumentu."""
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) else None
    except ValueError:
        return None


def stale_after(source):
    """Uwzględnia miesięczny rytm CPC niezależnie od dziennej telemetrii."""
    return timedelta(
        days=(
            max(35, settings.shipping_orbit_stale_days)
            if source == "ms_cpc"
            else settings.shipping_orbit_stale_days
        )
    )


def ingest_snapshot(connection, snapshot):
    """Zapisuje pełny odczyt MS atomowo; CPC używa wcześniejszych kluczy telemetrii."""
    if snapshot.get("complete") is not True:
        raise ValueError("orbit_incomplete_snapshot")
    store = TelemetryStore(connection)
    source = store.ensure_source("ms_orbit", "database")
    cpc_source = store.ensure_source("ms_cpc", "database")
    now = utcnow()
    serials = {item["ms_machine_id"]: item["serial"] for item in snapshot["devices"]}
    duplicate_serials = defaultdict(list)
    for item in snapshot["devices"]:
        duplicate_serials[serial_number(item["serial"])].append(item["id"])
    for item in snapshot["devices"]:
        status = item["status"]
        if not item["serial"] or len(duplicate_serials[serial_number(item["serial"])]) > 1:
            status = "review"
        data = {
            **{key: value for key, value in item.get("data", {}).items() if key != "raw"},
            **{key: item.get(key) for key in ("model_id", "customer_id", "contract_id")},
        }
        if (
            item.get("customer_id") in settings.shipping_orbit_scrap_customer_ids
            or data.get("warehouse_id") in settings.shipping_orbit_scrap_warehouse_ids
        ):
            status = "scrapped"
        values = dict(
            ms_machine_id=item["ms_machine_id"],
            serial=serial_number(item["serial"]),
            model=item.get("model") or "",
            customer=item.get("customer") or "",
            status=status,
            data=data,
            synced_at=now,
        )
        existing = (
            connection.execute(select(DEVICES).where(DEVICES.c.id == item["id"])).mappings().first()
        )
        if existing is not None:
            if values["status"] == "review":
                values["data"]["last_confirmed_status"] = existing["data"].get(
                    "last_confirmed_status", existing["status"]
                )
            if (
                not values["serial"]
                or existing["serial"]
                and values["serial"] != existing["serial"]
            ):
                values["data"]["identity_review"] = {
                    "previous": existing["serial"],
                    "incoming": values["serial"],
                }
                values["serial"] = existing["serial"]
                values["status"] = "review"
            for key in ("model", "customer"):
                values[key] = values[key] or existing[key]
        else:
            values.update(report={}, fingerprint="", updated_at=now)
        put(connection, DEVICES, {"id": item["id"]}, values)
        duplicates = list(
            connection.execute(
                select(DEVICES.c.id).where(DEVICES.c.serial == values["serial"])
            ).scalars()
        )
        if values["serial"] and len(duplicates) > 1:
            connection.execute(
                update(DEVICES).where(DEVICES.c.id.in_(duplicates)).values(status="review")
            )
        serials[item["ms_machine_id"]] = values["serial"]
    readings = []
    billing_readings = []
    by_id = {item["id"]: item for item in snapshot["devices"]}
    for item in snapshot["devices"]:
        readings.append(
            Reading(
                "device:" + item["id"],
                "device_identity",
                serials[item["ms_machine_id"]],
                {"device_id": item["id"], "data": item},
                precision="unknown",
                time_basis="source_state",
            )
        )
    for fact in snapshot["facts"]:
        device = by_id.get(fact["device_id"])
        if device is None:
            raise ValueError("orbit_fact_without_device")
        raw_cpc = fact.get("raw") or (fact.get("data", {}).get("raw", {}).get("CPC") or [None])[0]
        if fact["kind"] in {"cpc", "billing_period"} and raw_cpc:
            billing_readings.append(cpc_reading(raw_cpc, serials))
            continue
        readings.append(
            Reading(
                fact["external_key"],
                fact["kind"],
                serials[device["ms_machine_id"]],
                fact,
                instant(fact.get("observed_at")),
                fact.get("time_precision", "unknown"),
                "source",
                semantic_key=fingerprint(["ms_orbit", fact["external_key"]]),
            )
        )
    current_keys = {reading.external_key for reading in readings}
    previous = connection.execute(
        select(telemetry.record)
        .join(telemetry.record_head, telemetry.record_head.c.record_id == telemetry.record.c.id)
        .where(telemetry.record.c.source_id == source["id"])
    ).mappings()
    for row in previous:
        payload = row["payload"]
        if (
            payload.get("device_id") in by_id
            and row["external_key"] not in current_keys
            and not payload.get("withdrawn")
        ):
            readings.append(
                Reading(
                    row["external_key"],
                    row["kind"],
                    row["serial"],
                    {**payload, "withdrawn": True},
                    instant(row["observed_at"]),
                    row["time_precision"],
                    "source",
                    semantic_key=row["semantic_key"],
                )
            )
    append_withdrawals(
        connection,
        cpc_source,
        billing_readings,
        lambda payload: payload.get("ID_MASZYNA") in serials,
    )
    for source_row, rows in ((source, readings), (cpc_source, billing_readings)):
        for offset in range(0, len(rows), 250):
            page = rows[offset : offset + 250]
            store.ingest(
                source_row["id"], "orbit/" + fingerprint([row.external_key for row in page]), page
            )
        store.checkpoint(
            source_row["id"], {**source_row["checkpoint"], "orbit_full_history_at": now.isoformat()}
        )
    return {"devices": len(serials), "facts": len(snapshot["facts"])}


def normalize_record(row, device):
    """Tłumaczy potwierdzone pola, nie ujawniając oryginalnych wiadomości i dokumentów."""
    payload = row["payload"]
    kind = row["kind"]
    if kind == "device_identity" or payload.get("withdrawn"):
        return None
    data = {}
    financial = {}
    observed = row["observed_at"]
    quality = "confirmed"
    if row["source_name"] in {"ms_orbit", "shipping_orbit"}:
        if payload.get("device_id") != device["id"]:
            return None
        data = dict(payload.get("data", {}))
        if kind == "contract":
            data.update(start=data.get("starts_at"), end=data.get("ends_at"))
        quality = payload.get("status") or data.get("status") or "confirmed"
        financial = {
            key: data[key]
            for key in (
                "purchase_price",
                "purchase_value",
                "document_purchase_price",
                "document_purchase_value",
                "net_amount",
                "currency",
                "zero_cost_confirmed",
                "allocation_status",
                "is_contract",
                "issued_quantity",
                "returned_quantity",
                "contract_id",
            )
            if key in data
        }
        financial.update(
            {key: data[key] for key in ("lines", "problem", "work_done") if key in data}
        )
        if kind == "invoice":
            financial.update(
                net_amount=data.get("net_value"),
                allocation_status="confirmed" if quality == "confirmed" else quality,
            )
        if kind == "material_issue":
            financial["cost"] = purchase_cost(
                {**data, "role": "contract" if data.get("is_contract") is True else None}
            )
            if (
                data.get("is_contract") is None
                or data.get("is_contract") is True
                and quality not in {"confirmed", "known"}
            ):
                financial["cost"] = {
                    **financial["cost"],
                    "amount": None,
                    "status": "unknown",
                    "reason": "Nierozstrzygnięta jakość dokumentu źródłowego.",
                }
            if data.get("is_contract") is True and isinstance(
                data.get("issue_purchase_cost"), dict
            ):
                financial["issue_purchase_cost"] = data["issue_purchase_cost"]
    elif kind == "billing_period":
        if payload.get("ID_MASZYNA") != device["ms_machine_id"]:
            return None
        period = payload.get("__ctip_billing__", {})
        data = {
            "contract_id": payload.get("ID_UMOWACPC"),
            "invoice_id": payload.get("ID_FAKTURA"),
            "customer_id": payload.get("ID_KLIENT"),
            "period_start": period.get("start"),
            "period_end": period.get("end"),
            "billing": dict(period),
            "period": period,
            "counters": {},
            "billing_counters": {},
        }
        observed = instant(period.get("start"))
        for suffix, metric in (
            ("MONO", "mono"),
            ("KOLOR", "color"),
            ("SKAN", "scans"),
            ("MONOA3", "mono_a3"),
            ("KOLORA3", "color_a3"),
        ):
            start = number(payload.get(f"LICZNIK_{suffix}_START"))
            end = number(payload.get(f"LICZNIK_{suffix}_END"))
            data["billing_counters"][metric] = {"start": start, "end": end}
            if end is not None:
                data["counters"][metric] = end
            if start is not None and end is not None:
                data["billing"][metric] = end - start if end >= start else None
                if end < start:
                    quality = "conflict"
        financial = {
            "billing_net": payload.get("OPLATA_SUMA"),
            "currency": "PLN",
            "invoice_linked": period.get("invoice_linked"),
        }
    else:
        measurements = row["measurements"]
        metrics = {
            "lifetime.black": "mono",
            "lifetime.color": "color",
            "lifetime.total": "total",
            "lifetime.scan": "scans",
            "lifetime.scan_input": "scan_input",
        }
        data["counters"] = {
            target: measurements[key] for key, target in metrics.items() if key in measurements
        }
        data["toner_levels"] = {
            color: measurements[f"toner.{color}.percent"]
            for color in COLORS
            if f"toner.{color}.percent" in measurements
        }
        data["model_id"] = device["data"].get("model_id")
        for group in ("replacements", "bottle_pages"):
            data[group] = {
                color: measurements[f"{group}.{color}"]
                for color in COLORS
                if f"{group}.{color}" in measurements
            }
        description = payload.get("Call Type") or payload.get("description")
        if isinstance(description, str):
            data["event_description"] = description[:500]
        if kind == "daily_snapshot" and isinstance(payload.get("components"), dict):
            data["components"] = []
            for component in payload["components"].values():
                normalized = normalize_record(
                    {
                        **dict(row),
                        "kind": "reading",
                        "payload": {},
                        "measurements": component.get("measurements", {}),
                        "observed_at": instant(component.get("time")),
                        "time_precision": component.get("precision", "unknown"),
                    },
                    device,
                )
                data["components"].append(
                    {
                        **normalized["data"],
                        "observed_at": (
                            normalized["observed_at"].isoformat()
                            if normalized["observed_at"]
                            else None
                        ),
                        "time_precision": normalized["time_precision"],
                        "status": normalized["status"],
                    }
                )
            data["counters"], data["toner_levels"] = {}, {}
        data["model"] = device["model"]
        if not observed:
            quality = "partial"
        if kind in {"device_event", "event_observation"}:
            entry = payload.get("entry")
            if isinstance(entry, dict):
                data["event_code"] = next(
                    (
                        str(entry[key])
                        for key in ("code", "error_code", "event_code")
                        if entry.get(key)
                    ),
                    None,
                )
    fatal_issues = {
        "future_time",
        "invalid_range",
        "conflicting_value",
        "future_billing_period",
        "invalid_billing_period",
    }.intersection(row.get("quality_issues", []))
    if row.get("ambiguous_revision") or fatal_issues:
        quality = "conflict"
        data = {
            **data,
            "counters": {metric: None for metric in data.get("counters", {})},
            "toner_levels": {color: None for color in data.get("toner_levels", {})},
        }
        data["issues"] = sorted(fatal_issues) or ["ambiguous_revision"]
        financial = {"status": "conflict"}
    semantic = row["semantic_key"] or fingerprint([row["source_id"], row["external_key"]])
    return dict(
        id=fingerprint([device["id"], semantic, kind]),
        device_id=device["id"],
        record_id=row["id"],
        kind=kind,
        source=row["source_name"],
        observed_at=observed,
        time_precision=row["time_precision"],
        contract_id=str(data["contract_id"]) if data.get("contract_id") else None,
        title=TITLES.get(kind, "Dane źródłowe"),
        status=quality,
        data=safe_data(data),
        finance=financial,
        current=True,
    )


def ingest_shipments(connection, *, device_ids=None):
    """Zachowuje datę przekazania kurierowi bez ponownego naliczania kosztu RW/WZ."""
    cases, items, shipments = (
        model.__table__ for model in (ShippingCase, ShippingItem, ShippingShipment)
    )
    rows = connection.execute(
        select(
            DEVICES.c.id.label("device_id"),
            DEVICES.c.serial,
            DEVICES.c.model,
            DEVICES.c.data.label("device_data"),
            cases.c.firebird_order_id,
            cases.c.firebird_order_year,
            cases.c.firebird_client_id,
            items.c.id.label("item_row_id"),
            items.c.firebird_warehouse_item_id,
            items.c.item_name,
            items.c.quantity,
            shipments.c.id.label("shipment_id"),
            shipments.c.handed_over_at,
            shipments.c.tracking_number,
            shipments.c.status.label("shipment_status"),
            TonerYield.__table__.c.color,
        )
        .select_from(
            cases.join(DEVICES, cases.c.firebird_machine_id == DEVICES.c.ms_machine_id)
            .join(items, items.c.shipping_case_id == cases.c.id)
            .join(shipments, shipments.c.shipping_case_id == cases.c.id)
            .outerjoin(
                TonerYield.__table__, items.c.firebird_warehouse_item_id == TonerYield.item_id
            )
        )
        .where(shipments.c.handed_over_at.is_not(None))
        .where(DEVICES.c.id.in_(device_ids) if device_ids is not None else True)
    ).mappings()
    store = TelemetryStore(connection)
    source = store.ensure_source("shipping_orbit", "database")
    readings = []
    for row in rows:
        key = f"shipment:{row['shipment_id']}:{row['item_row_id']}"
        data = dict(
            order_id=row["firebird_order_id"],
            order_year=row["firebird_order_year"],
            customer_id=row["firebird_client_id"],
            model=row["model"],
            color=row["color"],
            item_id=row["firebird_warehouse_item_id"],
            item_name=row["item_name"],
            quantity=str(row["quantity"]),
            tracking_number=row["tracking_number"],
            item_row_id=row["item_row_id"],
            direction="outbound",
            shipment_status=row["shipment_status"],
        )
        payload = dict(external_key=key, device_id=row["device_id"], data=data, status="confirmed")
        readings.append(
            Reading(
                key,
                "shipment",
                row["serial"],
                payload,
                instant(row["handed_over_at"]),
                "second",
                "shipping_handover",
                semantic_key=fingerprint(["shipping", key]),
            )
        )
    visible_devices = (
        set(device_ids)
        if device_ids is not None
        else set(connection.execute(select(DEVICES.c.id)).scalars())
    )
    append_withdrawals(
        connection, source, readings, lambda payload: payload.get("device_id") in visible_devices
    )
    for offset in range(0, len(readings), 250):
        page = readings[offset : offset + 250]
        store.ingest(
            source["id"], "shipments/" + fingerprint([row.external_key for row in page]), page
        )
    return {"shipments": len(readings)}


def append_withdrawals(connection, source, readings, belongs):
    """Wycofuje brakujące fakty po pełnym odczycie zakresu, zachowując oryginały."""
    existing_keys = {reading.external_key for reading in readings}
    rows = connection.execute(
        select(telemetry.record)
        .join(telemetry.record_head, telemetry.record_head.c.record_id == telemetry.record.c.id)
        .where(telemetry.record.c.source_id == source["id"])
    ).mappings()
    for row in rows:
        if (
            row["external_key"] in existing_keys
            or row["payload"].get("withdrawn")
            or not belongs(row["payload"])
        ):
            continue
        readings.append(
            Reading(
                row["external_key"],
                row["kind"],
                row["serial"],
                {**row["payload"], "withdrawn": True},
                row["observed_at"],
                row["time_precision"],
                "source_state",
                semantic_key=row["semantic_key"],
            )
        )


def canonical_records(connection, device):
    """Używa jawnych głów; stare wielowersyjne dane bez głowy wymagają weryfikacji."""
    rows = connection.execute(
        select(telemetry.record, telemetry.source.c.name.label("source_name"))
        .join(telemetry.source, telemetry.record.c.source_id == telemetry.source.c.id)
        .where(telemetry.record.c.serial == device["serial"])
    ).mappings()
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["source_id"], row["external_key"])].append(dict(row))
    head_ids = {}
    quality_issues = defaultdict(list)
    for record_id, code in connection.execute(
        select(telemetry.issue.c.record_id, telemetry.issue.c.code)
        .join(telemetry.record, telemetry.issue.c.record_id == telemetry.record.c.id)
        .where(telemetry.record.c.serial == device["serial"])
    ):
        quality_issues[record_id].append(code)
    for table in (telemetry.record_head, telemetry.daily_head):
        for row in connection.execute(
            select(table)
            .join(telemetry.record, table.c.record_id == telemetry.record.c.id)
            .where(telemetry.record.c.serial == device["serial"])
        ).mappings():
            head_ids[(row["source_id"], row["external_key"])] = row["record_id"]
    for key, versions in grouped.items():
        chosen = next((row for row in versions if row["id"] == head_ids.get(key)), None)
        if chosen is None:
            chosen = max(versions, key=lambda row: (instant(row["imported_at"]), row["id"]))
            chosen["ambiguous_revision"] = len(versions) > 1
        if device["status"] == "review" and chosen["source_name"] not in {"ms_orbit", "ms_cpc"}:
            continue
        chosen["quality_issues"] = quality_issues[chosen["id"]]
        yield chosen


def event_json(row, can_view_finance=False):
    """Serializuje zdarzenie bez surowego payload i bez nieuprawnionych kwot."""
    result = {
        key: row[key]
        for key in (
            "id",
            "kind",
            "source",
            "time_precision",
            "contract_id",
            "title",
            "status",
            "data",
        )
    }
    result["observed_at"] = (
        instant(row["observed_at"]).isoformat()
        if row["observed_at"] and row["time_precision"] != "month"
        else None
    )
    result["data"] = safe_data(row["data"])
    if can_view_finance:
        result["finance"] = row["finance"]
    return result


def assign_contracts(device, events):
    """Przypisuje zdarzenie tylko do jednoznacznego okresu, nigdy do dzisiejszego klienta."""
    intervals = []
    orders = defaultdict(set)
    for event in events:
        data = event["data"]
        if event["kind"] == "contract" and data.get("contract_source", "UMOWACPC") == "UMOWACPC":
            start, end = instant(data.get("start")), instant(data.get("end"))
            if start and (
                end
                or device["status"] == "active"
                and str(device["data"].get("contract_id")) == event["contract_id"]
            ):
                intervals.append(
                    (
                        start.date(),
                        end.date() if end else date.max,
                        event["contract_id"],
                        data.get("customer_id"),
                        "contract_dates",
                    )
                )
        if (
            event["kind"] == "billing_period"
            and data.get("period_start")
            and data.get("period_end")
        ):
            intervals.append(
                (
                    date.fromisoformat(data["period_start"]),
                    date.fromisoformat(data["period_end"]),
                    event["contract_id"],
                    data.get("customer_id"),
                    "billing_period",
                )
            )
        if (
            event["kind"] == "service"
            and event["contract_id"]
            and data.get("order_id")
            and data.get("order_year")
        ):
            orders[(data["order_id"], data["order_year"])].add(event["contract_id"])
    for event in events:
        if event["contract_id"]:
            continue
        data = event["data"]
        if data.get("is_contract") is False or event["kind"] not in {
            "reading",
            "daily_snapshot",
            "device_event",
            "event_observation",
            "supply_event",
            "shipment",
            "unavailable",
            "request",
        }:
            continue
        candidates = orders.get((data.get("order_id"), data.get("order_year")), set())
        basis = "order_history"
        if not candidates and event["observed_at"]:
            day = instant(event["observed_at"]).date()
            matches = [
                interval
                for interval in intervals
                if interval[0] <= day <= interval[1]
                and (not data.get("customer_id") or interval[3] == data["customer_id"])
            ]
            candidates = {interval[2] for interval in matches if interval[2]}
            basis = "source_period"
        if len(candidates) == 1:
            event["contract_id"] = next(iter(candidates))
            event["data"] = {**data, "contract_id": event["contract_id"], "assignment_basis": basis}
        elif len(candidates) > 1:
            event["data"] = {
                **data,
                "issues": [*data.get("issues", []), "ambiguous_contract_period"],
            }


def financial_summary(events):
    """Sumuje wyłącznie udokumentowany zakup; nie deklaruje kompletnej marży."""
    totals = defaultdict(Decimal)
    unknown = 0
    revenue = defaultdict(Decimal)
    missing_revenue = 0
    documented_issues = []
    for event in events:
        data = event.get("finance", {})
        if event["kind"] == "material_issue":
            issued = data.get("issue_purchase_cost", {})
            if issued.get("status") == "known" and issued.get("amount") is not None:
                documented_issues.append(
                    {
                        "event_id": event["id"],
                        "observed_at": (
                            instant(event["observed_at"]).isoformat()
                            if event["observed_at"]
                            else None
                        ),
                        "source": event["source"],
                        "document_id": event["data"].get("document_id"),
                        **issued,
                    }
                )
            cost = data.get("cost", {})
            if cost.get("amount") is not None and cost.get("status") in {"confirmed", "known"}:
                totals[cost.get("currency", "PLN")] += Decimal(cost["amount"])
            elif cost.get("status") not in {"not_applicable", "excluded"}:
                unknown += 1
        if (
            event["kind"] == "invoice"
            and data.get("allocation_status") == "confirmed"
            and data.get("net_amount") is not None
            and isinstance(data.get("currency"), str)
            and len(data["currency"]) == 3
        ):
            revenue[data.get("currency", "PLN")] += Decimal(str(data["net_amount"]))
        elif event["kind"] in {"invoice", "correction"}:
            missing_revenue += 1
    return {
        "status": "partial",
        "purchase_costs": {key: str(value) for key, value in totals.items()},
        "invoice_revenue": {key: str(value) for key, value in revenue.items()},
        "missing_costs": unknown,
        "missing_revenue": missing_revenue,
        "documented_issues": documented_issues,
        "margin": None,
        "note": "Znane koszty zakupu materiałów i części, nie pełny koszt umowy. Brak kwoty nie oznacza zera.",
    }


def report_for(device, events, yields):
    """Buduje zakresowy raport z materiałów już zapisanych w PostgreSQL."""
    public_events = [event_json(event) for event in events]
    contracts = [
        dict(id=event["contract_id"], **event["data"])
        for event in events
        if event["kind"] == "contract"
    ]
    sources = defaultdict(list)
    for event in events:
        sources[event["source"]].append(event)
    coverage = []
    for source, values in sorted(sources.items()):
        times = [instant(event["observed_at"]) for event in values if event["observed_at"]]
        latest = max(times) if times else None
        coverage.append(
            {
                "source": source,
                "count": len(values),
                "first_at": min(times).isoformat() if times else None,
                "last_at": latest.isoformat() if latest else None,
                "status": (
                    "conflict"
                    if any(event["status"] == "conflict" for event in values)
                    else (
                        "stale"
                        if latest and utcnow() - latest > stale_after(source)
                        else "partial" if not times else "confirmed"
                    )
                ),
            }
        )
    calculation_events = []
    for event in public_events:
        data = {"model": device["model"], **event["data"]}
        if event["kind"] == "shipment" and data.get("items"):
            for item in data["items"]:
                calculation_events.append(
                    {
                        **event,
                        "id": event["id"] + ":" + str(item.get("item_row_id")),
                        "data": {**data, **item},
                    }
                )
        calculation_events.append(
            {
                **event,
                "kind": "billing_counter" if event["kind"] == "billing_period" else event["kind"],
                "data": data,
            }
        )
        for component in data.get("components", []):
            calculation_events.append(
                {
                    **event,
                    "id": event["id"],
                    "kind": "reading",
                    "observed_at": component.get("observed_at"),
                    "time_precision": component.get("time_precision", "unknown"),
                    "status": component.get("status", event["status"]),
                    "data": {"model": device["model"], **component},
                }
            )
        for color, level in data.get("toner_levels", {}).items():
            calculation_events.append(
                {
                    **event,
                    "kind": "toner_level",
                    "data": {**data, "color": color, "level_percent": level, "counters": {}},
                }
            )
    catalog = []
    for item in yields:
        for model in item["models"]:
            catalog.append(
                {
                    **item,
                    "model": model.get("model", ""),
                    "metric": "total" if item["color"] == "black" else "color",
                }
            )
    counters = counter_series(calculation_events)
    toners = estimate_toners(calculation_events, catalog)
    summary = {
        "events": len(events),
        "contracts": len(contracts),
        "status": "partial",
        "margin_available": False,
    }
    return {
        "summary": summary,
        "counters": counters,
        "toners": toners,
        "contracts": contracts,
        "finance": financial_summary(events),
        "coverage": coverage,
        "narrative": build_narrative(
            device,
            {
                "counter_series": counters,
                "toners": toners,
                "supply_alert_count": sum(event["kind"] == "supply_event" for event in events),
            },
        ),
    }


def project(connection, device_ids=None):
    """Odtwarza zmienione urządzenia; nie usuwa archiwum ani oryginalnych danych."""
    from app.services.orbit_linkage import consolidate

    query = select(DEVICES)
    if device_ids is not None:
        query = query.where(DEVICES.c.id.in_(device_ids))
    yields = [dict(row) for row in connection.execute(select(TonerYield.__table__)).mappings()]
    count = 0
    for device in connection.execute(query).mappings().all():
        canonical = list(canonical_records(connection, device))
        signature = fingerprint(
            [
                PROJECTION_VERSION,
                sorted(
                    (row["id"], row.get("ambiguous_revision", False), row.get("quality_issues", []))
                    for row in canonical
                ),
                yields,
                device["data"],
                device["status"],
            ]
        )
        if signature == device["fingerprint"]:
            continue
        events = (
            {
                row["id"]: dict(row)
                for row in connection.execute(
                    select(EVENTS).where(
                        EVENTS.c.device_id == device["id"],
                        EVENTS.c.current.is_(True),
                        EVENTS.c.source.not_in(["ms_orbit", "ms_cpc"]),
                    )
                ).mappings()
            }
            if device["status"] == "review"
            else {}
        )
        for row in canonical:
            event = normalize_record(row, device)
            if event:
                previous = events.get(event["id"])
                if previous and (
                    previous["data"] != event["data"] or previous["finance"] != event["finance"]
                ):
                    event["status"] = "conflict"
                    event["data"] = {}
                    event["finance"] = {"status": "conflict"}
                events[event["id"]] = event
        combined, evidence_links = consolidate(list(events.values()))
        events = {event["id"]: event for event in combined}
        assign_contracts(device, combined)
        connection.execute(
            update(EVENTS).where(EVENTS.c.device_id == device["id"]).values(current=False)
        )
        for event in events.values():
            put(
                connection,
                EVENTS,
                {"id": event["id"]},
                {key: value for key, value in event.items() if key != "id"},
            )
        report = report_for(device, list(events.values()), yields)
        evidence_table = OrbitEvidence.__table__
        connection.execute(
            update(evidence_table)
            .where(
                evidence_table.c.event_id.in_(
                    select(EVENTS.c.id).where(EVENTS.c.device_id == device["id"])
                )
            )
            .values(current=False)
        )
        for link in evidence_links:
            put(
                connection,
                evidence_table,
                {key: link[key] for key in ("event_id", "record_id")},
                {key: link[key] for key in ("role", "current")},
            )
        connection.execute(
            update(DEVICES)
            .where(DEVICES.c.id == device["id"])
            .values(
                report=report,
                fingerprint=signature,
                updated_at=utcnow(),
            )
        )
        count += 1
    put(
        connection,
        RUNS,
        {"name": "projection"},
        dict(
            finished_at=utcnow(),
            status="ok",
            counts={"updated": count},
            error_code=None,
        ),
    )
    return {"updated": count}


def list_devices(connection, *, query="", scope="active", page=1, page_size=50):
    """Przeszukuje indeksowaną projekcję, nigdy zewnętrzne źródła."""
    conditions = []
    if scope != "all":
        conditions.append(DEVICES.c.status == scope)
    if query.strip():
        term = (
            "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        conditions.append(
            or_(
                *(
                    column.ilike(term, escape="\\")
                    for column in (DEVICES.c.serial, DEVICES.c.model, DEVICES.c.customer)
                )
            )
        )
    total = connection.execute(
        select(func.count()).select_from(DEVICES).where(*conditions)
    ).scalar_one()
    rows = connection.execute(
        select(
            DEVICES.c.id,
            DEVICES.c.ms_machine_id,
            DEVICES.c.serial,
            DEVICES.c.model,
            DEVICES.c.customer,
            DEVICES.c.status,
            DEVICES.c.updated_at,
            DEVICES.c.synced_at,
        )
        .where(*conditions)
        .order_by(DEVICES.c.serial, DEVICES.c.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).mappings()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def device_row(connection, device_id):
    """Rozróżnia brak urządzenia od brakującej historii."""
    row = connection.execute(select(DEVICES).where(DEVICES.c.id == device_id)).mappings().first()
    if row is None:
        raise LookupError("Nie znaleziono urządzenia w ORBIT.")
    return row


def event_conditions(device_id, contract_id=None, date_from=None, date_to=None):
    """Filtruje po urządzeniu i okresie; miesięcy nie rozciąga na fikcyjne dni."""
    conditions = [EVENTS.c.device_id == device_id, EVENTS.c.current.is_(True)]
    if contract_id:
        conditions.append(EVENTS.c.contract_id == contract_id)
    if date_from:
        conditions.append(EVENTS.c.observed_at >= instant(date_from.replace(day=1)))
    if date_to:
        conditions.append(EVENTS.c.observed_at < instant(date_to + timedelta(days=1)))
    return conditions


def selected_events(connection, device_id, **filters):
    """Dobiera pełne okresy miesięczne i dokładne dni dla pozostałych zdarzeń."""
    rows = connection.execute(
        select(EVENTS)
        .where(*event_conditions(device_id, **filters))
        .order_by(EVENTS.c.observed_at, EVENTS.c.id)
    ).mappings()
    start = instant(filters.get("date_from"))
    return [
        dict(row)
        for row in rows
        if not start or row["time_precision"] == "month" or instant(row["observed_at"]) >= start
    ]


def device_detail(connection, device_id, *, can_view_finance=False, **filters):
    """Chroni kwoty niezależnie od ukrycia zakładki w przeglądarce."""
    device = device_row(connection, device_id)
    report = dict(device["report"])
    if any(filters.values()):
        events = selected_events(connection, device_id, **filters)
        yields = [dict(row) for row in connection.execute(select(TonerYield.__table__)).mappings()]
        report = report_for(device, events, yields)
        cycle_events = selected_events(connection, device_id, date_to=filters.get("date_to"))
        full_cycles = report_for(device, cycle_events, yields)
        report["toners"] = full_cycles["toners"]
        report["toner_scope"] = (
            "Historia urządzenia do końca zakresu; filtr początku i umowy nie rozpoczyna nowego cyklu."
        )
        report["narrative"] = build_narrative(
            device, {"counter_series": report["counters"], "toners": report["toners"]}
        )
    report = public_projection(report, device, can_view_finance=can_view_finance)
    from app.services.orbit_advice import assessment, forecasts, toner_forecasts
    from app.services.orbit_policy import resolve

    policies = resolve(connection, device)
    report["policy"] = policies
    forecast_events = selected_events(connection, device_id, **filters)
    report["forecast"] = forecasts(
        forecast_events,
        today=filters.get("date_to"),
        stale_days=policies["effective"]["black"]["cpc_stale_days"],
    )
    report["toners"] = [
        toner_forecasts(
            [toner],
            forecasts(
                forecast_events,
                today=filters.get("date_to"),
                stale_days=policies["effective"][toner["color"]]["cpc_stale_days"],
            ),
            report.get("counters", []),
            today=filters.get("date_to"),
            measurement_stale_days=policies["effective"][toner["color"]]["measurement_stale_days"],
        )[0]
        for toner in report.get("toners", [])
    ]
    report["advice"] = assessment(
        report.get("toners", []), policies["effective"], {color: 1 for color in COLORS}
    )
    report["coverage"] = [
        {
            **entry,
            "status": (
                "stale"
                if entry.get("status") == "confirmed"
                and entry.get("last_at")
                and utcnow() - instant(entry["last_at"]) > stale_after(entry.get("source"))
                else entry.get("status")
            ),
        }
        for entry in report.get("coverage", [])
    ]
    report["device"] = {
        key: device[key]
        for key in (
            "id",
            "ms_machine_id",
            "serial",
            "model",
            "customer",
            "status",
            "updated_at",
            "synced_at",
        )
    }
    report["can_view_finance"] = can_view_finance
    report["device"]["customer_id"] = device["data"].get("customer_id")
    report["sources"] = [dict(row) for row in connection.execute(select(RUNS)).mappings()]
    return report


def public_projection(report, device, *, can_view_finance):
    """Ponownie ogranicza także starszą zapisaną projekcję, zamiast ufać całemu JSON."""

    def fields(value, names):
        return {
            key: item
            for key, item in value.items()
            if key in names and isinstance(item, (str, int, float, bool, type(None)))
        }

    counters = []
    for series in report.get("counters", []):
        points = []
        for point in series.get("points", []):
            points.append(
                {
                    **fields(
                        point,
                        {
                            "event_id",
                            "observed_at",
                            "time_precision",
                            "value",
                            "delta",
                            "epoch",
                            "status",
                            "reason",
                        },
                    ),
                    "period": safe_data({"period": point.get("period")}).get("period"),
                }
            )
        counters.append({**fields(series, {"metric", "source", "status"}), "points": points})
    toners = [
        fields(
            item,
            {
                "model",
                "color",
                "status",
                "basis",
                "reason",
                "remaining_percent",
                "remaining_pages",
                "consumed_pages",
                "yield_pages",
                "yield_status",
                "yield_source",
                "source",
                "observed_at",
                "baseline_at",
                "time_precision",
                "stock_quantity",
            },
        )
        for item in report.get("toners", [])
    ]
    result = {
        "summary": fields(
            report.get("summary", {}), {"events", "contracts", "status", "margin_available"}
        ),
        "counters": counters,
        "toners": toners,
        "contracts": [
            {"id": str(item.get("id") or item.get("other_contract_id") or ""), **safe_data(item)}
            for item in report.get("contracts", [])
        ],
        "coverage": [
            fields(item, {"source", "count", "first_at", "last_at", "status"})
            for item in report.get("coverage", [])
        ],
        "narrative": build_narrative(device, {"counter_series": counters, "toners": toners}),
    }
    if report.get("toner_scope"):
        result["toner_scope"] = report["toner_scope"]
    if can_view_finance:
        result["finance"] = report.get("finance", {})
    return result


def timeline(
    connection,
    device_id,
    *,
    bucket="month",
    page=1,
    page_size=50,
    can_view_finance=False,
    **filters,
):
    """Zwraca paginowane zdarzenia i liczności okresów, nie sumy liczników narastających."""
    device_row(connection, device_id)
    events = selected_events(connection, device_id, **filters)
    groups = defaultdict(int)
    for event in events:
        observed = instant(event["observed_at"])
        if not observed:
            key = "unknown"
        elif event["time_precision"] == "month":
            key = "month:" + observed.strftime("%Y-%m")
        elif bucket == "week":
            key = (observed - timedelta(days=observed.weekday())).date().isoformat()
        else:
            key = observed.strftime("%Y-%m" if bucket == "month" else "%Y-%m-%d")
        groups[key] += 1
    events.reverse()
    selected = events[(page - 1) * page_size : page * page_size]
    return {
        "items": [event_json(row, can_view_finance) for row in selected],
        "total": len(events),
        "page": page,
        "page_size": page_size,
        "bucket": bucket,
        "buckets": [{"period": key, "events": value} for key, value in sorted(groups.items())],
    }


def evidence(connection, device_id, event_id, *, can_view_finance=False):
    """Udostępnia wyłącznie znormalizowany dowód należący do wskazanego urządzenia."""
    row = (
        connection.execute(
            select(EVENTS).where(
                EVENTS.c.id == event_id,
                EVENTS.c.device_id == device_id,
                EVENTS.c.current.is_(True),
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LookupError("Nie znaleziono dowodu dla tego urządzenia.")
    result = event_json(row, can_view_finance)
    links = OrbitEvidence.__table__
    records = connection.execute(
        select(telemetry.record, telemetry.source.c.name.label("source_name"))
        .join(links, links.c.record_id == telemetry.record.c.id)
        .join(telemetry.source, telemetry.source.c.id == telemetry.record.c.source_id)
        .where(links.c.event_id == event_id, links.c.current.is_(True))
    ).mappings()
    device = device_row(connection, device_id)
    result["evidence"] = [
        event_json(normalized, can_view_finance)
        for record in records
        if (normalized := normalize_record(record, device))
    ]
    return result
