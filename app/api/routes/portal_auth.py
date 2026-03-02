"""Centralne logowanie dla strony głównej i wyboru sekcji."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.models import AdminSession, AdminUser
from app.schemas.admin import (
    AdminLoginRequest,
    PortalLoginResponse,
    PortalPasswordChangeRequest,
    PortalProfile,
    PortalProfileUpdate,
    PortalUserInfo,
)
from app.services import operator_settings, section_permissions
from app.services.audit import record_audit
from app.services.security import generate_session_token, verify_password

router = APIRouter(prefix="/auth", tags=["portal-auth"])


@router.post("/login", response_model=PortalLoginResponse, summary="Logowanie do strony głównej")
async def portal_login(
    payload: AdminLoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PortalLoginResponse:
    """Logowanie centralne i zwrot dostępnych sekcji interfejsu."""
    stmt = select(AdminUser).where(AdminUser.email == payload.email)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Nieprawidłowe dane logowania."
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

    sections = await section_permissions.get_user_sections(session, user)
    await record_audit(
        session,
        user_id=user.id,
        action="portal_login",
        client_ip=admin_session.client_ip,
        payload={"user_id": user.id, "sections": sections},
    )
    await session.commit()
    return PortalLoginResponse(token=token, expires_at=expires_at, sections=sections)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Wylogowanie z portalu")
async def portal_logout(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Unieważnia aktywną sesję użytkownika."""
    admin_session, admin_user = admin_context
    admin_session.revoked_at = datetime.now(UTC)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="portal_logout",
        client_ip=admin_session.client_ip,
        payload={"user_id": admin_user.id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=PortalUserInfo, summary="Informacje o zalogowanym użytkowniku")
async def portal_me(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PortalUserInfo:
    """Zwraca dane konta i listę dostępnych sekcji."""
    _, admin_user = admin_context
    sections = await section_permissions.get_user_sections(session, admin_user)
    return PortalUserInfo(
        id=admin_user.id,
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        role=admin_user.role,
        sections=sections,
    )


@router.get("/profile", response_model=PortalProfile, summary="Profil użytkownika")
async def portal_profile(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PortalProfile:
    """Zwraca profil użytkownika dla widoku /choice."""
    _, admin_user = admin_context
    sections = await section_permissions.get_user_sections(session, admin_user)
    return PortalProfile(
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        internal_ext=admin_user.internal_ext,
        mobile_phone=admin_user.mobile_phone,
        role=admin_user.role,
        sections=sections,
    )


@router.put("/profile", response_model=PortalProfile, summary="Aktualizuj profil użytkownika")
async def portal_update_profile(
    payload: PortalProfileUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PortalProfile:
    """Aktualizuje własny profil użytkownika po zalogowaniu centralnym."""
    admin_session, admin_user = admin_context
    try:
        await operator_settings.update_profile(
            session,
            admin_user,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            internal_ext=payload.internal_ext,
            mobile_phone=payload.mobile_phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    sections = await section_permissions.get_user_sections(session, admin_user)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="portal_profile_update",
        client_ip=admin_session.client_ip,
        payload={"user_id": admin_user.id, "sections": sections},
    )
    await session.commit()
    return PortalProfile(
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        internal_ext=admin_user.internal_ext,
        mobile_phone=admin_user.mobile_phone,
        role=admin_user.role,
        sections=sections,
    )


@router.post(
    "/profile/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Zmień hasło użytkownika",
    response_class=Response,
)
async def portal_change_password(
    payload: PortalPasswordChangeRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Zmienia hasło użytkownika i unieważnia pozostałe sesje."""
    admin_session, admin_user = admin_context
    try:
        await operator_settings.change_password(
            session,
            admin_user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            exclude_token=admin_session.token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="portal_profile_change_password",
        client_ip=admin_session.client_ip,
        payload={"user_id": admin_user.id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
