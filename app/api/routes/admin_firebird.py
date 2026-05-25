"""Operacje pomocnicze związane z konfiguracją Firebird."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_config import (
    load_firebird_config,
    load_firebird_vmaintenance_config,
)
from app.core.config import settings
from app.schemas.admin import (
    FirebirdTestRequest,
    FirebirdTestResponse,
    FirebirdVMaintenanceTestRequest,
)
from app.services.audit import record_audit
from app.services.firebird_client import test_firebird_connection

router = APIRouter(prefix="/admin/firebird", tags=["admin-firebird"])


def _normalize_mode(value: str | None) -> str:
    mode = (value or "").strip().lower() or settings.fb_mode.lower()
    if mode not in {"network", "local"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tryb Firebird musi mieć wartość: network albo local.",
        )
    return mode


@router.post("/test", response_model=FirebirdTestResponse, summary="Sprawdź połączenie Firebird")
async def test_firebird_configuration(
    payload: FirebirdTestRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FirebirdTestResponse:
    """Weryfikuje konfigurację Firebird i wykonuje test logowania do bazy."""
    admin_session, admin_user = admin_context
    if admin_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora.",
        )

    config = await load_firebird_config(session)
    password = settings.fb_password

    mode = _normalize_mode(payload.mode if payload and payload.mode is not None else config.mode)
    host = payload.host if payload and payload.host is not None else config.host
    port = payload.port if payload and payload.port is not None else config.port
    database = payload.database if payload and payload.database is not None else config.database
    local_copy_path = (
        payload.local_copy_path
        if payload and payload.local_copy_path is not None
        else config.local_copy_path
    )
    user = payload.user if payload and payload.user is not None else config.user
    charset = payload.charset if payload and payload.charset is not None else config.charset
    role = payload.role if payload and payload.role is not None else config.role
    password_override = payload.password if payload and payload.password is not None else password

    if mode == "local":
        host_value = "127.0.0.1"
        local_path = Path((local_copy_path or "").strip()).expanduser()
        test_database = str(local_path.resolve())
    else:
        host_value = (host or "").strip()
        test_database = (database or "").strip()

    result = test_firebird_connection(
        host=host_value,
        port=int(port or settings.fb_port),
        database=test_database,
        user=(user or "").strip(),
        password=password_override,
        charset=(charset or settings.fb_charset).strip() or settings.fb_charset,
        role=(role or "").strip() or None,
    )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_firebird_test",
        client_ip=admin_session.client_ip,
        payload={
            "success": result.success,
            "message": result.message,
            "mode": mode,
            "host": host_value,
            "port": int(port or settings.fb_port),
            "database": test_database,
            "network_database": database,
            "local_copy_path": local_copy_path,
            "user": user,
            "charset": charset,
            "role": role,
            "engine_version": result.engine_version,
        },
    )
    await session.commit()

    return FirebirdTestResponse(
        success=result.success,
        message=result.message,
        engine_version=result.engine_version,
    )


@router.post(
    "/test-vmaintenance",
    response_model=FirebirdTestResponse,
    summary="Sprawdź połączenie Firebird v-maintenance",
)
async def test_firebird_vmaintenance_configuration(
    payload: FirebirdVMaintenanceTestRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FirebirdTestResponse:
    """Weryfikuje konfigurację Firebird v-maintenance i wykonuje test logowania."""
    admin_session, admin_user = admin_context
    if admin_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora.",
        )

    config = await load_firebird_vmaintenance_config(session)
    password = settings.fb_v_password

    host = payload.host if payload and payload.host is not None else config.host
    port = payload.port if payload and payload.port is not None else config.port
    database = payload.database if payload and payload.database is not None else config.database
    user = payload.user if payload and payload.user is not None else config.user
    charset = payload.charset if payload and payload.charset is not None else config.charset
    role = payload.role if payload and payload.role is not None else config.role
    password_override = payload.password if payload and payload.password is not None else password

    host_value = (host or "").strip()
    database_value = (database or "").strip()
    user_value = (user or "").strip()
    if not host_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Host Firebird v-maintenance nie może być pusty.",
        )
    if not database_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ścieżka bazy Firebird v-maintenance nie może być pusta.",
        )
    if not user_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Użytkownik Firebird v-maintenance nie może być pusty.",
        )

    result = test_firebird_connection(
        host=host_value,
        port=int(port or settings.fb_v_port),
        database=database_value,
        user=user_value,
        password=password_override,
        charset=(charset or settings.fb_v_charset).strip() or settings.fb_v_charset,
        role=(role or "").strip() or None,
    )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_firebird_vmaintenance_test",
        client_ip=admin_session.client_ip,
        payload={
            "success": result.success,
            "message": result.message,
            "host": host_value,
            "port": int(port or settings.fb_v_port),
            "database": database_value,
            "user": user_value,
            "charset": charset,
            "role": role,
            "engine_version": result.engine_version,
        },
    )
    await session.commit()

    return FirebirdTestResponse(
        success=result.success,
        message=result.message,
        engine_version=result.engine_version,
    )


__all__ = ["router"]
