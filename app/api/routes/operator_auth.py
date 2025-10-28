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
from app.services.audit import record_audit
from app.services.security import generate_session_token, verify_password

router = APIRouter(prefix="/operator/auth", tags=["operator-auth"])


@router.post("/login", summary="Logowanie operatora")
async def operator_login(
    payload: OperatorLoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    stmt = select(AdminUser).where(AdminUser.email == payload.email)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Nieprawidłowe dane logowania."
        )

    if user.role not in {"operator", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień operatora."
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
        action="operator_login",
        client_ip=admin_session.client_ip,
        payload={"user_id": user.id},
    )
    await session.commit()

    return {"token": token, "expires_at": expires_at.isoformat()}


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
) -> OperatorUserInfo:
    _, admin_user = admin_context
    if admin_user.role not in {"operator", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień operatora."
        )
    return OperatorUserInfo(
        id=admin_user.id,
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        role=admin_user.role,
    )
