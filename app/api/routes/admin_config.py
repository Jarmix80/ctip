"""Trasy konfiguracji panelu administracyjnego."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.schemas.admin import (
    CtipConfigResponse,
    CtipConfigUpdate,
    DatabaseConfigResponse,
    DatabaseConfigUpdate,
    EmailConfigResponse,
    EmailConfigUpdate,
    FirebirdConfigResponse,
    FirebirdConfigUpdate,
    FirebirdVMaintenanceConfigResponse,
    FirebirdVMaintenanceConfigUpdate,
    FormHandlingConfigResponse,
    FormHandlingConfigUpdate,
    GoogleSheetsConfigResponse,
    GoogleSheetsConfigUpdate,
    KpRepairSourceConfigResponse,
    KpRepairSourceConfigUpdate,
    SmsConfigResponse,
    SmsConfigUpdate,
)
from app.services.audit import record_audit
from app.services.form_handling_config import (
    load_form_handling_config as load_form_handling_runtime_config,
)
from app.services.form_handling_config import (
    normalize_public_base_url,
    validate_form_handling_templates,
)
from app.services.settings_store import StoredValue, build_store
from app.services.workflow_sheet_sync import (
    load_workflow_sheet_runtime_config,
    normalize_workflow_sheet_spreadsheet_id,
)

router = APIRouter(prefix="/admin/config", tags=["admin"])

settings_store = build_store(settings.admin_secret_key)
ENV_LOCK_REASON = (
    "Źródłem prawdy dla danych połączeniowych jest plik .env. "
    "Edycja tej sekcji w panelu została zablokowana."
)
ENV_LOCK_DETAIL = (
    f"{ENV_LOCK_REASON} Aby zmienić konfigurację, zaktualizuj odpowiedni plik środowiskowy "
    "i uruchom ponownie właściwą usługę."
)


def _assert_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operacja wymaga roli administratora."
        )


def _to_int(value: str | int) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Wartość musi być liczbą całkowitą"
        ) from exc


def _normalize_firebird_mode(value: str | None) -> str:
    """Normalizuje tryb połączenia Firebird."""
    mode = (value or "").strip().lower() or settings.fb_mode.lower()
    if mode not in {"network", "local"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tryb Firebird musi mieć wartość: network albo local.",
        )
    return mode


async def _raise_env_locked_update(
    session: AsyncSession,
    *,
    user_id: int,
    client_ip: str | None,
    action: str,
    payload: dict[str, object] | None = None,
    detail: str = ENV_LOCK_DETAIL,
) -> None:
    """Rejestruje próbę zapisu sekcji env-only i zwraca błąd 423."""

    await record_audit(
        session,
        user_id=user_id,
        action=action,
        client_ip=client_ip,
        payload=payload or {},
    )
    await session.commit()
    raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=detail)


async def load_database_config(session: AsyncSession) -> DatabaseConfigResponse:
    del session
    host = settings.pg_host
    port = settings.pg_port
    database = settings.pg_database
    user = settings.pg_user
    sslmode = settings.pg_sslmode
    password_set = bool(settings.pg_password)

    return DatabaseConfigResponse(
        host=host,
        port=port,
        database=database,
        user=user,
        sslmode=sslmode,
        password_set=password_set,
        source="env",
        editable=False,
        lock_reason=ENV_LOCK_REASON,
    )


async def load_firebird_config(session: AsyncSession) -> FirebirdConfigResponse:
    del session
    mode = _normalize_firebird_mode(settings.fb_mode)
    host = settings.fb_host
    port = settings.fb_port
    database = settings.fb_database
    user = settings.fb_user
    charset = settings.fb_charset
    local_copy_path = settings.fb_local_copy_path
    role = (settings.fb_role or "").strip() or None
    allow_writes = bool(settings.fb_allow_writes)
    password_set = bool(settings.fb_password)

    return FirebirdConfigResponse(
        mode=mode,
        host=host,
        port=port,
        database=database,
        user=user,
        charset=charset,
        role=role,
        local_copy_path=local_copy_path,
        allow_writes=allow_writes,
        password_set=password_set,
        source="env",
        editable=False,
        lock_reason=ENV_LOCK_REASON,
    )


async def load_firebird_vmaintenance_config(
    session: AsyncSession,
) -> FirebirdVMaintenanceConfigResponse:
    del session
    host = settings.fb_v_host
    port = settings.fb_v_port
    database = settings.fb_v_database
    user = settings.fb_v_user
    charset = settings.fb_v_charset
    role = (settings.fb_v_role or "").strip() or None
    password_set = bool(settings.fb_v_password)

    return FirebirdVMaintenanceConfigResponse(
        host=host,
        port=port,
        database=database,
        user=user,
        charset=charset,
        role=role,
        password_set=password_set,
        source="env",
        editable=False,
        lock_reason=ENV_LOCK_REASON,
    )


async def load_google_sheets_config(session: AsyncSession) -> GoogleSheetsConfigResponse:
    runtime = await load_workflow_sheet_runtime_config(session)
    return GoogleSheetsConfigResponse(
        enabled=runtime.enabled,
        credentials_path=runtime.credentials_path,
        spreadsheet_id=runtime.spreadsheet_id,
        workflow_devices_worksheet=runtime.workflow_devices_worksheet,
        source="env",
        editable=False,
        lock_reason=ENV_LOCK_REASON,
    )


async def load_kp_repair_source_config(session: AsyncSession) -> KpRepairSourceConfigResponse:
    stored = await settings_store.get_namespace(session, "kp_repair")
    csv_directory = settings.kp_csv_directory
    csv_pattern = settings.kp_csv_pattern
    email_lookback_months = _to_int(
        stored.get("email_lookback_months") or settings.kp_email_lookback_months
    )
    if email_lookback_months < 0:
        email_lookback_months = settings.kp_email_lookback_months
    email_lookback_source = "admin" if stored.get("email_lookback_months") is not None else "env"

    return KpRepairSourceConfigResponse(
        csv_directory=csv_directory,
        csv_pattern=csv_pattern,
        email_lookback_months=email_lookback_months,
        csv_directory_source="env",
        csv_pattern_source="env",
        csv_editable=False,
        email_lookback_source=email_lookback_source,
        email_lookback_editable=True,
        lock_reason=ENV_LOCK_REASON,
    )


async def load_ctip_config(session: AsyncSession) -> CtipConfigResponse:
    del session
    host = settings.pbx_host
    port = settings.pbx_port
    pin_set = bool(settings.pbx_pin)

    return CtipConfigResponse(
        host=host,
        port=port,
        pin_set=pin_set,
        source="env",
        editable=False,
        lock_reason=ENV_LOCK_REASON,
    )


async def load_sms_config(session: AsyncSession) -> SmsConfigResponse:
    del session
    default_sender = settings.sms_default_sender
    sms_type = settings.sms_type
    api_url = settings.sms_api_url
    api_username = settings.sms_api_username
    api_token_set = bool(settings.sms_api_token)
    api_password_set = bool(settings.sms_api_password)
    test_mode = settings.sms_test_mode

    return SmsConfigResponse(
        default_sender=default_sender,
        sms_type=sms_type,
        api_url=api_url,
        api_username=api_username,
        api_token_set=api_token_set,
        api_password_set=api_password_set,
        test_mode=test_mode,
        source="env",
        editable=False,
        lock_reason=ENV_LOCK_REASON,
    )


async def load_email_config(session: AsyncSession) -> EmailConfigResponse:
    del session
    host = settings.email_host
    port = settings.email_port
    username = settings.email_username
    sender_name = settings.email_sender_name
    sender_address = settings.email_sender_address
    reply_to_address = settings.email_reply_to_address
    use_tls = settings.email_use_tls
    use_ssl = settings.email_use_ssl
    password_set = bool(settings.email_password)

    return EmailConfigResponse(
        host=host,
        port=port,
        username=username,
        sender_name=sender_name,
        sender_address=sender_address,
        use_tls=use_tls,
        use_ssl=use_ssl,
        password_set=password_set,
        reply_to_address=reply_to_address,
        source="env",
        editable=False,
        lock_reason=ENV_LOCK_REASON,
    )


async def load_form_handling_config(session: AsyncSession) -> FormHandlingConfigResponse:
    runtime = await load_form_handling_runtime_config(session)
    return FormHandlingConfigResponse(
        public_base_url=runtime.public_base_url,
        invite_sms_template=runtime.invite_sms_template,
        invite_email_subject=runtime.invite_email_subject,
        invite_email_body=runtime.invite_email_body,
        submission_email_subject=runtime.submission_email_subject,
        submission_email_body=runtime.submission_email_body,
        owner_sms_template=runtime.owner_sms_template,
    )


@router.get(
    "/database", response_model=DatabaseConfigResponse, summary="Aktualna konfiguracja PostgreSQL"
)
async def get_database_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DatabaseConfigResponse:
    """Zwraca aktywną konfigurację połączenia z bazą."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_database_config(session)


