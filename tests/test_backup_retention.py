"""Testy wspólnego mechanizmu czasowej retencji kopii zapasowych."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.backup_retention import RetentionItem, build_retention_plan, run_local_retention


class BackupRetentionTests(unittest.TestCase):
    """Weryfikuje grupowanie, próg czasowy i ochronę najnowszego zestawu."""

    def setUp(self) -> None:
        self.now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

    def _item(self, name: str, age_days: int, size_bytes: int = 10) -> RetentionItem:
        return RetentionItem(
            name=name,
            modified_at=self.now - timedelta(days=age_days),
            size_bytes=size_bytes,
        )

    def test_plan_removes_only_old_managed_sets(self) -> None:
        old_archive = "backup_20260601_010101.tar.gz"
        new_archive = "backup_20260712_010101_auto_evening.tar.gz"
        items = [
            self._item(old_archive, 30),
            self._item(f"{old_archive}.sha256", 30),
            self._item(new_archive, 1),
            self._item(f"{new_archive}.sha256", 1),
            self._item("notatka_operatora.txt", 60),
        ]

        plan = build_retention_plan(items, retention_days=14, now=self.now)

        self.assertEqual([item.key for item in plan.deletion_sets], [f"ctip:{old_archive}"])
        self.assertEqual(plan.deletion_files, 2)
        self.assertEqual([item.name for item in plan.unknown_items], ["notatka_operatora.txt"])

    def test_plan_preserves_newest_complete_set_even_when_all_are_old(self) -> None:
        older = "backup_20260501_010101.tar.gz"
        newest = "backup_20260502_010101.tar.gz"
        items = [
            self._item(older, 40),
            self._item(f"{older}.sha256", 40),
            self._item(newest, 39),
            self._item(f"{newest}.sha256", 39),
        ]

        plan = build_retention_plan(items, retention_days=14, now=self.now)

        self.assertEqual(plan.preserved_newest_key, f"ctip:{newest}")
        self.assertEqual([item.key for item in plan.deletion_sets], [f"ctip:{older}"])

    def test_plan_reports_new_incomplete_and_deletes_old_orphan(self) -> None:
        old_orphan = "backup_20260501_010101.tar.gz.sha256"
        new_orphan = "backup_20260712_010101.tar.gz.sha256"

        plan = build_retention_plan(
            [self._item(old_orphan, 40), self._item(new_orphan, 1)],
            retention_days=14,
            now=self.now,
        )

        self.assertEqual(plan.deletion_files, 1)
        self.assertEqual(plan.deletion_sets[0].items[0].item.name, old_orphan)
        self.assertEqual(plan.newer_incomplete_sets[0].items[0].item.name, new_orphan)

    def test_optima_files_form_one_complete_run(self) -> None:
        prefix = "ctip_optima_20260701_200000"
        items: list[RetentionItem] = []
        for database in ("CDN_IT_Partner", "CDN_Ksero_Partner1", "CDN_KNF_Ksero_Partner"):
            backup_name = f"{prefix}_{database}.bak"
            items.extend((self._item(backup_name, 20), self._item(f"{backup_name}.sha256", 20)))
        items.append(self._item(f"{prefix}_manifest.json", 20))

        plan = build_retention_plan(items, retention_days=14, now=self.now)

        self.assertEqual(len(plan.sets), 1)
        self.assertTrue(plan.sets[0].complete)
        self.assertEqual(len(plan.sets[0].items), 7)
        self.assertEqual(plan.preserved_newest_key, "optima:20260701_200000")
        self.assertEqual(plan.deletion_sets, [])

    def test_local_apply_deletes_pair_and_keeps_unknown_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            archive = directory / "backup_20260501_010101.tar.gz"
            checksum = directory / f"{archive.name}.sha256"
            unknown = directory / "operator.txt"
            for path in (archive, checksum, unknown):
                path.write_bytes(b"test")
                old_timestamp = (self.now - timedelta(days=40)).timestamp()
                os.utime(path, (old_timestamp, old_timestamp))

            plan, result = run_local_retention(
                directory,
                retention_days=14,
                dry_run=False,
                preserve_newest_complete=False,
                now=self.now,
            )

            self.assertEqual(plan.deletion_files, 2)
            self.assertEqual(result.deleted_files, 2)
            self.assertFalse(archive.exists())
            self.assertFalse(checksum.exists())
            self.assertTrue(unknown.exists())

    def test_local_retention_does_not_follow_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            directory = root / "backup"
            directory.mkdir()
            target = root / "important.tar.gz"
            target.write_bytes(b"important")
            link = directory / "backup_20260501_010101.tar.gz"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"Brak obsługi dowiązań symbolicznych: {exc}")

            plan, result = run_local_retention(
                directory,
                retention_days=14,
                dry_run=False,
                preserve_newest_complete=False,
                now=self.now,
            )

            self.assertEqual(plan.managed_files, 0)
            self.assertEqual(result.deleted_files, 0)
            self.assertTrue(target.exists())
            self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
