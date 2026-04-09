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

router = APIRouter(prefix="/admin/config", tags=["admin"])

settings_store = build_store(settings.admin_secret_key)


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


def _to_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return value.lower() in {"1", "true", "t", "yes", "on"}


def _normalize_firebird_mode(value: str | None) -> str:
    """Normalizuje tryb połączenia Firebird."""
    mode = (value or "").strip().lower() or settings.fb_mode.lower()
    if mode not in {"network", "local"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tryb Firebird musi mieć wartość: network albo local.",
        )
    return mode


async def load_database_config(session: AsyncSession) -> DatabaseConfigResponse:
    stored = await settings_store.get_namespace(session, "database")
    host = stored.get("host") or settings.pg_host
    port = _to_int(stored.get("port") or settings.pg_port)
    database = stored.get("database") or settings.pg_database
    user = stored.get("user") or settings.pg_user
    sslmode = stored.get("sslmode") or settings.pg_sslmode
    password_set = bool(stored.get("password") or settings.pg_password)

    return DatabaseConfigResponse(
        host=host,
        port=port,
        database=database,
        user=user,
        sslmode=sslmode,
        password_set=password_set,
    )


async def load_firebird_config(session: AsyncSession) -> FirebirdConfigResponse:
    stored = await settings_store.get_namespace(session, "firebird")
    mode = _normalize_firebird_mode(stored.get("mode") or settings.fb_mode)
    host = stored.get("host") or settings.fb_host
    port = _to_int(stored.get("port") or settings.fb_port)
    database = stored.get("database") or settings.fb_database
    user = stored.get("user") or settings.fb_user
    charset = stored.get("charset") or settings.fb_charset
    local_copy_path = stored.get("local_copy_path") or settings.fb_local_copy_path
    raw_role = stored.get("role")
    if raw_role is None:
        role = settings.fb_role
    else:
        role = raw_role.strip() or None
    raw_allow_writes = stored.get("allow_writes")
    if isinstance(raw_allow_writes, str):
        allow_writes = _to_bool(raw_allow_writes)
    elif raw_allow_writes is None:
        allow_writes = settings.fb_allow_writes
    else:
        allow_writes = bool(raw_allow_writes)
    password_set = bool(stored.get("password") or settings.fb_password)

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
    )


async def load_firebird_vmaintenance_config(
    session: AsyncSession,
) -> FirebirdVMaintenanceConfigResponse:
    stored = await settings_store.get_namespace(session, "firebird_vmaintenance")
    host = stored.get("host") or settings.fb_v_host
    port = _to_int(stored.get("port") or settings.fb_v_port)
    database = stored.get("database") or settings.fb_v_database
    user = stored.get("user") or settings.fb_v_user
    charset = stored.get("charset") or settings.fb_v_charset
    raw_role = stored.get("role")
    if raw_role is None:
        role = settings.fb_v_role
    else:
        role = raw_role.strip() or None
    password_set = bool(stored.get("password") or settings.fb_v_password)

    return FirebirdVMaintenanceConfigResponse(
        host=host,
        port=port,
        database=database,
        user=user,
        charset=charset,
        role=role,
        password_set=password_set,
    )


async def load_kp_repair_source_config(session: AsyncSession) -> KpRepairSourceConfigResponse:
    stored = await settings_store.get_namespace(session, "kp_repair")
    csv_directory = stored.get("csv_directory") or settings.kp_csv_directory
    csv_pattern = stored.get("csv_pattern") or settings.kp_csv_pattern
    email_lookback_months = _to_int(
        stored.get("email_lookback_months") or settings.kp_email_lookback_months
    )
    if email_lookback_months < 0:
        email_lookback_months = settings.kp_email_lookback_months

    return KpRepairSourceConfigResponse(
        csv_directory=csv_directory,
        csv_pattern=csv_pattern,
        email_lookback_months=email_lookback_months,
    )


async def load_ctip_config(session: AsyncSession) -> CtipConfigResponse:
    stored = await settings_store.get_namespace(session, "ctip")
    host = stored.get("host") or settings.pbx_host
    port = _to_int(stored.get("port") or settings.pbx_port)
    pin_set = bool(stored.get("pin") or settings.pbx_pin)

    return CtipConfigResponse(host=host, port=port, pin_set=pin_set)


