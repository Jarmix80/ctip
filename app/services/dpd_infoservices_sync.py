"""Idempotentna synchronizacja statusów DPD InfoServices z PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import (
    ShippingTrackingEvent,
    ShippingTrackingParcel,
    ShippingTrackingSyncRun,
)
from app.services.dpd_infoservices import DpdInfoEvent, DpdInfoServicesClient

logger = logging.getLogger(__name__)
_scheduler_task: asyncio.Task[None] | None = None
_scheduler_stop_event: asyncio.Event | None = None
_process_lock = asyncio.Lock()
_POSTGRES_LOCK_KEY = 742_083_051
_REPLACEMENT_CODES = {"230402", "230403", "230408"}
_WAYBILL_PATTERN = re.compile(r"^[0-9A-Z]{10,20}$", re.IGNORECASE)

TrackingCategory = Literal[
    "registered",
    "in_transit",
    "out_for_delivery",
    "pickup_ready",
    "delivered",
    "undelivered",
    "redirected",
    "returning",
    "critical",
    "other",
]


class DpdInfoSyncBusyError(RuntimeError):
    """Synchronizacja jest już wykonywana przez inny proces lub operatora."""


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    path = Path(__file__).with_name("dpd_infoservices_catalog.json")
    return json.loads(path.read_text(encoding="utf-8"))


def dpd_event_group(business_code: str | None) -> str:
    """Zwraca oficjalną grupę kodu albo pustą wartość dla kodu nieznanego."""
    return str(_catalog().get("groups", {}).get(str(business_code or ""), "")).strip()


def is_dpd_pickup_confirmation(
    business_code: str | None,
    description: str | None,
) -> bool:
    """Rozpoznaje zdarzenie potwierdzające fizyczne przejęcie paczki przez DPD."""
    group = dpd_event_group(business_code)
    if group in {"Nadanie", "Przyjęta do Oddziału"}:
        return True
    normalized = unicodedata.normalize("NFKD", (description or "").casefold()).replace("ł", "l")
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return "odebrana przez kuriera" in normalized


def _description_category(description: str) -> TrackingCategory:
    normalized = unicodedata.normalize("NFKD", description.casefold()).replace("ł", "l")
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    if any(value in normalized for value in ("zagub", "utracon", "skradz")):
        return "critical"
    if "uszkodz" in normalized:
        return "critical"
    if "zwrot" in normalized:
        return "returning"
    if any(
        value in normalized
        for value in (
            "niedorecz",
            "odmow",
            "nieobecn",
            "pusty podjazd",
            "niezrealiz",
            "bledny adres",
            "brak przesylki u nadawcy",
            "niegotow",
            "rezygnac",
        )
    ):
        return "undelivered"
    if any(value in normalized for value in ("przekier", "przeadres", "nowy termin")):
        return "redirected"
    if "gotowa do odbioru" in normalized:
        return "pickup_ready"
    if "w doreczeniu" in normalized or "wydanie przesylki do doreczenia" in normalized:
        return "out_for_delivery"
    if "dorecz" in normalized or "odebrana w punkcie" in normalized:
        return "delivered"
    if any(
        value in normalized
        for value in ("odebrana przez kuriera", "przyjec", "sortown", "przepakow")
    ):
        return "in_transit"
    if "zarejestrowano" in normalized or "gotowa do nadania" in normalized:
        return "registered"
    return "other"


def classify_dpd_event(
    business_code: str | None,
    description: str | None,
) -> dict[str, Any]:
    """Nadaje zdarzeniu stabilną kategorię, kolor i flagę operacyjną."""
    code = str(business_code or "").strip()
    group = dpd_event_group(code)
    group_categories: dict[str, TrackingCategory] = {
        "Gotowa do nadania": "registered",
        "Nadanie": "in_transit",
        "Przyjęta do Oddziału": "in_transit",
        "Realizacja zlecenia": "in_transit",
        "Odprawa celna": "in_transit",
        "W Doręczeniu": "out_for_delivery",
        "Gotowa do odbioru w DPD Pickup": "pickup_ready",
        "Odbiór własny": "pickup_ready",
        "Odebrana w Punkcie": "delivered",
        "Doręczona": "delivered",
        "Niedoręczone": "undelivered",
        "Upłynął termin odbioru w Punkcie": "undelivered",
        "Przekierowana": "redirected",
        "Umówiono późniejszy termin doręczenia": "redirected",
        "Zwrot": "returning",
    }
    description_category = _description_category(description or "")
    if code == "230309" or description_category == "critical":
        category: TrackingCategory = "critical"
    elif code == "230402":
        category = "redirected"
    elif code in {"230403", "230408"}:
        category = "returning"
    elif group and group != "Realizacja zlecenia":
        category = group_categories.get(group) or description_category
    else:
        category = (
            description_category
            if description_category != "other"
            else (group_categories.get(group) or "other")
        )
    labels = {
        "registered": "Zarejestrowana",
        "in_transit": "W drodze",
        "out_for_delivery": "W doręczeniu",
        "pickup_ready": "Gotowa do odbioru",
        "delivered": "Doręczona",
        "undelivered": "Niedoręczona",
        "redirected": "Przekierowana",
        "returning": "Zwrot",
        "critical": "Problem krytyczny",
        "other": "Inny status DPD",
    }
    terminal = code in set(_catalog().get("terminal_codes", []))
    return {
        "category": category,
        "label": labels[category],
        "terminal": terminal,
        "requires_attention": category in {"undelivered", "returning", "critical"},
        "informational": group == "Powiadomienia",
        "group": group or None,
    }


def _event_key(channel: str, event: DpdInfoEvent) -> str:
    operation = str(event.operation_type or "INSERT").upper()
    object_id = str(event.object_id or "").strip()
    if object_id:
        payload = f"{operation}|object:{object_id}"
    else:
        event_payload = event.as_dict()
        event_payload.pop("event_id", None)
        payload = json.dumps(
            event_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return hashlib.sha256(f"{channel}|{payload}".encode()).hexdigest()


def _semantic_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _semantic_time(value: datetime | None) -> str:
    if value is None:
        return ""
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds")


def _semantic_event_data(values: list[dict[str, Any]] | tuple[Any, ...] | None) -> list[dict]:
    normalized = []
    for value in values or []:
        payload = value.as_dict() if hasattr(value, "as_dict") else dict(value or {})
        normalized.append(
            {
                "code": _semantic_text(payload.get("code")),
                "value": _semantic_text(payload.get("value")),
            }
        )
    return sorted(
        normalized,
        key=lambda item: (item["code"], item["value"]),
    )


def dpd_semantic_event_key(
    *,
    source_event_key: str,
    operation_type: str,
    waybill: str | None,
    business_code: str | None,
    description: str | None,
    event_time: datetime | None,
    depot: str | None,
    depot_name: str | None,
    country: str | None,
    package_reference: str | None,
    parcel_reference: str | None,
    event_data: list[dict[str, Any]] | tuple[Any, ...] | None,
) -> str:
    """Wyznacza tożsamość logiczną niezależną od metody SOAP i identyfikatorów DPD."""
    normalized_waybill = str(waybill or "").strip().upper()
    if str(operation_type or "INSERT").upper() != "INSERT" or not (
        normalized_waybill and event_time
    ):
        return source_event_key
    payload = {
        "waybill": normalized_waybill,
        "business_code": _semantic_text(business_code),
        "event_time": _semantic_time(event_time),
        "event_data": _semantic_event_data(event_data),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _semantic_key_for_event(source_event_key: str, event: DpdInfoEvent) -> str:
    return dpd_semantic_event_key(
        source_event_key=source_event_key,
        operation_type=event.operation_type,
        waybill=event.waybill,
        business_code=event.business_code,
        description=event.description,
        event_time=event.event_time,
        depot=event.depot,
        depot_name=event.depot_name,
        country=event.country,
        package_reference=event.package_reference,
        parcel_reference=event.parcel_reference,
        event_data=event.event_data,
    )


def _replacement_waybill(event: ShippingTrackingEvent) -> str | None:
    if event.business_code not in _REPLACEMENT_CODES:
        return None
    for value in event.event_data or []:
        candidate = str((value or {}).get("value") or "").strip().upper()
        if candidate != str(event.waybill or "").upper() and _WAYBILL_PATTERN.fullmatch(candidate):
            return candidate
    return None


async def _parcel_for_waybill(
    session: AsyncSession,
    *,
    waybill: str,
    channel: str,
) -> ShippingTrackingParcel:
    parcel = (
        await session.execute(
            select(ShippingTrackingParcel).where(
                ShippingTrackingParcel.provider == "dpd",
                ShippingTrackingParcel.waybill == waybill,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if parcel is None:
        parcel = ShippingTrackingParcel(
            provider="dpd",
            waybill=waybill,
            source_channel=channel,
            status_category="other",
            status_label="Inny status DPD",
            last_synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(parcel)
        await session.flush()
    else:
        parcel.source_channel = channel
        parcel.last_synced_at = now
        parcel.updated_at = now
    return parcel


async def _parcel_for_cancellation(
    session: AsyncSession,
    event: DpdInfoEvent,
) -> ShippingTrackingParcel | None:
    if event.waybill:
        return (
            await session.execute(
                select(ShippingTrackingParcel).where(
                    ShippingTrackingParcel.provider == "dpd",
                    ShippingTrackingParcel.waybill == event.waybill,
                )
            )
        ).scalar_one_or_none()
    if not event.object_id:
        return None
    parcel_id = (
        (
            await session.execute(
                select(ShippingTrackingEvent.parcel_id)
                .where(
                    ShippingTrackingEvent.object_id == event.object_id,
                    ShippingTrackingEvent.operation_type == "INSERT",
                )
                .order_by(ShippingTrackingEvent.id.desc())
            )
        )
        .scalars()
        .first()
    )
    return await session.get(ShippingTrackingParcel, parcel_id) if parcel_id else None


async def recompute_dpd_tracking_parcel(session: AsyncSession, parcel_id: int) -> None:
    """Przelicza bieżący stan listu wyłącznie ze zdarzeń kanonicznych."""
    parcel = await session.get(ShippingTrackingParcel, parcel_id)
    if parcel is None:
        return
    events = list(
        (
            await session.execute(
                select(ShippingTrackingEvent)
                .where(
                    ShippingTrackingEvent.parcel_id == parcel_id,
                    ShippingTrackingEvent.operation_type == "INSERT",
                    ShippingTrackingEvent.is_cancelled.is_(False),
                    ShippingTrackingEvent.canonical_event_id.is_(None),
                )
                .order_by(
                    ShippingTrackingEvent.event_time.desc().nullslast(),
                    ShippingTrackingEvent.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not events:
        parcel.latest_business_code = None
        parcel.latest_description = None
        parcel.latest_event_time = None
        parcel.latest_depot = None
        parcel.latest_depot_name = None
        parcel.latest_country = None
        parcel.replacement_waybill = None
        parcel.status_category = "other"
        parcel.status_label = "Inny status DPD"
        parcel.is_terminal = False
        parcel.requires_attention = False
        parcel.first_event_at = None
        parcel.last_event_at = None
        return
    current = next(
        (
            event
            for event in events
            if not classify_dpd_event(event.business_code, event.description)["informational"]
        ),
        events[0],
    )
    classification = classify_dpd_event(current.business_code, current.description)
    event_times = [event.event_time for event in events if event.event_time]
    parcel.latest_business_code = current.business_code
    parcel.latest_description = current.description
    parcel.latest_event_time = current.event_time
    parcel.latest_depot = current.depot
    parcel.latest_depot_name = current.raw_payload.get("depot_name")
    parcel.latest_country = current.country
    parcel.replacement_waybill = next(
        (replacement for event in events if (replacement := _replacement_waybill(event))),
        None,
    )
    parcel.status_category = classification["category"]
    parcel.status_label = classification["label"]
    parcel.is_terminal = bool(classification["terminal"])
    parcel.requires_attention = bool(classification["requires_attention"])
    parcel.first_event_at = min(event_times) if event_times else None
    parcel.last_event_at = max(event_times) if event_times else None
    parcel.updated_at = datetime.now(UTC)


async def persist_dpd_info_events(
    session: AsyncSession,
    *,
    events: tuple[DpdInfoEvent, ...],
    channel: str,
) -> dict[str, Any]:
    """Zapisuje techniczną historię i grupuje logiczne duplikaty zdarzeń DPD."""
    inserted = 0
    duplicates = 0
    cancelled = 0
    affected_parcels: set[int] = set()
    ordered_events = sorted(events, key=lambda value: value.operation_type == "CANCEL")
    for event in ordered_events:
        source_key = _event_key(channel, event)
        existing = (
            await session.execute(
                select(ShippingTrackingEvent.id).where(
                    ShippingTrackingEvent.source_event_key == source_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        semantic_key = _semantic_key_for_event(source_key, event)
        parcel = None
        if event.operation_type == "INSERT" and event.waybill:
            parcel = await _parcel_for_waybill(session, waybill=event.waybill, channel=channel)
        elif event.operation_type == "CANCEL":
            parcel = await _parcel_for_cancellation(session, event)
        raw = event.as_dict()
        canonical = None
        if event.operation_type == "INSERT" and event.waybill:
            canonical = (
                (
                    await session.execute(
                        select(ShippingTrackingEvent)
                        .where(
                            ShippingTrackingEvent.operation_type == "INSERT",
                            ShippingTrackingEvent.semantic_event_key == semantic_key,
                            ShippingTrackingEvent.canonical_event_id.is_(None),
                        )
                        .order_by(ShippingTrackingEvent.id.asc())
                    )
                )
                .scalars()
                .first()
            )
        stored = ShippingTrackingEvent(
            parcel_id=parcel.id if parcel else None,
            source_event_key=source_key,
            semantic_event_key=semantic_key,
            canonical_event_id=canonical.id if canonical else None,
            waybill=event.waybill or (parcel.waybill if parcel else None),
            dpd_event_id=event.event_id,
            object_id=event.object_id,
            business_code=event.business_code,
            operation_type=event.operation_type,
            description=event.description,
            event_time=event.event_time,
            depot=event.depot,
            depot_name=event.depot_name,
            country=event.country,
            package_reference=event.package_reference,
            parcel_reference=event.parcel_reference,
            event_data=[value.as_dict() for value in event.event_data],
            raw_payload=raw,
            is_cancelled=bool(canonical and canonical.is_cancelled),
            cancelled_at=canonical.cancelled_at if canonical and canonical.is_cancelled else None,
            received_at=datetime.now(UTC),
        )
        session.add(stored)
        inserted += 1
        if canonical is not None:
            duplicates += 1
        if parcel:
            affected_parcels.add(parcel.id)
        if event.operation_type == "CANCEL" and event.object_id:
            matched = list(
                (
                    await session.execute(
                        select(ShippingTrackingEvent).where(
                            ShippingTrackingEvent.object_id == event.object_id,
                            ShippingTrackingEvent.operation_type == "INSERT",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if matched:
                semantic_keys = {
                    value.semantic_event_key for value in matched if value.semantic_event_key
                }
                root_ids = {value.canonical_event_id or value.id for value in matched}
                logical_rows = list(
                    (
                        await session.execute(
                            select(ShippingTrackingEvent).where(
                                ShippingTrackingEvent.operation_type == "INSERT",
                                or_(
                                    ShippingTrackingEvent.semantic_event_key.in_(semantic_keys),
                                    ShippingTrackingEvent.id.in_(root_ids),
                                    ShippingTrackingEvent.canonical_event_id.in_(root_ids),
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                newly_cancelled_roots = {
                    value.canonical_event_id or value.id
                    for value in logical_rows
                    if not value.is_cancelled
                }
                cancelled_at = datetime.now(UTC)
                for value in logical_rows:
                    value.is_cancelled = True
                    value.cancelled_at = cancelled_at
                    if value.parcel_id is not None:
                        affected_parcels.add(value.parcel_id)
                cancelled += len(newly_cancelled_roots)
        elif event.operation_type == "INSERT" and event.object_id:
            prior_cancel = (
                (
                    await session.execute(
                        select(ShippingTrackingEvent.id).where(
                            ShippingTrackingEvent.object_id == event.object_id,
                            ShippingTrackingEvent.operation_type == "CANCEL",
                        )
                    )
                )
                .scalars()
                .first()
            )
            if prior_cancel is not None:
                cancelled_at = datetime.now(UTC)
                logical_rows = list(
                    (
                        await session.execute(
                            select(ShippingTrackingEvent).where(
                                ShippingTrackingEvent.operation_type == "INSERT",
                                ShippingTrackingEvent.semantic_event_key == semantic_key,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                newly_cancelled_roots = {
                    value.canonical_event_id or value.id
                    for value in logical_rows
                    if not value.is_cancelled
                }
                for value in logical_rows:
                    value.is_cancelled = True
                    value.cancelled_at = cancelled_at
                    if value.parcel_id is not None:
                        affected_parcels.add(value.parcel_id)
                cancel_rows = list(
                    (
                        await session.execute(
                            select(ShippingTrackingEvent).where(
                                ShippingTrackingEvent.object_id == event.object_id,
                                ShippingTrackingEvent.operation_type == "CANCEL",
                                ShippingTrackingEvent.parcel_id.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for cancel_row in cancel_rows:
                    cancel_row.parcel_id = parcel.id
                    cancel_row.waybill = parcel.waybill
                cancelled += len(newly_cancelled_roots)
    await session.flush()
    for parcel_id in affected_parcels:
        await recompute_dpd_tracking_parcel(session, parcel_id)
    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "cancelled": cancelled,
        "affected_parcel_ids": sorted(affected_parcels),
    }


async def _reconcile_firebird_milestones() -> dict[str, int]:
    """Ponawia niezależne zapisy kamieni milowych po trwałym zapisie zdarzeń DPD."""
    if not settings.shipping_dpd_firebird_milestones_enabled:
        return {"eligible": 0, "written": 0, "failed": 0, "conflicts": 0}
    from app.services.shipping_milestones import reconcile_shipping_milestones

    async with AsyncSessionLocal() as session:
        result = await reconcile_shipping_milestones(session)
        await session.commit()
        return result


@asynccontextmanager
async def _database_sync_lock() -> AsyncIterator[bool]:
    connection: AsyncConnection | None = None
    acquired = True
    if engine.dialect.name == "postgresql":
        connection = await engine.connect()
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": _POSTGRES_LOCK_KEY},
            )
        )
    try:
        yield acquired
    finally:
        if connection is not None:
            if acquired:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": _POSTGRES_LOCK_KEY},
                )
            await connection.close()


@asynccontextmanager
async def dpd_info_database_lock() -> AsyncIterator[bool]:
    """Udostępnia wspólną blokadę synchronizacji narzędziom administracyjnym DPD."""
    async with _database_sync_lock() as acquired:
        yield acquired


async def _create_sync_run(
    *,
    trigger_type: Literal["scheduler", "manual", "backfill"],
    user_id: int | None,
) -> int:
    async with AsyncSessionLocal() as session:
        row = ShippingTrackingSyncRun(
            source_channel=str(settings.dpd_info_channel or "brak kanału"),
            trigger_type=trigger_type,
            status="processing",
            triggered_by=user_id,
            started_at=datetime.now(UTC),
        )
        session.add(row)
        await session.commit()
        return int(row.id)


async def _update_sync_run(run_id: int, **values: Any) -> None:
    async with AsyncSessionLocal() as session:
        row = await session.get(ShippingTrackingSyncRun, run_id)
        if row is None:
            return
        for key, value in values.items():
            setattr(row, key, value)
        await session.commit()


async def synchronize_dpd_infoservices(
    *,
    trigger_type: Literal["scheduler", "manual"] = "manual",
    user_id: int | None = None,
    client: DpdInfoServicesClient | None = None,
) -> dict[str, Any]:
    """Pobiera, zapisuje i potwierdza ograniczoną liczbę partii kanału."""
    if _process_lock.locked():
        raise DpdInfoSyncBusyError("Synchronizacja DPD InfoServices jest już w toku.")
    async with _process_lock, _database_sync_lock() as lock_acquired:
        if not lock_acquired:
            raise DpdInfoSyncBusyError("Inny proces CTIP synchronizuje teraz InfoServices.")
        run_id = await _create_sync_run(trigger_type=trigger_type, user_id=user_id)
        fetched = inserted = duplicates = cancelled = batches = 0
        confirm_id: str | None = None
        acknowledgement_confirmed = False
        info_client = client or DpdInfoServicesClient()
        try:
            max_batches = min(max(int(settings.dpd_info_max_batches_per_sync), 1), 50)
            for _ in range(max_batches):
                batch = await asyncio.to_thread(info_client.get_customer_events)
                confirm_id = batch.confirm_id
                if not batch.events:
                    acknowledgement_confirmed = True
                    break
                fetched += len(batch.events)
                batches += 1
                async with AsyncSessionLocal() as session:
                    result = await persist_dpd_info_events(
                        session,
                        events=batch.events,
                        channel=str(settings.dpd_info_channel),
                    )
                    await session.commit()
                inserted += result["inserted"]
                duplicates += result["duplicates"]
                cancelled += result["cancelled"]
                if not confirm_id:
                    raise RuntimeError("DPD nie zwróciło identyfikatora potwierdzenia partii.")
                acknowledgement_confirmed = await asyncio.to_thread(
                    info_client.mark_events_processed,
                    confirm_id,
                )
                if not acknowledgement_confirmed:
                    raise RuntimeError("DPD nie potwierdziło odebrania zapisanej partii zdarzeń.")
            reached_limit = batches >= max_batches
            milestone_result = await _reconcile_firebird_milestones()
            status = "partial" if reached_limit else "success"
            error_text = (
                "Osiągnięto limit partii jednego cyklu; backlog zostanie pobrany później."
                if reached_limit
                else None
            )
            await _update_sync_run(
                run_id,
                status=status,
                fetched_count=fetched,
                inserted_count=inserted,
                duplicate_count=duplicates,
                cancelled_count=cancelled,
                batch_count=batches,
                confirm_id=confirm_id,
                acknowledgement_confirmed=acknowledgement_confirmed,
                error_text=error_text,
                completed_at=datetime.now(UTC),
            )
            return {
                "run_id": run_id,
                "status": status,
                "fetched_count": fetched,
                "inserted_count": inserted,
                "duplicate_count": duplicates,
                "cancelled_count": cancelled,
                "batch_count": batches,
                "acknowledgement_confirmed": acknowledgement_confirmed,
                "firebird_milestones": milestone_result,
                "message": error_text or "Synchronizacja InfoServices zakończona poprawnie.",
            }
        except Exception as exc:
            status = "partial" if batches else "failed"
            await _update_sync_run(
                run_id,
                status=status,
                fetched_count=fetched,
                inserted_count=inserted,
                duplicate_count=duplicates,
                cancelled_count=cancelled,
                batch_count=batches,
                confirm_id=confirm_id,
                acknowledgement_confirmed=acknowledgement_confirmed,
                error_text=str(exc),
                completed_at=datetime.now(UTC),
            )
            raise


async def backfill_dpd_waybills(
    waybills: list[str],
    *,
    client: DpdInfoServicesClient | None = None,
) -> dict[str, Any]:
    """Pobiera pełną historię wskazanych listów bez potwierdzania kanału."""
    if _process_lock.locked():
        raise DpdInfoSyncBusyError("Synchronizacja DPD InfoServices jest już w toku.")
    normalized = list(dict.fromkeys(str(value or "").strip() for value in waybills if value))
    async with _process_lock, _database_sync_lock() as lock_acquired:
        if not lock_acquired:
            raise DpdInfoSyncBusyError("Inny proces CTIP synchronizuje teraz InfoServices.")
        run_id = await _create_sync_run(trigger_type="backfill", user_id=None)
        fetched = inserted = duplicates = cancelled = 0
        info_client = client or DpdInfoServicesClient()
        try:
            for waybill in normalized:
                batch = await asyncio.to_thread(info_client.get_waybill_events, waybill)
                fetched += len(batch.events)
                async with AsyncSessionLocal() as session:
                    result = await persist_dpd_info_events(
                        session,
                        events=batch.events,
                        channel=str(settings.dpd_info_channel),
                    )
                    await session.commit()
                inserted += result["inserted"]
                duplicates += result["duplicates"]
                cancelled += result["cancelled"]
            milestone_result = await _reconcile_firebird_milestones()
            await _update_sync_run(
                run_id,
                status="success",
                fetched_count=fetched,
                inserted_count=inserted,
                duplicate_count=duplicates,
                cancelled_count=cancelled,
                batch_count=len(normalized),
                acknowledgement_confirmed=True,
                completed_at=datetime.now(UTC),
            )
            return {
                "run_id": run_id,
                "status": "success",
                "waybill_count": len(normalized),
                "fetched_count": fetched,
                "inserted_count": inserted,
                "duplicate_count": duplicates,
                "cancelled_count": cancelled,
                "firebird_milestones": milestone_result,
            }
        except Exception as exc:
            await _update_sync_run(
                run_id,
                status="partial" if fetched else "failed",
                fetched_count=fetched,
                inserted_count=inserted,
                duplicate_count=duplicates,
                cancelled_count=cancelled,
                batch_count=len(normalized),
                acknowledgement_confirmed=True,
                error_text=str(exc),
                completed_at=datetime.now(UTC),
            )
            raise


async def _scheduler_loop(stop_event: asyncio.Event) -> None:
    interval = max(int(settings.dpd_info_sync_interval_seconds), 60)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            continue
        except TimeoutError:
            pass
        try:
            await synchronize_dpd_infoservices(trigger_type="scheduler")
        except DpdInfoSyncBusyError:
            logger.info("Pominięto cykl InfoServices, ponieważ synchronizacja już trwa.")
        except Exception:
            logger.exception("Błąd cyklicznej synchronizacji DPD InfoServices")


async def start_dpd_infoservices_scheduler() -> None:
    """Uruchamia harmonogram tylko przy kompletnej, jawnie włączonej konfiguracji."""
    global _scheduler_stop_event, _scheduler_task  # noqa: PLW0603
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    if not settings.dpd_info_enabled:
        return
    status = DpdInfoServicesClient().configuration_status()
    if not status["api_ready"]:
        logger.error("Nie uruchomiono InfoServices: konfiguracja jest niekompletna.")
        return
    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(
        _scheduler_loop(_scheduler_stop_event),
        name="dpd-infoservices-scheduler",
    )
    logger.info(
        "Uruchomiono harmonogram DPD InfoServices co %s s.", settings.dpd_info_sync_interval_seconds
    )


async def stop_dpd_infoservices_scheduler() -> None:
    """Kończy harmonogram bez przerywania zapisu aktualnej partii."""
    global _scheduler_stop_event, _scheduler_task  # noqa: PLW0603
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    if _scheduler_task is not None:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=10)
        except TimeoutError:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass
    _scheduler_stop_event = None
    _scheduler_task = None


async def latest_dpd_info_sync_status(session: AsyncSession) -> dict[str, Any] | None:
    """Zwraca ostatnie wykonanie synchronizacji do panelu diagnostycznego."""
    row = (
        (
            await session.execute(
                select(ShippingTrackingSyncRun).order_by(
                    ShippingTrackingSyncRun.started_at.desc(),
                    ShippingTrackingSyncRun.id.desc(),
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "trigger_type": row.trigger_type,
        "fetched_count": row.fetched_count,
        "inserted_count": row.inserted_count,
        "duplicate_count": row.duplicate_count,
        "cancelled_count": row.cancelled_count,
        "batch_count": row.batch_count,
        "acknowledgement_confirmed": row.acknowledgement_confirmed,
        "error_text": row.error_text,
        "started_at": row.started_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


async def tracking_counts(session: AsyncSession) -> dict[str, int]:
    """Zwraca liczniki aktywnych i problematycznych listów."""
    total = int((await session.scalar(select(func.count(ShippingTrackingParcel.id)))) or 0)
    active = int(
        (
            await session.scalar(
                select(func.count(ShippingTrackingParcel.id)).where(
                    ShippingTrackingParcel.is_terminal.is_(False)
                )
            )
        )
        or 0
    )
    attention = int(
        (
            await session.scalar(
                select(func.count(ShippingTrackingParcel.id)).where(
                    ShippingTrackingParcel.requires_attention.is_(True)
                )
            )
        )
        or 0
    )
    return {"total": total, "active": active, "attention": attention}


__all__ = [
    "DpdInfoSyncBusyError",
    "backfill_dpd_waybills",
    "classify_dpd_event",
    "dpd_semantic_event_key",
    "dpd_event_group",
    "dpd_info_database_lock",
    "is_dpd_pickup_confirmation",
    "latest_dpd_info_sync_status",
    "persist_dpd_info_events",
    "recompute_dpd_tracking_parcel",
    "start_dpd_infoservices_scheduler",
    "stop_dpd_infoservices_scheduler",
    "synchronize_dpd_infoservices",
    "tracking_counts",
]
