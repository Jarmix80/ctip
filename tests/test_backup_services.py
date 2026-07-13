"""Testy usług tworzenia i wysyłania kopii zapasowych."""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services import backup_runner
from app.services.office365_backup import (
    UPLOAD_CHUNK_SIZE_BYTES,
    Office365BackupError,
    run_sharepoint_retention,
    upload_file_to_sharepoint,
)


class BackupRunnerTests(unittest.TestCase):
    """Weryfikuje kompletność lokalnego archiwum PostgreSQL."""

    def test_backup_contains_validated_postgres_dump(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            backup_dir = Path(temp_dir) / "backups"
            (root / "app").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "app" / "main.py").write_text("app = True\n", encoding="utf-8")
            (root / "scripts" / "task.py").write_text("pass\n", encoding="utf-8")
            (root / "README.md").write_text("# Test\n", encoding="utf-8")

            def fake_dump(target_path: Path) -> None:
                target_path.write_bytes(b"PGDMP\x01\x0ftest")

            config = {
                "archive_ctip_files": True,
                "archive_ctip_db": True,
                "archive_firebird_prod": False,
                "archive_firebird_test": False,
                "archive_optima": False,
            }
            with (
                patch.object(backup_runner, "BACKUP_DIR", backup_dir),
                patch.object(backup_runner, "_create_postgres_dump", side_effect=fake_dump),
            ):
                result = backup_runner.create_local_backup(
                    label="test",
                    compress=True,
                    config=config,
                    project_root=root,
                )

            self.assertTrue(result.postgres_dump_included)
            self.assertIn("postgresql_ctip", result.included_components)
            self.assertTrue(result.checksum_path.is_file())
            with tarfile.open(result.backup_path, "r:gz") as archive:
                self.assertIn("postgresql/ctip.dump", archive.getnames())
                manifest_file = archive.extractfile("manifest.json")
                self.assertIsNotNone(manifest_file)
                manifest = json.loads(manifest_file.read().decode("utf-8"))
            self.assertIn("postgresql_ctip", manifest["included_components"])
            self.assertEqual(manifest["omitted_components"], [])

    def test_pg_dump_password_is_not_exposed_in_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "ctip.dump"
            commands: list[list[str]] = []

            def fake_run(command, **_kwargs):
                commands.append(command)
                if command[0] == "pg_dump-test":
                    output_path = Path(command[command.index("--file") + 1])
                    output_path.write_bytes(b"PGDMP-test")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="; Archive created by pg_dump\n1; 0 0 TABLE test\n",
                    stderr="",
                )

            with (
                patch.object(
                    backup_runner,
                    "_resolve_postgres_tool",
                    side_effect=["pg_dump-test", "pg_restore-test"],
                ),
                patch.object(backup_runner.subprocess, "run", side_effect=fake_run),
                patch.object(settings, "pg_password", "bardzo-tajny-sekret"),
                patch.object(settings, "pg_dump_path", None),
                patch.object(settings, "pg_restore_path", None),
            ):
                backup_runner._create_postgres_dump(target_path)

            self.assertEqual(len(commands), 2)
            self.assertNotIn("bardzo-tajny-sekret", " ".join(commands[0]))
            self.assertEqual(commands[1][1], "--list")

    def test_main_archive_contains_only_manifests_of_external_components(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            backup_dir = Path(temp_dir) / "backups"
            root.mkdir()
            firebird_manifest = Path(temp_dir) / "ctip_firebird_prod_manifest.json"
            optima_manifest = Path(temp_dir) / "ctip_optima_manifest.json"
            firebird_manifest.write_text('{"type":"firebird"}', encoding="utf-8")
            optima_manifest.write_text('{"type":"optima"}', encoding="utf-8")
            config = {
                "archive_ctip_files": False,
                "archive_ctip_db": False,
                "archive_firebird_prod": True,
                "archive_firebird_test": False,
                "archive_optima": True,
            }

            with patch.object(backup_runner, "BACKUP_DIR", backup_dir):
                result = backup_runner.create_local_backup(
                    label="external",
                    compress=True,
                    config=config,
                    project_root=root,
                    component_manifests={
                        "firebird_prod": [firebird_manifest],
                        "optima_sql": [optima_manifest],
                    },
                )

            with tarfile.open(result.backup_path, "r:gz") as archive:
                names = archive.getnames()
                manifest_file = archive.extractfile("manifest.json")
                self.assertIsNotNone(manifest_file)
                manifest = json.loads(manifest_file.read().decode("utf-8"))
            self.assertIn(
                f"external/firebird/prod/{firebird_manifest.name}",
                names,
            )
            self.assertIn(f"external/optima/{optima_manifest.name}", names)
            self.assertFalse(any(name.lower().endswith((".fdb", ".fbk", ".bak")) for name in names))
            self.assertIn("firebird_prod", manifest["included_components"])
            self.assertIn("optima_sql", manifest["included_components"])

    def test_local_retention_removes_oldest_archive_and_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_dir = Path(temp_dir)
            archives: list[Path] = []
            for index in range(3):
                archive = backup_dir / f"backup_2026010{index + 1}_010101.tar.gz"
                archive.write_bytes(b"test")
                archive.with_name(f"{archive.name}.sha256").write_text(
                    "checksum\n",
                    encoding="utf-8",
                )
                os.utime(archive, (index + 1, index + 1))
                archives.append(archive)

            with patch.object(backup_runner, "BACKUP_DIR", backup_dir):
                deleted = backup_runner.prune_local_backups(2)

            self.assertEqual(deleted, 1)
            self.assertFalse(archives[0].exists())
            self.assertFalse(archives[0].with_name(f"{archives[0].name}.sha256").exists())
            self.assertTrue(archives[1].exists())
            self.assertTrue(archives[2].exists())

    def test_windows_predeploy_script_does_not_use_automatic_args_or_password_argument(self):
        script_path = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "windows"
            / "backup_prod_databases.ps1"
        )
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("[string[]]$ArgumentList", script)
        self.assertNotIn("[string[]]$Args", script)
        self.assertIn("$env:ISC_PASSWORD = $env:FB_PASSWORD", script)
        self.assertNotIn('"-password", $env:FB_PASSWORD', script)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class _FakeGraphClient:
    def __init__(self, expected_size: int):
        self.expected_size = expected_size
        self.chunk_sizes: list[int] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def post(self, url: str, **_kwargs):
        if "login.microsoftonline.com" in url:
            return _FakeResponse(200, {"access_token": "token"})
        return _FakeResponse(200, {"uploadUrl": "https://upload.test/session"})

    async def put(self, url: str, *, headers: dict, content: bytes):
        if url != "https://upload.test/session":
            return _FakeResponse(500, text="Nieoczekiwany adres")
        self.chunk_sizes.append(len(content))
        range_end = int(headers["Content-Range"].split("/")[0].split("-")[1])
        if range_end + 1 == self.expected_size:
            return _FakeResponse(
                201,
                {
                    "id": "item-id",
                    "name": "backup.tar.gz",
                    "size": self.expected_size,
                },
            )
        return _FakeResponse(202, {"nextExpectedRanges": [f"{range_end + 1}-"]})


