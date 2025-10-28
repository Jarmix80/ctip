"""Narzędzia pomocnicze do testowania konfiguracji SMTP."""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(slots=True)
class EmailTestResult:
    success: bool
    message: str


@dataclass(slots=True)
class EmailSendResult:
    success: bool
    message: str


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
) -> EmailSendResult:
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
        return EmailSendResult(True, "Wiadomość została wysłana.")

    return await asyncio.to_thread(_send)


__all__ = ["EmailTestResult", "EmailSendResult", "test_smtp_connection", "send_smtp_message"]