@router.get(
    "/firebird", response_model=FirebirdConfigResponse, summary="Aktualna konfiguracja Firebird"
)
async def get_firebird_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FirebirdConfigResponse:
    """Zwraca aktywną konfigurację połączenia z bazą Firebird."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_firebird_config(session)


@router.put(
    "/database",
    response_model=DatabaseConfigResponse,
    summary="Aktualizacja konfiguracji PostgreSQL",
)
async def update_database_config(
    payload: DatabaseConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DatabaseConfigResponse:
    """Blokuje zapis konfiguracji PostgreSQL poza `.env`."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)
    await _raise_env_locked_update(
        session,
        user_id=admin_user.id,
        client_ip=admin_session.client_ip,
        action="config_database_update_blocked_env",
        payload={
            "host": payload.host,
            "port": payload.port,
            "database": payload.database,
            "user": payload.user,
            "sslmode": payload.sslmode,
            "password_changed": payload.password is not None,
        },
    )


@router.put(
    "/firebird",
    response_model=FirebirdConfigResponse,
    summary="Aktualizacja konfiguracji Firebird",
)
async def update_firebird_config(
    payload: FirebirdConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FirebirdConfigResponse:
    """Blokuje zapis konfiguracji Firebird poza `.env`."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)
    mode = _normalize_firebird_mode(payload.mode)
    await _raise_env_locked_update(
        session,
        user_id=admin_user.id,
        client_ip=admin_session.client_ip,
        action="config_firebird_update_blocked_env",
        payload={
            "mode": mode,
            "host": payload.host.strip(),
            "port": payload.port,
            "database": payload.database.strip(),
            "user": payload.user.strip(),
            "charset": payload.charset.strip() or settings.fb_charset,
            "role": (payload.role or "").strip() or None,
            "local_copy_path": payload.local_copy_path.strip(),
            "allow_writes": payload.allow_writes,
            "password_changed": payload.password is not None,
        },
    )


@router.get(
    "/firebird-vmaintenance",
    response_model=FirebirdVMaintenanceConfigResponse,
    summary="Aktualna konfiguracja Firebird v-maintenance",
)
async def get_firebird_vmaintenance_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FirebirdVMaintenanceConfigResponse:
    """Zwraca konfigurację połączenia do bazy v-maintenance."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_firebird_vmaintenance_config(session)


@router.get(
    "/google-sheets",
    response_model=GoogleSheetsConfigResponse,
    summary="Aktualna konfiguracja Google Sheets dla FLOW",
)
async def get_google_sheets_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GoogleSheetsConfigResponse:
    """Zwraca aktywną konfigurację Google Sheets dla synchronizacji FLOW."""

    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_google_sheets_config(session)


@router.put(
    "/firebird-vmaintenance",
    response_model=FirebirdVMaintenanceConfigResponse,
    summary="Aktualizacja konfiguracji Firebird v-maintenance",
)
async def update_firebird_vmaintenance_config(
    payload: FirebirdVMaintenanceConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FirebirdVMaintenanceConfigResponse:
    """Blokuje zapis konfiguracji Firebird v-maintenance poza `.env`."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)
    await _raise_env_locked_update(
        session,
        user_id=admin_user.id,
        client_ip=admin_session.client_ip,
        action="config_firebird_vmaintenance_update_blocked_env",
        payload={
            "host": payload.host.strip(),
            "port": payload.port,
            "database": payload.database.strip(),
            "user": payload.user.strip(),
            "charset": payload.charset.strip() or settings.fb_v_charset,
            "role": (payload.role or "").strip() or None,
            "password_changed": payload.password is not None,
        },
    )


@router.put(
    "/google-sheets",
    response_model=GoogleSheetsConfigResponse,
    summary="Aktualizacja konfiguracji Google Sheets dla FLOW",
)
async def update_google_sheets_config(
    payload: GoogleSheetsConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GoogleSheetsConfigResponse:
    """Blokuje zapis konfiguracji Google Sheets poza `.env`."""

    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)
    await _raise_env_locked_update(
        session,
        user_id=admin_user.id,
        client_ip=admin_session.client_ip,
        action="config_google_sheets_update_blocked_env",
        payload={
            "enabled": payload.enabled,
            "credentials_path": payload.credentials_path.strip(),
            "spreadsheet_id": normalize_workflow_sheet_spreadsheet_id(payload.spreadsheet_id),
            "workflow_devices_worksheet": payload.workflow_devices_worksheet.strip()
            or "Urzadzenia_magazyn",
        },
    )


@router.get(
    "/kp-repair-source",
    response_model=KpRepairSourceConfigResponse,
    summary="Aktualna konfiguracja źródeł naprawy KP",
)
async def get_kp_repair_source_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> KpRepairSourceConfigResponse:
    """Zwraca konfigurację katalogu CSV i filtra czasowego dla EMAIL."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_kp_repair_source_config(session)


@router.put(
    "/kp-repair-source",
    response_model=KpRepairSourceConfigResponse,
    summary="Aktualizacja konfiguracji źródeł naprawy KP",
)
async def update_kp_repair_source_config(
    payload: KpRepairSourceConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> KpRepairSourceConfigResponse:
    """Zapisuje konfigurację katalogu CSV i filtra czasowego dla EMAIL."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    csv_directory = payload.csv_directory.strip()
    csv_pattern = payload.csv_pattern.strip() or settings.kp_csv_pattern
    expected_directory = settings.kp_csv_directory.strip()
    expected_pattern = settings.kp_csv_pattern.strip() or settings.kp_csv_pattern
    if csv_directory != expected_directory or csv_pattern != expected_pattern:
        await _raise_env_locked_update(
            session,
            user_id=admin_user.id,
            client_ip=admin_session.client_ip,
            action="config_kp_repair_source_update_blocked_env",
            payload={
                "csv_directory": csv_directory,
                "csv_pattern": csv_pattern,
                "email_lookback_months": payload.email_lookback_months,
            },
            detail=(
                "Katalog CSV i wzorzec plików są zarządzane wyłącznie z pliku .env. "
                "W panelu można zmienić tylko parametr email_lookback_months."
            ),
        )

    values: dict[str, StoredValue] = {
        "email_lookback_months": StoredValue(str(payload.email_lookback_months), False),
    }
    await settings_store.set_namespace(session, "kp_repair", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_kp_repair_source_update",
        client_ip=admin_session.client_ip,
        payload={
            "csv_directory_env": expected_directory,
            "csv_pattern_env": expected_pattern,
            "email_lookback_months": payload.email_lookback_months,
        },
    )
    await session.commit()

    return await load_kp_repair_source_config(session)


@router.get("/ctip", response_model=CtipConfigResponse, summary="Aktualna konfiguracja CTIP")
async def get_ctip_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CtipConfigResponse:
    """Zwraca aktywną konfigurację centrali CTIP."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_ctip_config(session)


@router.put("/ctip", response_model=CtipConfigResponse, summary="Aktualizacja konfiguracji CTIP")
async def update_ctip_config(
    payload: CtipConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CtipConfigResponse:
    """Blokuje zapis konfiguracji CTIP poza `.env`."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)
    await _raise_env_locked_update(
        session,
        user_id=admin_user.id,
        client_ip=admin_session.client_ip,
        action="config_ctip_update_blocked_env",
        payload={
            "host": payload.host,
            "port": payload.port,
            "pin_changed": payload.pin is not None,
        },
    )


@router.get("/sms", response_model=SmsConfigResponse, summary="Aktualna konfiguracja SerwerSMS")
async def get_sms_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> SmsConfigResponse:
    """Zwraca konfigurację operatora SMS."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_sms_config(session)


@router.put("/sms", response_model=SmsConfigResponse, summary="Aktualizacja konfiguracji SerwerSMS")
async def update_sms_config(
    payload: SmsConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> SmsConfigResponse:
    """Blokuje zapis konfiguracji SerwerSMS poza `.env`."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)
    await _raise_env_locked_update(
        session,
        user_id=admin_user.id,
        client_ip=admin_session.client_ip,
        action="config_sms_update_blocked_env",
        payload={
            "default_sender": payload.default_sender,
            "sms_type": payload.sms_type,
            "api_url": payload.api_url,
            "api_username": payload.api_username,
            "test_mode": payload.test_mode,
            "api_token_changed": payload.api_token is not None,
            "api_password_changed": payload.api_password is not None,
        },
    )


@router.get("/email", response_model=EmailConfigResponse, summary="Aktualna konfiguracja SMTP")
async def get_email_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> EmailConfigResponse:
    """Zwraca konfigurację serwera e-mail."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_email_config(session)


@router.put("/email", response_model=EmailConfigResponse, summary="Aktualizacja konfiguracji SMTP")
async def update_email_config(
    payload: EmailConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> EmailConfigResponse:
    """Blokuje zapis konfiguracji SMTP poza `.env`."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    if payload.use_tls and payload.use_ssl:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wybierz tylko jeden tryb zabezpieczenia: TLS lub SSL.",
        )

    host = payload.host.strip()
    if not host:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Host SMTP nie może być pusty."
        )

    username = (payload.username or "").strip()
    sender_name = (payload.sender_name or "").strip()
    sender_address = (payload.sender_address or "").strip()

    await _raise_env_locked_update(
        session,
        user_id=admin_user.id,
        client_ip=admin_session.client_ip,
        action="config_email_update_blocked_env",
        payload={
            "host": host,
            "port": payload.port,
            "username": username or None,
            "sender_name": sender_name or None,
            "sender_address": sender_address or None,
            "use_tls": payload.use_tls,
            "use_ssl": payload.use_ssl,
            "password_changed": payload.password is not None,
        },
    )


@router.get(
    "/form-handling",
    response_model=FormHandlingConfigResponse,
    summary="Aktualna konfiguracja obsługi formularza",
)
async def get_form_handling_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FormHandlingConfigResponse:
    """Zwraca aktywną konfigurację linków i komunikatów formularza."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    return await load_form_handling_config(session)


@router.put(
    "/form-handling",
    response_model=FormHandlingConfigResponse,
    summary="Aktualizacja konfiguracji obsługi formularza",
)
async def update_form_handling_config(
    payload: FormHandlingConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FormHandlingConfigResponse:
    """Zapisuje ustawienia publicznego linku i treści komunikatów formularza."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    public_base_url = normalize_public_base_url(payload.public_base_url)
    text_values = {
        "invite_sms_template": payload.invite_sms_template,
        "invite_email_subject": payload.invite_email_subject,
        "invite_email_body": payload.invite_email_body,
        "submission_email_subject": payload.submission_email_subject,
        "submission_email_body": payload.submission_email_body,
        "owner_sms_template": payload.owner_sms_template,
    }
    try:
        validate_form_handling_templates(text_values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    values: dict[str, StoredValue] = {
        "public_base_url": StoredValue(public_base_url, False),
        "invite_sms_template": StoredValue(payload.invite_sms_template, False),
        "invite_email_subject": StoredValue(payload.invite_email_subject, False),
        "invite_email_body": StoredValue(payload.invite_email_body, False),
        "submission_email_subject": StoredValue(payload.submission_email_subject, False),
        "submission_email_body": StoredValue(payload.submission_email_body, False),
        "owner_sms_template": StoredValue(payload.owner_sms_template, False),
    }

    await settings_store.set_namespace(session, "form_handling", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_form_handling_update",
        client_ip=admin_session.client_ip,
        payload={
            "public_base_url": public_base_url,
            "invite_email_subject": payload.invite_email_subject,
            "submission_email_subject": payload.submission_email_subject,
            "templates": {
                "invite_sms_template": payload.invite_sms_template,
                "invite_email_body": payload.invite_email_body,
                "submission_email_body": payload.submission_email_body,
                "owner_sms_template": payload.owner_sms_template,
            },
        },
    )
    await session.commit()

    return await load_form_handling_config(session)
