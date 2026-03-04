"""Operacje związane z kopiami zapasowymi w panelu administratora."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_config import settings_store
from app.core.config import settings
from app.schemas.admin_backup import (
    BackupConfigResponse,
    BackupConfigUpdate,
    BackupHistoryEntry,
    BackupHistoryResponse,
    BackupOffice365TestResponse,
    BackupRestoreRequest,
    BackupRestoreResponse,
    BackupRunRequest,
    BackupRunResponse,
)
from app.services.audit import record_audit
from app.services.backup_runner import list_backup_files
from app.services.office365_backup import Office365BackupError, test_office365_connection

router = APIRouter(prefix="/admin/backup", tags=["admin-backup"])


def _ensure_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operacja wymaga roli administratora."
        )


def _to_int(value: str | int | None, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _to_bool(value: str | bool | None, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "on"}
    return default


async def load_backup_config(session: AsyncSession) -> BackupConfigResponse:
    """Wczytuje konfigurację modułu kopii zapasowych."""
    stored = await settings_store.get_namespace(session, "backup")
    office_secret_set = bool(stored.get("office_client_secret") or settings.office365_client_secret)
    optima_password_set = bool(stored.get("optima_password") or settings.optima_sql_password)
    return BackupConfigResponse(
        schedule_morning=stored.get("schedule_morning") or "06:00",
        schedule_evening=stored.get("schedule_evening") or "20:00",
        retention_local_copies=_to_int(stored.get("retention_local_copies"), 14),
        retention_cloud_copies=_to_int(stored.get("retention_cloud_copies"), 7),
        archive_ctip_files=_to_bool(stored.get("archive_ctip_files"), True),
        archive_ctip_db=_to_bool(stored.get("archive_ctip_db"), True),
        archive_firebird_prod=_to_bool(stored.get("archive_firebird_prod"), True),
        archive_firebird_test=_to_bool(stored.get("archive_firebird_test"), True),
        archive_optima=_to_bool(stored.get("archive_optima"), True),
        storage_mode=stored.get("storage_mode") or "local",
        local_directory=stored.get("local_directory") or settings.backup_default_local_dir,
        network_directory=stored.get("network_directory"),
        cloud_provider=stored.get("cloud_provider") or "office365",
        cloud_only_evening=_to_bool(stored.get("cloud_only_evening"), True),
        office_tenant_id=stored.get("office_tenant_id") or settings.office365_tenant_id,
        office_client_id=stored.get("office_client_id") or settings.office365_client_id,
        office_site_id=stored.get("office_site_id") or settings.office365_site_id,
        office_drive_id=stored.get("office_drive_id") or settings.office365_drive_id,
        office_folder_path=stored.get("office_folder_path") or settings.office365_folder_path,
        office_folder_ctip=stored.get("office_folder_ctip") or settings.office365_folder_ctip,
        office_folder_firebird_prod=stored.get("office_folder_firebird_prod")
        or settings.office365_folder_firebird_prod,
        office_folder_firebird_test=stored.get("office_folder_firebird_test")
        or settings.office365_folder_firebird_test,
        office_folder_optima=stored.get("office_folder_optima") or settings.office365_folder_optima,
        office_client_secret_set=office_secret_set,
        optima_server_instance=stored.get("optima_server_instance")
        or settings.optima_sql_server_instance,
        optima_host=stored.get("optima_host")
        or settings.optima_sql_host
        or settings.optima_sql_host_ip,
        optima_port=_to_int(stored.get("optima_port"), settings.optima_sql_port),
        optima_auth_mode=stored.get("optima_auth_mode") or settings.optima_sql_auth_mode or "mixed",
        optima_login=stored.get("optima_login") or settings.optima_sql_login,
        optima_password_set=optima_password_set,
        optima_db_it_partner=stored.get("optima_db_it_partner") or settings.optima_db_it_partner,
        optima_db_ksero_partner=stored.get("optima_db_ksero_partner")
        or settings.optima_db_ksero_partner,
        optima_db_config=stored.get("optima_db_config") or settings.optima_db_config,
        execution_enabled=settings.backup_execution_enabled,
    )


@router.get("/config", response_model=BackupConfigResponse, summary="Konfiguracja kopii zapasowych")
async def backup_get_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> BackupConfigResponse:
    """Zwraca aktywną konfigurację harmonogramu i miejsc archiwizacji."""
    _, admin_user = admin_context
    _ensure_admin(admin_user.role)
    return await load_backup_config(session)


@router.put(
    "/config",
    response_model=BackupConfigResponse,
    summary="Aktualizacja konfiguracji kopii zapasowych",
)
async def backup_update_config(
    payload: BackupConfigUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> BackupConfigResponse:
    """Zapisuje konfigurację harmonogramu i zakresu backupu."""
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)

    from app.services.settings_store import StoredValue

    values = {
        "schedule_morning": StoredValue(payload.schedule_morning, False),
        "schedule_evening": StoredValue(payload.schedule_evening, False),
        "retention_local_copies": StoredValue(str(payload.retention_local_copies), False),
        "retention_cloud_copies": StoredValue(str(payload.retention_cloud_copies), False),
        "archive_ctip_files": StoredValue(str(payload.archive_ctip_files).lower(), False),
        "archive_ctip_db": StoredValue(str(payload.archive_ctip_db).lower(), False),
        "archive_firebird_prod": StoredValue(str(payload.archive_firebird_prod).lower(), False),
        "archive_firebird_test": StoredValue(str(payload.archive_firebird_test).lower(), False),
        "archive_optima": StoredValue(str(payload.archive_optima).lower(), False),
        "storage_mode": StoredValue(payload.storage_mode, False),
        "local_directory": StoredValue(payload.local_directory, False),
        "network_directory": StoredValue(payload.network_directory or "", False),
        "cloud_provider": StoredValue(payload.cloud_provider, False),
        "cloud_only_evening": StoredValue(str(payload.cloud_only_evening).lower(), False),
        "office_tenant_id": StoredValue(payload.office_tenant_id or "", False),
        "office_client_id": StoredValue(payload.office_client_id or "", False),
        "office_site_id": StoredValue(payload.office_site_id or "", False),
        "office_drive_id": StoredValue(payload.office_drive_id or "", False),
        "office_folder_path": StoredValue(payload.office_folder_path or "", False),
        "office_folder_ctip": StoredValue(payload.office_folder_ctip, False),
        "office_folder_firebird_prod": StoredValue(payload.office_folder_firebird_prod, False),
        "office_folder_firebird_test": StoredValue(payload.office_folder_firebird_test, False),
        "office_folder_optima": StoredValue(payload.office_folder_optima, False),
        "optima_server_instance": StoredValue(payload.optima_server_instance or "", False),
        "optima_host": StoredValue(payload.optima_host or "", False),
        "optima_port": StoredValue(str(payload.optima_port), False),
        "optima_auth_mode": StoredValue(payload.optima_auth_mode, False),
        "optima_login": StoredValue(payload.optima_login or "", False),
        "optima_db_it_partner": StoredValue(payload.optima_db_it_partner or "", False),
        "optima_db_ksero_partner": StoredValue(payload.optima_db_ksero_partner or "", False),
        "optima_db_config": StoredValue(payload.optima_db_config or "", False),
    }
    if payload.office_client_secret is not None:
        values["office_client_secret"] = StoredValue(payload.office_client_secret, True)
    if payload.optima_password is not None:
        values["optima_password"] = StoredValue(payload.optima_password, True)

    await settings_store.set_namespace(session, "backup", values, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="backup_config_update",
        client_ip=admin_session.client_ip,
        payload={
            "schedule_morning": payload.schedule_morning,
            "schedule_evening": payload.schedule_evening,
            "retention_local_copies": payload.retention_local_copies,
            "retention_cloud_copies": payload.retention_cloud_copies,
            "archive_ctip_files": payload.archive_ctip_files,
            "archive_ctip_db": payload.archive_ctip_db,
            "archive_firebird_prod": payload.archive_firebird_prod,
            "archive_firebird_test": payload.archive_firebird_test,
            "archive_optima": payload.archive_optima,
            "storage_mode": payload.storage_mode,
            "local_directory": payload.local_directory,
            "network_directory": payload.network_directory,
            "cloud_provider": payload.cloud_provider,
            "cloud_only_evening": payload.cloud_only_evening,
            "office_tenant_id_set": bool(payload.office_tenant_id),
            "office_client_id_set": bool(payload.office_client_id),
            "office_site_id_set": bool(payload.office_site_id),
            "office_drive_id_set": bool(payload.office_drive_id),
            "office_folder_path_set": bool(payload.office_folder_path),
            "office_folder_ctip": payload.office_folder_ctip,
            "office_folder_firebird_prod": payload.office_folder_firebird_prod,
            "office_folder_firebird_test": payload.office_folder_firebird_test,
            "office_folder_optima": payload.office_folder_optima,
            "office_client_secret_changed": payload.office_client_secret is not None,
            "optima_server_instance_set": bool(payload.optima_server_instance),
            "optima_host_set": bool(payload.optima_host),
            "optima_port": payload.optima_port,
            "optima_auth_mode": payload.optima_auth_mode,
            "optima_login_set": bool(payload.optima_login),
            "optima_password_changed": payload.optima_password is not None,
            "optima_db_it_partner_set": bool(payload.optima_db_it_partner),
            "optima_db_ksero_partner_set": bool(payload.optima_db_ksero_partner),
            "optima_db_config_set": bool(payload.optima_db_config),
        },
    )
    await session.commit()
    return await load_backup_config(session)


@router.post(
    "/office365/test",
    response_model=BackupOffice365TestResponse,
    summary="Test połączenia Office 365 / SharePoint",
)
async def backup_test_office365(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> BackupOffice365TestResponse:
    """Weryfikuje token OAuth i dostęp do SharePoint Drive/folderu."""
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)

    stored = await settings_store.get_namespace(session, "backup")
    tenant_id = stored.get("office_tenant_id") or settings.office365_tenant_id or ""
    client_id = stored.get("office_client_id") or settings.office365_client_id or ""
    client_secret = stored.get("office_client_secret") or settings.office365_client_secret or ""
    site_id = stored.get("office_site_id") or settings.office365_site_id
    drive_id = stored.get("office_drive_id") or settings.office365_drive_id
    folder_path = (
        stored.get("office_folder_ctip")
        or stored.get("office_folder_path")
        or settings.office365_folder_ctip
        or settings.office365_folder_path
    )

    try:
        result = await test_office365_connection(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            site_id=site_id,
            drive_id=drive_id,
            folder_path=folder_path,
        )
    except Office365BackupError as exc:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="backup_office365_test_failed",
            client_ip=admin_session.client_ip,
            payload={"error": str(exc)},
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not stored.get("office_drive_id") and result.drive_id:
        from app.services.settings_store import StoredValue

        await settings_store.set_namespace(
            session,
            "backup",
            {"office_drive_id": StoredValue(result.drive_id, False)},
            user_id=admin_user.id,
        )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="backup_office365_test_ok",
        client_ip=admin_session.client_ip,
        payload={
            "site_id_set": bool(result.site_id),
            "drive_id": result.drive_id,
            "folder_path": result.folder_path,
        },
    )
    await session.commit()

    return BackupOffice365TestResponse(
        ok=result.ok,
        message=result.message,
        site_id=result.site_id,
        drive_id=result.drive_id,
        folder_path=result.folder_path,
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
            confirmed=bool(item.checksum),
        )
        for item in items
    ]
    note = "Lista lokalnych kopii zapasowych (potwierdzone = dostępna suma kontrolna)."
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

    if not settings.backup_execution_enabled:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="backup_run_blocked_non_prod",
            client_ip=admin_session.client_ip,
            payload={"label": payload.label, "compress": payload.compress},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Backup jest wyłączony poza środowiskiem produkcyjnym.",
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

    if not settings.backup_execution_enabled:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="backup_restore_blocked_non_prod",
            client_ip=admin_session.client_ip,
            payload={"backup_name": payload.backup_name},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Przywracanie kopii jest wyłączone poza środowiskiem produkcyjnym.",
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
