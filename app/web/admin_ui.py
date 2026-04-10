"""Widoki panelu administratora renderowane po stronie serwera."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_call_sms import (
    CALL_SMS_SCENARIO_PATTERN,
    CALL_SMS_STATUS_PATTERN,
    load_call_sms_config,
    load_call_sms_history,
)
from app.api.routes.admin_config import (
    load_ctip_config,
    load_database_config,
    load_email_config,
    load_firebird_config,
    load_firebird_vmaintenance_config,
    load_form_handling_config,
    load_kp_repair_source_config,
    load_sms_config,
)
from app.api.routes.admin_ctip import (
    IVR_HISTORY_STATUS_PATTERN,
    load_ctip_events,
    load_ivr_sms_history,
)
from app.api.routes.admin_sms import (
    HISTORY_STATUS_PATTERN,
    collect_sms_log_entries,
    load_sms_history,
)
from app.api.routes.admin_status import compute_status_summary
from app.schemas.admin import (
    AdminUserSummary,
    CtipConfigResponse,
    DatabaseConfigResponse,
    FirebirdConfigResponse,
    FirebirdVMaintenanceConfigResponse,
    FormHandlingConfigResponse,
    KpRepairSourceConfigResponse,
    SmsConfigResponse,
)
from app.schemas.admin_contacts import AdminContactSummary
from app.schemas.admin_ctip import AdminIvrMapEntry
from app.services import admin_contacts, admin_ivr_map, admin_users, section_permissions
from app.services.backup_runner import BACKUP_DIR, format_backup_size, list_backup_files
from app.services.call_sms_rules import CALL_SMS_SCENARIO_LABELS

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["admin-ui"])


@router.get("/admin", response_class=HTMLResponse)
async def admin_index(request: Request) -> HTMLResponse:
    """Zwraca stronę główną panelu administracyjnego."""
    return templates.TemplateResponse("admin/index.html", {"request": request})


@router.get("/admin/partials/dashboard", response_class=HTMLResponse)
async def admin_dashboard_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Fragment HTML z kartami statusów na potrzeby HTMX."""
    cards = await compute_status_summary(session)
    return templates.TemplateResponse(
        "admin/partials/dashboard.html", {"request": request, "cards": cards}
    )


@router.get("/admin/partials/coming-soon", response_class=HTMLResponse)
async def admin_placeholder_partial(
    request: Request,
    title: str = Query(default="Funkcja w przygotowaniu"),
    description: str = Query(
        default="Moduł jest na etapie projektowania. Wkrótce pojawi się pierwszy podgląd."
    ),
) -> HTMLResponse:
    """Fragment informujący o sekcjach jeszcze niedostępnych."""
    return templates.TemplateResponse(
        "admin/partials/coming_soon.html",
        {
            "request": request,
            "title": title,
            "description": description,
        },
    )


