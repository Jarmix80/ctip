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
    PortalDeviceThemePreference,
    PortalLoginResponse,
    PortalPasswordChangeRequest,
    PortalProfile,
    PortalProfileUpdate,
    PortalShippingLayoutPreference,
    PortalUserInfo,
)
from app.services import operator_settings, section_permissions
from app.services.audit import record_audit
from app.services.login_security import (
    login_is_rate_limited,
    record_login_failure,
    record_login_rate_limit,
    request_client_ip,
)
from app.services.security import generate_session_token, hash_session_token, verify_password
from app.services.session_cookie import clear_admin_session_cookie, set_admin_session_cookie

router = APIRouter(prefix="/auth", tags=["portal-auth"])


@router.post("/login", response_model=PortalLoginResponse, summary="Logowanie do strony głównej")
async def portal_login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PortalLoginResponse:
    """Logowanie centralne, zwrot sekcji i ustawienie ciasteczka sesji."""
    client_ip = request_client_ip(request)
    if await login_is_rate_limited(session, email=payload.email, client_ip=client_ip):
        await record_login_rate_limit(
            session,
            email=payload.email,
            client_ip=client_ip,
            channel="portal",
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Przekroczono limit prób logowania. Spróbuj ponownie później.",
            headers={"Retry-After": str(settings.login_failure_window_minutes * 60)},
        )

    stmt = select(AdminUser).where(AdminUser.email == payload.email)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        await record_login_failure(
            session,
            email=payload.email,
            client_ip=client_ip,
            channel="portal",
            reason="invalid_credentials",
        )
        await session.commit()
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
        token=hash_session_token(token),
        created_at=now,
        expires_at=expires_at,
        client_ip=client_ip,
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
    set_admin_session_cookie(response, token=token, expires_at=expires_at)
    return PortalLoginResponse(token=token, expires_at=expires_at, sections=sections)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Wylogowanie z portalu")
async def portal_logout(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Uniewaznia aktywna sesje uzytkownika i czysci ciasteczko."""
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
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_admin_session_cookie(response)
    return response


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
        is_salesperson=bool(admin_user.is_salesperson),
        device_theme=admin_user.device_theme,
        shipping_layout=admin_user.shipping_layout,
        sections=sections,
    )


@router.put(
    "/preferences/device-theme",
    response_model=PortalDeviceThemePreference,
    summary="Zapisz kolorystykę modułu urządzeń",
)
async def portal_update_device_theme(
    payload: PortalDeviceThemePreference,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PortalDeviceThemePreference:
    """Zapisuje kolorystykę modułu urządzeń na koncie zalogowanego użytkownika."""
    admin_session, admin_user = admin_context
    admin_user.device_theme = payload.theme
    await record_audit(
        session,
        user_id=admin_user.id,
        action="portal_device_theme_update",
        client_ip=admin_session.client_ip,
        payload={"theme": payload.theme},
    )
    await session.commit()
    return PortalDeviceThemePreference(theme=admin_user.device_theme)


@router.put(
    "/preferences/shipping-layout",
    response_model=PortalShippingLayoutPreference,
    summary="Zapisz wygląd modułu wysyłek",
)
async def portal_update_shipping_layout(
    payload: PortalShippingLayoutPreference,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PortalShippingLayoutPreference:
    """Zapisuje domyślny wygląd Shipping na koncie zalogowanego użytkownika."""
    admin_session, admin_user = admin_context
    admin_user.shipping_layout = payload.layout
    await record_audit(
        session,
        user_id=admin_user.id,
        action="portal_shipping_layout_update",
        client_ip=admin_session.client_ip,
        payload={"layout": payload.layout},
    )
    await session.commit()
    return PortalShippingLayoutPreference(layout=admin_user.shipping_layout)


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
        is_salesperson=bool(admin_user.is_salesperson),
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
        is_salesperson=bool(admin_user.is_salesperson),
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
