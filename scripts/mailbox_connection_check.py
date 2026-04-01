#!/usr/bin/env python3
"""Test połączenia skrzynki automatyzacji (IMAP + SMTP)."""

from __future__ import annotations

import argparse
import imaplib
import smtplib
import socket
from dataclasses import dataclass

from app.core.config import settings


@dataclass(slots=True)
class MailboxConfig:
    """Parametry połączenia skrzynki automatyzacji."""

    email_address: str
    password: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    smtp_use_ssl: bool
    smtp_use_starttls: bool


@dataclass(slots=True)
class MailboxTestResult:
    """Wynik testu pojedynczego kanału połączenia."""

    success: bool
    message: str


def build_mailbox_config() -> MailboxConfig:
    """Buduje konfigurację skrzynki na podstawie zmiennych środowiskowych."""
    return MailboxConfig(
        email_address=(settings.mailbox_email_address or "").strip(),
        password=settings.mailbox_email_password or "",
        imap_host=(settings.mailbox_imap_host or "").strip(),
        imap_port=int(settings.mailbox_imap_port),
        smtp_host=(settings.mailbox_smtp_host or "").strip(),
        smtp_port=int(settings.mailbox_smtp_port),
        smtp_use_ssl=bool(settings.mailbox_smtp_use_ssl),
        smtp_use_starttls=bool(settings.mailbox_smtp_use_starttls),
    )


def validate_mailbox_config(config: MailboxConfig) -> list[str]:
    """Weryfikuje kompletność konfiguracji skrzynki."""
    errors: list[str] = []
    if not config.email_address:
        errors.append("Brak MAILBOX_EMAIL_ADDRESS.")
    if not config.password:
        errors.append("Brak MAILBOX_EMAIL_PASSWORD.")
    if not config.imap_host:
        errors.append("Brak MAILBOX_IMAP_HOST.")
    if not config.smtp_host:
        errors.append("Brak MAILBOX_SMTP_HOST.")
    if config.imap_port <= 0:
        errors.append("MAILBOX_IMAP_PORT musi być dodatni.")
    if config.smtp_port <= 0:
        errors.append("MAILBOX_SMTP_PORT musi być dodatni.")
    if config.smtp_use_ssl and config.smtp_use_starttls:
        errors.append(
            "MAILBOX_SMTP_USE_SSL i MAILBOX_SMTP_USE_STARTTLS nie mogą być jednocześnie true."
        )
    return errors


def test_imap_connection(config: MailboxConfig, timeout: float) -> MailboxTestResult:
    """Sprawdza logowanie IMAP SSL."""
    try:
        with imaplib.IMAP4_SSL(config.imap_host, config.imap_port, timeout=timeout) as client:
            client.login(config.email_address, config.password)
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                return MailboxTestResult(
                    False, "IMAP: logowanie poprawne, ale nie można otworzyć INBOX."
                )
    except Exception as exc:  # noqa: BLE001
        return MailboxTestResult(False, f"IMAP: błąd połączenia/logowania: {exc}")
    return MailboxTestResult(True, "IMAP: połączenie SSL i logowanie zakończone sukcesem.")


def test_smtp_connection(config: MailboxConfig, timeout: float) -> MailboxTestResult:
    """Sprawdza logowanie SMTP w trybie SSL albo STARTTLS."""
    try:
        if config.smtp_use_ssl:
            connection = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=timeout)
        else:
            connection = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=timeout)
        with connection:
            connection.ehlo()
            if config.smtp_use_starttls:
                connection.starttls()
                connection.ehlo()
            connection.login(config.email_address, config.password)
    except Exception as exc:  # noqa: BLE001
        return MailboxTestResult(False, f"SMTP: błąd połączenia/logowania: {exc}")
    mode = "SSL" if config.smtp_use_ssl else ("STARTTLS" if config.smtp_use_starttls else "PLAIN")
    return MailboxTestResult(True, f"SMTP: połączenie {mode} i logowanie zakończone sukcesem.")


def parse_args() -> argparse.Namespace:
    """Parsuje argumenty linii poleceń."""
    parser = argparse.ArgumentParser(
        description=(
            "Weryfikuje połączenie skrzynki automatyzacji umów (IMAP + SMTP) "
            "na podstawie konfiguracji MAILBOX_*."
        )
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=12.0,
        help="Limit czasu połączenia dla IMAP/SMTP w sekundach (domyślnie: 12).",
    )
    return parser.parse_args()


def main() -> int:
    """Punkt startowy skryptu testu połączenia skrzynki."""
    args = parse_args()
    socket.setdefaulttimeout(args.timeout)
    config = build_mailbox_config()
    errors = validate_mailbox_config(config)
    if errors:
        for error in errors:
            print(f"[ERR] {error}")
        return 2

    print(
        f"[INFO] Test skrzynki: {config.email_address} "
        f"(IMAP {config.imap_host}:{config.imap_port}, SMTP {config.smtp_host}:{config.smtp_port})"
    )

    imap_result = test_imap_connection(config, args.timeout)
    print(f"[{'OK' if imap_result.success else 'ERR'}] {imap_result.message}")

    smtp_result = test_smtp_connection(config, args.timeout)
    print(f"[{'OK' if smtp_result.success else 'ERR'}] {smtp_result.message}")

    if imap_result.success and smtp_result.success:
        print("[OK] Skrzynka automatyzacji jest gotowa.")
        return 0
    print("[ERR] Co najmniej jeden test połączenia nie powiódł się.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
