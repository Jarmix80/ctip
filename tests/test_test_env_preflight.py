"""Testy rygorystycznego preflightu środowiska testowego."""

from __future__ import annotations

import unittest
from unittest import mock

from scripts import test_env_preflight


class TestEnvironmentPreflightTests(unittest.TestCase):
    """Sprawdza wykrywanie produkcyjnych hostów i aktywnej komunikacji."""

    def test_detects_production_database_host(self) -> None:
        with mock.patch.object(test_env_preflight.settings, "pg_host", "192.168.0.8"):
            issues = test_env_preflight.collect_issues()
        self.assertTrue(any("PGHOST" in issue and "produkcyjny" in issue for issue in issues))

    def test_detects_live_delivery_mode(self) -> None:
        with mock.patch.object(test_env_preflight.settings, "outbound_delivery_mode", "live"):
            issues = test_env_preflight.collect_issues()
        self.assertTrue(any("OUTBOUND_DELIVERY_MODE" in issue for issue in issues))

    def test_detects_local_firebird_mode(self) -> None:
        with mock.patch.object(test_env_preflight.settings, "fb_mode", "local"):
            issues = test_env_preflight.collect_issues()
        self.assertTrue(any("FB_MODE" in issue for issue in issues))

    def test_allows_dedicated_test_google_sheet(self) -> None:
        with (
            mock.patch.multiple(
                test_env_preflight.settings,
                google_sheets_enabled=True,
                google_application_credentials="/run/secrets/google-service-account.json",
                google_sheets_spreadsheet_id="test-sheet-id",
                google_sheets_test_spreadsheet_id="test-sheet-id",
                google_sheets_test_spreadsheet_title="Zerowki_test",
                google_sheets_workflow_devices_sheet="Urzadzenia_magazyn",
                device_sheet_outbox_scheduler_enabled=True,
            ),
            mock.patch.object(test_env_preflight.Path, "is_file", return_value=True),
        ):
            issues = test_env_preflight.collect_issues()

        self.assertFalse(
            any("Google Sheets" in issue or "GOOGLE_SHEETS" in issue for issue in issues)
        )

    def test_blocks_google_sheet_different_from_test_guard(self) -> None:
        with (
            mock.patch.multiple(
                test_env_preflight.settings,
                google_sheets_enabled=True,
                google_application_credentials="/run/secrets/google-service-account.json",
                google_sheets_spreadsheet_id="production-sheet-id",
                google_sheets_test_spreadsheet_id="test-sheet-id",
                google_sheets_test_spreadsheet_title="Zerowki_test",
                google_sheets_workflow_devices_sheet="Urzadzenia_magazyn",
                device_sheet_outbox_scheduler_enabled=True,
            ),
            mock.patch.object(test_env_preflight.Path, "is_file", return_value=True),
        ):
            issues = test_env_preflight.collect_issues()

        self.assertTrue(any("dedykowanego skoroszytu TEST" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
