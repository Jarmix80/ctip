"""Konfiguracja workera z domyślnie wyłączonymi integracjami."""

import ipaddress
import socket
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import SETTINGS_ENV_FILE, settings


class TelemetrySettings(BaseSettings):
    """Parametry niezależnego, wyłącznie odczytowego dostępu do źródeł."""

    model_config = SettingsConfigDict(
        env_file=(SETTINGS_ENV_FILE, SETTINGS_ENV_FILE + ".telemetry"), extra="ignore"
    )
    enabled: bool = Field(False, alias="TELEMETRY_ENABLED")
    report_root: str = Field("", alias="TELEMETRY_REPORT_ROOT")
    dplac_root: str = Field("", alias="TELEMETRY_DPLAC_ROOT")
    csv_timezone: str = Field("Europe/Warsaw", alias="TELEMETRY_CSV_TIMEZONE")
    mail_timezone: str = Field("", alias="TELEMETRY_MAIL_TIMEZONE")
    mail_enabled: bool = Field(False, alias="TELEMETRY_MAIL_ENABLED")
    vm_enabled: bool = Field(False, alias="TELEMETRY_VM_ENABLED")
    ms_enabled: bool = Field(False, alias="TELEMETRY_MS_ENABLED")
    ms_cpc_enabled: bool = Field(False, alias="TELEMETRY_MS_CPC_ENABLED")
    history_years: int = Field(3, ge=1, le=10, alias="TELEMETRY_HISTORY_YEARS")
    vm_host: str = Field(settings.fb_v_host, alias="TELEMETRY_VM_HOST")
    vm_database: str = Field(settings.fb_v_database, alias="TELEMETRY_VM_DATABASE")
    vm_charset: str = Field("UTF8", alias="TELEMETRY_VM_CHARSET")
    vm_user: str = Field("", alias="TELEMETRY_VM_USER")
    vm_password: SecretStr = Field(SecretStr(""), alias="TELEMETRY_VM_PASSWORD")
    ms_user: str = Field("", alias="TELEMETRY_MS_USER")
    ms_password: SecretStr = Field(SecretStr(""), alias="TELEMETRY_MS_PASSWORD")
    printradar_dsn: SecretStr = Field(SecretStr(""), alias="TELEMETRY_PRINTRADAR_DSN")
    email_address: str = Field("", alias="REMOTE_EMAIL_ADDRESS")
    email_password: SecretStr = Field(SecretStr(""), alias="REMOTE_EMAIL_PASSWORD")
    imap_host: str = Field("", alias="REMOTE_IMAP_HOST")
    imap_port: int = Field(993, alias="REMOTE_IMAP_PORT")
    imap_folder: str = Field("INBOX", alias="TELEMETRY_IMAP_FOLDER")
    page_size: int = Field(500, ge=1, le=2000, alias="TELEMETRY_PAGE_SIZE")
    max_pages: int = Field(4, ge=1, le=100, alias="TELEMETRY_MAX_PAGES")
    max_file_bytes: int = Field(32 * 1024 * 1024, ge=1024, alias="TELEMETRY_MAX_FILE_BYTES")
    stability_seconds: int = Field(60, ge=60, alias="TELEMETRY_STABILITY_SECONDS")


def check_test_host(host: str):
    """Blokuje dostęp testowego workera do sieci produkcyjnej i usług publicznych."""
    if settings.ctip_runtime_profile == "production":
        return
    addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    for address in addresses:
        parsed = ipaddress.ip_address(address[4][0])
        if not parsed.is_loopback and parsed not in ipaddress.ip_network("172.28.252.0/24"):
            raise ValueError("test_source_not_local")


def validate_runtime(config: TelemetrySettings):
    """Wymusza jawny profil, lokalną bazę testową i oddzielne konta źródeł."""
    if not config.enabled:
        raise ValueError("telemetry_disabled")
    if settings.ctip_runtime_profile == "test":
        if settings.pg_database != "ctip_test" or not settings.sms_test_mode:
            raise ValueError("unsafe_test_database")
        check_test_host(settings.pg_host)
    elif Path(SETTINGS_ENV_FILE).name != ".env":
        raise ValueError("production_requires_env")
    if config.vm_enabled:
        check_test_host(config.vm_host)
        if not config.vm_user or config.vm_user.upper() == "SYSDBA":
            raise ValueError("vmaintenance_readonly_user_required")
    if config.ms_enabled or config.ms_cpc_enabled:
        check_test_host(settings.fb_host)
        if not config.ms_user or config.ms_user.upper() == "SYSDBA":
            raise ValueError("ms_readonly_user_required")
    if config.mail_enabled:
        check_test_host(config.imap_host)
    if config.printradar_dsn.get_secret_value():
        from psycopg.conninfo import conninfo_to_dict

        values = conninfo_to_dict(config.printradar_dsn.get_secret_value())
        for host in (values.get("host") or "127.0.0.1").split(","):
            check_test_host(host)
        if values.get("hostaddr"):
            for host in values["hostaddr"].split(","):
                check_test_host(host)
