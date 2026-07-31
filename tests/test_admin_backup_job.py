"""Izolowane testy orkiestracji zadania kopii zapasowej administratora."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.api.routes import admin_backup
from app.schemas.admin_backup import BackupConfigResponse
from app.services.backup_retention import RetentionApplyResult, RetentionPlan
from app.services.backup_runner import BackupRunResult
from app.services.firebird_backup import FirebirdBackupResult
from app.services.office365_backup import Office365UploadResult
from app.services.optima_backup import OptimaBackupResult, OptimaDatabaseBackup


def _config(local_directory: str, *, cloud_provider: str = "none") -> BackupConfigResponse:
    """Buduje pełną konfigurację przebiegu bez danych sekretnych."""
    return BackupConfigResponse(
        schedule_morning="06:00",
        schedule_evening="20:00",
        retention_local_copies=14,
        retention_cloud_copies=7,
        retention_local_days=21,
        retention_cloud_days=14,
        archive_ctip_files=True,
        archive_ctip_db=True,
        archive_firebird_prod=True,
        archive_firebird_test=False,
        archive_optima=True,
        storage_mode="local",
        local_directory=local_directory,
        cloud_provider=cloud_provider,
        cloud_only_evening=True,
        optima_only_evening=True,
        office_folder_ctip="BackupKP/CTIP",
        office_folder_firebird_prod="BackupKP/Menadzer_Serwisu/prod",
        office_folder_firebird_test="BackupKP/Menadzer_Serwisu/test",
        office_folder_optima="BackupKP/Optima",
        optima_auth_mode="windows",
        optima_db_it_partner="CDN_IT_Partner",
        optima_db_ksero_partner="CDN_Ksero_Partner1",
        optima_db_config="CDN_KNF_Ksero_Partner",
    )


def _empty_retention() -> tuple[RetentionPlan, RetentionApplyResult]:
    now = datetime.now(UTC)
    return (
        RetentionPlan(
            retention_days=14,
            cutoff_at=now,
            sets=[],
            deletion_sets=[],
            preserved_newest_key=None,
            unknown_items=[],
            newer_incomplete_sets=[],
        ),
        RetentionApplyResult(dry_run=False),
    )


def _component_results(root: Path) -> tuple[FirebirdBackupResult, OptimaBackupResult]:
    firebird_root = root / "firebird"
    firebird_root.mkdir(parents=True)
    firebird_paths = [
        firebird_root / "ctip_firebird_prod_20260713_060000.fbk",
        firebird_root / "ctip_firebird_prod_20260713_060000.fbk.sha256",
        firebird_root / "ctip_firebird_prod_20260713_060000_manifest.json",
    ]
    for path in firebird_paths:
        path.write_bytes(b"firebird")
    firebird = FirebirdBackupResult(
        backup_path=firebird_paths[0],
        checksum_path=firebird_paths[1],
        manifest_path=firebird_paths[2],
        checksum="firebird",
        size_bytes=8,
        source_path="BAZAMS.FDB",
        verified=True,
    )

    optima_root = root / "optima"
    optima_root.mkdir(parents=True)
    optima_items: list[OptimaDatabaseBackup] = []
    for database in ("CDN_IT_Partner", "CDN_Ksero_Partner1", "CDN_KNF_Ksero_Partner"):
        backup_path = optima_root / f"ctip_optima_20260713_200000_{database}.bak"
        checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")
        backup_path.write_bytes(b"optima")
        checksum_path.write_bytes(b"checksum")
        optima_items.append(
            OptimaDatabaseBackup(
                database_name=database,
                backup_path=backup_path,
                checksum_path=checksum_path,
                checksum=database,
                size_bytes=6,
            )
        )
    optima_manifest = optima_root / "ctip_optima_20260713_200000_manifest.json"
    optima_manifest.write_bytes(b"{}")
    optima = OptimaBackupResult(
        database_backups=optima_items,
        manifest_path=optima_manifest,
        restore_verified_database="CDN_IT_Partner",
        verified=True,
    )
    return firebird, optima


class AdminBackupJobTests(unittest.IsolatedAsyncioTestCase):
    """Weryfikuje różnicę slotu porannego, wieczornego i ręcznego."""

    async def test_morning_slot_skips_optima_without_partial_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            firebird, _ = _component_results(root)
            main_result = BackupRunResult(
                backup_name="backup_20260713_060000_auto_morning.tar.gz",
                backup_path=root / "backup.tar.gz",
                checksum="main",
                checksum_path=root / "backup.tar.gz.sha256",
                size_bytes=10,
                notes=[],
                included_components=["postgresql_ctip", "firebird_prod"],
                postgres_dump_included=True,
            )
            with (
                patch.object(admin_backup, "create_firebird_backup", return_value=firebird),
                patch.object(admin_backup, "create_optima_backup") as optima_mock,
                patch.object(admin_backup, "create_local_backup", return_value=main_result),
                patch.object(
                    admin_backup,
                    "run_local_retention",
                    return_value=_empty_retention(),
                ),
            ):
                outcome = await admin_backup._execute_backup_job_impl(
                    cfg=_config(temp_dir),
                    label="auto_morning",
                    compress=True,
                    cloud_upload_enabled=False,
                    slot="morning",
                )

        optima_mock.assert_not_called()
        self.assertEqual(outcome["status"], "SUCCESS")
        self.assertFalse(outcome["optima_backup_included"])
        self.assertTrue(outcome["firebird_backup_included"])

    async def test_manual_run_uploads_each_component_to_its_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            firebird, optima = _component_results(root)
            main_archive = root / "backup.tar.gz"
            main_checksum = root / "backup.tar.gz.sha256"
            main_archive.write_bytes(b"main")
            main_checksum.write_bytes(b"checksum")
            main_result = BackupRunResult(
                backup_name=main_archive.name,
                backup_path=main_archive,
                checksum="main",
                checksum_path=main_checksum,
                size_bytes=4,
                notes=[],
                included_components=["postgresql_ctip", "firebird_prod", "optima_sql"],
                postgres_dump_included=True,
            )

            async def fake_upload(**kwargs):
                file_path = kwargs["file_path"]
                return Office365UploadResult(
                    drive_id="drive",
                    item_id=file_path.name,
                    web_url=f"https://sharepoint.test/{file_path.name}",
                    name=file_path.name,
                    size=file_path.stat().st_size,
                )

            with (
                patch.object(admin_backup, "create_firebird_backup", return_value=firebird),
                patch.object(admin_backup, "create_optima_backup", return_value=optima),
                patch.object(admin_backup, "create_local_backup", return_value=main_result),
                patch.object(
                    admin_backup,
                    "run_local_retention",
                    return_value=_empty_retention(),
                ),
                patch.object(
                    admin_backup,
                    "upload_file_to_sharepoint",
                    new=AsyncMock(side_effect=fake_upload),
                ) as upload_mock,
                patch.object(
                    admin_backup,
                    "run_sharepoint_retention",
                    new=AsyncMock(return_value=_empty_retention()),
                ),
            ):
                outcome = await admin_backup._execute_backup_job_impl(
                    cfg=_config(temp_dir, cloud_provider="office365"),
                    label="manual",
                    compress=True,
                    cloud_upload_enabled=True,
                )

        folders = [call.kwargs["folder_path"] for call in upload_mock.await_args_list]
        self.assertEqual(len(folders), 12)
        self.assertEqual(
            set(folders),
            {"BackupKP/CTIP", "BackupKP/Menadzer_Serwisu/prod", "BackupKP/Optima"},
        )
        self.assertEqual(outcome["status"], "SUCCESS")
        self.assertTrue(outcome["main_uploaded"])
        self.assertTrue(outcome["firebird_uploaded_to_cloud"])
        self.assertTrue(outcome["optima_uploaded_to_cloud"])


if __name__ == "__main__":
    unittest.main()
