"""Endpointy diagnostyczne i strumień WebSocket dla zdarzeń CTIP."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.db.session import AsyncSessionLocal
from app.models import Call, CallEvent
from app.schemas.admin_ctip import (
    AdminIvrMapCreate,
    AdminIvrMapEntry,
    AdminIvrMapListResponse,
    AdminIvrMapUpdate,
)
from app.services import admin_ivr_map
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/ctip", tags=["admin-ctip"])

# Ramki typu "T" (keep-alive) zaciemniają log – pomijamy je domyślnie
TRANSIENT_TYPES = {"T"}


class CtipEventEntry(BaseModel):
    ts: datetime
    typ: str
    ext: str | None = None
    number: str | None = None
    payload: str | None = None


class CtipEventResponse(BaseModel):
    generated_at: datetime
    limit: int
    ext: str | None = None
    items: list[CtipEventEntry]


def _should_include(typ: str | None) -> bool:
    if typ is None:
        return False
    normalized = typ.strip().upper()
    if normalized.startswith("A") and len(normalized) > 1:
        normalized = normalized[1:]
    if not normalized:
        return False
    if normalized in TRANSIENT_TYPES:
        return False
    return True


def _normalize_type(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if trimmed.upper().startswith("A") and len(trimmed) > 1:
        return trimmed[1:]
    return trimmed


def _ensure_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Wymagane uprawnienia administratora."
        )


async def load_ctip_events(
    session: AsyncSession,
    limit: int,
    *,
    ext_filter: str | None = None,
) -> list[CtipEventEntry]:
    """Zwraca najnowsze zdarzenia CTIP z pominięciem ramek keep-alive."""
    stmt = (
        select(
            CallEvent.ts,
            CallEvent.typ,
            func.coalesce(CallEvent.ext, Call.ext).label("event_ext"),
            func.coalesce(CallEvent.number, Call.number).label("event_number"),
            CallEvent.payload,
        )
        .join(Call, Call.id == CallEvent.call_id, isouter=True)
        .order_by(desc(CallEvent.ts))
    )

    trimmed_typ = func.upper(func.trim(CallEvent.typ))
    stmt = stmt.where((CallEvent.typ.is_(None)) | (trimmed_typ != "T"))
    if ext_filter:
        stmt = stmt.where(CallEvent.ext == ext_filter)

    stmt = stmt.limit(limit * 2)

    rows = (await session.execute(stmt)).all()
    items: list[CtipEventEntry] = []
    for row in rows:
        typ = row.typ
        if not _should_include(typ):
            continue
        normalized_typ = _normalize_type(typ) or typ
        ext_value = row.event_ext if hasattr(row, "event_ext") else row.ext
        number_value = row.event_number if hasattr(row, "event_number") else row.number
        if ext_value is None and number_value is None and row.payload:
            ext_value, number_value = _extract_from_payload(row.payload)
        items.append(
            CtipEventEntry(
                ts=row.ts,
                typ=normalized_typ,
                ext=ext_value,
                number=number_value,
                payload=row.payload,
            )
        )
        if len(items) >= limit:
            break
    return items


@router.get("/events", response_model=CtipEventResponse, summary="Najnowsze zdarzenia CTIP")
async def get_ctip_events(
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=50, ge=5, le=200),
    ext: str | None = Query(default=None, min_length=1, max_length=16),
) -> CtipEventResponse:
    """Zwraca listę zdarzeń CTIP na potrzeby podglądu live."""
    items = await load_ctip_events(session, limit, ext_filter=ext)
    now = datetime.now(UTC)
    return CtipEventResponse(generated_at=now, limit=limit, ext=ext, items=items)


@router.get("/ivr-map", response_model=AdminIvrMapListResponse, summary="Lista mapowań IVR")
async def admin_ivr_map_list(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminIvrMapListResponse:
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    entries = await admin_ivr_map.list_entries(session)
    items = [
        AdminIvrMapEntry(
            ext=entry.ext, digit=entry.digit, sms_text=entry.sms_text, enabled=entry.enabled
        )
        for entry in entries
    ]
    return AdminIvrMapListResponse(items=items)


@router.post(
    "/ivr-map",
    response_model=AdminIvrMapEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Dodaj mapowanie IVR",
)
async def admin_ivr_map_create(
    payload: AdminIvrMapCreate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminIvrMapEntry:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    entry = await admin_ivr_map.upsert_entry(
        session,
        ext=payload.ext,
        digit=payload.digit,
        sms_text=payload.sms_text,
        enabled=payload.enabled,
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="ivr_map_create",
        client_ip=admin_session.client_ip,
        payload={"ext": entry.ext, "digit": entry.digit},
    )
    await session.commit()
    return AdminIvrMapEntry(
        ext=entry.ext, digit=entry.digit, sms_text=entry.sms_text, enabled=entry.enabled
    )


@router.put("/ivr-map/{ext}", response_model=AdminIvrMapEntry, summary="Aktualizuj mapowanie IVR")
async def admin_ivr_map_update(
    payload: AdminIvrMapUpdate,
    ext: str = Path(..., min_length=1, max_length=32),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminIvrMapEntry:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    entry = await admin_ivr_map.upsert_entry(
        session,
        ext=ext,
        digit=payload.digit,
        sms_text=payload.sms_text,
        enabled=payload.enabled,
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="ivr_map_update",
        client_ip=admin_session.client_ip,
        payload={"ext": entry.ext, "digit": entry.digit, "enabled": entry.enabled},
    )
    await session.commit()
    return AdminIvrMapEntry(
        ext=entry.ext, digit=entry.digit, sms_text=entry.sms_text, enabled=entry.enabled
    )


@router.delete(
    "/ivr-map/{ext}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Usuń mapowanie IVR",
)
async def admin_ivr_map_delete(
    ext: str = Path(..., min_length=1, max_length=32),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    existing = await admin_ivr_map.fetch_entry(session, ext)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapowanie nie istnieje.")
    await admin_ivr_map.delete_entry(session, ext)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="ivr_map_delete",
        client_ip=admin_session.client_ip,
        payload={"ext": ext},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class _CtipStreamManager:
    """Zarządza klientami WebSocket z podglądem CTIP."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.interval = 3.0

    async def connect(self, websocket: WebSocket, *, ext: str | None, limit: int) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[websocket] = {"ext": ext, "limit": limit}
            if not self._task:
                self._task = asyncio.create_task(self._broadcast_loop())

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)
            if not self._clients and self._task:
                self._task.cancel()
                self._task = None

    async def update_filter(self, websocket: WebSocket, *, ext: str | None, limit: int) -> None:
        async with self._lock:
            if websocket in self._clients:
                self._clients[websocket]["ext"] = ext
                self._clients[websocket]["limit"] = limit

    async def send_snapshot(self, websocket: WebSocket) -> None:
        async with AsyncSessionLocal() as session:
            state = self._clients.get(websocket, {"ext": None, "limit": 50})
            items = await load_ctip_events(session, state["limit"], ext_filter=state.get("ext"))
        payload = {
            "type": "events",
            "generated_at": datetime.now(UTC).isoformat(),
            "limit": state["limit"],
            "ext": state.get("ext"),
            "items": [item.model_dump() for item in items],
        }
        await websocket.send_json(payload)

    async def _broadcast_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.interval)
                async with self._lock:
                    clients_snapshot = list(self._clients.items())
                if not clients_snapshot:
                    continue
                async with AsyncSessionLocal() as session:
                    for ws, state in clients_snapshot:
                        try:
                            items = await load_ctip_events(
                                session, state["limit"], ext_filter=state.get("ext")
                            )
                            payload = {
                                "type": "events",
                                "generated_at": datetime.now(UTC).isoformat(),
                                "limit": state["limit"],
                                "ext": state.get("ext"),
                                "items": [item.model_dump() for item in items],
                            }
                            await ws.send_json(payload)
                        except Exception as exc:  # pragma: no cover
                            logger.exception("Błąd wysyłania strumienia CTIP: %s", exc)
                            try:
                                await ws.close(code=status.WS_1011_INTERNAL_ERROR)
                            finally:
                                await self.disconnect(ws)
        except asyncio.CancelledError:  # pragma: no cover
            logger.debug("Pętla streamu CTIP zatrzymana")
        finally:
            self._task = None


