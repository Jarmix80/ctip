"""Testy logiki ustawien aplikacji."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.core import config
from app.core.config import Settings


class SettingsTests(unittest.TestCase):
    """Waliduje zachowanie ustawien wyliczanych dynamicznie."""

    def test_domyslne_fallbacki_wskazuja_na_lokalne_srodowisko_testowe(self) -> None:
        cfg = Settings(_env_file=None)

        self.assertEqual(cfg.pbx_host, "127.0.0.1")
        self.assertEqual(cfg.pbx_port, 5525)
        self.assertEqual(cfg.pg_host, "127.0.0.1")
        self.assertEqual(cfg.pg_port, 5432)
        self.assertEqual(cfg.pg_database, "ctip_test")
        self.assertEqual(cfg.pg_user, "ctip_test")
        self.assertEqual(cfg.fb_host, "127.0.0.1")
        self.assertEqual(cfg.fb_v_host, "127.0.0.1")
        self.assertTrue(cfg.sms_test_mode)
        self.assertEqual(cfg.sms_api_url, "")
        self.assertFalse(cfg.block_client_communications)
        self.assertEqual(cfg.ctip_runtime_profile, "test")
        self.assertEqual(cfg.outbound_delivery_mode, "disabled")
        self.assertTrue(cfg.test_network_isolation_required)
        self.assertFalse(cfg.shipping_enabled)
        self.assertFalse(cfg.shipping_catalog_mutations_enabled)
        self.assertFalse(cfg.shipping_fulfillment_enabled)

    def test_resolver_prefers_test_file_without_explicit_override(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            test_file = root / ".env.test"
            production_file = root / ".env"
            test_file.touch()
            production_file.touch()
            with (
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch.object(config, "_PROJECT_ROOT", root),
                mock.patch.object(config, "_ENV_TEST_FILE", test_file),
                mock.patch.object(config, "_ENV_FILE", production_file),
            ):
                selected = config._resolve_settings_env_file()

        self.assertEqual(selected, str(test_file))

    def test_resolver_uses_production_file_when_test_file_is_absent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            test_file = root / ".env.test"
            production_file = root / ".env"
            production_file.touch()
            with (
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch.object(config, "_PROJECT_ROOT", root),
                mock.patch.object(config, "_ENV_TEST_FILE", test_file),
                mock.patch.object(config, "_ENV_FILE", production_file),
            ):
                selected = config._resolve_settings_env_file()

        self.assertEqual(selected, str(production_file))

    def test_database_url_uses_psycopg_async_driver(self) -> None:
        cfg = Settings(_env_file=None)

        self.assertTrue(cfg.database_url.startswith("postgresql+psycopg://"))
        self.assertIn("@127.0.0.1:5432/ctip_test", cfg.database_url)

    def test_tryb_dpd_ma_fallback_i_jawne_pierwszenstwo(self) -> None:
        mock = Settings(_env_file=None, DPD_MODE=None, DPD_TEST_MODE=True)
        production = Settings(_env_file=None, DPD_MODE=None, DPD_TEST_MODE=False)
        demo = Settings(_env_file=None, DPD_MODE="demo", DPD_TEST_MODE=False)

        self.assertEqual(mock.dpd_effective_mode, "mock")
        self.assertEqual(production.dpd_effective_mode, "production")
        self.assertEqual(demo.dpd_effective_mode, "demo")

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

    def test_cors_allowed_origins_merge_env_and_public_urls(self) -> None:
        cfg = Settings(
            CORS_ALLOWED_ORIGINS="http://localhost:8000, https://panel.example.com/app",
            ADMIN_PANEL_URL="http://192.168.0.133:8000/admin",
            FORM_PUBLIC_BASE_URL="https://forms.example.com/formularz",
        )
        self.assertEqual(
            cfg.cors_allowed_origins,
            [
                "http://localhost:8000",
                "https://panel.example.com",
                "http://192.168.0.133:8000",
                "https://forms.example.com",
            ],
        )

    def test_auth_cookie_samesite_falls_back_to_lax(self) -> None:
        cfg = Settings(AUTH_COOKIE_SAMESITE="niepoprawne")
        self.assertEqual(cfg.auth_cookie_samesite, "lax")

    def test_public_form_trusted_hosts_include_configured_host(self) -> None:
        cfg = Settings(FORM_PUBLIC_BASE_URL="https://form.ksero-partner.com.pl")
        self.assertEqual(
            cfg.public_form_trusted_hosts,
            ["localhost", "127.0.0.1", "::1", "testserver", "form.ksero-partner.com.pl"],
        )

    def test_mailbox_defaults_and_override(self) -> None:
        cfg_default = Settings(_env_file=None)
        self.assertEqual(cfg_default.mailbox_imap_port, 993)
        self.assertEqual(cfg_default.mailbox_smtp_port, 465)
        self.assertTrue(cfg_default.mailbox_smtp_use_ssl)
        self.assertFalse(cfg_default.mailbox_smtp_use_starttls)

        cfg_custom = Settings(
            MAILBOX_EMAIL_ADDRESS="umowy-tets@ksero-partner.com.pl",
            MAILBOX_EMAIL_PASSWORD="Sekret",
            MAILBOX_IMAP_HOST="ksero-partner.com.pl",
            MAILBOX_IMAP_PORT=993,
            MAILBOX_SMTP_HOST="ksero-partner.com.pl",
            MAILBOX_SMTP_PORT=465,
            MAILBOX_SMTP_USE_SSL=True,
            MAILBOX_SMTP_USE_STARTTLS=False,
        )
        self.assertEqual(cfg_custom.mailbox_email_address, "umowy-tets@ksero-partner.com.pl")
        self.assertEqual(cfg_custom.mailbox_imap_host, "ksero-partner.com.pl")
        self.assertEqual(cfg_custom.mailbox_smtp_host, "ksero-partner.com.pl")

    def test_client_communications_blocking_from_env(self) -> None:
        cfg = Settings(BLOCK_CLIENT_COMMUNICATIONS=True)
        self.assertTrue(cfg.block_client_communications)


if __name__ == "__main__":
    unittest.main()
