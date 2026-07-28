"""Panelowe API synchronizacji i rozstrzygania katalogu tożsamości botów."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.schemas.bot_identity import (
    IdentityDuplicateListResponse,
    IdentityOverrideRequest,
    IdentitySyncStatusResponse,
    PromoteSmsBindingRequest,
)
from app.services.audit import record_audit
from app.services.bot_identity_directory import (
    directory_status,
    list_duplicate_groups,
    promote_sms_binding,
    set_override,
    sync_firebird_directory,
)

router = APIRouter(prefix="/admin/bot-identities", tags=["admin-bot-identities"])


def _can_manage(role: str) -> None:
    if role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Brak uprawnień operatora.")


@router.get("/status", response_model=IdentitySyncStatusResponse)
async def get_identity_status(
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IdentitySyncStatusResponse:
    return await directory_status(session)


@router.get("/duplicates", response_model=IdentityDuplicateListResponse)
async def get_identity_duplicates(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IdentityDuplicateListResponse:
    _, user = admin_context
    _can_manage(user.role)
    return IdentityDuplicateListResponse(items=await list_duplicate_groups(session))


@router.post("/overrides", status_code=status.HTTP_200_OK)
async def put_identity_override(
    payload: IdentityOverrideRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> None:
    admin_session, user = admin_context
    _can_manage(user.role)
    try:
        item = await set_override(
            session,
            phone_ref=payload.phone_ref,
            subject_id=payload.subject_id,
            binding_id=payload.binding_id,
            reason=payload.reason,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit(
        session,
        user_id=user.id,
        action="bot_identity_override_set",
        client_ip=admin_session.client_ip,
        payload={
            "override_id": item.id,
            "subject_id": item.subject_id,
            "binding_id": item.binding_id,
        },
    )
    await session.commit()


@router.post("/promote-sms", status_code=status.HTTP_200_OK)
async def approve_sms_identity_binding(
    payload: PromoteSmsBindingRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> None:
    admin_session, user = admin_context
    _can_manage(user.role)
    try:
        binding = await promote_sms_binding(session, payload)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit(
        session,
        user_id=user.id,
        action="bot_identity_sms_promoted",
        client_ip=admin_session.client_ip,
        payload={"binding_id": binding.id, "case_ref": payload.case_ref},
    )
    await session.commit()


@router.post("/sync", response_model=IdentitySyncStatusResponse)
async def run_identity_sync(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IdentitySyncStatusResponse:
    admin_session, user = admin_context
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Synchronizacja wymaga roli administratora.")
    run = await sync_firebird_directory(session)
    await record_audit(
        session,
        user_id=user.id,
        action="bot_identity_sync",
        client_ip=admin_session.client_ip,
        payload={"sync_id": run.id, "status": run.status},
    )
    await session.commit()
    return await directory_status(session)
