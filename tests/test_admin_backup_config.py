"""Testy runtime konfiguracji modułu kopii zapasowych."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.api.routes.admin_backup import load_backup_config
from app.core.config import settings


class BackupConfigRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_backup_config_normalizuje_optima_auth_mode_do_lowercase(self) -> None:
        original = settings.optima_sql_auth_mode
        settings.optima_sql_auth_mode = "MIXED"
        try:
            with patch(
                "app.api.routes.admin_backup.settings_store.get_namespace",
                AsyncMock(return_value={}),
            ):
                config = await load_backup_config(session=None)  # type: ignore[arg-type]
        finally:
            settings.optima_sql_auth_mode = original

        self.assertEqual(config.optima_auth_mode, "mixed")
        self.assertEqual(config.integration_source, "env")
        self.assertFalse(config.integration_editable)
