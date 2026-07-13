"""Logowanie i sesje panelu operatora."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.models import AdminSession, AdminUser
from app.schemas.operator import OperatorLoginRequest, OperatorUserInfo
from app.services import section_permissions
from app.services.audit import record_audit
from app.services.login_security import (
    login_is_rate_limited,
    record_login_failure,
    record_login_rate_limit,
    request_client_ip,
)
from app.services.security import generate_session_token, hash_session_token, verify_password

router = APIRouter(prefix="/operator/auth", tags=["operator-auth"])


@router.post("/login", summary="Logowanie operatora")
async def operator_login(
    payload: OperatorLoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    client_ip = request_client_ip(request)
    if await login_is_rate_limited(session, email=payload.email, client_ip=client_ip):
        await record_login_rate_limit(
            session,
            email=payload.email,
            client_ip=client_ip,
            channel="operator",
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Przekroczono limit prób logowania. Spróbuj ponownie później.",
            headers={"Retry-After": str(settings.login_failure_window_minutes * 60)},
        )

    stmt = select(AdminUser).where(AdminUser.email == payload.email)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        await record_login_failure(
            session,
            email=payload.email,
            client_ip=client_ip,
            channel="operator",
            reason="invalid_credentials",
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Nieprawidłowe dane logowania."
        )

    if user.role not in {"operator", "admin"}:
        await record_login_failure(
            session,
            email=payload.email,
            client_ip=client_ip,
            channel="operator",
            reason="role_denied",
            user_id=user.id,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień operatora."
        )
    sections = await section_permissions.get_user_sections(session, user)
    if "operator" not in sections:
        await record_login_failure(
            session,
            email=payload.email,
            client_ip=client_ip,
            channel="operator",
            reason="section_denied",
            user_id=user.id,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Konto nie ma uprawnień operatora."
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
        token=hash_session_token(token),
        created_at=now,
        expires_at=expires_at,
        client_ip=client_ip,
        user_agent=request.headers.get("User-Agent"),
    )
    session.add(admin_session)
    await record_audit(
        session,
        user_id=user.id,
        action="operator_login",
        client_ip=admin_session.client_ip,
        payload={"user_id": user.id},
    )
    await session.commit()

    return {"token": token, "expires_at": expires_at.isoformat(), "sections": sections}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Wylogowanie operatora")
async def operator_logout(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    admin_session, admin_user = admin_context
    if admin_user.role not in {"operator", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień operatora."
        )
    sections = await section_permissions.get_user_sections(session, admin_user)
    if "operator" not in sections:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Konto nie ma uprawnień operatora."
        )

    admin_session.revoked_at = datetime.now(UTC)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="operator_logout",
        client_ip=admin_session.client_ip,
        payload={"user_id": admin_user.id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=OperatorUserInfo, summary="Informacje o zalogowanym operatorze")
async def operator_me(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorUserInfo:
    _, admin_user = admin_context
    if admin_user.role not in {"operator", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień operatora."
        )
    sections = await section_permissions.get_user_sections(session, admin_user)
    if "operator" not in sections:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Konto nie ma uprawnień operatora."
        )
    return OperatorUserInfo(
        id=admin_user.id,
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        role=admin_user.role,
        is_salesperson=bool(admin_user.is_salesperson),
        sections=sections,
    )
