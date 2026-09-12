"""Polityki ORBIT i odczytowa ocena szkicu wysyłki z lokalnego PostgreSQL."""

from datetime import UTC, datetime

from sqlalchemy import and_, select

from app.models import (
    OrbitDevice,
    OrbitPolicy,
    ShippingCase,
    ShippingItem,
    ShippingShipment,
    TonerYield,
)
from app.services import orbit_advice

POLICIES = OrbitPolicy.__table__


def resolve(connection, device=None):
    """Nakłada reguły od ogólnych do urządzenia, a kolor po regule całego zakresu."""
    scopes = [("global", "0")]
    if device is not None:
        customer_id = device["data"].get("customer_id")
        if customer_id:
            scopes.append(("customer", str(customer_id)))
        scopes.append(("device", device["id"]))
    rows = [
        dict(row)
        for row in connection.execute(select(POLICIES)).mappings()
        if (row["scope"], row["scope_id"]) in scopes
    ]
    values = {color: dict(orbit_advice.DEFAULT_POLICY) for color in orbit_advice.COLORS}
    for scope in scopes:
        for color in ("all", *orbit_advice.COLORS):
            for row in rows:
                if (row["scope"], row["scope_id"]) == scope and row["color"] == color:
                    for target in orbit_advice.COLORS if color == "all" else (color,):
                        values[target].update(row["values"])
    return {"defaults": dict(orbit_advice.DEFAULT_POLICY), "effective": values, "overrides": rows}


def save(connection, scope, scope_id, color, values, actor_id):
    """Zastępuje pojedynczy zestaw nadpisań; pusty zestaw przywraca dziedziczenie."""
    from app.services.orbit import put

    key = dict(scope=scope, scope_id=scope_id, color=color)
    before = (
        connection.execute(
            select(POLICIES).where(
                and_(*(POLICIES.c[name] == value for name, value in key.items()))
            )
        )
        .mappings()
        .first()
    )
    put(
        connection,
        POLICIES,
        key,
        dict(values=values, updated_at=datetime.now(UTC), updated_by=actor_id),
    )
    return {"key": key, "before": dict(before["values"]) if before else {}, "after": values}


def pending_shipments(connection, device_id, excluded_orders):
    """Nie traktuje braku potwierdzenia doręczenia jako pewnej dostawy do klienta."""
    from app.models.shipping import ShippingTrackingParcel

    cases, items, shipments, catalog, tracking = (
        model.__table__
        for model in (
            ShippingCase,
            ShippingItem,
            ShippingShipment,
            TonerYield,
            ShippingTrackingParcel,
        )
    )
    rows = connection.execute(
        select(items.c.quantity, catalog.c.color, tracking.c.status_category)
        .select_from(
            cases.join(
                OrbitDevice.__table__, cases.c.firebird_machine_id == OrbitDevice.ms_machine_id
            )
            .join(items, items.c.shipping_case_id == cases.c.id)
            .join(shipments, shipments.c.shipping_case_id == cases.c.id)
            .join(catalog, items.c.firebird_warehouse_item_id == catalog.c.item_id)
            .outerjoin(
                tracking,
                and_(
                    tracking.c.waybill == shipments.c.tracking_number,
                    tracking.c.provider == shipments.c.provider,
                ),
            )
        )
        .where(OrbitDevice.id == device_id)
        .where(cases.c.firebird_order_table_id.not_in(excluded_orders) if excluded_orders else True)
        .where(
            shipments.c.status.in_(["label_ready", "handed_over", "closed", "reconcile_required"])
        )
    ).mappings()
    pending = {}
    for row in rows:
        if row["status_category"] in {"delivered", "returned", "cancelled"}:
            continue
        color = row["color"]
        pending[color] = pending.get(color, 0) + row["quantity"]
    return pending


def assess_drafts(connection, drafts):
    """Sumuje szkice dla urządzenia i koloru; nigdy nie zmienia zleceń ani dokumentów."""
    from app.services.orbit import device_detail, device_row

    grouped = {}
    excluded_orders = sorted({draft["order_table_id"] for draft in drafts})
    catalog = {
        row["item_id"]: dict(row)
        for row in connection.execute(select(TonerYield.__table__)).mappings()
    }
    seen = set()
    for draft in drafts:
        key = draft["order_table_id"]
        if key in seen:
            raise ValueError("Powtórzone zlecenie w ocenie wysyłki.")
        seen.add(key)
        entry = grouped.setdefault(draft["device_id"], {"requested": {}, "unknown": False})
        item_ids = set()
        for item in draft["items"]:
            if item["item_id"] in item_ids:
                raise ValueError("Powtórzona pozycja w zleceniu.")
            item_ids.add(item["item_id"])
            toner = catalog.get(item["item_id"])
            if toner is None or toner["color"] not in orbit_advice.COLORS:
                entry["unknown"] = True
                continue
            color = toner["color"]
            entry["requested"][color] = entry["requested"].get(color, 0) + item["quantity"]
    results = []
    for device_id, entry in grouped.items():
        try:
            device = device_row(connection, device_id)
        except LookupError:
            results.append(
                {
                    "device_id": device_id,
                    "status": "insufficient_data",
                    "items": [],
                    "blocking": False,
                }
            )
            continue
        policies = resolve(connection, device)["effective"]
        detail = device_detail(connection, device_id)
        result = orbit_advice.assessment(
            detail.get("toners", []),
            policies,
            entry["requested"],
            pending_shipments(connection, device_id, excluded_orders),
        )
        if device["status"] != "active" or entry["unknown"]:
            if result["status"] == "ok":
                result["status"] = "insufficient_data"
            result["note"] = (
                "Niepełne dopasowanie materiałów lub urządzenie poza aktywnym monitoringiem."
            )
        results.append({"device_id": device_id, **result})
    return {"devices": results, "blocking": False}


def assess_saved(connection, order_ids):
    """Ocenia zaakceptowane pozycje wielu zleceń, bez kolejnego odczytu Firebirda."""
    cases, items = ShippingCase.__table__, ShippingItem.__table__
    rows = connection.execute(
        select(
            cases.c.firebird_order_table_id,
            cases.c.firebird_machine_id,
            items.c.firebird_warehouse_item_id,
            items.c.quantity,
        )
        .join(items, items.c.shipping_case_id == cases.c.id)
        .where(cases.c.firebird_order_table_id.in_(order_ids))
    ).mappings()
    drafts = {}
    for row in rows:
        if not row["firebird_machine_id"]:
            continue
        identifier = row["firebird_order_table_id"]
        draft = drafts.setdefault(
            identifier,
            {
                "order_table_id": identifier,
                "device_id": f"ms:{row['firebird_machine_id']}",
                "items": [],
            },
        )
        draft["items"].append(
            {"item_id": row["firebird_warehouse_item_id"], "quantity": row["quantity"]}
        )
    result = assess_drafts(connection, list(drafts.values()))
    result["unmatched_orders"] = sorted(set(order_ids) - set(drafts))
    return result
