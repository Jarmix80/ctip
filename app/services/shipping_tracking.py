"""Zapytania i serializacja rejestru statusów przesyłek."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Text, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ShippingCase,
    ShippingShipment,
    ShippingTrackingEvent,
    ShippingTrackingParcel,
)
from app.services.dpd_infoservices_sync import (
    dpd_event_group,
    latest_dpd_info_sync_status,
)

WARSAW = ZoneInfo("Europe/Warsaw")


def _normalize_search(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold().replace("ł", "l")
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())


def _normalized_sql_text(value: Any) -> Any:
    """Normalizuje polskie znaki w wyrażeniu SQL bez wymogu rozszerzenia `unaccent`."""
    expression = func.lower(cast(value, Text))
    for source, target in (
        ("ą", "a"),
        ("ć", "c"),
        ("ę", "e"),
        ("ł", "l"),
        ("ń", "n"),
        ("ó", "o"),
        ("ś", "s"),
        ("ź", "z"),
        ("ż", "z"),
        ("Ą", "a"),
        ("Ć", "c"),
        ("Ę", "e"),
        ("Ł", "l"),
        ("Ń", "n"),
        ("Ó", "o"),
        ("Ś", "s"),
        ("Ź", "z"),
        ("Ż", "z"),
    ):
        expression = func.replace(expression, source, target)
    return expression


def _date_bounds(
    date_from: date | None,
    date_to: date | None,
) -> tuple[datetime | None, datetime | None]:
    start = (
        datetime.combine(date_from, time.min, tzinfo=WARSAW).astimezone(UTC) if date_from else None
    )
    end = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=WARSAW).astimezone(UTC)
        if date_to
        else None
    )
    return start, end


def serialize_tracking_parcel(parcel: ShippingTrackingParcel) -> dict[str, Any]:
    """Zwraca bieżący stan listu bez danych technicznych synchronizatora."""
    return {
        "id": parcel.id,
        "provider": parcel.provider,
        "waybill": parcel.waybill,
        "business_code": parcel.latest_business_code,
        "description": parcel.latest_description,
        "event_time": parcel.latest_event_time.isoformat() if parcel.latest_event_time else None,
        "depot": parcel.latest_depot,
        "depot_name": parcel.latest_depot_name,
        "country": parcel.latest_country,
        "replacement_waybill": parcel.replacement_waybill,
        "category": parcel.status_category,
        "status_label": parcel.status_label,
        "is_terminal": parcel.is_terminal,
        "requires_attention": parcel.requires_attention,
        "first_event_at": parcel.first_event_at.isoformat() if parcel.first_event_at else None,
        "last_event_at": parcel.last_event_at.isoformat() if parcel.last_event_at else None,
        "last_synced_at": parcel.last_synced_at.isoformat(),
        "detail_url": f"/shipping?view=tracking&waybill={parcel.waybill}",
    }


def _shipment_link_payload(shipment: ShippingShipment, case: ShippingCase) -> dict[str, Any]:
    snapshot = case.address_snapshot or {}
    return {
        "shipment_id": shipment.id,
        "order_table_id": case.firebird_order_table_id,
        "order_number": f"{case.firebird_order_id}/{case.firebird_order_year}",
        "case_status": case.status,
        "shipment_status": shipment.status,
        "company_name": snapshot.get("company_name"),
        "city": snapshot.get("city"),
        "is_archived": shipment.status == "closed",
        "target_url": (
            f"/shipping?view=archive&order={case.firebird_order_table_id}"
            if shipment.status == "closed"
            else f"/shipping?view=dispatch&order={case.firebird_order_table_id}"
        ),
    }


async def _links_for_waybills(
    session: AsyncSession,
    waybills: set[str],
) -> dict[str, list[dict[str, Any]]]:
    if not waybills:
        return {}
    rows = (
        await session.execute(
            select(ShippingShipment, ShippingCase)
            .join(ShippingCase, ShippingCase.id == ShippingShipment.shipping_case_id)
            .where(
                ShippingShipment.provider == "dpd",
                ShippingShipment.provider_mode == "production",
                ShippingShipment.tracking_number.in_(waybills),
            )
            .order_by(
                ShippingCase.firebird_order_year.desc(), ShippingCase.firebird_order_id.desc()
            )
        )
    ).all()
    result: dict[str, list[dict[str, Any]]] = {}
    for shipment, case in rows:
        if not shipment.tracking_number:
            continue
        result.setdefault(shipment.tracking_number, []).append(
            _shipment_link_payload(shipment, case)
        )
    return result


def _linked_exists() -> Any:
    return exists(
        select(ShippingShipment.id).where(
            ShippingShipment.provider == "dpd",
            ShippingShipment.provider_mode == "production",
            ShippingShipment.tracking_number == ShippingTrackingParcel.waybill,
        )
    )


async def tracking_for_waybills(
    session: AsyncSession,
    waybills: set[str],
) -> dict[str, dict[str, Any]]:
    """Zwraca statusy do wzbogacenia kolejki i archiwum jednym zapytaniem."""
    normalized = {str(value).strip() for value in waybills if str(value or "").strip()}
    if not normalized:
        return {}
    parcels = list(
        (
            await session.execute(
                select(ShippingTrackingParcel).where(
                    ShippingTrackingParcel.provider == "dpd",
                    ShippingTrackingParcel.waybill.in_(normalized),
                )
            )
        )
        .scalars()
        .all()
    )
    return {parcel.waybill: serialize_tracking_parcel(parcel) for parcel in parcels}


async def list_shipping_tracking(
    session: AsyncSession,
    *,
    query: str | None = None,
    category: str | None = None,
    linked: bool | None = None,
    terminal: bool | None = None,
    attention: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: Literal["newest", "oldest", "waybill", "status"] = "newest",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Zwraca stronicowaną kartotekę całego kanału InfoServices."""
    statement = select(ShippingTrackingParcel)
    if category:
        statement = statement.where(ShippingTrackingParcel.status_category == category)
    if linked is not None:
        linked_condition = _linked_exists()
        statement = statement.where(linked_condition if linked else ~linked_condition)
    if terminal is not None:
        statement = statement.where(ShippingTrackingParcel.is_terminal.is_(terminal))
    if attention is not None:
        statement = statement.where(ShippingTrackingParcel.requires_attention.is_(attention))
    start, end = _date_bounds(date_from, date_to)
    if start:
        statement = statement.where(ShippingTrackingParcel.latest_event_time >= start)
    if end:
        statement = statement.where(ShippingTrackingParcel.latest_event_time < end)
    terms = _normalize_search(query).split()[:10]
    for term in terms:
        needle = term[:-1] if term.isalpha() and len(term) >= 6 else term
        pattern = f"%{needle}%"
        event_match = exists(
            select(ShippingTrackingEvent.id).where(
                ShippingTrackingEvent.parcel_id == ShippingTrackingParcel.id,
                or_(
                    _normalized_sql_text(ShippingTrackingEvent.description).like(pattern),
                    _normalized_sql_text(ShippingTrackingEvent.package_reference).like(pattern),
                    _normalized_sql_text(ShippingTrackingEvent.parcel_reference).like(pattern),
                ),
            )
        )
        linked_match = exists(
            select(ShippingShipment.id)
            .join(ShippingCase, ShippingCase.id == ShippingShipment.shipping_case_id)
            .where(
                ShippingShipment.provider == "dpd",
                ShippingShipment.provider_mode == "production",
                ShippingShipment.tracking_number == ShippingTrackingParcel.waybill,
                or_(
                    cast(ShippingCase.firebird_order_id, Text).like(pattern),
                    _normalized_sql_text(
                        ShippingCase.address_snapshot["company_name"].as_string()
                    ).like(pattern),
                ),
            )
        )
        statement = statement.where(
            or_(
                _normalized_sql_text(ShippingTrackingParcel.waybill).like(pattern),
                _normalized_sql_text(ShippingTrackingParcel.latest_description).like(pattern),
                _normalized_sql_text(ShippingTrackingParcel.latest_business_code).like(pattern),
                event_match,
                linked_match,
            )
        )
    total = int(
        (
            await session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
        )
        or 0
    )
    order_by = {
        "newest": (
            ShippingTrackingParcel.latest_event_time.desc().nullslast(),
            ShippingTrackingParcel.id.desc(),
        ),
        "oldest": (
            ShippingTrackingParcel.latest_event_time.asc().nullsfirst(),
            ShippingTrackingParcel.id.asc(),
        ),
        "waybill": (ShippingTrackingParcel.waybill.asc(),),
        "status": (
            ShippingTrackingParcel.status_category.asc(),
            ShippingTrackingParcel.latest_event_time.desc().nullslast(),
        ),
    }[sort]
    parcels = list(
        (
            await session.execute(
                statement.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    links = await _links_for_waybills(session, {parcel.waybill for parcel in parcels})
    grouped_counts = {
        str(name): int(count)
        for name, count in (
            await session.execute(
                select(
                    ShippingTrackingParcel.status_category,
                    func.count(ShippingTrackingParcel.id),
                ).group_by(ShippingTrackingParcel.status_category)
            )
        ).all()
    }
    active_count = int(
        (
            await session.scalar(
                select(func.count(ShippingTrackingParcel.id)).where(
                    ShippingTrackingParcel.is_terminal.is_(False)
                )
            )
        )
        or 0
    )
    items = []
    for parcel in parcels:
        item = serialize_tracking_parcel(parcel)
        item["links"] = links.get(parcel.waybill, [])
        item["linked"] = bool(item["links"])
        items.append(item)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "summary": {
            "total": sum(grouped_counts.values()),
            "active": active_count,
            "attention": int(
                (
                    await session.scalar(
                        select(func.count(ShippingTrackingParcel.id)).where(
                            ShippingTrackingParcel.requires_attention.is_(True)
                        )
                    )
                )
                or 0
            ),
            "categories": grouped_counts,
        },
        "sync": await latest_dpd_info_sync_status(session),
    }


async def get_shipping_tracking_detail(
    session: AsyncSession,
    *,
    waybill: str,
) -> dict[str, Any] | None:
    """Zwraca pełną historię DPD i wszystkie powiązane zlecenia CTIP."""
    normalized = str(waybill or "").strip()
    parcel = (
        await session.execute(
            select(ShippingTrackingParcel).where(
                ShippingTrackingParcel.provider == "dpd",
                ShippingTrackingParcel.waybill == normalized,
            )
        )
    ).scalar_one_or_none()
    if parcel is None:
        return None
    events = list(
        (
            await session.execute(
                select(ShippingTrackingEvent)
                .where(
                    ShippingTrackingEvent.parcel_id == parcel.id,
                    or_(
                        ShippingTrackingEvent.operation_type == "CANCEL",
                        ShippingTrackingEvent.canonical_event_id.is_(None),
                    ),
                )
                .order_by(
                    ShippingTrackingEvent.event_time.asc().nullsfirst(),
                    ShippingTrackingEvent.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    links = await _links_for_waybills(session, {normalized})
    replacement = None
    if parcel.replacement_waybill:
        replacement_row = (
            await session.execute(
                select(ShippingTrackingParcel).where(
                    ShippingTrackingParcel.provider == "dpd",
                    ShippingTrackingParcel.waybill == parcel.replacement_waybill,
                )
            )
        ).scalar_one_or_none()
        if replacement_row:
            replacement = serialize_tracking_parcel(replacement_row)
    return {
        "parcel": serialize_tracking_parcel(parcel),
        "replacement": replacement,
        "links": links.get(normalized, []),
        "events": [
            {
                "id": event.id,
                "business_code": event.business_code,
                "group": dpd_event_group(event.business_code),
                "description": event.description,
                "event_time": event.event_time.isoformat() if event.event_time else None,
                "depot": event.depot,
                "depot_name": event.depot_name,
                "country": event.country,
                "package_reference": event.package_reference,
                "parcel_reference": event.parcel_reference,
                "operation_type": event.operation_type,
                "event_data": event.event_data,
                "is_cancelled": event.is_cancelled,
                "cancelled_at": event.cancelled_at.isoformat() if event.cancelled_at else None,
            }
            for event in events
        ],
        "sync": await latest_dpd_info_sync_status(session),
    }


__all__ = [
    "get_shipping_tracking_detail",
    "list_shipping_tracking",
    "serialize_tracking_parcel",
    "tracking_for_waybills",
]
