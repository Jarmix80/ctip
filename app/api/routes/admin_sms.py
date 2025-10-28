"""Dodatkowe operacje związane z SerwerSMS."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_config import load_sms_config, settings_store
from app.models import SmsOut
from app.services.sms_provider import HttpSmsProvider, SmsTransportError
from log_utils import append_log, daily_log_path, read_log_tail

router = APIRouter(prefix="/admin/sms", tags=["admin-sms"])

ALLOWED_HISTORY_STATUSES = {"NEW", "RETRY", "SENT", "ERROR", "SIMULATED"}
HISTORY_STATUS_PATTERN = f"^({'|'.join(sorted(ALLOWED_HISTORY_STATUSES))})$"


def _ensure_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operacja wymaga roli administratora."
        )


class SmsTestRequest(BaseModel):
    dest: str = Field(
        ..., pattern=r"^\+[1-9]\d{7,14}$", description="Docelowy numer telefonu w formacie E.164"
    )
    text: str = Field(..., min_length=1, max_length=480)


class SmsTestResponse(BaseModel):
    success: bool
    message: str
    provider_status: str | None = None
    provider_message_id: str | None = None


class SmsLogEntry(BaseModel):
    timestamp: datetime | None
    message: str
    raw: str


class SmsLogResponse(BaseModel):
    generated_at: datetime
    log_path: str | None
    lines: list[SmsLogEntry]


class SmsHistoryEntry(BaseModel):
    id: int
    created_at: datetime
    dest: str
    text: str
    status: str
    origin: str | None = None
    provider_status: str | None = None
    provider_message_id: str | None = None
    error_msg: str | None = None


class SmsHistoryResponse(BaseModel):
    generated_at: datetime
    limit: int
    items: list[SmsHistoryEntry]
    status: str | None = None


@router.post("/test", response_model=SmsTestResponse, summary="Wyślij testowy SMS")
async def send_test_sms(
    payload: SmsTestRequest,
    context: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> SmsTestResponse:
    """Wysyła wiadomość testową przy użyciu aktualnej konfiguracji operatora."""
    _, admin_user = context
    _ensure_admin(admin_user.role)
    config = await load_sms_config(session)
    stored = await settings_store.get_namespace(session, "sms")

    token = stored.get("api_token") or None
    username = stored.get("api_username") or None
    password = stored.get("api_password") or None

    if not token and not (username and password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Brak danych logowania SerwerSMS (token lub login/hasło)",
        )

    provider = HttpSmsProvider(
        base_url=config.api_url,
        token=token,
        sender=config.default_sender,
        username=username,
        password=password,
        sms_type=config.sms_type,
        test_mode=config.test_mode,
        timeout=10.0,
    )

    try:
        result = provider.send_sms(
            payload.dest,
            payload.text,
            metadata={"origin": "admin-test"},
        )
    except SmsTransportError as exc:  # pragma: no cover - zależne od zewnętrznego API
        await _record_admin_test_event(
            session,
            admin_user.id,
            payload,
            success=False,
            provider_status=None,
            provider_message_id=None,
            error=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    error_text = None
    if not result.success:
        error_text = result.error or "Brak szczegółów błędu"

    await _record_admin_test_event(
        session,
        admin_user.id,
        payload,
        success=result.success,
        provider_status=result.provider_status,
        provider_message_id=result.provider_message_id,
        error=error_text,
    )

    if result.success:
        return SmsTestResponse(
            success=True,
            message="Wiadomość testowa została wysłana (tryb demo: {}".format(
                "tak" if config.test_mode else "nie"
            )
            + ")",
            provider_status=result.provider_status,
            provider_message_id=result.provider_message_id,
        )

    return SmsTestResponse(
        success=False,
        message=f"SerwerSMS zwrócił błąd: {error_text}",
        provider_status=result.provider_status,
        provider_message_id=result.provider_message_id,
    )


def parse_sms_log_line(raw: str) -> SmsLogEntry:
    raw = raw.rstrip("\n")
    timestamp: datetime | None = None
    message = raw
    closing = raw.find("] ")
    if raw.startswith("[") and closing != -1:
        ts_candidate = raw[1:closing]
        try:
            timestamp = datetime.strptime(ts_candidate, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            timestamp = None
        message = raw[closing + 2 :].strip()
    return SmsLogEntry(timestamp=timestamp, message=message, raw=raw)


def collect_sms_log_entries(
    limit: int,
    *,
    now: datetime | None = None,
) -> tuple[list[SmsLogEntry], str | None, datetime]:
    """Zwraca listę wpisów logu sms_sender wraz ze ścieżką pliku."""
    now = now or datetime.now(UTC)
    lines = read_log_tail("sms", "sms_sender", limit=limit, now=now)
    path = daily_log_path("sms", "sms_sender", now).as_posix()
    entries = [parse_sms_log_line(line) for line in lines]
    return entries, (path if lines else None), now


async def load_sms_history(
    session: AsyncSession, limit: int, status_filter: str | None = None
) -> list[SmsHistoryEntry]:
    """Pobiera historię wiadomości z tabeli sms_out."""
    stmt = select(
        SmsOut.id,
        SmsOut.created_at,
        SmsOut.dest,
        SmsOut.text,
        SmsOut.status,
        SmsOut.origin,
        SmsOut.provider_status,
        SmsOut.provider_msg_id,
        SmsOut.error_msg,
    ).order_by(desc(SmsOut.created_at))
    if status_filter:
        stmt = stmt.where(SmsOut.status == status_filter)
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    rows = result.all()
    return [
        SmsHistoryEntry(
            id=row.id,
            created_at=row.created_at,
            dest=row.dest,
            text=row.text,
            status=row.status,
            origin=row.origin,
            provider_status=row.provider_status,
            provider_message_id=row.provider_msg_id,
            error_msg=row.error_msg,
        )
        for row in rows
    ]


async def _record_admin_test_event(
    session: AsyncSession,
    admin_user_id: int,
    payload: SmsTestRequest,
    *,
    success: bool,
    provider_status: str | None,
    provider_message_id: str | None,
    error: str | None = None,
) -> SmsOut:
    """Zapisuje wynik testowej wysyłki do logu oraz tabeli sms_out."""
    status_label = "SIMULATED" if success else "ERROR"
    log_details = f"status={status_label}, dest={payload.dest}"
    if provider_status:
        log_details += f", provider_status={provider_status}"
    if provider_message_id:
        log_details += f", provider_msg_id={provider_message_id}"
    if error:
        log_details += f", error={error}"
    append_log("sms", "sms_sender", f"[admin-test] {log_details}")

    created_at = datetime.now(UTC).replace(tzinfo=None)
    sms = SmsOut(
        dest=payload.dest,
        text=payload.text,
        created_at=created_at,
        status=status_label,
        origin="admin-test",
        source="admin-test",
        created_by=admin_user_id,
        provider_status=provider_status,
        provider_msg_id=provider_message_id,
        error_msg=error,
        meta={"admin_test": True},
    )
    session.add(sms)
    await session.commit()
    await session.refresh(sms)
    return sms


@router.get("/logs", response_model=SmsLogResponse, summary="Odczytaj log sms_sender")
async def get_sms_logs(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    limit: int = Query(default=100, ge=1, le=500),
) -> SmsLogResponse:
    """Zwraca końcówkę dziennego logu pracy sms_sender."""
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    entries, path, now = collect_sms_log_entries(limit)
    return SmsLogResponse(
        generated_at=now,
        log_path=path,
        lines=entries,
    )


@router.get("/history", response_model=SmsHistoryResponse, summary="Historia wysyłki SMS")
async def get_sms_history(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=20, ge=1, le=200),
    status: str | None = Query(
        default=None, description="Filtr statusu wiadomości", pattern=HISTORY_STATUS_PATTERN
    ),
) -> SmsHistoryResponse:
    """Zwraca najnowsze wpisy z kolejki sms_out."""
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    items = await load_sms_history(session, limit, status)
    now = datetime.now(UTC)
    return SmsHistoryResponse(generated_at=now, limit=limit, items=items, status=status)


__all__ = ["router", "parse_sms_log_line", "collect_sms_log_entries", "load_sms_history"]
