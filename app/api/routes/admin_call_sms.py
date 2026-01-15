"""Endpointy panelu administratora dla SMS polaczen."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_config import settings_store
from app.models import Call, SmsOut
from app.schemas.admin_call_sms import (
    CallSmsBulkRequest,
    CallSmsBulkResponse,
    CallSmsConfigResponse,
    CallSmsConfigUpdate,
    CallSmsHistoryEntry,
    CallSmsHistoryResponse,
)
from app.services.audit import record_audit
from app.services.call_sms_config import CALL_SMS_COOLDOWN_MODES, normalize_call_sms_config
from app.services.call_sms_rules import (
    CALL_SMS_SCENARIO_CODES,
    is_polish_mobile,
    normalize_destination,
    parse_opt_out_numbers,
)
from app.services.settings_store import StoredValue

router = APIRouter(prefix="/admin/call-sms", tags=["admin-call-sms"])

CALL_SMS_HISTORY_STATUSES = {"NEW", "RETRY", "SENT", "ERROR", "SIMULATED"}
CALL_SMS_STATUS_PATTERN = f"^({'|'.join(sorted(CALL_SMS_HISTORY_STATUSES))})$"
CALL_SMS_SCENARIO_PATTERN = f"^({'|'.join(CALL_SMS_SCENARIO_CODES)})$"


def _ensure_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operacja wymaga roli administratora."
        )


def _bool_flag(value: bool) -> str:
    return "true" if value else "false"


async def load_call_sms_config(session: AsyncSession) -> CallSmsConfigResponse:
    """Pobiera konfiguracje automatycznych SMS polaczen."""
    stored = await settings_store.get_namespace(session, "call_sms")
    config = normalize_call_sms_config(stored)
    return CallSmsConfigResponse(**config.as_dict())


def _validate_config(payload: CallSmsConfigUpdate) -> None:
    if payload.cooldown_mode not in CALL_SMS_COOLDOWN_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Niepoprawny tryb ograniczania SMS.",
        )

    def ensure_text(enabled: bool, text: str, label: str) -> None:
        if enabled and not text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tresc dla scenariusza '{label}' nie moze byc pusta.",
            )

    if payload.enabled and payload.inbound_enabled:
        ensure_text(
            payload.inbound_answered_enabled,
            payload.inbound_answered_text,
            "przychodzace odebrane",
        )
        ensure_text(
            payload.inbound_missed_enabled,
            payload.inbound_missed_text,
            "przychodzace nieodebrane",
        )
        ensure_text(
            payload.inbound_repeat_answered_enabled,
            payload.inbound_repeat_answered_text,
            "przychodzace ponowne (odebrane)",
        )
        ensure_text(
            payload.inbound_repeat_missed_enabled,
            payload.inbound_repeat_missed_text,
            "przychodzace ponowne (nieodebrane)",
        )

    if payload.enabled and payload.outbound_enabled:
        ensure_text(
            payload.outbound_answered_enabled,
            payload.outbound_answered_text,
            "wychodzace odebrane",
        )
        ensure_text(
            payload.outbound_missed_enabled,
            payload.outbound_missed_text,
            "wychodzace nieodebrane",
        )
        ensure_text(
            payload.outbound_repeat_answered_enabled,
            payload.outbound_repeat_answered_text,
            "wychodzace ponowne (odebrane)",
        )
        ensure_text(
            payload.outbound_repeat_missed_enabled,
            payload.outbound_repeat_missed_text,
            "wychodzace ponowne (nieodebrane)",
        )


async def load_call_sms_history(
    session: AsyncSession,
    limit: int,
    *,
    status_filter: str | None = None,
    scenario_filter: str | None = None,
) -> list[CallSmsHistoryEntry]:
    """Pobiera historie SMS generowanych po polaczeniach."""
    fetch_limit = max(limit, 1)
    if scenario_filter:
        fetch_limit = max(fetch_limit * 3, 50)

    stmt = (
        select(
            SmsOut.id,
            SmsOut.created_at,
            SmsOut.dest,
            SmsOut.text,
            SmsOut.status,
            SmsOut.provider_status,
            SmsOut.provider_msg_id,
            SmsOut.error_msg,
            SmsOut.call_id,
            Call.ext.label("call_ext"),
            Call.direction.label("call_direction"),
            Call.disposition.label("call_disposition"),
            SmsOut.meta,
        )
        .join(Call, SmsOut.call_id == Call.id, isouter=True)
        .where(SmsOut.source == "call_sms")
        .order_by(desc(SmsOut.created_at))
        .limit(fetch_limit)
    )
    if status_filter:
        stmt = stmt.where(SmsOut.status == status_filter)

    rows = (await session.execute(stmt)).all()
    items: list[CallSmsHistoryEntry] = []
    for row in rows:
        meta = row.meta
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        scenario = meta.get("scenario") if isinstance(meta, dict) else None
        if scenario_filter and scenario != scenario_filter:
            continue
        repeat_flag = meta.get("repeat") if isinstance(meta, dict) else None
        bulk_id = meta.get("bulk_id") if isinstance(meta, dict) else None
        internal_ext = meta.get("ext") if isinstance(meta, dict) else None
        if not internal_ext:
            internal_ext = getattr(row, "call_ext", None)

        items.append(
            CallSmsHistoryEntry(
                id=row.id,
                created_at=row.created_at,
                dest=row.dest,
                status=row.status,
                text=row.text,
                call_id=row.call_id,
                internal_ext=internal_ext,
                direction=(meta.get("direction") if isinstance(meta, dict) else None)
                or getattr(row, "call_direction", None),
                disposition=(meta.get("disposition") if isinstance(meta, dict) else None)
                or getattr(row, "call_disposition", None),
                scenario=scenario,
                repeat=bool(repeat_flag) if repeat_flag is not None else None,
                provider_status=row.provider_status,
                provider_message_id=row.provider_msg_id,
                error_msg=row.error_msg,
                bulk_id=bulk_id if isinstance(bulk_id, str) else None,
            )
        )
        if len(items) >= limit:
            break

    return items


@router.get("/config", response_model=CallSmsConfigResponse, summary="Konfiguracja SMS polaczen")
async def get_call_sms_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CallSmsConfigResponse:
    """Zwraca aktualna konfiguracje automatycznych SMS dla polaczen."""
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    return await load_call_sms_config(session)


@router.put("/config", response_model=CallSmsConfigResponse, summary="Aktualizacja SMS polaczen")
async def update_call_sms_config(
    payload: CallSmsConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CallSmsConfigResponse:
    """Zapisuje konfiguracje automatycznych SMS dla polaczen."""
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    _validate_config(payload)

    values: dict[str, StoredValue] = {
        "enabled": StoredValue(_bool_flag(payload.enabled), False),
        "inbound_enabled": StoredValue(_bool_flag(payload.inbound_enabled), False),
        "outbound_enabled": StoredValue(_bool_flag(payload.outbound_enabled), False),
        "inbound_answered_enabled": StoredValue(
            _bool_flag(payload.inbound_answered_enabled), False
        ),
        "inbound_answered_text": StoredValue(payload.inbound_answered_text, False),
        "inbound_missed_enabled": StoredValue(_bool_flag(payload.inbound_missed_enabled), False),
        "inbound_missed_text": StoredValue(payload.inbound_missed_text, False),
        "inbound_repeat_answered_enabled": StoredValue(
            _bool_flag(payload.inbound_repeat_answered_enabled), False
        ),
        "inbound_repeat_answered_text": StoredValue(payload.inbound_repeat_answered_text, False),
        "inbound_repeat_missed_enabled": StoredValue(
            _bool_flag(payload.inbound_repeat_missed_enabled), False
        ),
        "inbound_repeat_missed_text": StoredValue(payload.inbound_repeat_missed_text, False),
        "outbound_answered_enabled": StoredValue(
            _bool_flag(payload.outbound_answered_enabled), False
        ),
        "outbound_answered_text": StoredValue(payload.outbound_answered_text, False),
        "outbound_missed_enabled": StoredValue(_bool_flag(payload.outbound_missed_enabled), False),
        "outbound_missed_text": StoredValue(payload.outbound_missed_text, False),
        "outbound_repeat_answered_enabled": StoredValue(
            _bool_flag(payload.outbound_repeat_answered_enabled), False
        ),
        "outbound_repeat_answered_text": StoredValue(payload.outbound_repeat_answered_text, False),
        "outbound_repeat_missed_enabled": StoredValue(
            _bool_flag(payload.outbound_repeat_missed_enabled), False
        ),
        "outbound_repeat_missed_text": StoredValue(payload.outbound_repeat_missed_text, False),
        "cooldown_mode": StoredValue(payload.cooldown_mode, False),
        "cooldown_days": StoredValue(str(payload.cooldown_days), False),
        "opt_out_numbers": StoredValue(payload.opt_out_numbers or "", False),
    }

    await settings_store.set_namespace(session, "call_sms", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="call_sms_config_update",
        client_ip=admin_session.client_ip,
        payload={
            "enabled": payload.enabled,
            "inbound_enabled": payload.inbound_enabled,
            "outbound_enabled": payload.outbound_enabled,
            "cooldown_mode": payload.cooldown_mode,
            "cooldown_days": payload.cooldown_days,
        },
    )
    await session.commit()

    return await load_call_sms_config(session)


@router.post("/bulk", response_model=CallSmsBulkResponse, summary="Masowa wysylka SMS")
async def send_bulk_sms(
    payload: CallSmsBulkRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CallSmsBulkResponse:
    """Dodaje SMS do kolejki dla wszystkich numerow z historii polaczen."""
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)

    config = normalize_call_sms_config(await settings_store.get_namespace(session, "call_sms"))
    opt_out = parse_opt_out_numbers(config.opt_out_numbers)
    direction_filter = payload.direction.upper()
    days_back = payload.days_back
    threshold: datetime | None = None
    if days_back:
        threshold = datetime.now(UTC) - timedelta(days=days_back)

    stmt = select(Call.number).where(Call.number.isnot(None))
    if direction_filter != "ALL":
        stmt = stmt.where(Call.direction == direction_filter)
    if threshold is not None:
        stmt = stmt.where(Call.started_at >= threshold)
    stmt = stmt.distinct()

    rows = (await session.execute(stmt)).all()
    candidates: set[str] = set()
    skipped_invalid = 0
    skipped_non_mobile = 0
    skipped_opt_out = 0
    skipped_duplicates = 0

    for (number,) in rows:
        normalized = normalize_destination(number)
        if not normalized:
            skipped_invalid += 1
            continue
        if normalized in opt_out:
            skipped_opt_out += 1
            continue
        if not is_polish_mobile(normalized):
            skipped_non_mobile += 1
            continue
        if normalized in candidates:
            skipped_duplicates += 1
            continue
        candidates.add(normalized)

    skipped_cooldown = 0
    allowed: list[str] = []
    now = datetime.now(UTC)
    if config.cooldown_mode == "always":
        allowed = sorted(candidates)
    else:
        last_sms_map: dict[str, datetime] = {}
        if candidates:
            last_sms_stmt = (
                select(SmsOut.dest, func.max(SmsOut.created_at))
                .where(SmsOut.dest.in_(candidates), SmsOut.source == "call_sms")
                .group_by(SmsOut.dest)
            )
            for dest, last_ts in (await session.execute(last_sms_stmt)).all():
                if last_ts:
                    last_sms_map[dest] = last_ts

        for dest in sorted(candidates):
            last_ts = last_sms_map.get(dest)
            if last_ts is None:
                allowed.append(dest)
                continue
            if config.cooldown_mode == "never":
                skipped_cooldown += 1
                continue
            if config.cooldown_mode == "after_days":
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=UTC)
                if now - last_ts < timedelta(days=config.cooldown_days):
                    skipped_cooldown += 1
                    continue
            allowed.append(dest)

    bulk_id = uuid4().hex[:12] if allowed else None
    for dest in allowed:
        session.add(
            SmsOut(
                dest=dest,
                text=payload.text,
                status="NEW",
                source="call_sms",
                origin="admin-bulk",
                created_by=admin_user.id,
                meta={"scenario": "bulk", "bulk_id": bulk_id},
            )
        )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="call_sms_bulk_send",
        client_ip=admin_session.client_ip,
        payload={
            "direction": payload.direction,
            "days_back": payload.days_back,
            "created": len(allowed),
        },
    )
    await session.commit()

    return CallSmsBulkResponse(
        created=len(allowed),
        skipped_invalid=skipped_invalid,
        skipped_non_mobile=skipped_non_mobile,
        skipped_opt_out=skipped_opt_out,
        skipped_cooldown=skipped_cooldown,
        skipped_duplicates=skipped_duplicates,
        total_unique=len(candidates),
        bulk_id=bulk_id,
    )


@router.get("/history", response_model=CallSmsHistoryResponse, summary="Historia SMS polaczen")
async def get_call_sms_history(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=20, ge=5, le=200),
    status: str | None = Query(default=None, pattern=CALL_SMS_STATUS_PATTERN),
    scenario: str | None = Query(default=None, pattern=CALL_SMS_SCENARIO_PATTERN),
) -> CallSmsHistoryResponse:
    """Zwraca historie wiadomosci wyslanych przez automatyzacje polaczen."""
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    items = await load_call_sms_history(
        session, limit, status_filter=status, scenario_filter=scenario
    )
    now = datetime.now(UTC)
    return CallSmsHistoryResponse(
        generated_at=now,
        limit=limit,
        status=status,
        scenario=scenario,
        items=items,
    )


__all__ = [
    "CALL_SMS_SCENARIO_PATTERN",
    "CALL_SMS_STATUS_PATTERN",
    "router",
    "load_call_sms_config",
    "load_call_sms_history",
]