async def load_sms_config(session: AsyncSession) -> SmsConfigResponse:
    stored = await settings_store.get_namespace(session, "sms")
    default_sender = stored.get("default_sender") or settings.sms_default_sender
    sms_type = stored.get("sms_type") or settings.sms_type
    api_url = stored.get("api_url") or settings.sms_api_url
    api_username = stored.get("api_username") or settings.sms_api_username
    api_token_set = bool(stored.get("api_token") or settings.sms_api_token)
    api_password_set = bool(stored.get("api_password") or settings.sms_api_password)
    raw_test_mode = stored.get("test_mode")
    if isinstance(raw_test_mode, str):
        test_mode = _to_bool(raw_test_mode)
    elif raw_test_mode is None:
        test_mode = settings.sms_test_mode
    else:
        test_mode = bool(raw_test_mode)

    return SmsConfigResponse(
        default_sender=default_sender,
        sms_type=sms_type,
        api_url=api_url,
        api_username=api_username,
        api_token_set=api_token_set,
        api_password_set=api_password_set,
        test_mode=test_mode,
    )


async def load_email_config(session: AsyncSession) -> EmailConfigResponse:
    stored = await settings_store.get_namespace(session, "email")
    host = stored.get("host") or settings.email_host
    port = _to_int(stored.get("port") or settings.email_port)
    username = stored.get("username") or settings.email_username
    sender_name = stored.get("sender_name") or settings.email_sender_name
    sender_address = stored.get("sender_address") or settings.email_sender_address

    raw_use_tls = stored.get("use_tls")
    if raw_use_tls is None:
        use_tls = settings.email_use_tls
    elif isinstance(raw_use_tls, str):
        use_tls = _to_bool(raw_use_tls)
    else:
        use_tls = bool(raw_use_tls)

    raw_use_ssl = stored.get("use_ssl")
    if raw_use_ssl is None:
        use_ssl = settings.email_use_ssl
    elif isinstance(raw_use_ssl, str):
        use_ssl = _to_bool(raw_use_ssl)
    else:
        use_ssl = bool(raw_use_ssl)

    password_set = bool(stored.get("password") or settings.email_password)

    return EmailConfigResponse(
        host=host,
        port=port,
        username=username,
        sender_name=sender_name,
        sender_address=sender_address,
        use_tls=use_tls,
        use_ssl=use_ssl,
        password_set=password_set,
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
    """Zapisuje nowe parametry połączenia z bazą danych."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    values: dict[str, StoredValue] = {
        "host": StoredValue(payload.host, False),
        "port": StoredValue(str(payload.port), False),
        "database": StoredValue(payload.database, False),
        "user": StoredValue(payload.user, False),
        "sslmode": StoredValue(payload.sslmode, False),
    }
    if payload.password is not None:
        values["password"] = StoredValue(payload.password, True)

    await settings_store.set_namespace(session, "database", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_database_update",
        client_ip=admin_session.client_ip,
        payload={
            "host": payload.host,
            "port": payload.port,
            "database": payload.database,
            "user": payload.user,
            "sslmode": payload.sslmode,
            "password_changed": payload.password is not None,
        },
    )
    await session.commit()

    return await load_database_config(session)


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
    """Zapisuje parametry połączenia z bazą Firebird."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    mode = _normalize_firebird_mode(payload.mode)
    requested_host = payload.host.strip()
    host = "127.0.0.1" if mode == "local" else requested_host
    database = payload.database.strip()
    user = payload.user.strip()
    charset = payload.charset.strip() or settings.fb_charset
    role = (payload.role or "").strip()
    local_copy_path = payload.local_copy_path.strip()
    if mode == "network" and not host:
        raise HTTPException(status_code=400, detail="Host Firebird nie może być pusty.")
    if not database:
        raise HTTPException(status_code=400, detail="Ścieżka bazy Firebird nie może być pusta.")
    if not user:
        raise HTTPException(status_code=400, detail="Użytkownik Firebird nie może być pusty.")
    if not local_copy_path:
        raise HTTPException(
            status_code=400,
            detail="Ścieżka lokalnej kopii Firebird nie może być pusta.",
        )
    current_config = await load_firebird_config(session)
    allow_writes = (
        payload.allow_writes if payload.allow_writes is not None else current_config.allow_writes
    )

    values: dict[str, StoredValue] = {
        "mode": StoredValue(mode, False),
        "host": StoredValue(host, False),
        "port": StoredValue(str(payload.port), False),
        "database": StoredValue(database, False),
        "user": StoredValue(user, False),
        "charset": StoredValue(charset, False),
        "role": StoredValue(role, False),
        "local_copy_path": StoredValue(local_copy_path, False),
        "allow_writes": StoredValue("true" if allow_writes else "false", False),
    }
    if payload.password is not None:
        values["password"] = StoredValue(payload.password, True)

    await settings_store.set_namespace(session, "firebird", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_firebird_update",
        client_ip=admin_session.client_ip,
        payload={
            "mode": mode,
            "host": host,
            "port": payload.port,
            "database": database,
            "user": user,
            "charset": charset,
            "role": role or None,
            "local_copy_path": local_copy_path,
            "allow_writes": allow_writes,
            "password_changed": payload.password is not None,
        },
    )
    await session.commit()

    return await load_firebird_config(session)


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
    """Zapisuje parametry połączenia do bazy v-maintenance."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    host = payload.host.strip()
    database = payload.database.strip()
    user = payload.user.strip()
    charset = payload.charset.strip() or settings.fb_v_charset
    role = (payload.role or "").strip()
    if not host:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Host Firebird v-maintenance nie może być pusty.",
        )
    if not database:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ścieżka bazy Firebird v-maintenance nie może być pusta.",
        )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Użytkownik Firebird v-maintenance nie może być pusty.",
        )

    values: dict[str, StoredValue] = {
        "host": StoredValue(host, False),
        "port": StoredValue(str(payload.port), False),
        "database": StoredValue(database, False),
        "user": StoredValue(user, False),
        "charset": StoredValue(charset, False),
        "role": StoredValue(role, False),
    }
    if payload.password is not None:
        values["password"] = StoredValue(payload.password, True)

    await settings_store.set_namespace(
        session,
        "firebird_vmaintenance",
        values,
        user_id=admin_user.id,
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_firebird_vmaintenance_update",
        client_ip=admin_session.client_ip,
        payload={
            "host": host,
            "port": payload.port,
            "database": database,
            "user": user,
            "charset": charset,
            "role": role or None,
            "password_changed": payload.password is not None,
        },
    )
    await session.commit()

    return await load_firebird_vmaintenance_config(session)


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
    if not csv_directory:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Katalog CSV nie może być pusty.",
        )
    if not csv_pattern:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wzorzec pliku CSV nie może być pusty.",
        )

    values: dict[str, StoredValue] = {
        "csv_directory": StoredValue(csv_directory, False),
        "csv_pattern": StoredValue(csv_pattern, False),
        "email_lookback_months": StoredValue(str(payload.email_lookback_months), False),
    }
    await settings_store.set_namespace(session, "kp_repair", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_kp_repair_source_update",
        client_ip=admin_session.client_ip,
        payload={
            "csv_directory": csv_directory,
            "csv_pattern": csv_pattern,
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
    """Zapisuje parametry centrali CTIP."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    values: dict[str, StoredValue] = {
        "host": StoredValue(payload.host, False),
        "port": StoredValue(str(payload.port), False),
    }
    if payload.pin is not None:
        values["pin"] = StoredValue(payload.pin, True)

    await settings_store.set_namespace(session, "ctip", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_ctip_update",
        client_ip=admin_session.client_ip,
        payload={
            "host": payload.host,
            "port": payload.port,
            "pin_changed": payload.pin is not None,
        },
    )
    await session.commit()

    return await load_ctip_config(session)


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
    """Zapisuje ustawienia operatora SMS."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    values: dict[str, StoredValue] = {
        "default_sender": StoredValue(payload.default_sender, False),
        "sms_type": StoredValue(payload.sms_type, False),
        "api_url": StoredValue(payload.api_url, False),
        "test_mode": StoredValue("true" if payload.test_mode else "false", False),
    }
    if payload.api_username is not None:
        values["api_username"] = StoredValue(payload.api_username, False)
    if payload.api_token is not None:
        values["api_token"] = StoredValue(payload.api_token, True)
    if payload.api_password is not None:
        values["api_password"] = StoredValue(payload.api_password, True)

    await settings_store.set_namespace(session, "sms", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_sms_update",
        client_ip=admin_session.client_ip,
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
    await session.commit()

    return await load_sms_config(session)


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
    """Zapisuje ustawienia serwera SMTP."""
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

    values: dict[str, StoredValue] = {
        "host": StoredValue(host, False),
        "port": StoredValue(str(payload.port), False),
        "username": StoredValue(username, False),
        "sender_name": StoredValue(sender_name, False),
        "sender_address": StoredValue(sender_address, False),
        "use_tls": StoredValue("true" if payload.use_tls else "false", False),
        "use_ssl": StoredValue("true" if payload.use_ssl else "false", False),
    }
    if payload.password is not None:
        values["password"] = StoredValue(payload.password, True)

    await settings_store.set_namespace(session, "email", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_email_update",
        client_ip=admin_session.client_ip,
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
    await session.commit()

    return await load_email_config(session)


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
