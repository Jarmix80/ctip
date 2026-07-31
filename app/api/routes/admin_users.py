"""Zarządzanie użytkownikami panelu administracyjnego."""

from __future__ import annotations

from urllib.parse import urljoin

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.schemas.admin import (
    AdminUserCreate,
    AdminUserCreateResponse,
    AdminUserDetail,
    AdminUserImapConfig,
    AdminUserImapUpdate,
    AdminUserListResponse,
    AdminUserResetPasswordResponse,
    AdminUserSessionInfo,
    AdminUserStatusUpdate,
    AdminUserSummary,
    AdminUserUpdate,
    FirebirdMsUserListResponse,
    FirebirdMsUserOption,
)
from app.services import admin_users, firebird_ms_users, section_permissions
from app.services.admin_user_imap import UserImapUpdate, load_user_imap_config, set_user_imap_config
from app.services.audit import record_audit

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def _ensure_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operacja wymaga roli administratora."
        )


def _map_imap_config(config) -> AdminUserImapConfig:
    return AdminUserImapConfig(
        enabled=config.enabled,
        email=config.email,
        host=config.host,
        port=config.port,
        username=config.username,
        use_ssl=config.use_ssl,
        folder=config.folder,
        password_set=config.password_set,
    )


def _build_imap_update(payload: AdminUserImapUpdate | None) -> UserImapUpdate | None:
    if payload is None:
        return None
    return UserImapUpdate(
        enabled=payload.enabled,
        email=str(payload.email) if payload.email is not None else None,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        use_ssl=payload.use_ssl,
        folder=payload.folder,
        password=payload.password,
        clear_password=payload.clear_password,
    )


def _map_summary(
    row: admin_users.UserRow,
    sections: list[str],
    imap: AdminUserImapConfig | None,
) -> AdminUserSummary:
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
        crm_sales_sms_enabled=bool(user.crm_sales_sms_enabled),
        crm_sales_email_enabled=bool(user.crm_sales_email_enabled),
        crm_operations_sms_enabled=bool(user.crm_operations_sms_enabled),
        crm_operations_email_enabled=bool(user.crm_operations_email_enabled),
        firebird_app_user_id=user.firebird_app_user_id,
        firebird_app_user_login=user.firebird_app_user_login,
        sections=sections,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=row.last_login_at,
        sessions_active=row.sessions_active,
        imap=imap,
    )


def _map_firebird_ms_user_option(
    option: firebird_ms_users.FirebirdMsUserOption,
) -> FirebirdMsUserOption:
    return FirebirdMsUserOption(
        id=option.id,
        login_user=option.login_user,
        workstation=option.workstation,
        app_name=option.app_name,
        label=option.label,
    )


async def _load_detail(session: AsyncSession, user_id: int) -> AdminUserDetail:
    user = await admin_users.fetch_user(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono użytkownika."
        )
    rows = await admin_users.list_users(session)
    summary_row = next((row for row in rows if row.user.id == user_id), None)
    if not summary_row:
        summary_row = admin_users.UserRow(user=user, sessions_active=0, last_login_at=None)
    user_sections = await section_permissions.get_user_sections(session, user)
    imap_config = _map_imap_config(
        await load_user_imap_config(
            session,
            user_id=user.id,
            fallback_email=user.email,
        )
    )
    sessions = await admin_users.list_sessions(session, user_id)
    session_items = [
        AdminUserSessionInfo(
            id=item.id,
            created_at=item.created_at,
            expires_at=item.expires_at,
            revoked_at=item.revoked_at,
            client_ip=item.client_ip,
            user_agent=item.user_agent,
        )
        for item in sessions
    ]
    return AdminUserDetail(
        **_map_summary(summary_row, user_sections, imap_config).model_dump(),
        sessions=session_items,
    )


