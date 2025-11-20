"""Endpointy statusowe panelu administratora."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_config import (
    load_ctip_config,
    load_database_config,
    load_email_config,
    load_sms_config,
    settings_store,
)
from app.api.routes.admin_ctip import load_ivr_sms_history
from app.api.routes.admin_sms import load_sms_history
from app.core.config import settings
from app.models import CallEvent, SmsOut
from app.schemas.admin_ctip import AdminIvrSmsHistoryEntry

router = APIRouter(prefix="/admin/status", tags=["admin-status"])


async def _check_database(session: AsyncSession) -> dict[str, Any]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - treść zależy od środowiska
        return {
            "state": "error",
            "title": "Baza danych",
            "status": "Błąd połączenia",
            "details": str(exc),
            "variant": "warning",
            "cta": {"label": "Przejrzyj konfigurację", "action": "open-section:database"},
            "diagnostics_endpoint": "/admin/status/database",
        }

    db_config = await load_database_config(session)
    return {
        "state": "ok",
        "title": "Baza danych",
        "status": f"Połączono z {db_config.host}:{db_config.port}",
        "details": f"Baza: {db_config.database}, użytkownik: {db_config.user}",
        "variant": "success",
        "cta": {"label": "Testuj połączenie", "action": "test-database"},
        "secondary_cta": {"label": "Odśwież dashboard", "action": "refresh-dashboard"},
        "diagnostics_endpoint": "/admin/status/database",
    }


async def _ctip_metrics(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
    config = await load_ctip_config(session)
    host = config.host
    port = config.port
    configured = bool(host and config.pin_set)
    details = []
    now = datetime.now(UTC)
    minutes: int | None = None
    normalized_last_event: datetime | None = None
    if configured:
        status = f"{host}:{port}"
        variant = "info"
        details.append("PIN ustawiony" if config.pin_set else "Brak PIN-u w konfiguracji")
        state = "info"
    else:
        status = "Konfiguracja niepełna"
        variant = "warning"
        details.append("Uzupełnij adres centrali oraz PIN do LOGA")
        state = "warning"

    result = await session.execute(select(func.max(CallEvent.ts)))
    last_event_ts: datetime | None = result.scalar()
    if last_event_ts:
        if last_event_ts.tzinfo is None:
            normalized_last_event = last_event_ts.replace(tzinfo=UTC)
        else:
            normalized_last_event = last_event_ts.astimezone(UTC)
        delta = now - normalized_last_event
        minutes = int(delta.total_seconds() // 60)
        details.append(f"Ostatnie zdarzenie {minutes} min temu")
        if delta.total_seconds() <= 300:
            state = "ok"
            variant = "success"
        elif delta.total_seconds() > 1800:
            state = "warning"
            variant = "warning"
            status = "Brak zdarzeń CTIP"
    else:
        details.append("Brak zdarzeń w bazie")
        state = "warning"
        variant = "warning"
        status = "Brak danych CTIP"

    threshold_param = now - timedelta(hours=1)
    if threshold_param.tzinfo is not None:
        threshold_param = threshold_param.replace(tzinfo=None)

    events_last_hour_stmt = select(func.count(CallEvent.id)).where(CallEvent.ts >= threshold_param)
    events_last_hour = (await session.execute(events_last_hour_stmt)).scalar() or 0
    details.append(f"Zdarzenia (60 min): {events_last_hour}")

    card = {
        "state": state,
        "title": "Centrala CTIP",
        "status": status,
        "details": " • ".join(details),
        "variant": variant,
        "cta": {"label": "Edytuj konfigurację", "action": "open-section:ctip-config"},
        "secondary_cta": {"label": "Podgląd na żywo", "action": "open-section:ctip"},
        "diagnostics_endpoint": "/admin/status/ctip",
    }
    diagnostics = {
        "state": state,
        "host": host,
        "port": port,
        "pin_set": config.pin_set,
        "last_event_at": normalized_last_event.isoformat() if normalized_last_event else None,
        "minutes_since_last_event": minutes,
        "events_last_hour": events_last_hour,
        "details": card["details"],
    }
    return card, diagnostics


async def _ctip_status(session: AsyncSession) -> dict[str, Any]:
    card, _ = await _ctip_metrics(session)
    return card


async def _sms_metrics(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
    config = await load_sms_config(session)
    stored = await settings_store.get_namespace(session, "sms")
    token = stored.get("api_token") or settings.sms_api_token
    password = stored.get("api_password") or settings.sms_api_password
    credentials_ok = bool(token or (config.api_username and password))
    if credentials_ok:
        status = f"Nadawca: {config.default_sender}"
        details = "Tryb demo aktywny" if config.test_mode else "Tryb produkcyjny"
        variant = "success" if not config.test_mode else "info"
        state = "ok" if not config.test_mode else "info"
    else:
        status = "Brak danych logowania"
        details = "Uzupełnij token lub login/hasło operatora"
        variant = "warning"
        state = "warning"

    failed_stmt = select(func.count(SmsOut.id)).where(SmsOut.status == "ERROR")
    failed_count = (await session.execute(failed_stmt)).scalar() or 0

    latest_error_stmt = (
        select(SmsOut.dest, SmsOut.error_msg, SmsOut.created_at, SmsOut.provider_status)
        .where(SmsOut.status == "ERROR")
        .order_by(SmsOut.created_at.desc())
        .limit(1)
    )
    latest = await session.execute(latest_error_stmt)
    latest_failure = latest.first()

    pending_stmt = select(func.count(SmsOut.id)).where(SmsOut.status.in_(("NEW", "RETRY")))
    pending_count = (await session.execute(pending_stmt)).scalar() or 0

    details += f" • Kolejka: {pending_count} w toku, błędy: {failed_count}"

    if failed_count and latest_failure:
        dest, error_msg, created_at, provider_status = latest_failure
        extra = f"Błędy wysyłki: {failed_count}"
        if created_at:
            extra += f" (ostatni {created_at:%Y-%m-%d %H:%M})"
        if error_msg:
            extra += f" – {error_msg}"
        if provider_status:
            extra += f" [{provider_status}]"
        details += f" • {extra}"
        state = "warning"
        variant = "warning"

    card = {
        "state": state,
        "title": "SerwerSMS",
        "status": status,
        "details": details,
        "variant": variant,
        "cta": {"label": "Ustaw parametry", "action": "open-section:sms"},
        "secondary_cta": {"label": "Historia wysyłek", "action": "open-section:sms-history"},
        "diagnostics_endpoint": "/admin/status/sms",
    }

    diagnostics = {
        "state": state,
        "credentials_present": credentials_ok,
        "default_sender": config.default_sender,
        "sms_type": config.sms_type,
        "test_mode": config.test_mode,
        "pending_messages": pending_count,
        "failed_messages": failed_count,
        "last_failure": {
            "dest": latest_failure[0] if latest_failure else None,
            "error": latest_failure[1] if latest_failure else None,
            "provider_status": latest_failure[3] if latest_failure else None,
            "occurred_at": (
                latest_failure[2].isoformat() if latest_failure and latest_failure[2] else None
            ),
        },
        "details": card["details"],
    }
    history_preview = await load_sms_history(session, limit=5)
    diagnostics["recent_messages"] = [
        {
            "id": item.id,
            "dest": item.dest,
            "status": item.status,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "provider_status": item.provider_status,
            "error_msg": item.error_msg,
        }
        for item in history_preview
    ]
    return card, diagnostics


async def _ivr_automation_status(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
    pending_q = await session.execute(
        select(func.count(SmsOut.id)).where(SmsOut.source == "ivr", SmsOut.status == "NEW")
    )
    error_q = await session.execute(
        select(func.count(SmsOut.id)).where(SmsOut.source == "ivr", SmsOut.status == "ERROR")
    )
    sent_q = await session.execute(
        select(func.count(SmsOut.id)).where(SmsOut.source == "ivr", SmsOut.status == "SENT")
    )
    pending = int(pending_q.scalar() or 0)
    errors = int(error_q.scalar() or 0)
    sent = int(sent_q.scalar() or 0)

    last_entry: AdminIvrSmsHistoryEntry | None = None
    history: list[AdminIvrSmsHistoryEntry] = await load_ivr_sms_history(session, limit=5)
    if history:
        last_entry = history[0]

    if errors > 0:
        state = "warning"
        variant = "warning"
        status = f"Błędy: {errors}, w kolejce: {pending}"
    else:
        state = "ok"
        variant = "success" if pending == 0 else "info"
        status = f"W kolejce: {pending}, wysłane: {sent}"

    details = "Brak ostatnich wysyłek IVR"
    if last_entry:
        ts = (
            last_entry.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            if last_entry.created_at.tzinfo
            else last_entry.created_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        details = f"Ostatnia wysyłka: {ts} → {last_entry.dest} ({last_entry.status})"

    card = {
        "state": state,
        "title": "Automatyczne SMS (IVR)",
        "status": status,
        "details": details,
        "variant": variant,
        "cta": {"label": "Konfiguracja", "action": "open-section:ctip-config"},
        "secondary_cta": {
            "label": "Historia IVR",
            "action": "open-section:ctip-config",
        },
        "diagnostics_endpoint": "/admin/status/ivr",
    }

    diagnostics = {
        "pending": pending,
        "errors": errors,
        "sent": sent,
        "recent": [item.model_dump() for item in history],
        "last_entry": last_entry.model_dump() if last_entry else None,
    }
    return card, diagnostics


async def _email_metrics(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
    config = await load_email_config(session)
    stored = await settings_store.get_namespace(session, "email")
    password_present = bool(stored.get("password") or settings.email_password)

    encryption = (
        "STARTTLS" if config.use_tls else "SSL/TLS" if config.use_ssl else "Brak szyfrowania"
    )

    if config.host and config.sender_address:
        status = f"{config.host}:{config.port}"
        details = f"Nadawca: {config.sender_address} • {encryption}"
        state = "ok"
        variant = "success"
        if config.username and not password_present:
            details += " • Brak hasła do logowania"
            state = "warning"
            variant = "warning"
    else:
        status = "Konfiguracja niepełna"
        details = "Uzupełnij host, port oraz adres nadawcy"
        state = "warning"
        variant = "warning"

    card = {
        "state": state,
        "title": "E-mail SMTP",
        "status": status,
        "details": details,
        "variant": variant,
        "cta": {"label": "Konfiguracja", "action": "open-section:email"},
        "secondary_cta": {"label": "Testuj połączenie", "action": "test-email"},
    }

    diagnostics = {
        "state": state,
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "sender_name": config.sender_name,
        "sender_address": config.sender_address,
        "use_tls": config.use_tls,
        "use_ssl": config.use_ssl,
        "password_present": password_present,
        "details": details,
    }
    return card, diagnostics


async def _backups_status(session: AsyncSession) -> dict[str, Any]:  # noqa: ARG001
    # Wersja MVP – brak tabeli metadanych, więc posługujemy się placeholderem.
    return {
        "state": "info",
        "title": "Kopie zapasowe",
        "status": "Brak danych o ostatniej kopii",
        "details": "Moduł backupów zostanie zintegrowany w kolejnych iteracjach.",
        "variant": "neutral",
        "cta": {"label": "Przejdź do modułu kopii", "action": "open-section:backups"},
    }


async def compute_status_summary(session: AsyncSession) -> list[dict[str, Any]]:
    """Zwraca listę kart statusowych do wyświetlenia w Dashboardzie."""
    ctip_card, _ = await _ctip_metrics(session)
    ivr_card, _ = await _ivr_automation_status(session)
    sms_card, _ = await _sms_metrics(session)
    email_card, _ = await _email_metrics(session)
    return [
        await _check_database(session),
        ctip_card,
        ivr_card,
        sms_card,
        email_card,
        await _backups_status(session),
    ]


@router.get("/database")
async def database_status(
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Sprawdza połączenie z bazą danych."""
    card = await _check_database(session)
    diagnostics = {
        "card": card,
        "state": card.get("state"),
        "status": card.get("status"),
        "details": card.get("details"),
        "checked_at": datetime.now(UTC).isoformat(),
    }
    return diagnostics


@router.get("/ctip")
async def ctip_status(
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca szczegółową diagnostykę połączenia z centralą CTIP."""
    card, diagnostics = await _ctip_metrics(session)
    diagnostics_with_card = {**diagnostics, "card": card}
    return diagnostics_with_card


@router.get("/sms")
async def sms_status(
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca szczegółową diagnostykę konfiguracji SerwerSMS."""
    card, diagnostics = await _sms_metrics(session)
    diagnostics_with_card = {**diagnostics, "card": card}
    return diagnostics_with_card


@router.get("/ivr")
async def ivr_status(
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Diagnostyka automatycznych SMS IVR."""
    card, diagnostics = await _ivr_automation_status(session)
    diagnostics_with_card = {**diagnostics, "card": card}
    return diagnostics_with_card


@router.get("/summary")
async def summary_status(
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca skondensowany status głównych modułów."""
    cards = await compute_status_summary(session)
    return {"cards": cards}


__all__ = ["router", "compute_status_summary"]
