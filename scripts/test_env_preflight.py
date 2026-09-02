"""Rygorystyczna kontrola izolacji lokalnego środowiska testowego."""

from __future__ import annotations

import argparse
import re
import sys
from importlib import import_module
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

config_module = import_module("app.core.config")
SETTINGS_ENV_FILE = config_module.SETTINGS_ENV_FILE
settings = config_module.settings

PRODUCTION_HOSTS = {"192.168.0.8", "192.168.0.11"}
LOCAL_SERVICE_HOSTS = {
    "127.0.0.1",
    "::1",
    "localhost",
    "postgres",
    "ctip-test-postgres",
    "firebird",
    "mailpit",
    "mock-ctip",
}


def _hostname(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    parsed = urlsplit(normalized if "://" in normalized else f"tcp://{normalized}")
    return str(parsed.hostname or "").lower()


def _spreadsheet_id(value: str | None) -> str:
    """Wyodrębnia identyfikator skoroszytu z ID albo pełnego adresu Google Sheets."""
    normalized = str(value or "").strip()
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", normalized)
    return match.group(1) if match else normalized


def collect_issues(*, check_network: bool = False) -> list[str]:
    """Zwraca listę warunków uniemożliwiających bezpieczny start testu."""
    issues: list[str] = []
    if settings.ctip_runtime_profile != "test":
        issues.append("CTIP_RUNTIME_PROFILE musi mieć wartość test.")
    if Path(SETTINGS_ENV_FILE).name != ".env.test":
        issues.append("CTIP_ENV_FILE musi wskazywać wyłącznie plik .env.test.")
    if settings.pg_database != "ctip_test":
        issues.append("PGDATABASE musi mieć wartość ctip_test.")
    if settings.fb_mode != "network":
        issues.append("FB_MODE musi mieć wartość network dla izolowanego kontenera Firebird.")

    endpoints = {
        "PGHOST": settings.pg_host,
        "FB_HOST": settings.fb_host,
        "FB_V_HOST": settings.fb_v_host,
        "PBX_HOST": settings.pbx_host,
        "EMAIL_HOST": settings.email_host,
    }
    for name, value in endpoints.items():
        host = _hostname(value)
        if host in PRODUCTION_HOSTS:
            issues.append(f"{name} wskazuje na zasób produkcyjny {host}.")
        if host and host not in LOCAL_SERVICE_HOSTS:
            issues.append(f"{name} nie wskazuje na dozwoloną usługę lokalną: {host}.")

    if settings.outbound_delivery_mode not in {"capture", "disabled"}:
        issues.append("OUTBOUND_DELIVERY_MODE musi mieć wartość capture albo disabled.")
    if not settings.sms_test_mode:
        issues.append("SMS_TEST_MODE musi mieć wartość true.")
    if any(
        (
            settings.sms_api_url,
            settings.sms_api_token,
            settings.sms_api_username,
            settings.sms_api_password,
        )
    ):
        issues.append("Dane dostępowe operatora SMS muszą być puste.")
    if settings.outbound_delivery_mode == "capture":
        if _hostname(settings.email_host) not in {"127.0.0.1", "::1", "localhost", "mailpit"}:
            issues.append("Tryb capture wymaga lokalnego serwera Mailpit.")
        if settings.email_username or settings.email_password:
            issues.append("Mailpit nie może używać produkcyjnych danych logowania SMTP.")
        if settings.email_use_tls or settings.email_use_ssl:
            issues.append("Lokalny Mailpit musi działać bez TLS i SSL.")

    if any(
        (
            settings.mailbox_email_address,
            settings.mailbox_email_password,
            settings.mailbox_imap_host,
        )
    ):
        issues.append("Integracja mailbox musi być całkowicie wyłączona.")
    if settings.contracts_mailbox_scheduler_enabled:
        issues.append("Scheduler mailbox musi być wyłączony.")
    if settings.contracts_mailbox_processing_enabled:
        issues.append("Przetwarzanie mailboxa musi być wyłączone.")
    google_sheets_active = any(
        (
            settings.google_sheets_enabled,
            settings.google_application_credentials,
            settings.google_sheets_spreadsheet_id,
            settings.device_sheet_outbox_scheduler_enabled,
        )
    )
    if google_sheets_active:
        spreadsheet_id = _spreadsheet_id(settings.google_sheets_spreadsheet_id)
        test_spreadsheet_id = _spreadsheet_id(settings.google_sheets_test_spreadsheet_id)
        credentials_path = str(settings.google_application_credentials or "").strip()
        if not settings.google_sheets_enabled:
            issues.append("GOOGLE_SHEETS_ENABLED musi mieć wartość true dla workera TEST.")
        if not credentials_path:
            issues.append("Brak GOOGLE_APPLICATION_CREDENTIALS dla arkusza TEST.")
        elif not Path(credentials_path).is_file():
            issues.append("Plik GOOGLE_APPLICATION_CREDENTIALS dla arkusza TEST nie istnieje.")
        if not spreadsheet_id:
            issues.append("Brak GOOGLE_SHEETS_SPREADSHEET_ID dla arkusza TEST.")
        if not test_spreadsheet_id:
            issues.append("Brak GOOGLE_SHEETS_TEST_SPREADSHEET_ID.")
        elif spreadsheet_id != test_spreadsheet_id:
            issues.append("GOOGLE_SHEETS_SPREADSHEET_ID nie wskazuje dedykowanego skoroszytu TEST.")
        if str(settings.google_sheets_test_spreadsheet_title or "").strip() != "Zerowki_test":
            issues.append("GOOGLE_SHEETS_TEST_SPREADSHEET_TITLE musi mieć wartość Zerowki_test.")
        if str(settings.google_sheets_workflow_devices_sheet or "").strip() != (
            "Urzadzenia_magazyn"
        ):
            issues.append(
                "GOOGLE_SHEETS_WORKFLOW_DEVICES_SHEET musi mieć wartość Urzadzenia_magazyn."
            )
        if not settings.device_sheet_outbox_scheduler_enabled:
            issues.append("Scheduler outboxu Google Sheets TEST musi być włączony.")
    if settings.workflow_sheet_status_cache_scheduler_enabled:
        issues.append("Scheduler odświeżania Google Sheets musi być wyłączony.")
    if settings.delivery_notifications_scheduler_enabled:
        issues.append("Scheduler powiadomień dostaw musi być wyłączony.")
    if settings.backup_scheduler_enabled or settings.backup_execution_active:
        issues.append("Automatyczny backup zewnętrzny musi być wyłączony.")
    if any(
        (
            settings.office365_tenant_id,
            settings.office365_client_id,
            settings.office365_client_secret,
            settings.optima_sql_host,
            settings.optima_sql_host_ip,
            settings.optima_sql_login,
            settings.optima_sql_password,
            settings.openai_api_key,
            settings.openai_api_chat_kp,
        )
    ):
        issues.append("Dane dostępowe integracji zewnętrznych muszą być puste.")
    for name, value in (
        ("GRENKE_APP_BASE_URL", settings.grenke_app_base_url),
        ("GRENKE_API_BASE_URL", settings.grenke_api_base_url),
    ):
        host = _hostname(value)
        if host and not host.endswith(".invalid") and host not in LOCAL_SERVICE_HOSTS:
            issues.append(f"{name} musi wskazywać nieaktywną domenę .invalid.")

    if not settings.test_network_isolation_required:
        issues.append("TEST_NETWORK_ISOLATION_REQUIRED musi mieć wartość true.")
    if check_network and Path("/proc/net/route").exists():
        routes = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()[1:]
        if any(line.split()[1] == "00000000" for line in routes if len(line.split()) > 1):
            issues.append("Kontener testowy ma trasę domyślną; wymagana jest sieć internal.")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-network", action="store_true")
    args = parser.parse_args()
    issues = collect_issues(check_network=args.check_network)
    if issues:
        for issue in issues:
            print(f"[BŁĄD] {issue}")
        return 1
    print(f"[OK] Profil testowy jest izolowany; konfiguracja: {SETTINGS_ENV_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
