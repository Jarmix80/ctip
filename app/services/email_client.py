"""Narzędzia pomocnicze do testowania konfiguracji SMTP."""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import settings
from app.services.outbound_audit import record_email_attempt

_CAPTURE_SMTP_HOSTS = {"127.0.0.1", "::1", "localhost", "mailpit"}


@dataclass(slots=True)
class EmailTestResult:
    success: bool
    message: str


@dataclass(slots=True)
class EmailSendResult:
    success: bool
    message: str


def _resolve_transport(
    *,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    use_tls: bool,
    use_ssl: bool,
) -> tuple[str, int, str | None, str | None, bool, bool]:
    """Wymusza lokalny transport SMTP w profilu przechwytującym."""
    mode = settings.outbound_delivery_mode
    if settings.is_test_runtime and mode == "live":
        raise ValueError("Profil testowy nie może używać trybu komunikacji live.")
    if mode != "capture":
        return host, port, username, password, use_tls, use_ssl

    capture_host = str(settings.email_host or "").strip().lower()
    if capture_host not in _CAPTURE_SMTP_HOSTS:
        raise ValueError("Tryb capture wymaga lokalnego hosta SMTP Mailpit.")
    return capture_host, int(settings.email_port), None, None, False, False


def test_smtp_connection(
    *,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    use_tls: bool,
    use_ssl: bool,
    timeout: float = 10.0,
) -> EmailTestResult:
    """Weryfikuje możliwość połączenia z serwerem SMTP i ewentualnego logowania."""

    if settings.outbound_delivery_mode == "disabled":
        return EmailTestResult(False, "Transport SMTP jest wyłączony przez profil środowiska.")

    try:
        host, port, username, password, use_tls, use_ssl = _resolve_transport(
            host=host,
            port=port,
            username=username,
            password=password,
            use_tls=use_tls,
            use_ssl=use_ssl,
        )
    except ValueError as exc:
        return EmailTestResult(False, str(exc))

    if not host:
        return EmailTestResult(False, "Brak hosta SMTP w konfiguracji.")

    if use_tls and use_ssl:
        return EmailTestResult(False, "Nie można jednocześnie używać STARTTLS i SSL.")

    try:
        if use_ssl:
            connection = smtplib.SMTP_SSL(host=host, port=port, timeout=timeout)
        else:
            connection = smtplib.SMTP(host=host, port=port, timeout=timeout)
        with connection:
            connection.ehlo()
            if use_tls:
                connection.starttls()
                connection.ehlo()
            if username:
                connection.login(username, password or "")
    except smtplib.SMTPAuthenticationError as exc:
        return EmailTestResult(
            False,
            f"Błąd uwierzytelnienia: {exc.smtp_error.decode(errors='ignore') if hasattr(exc.smtp_error, 'decode') else exc.smtp_error}",
        )
    except Exception as exc:
        return EmailTestResult(False, f"Błąd połączenia SMTP: {exc}")

    return EmailTestResult(True, "Połączenie z serwerem SMTP zakończone sukcesem.")


async def send_smtp_message(
    *,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    use_tls: bool,
    use_ssl: bool,
    message: EmailMessage,
    timeout: float = 10.0,
    source: str = "smtp",
) -> EmailSendResult:
    mode = settings.outbound_delivery_mode
    if mode == "disabled":
        try:
            record_email_attempt(message, source=source, status="BLOCKED")
        except OSError as exc:
            return EmailSendResult(False, f"Nie udało się zapisać raportu komunikacji: {exc}")
        return EmailSendResult(False, "Wysyłka została zablokowana przez profil środowiska.")

    try:
        host, port, username, password, use_tls, use_ssl = _resolve_transport(
            host=host,
            port=port,
            username=username,
            password=password,
            use_tls=use_tls,
            use_ssl=use_ssl,
        )
    except ValueError as exc:
        return EmailSendResult(False, str(exc))

    if mode == "capture":
        try:
            record_email_attempt(
                message,
                source=source,
                status="CAPTURED",
                metadata={"smtp_host": host, "smtp_port": port},
            )
        except OSError as exc:
            return EmailSendResult(False, f"Nie udało się zapisać raportu komunikacji: {exc}")

    if not host:
        return EmailSendResult(False, "Brak hosta SMTP w konfiguracji.")
    if use_tls and use_ssl:
        return EmailSendResult(False, "Nie można jednocześnie używać STARTTLS i SSL.")

    def _send() -> EmailSendResult:
        try:
            if use_ssl:
                connection = smtplib.SMTP_SSL(host=host, port=port, timeout=timeout)
            else:
                connection = smtplib.SMTP(host=host, port=port, timeout=timeout)
            with connection:
                connection.ehlo()
                if use_tls:
                    connection.starttls()
                    connection.ehlo()
                if username:
                    connection.login(username, password or "")
                connection.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            return EmailSendResult(
                False,
                f"Błąd uwierzytelnienia: {exc.smtp_error.decode(errors='ignore') if hasattr(exc.smtp_error, 'decode') else exc.smtp_error}",
            )
        except Exception as exc:
            return EmailSendResult(False, f"Błąd wysyłki SMTP: {exc}")
        if mode == "capture":
            return EmailSendResult(True, "Wiadomość została przechwycona lokalnie przez Mailpit.")
        return EmailSendResult(True, "Wiadomość została wysłana.")

    return await asyncio.to_thread(_send)


__all__ = ["EmailTestResult", "EmailSendResult", "test_smtp_connection", "send_smtp_message"]
