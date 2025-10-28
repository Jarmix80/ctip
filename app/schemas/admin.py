"""Schematy Pydantic dla modułu administratora."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class AdminLoginRequest(BaseModel):
    """Dane uwierzytelniające administratora."""

    email: EmailStr
    password: str = Field(min_length=1)
    remember_me: bool = False


class AdminLoginResponse(BaseModel):
    """Odpowiedź z tokenem sesji."""

    token: str
    expires_at: datetime


class AdminUserInfo(BaseModel):
    """Widok podstawowych danych administratora."""

    id: int
    email: EmailStr
    first_name: str | None
    last_name: str | None
    internal_ext: str | None
    role: str
    mobile_phone: str | None = None


class DatabaseConfigResponse(BaseModel):
    """Widok konfiguracji połączenia z PostgreSQL."""

    host: str
    port: int
    database: str
    user: str
    sslmode: str
    password_set: bool


class DatabaseConfigUpdate(BaseModel):
    """Żądanie aktualizacji konfiguracji bazy."""

    host: str
    port: int
    database: str
    user: str
    sslmode: str
    password: str | None = None


class CtipConfigResponse(BaseModel):
    """Widok konfiguracji centrali CTIP."""

    host: str
    port: int
    pin_set: bool


class CtipConfigUpdate(BaseModel):
    """Żądanie aktualizacji parametrów CTIP."""

    host: str
    port: int
    pin: str | None = None


class SmsConfigResponse(BaseModel):
    """Widok konfiguracji SerwerSMS."""

    default_sender: str
    sms_type: str
    api_url: str
    api_username: str | None
    api_token_set: bool
    api_password_set: bool
    test_mode: bool


class SmsConfigUpdate(BaseModel):
    """Żądanie aktualizacji parametrów SerwerSMS."""

    default_sender: str
    sms_type: str
    api_url: str
    api_username: str | None = None
    api_token: str | None = None
    api_password: str | None = None
    test_mode: bool


class EmailConfigResponse(BaseModel):
    """Widok konfiguracji serwera SMTP."""

    host: str | None
    port: int
    username: str | None
    sender_name: str | None
    sender_address: EmailStr | None
    use_tls: bool
    use_ssl: bool
    password_set: bool


class EmailConfigUpdate(BaseModel):
    """Żądanie aktualizacji parametrów SMTP."""

    host: str
    port: int
    username: str | None = None
    password: str | None = None
    sender_name: str | None = None
    sender_address: EmailStr | None = None
    use_tls: bool = True
    use_ssl: bool = False

    @model_validator(mode="after")
    def validate_tls_ssl(self) -> EmailConfigUpdate:
        if self.use_tls and self.use_ssl:
            raise ValueError("Nie można jednocześnie używać STARTTLS i SSL.")
        return self


class EmailTestRequest(BaseModel):
    """Parametry testu SMTP (opcjonalne nadpisania)."""

    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    sender_name: str | None = None
    sender_address: EmailStr | None = None
    use_tls: bool | None = None
    use_ssl: bool | None = None
    test_recipient: EmailStr | None = None
    test_subject: str | None = Field(default=None, max_length=200)
    test_body: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_flags(self) -> EmailTestRequest:
        if (self.use_tls or False) and (self.use_ssl or False):
            raise ValueError("Nie można jednocześnie używać STARTTLS i SSL.")
        return self


class EmailTestResponse(BaseModel):
    """Rezultat testu połączenia SMTP."""

    success: bool
    message: str


class AdminUserSummary(BaseModel):
    """Skrócony widok użytkownika panelu."""

    id: int
    email: EmailStr
    mobile_phone: str | None
    first_name: str | None
    last_name: str | None
    internal_ext: str | None
    role: Literal["admin", "operator"]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
    sessions_active: int

    model_config = ConfigDict(from_attributes=True)


class AdminUserDetail(AdminUserSummary):
    """Szczegółowy widok użytkownika wraz z aktywnymi sesjami."""

    sessions: list[AdminUserSessionInfo]


class AdminUserSessionInfo(BaseModel):
    """Informacje o sesji administratora."""

    id: int
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    client_ip: str | None
    user_agent: str | None

    model_config = ConfigDict(from_attributes=True)


class AdminUserListResponse(BaseModel):
    """Lista użytkowników panelu."""

    items: list[AdminUserSummary]


class AdminUserCreate(BaseModel):
    """Żądanie utworzenia użytkownika panelu."""

    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    internal_ext: str | None = None
    role: Literal["admin", "operator"] = "operator"
    password: str | None = None
    mobile_phone: str = Field(min_length=6, max_length=32, pattern=r"^[0-9+\s\-]+$")


class AdminUserCreateResponse(BaseModel):
    """Odpowiedź po utworzeniu użytkownika (z hasłem jednorazowym)."""

    user: AdminUserSummary
    password: str


class AdminUserUpdate(BaseModel):
    """Aktualizacja danych użytkownika."""

    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    internal_ext: str | None = None
    role: Literal["admin", "operator"] = "operator"
    mobile_phone: str | None = Field(
        default=None, min_length=6, max_length=32, pattern=r"^[0-9+\s\-]+$"
    )


class AdminUserResetPasswordResponse(BaseModel):
    """Wynik resetu hasła."""

    password: str


class AdminUserStatusUpdate(BaseModel):
    """Zmiana statusu aktywności konta."""

    is_active: bool


AdminUserDetail.model_rebuild()
