"""Konfiguracja aplikacji oparta na zmiennych środowiskowych."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parametry działania backendu."""

    app_title: str = "CTIP API"
    app_version: str = "0.1.0"

    pbx_host: str = Field(default="192.168.0.11", alias="PBX_HOST")
    pbx_port: int = Field(default=5524, alias="PBX_PORT")
    pbx_pin: str = Field(default="1234", alias="PBX_PIN")

    pg_host: str = Field(default="192.168.0.8", alias="PGHOST")
    pg_port: int = Field(default=5433, alias="PGPORT")
    pg_database: str = Field(default="ctip", alias="PGDATABASE")
    pg_user: str = Field(default="appuser", alias="PGUSER")
    pg_password: str = Field(default="change_me", alias="PGPASSWORD")
    pg_sslmode: str = Field(default="disable", alias="PGSSLMODE")

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

    email_host: str | None = Field(default=None, alias="EMAIL_HOST")
    email_port: int = Field(default=587, alias="EMAIL_PORT")
    email_username: str | None = Field(default=None, alias="EMAIL_USERNAME")
    email_password: str | None = Field(default=None, alias="EMAIL_PASSWORD")
    email_sender_name: str | None = Field(default=None, alias="EMAIL_SENDER_NAME")
    email_sender_address: str | None = Field(default=None, alias="EMAIL_SENDER_ADDRESS")
    email_use_tls: bool = Field(default=True, alias="EMAIL_USE_TLS")
    email_use_ssl: bool = Field(default=False, alias="EMAIL_USE_SSL")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        """Zwraca asynchroniczny URL połączenia PostgreSQL."""
        return (
            f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )


@lru_cache(1)
def get_settings() -> Settings:
    """Zwraca singleton ustawień."""
    return Settings()


settings = get_settings()
