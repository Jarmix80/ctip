"""Testy zweryfikowanych komponentów kopii Firebird i SQL Optima."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services import firebird_backup, optima_backup


class FirebirdBackupTests(unittest.TestCase):
    """Weryfikuje tworzenie `.fbk` i przekazywanie sekretu przez środowisko."""

    def test_backup_is_restored_and_password_is_not_in_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "BAZAMS.FDB"
            source.write_bytes(b"firebird-source")
            output = root / "backup"
            commands: list[list[str]] = []
            environments: list[dict[str, str]] = []

            def fake_run(command: list[str], **kwargs):
                commands.append(command)
                environments.append(kwargs["env"])
                Path(command[-1]).write_bytes(b"verified-firebird")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with (
                patch.object(firebird_backup, "_resolve_gbak", return_value="gbak-test"),
                patch.object(firebird_backup.subprocess, "run", side_effect=fake_run),
                patch.object(settings, "fb_user", "SYSDBA"),
                patch.object(settings, "fb_password", "sekret-firebird"),
            ):
                result = firebird_backup.create_firebird_backup(
                    source_path=source,
                    output_directory=output,
                    now=datetime(2026, 7, 13, 6, 0, tzinfo=UTC),
                )

            self.assertTrue(result.verified)
            self.assertTrue(all(path.is_file() for path in result.files))
            self.assertEqual(len(commands), 2)
            self.assertNotIn("sekret-firebird", " ".join(part for cmd in commands for part in cmd))
            self.assertTrue(all(env["ISC_PASSWORD"] == "sekret-firebird" for env in environments))
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["verification"], "gbak_restore_success")


class OptimaBackupTests(unittest.TestCase):
    """Weryfikuje opcje SQL, komplet zestawu oraz kontrolowane odtworzenie."""

    def test_sql_password_is_passed_only_in_environment(self) -> None:
        with (
            patch.object(optima_backup, "_resolve_sqlcmd", return_value="sqlcmd-test"),
            patch.object(optima_backup, "_server_target", return_value="SERWER1\\OPTIMA"),
            patch.object(settings, "optima_sql_auth_mode", "sql"),
            patch.object(settings, "optima_sql_login", "backup_user"),
            patch.object(settings, "optima_sql_password", "sekret-sql"),
        ):
            command, environment = optima_backup._sqlcmd_context()

        self.assertIn("-U", command)
        self.assertNotIn("sekret-sql", command)
        self.assertEqual(environment["SQLCMDPASSWORD"], "sekret-sql")

    def test_backup_query_uses_copy_only_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_path = Path(temp_dir) / "database.bak"
            queries: list[str] = []

            def fake_sqlcmd(query: str, **_kwargs):
                queries.append(query)
                backup_path.write_bytes(b"sql-backup")
                return subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with patch.object(optima_backup, "_run_sqlcmd", side_effect=fake_sqlcmd):
                optima_backup._backup_database("CDN_IT_Partner", backup_path)

            self.assertIn("COPY_ONLY", queries[0])
            self.assertIn("CHECKSUM", queries[0])
            self.assertIn("RESTORE VERIFYONLY", queries[0])
            self.assertNotIn("COMPRESSION", queries[0])

    def test_complete_run_creates_three_backups_checksums_and_manifest(self) -> None:
        databases = ["CDN_IT_Partner", "CDN_Ksero_Partner1", "CDN_KNF_Ksero_Partner"]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "optima"

            def fake_backup(database_name: str, backup_path: Path) -> None:
                backup_path.write_bytes(f"backup-{database_name}".encode())

            with (
                patch.object(optima_backup, "_backup_database", side_effect=fake_backup),
                patch.object(optima_backup, "_verify_database_restore"),
                patch.object(optima_backup, "_server_target", return_value="SERWER1\\OPTIMA"),
            ):
                result = optima_backup.create_optima_backup(
                    database_names=databases,
                    output_directory=output,
                    restore_test_database="CDN_IT_Partner",
                    now=datetime(2026, 7, 13, 20, 0, tzinfo=UTC),
                )

            self.assertEqual(result.database_names, databases)
            self.assertEqual(len(result.files), 7)
            self.assertTrue(all(path.is_file() for path in result.files))
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["verification"]["dbcc_checkdb"], "PHYSICAL_ONLY")
            self.assertEqual(len(manifest["databases"]), 3)

    def test_restore_verification_runs_checkdb_and_removes_temporary_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backup_path = root / "database.bak"
            backup_path.write_bytes(b"backup")
            queries: list[str] = []

            def fake_sqlcmd(query: str, **_kwargs):
                queries.append(query)
                return subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with (
                patch.object(
                    optima_backup,
                    "_read_backup_logical_files",
                    return_value=[("Data", "D"), ("Log", "L")],
                ),
                patch.object(optima_backup, "_run_sqlcmd", side_effect=fake_sqlcmd),
            ):
                optima_backup._verify_database_restore(
                    database_name="CDN_IT_Partner",
                    backup_path=backup_path,
                    verify_directory=root,
                )

            self.assertEqual(len(queries), 2)
            self.assertIn("DBCC CHECKDB", queries[0])
            self.assertIn("PHYSICAL_ONLY", queries[0])
            self.assertIn("DROP DATABASE", queries[1])


if __name__ == "__main__":
    unittest.main()
