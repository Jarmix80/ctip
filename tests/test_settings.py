"""Testy logiki ustawien aplikacji."""

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