@router.get("", response_model=AdminUserListResponse, summary="Lista użytkowników panelu")
async def list_admin_users(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminUserListResponse:
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    rows = await admin_users.list_users(session)
    sections_map = await section_permissions.list_user_sections(session, [row.user for row in rows])
    imap_map: dict[int, AdminUserImapConfig] = {}
    for row in rows:
        imap_map[row.user.id] = _map_imap_config(
            await load_user_imap_config(
                session,
                user_id=row.user.id,
                fallback_email=row.user.email,
            )
        )
    items = [
        _map_summary(row, sections_map.get(row.user.id, []), imap_map.get(row.user.id))
        for row in rows
    ]
    return AdminUserListResponse(items=items)


@router.get(
    "/firebird-ms-users",
    response_model=FirebirdMsUserListResponse,
    summary="Lista użytkowników Menadżera Serwisu",
)
async def list_firebird_ms_user_options(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FirebirdMsUserListResponse:
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    try:
        items = await firebird_ms_users.list_firebird_ms_users(session)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return FirebirdMsUserListResponse(items=[_map_firebird_ms_user_option(item) for item in items])


@router.post(
    "",
    response_model=AdminUserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Dodaj użytkownika",
)
async def create_admin_user(
    payload: AdminUserCreate,
    request: Request,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminUserCreateResponse:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    try:
        firebird_user = None
        if payload.firebird_app_user_id is not None:
            firebird_user = await firebird_ms_users.resolve_firebird_ms_user(
                session, payload.firebird_app_user_id
            )
        user, password = await admin_users.create_user(
            session,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            internal_ext=payload.internal_ext,
            role=payload.role,
            is_salesperson=payload.is_salesperson,
            crm_sales_sms_enabled=payload.crm_sales_sms_enabled,
            crm_sales_email_enabled=payload.crm_sales_email_enabled,
            crm_operations_sms_enabled=payload.crm_operations_sms_enabled,
            crm_operations_email_enabled=payload.crm_operations_email_enabled,
            password=payload.password,
            mobile_phone=payload.mobile_phone,
            firebird_app_user_id=firebird_user.id if firebird_user else None,
            firebird_app_user_login=firebird_user.login_user if firebird_user else None,
        )
        normalized_sections = await section_permissions.set_user_sections(
            session,
            user_id=user.id,
            role=user.role,
            sections=payload.sections,
            updated_by=admin_user.id,
        )
        imap_update = _build_imap_update(payload.imap)
        if imap_update is not None:
            await set_user_imap_config(
                session,
                user_id=user.id,
                update=imap_update,
                updated_by=admin_user.id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user_imap_config = _map_imap_config(
        await load_user_imap_config(
            session,
            user_id=user.id,
            fallback_email=user.email,
        )
    )

    login_url_default = urljoin(str(request.base_url), "")
    panel_url_config = getattr(settings, "admin_panel_url", None)
    login_url = (panel_url_config or "").strip() or login_url_default
    email_delivery = await admin_users.resolve_email_delivery_settings(session)
    sms_queued = await admin_users.queue_credentials_sms(
        session,
        user,
        password,
        created_by=admin_user.id,
        login_url=login_url,
    )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="user_create",
        client_ip=admin_session.client_ip,
        payload={
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "is_salesperson": user.is_salesperson,
            "crm_sales_sms_enabled": user.crm_sales_sms_enabled,
            "crm_sales_email_enabled": user.crm_sales_email_enabled,
            "crm_operations_sms_enabled": user.crm_operations_sms_enabled,
            "crm_operations_email_enabled": user.crm_operations_email_enabled,
            "firebird_app_user_id": user.firebird_app_user_id,
            "firebird_app_user_login": user.firebird_app_user_login,
            "sections": normalized_sections,
            "imap": {
                "enabled": user_imap_config.enabled,
                "email": user_imap_config.email,
                "host": user_imap_config.host,
                "port": user_imap_config.port,
                "username": user_imap_config.username,
                "use_ssl": user_imap_config.use_ssl,
                "folder": user_imap_config.folder,
                "password_set": user_imap_config.password_set,
            },
        },
    )
    await session.commit()

    summary_row = admin_users.UserRow(user=user, sessions_active=0, last_login_at=None)
    await admin_users.send_credentials_email(email_delivery, user, password, login_url)
    return AdminUserCreateResponse(
        user=_map_summary(summary_row, normalized_sections, user_imap_config),
        password=password,
        sms_queued=sms_queued,
        sms_recipient=user.mobile_phone if sms_queued else None,
    )


@router.get("/{user_id}", response_model=AdminUserDetail, summary="Szczegóły użytkownika")
async def get_admin_user(
    user_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminUserDetail:
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    return await _load_detail(session, user_id)


@router.put("/{user_id}", response_model=AdminUserDetail, summary="Aktualizuj użytkownika")
async def update_admin_user(
    payload: AdminUserUpdate,
    user_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminUserDetail:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    user = await admin_users.fetch_user(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono użytkownika."
        )
    try:
        firebird_user = None
        if payload.firebird_app_user_id is not None:
            firebird_user = await firebird_ms_users.resolve_firebird_ms_user(
                session, payload.firebird_app_user_id
            )
        await admin_users.update_user(
            session,
            user,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            internal_ext=payload.internal_ext,
            role=payload.role,
            is_salesperson=payload.is_salesperson,
            crm_sales_sms_enabled=payload.crm_sales_sms_enabled,
            crm_sales_email_enabled=payload.crm_sales_email_enabled,
            crm_operations_sms_enabled=payload.crm_operations_sms_enabled,
            crm_operations_email_enabled=payload.crm_operations_email_enabled,
            mobile_phone=payload.mobile_phone,
            firebird_app_user_id=firebird_user.id if firebird_user else None,
            firebird_app_user_login=firebird_user.login_user if firebird_user else None,
        )
        normalized_sections = await section_permissions.set_user_sections(
            session,
            user_id=user.id,
            role=payload.role,
            sections=payload.sections,
            updated_by=admin_user.id,
        )
        imap_update = _build_imap_update(payload.imap)
        if imap_update is not None:
            await set_user_imap_config(
                session,
                user_id=user.id,
                update=imap_update,
                updated_by=admin_user.id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user_imap_config = _map_imap_config(
        await load_user_imap_config(
            session,
            user_id=user.id,
            fallback_email=user.email,
        )
    )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="user_update",
        client_ip=admin_session.client_ip,
        payload={
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "is_salesperson": user.is_salesperson,
            "crm_sales_sms_enabled": user.crm_sales_sms_enabled,
            "crm_sales_email_enabled": user.crm_sales_email_enabled,
            "crm_operations_sms_enabled": user.crm_operations_sms_enabled,
            "crm_operations_email_enabled": user.crm_operations_email_enabled,
            "firebird_app_user_id": user.firebird_app_user_id,
            "firebird_app_user_login": user.firebird_app_user_login,
            "sections": normalized_sections,
            "imap": {
                "enabled": user_imap_config.enabled,
                "email": user_imap_config.email,
                "host": user_imap_config.host,
                "port": user_imap_config.port,
                "username": user_imap_config.username,
                "use_ssl": user_imap_config.use_ssl,
                "folder": user_imap_config.folder,
                "password_set": user_imap_config.password_set,
            },
        },
    )
    await session.commit()

    return await _load_detail(session, user_id)


@router.post(
    "/{user_id}/reset-password",
    response_model=AdminUserResetPasswordResponse,
    summary="Reset hasła użytkownika",
)
async def reset_admin_password(
    request: Request,
    user_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminUserResetPasswordResponse:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    user = await admin_users.fetch_user(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono użytkownika."
        )
    new_password = await admin_users.reset_password(session, user)
    login_url_default = urljoin(str(request.base_url), "")
    panel_url_config = getattr(settings, "admin_panel_url", None)
    login_url = (panel_url_config or "").strip() or login_url_default
    email_delivery = await admin_users.resolve_email_delivery_settings(session)
    sms_queued = await admin_users.queue_credentials_sms(
        session,
        user,
        new_password,
        created_by=admin_user.id,
        login_url=login_url,
        reason="password_reset",
    )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="user_reset_password",
        client_ip=admin_session.client_ip,
        payload={"user_id": user.id, "email": user.email},
    )
    await session.commit()

    await admin_users.send_credentials_email(
        email_delivery,
        user,
        new_password,
        login_url,
        reason="password_reset",
    )
    return AdminUserResetPasswordResponse(
        password=new_password,
        sms_queued=sms_queued,
        sms_recipient=user.mobile_phone if sms_queued else None,
    )


@router.patch(
    "/{user_id}/status", response_model=AdminUserSummary, summary="Aktywuj/dezaktywuj użytkownika"
)
async def update_admin_status(
    payload: AdminUserStatusUpdate,
    user_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminUserSummary:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    user = await admin_users.fetch_user(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono użytkownika."
        )
    await admin_users.set_user_active(session, user, is_active=payload.is_active)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="user_status_update",
        client_ip=admin_session.client_ip,
        payload={"user_id": user.id, "is_active": payload.is_active},
    )
    await session.commit()
    rows = await admin_users.list_users(session)
    sections_map = await section_permissions.list_user_sections(session, [row.user for row in rows])
    summary_row = next(
        (row for row in rows if row.user.id == user_id),
        admin_users.UserRow(user=user, sessions_active=0, last_login_at=None),
    )
    user_sections = sections_map.get(user_id) or await section_permissions.get_user_sections(
        session, user
    )
    user_imap_config = _map_imap_config(
        await load_user_imap_config(
            session,
            user_id=user.id,
            fallback_email=user.email,
        )
    )
    return _map_summary(summary_row, user_sections, user_imap_config)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Usuń użytkownika")
async def delete_admin_user(
    user_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    if admin_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nie można usunąć własnego konta."
        )

    user = await admin_users.fetch_user(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono użytkownika."
        )

    if user.role == "admin":
        remaining = await admin_users.count_active_admins(session, exclude_user_id=user_id)
        if remaining <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nie można usunąć ostatniego administratora.",
            )

    await admin_users.delete_user(session, user)
    await section_permissions.delete_user_sections(session, user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="user_delete",
        client_ip=admin_session.client_ip,
        payload={"deleted_user_id": user.id, "email": user.email},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
