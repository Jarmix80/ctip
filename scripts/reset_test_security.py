"""Resetuje dane uwierzytelniające po odtworzeniu produkcyjnego dumpu."""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from importlib import import_module
from pathlib import Path

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

settings = import_module("app.core.config").settings
hash_password = import_module("app.services.security").hash_password

_ISOLATED_SETTING_NAMESPACES = (
    "backup",
    "ctip",
    "database",
    "email",
    "firebird",
    "firebird_vmaintenance",
    "google_sheets",
    "kp_repair",
    "sms",
    "user_imap",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Neutralizuje sekrety po odtworzeniu produkcyjnej bazy w ctip_test."
    )
    parser.add_argument(
        "--source-env",
        type=Path,
        help="Plik .env zawierający źródłowy ADMIN_SECRET_KEY do rotacji formularzy.",
    )
    return parser.parse_args()


def _build_cipher(secret: str | None, label: str) -> Fernet:
    value = str(secret or "").strip()
    if not value:
        raise ValueError(f"Brak {label}.")
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"Nieprawidłowy {label}.") from exc


def _rotate_payload(
    payload: str,
    *,
    source_cipher: Fernet,
    target_cipher: Fernet,
) -> tuple[str, bool]:
    """Przekłada zaszyfrowany formularz na klucz testowy albo wykrywa gotowy rekord."""
    encoded = payload.encode("ascii")
    try:
        target_cipher.decrypt(encoded)
        return payload, False
    except (InvalidToken, ValueError, UnicodeEncodeError):
        pass
    try:
        decrypted = source_cipher.decrypt(encoded)
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Nie można odszyfrować formularza kluczem źródłowym.") from exc
    return target_cipher.encrypt(decrypted).decode("ascii"), True


def main() -> int:
    """Unieważnia produkcyjne sekrety, zachowując dane biznesowe bez zmian."""
    args = _parse_args()
    if not settings.is_test_runtime or settings.pg_database != "ctip_test":
        print("[BŁĄD] Reset zabezpieczeń wolno wykonać wyłącznie w bazie ctip_test.")
        return 1

    test_email = str(os.getenv("TEST_ADMIN_EMAIL") or "admin-test@example.com").strip().lower()
    test_password = str(os.getenv("TEST_ADMIN_PASSWORD") or "").strip()
    if len(test_password) < 12:
        print("[BŁĄD] TEST_ADMIN_PASSWORD musi mieć co najmniej 12 znaków.")
        return 1

    try:
        target_cipher = _build_cipher(settings.admin_secret_key, "testowego ADMIN_SECRET_KEY")
        source_secret = settings.admin_secret_key
        if args.source_env is not None:
            source_path = args.source_env.expanduser().resolve()
            if not source_path.is_file():
                raise ValueError(f"Brak pliku źródłowego {source_path}.")
            source_secret = dotenv_values(source_path).get("ADMIN_SECRET_KEY")
        source_cipher = _build_cipher(source_secret, "źródłowego ADMIN_SECRET_KEY")
    except ValueError as exc:
        print(f"[BŁĄD] {exc}")
        return 1

    generated_hash = hash_password(secrets.token_urlsafe(48))
    test_hash = hash_password(test_password)
    with psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_database,
        user=settings.pg_user,
        password=settings.pg_password,
        options="-c search_path=ctip,public",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, submitted_payload
                FROM ctip.form_request
                WHERE submitted_payload IS NOT NULL
                  AND submitted_payload <> ''
                ORDER BY id
                """
            )
            payloads_rotated = 0
            for form_id, payload in cursor.fetchall():
                try:
                    rotated_payload, changed = _rotate_payload(
                        str(payload),
                        source_cipher=source_cipher,
                        target_cipher=target_cipher,
                    )
                except ValueError as exc:
                    raise RuntimeError(
                        f"Formularz {form_id}: {exc} Podaj poprawny --source-env."
                    ) from exc
                if changed:
                    cursor.execute(
                        "UPDATE ctip.form_request SET submitted_payload = %s WHERE id = %s",
                        (rotated_payload, form_id),
                    )
                    payloads_rotated += 1
            cursor.execute("DELETE FROM ctip.admin_session")
            sessions_deleted = cursor.rowcount
            cursor.execute("DELETE FROM ctip.admin_setting WHERE is_secret")
            secrets_deleted = cursor.rowcount
            cursor.execute(
                """
                DELETE FROM ctip.admin_setting
                WHERE split_part(key, '.', 1) = ANY(%s)
                   OR key = 'form_handling.public_base_url'
                """,
                (list(_ISOLATED_SETTING_NAMESPACES),),
            )
            integration_settings_deleted = cursor.rowcount
            cursor.execute(
                """
                UPDATE ctip.form_request
                SET token_expires_at = LEAST(token_expires_at, now() - interval '1 second')
                WHERE token_expires_at > now()
                """
            )
            tokens_expired = cursor.rowcount
            cursor.execute(
                """
                UPDATE ctip.sms_out
                SET status = 'ERROR',
                    error_msg = 'Zneutralizowano podczas przygotowania środowiska testowego'
                WHERE status = 'NEW'
                """
            )
            sms_neutralized = cursor.rowcount
            cursor.execute(
                """
                UPDATE ctip.admin_user
                SET is_active = false,
                    password_hash = %s,
                    updated_at = now()
                WHERE email <> %s
                """,
                (generated_hash, test_email),
            )
            users_disabled = cursor.rowcount
            cursor.execute(
                """
                INSERT INTO ctip.admin_user (
                    first_name, last_name, email, role, password_hash, is_active,
                    is_salesperson, mobile_phone, created_at, updated_at
                )
                VALUES ('Administrator', 'Testowy', %s, 'admin', %s, true, false, NULL, now(), now())
                ON CONFLICT (email) DO UPDATE
                SET first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    role = 'admin',
                    password_hash = EXCLUDED.password_hash,
                    is_active = true,
                    is_salesperson = false,
                    mobile_phone = NULL,
                    updated_at = now()
                """,
                (test_email, test_hash),
            )
    print(
        "[OK] Reset zabezpieczeń: "
        f"formularze_przeszyfrowane={payloads_rotated}, "
        f"sesje={sessions_deleted}, sekrety={secrets_deleted}, "
        f"ustawienia_integracji={integration_settings_deleted}, "
        f"tokeny={tokens_expired}, SMS={sms_neutralized}, "
        f"wyłączeni użytkownicy={users_disabled}, konto testowe={test_email}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