stream_manager = _CtipStreamManager()


@router.websocket("/ws")
async def ctip_events_ws(websocket: WebSocket) -> None:
    """Dwukierunkowy kanał WebSocket publikujący zdarzenia CTIP."""
    token = websocket.query_params.get("token") or websocket.headers.get("X-Admin-Session")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    limit_param = websocket.query_params.get("limit")
    try:
        limit = int(limit_param) if limit_param else 50
    except ValueError:
        limit = 50
    limit = min(max(limit, 5), 200)

    ext = websocket.query_params.get("ext")
    if ext is not None:
        ext = ext.strip() or None

    async with AsyncSessionLocal() as session:
        try:
            await get_admin_session_context(token, session)
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await stream_manager.connect(websocket, ext=ext, limit=limit)
    try:
        await stream_manager.send_snapshot(websocket)
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:  # pragma: no cover
                await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
                return

            if not isinstance(message, dict):
                continue

            if message.get("type") == "filter":
                new_ext = message.get("ext")
                if isinstance(new_ext, str):
                    new_ext = new_ext.strip() or None
                else:
                    new_ext = None
                new_limit = message.get("limit")
                try:
                    new_limit_int = int(new_limit)
                except (TypeError, ValueError):
                    new_limit_int = limit
                new_limit_int = min(max(new_limit_int, 5), 200)
                await stream_manager.update_filter(websocket, ext=new_ext, limit=new_limit_int)
                await stream_manager.send_snapshot(websocket)
    finally:
        await stream_manager.disconnect(websocket)


_DIGIT_PATTERN = re.compile(r"\+?\d+")


def _extract_from_payload(payload: str) -> tuple[str | None, str | None]:
    if not payload:
        return None, None
    ext_guess: str | None = None
    number_guess: str | None = None
    for token in _DIGIT_PATTERN.findall(payload):
        cleaned = token.strip()
        if not cleaned:
            continue
        if cleaned.startswith("+") or len(cleaned) >= 7:
            if number_guess is None:
                number_guess = cleaned
            continue
        if len(cleaned) <= 6 and ext_guess is None:
            ext_guess = cleaned
    return ext_guess, number_guess


__all__ = ["router", "load_ctip_events"]
