"""Twarda walidacja konfiguracji testowego runtime CRM i LAB."""

from __future__ import annotations

import sys

from app.core.config import settings

_PRODUCTION_HOSTS = {"192.168.0.8", "192.168.0.11"}


def validate_test_runtime() -> list[str]:
    """Zwraca listę naruszeń izolacji środowiska testowego."""
    errors: list[str] = []
    if not settings.crm_enabled:
        errors.append("CRM_ENABLED musi mieć wartość true.")
    if not settings.crm_lab_mode:
        errors.append("CRM_LAB_MODE musi mieć wartość true.")
    if not settings.crm_public_prototype_mode:
        errors.append("CRM_PUBLIC_PROTOTYPE_MODE musi mieć wartość true.")
    if settings.pg_database != "ctip_test":
        errors.append("PGDATABASE musi wskazywać bazę ctip_test.")
    if settings.pg_host.strip() in _PRODUCTION_HOSTS:
        errors.append("PGHOST wskazuje zabroniony host produkcyjny.")
    if settings.pbx_host.strip() in _PRODUCTION_HOSTS:
        errors.append("PBX_HOST wskazuje zabronioną centralę produkcyjną.")
    if not settings.sms_test_mode:
        errors.append("SMS_TEST_MODE musi mieć wartość true.")
    if not settings.block_client_communications:
        errors.append("BLOCK_CLIENT_COMMUNICATIONS musi mieć wartość true.")
    if settings.fb_allow_writes:
        errors.append("FB_ALLOW_WRITES musi mieć wartość false.")
    if not settings.is_safe_test_firebird:
        errors.append("Firebird musi być lokalną bazą testową w trybie tylko do odczytu.")
    return errors


def main() -> int:
    """Kończy start procesu, jeżeli konfiguracja nie jest bezpiecznie testowa."""
    errors = validate_test_runtime()
    if errors:
        for error in errors:
            print(f"[BŁĄD] {error}", file=sys.stderr)
        return 1
    print("[OK] Konfiguracja CRM/LAB jest bezpiecznie testowa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
