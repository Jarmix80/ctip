"""Lokalny cache statusów arkusza Google dla modalu FLOW."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import WorkflowSheetStatusCache
from app.services.settings_store import StoredValue, build_store
from app.services.workflow_sheet_sync import (
    SHEET_DEFAULT_WORKSHEET,
    load_workflow_sheet_devices_lookup,
    load_workflow_sheet_runtime_config,
    use_workflow_sheet_runtime_config,
    workflow_sheet_sync_configured,
)

logger = logging.getLogger(__name__)

_WORKFLOW_SHEET_STATUS_CACHE_NAMESPACE = "workflow_sheet_status_cache"
_REFRESH_LOCK = asyncio.Lock()
_scheduler_task: asyncio.Task[None] | None = None
_scheduler_stop_event: asyncio.Event | None = None
_settings_store = build_store(settings.admin_secret_key)


def _normalize_device_key(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_date(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for date_format in ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], date_format).date()
        except ValueError:
            continue
    return None


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _split_source_key(source_key: str | None) -> tuple[str, int | None]:
    text = str(source_key or "").strip()
    if not text or ":" not in text:
        return ("firebird_magazyn_28", None)
    source_type, raw_row = text.split(":", 1)
    return (source_type or "firebird_magazyn_28", _parse_int(raw_row))


def _entry_from_cache_row(row: WorkflowSheetStatusCache) -> dict[str, str]:
    return {
        "sheet_row": str(row.sheet_row or ""),
        "producer": row.producer or "",
        "model": row.model or "",
        "serial": row.serial or "",
        "status": row.sheet_status or "",
        "price": row.price or "",
        "notes": row.sheet_notes or "",
        "counter_bw": row.counter_bw or "",
        "counter_color": row.counter_color or "",
        "reservation_status": row.reservation_status or "",
        "reservation_until": (row.reservation_until.isoformat() if row.reservation_until else ""),
        "reservation_grenke": row.reservation_grenke or "",
        "form_ctip": row.form_ctip or "",
        "ctip_form_id": str(row.ctip_form_id or ""),
        "ctip_workflow_case_id": str(row.ctip_workflow_case_id or ""),
        "business_status_legacy": row.business_status_legacy or "",
        "ms_id_magazyn_table": str(row.source_row or ""),
        "ms_id_maszyna": str(row.ms_id_maszyna or ""),
        "index": row.device_index or "",
    }


async def ensure_workflow_sheet_status_cache_table() -> None:
    """Zapewnia istnienie tabeli cache statusów arkusza."""

    async with engine.begin() as conn:
        await conn.run_sync(WorkflowSheetStatusCache.__table__.create, checkfirst=True)


async def _update_cache_metadata(
    session: AsyncSession,
    *,
    user_id: int | None,
    values: dict[str, str],
) -> None:
    stored_values = {
        key: StoredValue(value=str(value), is_secret=False) for key, value in values.items()
    }
    if stored_values:
        await _settings_store.set_namespace(
            session,
            _WORKFLOW_SHEET_STATUS_CACHE_NAMESPACE,
            stored_values,
            user_id=user_id,
        )


async def load_workflow_sheet_status_cache_metadata(
    session: AsyncSession,
    *,
    config=None,
) -> dict[str, Any]:
    """Zwraca metadane ostatniej synchronizacji cache statusów arkusza."""

    active_config = config or await load_workflow_sheet_runtime_config(session)
    if not active_config.enabled:
        return {
            "enabled": False,
            "reason": "Synchronizacja arkusza jest wyłączona w panelu administratora.",
            "worksheet_title": None,
            "last_sync_at": None,
            "last_error": None,
            "row_count": 0,
            "stale": False,
            "refresh_interval_seconds": int(
                max(60, settings.workflow_sheet_status_cache_refresh_interval_seconds)
            ),
        }

    refresh_enabled, refresh_reason = workflow_sheet_sync_configured(active_config)
    stored = await _settings_store.get_namespace(session, _WORKFLOW_SHEET_STATUS_CACHE_NAMESPACE)
    last_sync_at = _parse_datetime(stored.get("last_sync_at"))
    last_error = str(stored.get("last_error") or "").strip() or None
    worksheet_title = str(stored.get("worksheet_title") or "").strip() or None
    row_count = _parse_int(stored.get("row_count")) or 0
    stale_after_seconds = max(60, int(settings.workflow_sheet_status_cache_stale_after_seconds))
    stale = False
    if last_sync_at is not None:
        stale = datetime.now(UTC) - last_sync_at > timedelta(seconds=stale_after_seconds)

    return {
        "enabled": True,
        "reason": None,
        "worksheet_title": worksheet_title,
        "last_sync_at": _to_iso(last_sync_at),
        "last_error": last_error,
        "row_count": row_count,
        "stale": stale,
        "refresh_enabled": bool(refresh_enabled),
        "refresh_reason": refresh_reason,
        "refresh_interval_seconds": int(
            max(60, settings.workflow_sheet_status_cache_refresh_interval_seconds)
        ),
    }


async def load_workflow_sheet_status_cache_lookup(
    session: AsyncSession,
    *,
    config=None,
) -> dict[str, Any]:
    """Zwraca lookup statusów urządzeń z lokalnego cache PostgreSQL."""

    metadata = await load_workflow_sheet_status_cache_metadata(session, config=config)
    if not metadata["enabled"]:
        return {
            "enabled": False,
            "reason": metadata["reason"],
            "worksheet_title": metadata["worksheet_title"],
            "last_sync_at": metadata["last_sync_at"],
            "last_error": metadata["last_error"],
            "stale": metadata["stale"],
            "row_count": metadata["row_count"],
            "refresh_enabled": metadata.get("refresh_enabled"),
            "refresh_reason": metadata.get("refresh_reason"),
            "refresh_interval_seconds": metadata["refresh_interval_seconds"],
            "by_source_key": {},
            "by_index": {},
        }

    rows = (
        await session.execute(
            select(WorkflowSheetStatusCache).order_by(WorkflowSheetStatusCache.id.asc())
        )
    ).scalars()
    by_source_key: dict[str, dict[str, str]] = {}
    by_index: dict[str, dict[str, str]] = {}

    cached_rows = list(rows)
    for row in cached_rows:
        entry = _entry_from_cache_row(row)
        source_key = str(row.source_key or "").strip()
        if source_key:
            by_source_key[source_key] = entry
        normalized_index = str(row.device_index_normalized or "").strip()
        if normalized_index and normalized_index not in by_index:
            by_index[normalized_index] = entry

    metadata["row_count"] = len(cached_rows)
    return {
        "enabled": True,
        "reason": metadata["reason"],
        "worksheet_title": metadata["worksheet_title"],
        "last_sync_at": metadata["last_sync_at"],
        "last_error": metadata["last_error"],
        "stale": metadata["stale"],
        "row_count": len(cached_rows),
        "refresh_enabled": metadata.get("refresh_enabled"),
        "refresh_reason": metadata.get("refresh_reason"),
        "refresh_interval_seconds": metadata["refresh_interval_seconds"],
        "by_source_key": by_source_key,
        "by_index": by_index,
    }


def _build_cache_rows(
    lookup: dict[str, Any], *, synced_at: datetime
) -> list[WorkflowSheetStatusCache]:
    rows: list[WorkflowSheetStatusCache] = []
    seen_source_keys: set[str] = set()
    seen_indexes: set[str] = set()

    for source_key, entry in (lookup.get("by_source_key") or {}).items():
        clean_source_key = str(source_key or "").strip()
        source_type, source_row = _split_source_key(clean_source_key)
        device_index = str(entry.get("index") or "").strip()
        normalized_index = _normalize_device_key(device_index)
        if clean_source_key:
            seen_source_keys.add(clean_source_key)
        if normalized_index:
            seen_indexes.add(normalized_index)
        rows.append(
            WorkflowSheetStatusCache(
                source_key=clean_source_key or None,
                source_type=source_type,
                source_row=source_row,
                producer=str(entry.get("producer") or "").strip() or None,
                model=str(entry.get("model") or "").strip() or None,
                serial=str(entry.get("serial") or "").strip() or None,
                device_index=device_index or None,
                device_index_normalized=normalized_index or None,
                sheet_row=_parse_int(entry.get("sheet_row")),
                sheet_status=str(entry.get("status") or "").strip() or None,
                sheet_notes=str(entry.get("notes") or "").strip() or None,
                counter_bw=str(entry.get("counter_bw") or "").strip() or None,
                counter_color=str(entry.get("counter_color") or "").strip() or None,
                reservation_status=str(entry.get("reservation_status") or "").strip() or None,
                reservation_grenke=str(entry.get("reservation_grenke") or "").strip() or None,
                reservation_until=_parse_date(entry.get("reservation_until")),
                price=str(entry.get("price") or "").strip() or None,
                ms_id_maszyna=_parse_int(entry.get("ms_id_maszyna")),
                form_ctip=str(entry.get("form_ctip") or "").strip() or None,
                ctip_form_id=_parse_int(entry.get("ctip_form_id")),
                ctip_workflow_case_id=_parse_int(entry.get("ctip_workflow_case_id")),
                business_status_legacy=str(entry.get("business_status_legacy") or "").strip()
                or None,
                synced_at=synced_at,
            )
        )

    for normalized_index, entry in (lookup.get("by_index") or {}).items():
        clean_index = str(normalized_index or "").strip()
        if clean_index in seen_indexes:
            continue
        source_row = _parse_int(entry.get("ms_id_magazyn_table"))
        source_type = "firebird_magazyn_28"
        source_key = f"{source_type}:{source_row}" if source_row is not None else None
        if source_key and source_key in seen_source_keys:
            continue
        rows.append(
            WorkflowSheetStatusCache(
                source_key=source_key,
                source_type=source_type,
                source_row=source_row,
                producer=str(entry.get("producer") or "").strip() or None,
                model=str(entry.get("model") or "").strip() or None,
                serial=str(entry.get("serial") or "").strip() or None,
                device_index=str(entry.get("index") or "").strip() or None,
                device_index_normalized=clean_index or None,
                sheet_row=_parse_int(entry.get("sheet_row")),
                sheet_status=str(entry.get("status") or "").strip() or None,
                sheet_notes=str(entry.get("notes") or "").strip() or None,
                counter_bw=str(entry.get("counter_bw") or "").strip() or None,
                counter_color=str(entry.get("counter_color") or "").strip() or None,
                reservation_status=str(entry.get("reservation_status") or "").strip() or None,
                reservation_grenke=str(entry.get("reservation_grenke") or "").strip() or None,
                reservation_until=_parse_date(entry.get("reservation_until")),
                price=str(entry.get("price") or "").strip() or None,
                ms_id_maszyna=_parse_int(entry.get("ms_id_maszyna")),
                form_ctip=str(entry.get("form_ctip") or "").strip() or None,
                ctip_form_id=_parse_int(entry.get("ctip_form_id")),
                ctip_workflow_case_id=_parse_int(entry.get("ctip_workflow_case_id")),
                business_status_legacy=str(entry.get("business_status_legacy") or "").strip()
                or None,
                synced_at=synced_at,
            )
        )

    return rows


async def refresh_workflow_sheet_status_cache(
    session: AsyncSession,
    *,
    user_id: int | None,
) -> dict[str, Any]:
    """Odswieza lokalny cache statusów urządzeń z arkusza Google."""

    async with _REFRESH_LOCK:
        await ensure_workflow_sheet_status_cache_table()
        config = await load_workflow_sheet_runtime_config(session)
        enabled, reason = workflow_sheet_sync_configured(config)
        if not enabled:
            return {
                "success": False,
                "message": reason or "Synchronizacja arkusza jest nieaktywna.",
                **(await load_workflow_sheet_status_cache_metadata(session, config=config)),
            }

        try:
            with use_workflow_sheet_runtime_config(config):
                lookup = await asyncio.to_thread(load_workflow_sheet_devices_lookup, config)
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc).strip() or "Blad odswiezania statusow z arkusza."
            await _update_cache_metadata(
                session,
                user_id=user_id,
                values={"last_error": error_message},
            )
            await session.commit()
            return {
                "success": False,
                "message": error_message,
                **(await load_workflow_sheet_status_cache_metadata(session, config=config)),
            }

        synced_at = datetime.now(UTC)
        cache_rows = _build_cache_rows(lookup, synced_at=synced_at)
        await session.execute(delete(WorkflowSheetStatusCache))
        session.add_all(cache_rows)
        await _update_cache_metadata(
            session,
            user_id=user_id,
            values={
                "last_sync_at": synced_at.isoformat(),
                "last_error": "",
                "worksheet_title": str(lookup.get("worksheet_title") or SHEET_DEFAULT_WORKSHEET),
                "row_count": str(len(cache_rows)),
            },
        )
        await session.commit()
        metadata = await load_workflow_sheet_status_cache_metadata(session, config=config)
        return {
            "success": True,
            "message": f"Odswiezono statusy z arkusza ({len(cache_rows)} pozycji).",
            "worksheet_title": lookup.get("worksheet_title"),
            "refreshed_count": len(cache_rows),
            **metadata,
        }


async def workflow_sheet_status_cache_refresh_tick() -> dict[str, Any]:
    """Wykonuje pojedynczy krok odswiezenia cache w tle aplikacji."""

    async with AsyncSessionLocal() as session:
        result = await refresh_workflow_sheet_status_cache(session, user_id=None)
        if not result.get("success"):
            message = str(result.get("message") or "").strip()
            if result.get("enabled") is False:
                logger.info("Scheduler cache arkusza pomija odswiezenie: %s", message)
            else:
                logger.warning("Scheduler cache arkusza nie odswiezyl danych: %s", message)
        else:
            logger.info(
                "Scheduler cache arkusza odswiezyl %s pozycji.",
                result.get("refreshed_count", 0),
            )
        return result


async def _workflow_sheet_status_cache_loop(stop_event: asyncio.Event) -> None:
    """Petla okresowego odswiezania cache statusów arkusza."""

    interval_seconds = max(60, int(settings.workflow_sheet_status_cache_refresh_interval_seconds))
    while not stop_event.is_set():
        try:
            await workflow_sheet_status_cache_refresh_tick()
        except Exception:  # noqa: BLE001
            logger.exception("Blad petli odswiezania cache statusow arkusza")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


async def start_workflow_sheet_status_cache_scheduler() -> None:
    """Startuje scheduler okresowego odswiezania cache statusów arkusza."""

    global _scheduler_task, _scheduler_stop_event  # noqa: PLW0603
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    await ensure_workflow_sheet_status_cache_table()
    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(
        _workflow_sheet_status_cache_loop(_scheduler_stop_event),
        name="workflow-sheet-status-cache-scheduler",
    )
    logger.info("Uruchomiono scheduler cache statusow arkusza FLOW.")


async def stop_workflow_sheet_status_cache_scheduler() -> None:
    """Zatrzymuje scheduler okresowego odswiezania cache statusów arkusza."""

    global _scheduler_task, _scheduler_stop_event  # noqa: PLW0603
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    if _scheduler_task is not None:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=5)
        except TimeoutError:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass
    _scheduler_task = None
    _scheduler_stop_event = None


__all__ = [
    "ensure_workflow_sheet_status_cache_table",
    "load_workflow_sheet_status_cache_lookup",
    "load_workflow_sheet_status_cache_metadata",
    "refresh_workflow_sheet_status_cache",
    "start_workflow_sheet_status_cache_scheduler",
    "stop_workflow_sheet_status_cache_scheduler",
    "workflow_sheet_status_cache_refresh_tick",
]
