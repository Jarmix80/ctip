"""Operacje związane z kopiami zapasowymi w panelu administratora."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
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
    BackupRetentionRunRequest,
    BackupRetentionRunResponse,
    BackupRetentionScopeResult,
    BackupRunRequest,
    BackupRunResponse,
)
from app.services.audit import record_audit
from app.services.backup_artifacts import remove_files
from app.services.backup_retention import RetentionApplyResult, RetentionPlan, run_local_retention
from app.services.backup_runner import (
    BACKUP_DIR,
    BackupRunError,
    BackupRunResult,
    create_local_backup,
    list_backup_files,
)
from app.services.firebird_backup import FirebirdBackupResult, create_firebird_backup
from app.services.office365_backup import (
    Office365BackupError,
    run_sharepoint_retention,
    test_office365_connection,
    upload_file_to_sharepoint,
)
from app.services.optima_backup import OptimaBackupResult, create_optima_backup

router = APIRouter(prefix="/admin/backup", tags=["admin-backup"])
logger = logging.getLogger(__name__)
_scheduler_task: asyncio.Task[None] | None = None
_scheduler_stop_event: asyncio.Event | None = None
_scheduler_last_run: dict[str, str] = {}
_backup_job_lock = asyncio.Lock()
BACKUP_ENV_LOCK_REASON = (
    "Dane połączeniowe i sekretne modułu backupów są zarządzane wyłącznie z pliku .env."
)
RETENTION_CONFIRMATION = "USUŃ STARE KOPIE"


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


def _component_directories(cfg: BackupConfigResponse) -> dict[str, Path]:
    """Wyznacza lokalne katalogi samodzielnych artefaktów Firebird i Optima."""
    root = Path(cfg.local_directory)
    return {
        "firebird_prod": root / "Menadzer_Serwisu" / "prod",
        "firebird_test": root / "Menadzer_Serwisu" / "test",
        "optima": root / "Optima",
    }


def _configured_optima_databases(cfg: BackupConfigResponse) -> list[str]:
    """Zwraca trzy wymagane bazy Optimy albo zgłasza niepełną konfigurację."""
    configured = [cfg.optima_db_it_partner, cfg.optima_db_ksero_partner, cfg.optima_db_config]
    if any(not name or not name.strip() for name in configured):
        raise BackupRunError("Skonfiguruj w .env wszystkie trzy bazy SQL Optimy.")
    return [str(name).strip() for name in configured]


async def _upload_artifact_group(
    *,
    cfg: BackupConfigResponse,
    folder_path: str,
    files: list[Path],
) -> tuple[str, list[str]]:
    """Wysyła kompletną grupę artefaktów i zwraca Drive ID oraz adresy plików."""
    normalized_folder = folder_path.strip().strip("/")
    if not normalized_folder:
        raise Office365BackupError("Brak folderu docelowego Office 365.")
    drive_id = cfg.office_drive_id
    urls: list[str] = []
    for file_path in files:
        upload = await upload_file_to_sharepoint(
            tenant_id=cfg.office_tenant_id or "",
            client_id=cfg.office_client_id or "",
            client_secret=settings.office365_client_secret or "",
            site_id=cfg.office_site_id,
            drive_id=drive_id,
            folder_path=normalized_folder,
            file_path=file_path,
        )
        drive_id = upload.drive_id
        if upload.web_url:
            urls.append(upload.web_url)
    if not drive_id:
        raise Office365BackupError("Microsoft Graph nie zwrócił Drive ID po uploadzie.")
    return drive_id, urls


async def _execute_backup_job_impl(
    *,
    cfg: BackupConfigResponse,
    label: str | None,
    compress: bool,
    cloud_upload_enabled: bool,
    slot: str | None = None,
) -> dict[str, object]:
    """Wykonuje zweryfikowane kopie komponentów, upload i retencję czasową."""
    directories = _component_directories(cfg)
    generated_at = datetime.now(UTC)
    firebird_prod_result: FirebirdBackupResult | None = None
    firebird_test_result: FirebirdBackupResult | None = None
    optima_result: OptimaBackupResult | None = None
    created_component_files: list[Path] = []
    include_optima = cfg.archive_optima and not (cfg.optima_only_evening and slot == "morning")

    try:
        if cfg.archive_firebird_prod:
            firebird_prod_result = await asyncio.to_thread(
                create_firebird_backup,
                source_path=settings.fb_database,
                output_directory=directories["firebird_prod"],
                scope="prod",
                now=generated_at,
            )
            created_component_files.extend(firebird_prod_result.files)

        if cfg.archive_firebird_test:
            firebird_test_result = await asyncio.to_thread(
                create_firebird_backup,
                source_path=settings.fb_local_copy_path,
                output_directory=directories["firebird_test"],
                scope="test",
                now=generated_at,
            )
            created_component_files.extend(firebird_test_result.files)

        if include_optima:
            optima_result = await asyncio.to_thread(
                create_optima_backup,
                database_names=_configured_optima_databases(cfg),
                output_directory=directories["optima"],
                restore_test_database=cfg.optima_db_it_partner,
                now=generated_at,
            )
            created_component_files.extend(optima_result.files)

        archive_config = cfg.model_dump()
        archive_config["archive_optima"] = include_optima
        component_manifests: dict[str, list[Path]] = {}
        if firebird_prod_result:
            component_manifests["firebird_prod"] = [firebird_prod_result.manifest_path]
        if firebird_test_result:
            component_manifests["firebird_test"] = [firebird_test_result.manifest_path]
        if optima_result:
            component_manifests["optima_sql"] = [optima_result.manifest_path]
        run_result: BackupRunResult = await asyncio.to_thread(
            create_local_backup,
            label=label,
            compress=compress,
            config=archive_config,
            component_manifests=component_manifests,
        )
    except Exception:
        await asyncio.to_thread(remove_files, created_component_files)
        raise

    uploaded_folders: list[str] = []
    upload_errors: list[str] = []
    upload_urls: list[str] = []
    local_deleted = 0
    cloud_deleted = 0
    main_uploaded = False
    firebird_prod_uploaded = False
    firebird_test_uploaded = False
    optima_uploaded = False

    local_scopes = {
        "ctip": BACKUP_DIR,
        "firebird_prod": directories["firebird_prod"],
        "firebird_test": directories["firebird_test"],
        "optima": directories["optima"],
    }
    for scope_name, directory in local_scopes.items():
        try:
            _, apply_result = await asyncio.to_thread(
                run_local_retention,
                directory,
                retention_days=cfg.retention_local_days,
                dry_run=False,
            )
            local_deleted += apply_result.deleted_files
            upload_errors.extend(
                f"retencja lokalna {scope_name}: {error}" for error in apply_result.errors
            )
        except (OSError, ValueError) as exc:
            upload_errors.append(f"retencja lokalna {scope_name}: {exc}")

    if cloud_upload_enabled and cfg.cloud_provider == "office365":
        cloud_groups: list[tuple[str, str, list[Path]]] = []
        cloud_targets = _resolve_cloud_targets(cfg)
        if cloud_targets:
            cloud_groups.append(
                ("ctip", cloud_targets[0], [run_result.backup_path, run_result.checksum_path])
            )
        else:
            upload_errors.append("CTIP: brak folderu docelowego Office 365.")
        if firebird_prod_result:
            cloud_groups.append(
                ("firebird_prod", cfg.office_folder_firebird_prod, firebird_prod_result.files)
            )
        if firebird_test_result:
            cloud_groups.append(
                ("firebird_test", cfg.office_folder_firebird_test, firebird_test_result.files)
            )
        if optima_result:
            cloud_groups.append(("optima", cfg.office_folder_optima, optima_result.files))

        for scope_name, folder_path, files in cloud_groups:
            try:
                resolved_drive_id, group_urls = await _upload_artifact_group(
                    cfg=cfg,
                    folder_path=folder_path,
                    files=files,
                )
                uploaded_folders.append(folder_path)
                upload_urls.extend(group_urls)
                if scope_name == "ctip":
                    main_uploaded = True
                elif scope_name == "firebird_prod":
                    firebird_prod_uploaded = True
                elif scope_name == "firebird_test":
                    firebird_test_uploaded = True
                elif scope_name == "optima":
                    optima_uploaded = True
            except (Office365BackupError, httpx.HTTPError) as exc:
                upload_errors.append(f"{scope_name} ({folder_path}): {exc}")
                continue

            try:
                _, apply_result = await run_sharepoint_retention(
                    tenant_id=cfg.office_tenant_id or "",
                    client_id=cfg.office_client_id or "",
                    client_secret=settings.office365_client_secret or "",
                    site_id=cfg.office_site_id,
                    drive_id=resolved_drive_id,
                    folder_path=folder_path,
                    retention_days=cfg.retention_cloud_days,
                    dry_run=False,
                )
                cloud_deleted += apply_result.deleted_files
                upload_errors.extend(
                    f"retencja Office 365 {scope_name}: {error}" for error in apply_result.errors
                )
            except (Office365BackupError, httpx.HTTPError) as exc:
                upload_errors.append(f"{scope_name} ({folder_path}), retencja: {exc}")

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
        "main_uploaded": main_uploaded,
        "firebird_backup_included": firebird_prod_result is not None,
        "firebird_uploaded_to_cloud": firebird_prod_uploaded,
        "firebird_test_uploaded_to_cloud": firebird_test_uploaded,
        "optima_backup_included": optima_result is not None,
        "optima_uploaded_to_cloud": optima_uploaded,
        "optima_databases": optima_result.database_names if optima_result else [],
    }


async def _execute_backup_job(
    *,
    cfg: BackupConfigResponse,
    label: str | None,
    compress: bool,
    cloud_upload_enabled: bool,
    slot: str | None = None,
) -> dict[str, object]:
    """Uruchamia pojedynczy przebieg i blokuje nakładanie ciężkich zadań backupu."""
    if _backup_job_lock.locked():
        raise BackupRunError("Inny przebieg kopii zapasowej jest już wykonywany.")
    async with _backup_job_lock:
        return await _execute_backup_job_impl(
            cfg=cfg,
            label=label,
            compress=compress,
            cloud_upload_enabled=cloud_upload_enabled,
            slot=slot,
        )


def _retention_scope_response(
    *,
    scope: str,
    location: str,
    plan: RetentionPlan,
    apply_result: RetentionApplyResult,
) -> BackupRetentionScopeResult:
    """Buduje odpowiedź API dla poprawnie przeanalizowanego zakresu retencji."""
    plan_data = plan.as_dict()
    return BackupRetentionScopeResult(
        scope=scope,
        location=location,
        retention_days=plan.retention_days,
        dry_run=apply_result.dry_run,
        managed_sets=len(plan.sets),
        managed_files=plan.managed_files,
        candidate_sets=len(plan.deletion_sets),
        candidate_files=plan.deletion_files,
        candidate_bytes=plan.deletion_bytes,
        deleted_sets=apply_result.deleted_sets,
        deleted_files=apply_result.deleted_files,
        deleted_bytes=apply_result.deleted_bytes,
        preserved_newest_key=plan.preserved_newest_key,
        unknown_files=list(plan_data["unknown_files"]),
        newer_incomplete_sets=list(plan_data["newer_incomplete_sets"]),
        deletion_sets=list(plan_data["deletion_sets"]),
        errors=apply_result.errors,
    )


def _retention_error_response(
    *,
    scope: str,
    location: str,
    retention_days: int,
    dry_run: bool,
    error: Exception | str,
) -> BackupRetentionScopeResult:
    """Buduje odpowiedź zakresu, którego nie udało się przeanalizować."""
    return BackupRetentionScopeResult(
        scope=scope,
        location=location,
        retention_days=retention_days,
        dry_run=dry_run,
        errors=[str(error)],
    )


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
        retention_local_days=_to_int(stored.get("retention_local_days"), 21),
        retention_cloud_days=_to_int(stored.get("retention_cloud_days"), 14),
        archive_ctip_files=_to_bool(stored.get("archive_ctip_files"), True),
        archive_ctip_db=_to_bool(stored.get("archive_ctip_db"), True),
        archive_firebird_prod=_to_bool(stored.get("archive_firebird_prod"), True),
        archive_firebird_test=_to_bool(stored.get("archive_firebird_test"), False),
        archive_optima=_to_bool(stored.get("archive_optima"), True),
        storage_mode=stored.get("storage_mode") or "local",
        local_directory=settings.backup_default_local_dir,
        network_directory=stored.get("network_directory"),
        cloud_provider=stored.get("cloud_provider") or "office365",
        cloud_only_evening=_to_bool(stored.get("cloud_only_evening"), True),
        optima_only_evening=_to_bool(stored.get("optima_only_evening"), True),
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
                slot=slot,
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
                    "firebird_backup_included": outcome["firebird_backup_included"],
                    "firebird_uploaded_to_cloud": outcome["firebird_uploaded_to_cloud"],
                    "optima_backup_included": outcome["optima_backup_included"],
                    "optima_uploaded_to_cloud": outcome["optima_uploaded_to_cloud"],
                    "optima_databases": outcome["optima_databases"],
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
                "wysyłki/Optimy tylko wieczorem."
            ),
        )

    values = {
        "schedule_morning": StoredValue(payload.schedule_morning, False),
        "schedule_evening": StoredValue(payload.schedule_evening, False),
        "retention_local_copies": StoredValue(str(payload.retention_local_copies), False),
        "retention_cloud_copies": StoredValue(str(payload.retention_cloud_copies), False),
        "retention_local_days": StoredValue(str(payload.retention_local_days), False),
        "retention_cloud_days": StoredValue(str(payload.retention_cloud_days), False),
        "archive_ctip_files": StoredValue(str(payload.archive_ctip_files).lower(), False),
        "archive_ctip_db": StoredValue(str(payload.archive_ctip_db).lower(), False),
        "archive_firebird_prod": StoredValue(str(payload.archive_firebird_prod).lower(), False),
        "archive_firebird_test": StoredValue(str(payload.archive_firebird_test).lower(), False),
        "archive_optima": StoredValue(str(payload.archive_optima).lower(), False),
        "storage_mode": StoredValue(payload.storage_mode, False),
        "network_directory": StoredValue(payload.network_directory or "", False),
        "cloud_provider": StoredValue(payload.cloud_provider, False),
        "cloud_only_evening": StoredValue(str(payload.cloud_only_evening).lower(), False),
        "optima_only_evening": StoredValue(str(payload.optima_only_evening).lower(), False),
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
            "retention_local_days": payload.retention_local_days,
            "retention_cloud_days": payload.retention_cloud_days,
            "archive_ctip_files": payload.archive_ctip_files,
            "archive_ctip_db": payload.archive_ctip_db,
            "archive_firebird_prod": payload.archive_firebird_prod,
            "archive_firebird_test": payload.archive_firebird_test,
            "archive_optima": payload.archive_optima,
            "storage_mode": payload.storage_mode,
            "network_directory": payload.network_directory,
            "cloud_provider": payload.cloud_provider,
            "cloud_only_evening": payload.cloud_only_evening,
            "optima_only_evening": payload.optima_only_evening,
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
    except (Office365BackupError, httpx.HTTPError) as exc:
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


@router.post(
    "/retention/run",
    response_model=BackupRetentionRunResponse,
    summary="Podgląd albo wykonanie retencji kopii zapasowych",
)
async def backup_run_retention(
    payload: BackupRetentionRunRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> BackupRetentionRunResponse:
    """Analizuje wszystkie katalogi, a po potwierdzeniu usuwa wyłącznie zarządzane zestawy."""
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    if not payload.dry_run and payload.confirm != RETENTION_CONFIRMATION:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="backup_retention_blocked_confirmation",
            client_ip=admin_session.client_ip,
            payload={"dry_run": payload.dry_run},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Wymagane potwierdzenie: {RETENTION_CONFIRMATION}",
        )
    if not payload.dry_run and not settings.backup_execution_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Wykonanie retencji jest wyłączone poza środowiskiem produkcyjnym.",
        )
    if not payload.dry_run and _backup_job_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nie można wykonać retencji podczas aktywnego zadania backupu.",
        )

    cfg = await load_backup_config(session)
    generated_at = datetime.now(UTC)
    scopes: list[BackupRetentionScopeResult] = []
    directories = _component_directories(cfg)
    local_scopes = {
        "local_ctip": BACKUP_DIR,
        "local_firebird_prod": directories["firebird_prod"],
        "local_firebird_test": directories["firebird_test"],
        "local_optima": directories["optima"],
    }
    for scope_name, directory in local_scopes.items():
        try:
            plan, apply_result = await asyncio.to_thread(
                run_local_retention,
                directory,
                retention_days=cfg.retention_local_days,
                dry_run=payload.dry_run,
                now=generated_at,
            )
            scopes.append(
                _retention_scope_response(
                    scope=scope_name,
                    location=str(directory),
                    plan=plan,
                    apply_result=apply_result,
                )
            )
        except (OSError, ValueError) as exc:
            scopes.append(
                _retention_error_response(
                    scope=scope_name,
                    location=str(directory),
                    retention_days=cfg.retention_local_days,
                    dry_run=payload.dry_run,
                    error=exc,
                )
            )

    if cfg.cloud_provider == "office365":
        cloud_scopes = {
            "office365_ctip": cfg.office_folder_ctip or cfg.office_folder_path or "",
            "office365_firebird_prod": cfg.office_folder_firebird_prod,
            "office365_firebird_test": cfg.office_folder_firebird_test,
            "office365_optima": cfg.office_folder_optima,
        }
        for scope_name, folder_path in cloud_scopes.items():
            try:
                plan, apply_result = await run_sharepoint_retention(
                    tenant_id=cfg.office_tenant_id or "",
                    client_id=cfg.office_client_id or "",
                    client_secret=settings.office365_client_secret or "",
                    site_id=cfg.office_site_id,
                    drive_id=cfg.office_drive_id,
                    folder_path=folder_path,
                    retention_days=cfg.retention_cloud_days,
                    dry_run=payload.dry_run,
                    now=generated_at,
                )
                scopes.append(
                    _retention_scope_response(
                        scope=scope_name,
                        location=folder_path,
                        plan=plan,
                        apply_result=apply_result,
                    )
                )
            except (Office365BackupError, ValueError, httpx.HTTPError) as exc:
                scopes.append(
                    _retention_error_response(
                        scope=scope_name,
                        location=folder_path,
                        retention_days=cfg.retention_cloud_days,
                        dry_run=payload.dry_run,
                        error=exc,
                    )
                )

    response = BackupRetentionRunResponse(
        accepted=True,
        dry_run=payload.dry_run,
        message=(
            "Podgląd retencji został przygotowany bez usuwania plików."
            if payload.dry_run
            else "Retencja została wykonana; szczegóły zapisano w audycie."
        ),
        generated_at=generated_at,
        scopes=scopes,
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="backup_retention_dry_run" if payload.dry_run else "backup_retention_apply",
        client_ip=admin_session.client_ip,
        payload=response.model_dump(mode="json"),
    )
    await session.commit()
    return response


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

    uploaded = bool(outcome["main_uploaded"])
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
            "firebird_backup_included": outcome["firebird_backup_included"],
            "firebird_uploaded_to_cloud": outcome["firebird_uploaded_to_cloud"],
            "optima_backup_included": outcome["optima_backup_included"],
            "optima_uploaded_to_cloud": outcome["optima_uploaded_to_cloud"],
            "optima_databases": outcome["optima_databases"],
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
    if uploaded:
        message += " Wysłano archiwum CTIP do SharePoint."
    if outcome["firebird_uploaded_to_cloud"]:
        message += " Wysłano kopię Firebird."
    if outcome["optima_uploaded_to_cloud"]:
        message += " Wysłano kopie Optimy."
    if upload_errors:
        message += f" Wystąpiły błędy uploadu lub retencji: {upload_error}"
    return BackupRunResponse(
        accepted=True,
        dry_run=False,
        status=run_status,
        message=message,
        backup_name=run_result.backup_name,
        postgres_dump_included=run_result.postgres_dump_included,
        uploaded_to_cloud=uploaded,
        firebird_backup_included=bool(outcome["firebird_backup_included"]),
        firebird_uploaded_to_cloud=bool(outcome["firebird_uploaded_to_cloud"]),
        optima_backup_included=bool(outcome["optima_backup_included"]),
        optima_uploaded_to_cloud=bool(outcome["optima_uploaded_to_cloud"]),
        optima_databases=list(outcome["optima_databases"]),
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