class _FakeRetentionGraphClient:
    def __init__(self, items: list[dict[str, object]]):
        self.items = items
        self.deleted_ids: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def post(self, url: str, **_kwargs):
        if "login.microsoftonline.com" in url:
            return _FakeResponse(200, {"access_token": "token"})
        return _FakeResponse(500, text="Nieoczekiwany adres")

    async def get(self, _url: str, **_kwargs):
        return _FakeResponse(200, {"value": self.items})

    async def delete(self, url: str, **_kwargs):
        self.deleted_ids.append(url.rsplit("/", 1)[-1])
        return _FakeResponse(204)


class Office365UploadTests(unittest.IsolatedAsyncioTestCase):
    """Weryfikuje fragmentowy upload dużego archiwum."""

    async def test_large_file_uses_upload_session_and_validates_size(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "backup.tar.gz"
            file_size = UPLOAD_CHUNK_SIZE_BYTES + 17
            with file_path.open("wb") as stream:
                stream.seek(file_size - 1)
                stream.write(b"x")
            fake_client = _FakeGraphClient(file_size)

            with patch(
                "app.services.office365_backup.httpx.AsyncClient",
                return_value=fake_client,
            ):
                result = await upload_file_to_sharepoint(
                    tenant_id="tenant",
                    client_id="client",
                    client_secret="secret",
                    site_id=None,
                    drive_id="drive",
                    folder_path="BackupKP/CTIP",
                    file_path=file_path,
                )

            self.assertEqual(result.size, file_size)
            self.assertEqual(fake_client.chunk_sizes, [UPLOAD_CHUNK_SIZE_BYTES, 17])
            self.assertEqual(UPLOAD_CHUNK_SIZE_BYTES % (320 * 1024), 0)

    async def test_upload_rejects_missing_file(self):
        with self.assertRaises(Office365BackupError):
            await upload_file_to_sharepoint(
                tenant_id="tenant",
                client_id="client",
                client_secret="secret",
                site_id=None,
                drive_id="drive",
                folder_path="BackupKP/CTIP",
                file_path=Path("missing-backup.tar.gz"),
            )

    async def test_time_retention_deletes_old_pair_and_keeps_unknown_file(self):
        now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        old_date = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        new_date = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        old_archive = "backup_20260601_010101.tar.gz"
        new_archive = "backup_20260712_010101.tar.gz"
        items = [
            {"id": "old-a", "name": old_archive, "size": 100, "lastModifiedDateTime": old_date},
            {
                "id": "old-s",
                "name": f"{old_archive}.sha256",
                "size": 10,
                "lastModifiedDateTime": old_date,
            },
            {"id": "new-a", "name": new_archive, "size": 100, "lastModifiedDateTime": new_date},
            {
                "id": "new-s",
                "name": f"{new_archive}.sha256",
                "size": 10,
                "lastModifiedDateTime": new_date,
            },
            {"id": "note", "name": "notatka.txt", "size": 5, "lastModifiedDateTime": old_date},
        ]
        fake_client = _FakeRetentionGraphClient(items)

        with patch(
            "app.services.office365_backup.httpx.AsyncClient",
            return_value=fake_client,
        ):
            plan, result = await run_sharepoint_retention(
                tenant_id="tenant",
                client_id="client",
                client_secret="secret",
                site_id=None,
                drive_id="drive",
                folder_path="BackupKP/CTIP",
                retention_days=14,
                dry_run=False,
                now=now,
            )

        self.assertEqual(plan.deletion_files, 2)
        self.assertEqual(result.deleted_files, 2)
        self.assertEqual(set(fake_client.deleted_ids), {"old-a", "old-s"})
        self.assertEqual([item.name for item in plan.unknown_items], ["notatka.txt"])


if __name__ == "__main__":
    unittest.main()
