"""Konfiguracja aplikacji oparta na zmiennych środowiskowych."""

import socket
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parametry działania backendu."""

    app_title: str = "CTIP API"
    app_version: str = "0.2.2"

    pbx_host: str = Field(default="192.168.0.11", alias="PBX_HOST")
    pbx_port: int = Field(default=5524, alias="PBX_PORT")
    pbx_pin: str = Field(default="1234", alias="PBX_PIN")

    pg_host: str = Field(default="192.168.0.8", alias="PGHOST")
    pg_port: int = Field(default=5433, alias="PGPORT")
    pg_database: str = Field(default="ctip", alias="PGDATABASE")
    pg_user: str = Field(default="appuser", alias="PGUSER")
    pg_password: str = Field(default="change_me", alias="PGPASSWORD")
    pg_sslmode: str = Field(default="disable", alias="PGSSLMODE")

    fb_host: str = Field(default="127.0.0.1", alias="FB_HOST")
    fb_port: int = Field(default=3050, alias="FB_PORT")
    fb_mode: str = Field(default="network", alias="FB_MODE")
    fb_database: str = Field(default="D:/BAZA_MS_KP/BAZAMS.FDB", alias="FB_DATABASE")
    fb_user: str = Field(default="SYSDBA", alias="FB_USER")
    fb_password: str = Field(default="masterkey", alias="FB_PASSWORD")
    fb_charset: str = Field(default="WIN1250", alias="FB_CHARSET")
    fb_role: str | None = Field(default=None, alias="FB_ROLE")
    fb_local_copy_path: str = Field(
        default="inbox/firebird/menadzer_serwisu.fdb", alias="FB_LOCAL_COPY_PATH"
    )
    fb_v_host: str = Field(default="192.168.0.8", alias="FB_V_HOST")
    fb_v_port: int = Field(default=3050, alias="FB_V_PORT")
    fb_v_database: str = Field(default="D:\\bazavmantenance\\BAZA_CPC.FDB", alias="FB_V_DATABASE")
    fb_v_user: str = Field(default="SYSDBA", alias="FB_V_USER")
    fb_v_password: str = Field(default="masterkey", alias="FB_V_PASSWORD")
    fb_v_charset: str = Field(default="WIN1250", alias="FB_V_CHARSET")
    fb_v_role: str | None = Field(default=None, alias="FB_V_ROLE")

    kp_csv_directory: str = Field(default="inbox/ewidencja", alias="KP_CSV_DIRECTORY")
    kp_csv_pattern: str = Field(default="DPLAC*.csv", alias="KP_CSV_PATTERN")
    kp_email_lookback_months: int = Field(default=5, alias="KP_EMAIL_LOOKBACK_MONTHS")
    fb_allow_writes: bool = Field(default=False, alias="FB_ALLOW_WRITES")
    fb_warehouse_client_id: int = Field(default=656, alias="FB_WAREHOUSE_CLIENT_ID")
    fb_warehouse_id: int = Field(default=28, alias="FB_WAREHOUSE_ID")

    sms_default_sender: str = Field(default="KseroPartner", alias="SMS_DEFAULT_SENDER")
    sms_type: str = Field(default="eco+", alias="SMS_TYPE")
    sms_api_url: str = Field(default="https://api2.serwersms.pl", alias="SMS_API_URL")
    sms_api_token: str | None = Field(default=None, alias="SMS_API_TOKEN")
    sms_api_username: str | None = Field(default=None, alias="SMS_API_USERNAME")
    sms_api_password: str | None = Field(default=None, alias="SMS_API_PASSWORD")
    sms_test_mode: bool = Field(default=True, alias="SMS_TEST_MODE")

    admin_secret_key: str | None = Field(default=None, alias="ADMIN_SECRET_KEY")
    admin_session_ttl_minutes: int = Field(default=60, alias="ADMIN_SESSION_TTL_MINUTES")
    admin_session_remember_hours: int = Field(default=72, alias="ADMIN_SESSION_REMEMBER_HOURS")
    admin_panel_url: str | None = Field(
        default="http://localhost:8000/admin", alias="ADMIN_PANEL_URL"
    )
    form_public_base_url: str | None = Field(default=None, alias="FORM_PUBLIC_BASE_URL")

    email_host: str | None = Field(default=None, alias="EMAIL_HOST")
    email_port: int = Field(default=587, alias="EMAIL_PORT")
    email_username: str | None = Field(default=None, alias="EMAIL_USERNAME")
    email_password: str | None = Field(default=None, alias="EMAIL_PASSWORD")
    email_sender_name: str | None = Field(default=None, alias="EMAIL_SENDER_NAME")
    email_sender_address: str | None = Field(default=None, alias="EMAIL_SENDER_ADDRESS")
    email_use_tls: bool = Field(default=True, alias="EMAIL_USE_TLS")
    email_use_ssl: bool = Field(default=False, alias="EMAIL_USE_SSL")

    backup_execution_enabled: bool | None = Field(default=None, alias="BACKUP_EXECUTION_ENABLED")
    backup_scheduler_enabled: bool = Field(default=True, alias="BACKUP_SCHEDULER_ENABLED")
    backup_production_host: str = Field(default="192.168.0.8", alias="BACKUP_PRODUCTION_HOST")
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        """Zwraca asynchroniczny URL połączenia PostgreSQL."""
        return (
            f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}"
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


@lru_cache(1)
def get_settings() -> Settings:
    """Zwraca singleton ustawień."""
    return Settings()


settings = get_settings()
