"""Scalanie dowodów tej samej przesyłki bez podwajania części i rozchodów."""

from collections import defaultdict
from copy import deepcopy

from app.services.telemetry.parsers import fingerprint


def consolidate(events):
    """Scala wyłącznie zgodną tożsamość urządzenia, zlecenia, kierunku i listu.

    Ilości pochodzą z pozycji Shipping; nagłówek MS jest dodatkowym dowodem.
    Ten sam list dla kilku urządzeń zachowuje oddzielne przypisania materiałów.
    """
    groups = defaultdict(list)
    untouched = []
    for event in events:
        data = event["data"]
        identity = (
            event["device_id"],
            data.get("order_id"),
            data.get("order_year"),
            data.get("direction"),
            str(data.get("tracking_number") or "").strip(),
        )
        if (
            event["kind"] == "shipment"
            and event["source"] in {"shipping_orbit", "ms_orbit"}
            and event["status"] == "confirmed"
            and all(identity)
        ):
            groups[identity].append(event)
        else:
            untouched.append(deepcopy(event))
    result = untouched
    evidence = [
        dict(event_id=event["id"], record_id=event["record_id"], role=event["source"], current=True)
        for event in untouched
    ]
    for identity, entries in groups.items():
        shipping = [event for event in entries if event["source"] == "shipping_orbit"]
        primary = deepcopy(sorted(shipping or entries, key=lambda event: event["id"])[0])
        primary["id"] = fingerprint(["orbit_shipment", *identity])
        items = {}
        conflicts = False
        for event in shipping:
            data = event["data"]
            key = data.get("item_row_id") or data.get("item_id")
            item = {
                name: data.get(name)
                for name in (
                    "item_id",
                    "item_row_id",
                    "item_name",
                    "color",
                    "quantity",
                    "model",
                )
            }
            if key in items and items[key] != item:
                conflicts = True
            items[key] = item
        for name in ("color", "quantity", "item_id", "item_name", "item_row_id"):
            primary["data"].pop(name, None)
        primary["data"]["items"] = list(items.values())
        primary["data"]["evidence_count"] = len(entries)
        if conflicts:
            primary["status"] = "conflict"
            primary["data"]["issues"] = ["conflicting_shipment_items"]
        result.append(primary)
        evidence.extend(
            dict(
                event_id=primary["id"],
                record_id=event["record_id"],
                role=event["source"],
                current=True,
            )
            for event in entries
        )
    return result, evidence
