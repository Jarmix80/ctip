"""Wewnętrzne API katalogu tożsamości dla usług voice i CHAT_KP."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models import BotIdentityResolution
from app.schemas.bot_identity import (
    IdentityConfirmRequest,
    IdentityConfirmResponse,
    IdentityDevicesRequest,
    IdentityDevicesResponse,
    IdentityNipVerifyRequest,
    IdentityNipVerifyResponse,
    IdentityResolveRequest,
    IdentityResolveResponse,
)
from app.services.bot_identity_directory import (
    authenticate_service_token,
    confirm_current,
    disclose_devices,
    resolve_phone,
    verify_resolution_nip,
)

router = APIRouter(prefix="/internal/v1", tags=["bot-identity"])


def _service_channel(authorization: str | None = Header(default=None)) -> str:
    try:
        return authenticate_service_token(authorization)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nieprawidłowe uwierzytelnienie usługi.",
        ) from exc


@router.post("/identities/resolve-phone", response_model=IdentityResolveResponse)
async def resolve_identity_phone(
    payload: IdentityResolveRequest,
    channel: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IdentityResolveResponse:
    if payload.channel != channel:
        raise HTTPException(status_code=403, detail="Token nie ma dostępu do tego kanału.")
    try:
        result = await resolve_phone(
            session,
            channel=channel,
            conversation_ref=payload.conversation_ref,
            phone=payload.phone,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return result


@router.post(
    "/identity-resolutions/{resolution_ref}/verify-nip",
    response_model=IdentityNipVerifyResponse,
)
async def verify_identity_nip(
    resolution_ref: str,
    payload: IdentityNipVerifyRequest,
    channel: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IdentityNipVerifyResponse:
    resolution = await session.get(BotIdentityResolution, resolution_ref)
    if resolution is not None and resolution.channel != channel:
        raise HTTPException(status_code=403, detail="Rozwiązanie należy do innego kanału.")
    result = await verify_resolution_nip(
        session,
        resolution_ref=resolution_ref,
        conversation_ref=payload.conversation_ref,
        nip=payload.nip,
    )
    await session.commit()
    return result


@router.post(
    "/identity-resolutions/{resolution_ref}/confirm-current",
    response_model=IdentityConfirmResponse,
)
async def confirm_identity_current(
    resolution_ref: str,
    payload: IdentityConfirmRequest,
    channel: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IdentityConfirmResponse:
    resolution = await session.get(BotIdentityResolution, resolution_ref)
    if resolution is not None and resolution.channel != channel:
        raise HTTPException(status_code=403, detail="Rozwiązanie należy do innego kanału.")
    result = await confirm_current(
        session,
        resolution_ref=resolution_ref,
        conversation_ref=payload.conversation_ref,
        confirmed=payload.confirmed,
    )
    await session.commit()
    return result


@router.post(
    "/customers/{customer_ref}/devices",
    response_model=IdentityDevicesResponse,
)
async def get_identity_devices(
    customer_ref: str,
    payload: IdentityDevicesRequest,
    channel: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IdentityDevicesResponse:
    if payload.channel != channel:
        raise HTTPException(status_code=403, detail="Token nie ma dostępu do tego kanału.")
    try:
        result = await disclose_devices(
            session,
            customer_ref=customer_ref,
            disclosure_grant=payload.disclosure_grant,
            channel=channel,
            conversation_ref=payload.conversation_ref,
        )
        await session.commit()
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
