"""Operacje związane z kopiami zapasowymi w panelu administratora."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_config import settings_store
from app.core.config import settings
from app.db.session import AsyncSessionLocal
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
from app.services.backup_runner import (
    BackupRunError,
    BackupRunResult,
    create_local_backup,
    list_backup_files,
    prune_local_backups,
)
from app.services.office365_backup import (
    Office365BackupError,
    prune_sharepoint_backups,
    test_office365_connection,
    upload_file_to_sharepoint,
)

router = APIRouter(prefix="/admin/backup", tags=["admin-backup"])
logger = logging.getLogger(__name__)
_scheduler_task: asyncio.Task[None] | None = None
_scheduler_stop_event: asyncio.Event | None = None
_scheduler_last_run: dict[str, str] = {}
BACKUP_ENV_LOCK_REASON = (
    "Dane połączeniowe i sekretne modułu backupów są zarządzane wyłącznie z pliku .env."
)


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


def _resolve_cloud_targets(cfg: BackupConfigResponse) -> list[str]:
    """Wyznacza pojedynczy folder kompletnego archiwum CTIP w chmurze."""
    target = cfg.office_folder_ctip or cfg.office_folder_path or ""
    normalized = target.strip().strip("/")
    return [normalized] if normalized else []


async def _execute_backup_job(
    *,
    cfg: BackupConfigResponse,
    label: str | None,
    compress: bool,
    cloud_upload_enabled: bool,
) -> dict[str, object]:
    """Wykonuje backup, retencję i opcjonalny upload kompletnego archiwum."""
    run_result: BackupRunResult = await asyncio.to_thread(
        create_local_backup,
        label=label,
        compress=compress,
        config=cfg.model_dump(),
    )

    uploaded_folders: list[str] = []
    upload_errors: list[str] = []
    upload_urls: list[str] = []
    local_deleted = 0
    cloud_deleted = 0

    try:
        local_deleted = await asyncio.to_thread(
            prune_local_backups,
            cfg.retention_local_copies,
        )
    except OSError as exc:
        upload_errors.append(f"retencja lokalna: {exc}")

    if cloud_upload_enabled and cfg.cloud_provider == "office365":
        tenant_id = cfg.office_tenant_id or ""
        client_id = cfg.office_client_id or ""
        client_secret = settings.office365_client_secret or ""
        site_id = cfg.office_site_id
        drive_id = cfg.office_drive_id

        cloud_targets = _resolve_cloud_targets(cfg)
        if not cloud_targets:
            upload_errors.append("Brak folderu docelowego Office 365.")
        for folder_path in cloud_targets:
            try:
                up_archive = await upload_file_to_sharepoint(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret,
                    site_id=site_id,
                    drive_id=drive_id,
                    folder_path=folder_path,
                    file_path=run_result.backup_path,
                )
                await upload_file_to_sharepoint(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret,
                    site_id=site_id,
                    drive_id=up_archive.drive_id,
                    folder_path=folder_path,
                    file_path=run_result.checksum_path,
                )
                uploaded_folders.append(folder_path)
                if up_archive.web_url:
                    upload_urls.append(up_archive.web_url)
            except Office365BackupError as exc:
                upload_errors.append(f"{folder_path}: {exc}")
                continue

            try:
                prune_result = await prune_sharepoint_backups(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret,
                    site_id=site_id,
                    drive_id=up_archive.drive_id,
                    folder_path=folder_path,
                    retention_count=cfg.retention_cloud_copies,
                )
                cloud_deleted += prune_result.deleted_archives
            except Office365BackupError as exc:
                upload_errors.append(f"{folder_path}, retencja: {exc}")

    run_status = "SUCCESS"
    if run_result.omitted_components or upload_errors:
        run_status = "PARTIAL"

    return {
        "run_result": run_result,
        "status": run_status,
        "uploaded_folders": uploaded_folders,
        "upload_errors": upload_errors,
        "upload_urls": upload_urls,
        "local_deleted": local_deleted,
        "cloud_deleted": cloud_deleted,
    }


async def load_backup_config(session: AsyncSession) -> BackupConfigResponse:
    """Wczytuje konfigurację modułu kopii zapasowych."""
    stored = await settings_store.get_namespace(session, "backup")
    office_secret_set = bool(settings.office365_client_secret)
    optima_password_set = bool(settings.optima_sql_password)
    optima_auth_mode = (settings.optima_sql_auth_mode or "mixed").strip().lower() or "mixed"
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
        local_directory=settings.backup_default_local_dir,
        network_directory=stored.get("network_directory"),
        cloud_provider=stored.get("cloud_provider") or "office365",
        cloud_only_evening=_to_bool(stored.get("cloud_only_evening"), True),
        office_tenant_id=settings.office365_tenant_id,
        office_client_id=settings.office365_client_id,
        office_site_id=settings.office365_site_id,
        office_drive_id=settings.office365_drive_id,
        office_folder_path=settings.office365_folder_path,
        office_folder_ctip=settings.office365_folder_ctip,
        office_folder_firebird_prod=settings.office365_folder_firebird_prod,
        office_folder_firebird_test=settings.office365_folder_firebird_test,
        office_folder_optima=settings.office365_folder_optima,
        office_client_secret_set=office_secret_set,
        optima_server_instance=settings.optima_sql_server_instance,
        optima_host=settings.optima_sql_host or settings.optima_sql_host_ip,
        optima_port=settings.optima_sql_port,
        optima_auth_mode=optima_auth_mode,
        optima_login=settings.optima_sql_login,
        optima_password_set=optima_password_set,
        optima_db_it_partner=settings.optima_db_it_partner,
        optima_db_ksero_partner=settings.optima_db_ksero_partner,
        optima_db_config=settings.optima_db_config,
        execution_enabled=settings.backup_execution_active,
        integration_source="env",
        integration_editable=False,
        operational_editable=True,
        lock_reason=BACKUP_ENV_LOCK_REASON,
    )


def _scheduled_slot_for_now(cfg: BackupConfigResponse, now: datetime) -> str | None:
    """Zwraca nazwę slotu harmonogramu dla bieżącej minuty."""
    hhmm = now.strftime("%H:%M")
    if hhmm == cfg.schedule_morning:
        return "morning"
    if hhmm == cfg.schedule_evening:
        return "evening"
    return None


async def backup_scheduler_tick() -> None:
    """Wykonuje pojedynczy krok harmonogramu backupu."""
    if not settings.backup_execution_active:
        return

    now = datetime.now()
    day_key = now.strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as session:
        cfg = await load_backup_config(session)
        slot = _scheduled_slot_for_now(cfg, now)
        if not slot:
            return
        if _scheduler_last_run.get(slot) == day_key:
            return

        cloud_upload_enabled = not (cfg.cloud_only_evening and slot == "morning")
        label = f"auto_{slot}"

        try:
            outcome = await _execute_backup_job(
                cfg=cfg,
                label=label,
                compress=True,
                cloud_upload_enabled=cloud_upload_enabled,
            )
            run_result = outcome["run_result"]
            uploaded_folders = outcome["uploaded_folders"]
            upload_errors = outcome["upload_errors"]
            upload_urls = outcome["upload_urls"]
            run_status = str(outcome["status"])
            audit_action = (
                "backup_run_auto_success" if run_status == "SUCCESS" else "backup_run_auto_partial"
            )
            await record_audit(
                session,
                user_id=None,
                action=audit_action,
                client_ip="scheduler",
                payload={
                    "slot": slot,
                    "label": label,
                    "status": run_status,
                    "backup_name": run_result.backup_name,
                    "checksum": run_result.checksum,
                    "size_bytes": run_result.size_bytes,
                    "postgres_dump_included": run_result.postgres_dump_included,
                    "included_components": run_result.included_components,
                    "omitted_components": run_result.omitted_components,
                    "cloud_upload_enabled": cloud_upload_enabled,
                    "uploaded_folders": uploaded_folders,
                    "upload_errors": upload_errors,
                    "upload_urls": upload_urls,
                    "local_deleted": outcome["local_deleted"],
                    "cloud_deleted": outcome["cloud_deleted"],
                    "notes": run_result.notes,
                },
            )
        except Exception as exc:  # noqa: BLE001
            await record_audit(
                session,
                user_id=None,
                action="backup_run_auto_failed",
                client_ip="scheduler",
                payload={"slot": slot, "label": label, "error": str(exc)},
            )
            logger.exception("Automatyczny backup nieudany dla slotu %s", slot)
        finally:
            _scheduler_last_run[slot] = day_key
            await session.commit()


async def _backup_scheduler_loop(stop_event: asyncio.Event) -> None:
    """Uruchamia pętlę harmonogramu backupów."""
    while not stop_event.is_set():
        try:
            await backup_scheduler_tick()
        except Exception:  # noqa: BLE001
            logger.exception("Błąd pętli harmonogramu backupów")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
        except TimeoutError:
            continue


async def start_backup_scheduler() -> None:
    """Startuje harmonogram backupów w tle aplikacji."""
    global _scheduler_task, _scheduler_stop_event  # noqa: PLW0603
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(
        _backup_scheduler_loop(_scheduler_stop_event), name="backup-scheduler"
    )
    logger.info("Uruchomiono harmonogram backupow.")


async def stop_backup_scheduler() -> None:
    """Zatrzymuje harmonogram backupów."""
    global _scheduler_task, _scheduler_stop_event  # noqa: PLW0603
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    if _scheduler_task is not None:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=5)
        except TimeoutError:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass
    _scheduler_task = None
    _scheduler_stop_event = None


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
    """Zapisuje tylko operacyjne ustawienia backupu; dane połączeniowe są env-only."""
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)

    from app.services.settings_store import StoredValue

    current = await load_backup_config(session)
    env_locked_changes = {
        "local_directory": payload.local_directory,
        "office_tenant_id": payload.office_tenant_id,
        "office_client_id": payload.office_client_id,
        "office_site_id": payload.office_site_id,
        "office_drive_id": payload.office_drive_id,
        "office_folder_path": payload.office_folder_path,
        "office_folder_ctip": payload.office_folder_ctip,
        "office_folder_firebird_prod": payload.office_folder_firebird_prod,
        "office_folder_firebird_test": payload.office_folder_firebird_test,
        "office_folder_optima": payload.office_folder_optima,
        "office_client_secret": payload.office_client_secret,
        "optima_server_instance": payload.optima_server_instance,
        "optima_host": payload.optima_host,
        "optima_port": payload.optima_port,
        "optima_auth_mode": payload.optima_auth_mode,
        "optima_login": payload.optima_login,
        "optima_password": payload.optima_password,
        "optima_db_it_partner": payload.optima_db_it_partner,
        "optima_db_ksero_partner": payload.optima_db_ksero_partner,
        "optima_db_config": payload.optima_db_config,
    }
    current_env_values = {
        "local_directory": current.local_directory,
        "office_tenant_id": current.office_tenant_id,
        "office_client_id": current.office_client_id,
        "office_site_id": current.office_site_id,
        "office_drive_id": current.office_drive_id,
        "office_folder_path": current.office_folder_path,
        "office_folder_ctip": current.office_folder_ctip,
        "office_folder_firebird_prod": current.office_folder_firebird_prod,
        "office_folder_firebird_test": current.office_folder_firebird_test,
        "office_folder_optima": current.office_folder_optima,
        "office_client_secret": "__present__" if current.office_client_secret_set else None,
        "optima_server_instance": current.optima_server_instance,
        "optima_host": current.optima_host,
        "optima_port": current.optima_port,
        "optima_auth_mode": current.optima_auth_mode,
        "optima_login": current.optima_login,
        "optima_password": "__present__" if current.optima_password_set else None,
        "optima_db_it_partner": current.optima_db_it_partner,
        "optima_db_ksero_partner": current.optima_db_ksero_partner,
        "optima_db_config": current.optima_db_config,
    }
    changed_env_fields: dict[str, object] = {}
    for key, value in env_locked_changes.items():
        if key in {"office_client_secret", "optima_password"}:
            if value:
                changed_env_fields[key] = "__provided__"
            continue
        if value != current_env_values[key]:
            changed_env_fields[key] = value
    if changed_env_fields:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="backup_config_update_blocked_env",
            client_ip=admin_session.client_ip,
            payload={"fields": changed_env_fields},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                f"{BACKUP_ENV_LOCK_REASON} W panelu możesz zmieniać tylko harmonogram, "
                "retencję, zakres archiwizacji, tryb zapisu, katalog sieciowy i przełącznik "
                "cloud_only_evening."
            ),
        )

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
        "network_directory": StoredValue(payload.network_directory or "", False),
        "cloud_provider": StoredValue(payload.cloud_provider, False),
        "cloud_only_evening": StoredValue(str(payload.cloud_only_evening).lower(), False),
    }

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
            "network_directory": payload.network_directory,
            "cloud_provider": payload.cloud_provider,
            "cloud_only_evening": payload.cloud_only_evening,
            "integration_source": "env",
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

    cfg = await load_backup_config(session)
    tenant_id = cfg.office_tenant_id or ""
    client_id = cfg.office_client_id or ""
    client_secret = settings.office365_client_secret or ""
    site_id = cfg.office_site_id
    drive_id = cfg.office_drive_id
    folder_path = cfg.office_folder_ctip or cfg.office_folder_path

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
            status="DRY_RUN",
            message="Symulacja kopii zapasowej zakończona.",
            backup_name=None,
            postgres_dump_included=False,
            uploaded_to_cloud=False,
        )

    if not settings.backup_execution_active:
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

    cfg = await load_backup_config(session)

    try:
        outcome = await _execute_backup_job(
            cfg=cfg,
            label=payload.label,
            compress=payload.compress,
            cloud_upload_enabled=True,
        )
        run_result = outcome["run_result"]
        uploaded_folders = outcome["uploaded_folders"]
        upload_errors = outcome["upload_errors"]
        upload_urls = outcome["upload_urls"]
        run_status = str(outcome["status"])
    except BackupRunError as exc:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="backup_run_failed",
            client_ip=admin_session.client_ip,
            payload={"error": str(exc)},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backup nie został utworzony: {exc}",
        ) from exc

    uploaded = bool(uploaded_folders)
    upload_error = "; ".join(upload_errors) if upload_errors else None

    await record_audit(
        session,
        user_id=admin_user.id,
        action="backup_run_success" if run_status == "SUCCESS" else "backup_run_partial",
        client_ip=admin_session.client_ip,
        payload={
            "label": payload.label,
            "compress": payload.compress,
            "status": run_status,
            "backup_name": run_result.backup_name,
            "checksum": run_result.checksum,
            "size_bytes": run_result.size_bytes,
            "postgres_dump_included": run_result.postgres_dump_included,
            "included_components": run_result.included_components,
            "omitted_components": run_result.omitted_components,
            "uploaded_to_cloud": uploaded,
            "uploaded_folders": uploaded_folders,
            "upload_urls": upload_urls,
            "upload_error": upload_error,
            "local_deleted": outcome["local_deleted"],
            "cloud_deleted": outcome["cloud_deleted"],
            "notes": run_result.notes,
        },
    )
    await session.commit()

    message = "Kopia zapasowa została utworzona."
    if uploaded_folders:
        message += " Wysłano kompletne archiwum do SharePoint."
    elif upload_errors:
        message += f" Upload SharePoint częściowo/całkowicie nieudany: {upload_error}"
    elif upload_error:
        message += f" Upload SharePoint nieudany: {upload_error}"
    return BackupRunResponse(
        accepted=True,
        dry_run=False,
        status=run_status,
        message=message,
        backup_name=run_result.backup_name,
        postgres_dump_included=run_result.postgres_dump_included,
        uploaded_to_cloud=uploaded,
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

    if not settings.backup_execution_active:
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


__all__ = ["backup_scheduler_tick", "router", "start_backup_scheduler", "stop_backup_scheduler"]
