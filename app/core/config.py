"""Konfiguracja aplikacji oparta na zmiennych środowiskowych."""

import socket
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_TEST_FILE = _PROJECT_ROOT / ".env.test"
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Parametry działania backendu."""

    app_title: str = "CTIP API"
    app_version: str = "0.2.13"

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
    openai_api_chat_kp: str | None = Field(default=None, alias="OPENAI_API_CHAT_KP")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    admin_secret_key: str | None = Field(default=None, alias="ADMIN_SECRET_KEY")
    admin_session_ttl_minutes: int = Field(default=60, alias="ADMIN_SESSION_TTL_MINUTES")
    admin_session_remember_hours: int = Field(default=72, alias="ADMIN_SESSION_REMEMBER_HOURS")
    admin_panel_url: str | None = Field(
        default="http://localhost:8000/admin", alias="ADMIN_PANEL_URL"
    )
    form_public_base_url: str | None = Field(default=None, alias="FORM_PUBLIC_BASE_URL")
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

    google_application_credentials: str | None = Field(
        default=None,
        alias="GOOGLE_APPLICATION_CREDENTIALS",
    )
    google_sheets_spreadsheet_id: str | None = Field(
        default=None,
        alias="GOOGLE_SHEETS_SPREADSHEET_ID",
    )
    google_sheets_workflow_devices_sheet: str = Field(
        default="Urzadzenia_magazyn",
        alias="GOOGLE_SHEETS_WORKFLOW_DEVICES_SHEET",
    )
    google_sheets_config_lock: bool = Field(
        default=False,
        alias="GOOGLE_SHEETS_CONFIG_LOCK",
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

    optima_sql_server_instance: str | None = Field(default=None, alias="OPTIMA_SQL_SERVER_INSTANCE")
    optima_sql_host: str | None = Field(default=None, alias="OPTIMA_SQL_HOST")
    optima_sql_host_ip: str | None = Field(default=None, alias="OPTIMA_SQL_HOST_IP")
    optima_sql_port: int = Field(default=1433, alias="OPTIMA_SQL_PORT")
    optima_sql_auth_mode: str | None = Field(default=None, alias="OPTIMA_SQL_AUTH_MODE")
    optima_sql_login: str | None = Field(default=None, alias="OPTIMA_SQL_LOGIN")
    optima_sql_password: str | None = Field(default=None, alias="OPTIMA_SQL_PASSWORD")
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
        env_file=(str(_ENV_TEST_FILE), str(_ENV_FILE)),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        """Zwraca asynchroniczny URL połączenia PostgreSQL."""
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
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
