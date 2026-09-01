"""Testy izolacji komunikacji wychodzącej w profilu testowym."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock

from app.core.config import settings
from app.services.admin_users import resolve_email_delivery_settings
from app.services.email_client import send_smtp_message
from app.services.sms_provider import HttpSmsProvider


class DummySmtp:
    """Minimalny lokalny transport SMTP używany w testach."""

    instances: list[DummySmtp] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.messages: list[EmailMessage] = []
        self.envelope_senders: list[str | None] = []
        self.instances.append(self)

    def __enter__(self) -> DummySmtp:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def send_message(self, message: EmailMessage, from_addr: str | None = None) -> None:
        self.messages.append(message)
        self.envelope_senders.append(from_addr)


class OutboundSafetyTests(unittest.TestCase):
    """Waliduje przechwytywanie e-maili i pełną symulację SMS."""

    def setUp(self) -> None:
        self.previous = {
            "ctip_runtime_profile": settings.ctip_runtime_profile,
            "outbound_delivery_mode": settings.outbound_delivery_mode,
            "outbound_audit_dir": settings.outbound_audit_dir,
            "email_host": settings.email_host,
            "email_port": settings.email_port,
            "email_sender_address": settings.email_sender_address,
            "email_reply_to_address": settings.email_reply_to_address,
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        settings.ctip_runtime_profile = "test"
        settings.outbound_delivery_mode = "capture"
        settings.outbound_audit_dir = self.temp_dir.name
        settings.email_host = "localhost"
        settings.email_port = 1025
        settings.email_sender_address = "system@ksero-partner.com.pl"
        settings.email_reply_to_address = "marcin@ksero-partner.com.pl"
        DummySmtp.instances.clear()

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            setattr(settings, key, value)
        self.temp_dir.cleanup()

    @staticmethod
    def _message() -> EmailMessage:
        message = EmailMessage()
        message["From"] = "ctip-test@localhost"
        message["To"] = "klient@example.com"
        message["Subject"] = "Test przechwycenia"
        message.set_content("Pełna treść wiadomości testowej")
        return message

    def test_email_uses_local_mailpit_and_writes_full_report(self) -> None:
        with mock.patch("app.services.email_client.smtplib.SMTP", DummySmtp):
            result = asyncio.run(
                send_smtp_message(
                    host="smtp.production.example",
                    port=587,
                    username="real-user",
                    password="real-password",
                    use_tls=True,
                    use_ssl=False,
                    message=self._message(),
                    source="unit_test",
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(DummySmtp.instances[0].host, "localhost")
        self.assertEqual(DummySmtp.instances[0].port, 1025)
        sent_message = DummySmtp.instances[0].messages[0]
        self.assertEqual(parseaddr(sent_message["From"])[1], "system@ksero-partner.com.pl")
        self.assertEqual(sent_message["Reply-To"], "marcin@ksero-partner.com.pl")
        self.assertEqual(
            DummySmtp.instances[0].envelope_senders,
            ["system@ksero-partner.com.pl"],
        )
        logs = list(Path(self.temp_dir.name).glob("outbound_test_*.log"))
        self.assertEqual(len(logs), 1)
        entry = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["recipients"], ["klient@example.com"])
        self.assertEqual(entry["source"], "unit_test")
        self.assertIn("Pełna treść wiadomości testowej", entry["content"])

    def test_live_mode_is_rejected_in_test_runtime(self) -> None:
        settings.outbound_delivery_mode = "live"
        with mock.patch("app.services.email_client.smtplib.SMTP") as smtp:
            result = asyncio.run(
                send_smtp_message(
                    host="smtp.production.example",
                    port=587,
                    username=None,
                    password=None,
                    use_tls=False,
                    use_ssl=False,
                    message=self._message(),
                )
            )
        self.assertFalse(result.success)
        smtp.assert_not_called()

    def test_live_wymaga_kanonicznego_nadawcy_i_reply_to(self) -> None:
        settings.ctip_runtime_profile = "production"
        settings.outbound_delivery_mode = "live"
        settings.email_reply_to_address = None
        with mock.patch("app.services.email_client.smtplib.SMTP") as smtp:
            result = asyncio.run(
                send_smtp_message(
                    host="smtp.production.example",
                    port=587,
                    username=None,
                    password=None,
                    use_tls=False,
                    use_ssl=False,
                    message=self._message(),
                )
            )
        self.assertFalse(result.success)
        self.assertIn("EMAIL_REPLY_TO_ADDRESS", result.message)
        smtp.assert_not_called()

    def test_resolver_nie_pozwala_bazie_nadpisac_nadawcy(self) -> None:
        settings.ctip_runtime_profile = "production"
        with mock.patch(
            "app.services.admin_users._settings_store.get_namespace",
            new=AsyncMock(
                return_value={
                    "host": "smtp.example.com",
                    "sender_address": "umowy@ksero-partner.com.pl",
                }
            ),
        ):
            delivery = asyncio.run(resolve_email_delivery_settings(mock.Mock()))

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.sender_address, "system@ksero-partner.com.pl")

    def test_sms_test_mode_never_opens_http_connection(self) -> None:
        provider = HttpSmsProvider(
            "https://sms.production.example",
            "token",
            "CTIP",
            test_mode=True,
            delivery_mode="capture",
        )
        provider._client = mock.Mock(side_effect=AssertionError("Połączenie HTTP jest zabronione"))
        result = provider.send_sms(
            "+48123123123",
            "Treść SMS",
            metadata={"sms_id": 10, "source": "unit_test_sms"},
        )
        self.assertTrue(result.success)
        self.assertEqual(result.provider_status, "SIMULATED")
        provider._client.assert_not_called()
        log = next(Path(self.temp_dir.name).glob("outbound_test_*.log"))
        entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["channel"], "sms")
        self.assertEqual(entry["recipients"], ["+48123123123"])
        self.assertEqual(entry["content"], "Treść SMS")


if __name__ == "__main__":
    unittest.main()