@router.get("/admin/partials/backups", response_class=HTMLResponse)
async def admin_backups_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
) -> HTMLResponse:
    """Fragment HTML dla modułu kopii zapasowych."""
    items = []
    for entry in list_backup_files(limit=200):
        formatted_ts = (
            entry.modified_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            if entry.modified_at.tzinfo
            else entry.modified_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        items.append(
            {
                "name": entry.name,
                "size": format_backup_size(entry.size_bytes),
                "size_bytes": entry.size_bytes,
                "modified_at": formatted_ts,
                "status": entry.status,
                "checksum": entry.checksum,
            }
        )
    return templates.TemplateResponse(
        "admin/partials/backups.html",
        {
            "request": request,
            "items": items,
            "backup_dir": BACKUP_DIR.as_posix(),
            "note": "Operacje kopii zapasowych i przywracania są obecnie zablokowane.",
        },
    )


@router.get("/admin/partials/config/database", response_class=HTMLResponse)
async def admin_database_config_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Formularz konfiguracji baz danych i źródeł CSV."""
    config: DatabaseConfigResponse = await load_database_config(session)
    firebird_config: FirebirdConfigResponse = await load_firebird_config(session)
    firebird_v_config: FirebirdVMaintenanceConfigResponse = await load_firebird_vmaintenance_config(
        session
    )
    kp_source_config: KpRepairSourceConfigResponse = await load_kp_repair_source_config(session)
    return templates.TemplateResponse(
        "admin/partials/config_database.html",
        {
            "request": request,
            "config": config.model_dump(),
            "firebird_config": firebird_config.model_dump(),
            "firebird_v_config": firebird_v_config.model_dump(),
            "kp_source_config": kp_source_config.model_dump(),
        },
    )


@router.get("/admin/partials/config/firebird", response_class=HTMLResponse)
async def admin_firebird_config_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Formularz konfiguracji połączenia Firebird."""
    config: FirebirdConfigResponse = await load_firebird_config(session)
    return templates.TemplateResponse(
        "admin/partials/config_firebird.html",
        {
            "request": request,
            "config": config.model_dump(),
        },
    )


@router.get("/admin/partials/kp-repair", response_class=HTMLResponse)
async def admin_kp_repair_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Widok narzędzia naprawy oznaczeń KP/xxxx."""
    source_config: KpRepairSourceConfigResponse = await load_kp_repair_source_config(session)
    return templates.TemplateResponse(
        "admin/partials/kp_repair.html",
        {
            "request": request,
            "source_config": source_config.model_dump(),
        },
    )


@router.get("/admin/partials/config/ctip", response_class=HTMLResponse)
async def admin_ctip_config_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Formularz konfiguracji centrali CTIP."""
    config: CtipConfigResponse = await load_ctip_config(session)
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/config_ctip.html",
        {
            "request": request,
            "config": config.model_dump(),
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/ctip/ivr-map", response_class=HTMLResponse)
async def admin_ctip_ivr_map_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Fragment konfiguracji automatycznych SMS w oparciu o IVR."""
    entries = await admin_ivr_map.list_entries(session)
    admin_token = request.headers.get("x-admin-session", "")
    serialized = [
        AdminIvrMapEntry(
            ext=item.ext, digit=item.digit, sms_text=item.sms_text, enabled=item.enabled
        ).model_dump()
        for item in entries
    ]
    return templates.TemplateResponse(
        "admin/partials/ctip_ivr_map.html",
        {
            "request": request,
            "entries": serialized,
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/ctip/ivr-history", response_class=HTMLResponse)
async def admin_ctip_ivr_history_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=25, ge=5, le=200),
    status: str | None = Query(default=None, pattern=IVR_HISTORY_STATUS_PATTERN),
    ext: str | None = Query(default=None, min_length=1, max_length=16),
) -> HTMLResponse:
    """Fragment HTML przedstawiający historię wysyłek IVR."""
    items = await load_ivr_sms_history(session, limit, status_filter=status, ext_filter=ext)
    generated_at = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/ctip_ivr_history.html",
        {
            "request": request,
            "items": items,
            "limit": limit,
            "status": status,
            "ext": ext,
            "generated_at": generated_at,
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/config/sms", response_class=HTMLResponse)
async def admin_sms_config_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Formularz konfiguracji operatora SMS."""
    config: SmsConfigResponse = await load_sms_config(session)
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/config_sms.html",
        {
            "request": request,
            "config": config.model_dump(),
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/call-sms", response_class=HTMLResponse)
async def admin_call_sms_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Formularz konfiguracji SMS wysyłanych po połączeniach."""
    config = await load_call_sms_config(session)
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/call_sms.html",
        {
            "request": request,
            "config": config.model_dump(),
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/call-sms/history", response_class=HTMLResponse)
async def admin_call_sms_history_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=25, ge=5, le=200),
    status: str | None = Query(default=None, pattern=CALL_SMS_STATUS_PATTERN),
    scenario: str | None = Query(default=None, pattern=CALL_SMS_SCENARIO_PATTERN),
) -> HTMLResponse:
    """Fragment HTML z historią SMS generowanych po połączeniach."""
    items = await load_call_sms_history(
        session, limit, status_filter=status, scenario_filter=scenario
    )
    generated_at = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/call_sms_history.html",
        {
            "request": request,
            "items": items,
            "limit": limit,
            "status": status,
            "scenario": scenario,
            "scenario_labels": CALL_SMS_SCENARIO_LABELS,
            "generated_at": generated_at,
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/sms/logs", response_class=HTMLResponse)
async def admin_sms_logs_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    limit: int = Query(default=50, ge=5, le=300),
) -> HTMLResponse:
    """Fragment HTML z końcówką logu sms_sender."""
    entries, log_path, generated_at = collect_sms_log_entries(limit)
    refreshed_at = (
        generated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        if generated_at.tzinfo
        else generated_at.strftime("%Y-%m-%d %H:%M:%S")
    )
    return templates.TemplateResponse(
        "admin/partials/sms_logs.html",
        {
            "request": request,
            "entries": entries,
            "log_path": log_path,
            "generated_at": refreshed_at,
        },
    )


@router.get("/admin/partials/sms/history", response_class=HTMLResponse)
async def admin_sms_history_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=20, ge=5, le=200),
    status: str | None = Query(
        default=None,
        pattern=HISTORY_STATUS_PATTERN,
        description="Filtr statusu wiadomości",
    ),
) -> HTMLResponse:
    """Fragment HTML prezentujący najnowszą historię wysyłek SMS."""
    items = await load_sms_history(session, limit, status)
    generated_at = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/sms_history.html",
        {
            "request": request,
            "items": items,
            "limit": limit,
            "generated_at": generated_at,
            "status": status,
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/ctip/events", response_class=HTMLResponse)
async def admin_ctip_events_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=50, ge=5, le=200),
    ext: str | None = Query(default=None, min_length=1, max_length=16),
) -> HTMLResponse:
    """Fragment HTML z listą ostatnich zdarzeń CTIP."""
    items = await load_ctip_events(session, limit, ext_filter=ext)
    generated_at = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/ctip_events.html",
        {
            "request": request,
            "items": items,
            "limit": limit,
            "ext": ext,
            "generated_at": generated_at,
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/ctip/live", response_class=HTMLResponse)
async def admin_ctip_live_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
) -> HTMLResponse:
    """Podgląd na żywo zdarzeń CTIP z miejscem na konfigurację."""
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/ctip_live.html",
        {
            "request": request,
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/config/email", response_class=HTMLResponse)
async def admin_email_config_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Formularz konfiguracji serwera SMTP."""
    config = await load_email_config(session)
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/config_email.html",
        {
            "request": request,
            "config": config.model_dump(),
            "admin_token": admin_token,
        },
    )


@router.get("/admin/partials/config/form-handling", response_class=HTMLResponse)
async def admin_form_handling_config_partial(
    request: Request,
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Formularz konfiguracji publicznego formularza i powiadomień."""
    config: FormHandlingConfigResponse = await load_form_handling_config(session)
    return templates.TemplateResponse(
        "admin/partials/config_form_handling.html",
        {
            "request": request,
            "config": config.model_dump(),
        },
    )


def _contact_summary(contact) -> AdminContactSummary:
    return AdminContactSummary(
        id=contact.id,
        number=contact.number,
        ext=contact.ext,
        firebird_id=contact.firebird_id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        company=contact.company,
        email=contact.email,
        nip=contact.nip,
        notes=contact.notes,
        source=contact.source,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
    )


@router.get("/admin/partials/contacts", response_class=HTMLResponse)
async def admin_contacts_partial(
    request: Request,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Widok HTMX dla modułu książki adresowej."""
    _, admin_user = admin_context
    can_edit = admin_user.role in {"admin", "operator"}
    can_delete = admin_user.role == "admin"
    contacts = await admin_contacts.list_contacts(session)
    items = [_contact_summary(contact).model_dump(mode="json") for contact in contacts]
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/contacts.html",
        {
            "request": request,
            "contacts": items,
            "can_manage": can_edit,
            "can_delete": can_delete,
            "admin_token": admin_token,
        },
    )


def _user_summary_from_row(row: admin_users.UserRow, sections: list[str]) -> AdminUserSummary:
    user = row.user
    return AdminUserSummary(
        id=user.id,
        email=user.email,
        mobile_phone=user.mobile_phone,
        first_name=user.first_name,
        last_name=user.last_name,
        internal_ext=user.internal_ext,
        role=user.role,
        is_salesperson=bool(user.is_salesperson),
        firebird_app_user_id=user.firebird_app_user_id,
        firebird_app_user_login=user.firebird_app_user_login,
        sections=sections,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=row.last_login_at,
        sessions_active=row.sessions_active,
    )


@router.get("/admin/partials/users", response_class=HTMLResponse)
async def admin_users_partial(
    request: Request,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Widok listy użytkowników panelu."""
    _, admin_user = admin_context
    rows = await admin_users.list_users(session)
    sections_map = await section_permissions.list_user_sections(session, [row.user for row in rows])
    summaries = [
        _user_summary_from_row(row, sections_map.get(row.user.id, [])).model_dump(mode="json")
        for row in rows
    ]
    admin_token = request.headers.get("x-admin-session", "")
    return templates.TemplateResponse(
        "admin/partials/users.html",
        {
            "request": request,
            "users": summaries,
            "can_manage": admin_user.role == "admin",
            "admin_token": admin_token,
            "current_user_id": admin_user.id,
        },
    )


__all__ = ["router"]
