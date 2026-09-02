"""Kontrolowana kanonizacja istniejących zdarzeń DPD InfoServices."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ShippingTrackingEvent
from app.services.dpd_infoservices_sync import (
    dpd_semantic_event_key,
    recompute_dpd_tracking_parcel,
)


class DpdTrackingDedupeValidationError(RuntimeError):
    """Oznacza zmianę stanu bazy albo niebezpieczną próbę rollbacku."""


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_state(row: ShippingTrackingEvent) -> dict[str, Any]:
    return {
        "semantic_event_key": row.semantic_event_key,
        "canonical_event_id": row.canonical_event_id,
        "is_cancelled": bool(row.is_cancelled),
        "cancelled_at": _timestamp(row.cancelled_at),
    }


def _semantic_key(row: ShippingTrackingEvent) -> str:
    return dpd_semantic_event_key(
        source_event_key=row.source_event_key,
        operation_type=row.operation_type,
        waybill=row.waybill,
        business_code=row.business_code,
        description=row.description,
        event_time=row.event_time,
        depot=row.depot,
        depot_name=row.depot_name,
        country=row.country,
        package_reference=row.package_reference,
        parcel_reference=row.parcel_reference,
        event_data=row.event_data,
    )


def _state_token(changes: list[dict[str, Any]]) -> str:
    serialized = json.dumps(changes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def _build_plan(
    session: AsyncSession,
    *,
    lock: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    statement = select(ShippingTrackingEvent).where(
        ShippingTrackingEvent.operation_type == "INSERT"
    )
    if lock:
        statement = statement.with_for_update()
    rows = list(
        (
            await session.execute(
                statement.order_by(
                    ShippingTrackingEvent.event_time.asc().nullsfirst(),
                    ShippingTrackingEvent.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    groups: dict[str, list[ShippingTrackingEvent]] = {}
    for row in rows:
        groups.setdefault(_semantic_key(row), []).append(row)

    changes: list[dict[str, Any]] = []
    duplicate_count = 0
    duplicate_group_count = 0
    cancellation_updates = 0
    for semantic_key, group in groups.items():
        canonical = min(group, key=lambda value: value.id)
        if len(group) > 1:
            duplicate_group_count += 1
            duplicate_count += len(group) - 1
        group_cancelled = any(value.is_cancelled for value in group)
        cancellation_times = [value.cancelled_at for value in group if value.cancelled_at]
        group_cancelled_at = min(cancellation_times) if cancellation_times else None
        for row in group:
            before = _row_state(row)
            after = {
                "semantic_event_key": semantic_key,
                "canonical_event_id": None if row.id == canonical.id else canonical.id,
                "is_cancelled": group_cancelled,
                "cancelled_at": _timestamp(group_cancelled_at) if group_cancelled else None,
            }
            if not before["is_cancelled"] and after["is_cancelled"]:
                cancellation_updates += 1
            if before != after:
                changes.append(
                    {
                        "id": row.id,
                        "parcel_id": row.parcel_id,
                        "before": before,
                        "after": after,
                    }
                )
    summary = {
        "technical_insert_count": len(rows),
        "logical_event_count": len(groups),
        "duplicate_group_count": duplicate_group_count,
        "duplicate_count": duplicate_count,
        "change_count": len(changes),
        "cancellation_update_count": cancellation_updates,
        "affected_parcel_count": len(
            {change["parcel_id"] for change in changes if change["parcel_id"] is not None}
        ),
    }
    return summary, changes


async def preview_dpd_tracking_dedupe(session: AsyncSession) -> dict[str, Any]:
    """Wylicza stabilny plan kanonizacji bez modyfikowania bazy."""
    summary, changes = await _build_plan(session, lock=False)
    token = _state_token(changes)
    return {
        "mode": "dry-run",
        **summary,
        "state_token": token,
        "required_confirmation": "ZASTOSUJ DEDUPLIKACJE DPD",
    }


async def apply_dpd_tracking_dedupe(
    session: AsyncSession,
    *,
    expected_state_token: str,
) -> dict[str, Any]:
    """Stosuje zatwierdzony plan i zwraca pełny stan wymagany do rollbacku."""
    summary, changes = await _build_plan(session, lock=True)
    actual_token = _state_token(changes)
    if not expected_state_token or actual_token != expected_state_token:
        raise DpdTrackingDedupeValidationError(
            "Stan zdarzeń DPD zmienił się od dry-run. Wykonaj ponowny podgląd."
        )
    rows = {
        row.id: row
        for row in (
            (
                await session.execute(
                    select(ShippingTrackingEvent).where(
                        ShippingTrackingEvent.id.in_([change["id"] for change in changes])
                    )
                )
            )
            .scalars()
            .all()
            if changes
            else []
        )
    }
    for change in changes:
        row = rows[change["id"]]
        after = change["after"]
        row.semantic_event_key = after["semantic_event_key"]
        row.canonical_event_id = after["canonical_event_id"]
        row.is_cancelled = after["is_cancelled"]
        row.cancelled_at = _parse_timestamp(after["cancelled_at"])
    await session.flush()
    affected_parcels = {
        change["parcel_id"] for change in changes if change["parcel_id"] is not None
    }
    for parcel_id in sorted(affected_parcels):
        await recompute_dpd_tracking_parcel(session, parcel_id)
    return {
        "mode": "apply",
        "run_id": str(uuid.uuid4()),
        **summary,
        "state_token": actual_token,
        "rollback_state": changes,
    }


async def rollback_dpd_tracking_dedupe(
    session: AsyncSession,
    *,
    run_id: str,
    rollback_state: list[dict[str, Any]],
) -> dict[str, Any]:
    """Przywraca metadane sprzed jednego przebiegu, jeśli stan nadal jest zgodny."""
    ids = [int(change["id"]) for change in rollback_state]
    rows = {
        row.id: row
        for row in (
            (
                await session.execute(
                    select(ShippingTrackingEvent)
                    .where(ShippingTrackingEvent.id.in_(ids))
                    .with_for_update()
                )
            )
            .scalars()
            .all()
            if ids
            else []
        )
    }
    if len(rows) != len(ids):
        raise DpdTrackingDedupeValidationError(
            "Nie można wykonać rollbacku: brakuje zdarzeń zapisanych w raporcie."
        )
    for change in rollback_state:
        row = rows[int(change["id"])]
        if _row_state(row) != change["after"]:
            raise DpdTrackingDedupeValidationError(
                "Nie można wykonać rollbacku: metadane zdarzeń zostały później zmienione."
            )
    for change in rollback_state:
        row = rows[int(change["id"])]
        before = change["before"]
        row.semantic_event_key = before["semantic_event_key"]
        row.canonical_event_id = before["canonical_event_id"]
        row.is_cancelled = before["is_cancelled"]
        row.cancelled_at = _parse_timestamp(before["cancelled_at"])
    await session.flush()
    affected_parcels = {
        change["parcel_id"] for change in rollback_state if change["parcel_id"] is not None
    }
    for parcel_id in sorted(affected_parcels):
        await recompute_dpd_tracking_parcel(session, parcel_id)
    return {
        "mode": "rollback",
        "run_id": run_id,
        "restored_count": len(rollback_state),
        "affected_parcel_count": len(affected_parcels),
    }


__all__ = [
    "DpdTrackingDedupeValidationError",
    "apply_dpd_tracking_dedupe",
    "preview_dpd_tracking_dedupe",
    "rollback_dpd_tracking_dedupe",
]
