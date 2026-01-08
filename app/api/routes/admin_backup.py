"""Operacje związane z kopiami zapasowymi w panelu administratora."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.schemas.admin_backup import (
    BackupHistoryEntry,
    BackupHistoryResponse,
    BackupRestoreRequest,
    BackupRestoreResponse,
    BackupRunRequest,
    BackupRunResponse,
)
from app.services.audit import record_audit
from app.services.backup_runner import list_backup_files

router = APIRouter(prefix="/admin/backup", tags=["admin-backup"])


def _ensure_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operacja wymaga roli administratora."
        )


@router.get("/history", response_model=BackupHistoryResponse, summary="Historia kopii zapasowych")
async def backup_history(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
) -> BackupHistoryResponse:
    """Zwraca listę dostępnych plików kopii zapasowych (MVP)."""
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)

    items = list_backup_files()
    response_items = [
        BackupHistoryEntry(
            name=item.name,
            size_bytes=item.size_bytes,
            modified_at=item.modified_at,
            status=item.status,
            checksum=item.checksum,
        )
        for item in items
    ]
    note = "Moduł kopii zapasowych działa w trybie podglądu."
    return BackupHistoryResponse(generated_at=datetime.now(UTC), items=response_items, note=note)


@router.post("/run", response_model=BackupRunResponse, summary="Uruchom kopię zapasową")
async def backup_run(
    payload: BackupRunRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> BackupRunResponse:
    """Uruchamia kopię zapasową w trybie dry-run."""
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)

    if payload.dry_run:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="backup_run_dry",
            client_ip=admin_session.client_ip,
            payload={
                "label": payload.label,
                "compress": payload.compress,
                "dry_run": payload.dry_run,
            },
        )
        await session.commit()
        return BackupRunResponse(
            accepted=True,
            dry_run=True,
            message="Symulacja kopii zapasowej zakończona.",
            backup_name=None,
        )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="backup_run_blocked",
        client_ip=admin_session.client_ip,
        payload={"label": payload.label, "compress": payload.compress},
    )
    await session.commit()
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Moduł kopii zapasowych nie jest jeszcze aktywny.",
    )


@router.post("/restore", response_model=BackupRestoreResponse, summary="Przywróć kopię zapasową")
async def backup_restore(
    payload: BackupRestoreRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> BackupRestoreResponse:
    """Przywraca kopię zapasową w trybie dry-run."""
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)

    if payload.dry_run:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="backup_restore_dry",
            client_ip=admin_session.client_ip,
            payload={
                "backup_name": payload.backup_name,
                "dry_run": payload.dry_run,
            },
        )
        await session.commit()
        return BackupRestoreResponse(
            accepted=True,
            dry_run=True,
            message="Symulacja przywracania kopii zapasowej zakończona.",
        )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="backup_restore_blocked",
        client_ip=admin_session.client_ip,
        payload={"backup_name": payload.backup_name},
    )
    await session.commit()
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Przywracanie kopii zapasowych nie jest jeszcze aktywne.",
    )


__all__ = ["router"]
