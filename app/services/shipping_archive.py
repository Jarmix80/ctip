"""Niezmienny rejestr zakończonych wysyłek i dokumentów magazynowych."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AdminUser, ShippingCase, ShippingEvent, ShippingShipment
from app.services.shipping_firebird import shipping_document_mode
from app.services.shipping_tracking import tracking_for_waybills

WARSAW = ZoneInfo("Europe/Warsaw")
ARCHIVE_VERSION = 1


def normalize_shipping_archive_text(value: Any) -> str:
    """Normalizuje tekst wyszukiwarki, usuwając polskie znaki i separatory."""
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold().replace("ł", "l")
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _operator_name(user: AdminUser | None) -> str | None:
    if user is None:
        return None
    name = " ".join(
        value.strip() for value in (user.first_name, user.last_name) if value and value.strip()
    )
    return name or user.email


async def _load_operators(
    session: AsyncSession,
    operator_ids: set[int | None],
) -> dict[int, AdminUser]:
    ids = {int(value) for value in operator_ids if value is not None}
    if not ids:
        return {}
    users = (await session.execute(select(AdminUser).where(AdminUser.id.in_(ids)))).scalars()
    return {int(user.id): user for user in users}


def _operator_snapshot(
    user_id: int | None,
    users: dict[int, AdminUser],
    *,
    fallback_name: str | None = None,
) -> dict[str, Any] | None:
    if user_id is None and not fallback_name:
        return None
    user = users.get(int(user_id)) if user_id is not None else None
    return {
        "id": int(user_id) if user_id is not None else None,
        "name": _operator_name(user) or fallback_name or "Nieznany operator",
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _consolidation_snapshot(shipment: ShippingShipment) -> dict[str, Any] | None:
    request = shipment.provider_request if isinstance(shipment.provider_request, dict) else {}
    raw = request.get("ctip_consolidation")
    if not isinstance(raw, dict):
        return None
    order_table_ids = [
        int(value)
        for value in raw.get("order_table_ids", [])
        if isinstance(value, int) and value > 0
    ]
    if len(order_table_ids) < 2:
        return None
    return {
        "group_id": str(raw.get("group_id") or "") or None,
        "count": len(order_table_ids),
        "order_table_ids": order_table_ids,
        "order_numbers": [str(value) for value in raw.get("order_numbers", []) if value],
        "primary_order_table_id": int(raw.get("primary_order_table_id") or order_table_ids[0]),
    }


def _build_archive_snapshot(
    shipment: ShippingShipment,
    *,
    users: dict[int, AdminUser],
    closed_by: int | None,
    closed_at: datetime,
    closing_operator_name: str | None = None,
) -> dict[str, Any]:
    case = shipment.shipping_case
    source = dict(case.source_snapshot or {})
    address = dict(case.address_snapshot or {})
    document_mode = shipping_document_mode(
        order_kind=case.order_kind,
        invoice_required=case.invoice_required,
    )
    order_number = f"{case.firebird_order_id}/{case.firebird_order_year}"
    source_type = "mobile" if source.get("created_from_mobile_app") else "manual"
    items = [
        {
            "id": item.id,
            "warehouse_item_id": item.firebird_warehouse_item_id,
            "warehouse_id": item.warehouse_id,
            "index": item.item_index,
            "name": item.item_name,
            "unit": item.unit,
            "quantity": _json_value(item.quantity),
            "price_net": _json_value(item.price_net),
            "catalog_price_net": _json_value(item.catalog_price_net),
            "purchase_price_net": _json_value(item.purchase_price_net),
            "price_source": item.price_source,
            "vat_rate": _json_value(item.vat_rate),
            "negative_stock_authorized": bool(item.allow_negative_stock),
            "firebird_position_id": item.firebird_position_id,
        }
        for item in case.items
    ]
    return {
        "version": ARCHIVE_VERSION,
        "archived_at": closed_at.isoformat(),
        "order": {
            "order_table_id": case.firebird_order_table_id,
            "order_id": case.firebird_order_id,
            "order_year": case.firebird_order_year,
            "order_number": order_number,
            "order_date": source.get("order_date"),
            "order_kind": case.order_kind,
            "invoice_required": bool(case.invoice_required),
            "source": source_type,
            "problem": source.get("problem"),
            "source_snapshot": source,
        },
        "recipient": address,
        "device": {
            "machine_id": case.firebird_machine_id,
            "model_id": case.firebird_model_id,
            "brand": source.get("device_brand") or source.get("machine_brand"),
            "model": source.get("device_model") or source.get("machine_model"),
            "serial": source.get("device_serial"),
            "asset_number": source.get("device_asset_number"),
            "location": case.location_text_snapshot,
        },
        "shipment": {
            "id": shipment.id,
            "provider": shipment.provider,
            "provider_mode": shipment.provider_mode,
            "provider_shipment_id": shipment.provider_shipment_id,
            "tracking_number": shipment.tracking_number,
            "weight_kg": _json_value(case.weight_kg),
            "label_available": bool(shipment.label_content),
            "created_at": _json_value(shipment.created_at),
            "handed_over_at": _json_value(shipment.handed_over_at),
            "closed_at": closed_at.isoformat(),
            "consolidation": _consolidation_snapshot(shipment),
        },
        "documents": {
            "mode": document_mode,
            "rw": {
                "id": shipment.firebird_rw_id,
                "number": shipment.firebird_rw_number,
            },
            "wz": {
                "id": shipment.firebird_wz_id,
                "number": shipment.firebird_wz_number,
            },
            "invoice": {
                "id": shipment.firebird_invoice_id,
                "number": shipment.firebird_invoice_number,
            },
        },
        "items": items,
        "operators": {
            "reviewed": _operator_snapshot(case.reviewed_by, users),
            "label_created": _operator_snapshot(shipment.created_by, users),
            "closed": _operator_snapshot(
                closed_by,
                users,
                fallback_name=closing_operator_name,
            ),
        },
    }


def _search_values(value: Any) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, dict):
        return [item for child in value.values() for item in _search_values(child)]
    if isinstance(value, (list, tuple, set)):
        return [item for child in value for item in _search_values(child)]
    return [str(value)]


async def archive_shipping_shipment(
    session: AsyncSession,
    *,
    shipment: ShippingShipment,
    closed_by: int,
    closed_at: datetime,
    closing_operator_name: str | None = None,
) -> dict[str, Any]:
    """Zapisuje końcowy snapshot przesyłki w tej samej transakcji co zamknięcie."""
    case = shipment.shipping_case
    users = await _load_operators(
        session,
        {case.reviewed_by, shipment.created_by, closed_by},
    )
    snapshot = _build_archive_snapshot(
        shipment,
        users=users,
        closed_by=closed_by,
        closed_at=closed_at,
        closing_operator_name=closing_operator_name,
    )
    shipment.closed_by = closed_by
    shipment.archive_snapshot = snapshot
    shipment.archive_search_text = normalize_shipping_archive_text(
        " ".join(_search_values(snapshot))
    )
    return snapshot


def _archive_item(
    shipment: ShippingShipment,
    case: ShippingCase,
    *,
    users: dict[int, AdminUser],
) -> dict[str, Any]:
    snapshot = shipment.archive_snapshot or _build_archive_snapshot(
        shipment,
        users=users,
        closed_by=shipment.closed_by,
        closed_at=shipment.closed_at or shipment.updated_at,
    )
    items = list(snapshot.get("items") or [])
    quantity = sum(float(item.get("quantity") or 0) for item in items)
    return {
        "shipment_id": shipment.id,
        "order_table_id": case.firebird_order_table_id,
        "order_number": snapshot.get("order", {}).get("order_number"),
        "closed_at": snapshot.get("archived_at"),
        "company_name": snapshot.get("recipient", {}).get("company_name"),
        "city": snapshot.get("recipient", {}).get("city"),
        "device": snapshot.get("device"),
        "source": snapshot.get("order", {}).get("source"),
        "problem": snapshot.get("order", {}).get("problem"),
        "operators": snapshot.get("operators") or {},
        "item_count": len(items),
        "quantity": quantity,
        "items": items[:3],
        "documents": snapshot.get("documents") or {},
        "shipment": snapshot.get("shipment") or {},
    }


async def _archive_operator_options(session: AsyncSession) -> list[dict[str, Any]]:
    reviewed_ids = (
        select(ShippingCase.reviewed_by.label("user_id"))
        .join(ShippingShipment, ShippingShipment.shipping_case_id == ShippingCase.id)
        .where(ShippingShipment.status == "closed", ShippingCase.reviewed_by.is_not(None))
    )
    creator_ids = select(ShippingShipment.created_by.label("user_id")).where(
        ShippingShipment.status == "closed",
        ShippingShipment.created_by.is_not(None),
    )
    closer_ids = select(ShippingShipment.closed_by.label("user_id")).where(
        ShippingShipment.status == "closed",
        ShippingShipment.closed_by.is_not(None),
    )
    operator_ids = reviewed_ids.union(creator_ids, closer_ids).subquery()
    users = list(
        (
            await session.execute(
                select(AdminUser)
                .join(operator_ids, operator_ids.c.user_id == AdminUser.id)
                .order_by(AdminUser.last_name, AdminUser.first_name, AdminUser.email)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": user.id, "name": _operator_name(user)} for user in users]


def _archive_date_bounds(
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


async def list_shipping_archive(
    session: AsyncSession,
    *,
    query: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    operator_id: int | None = None,
    document_type: Literal["rw", "wz", "invoice"] | None = None,
    source: Literal["mobile", "manual"] | None = None,
    provider_mode: Literal["production", "demo", "mock", "manual"] | None = None,
    consolidated: bool | None = None,
    sort: Literal["newest", "oldest", "order", "client"] = "newest",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Zwraca stronicowany rejestr archiwalny z filtrami wykonywanymi w PostgreSQL."""
    statement = (
        select(ShippingShipment, ShippingCase)
        .join(ShippingCase, ShippingCase.id == ShippingShipment.shipping_case_id)
        .where(ShippingShipment.status == "closed")
    )
    start, end = _archive_date_bounds(date_from, date_to)
    if start:
        statement = statement.where(ShippingShipment.closed_at >= start)
    if end:
        statement = statement.where(ShippingShipment.closed_at < end)
    if operator_id:
        statement = statement.where(
            or_(
                ShippingCase.reviewed_by == operator_id,
                ShippingShipment.created_by == operator_id,
                ShippingShipment.closed_by == operator_id,
            )
        )
    document_columns = {
        "rw": ShippingShipment.firebird_rw_number,
        "wz": ShippingShipment.firebird_wz_number,
        "invoice": ShippingShipment.firebird_invoice_number,
    }
    if document_type:
        statement = statement.where(document_columns[document_type].is_not(None))
    if source:
        mobile_source = ShippingCase.source_snapshot["created_from_mobile_app"].as_boolean()
        statement = statement.where(
            mobile_source.is_(True)
            if source == "mobile"
            else or_(mobile_source.is_(False), mobile_source.is_(None))
        )
    if provider_mode:
        statement = statement.where(ShippingShipment.provider_mode == provider_mode)
    if consolidated is not None:
        consolidation_type = func.json_typeof(
            ShippingShipment.provider_request["ctip_consolidation"]
        )
        has_consolidation = consolidation_type == "object"
        statement = statement.where(
            has_consolidation
            if consolidated
            else or_(consolidation_type.is_(None), consolidation_type != "object")
        )
    terms = normalize_shipping_archive_text(query).split()[:12]
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        direct_match = ShippingShipment.archive_search_text.ilike(
            f"%{escaped}%",
            escape="\\",
        )
        condition = direct_match
        if len(term) >= 3:
            similarity_threshold = 0.55 if len(term) >= 4 else 0.72
            condition = or_(
                direct_match,
                func.word_similarity(term, ShippingShipment.archive_search_text)
                >= similarity_threshold,
            )
        statement = statement.where(condition)

    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = int((await session.execute(count_statement)).scalar_one())
    company_name = cast(ShippingCase.address_snapshot["company_name"].as_string(), Text)
    order_by = {
        "newest": (ShippingShipment.closed_at.desc(), ShippingShipment.id.desc()),
        "oldest": (ShippingShipment.closed_at.asc(), ShippingShipment.id.asc()),
        "order": (
            ShippingCase.firebird_order_year.desc(),
            ShippingCase.firebird_order_id.desc(),
        ),
        "client": (func.lower(company_name).asc(), ShippingShipment.closed_at.desc()),
    }[sort]
    rows = (
        await session.execute(
            statement.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    operator_ids = {
        value
        for shipment, case in rows
        for value in (case.reviewed_by, shipment.created_by, shipment.closed_by)
    }
    users = await _load_operators(session, operator_ids)
    items = [_archive_item(shipment, case, users=users) for shipment, case in rows]
    tracking = await tracking_for_waybills(
        session,
        {
            str(item.get("shipment", {}).get("tracking_number"))
            for item in items
            if item.get("shipment", {}).get("tracking_number")
        },
    )
    for item in items:
        waybill = item.get("shipment", {}).get("tracking_number")
        item["dpd_tracking"] = tracking.get(str(waybill)) if waybill else None
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "filters": {"operators": await _archive_operator_options(session)},
    }


async def get_shipping_archive_detail(
    session: AsyncSession,
    *,
    order_table_id: int,
) -> dict[str, Any] | None:
    """Zwraca pełny snapshot i dziennik zdarzeń zakończonego zlecenia."""
    shipment = (
        await session.execute(
            select(ShippingShipment)
            .join(ShippingCase, ShippingCase.id == ShippingShipment.shipping_case_id)
            .options(selectinload(ShippingShipment.shipping_case).selectinload(ShippingCase.items))
            .where(
                ShippingCase.firebird_order_table_id == int(order_table_id),
                ShippingShipment.status == "closed",
            )
        )
    ).scalar_one_or_none()
    if shipment is None:
        return None
    events = list(
        (
            await session.execute(
                select(ShippingEvent)
                .where(ShippingEvent.shipping_case_id == shipment.shipping_case_id)
                .order_by(ShippingEvent.created_at.asc(), ShippingEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    operator_ids = {
        shipment.shipping_case.reviewed_by,
        shipment.created_by,
        shipment.closed_by,
        *(event.created_by for event in events),
    }
    users = await _load_operators(session, operator_ids)
    snapshot = shipment.archive_snapshot or _build_archive_snapshot(
        shipment,
        users=users,
        closed_by=shipment.closed_by,
        closed_at=shipment.closed_at or shipment.updated_at,
    )
    tracking = await tracking_for_waybills(
        session,
        {str(shipment.tracking_number)} if shipment.tracking_number else set(),
    )
    return {
        "shipment_id": shipment.id,
        "order_table_id": shipment.shipping_case.firebird_order_table_id,
        "snapshot": snapshot,
        "label_url": (
            f"/admin/shipping/shipments/{shipment.id}/label" if shipment.label_content else None
        ),
        "dpd_tracking": (
            tracking.get(str(shipment.tracking_number)) if shipment.tracking_number else None
        ),
        "events": [
            {
                "id": event.id,
                "type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
                "operator": _operator_snapshot(event.created_by, users),
            }
            for event in events
        ],
    }


__all__ = [
    "archive_shipping_shipment",
    "get_shipping_archive_detail",
    "list_shipping_archive",
    "normalize_shipping_archive_text",
]
