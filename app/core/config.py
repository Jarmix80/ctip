"""Konfiguracja aplikacji oparta na zmiennych środowiskowych."""

import os
import socket
from functools import lru_cache
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_TEST_FILE = _PROJECT_ROOT / ".env.test"
_ENV_FILE = _PROJECT_ROOT / ".env"


def _resolve_settings_env_file() -> str:
    """Wybiera dokładnie jeden plik środowiskowy dla bieżącego procesu."""
    configured = str(os.getenv("CTIP_ENV_FILE") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return str(path.resolve())
    if _ENV_TEST_FILE.is_file():
        return str(_ENV_TEST_FILE)
    if _ENV_FILE.is_file():
        return str(_ENV_FILE)
    return str(_ENV_TEST_FILE)


SETTINGS_ENV_FILE = _resolve_settings_env_file()
_PRODUCTION_ENV_SELECTED = Path(SETTINGS_ENV_FILE).name == ".env"


class Settings(BaseSettings):
    """Parametry działania backendu."""

    app_title: str = "CTIP API"
    app_version: str = "0.2.14"
    ctip_runtime_profile: Literal["test", "production"] = Field(
        default="production" if _PRODUCTION_ENV_SELECTED else "test",
        alias="CTIP_RUNTIME_PROFILE",
    )
    outbound_delivery_mode: Literal["live", "capture", "disabled"] = Field(
        default="live" if _PRODUCTION_ENV_SELECTED else "disabled",
        alias="OUTBOUND_DELIVERY_MODE",
    )
    outbound_audit_dir: str = Field(default="logs/outbound_test", alias="OUTBOUND_AUDIT_DIR")
    outbound_audit_retention_days: int = Field(default=14, alias="OUTBOUND_AUDIT_RETENTION_DAYS")
    test_network_isolation_required: bool = Field(
        default=True, alias="TEST_NETWORK_ISOLATION_REQUIRED"
    )

    pbx_host: str = Field(default="127.0.0.1", alias="PBX_HOST")
    pbx_port: int = Field(default=5525, alias="PBX_PORT")
    pbx_pin: str = Field(default="1234", alias="PBX_PIN")

    pg_host: str = Field(default="127.0.0.1", alias="PGHOST")
    pg_port: int = Field(default=5432, alias="PGPORT")
    pg_database: str = Field(default="ctip_test", alias="PGDATABASE")
    pg_user: str = Field(default="ctip_test", alias="PGUSER")
    pg_password: str = Field(default="ctip_test", alias="PGPASSWORD")
    pg_sslmode: str = Field(default="disable", alias="PGSSLMODE")

    fb_host: str = Field(default="127.0.0.1", alias="FB_HOST")
    fb_port: int = Field(default=3050, alias="FB_PORT")
    fb_mode: str = Field(default="local", alias="FB_MODE")
    fb_database: str = Field(default="/tmp/test_ms.fdb", alias="FB_DATABASE")
    fb_user: str = Field(default="SYSDBA", alias="FB_USER")
    fb_password: str = Field(default="masterkey", alias="FB_PASSWORD")
    fb_charset: str = Field(default="UTF8", alias="FB_CHARSET")
    fb_role: str | None = Field(default=None, alias="FB_ROLE")
    fb_local_copy_path: str = Field(
        default="inbox/firebird/test_ms_local.fdb", alias="FB_LOCAL_COPY_PATH"
    )
    fb_v_host: str = Field(default="127.0.0.1", alias="FB_V_HOST")
    fb_v_port: int = Field(default=3050, alias="FB_V_PORT")
    fb_v_database: str = Field(default="/tmp/test_vmaintenance.fdb", alias="FB_V_DATABASE")
    fb_v_user: str = Field(default="SYSDBA", alias="FB_V_USER")
    fb_v_password: str = Field(default="masterkey", alias="FB_V_PASSWORD")
    fb_v_charset: str = Field(default="UTF8", alias="FB_V_CHARSET")
    fb_v_role: str | None = Field(default=None, alias="FB_V_ROLE")

    kp_csv_directory: str = Field(default="inbox/ewidencja", alias="KP_CSV_DIRECTORY")
    kp_csv_pattern: str = Field(default="DPLAC*.csv", alias="KP_CSV_PATTERN")
    kp_email_lookback_months: int = Field(default=5, alias="KP_EMAIL_LOOKBACK_MONTHS")
    fb_allow_writes: bool = Field(default=False, alias="FB_ALLOW_WRITES")
    fb_warehouse_client_id: int = Field(default=656, alias="FB_WAREHOUSE_CLIENT_ID")
    fb_warehouse_id: int = Field(default=28, alias="FB_WAREHOUSE_ID")

    sms_default_sender: str = Field(default="CTIP-Test", alias="SMS_DEFAULT_SENDER")
    sms_type: str = Field(default="eco+", alias="SMS_TYPE")
    sms_api_url: str = Field(default="", alias="SMS_API_URL")
    sms_api_token: str | None = Field(default=None, alias="SMS_API_TOKEN")
    sms_api_username: str | None = Field(default=None, alias="SMS_API_USERNAME")
    sms_api_password: str | None = Field(default=None, alias="SMS_API_PASSWORD")
    sms_test_mode: bool = Field(default=True, alias="SMS_TEST_MODE")
    shipping_enabled: bool = Field(default=False, alias="SHIPPING_ENABLED")
    shipping_catalog_mutations_enabled: bool = Field(
        default=False, alias="SHIPPING_CATALOG_MUTATIONS_ENABLED"
    )
    shipping_fulfillment_enabled: bool = Field(default=False, alias="SHIPPING_FULFILLMENT_ENABLED")
    dpd_enabled: bool = Field(default=False, alias="DPD_ENABLED")
    dpd_mode: str | None = Field(default=None, alias="DPD_MODE")
    dpd_test_mode: bool = Field(default=True, alias="DPD_TEST_MODE")
    dpd_api_url: str = Field(default="", alias="DPD_API_URL")
    dpd_login: str | None = Field(default=None, alias="DPD_LOGIN")
    dpd_password: str | None = Field(default=None, alias="DPD_PASSWORD")
    dpd_master_fid: str | None = Field(default=None, alias="DPD_MASTER_FID")
    dpd_payer_fid: str | None = Field(default=None, alias="DPD_PAYER_FID")
    dpd_generate_packages_path: str = Field(
        default="/public/shipment/v1/generatePackagesNumbers",
        alias="DPD_GENERATE_PACKAGES_PATH",
    )
    dpd_generate_labels_path: str = Field(
        default="/public/shipment/v1/generateSpedLabels",
        alias="DPD_GENERATE_LABELS_PATH",
    )
    dpd_timeout_seconds: float = Field(default=20.0, alias="DPD_TIMEOUT_SECONDS")
    dpd_sender_company: str = Field(default="Ksero-Partner", alias="DPD_SENDER_COMPANY")
    dpd_sender_contact: str | None = Field(default=None, alias="DPD_SENDER_CONTACT")
    dpd_sender_street: str | None = Field(default=None, alias="DPD_SENDER_STREET")
    dpd_sender_postal_code: str | None = Field(default=None, alias="DPD_SENDER_POSTAL_CODE")
    dpd_sender_city: str | None = Field(default=None, alias="DPD_SENDER_CITY")
    dpd_sender_phone: str | None = Field(default=None, alias="DPD_SENDER_PHONE")
    dpd_sender_email: str | None = Field(default=None, alias="DPD_SENDER_EMAIL")
    shipping_warehouse_id: int = Field(default=1, alias="SHIPPING_WAREHOUSE_ID")
    shipping_default_weight_kg: float = Field(default=2.0, alias="SHIPPING_DEFAULT_WEIGHT_KG")
    shipping_weight_presets_raw: str = Field(default="1,2,5,10", alias="SHIPPING_WEIGHT_PRESETS_KG")
    shipping_courier_cutoff_hour: int = Field(default=14, alias="SHIPPING_COURIER_CUTOFF_HOUR")
    shipping_courier_cutoff_minute: int = Field(default=30, alias="SHIPPING_COURIER_CUTOFF_MINUTE")
    shipping_test_firebird_writes: bool = Field(
        default=False, alias="SHIPPING_TEST_FIREBIRD_WRITES"
    )
    shipping_compatibility_web_enabled: bool = Field(
        default=False, alias="SHIPPING_COMPATIBILITY_WEB_ENABLED"
    )
    shipping_compatibility_web_model: str = Field(
        default="gpt-4.1-mini", alias="SHIPPING_COMPATIBILITY_WEB_MODEL"
    )
    shipping_compatibility_web_batch_limit: int = Field(
        default=20, alias="SHIPPING_COMPATIBILITY_WEB_BATCH_LIMIT"
    )
    shipping_compatibility_web_daily_limit: int = Field(
        default=100, alias="SHIPPING_COMPATIBILITY_WEB_DAILY_LIMIT"
    )
    shipping_compatibility_web_timeout_seconds: float = Field(
        default=60.0, alias="SHIPPING_COMPATIBILITY_WEB_TIMEOUT_SECONDS"
    )
    openai_api_chat_kp: str | None = Field(default=None, alias="OPENAI_API_CHAT_KP")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    admin_secret_key: str | None = Field(default=None, alias="ADMIN_SECRET_KEY")
    admin_session_ttl_minutes: int = Field(default=60, alias="ADMIN_SESSION_TTL_MINUTES")
    admin_session_remember_hours: int = Field(default=72, alias="ADMIN_SESSION_REMEMBER_HOURS")
    login_failure_limit: int = Field(default=5, alias="LOGIN_FAILURE_LIMIT")
    login_failure_window_minutes: int = Field(default=15, alias="LOGIN_FAILURE_WINDOW_MINUTES")
    panel_allowed_networks_raw: str = Field(
        default="127.0.0.0/8,::1/128,192.168.0.0/24",
        alias="PANEL_ALLOWED_NETWORKS",
    )
    admin_panel_url: str | None = Field(
        default="http://localhost:8000/admin", alias="ADMIN_PANEL_URL"
    )
    form_public_base_url: str | None = Field(default=None, alias="FORM_PUBLIC_BASE_URL")
    grenke_app_base_url: str = Field(
        default="https://newonline.leasingoptymalny.pl",
        alias="GRENKE_APP_BASE_URL",
    )
    grenke_api_base_url: str = Field(
        default="https://newonline.leasingoptymalny.pl/API",
        alias="GRENKE_API_BASE_URL",
    )
    grenke_timeout_seconds: float = Field(default=12.0, alias="GRENKE_TIMEOUT_SECONDS")
    cors_allowed_origins_raw: str = Field(
        default="http://localhost:8000,http://127.0.0.1:8000,http://testserver",
        alias="CORS_ALLOWED_ORIGINS",
    )
    auth_cookie_name: str = Field(default="ctip_session", alias="AUTH_COOKIE_NAME")
    auth_cookie_secure: bool = Field(default=False, alias="AUTH_COOKIE_SECURE")
    auth_cookie_samesite_raw: str = Field(default="lax", alias="AUTH_COOKIE_SAMESITE")
    auth_cookie_domain: str | None = Field(default=None, alias="AUTH_COOKIE_DOMAIN")
    auth_cookie_path: str = Field(default="/", alias="AUTH_COOKIE_PATH")

    email_host: str | None = Field(default=None, alias="EMAIL_HOST")
    email_port: int = Field(default=587, alias="EMAIL_PORT")
    email_username: str | None = Field(default=None, alias="EMAIL_USERNAME")
    email_password: str | None = Field(default=None, alias="EMAIL_PASSWORD")
    email_sender_name: str | None = Field(default=None, alias="EMAIL_SENDER_NAME")
    email_sender_address: str | None = Field(default=None, alias="EMAIL_SENDER_ADDRESS")
    email_use_tls: bool = Field(default=True, alias="EMAIL_USE_TLS")
    email_use_ssl: bool = Field(default=False, alias="EMAIL_USE_SSL")
    block_client_communications: bool = Field(
        default=False,
        alias="BLOCK_CLIENT_COMMUNICATIONS",
    )

    google_application_credentials: str | None = Field(
        default=None,
        alias="GOOGLE_APPLICATION_CREDENTIALS",
    )
    google_sheets_spreadsheet_id: str | None = Field(
        default=None,
        alias="GOOGLE_SHEETS_SPREADSHEET_ID",
    )
    google_sheets_enabled: bool = Field(
        default=True,
        alias="GOOGLE_SHEETS_ENABLED",
    )
    google_sheets_workflow_devices_sheet: str = Field(
        default="Urzadzenia_magazyn",
        alias="GOOGLE_SHEETS_WORKFLOW_DEVICES_SHEET",
    )
    google_sheets_config_lock: bool = Field(
        default=False,
        alias="GOOGLE_SHEETS_CONFIG_LOCK",
    )
    google_sheets_test_spreadsheet_id: str | None = Field(
        default=None,
        alias="GOOGLE_SHEETS_TEST_SPREADSHEET_ID",
    )
    google_sheets_test_spreadsheet_title: str = Field(
        default="Zerowki_test",
        alias="GOOGLE_SHEETS_TEST_SPREADSHEET_TITLE",
    )
    google_sheets_expected_timezone: str = Field(
        default="Europe/Warsaw",
        alias="GOOGLE_SHEETS_EXPECTED_TIMEZONE",
    )
    device_sheet_outbox_scheduler_enabled: bool = Field(
        default=_PRODUCTION_ENV_SELECTED,
        alias="DEVICE_SHEET_OUTBOX_SCHEDULER_ENABLED",
    )
    device_sheet_outbox_interval_seconds: int = Field(
        default=60,
        alias="DEVICE_SHEET_OUTBOX_INTERVAL_SECONDS",
    )
    device_sheet_outbox_batch_size: int = Field(
        default=25,
        alias="DEVICE_SHEET_OUTBOX_BATCH_SIZE",
    )
    device_manual_reservation_default_days: int = Field(
        default=14,
        alias="DEVICE_MANUAL_RESERVATION_DEFAULT_DAYS",
    )
    workflow_sheet_status_cache_scheduler_enabled: bool = Field(
        default=True,
        alias="WORKFLOW_SHEET_STATUS_CACHE_SCHEDULER_ENABLED",
    )
    workflow_sheet_status_cache_refresh_interval_seconds: int = Field(
        default=900,
        alias="WORKFLOW_SHEET_STATUS_CACHE_REFRESH_INTERVAL_SECONDS",
    )
    workflow_sheet_status_cache_stale_after_seconds: int = Field(
        default=1800,
        alias="WORKFLOW_SHEET_STATUS_CACHE_STALE_AFTER_SECONDS",
    )
    contracts_workflow_maintenance_scheduler_enabled: bool = Field(
        default=True,
        alias="CONTRACTS_WORKFLOW_MAINTENANCE_SCHEDULER_ENABLED",
    )
    contracts_workflow_maintenance_interval_seconds: int = Field(
        default=3600,
        alias="CONTRACTS_WORKFLOW_MAINTENANCE_INTERVAL_SECONDS",
    )
    delivery_notifications_scheduler_enabled: bool = Field(
        default=True,
        alias="DELIVERY_NOTIFICATIONS_SCHEDULER_ENABLED",
    )
    delivery_notifications_interval_seconds: int = Field(
        default=86400,
        alias="DELIVERY_NOTIFICATIONS_INTERVAL_SECONDS",
    )
    delivery_files_root: str = Field(
        default="inbox/delivery/files",
        alias="DELIVERY_FILES_ROOT",
    )
    delivery_document_templates_root: str = Field(
        default="inbox/doku",
        alias="DELIVERY_DOCUMENT_TEMPLATES_ROOT",
    )

    mailbox_email_address: str | None = Field(default=None, alias="MAILBOX_EMAIL_ADDRESS")
    mailbox_email_password: str | None = Field(default=None, alias="MAILBOX_EMAIL_PASSWORD")
    mailbox_imap_host: str | None = Field(default=None, alias="MAILBOX_IMAP_HOST")
    mailbox_imap_port: int = Field(default=993, alias="MAILBOX_IMAP_PORT")
    mailbox_smtp_host: str | None = Field(default=None, alias="MAILBOX_SMTP_HOST")
    mailbox_smtp_port: int = Field(default=465, alias="MAILBOX_SMTP_PORT")
    mailbox_smtp_use_ssl: bool = Field(default=True, alias="MAILBOX_SMTP_USE_SSL")
    mailbox_smtp_use_starttls: bool = Field(default=False, alias="MAILBOX_SMTP_USE_STARTTLS")
    contracts_mailbox_scheduler_enabled: bool = Field(
        default=True,
        alias="CONTRACTS_MAILBOX_SCHEDULER_ENABLED",
    )
    contracts_mailbox_sync_interval_seconds: int = Field(
        default=300,
        alias="CONTRACTS_MAILBOX_SYNC_INTERVAL_SECONDS",
    )
    contracts_mailbox_sync_limit: int = Field(
        default=60,
        alias="CONTRACTS_MAILBOX_SYNC_LIMIT",
    )
    contracts_mailbox_sync_folder: str = Field(
        default="INBOX",
        alias="CONTRACTS_MAILBOX_SYNC_FOLDER",
    )
    contracts_mailbox_sync_timeout_seconds: int = Field(
        default=300,
        alias="CONTRACTS_MAILBOX_SYNC_TIMEOUT_SECONDS",
    )
    contracts_mailbox_sync_reprocess: bool = Field(
        default=False,
        alias="CONTRACTS_MAILBOX_SYNC_REPROCESS",
    )
    contracts_mailbox_audit_cleanup_enabled: bool = Field(
        default=False,
        alias="CONTRACTS_MAILBOX_AUDIT_CLEANUP_ENABLED",
    )
    contracts_mailbox_audit_cleanup_interval_seconds: int = Field(
        default=21600,
        alias="CONTRACTS_MAILBOX_AUDIT_CLEANUP_INTERVAL_SECONDS",
    )
    contracts_mailbox_audit_compact_after_days: int = Field(
        default=7,
        alias="CONTRACTS_MAILBOX_AUDIT_COMPACT_AFTER_DAYS",
    )
    contracts_mailbox_audit_compact_max_chars: int = Field(
        default=1000,
        alias="CONTRACTS_MAILBOX_AUDIT_COMPACT_MAX_CHARS",
    )
    contracts_mailbox_audit_delete_after_days: int = Field(
        default=90,
        alias="CONTRACTS_MAILBOX_AUDIT_DELETE_AFTER_DAYS",
    )
    contracts_mailbox_archive_root: str | None = Field(
        default=None,
        alias="CONTRACTS_MAILBOX_ARCHIVE_ROOT",
    )

    backup_execution_enabled: bool | None = Field(default=None, alias="BACKUP_EXECUTION_ENABLED")
    backup_scheduler_enabled: bool = Field(default=True, alias="BACKUP_SCHEDULER_ENABLED")
    backup_production_host: str = Field(default="", alias="BACKUP_PRODUCTION_HOST")
    backup_default_local_dir: str = Field(
        default="D:\\Backup_CTIP_MS_optima", alias="BACKUP_DEFAULT_LOCAL_DIR"
    )
    pg_dump_path: str | None = Field(default=None, alias="PG_DUMP_PATH")
    pg_restore_path: str | None = Field(default=None, alias="PG_RESTORE_PATH")
    backup_pg_dump_timeout_seconds: int = Field(
        default=900,
        alias="BACKUP_PG_DUMP_TIMEOUT_SECONDS",
    )
    backup_firebird_timeout_seconds: int = Field(
        default=7200,
        alias="BACKUP_FIREBIRD_TIMEOUT_SECONDS",
    )
    backup_optima_timeout_seconds: int = Field(
        default=7200,
        alias="BACKUP_OPTIMA_TIMEOUT_SECONDS",
    )
    firebird_gbak_path: str | None = Field(default=None, alias="FIREBIRD_GBAK_PATH")

    optima_sql_server_instance: str | None = Field(default=None, alias="OPTIMA_SQL_SERVER_INSTANCE")
    optima_sql_host: str | None = Field(default=None, alias="OPTIMA_SQL_HOST")
    optima_sql_host_ip: str | None = Field(default=None, alias="OPTIMA_SQL_HOST_IP")
    optima_sql_port: int = Field(default=1433, alias="OPTIMA_SQL_PORT")
    optima_sql_auth_mode: str | None = Field(default=None, alias="OPTIMA_SQL_AUTH_MODE")
    optima_sql_login: str | None = Field(default=None, alias="OPTIMA_SQL_LOGIN")
    optima_sql_password: str | None = Field(default=None, alias="OPTIMA_SQL_PASSWORD")
    optima_sqlcmd_path: str | None = Field(default=None, alias="OPTIMA_SQLCMD_PATH")
    optima_db_it_partner: str | None = Field(default=None, alias="OPTIMA_DB_IT_PARTNER")
    optima_db_ksero_partner: str | None = Field(default=None, alias="OPTIMA_DB_KSERO_PARTNER")
    optima_db_config: str | None = Field(default=None, alias="OPTIMA_DB_CONFIG")
    office365_tenant_id: str | None = Field(default=None, alias="OFFICE365_TENANT_ID")
    office365_client_id: str | None = Field(default=None, alias="OFFICE365_CLIENT_ID")
    office365_client_secret: str | None = Field(default=None, alias="OFFICE365_CLIENT_SECRET")
    office365_site_id: str | None = Field(default=None, alias="OFFICE365_SITE_ID")
    office365_drive_id: str | None = Field(default=None, alias="OFFICE365_DRIVE_ID")
    office365_folder_path: str | None = Field(default=None, alias="OFFICE365_FOLDER_PATH")
    office365_folder_ctip: str = Field(default="BackupKP/CTIP", alias="OFFICE365_FOLDER_CTIP")
    office365_folder_firebird_prod: str = Field(
        default="BackupKP/Menadzer_Serwisu/prod", alias="OFFICE365_FOLDER_FIREBIRD_PROD"
    )
    office365_folder_firebird_test: str = Field(
        default="BackupKP/Menadzer_Serwisu/test", alias="OFFICE365_FOLDER_FIREBIRD_TEST"
    )
    office365_folder_optima: str = Field(default="BackupKP/Optima", alias="OFFICE365_FOLDER_OPTIMA")

    model_config = SettingsConfigDict(
        env_file=SETTINGS_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_test_runtime(self) -> bool:
        """Określa, czy aplikacja działa w izolowanym profilu testowym."""
        return self.ctip_runtime_profile == "test"

    @property
    def database_url(self) -> str:
        """Zwraca asynchroniczny URL połączenia PostgreSQL."""
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    @property
    def dpd_effective_mode(self) -> str:
        """Zwraca jawny tryb DPD z fallbackiem dla historycznej flagi testowej."""
        configured = str(self.dpd_mode or "").strip().lower()
        if configured:
            return configured
        return "mock" if self.dpd_test_mode else "production"

    @property
    def shipping_test_firebird_writes_active(self) -> bool:
        """Dopuszcza zapis testowych wysyłek wyłącznie do lokalnej kopii Firebird."""
        host = self.fb_host.strip().lower()
        database_name = self.fb_database.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        return bool(
            self.shipping_test_firebird_writes
            and self.pg_database == "ctip_test"
            and self.fb_mode.strip().lower() == "network"
            and self.fb_allow_writes
            and self.dpd_effective_mode in {"mock", "demo"}
            and self.sms_test_mode
            and host
            in {
                "127.0.0.1",
                "localhost",
                "::1",
                "192.168.0.9",
                "firebird",
                "ctip-prod-mirror-firebird-1",
            }
            and "TEST" in database_name.upper()
        )

    @staticmethod
    def _resolve_host_ips(host: str) -> set[str]:
        """Zwraca zestaw adresów IP rozwiązywanych dla podanego hosta."""
        if not host:
            return set()
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return set()
        return {info[4][0] for info in infos if info and info[4]}

    @property
    def backup_execution_active(self) -> bool:
        """Określa, czy pełne wykonanie backupu jest aktywne dla bieżącego hosta."""
        if self.backup_execution_enabled is not None:
            return self.backup_execution_enabled

        production_host = (self.backup_production_host or "").strip()
        if not production_host:
            return False

        local_hostnames = {
            socket.gethostname().lower(),
            socket.getfqdn().lower(),
            "localhost",
        }
        if production_host.lower() in local_hostnames:
            return True

        production_ips = self._resolve_host_ips(production_host)
        local_ips = {
            "127.0.0.1",
            "::1",
        }
        local_ips |= self._resolve_host_ips(socket.gethostname())
        local_ips |= self._resolve_host_ips(socket.getfqdn())
        return bool(production_ips.intersection(local_ips))

    @staticmethod
    def _normalize_origin(value: str | None) -> str | None:
        """Normalizuje URL do postaci origin używanej przez CORS."""
        normalized = str(value or "").strip().rstrip("/")
        if not normalized or "://" not in normalized:
            return None
        parsed = urlsplit(normalized)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _extract_hostname(value: str | None) -> str | None:
        """Wyciąga nazwę hosta z pełnego URL lub zwraca None dla pustej wartości."""
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if "://" not in normalized:
            normalized = f"http://{normalized}"
        parsed = urlsplit(normalized)
        return parsed.hostname or None

    @property
    def cors_allowed_origins(self) -> list[str]:
        """Zwraca listę dozwolonych originów CORS dla paneli WWW."""
        values = [item.strip() for item in self.cors_allowed_origins_raw.split(",")]
        values.extend([self.admin_panel_url or "", self.form_public_base_url or ""])
        origins: list[str] = []
        for value in values:
            origin = self._normalize_origin(value)
            if origin and origin not in origins:
                origins.append(origin)
        return origins

    @property
    def public_form_trusted_hosts(self) -> list[str]:
        """Zwraca listę hostów akceptowanych przez publiczną aplikację formularzy."""
        hosts = ["localhost", "127.0.0.1", "::1", "testserver"]
        public_host = self._extract_hostname(self.form_public_base_url)
        if public_host and public_host not in hosts:
            hosts.append(public_host)
        return hosts

    def is_panel_client_allowed(self, client_host: str | None) -> bool:
        """Sprawdza, czy adres klienta należy do dozwolonej sieci panelu."""
        normalized = str(client_host or "").strip()
        if normalized in {"localhost", "testclient"}:
            return True
        try:
            address = ip_address(normalized)
        except ValueError:
            return False
        if getattr(address, "ipv4_mapped", None) is not None:
            address = address.ipv4_mapped
        for raw_network in self.panel_allowed_networks_raw.split(","):
            try:
                network = ip_network(raw_network.strip(), strict=False)
            except ValueError:
                continue
            if address.version == network.version and address in network:
                return True
        return False

    @property
    def auth_cookie_samesite(self) -> str:
        """Zwraca poprawną wartość SameSite dla ciasteczka sesji."""
        value = str(self.auth_cookie_samesite_raw or "").strip().lower()
        if value in {"lax", "strict", "none"}:
            return value
        return "lax"


@lru_cache(1)
def get_settings() -> Settings:
    """Zwraca singleton ustawień."""
    return Settings()


settings = get_settings()
