"""Testy logiki ustawien aplikacji."""

from __future__ import annotations

import unittest

from app.core.config import Settings


class SettingsTests(unittest.TestCase):
    """Waliduje zachowanie ustawien wyliczanych dynamicznie."""

    def test_backup_execution_active_respects_explicit_true(self) -> None:
        cfg = Settings(
            BACKUP_EXECUTION_ENABLED=True,
            BACKUP_PRODUCTION_HOST="203.0.113.10",
        )
        self.assertTrue(cfg.backup_execution_active)

    def test_backup_execution_active_respects_explicit_false(self) -> None:
        cfg = Settings(
            BACKUP_EXECUTION_ENABLED=False,
            BACKUP_PRODUCTION_HOST="127.0.0.1",
        )
        self.assertFalse(cfg.backup_execution_active)

    def test_backup_execution_active_uses_production_host_when_flag_is_none(self) -> None:
        cfg = Settings(
            BACKUP_EXECUTION_ENABLED=None,
            BACKUP_PRODUCTION_HOST="127.0.0.1",
        )
        self.assertTrue(cfg.backup_execution_active)

    def test_backup_execution_active_blocks_non_production_host_when_flag_is_none(self) -> None:
        cfg = Settings(
            BACKUP_EXECUTION_ENABLED=None,
            BACKUP_PRODUCTION_HOST="203.0.113.10",
        )
        self.assertFalse(cfg.backup_execution_active)


if __name__ == "__main__":
    unittest.main()
