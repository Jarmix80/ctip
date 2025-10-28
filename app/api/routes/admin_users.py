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
    AdminUserListResponse,
    AdminUserResetPasswordResponse,
    AdminUserSessionInfo,
    AdminUserStatusUpdate,
    AdminUserSummary,
    AdminUserUpdate,
)
from app.services import admin_users
from app.services.audit import record_audit

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def _ensure_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operacja wymaga roli administratora."
        )


def _map_summary(row: admin_users.UserRow) -> AdminUserSummary:
    user = row.user
    return AdminUserSummary(
        id=user.id,
        email=user.email,
        mobile_phone=user.mobile_phone,
        first_name=user.first_name,
        last_name=user.last_name,
        internal_ext=user.internal_ext,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=row.last_login_at,
        sessions_active=row.sessions_active,
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
        **_map_summary(summary_row).model_dump(),
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
    items = [_map_summary(row) for row in rows]
    return AdminUserListResponse(items=items)


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
        user, password = await admin_users.create_user(
            session,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            internal_ext=payload.internal_ext,
            role=payload.role,
            password=payload.password,
            mobile_phone=payload.mobile_phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    login_url_default = urljoin(str(request.base_url), "admin")
    panel_url_config = getattr(settings, "admin_panel_url", None)
    login_url = (panel_url_config or "").strip() or login_url_default
    email_delivery = await admin_users.resolve_email_delivery_settings(session)
    await admin_users.queue_credentials_sms(
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
        payload={"user_id": user.id, "email": user.email, "role": user.role},
    )
    await session.commit()

    summary_row = admin_users.UserRow(user=user, sessions_active=0, last_login_at=None)
    await admin_users.send_credentials_email(email_delivery, user, password, login_url)
    return AdminUserCreateResponse(user=_map_summary(summary_row), password=password)


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
        await admin_users.update_user(
            session,
            user,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            internal_ext=payload.internal_ext,
            role=payload.role,
            mobile_phone=payload.mobile_phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="user_update",
        client_ip=admin_session.client_ip,
        payload={"user_id": user.id, "email": user.email, "role": user.role},
    )
    await session.commit()

    return await _load_detail(session, user_id)


@router.post(
    "/{user_id}/reset-password",
    response_model=AdminUserResetPasswordResponse,
    summary="Reset hasła użytkownika",
)
async def reset_admin_password(
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

    await record_audit(
        session,
        user_id=admin_user.id,
        action="user_reset_password",
        client_ip=admin_session.client_ip,
        payload={"user_id": user.id, "email": user.email},
    )
    await session.commit()
    return AdminUserResetPasswordResponse(password=new_password)


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
    summary_row = next(
        (row for row in rows if row.user.id == user_id),
        admin_users.UserRow(user=user, sessions_active=0, last_login_at=None),
    )
    return _map_summary(summary_row)


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
    await record_audit(
        session,
        user_id=admin_user.id,
        action="user_delete",
        client_ip=admin_session.client_ip,
        payload={"deleted_user_id": user.id, "email": user.email},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
