"""Trasy uwierzytelniające panelu administracyjnego."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.models import AdminSession, AdminUser
from app.schemas.admin import AdminLoginRequest, AdminLoginResponse, AdminUserInfo
from app.services.audit import record_audit
from app.services.security import generate_session_token, verify_password

router = APIRouter(prefix="/admin/auth", tags=["admin"])


@router.post("/login", response_model=AdminLoginResponse, summary="Logowanie administratora")
async def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminLoginResponse:
    """Weryfikuje dane logowania i zwraca token sesji."""
    stmt = select(AdminUser).where(AdminUser.email == payload.email)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Nieprawidłowe dane logowania"
        )

    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień do panelu administratora."
        )

    now = datetime.now(UTC)
    ttl_minutes = settings.admin_session_ttl_minutes
    if payload.remember_me and settings.admin_session_remember_hours > 0:
        remember_minutes = max(settings.admin_session_remember_hours * 60, ttl_minutes)
        ttl_minutes = remember_minutes
    expires_at = now + timedelta(minutes=ttl_minutes)
    token = generate_session_token()
    admin_session = AdminSession(
        user_id=user.id,
        token=token,
        created_at=now,
        expires_at=expires_at,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )
    session.add(admin_session)
    await record_audit(
        session,
        user_id=user.id,
        action="admin_login",
        client_ip=admin_session.client_ip,
        payload={"user_id": user.id},
    )
    await session.commit()

    return AdminLoginResponse(token=token, expires_at=expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Wylogowanie sesji")
async def admin_logout(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Zamyka bieżącą sesję administratora."""
    admin_session, admin_user = admin_context
    now = datetime.now(UTC)
    admin_session.revoked_at = now
    await record_audit(
        session,
        user_id=admin_user.id,
        action="admin_logout",
        client_ip=admin_session.client_ip,
        payload={"user_id": admin_user.id},
    )
    await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AdminUserInfo, summary="Informacje o zalogowanym administratorze")
async def admin_me(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
) -> AdminUserInfo:
    """Zwraca podstawowe dane konta administratora."""
    _, admin_user = admin_context
    if admin_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień do panelu administratora."
        )
    return AdminUserInfo(
        id=admin_user.id,
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        internal_ext=admin_user.internal_ext,
        role=admin_user.role,
        mobile_phone=admin_user.mobile_phone,
    )
