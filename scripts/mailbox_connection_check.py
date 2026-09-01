#!/usr/bin/env python3
"""Test odbiorczego połączenia IMAP skrzynki automatyzacji."""

from __future__ import annotations

import argparse
import imaplib
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
    if config.imap_port <= 0:
        errors.append("MAILBOX_IMAP_PORT musi być dodatni.")
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


def parse_args() -> argparse.Namespace:
    """Parsuje argumenty linii poleceń."""
    parser = argparse.ArgumentParser(
        description=(
            "Weryfikuje odbiorcze połączenie IMAP skrzynki automatyzacji umów "
            "na podstawie konfiguracji MAILBOX_*."
        )
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=12.0,
        help="Limit czasu połączenia IMAP w sekundach (domyślnie: 12).",
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
        f"(IMAP {config.imap_host}:{config.imap_port})"
    )

    imap_result = test_imap_connection(config, args.timeout)
    print(f"[{'OK' if imap_result.success else 'ERR'}] {imap_result.message}")

    if imap_result.success:
        print("[OK] Skrzynka odbiorcza automatyzacji jest gotowa.")
        return 0
    print("[ERR] Test połączenia IMAP nie powiódł się.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
