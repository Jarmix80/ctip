"""Schematy Pydantic dla modułu administratora."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

PanelSection = Literal["admin", "operator", "generator", "delivery", "shipping"]
PanelRole = Literal["admin", "operator", "serwisant"]


class EnvBackedConfigMetadata(BaseModel):
    """Metadane sekcji, których źródłem prawdy jest plik `.env`."""

    source: Literal["env"] = "env"
    editable: bool = False
    lock_reason: str | None = None


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
    is_salesperson: bool = False
    sections: list[PanelSection] = Field(default_factory=list)


class DatabaseConfigResponse(EnvBackedConfigMetadata):
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


class FirebirdConfigResponse(EnvBackedConfigMetadata):
    """Widok konfiguracji połączenia z bazą Firebird."""

    mode: Literal["network", "local"] = "network"
    host: str
    port: int
    database: str
    user: str
    charset: str
    role: str | None
    local_copy_path: str
    allow_writes: bool
    password_set: bool


class FirebirdConfigUpdate(BaseModel):
    """Żądanie aktualizacji konfiguracji Firebird."""

    mode: Literal["network", "local"] = "network"
    host: str
    port: int
    database: str
    user: str
    charset: str = "WIN1250"
    role: str | None = None
    local_copy_path: str
    allow_writes: bool | None = None
    password: str | None = None


class FirebirdTestRequest(BaseModel):
    """Parametry testu Firebird (opcjonalne nadpisania)."""

    mode: Literal["network", "local"] | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    local_copy_path: str | None = None
    user: str | None = None
    password: str | None = None
    charset: str | None = None
    role: str | None = None


class FirebirdTestResponse(BaseModel):
    """Rezultat testu połączenia Firebird."""

    success: bool
    message: str
    engine_version: str | None = None


class GoogleSheetsConfigResponse(EnvBackedConfigMetadata):
    """Widok konfiguracji Google Sheets dla synchronizacji FLOW."""

    enabled: bool
    credentials_path: str
    spreadsheet_id: str
    workflow_devices_worksheet: str


class GoogleSheetsConfigUpdate(BaseModel):
    """Żądanie aktualizacji konfiguracji Google Sheets dla FLOW."""

    enabled: bool = True
    credentials_path: str = Field(default="")
    spreadsheet_id: str = Field(default="")
    workflow_devices_worksheet: str = Field(default="Urzadzenia_magazyn", max_length=200)

    @field_validator("credentials_path", "spreadsheet_id", "workflow_devices_worksheet")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class GoogleSheetsTestRequest(BaseModel):
    """Parametry testu Google Sheets (opcjonalne nadpisania)."""

    enabled: bool | None = None
    credentials_path: str | None = Field(default=None, max_length=1000)
    spreadsheet_id: str | None = Field(default=None, max_length=500)
    workflow_devices_worksheet: str | None = Field(default=None, max_length=200)

    @field_validator("credentials_path", "spreadsheet_id", "workflow_devices_worksheet")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class GoogleSheetsTestResponse(BaseModel):
    """Rezultat testu połączenia z Google Sheets dla FLOW."""

    success: bool
    message: str
    service_account_email: str | None = None
    spreadsheet_title: str | None = None
    worksheet_found: bool = False
    worksheet_title: str | None = None
    missing_headers: list[str] = Field(default_factory=list)


class GoogleSheetsBootstrapResponse(BaseModel):
    """Rezultat przygotowania nagłówków FLOW w Google Sheets."""

    success: bool
    message: str
    service_account_email: str | None = None
    spreadsheet_title: str | None = None
    worksheet_title: str | None = None
    added_headers: list[str] = Field(default_factory=list)
    existing_headers: list[str] = Field(default_factory=list)


class FirebirdVMaintenanceConfigResponse(EnvBackedConfigMetadata):
    """Widok konfiguracji połączenia z bazą Firebird v-maintenance."""

    host: str
    port: int
    database: str
    user: str
    charset: str
    role: str | None
    password_set: bool


class FirebirdVMaintenanceConfigUpdate(BaseModel):
    """Żądanie aktualizacji konfiguracji Firebird v-maintenance."""

    host: str
    port: int
    database: str
    user: str
    charset: str = "WIN1250"
    role: str | None = None
    password: str | None = None


class FirebirdVMaintenanceTestRequest(BaseModel):
    """Parametry testu Firebird v-maintenance (opcjonalne nadpisania)."""

    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = None
    charset: str | None = None
    role: str | None = None


class KpRepairSourceConfigResponse(BaseModel):
    """Widok konfiguracji źródeł wejściowych dla naprawy KP."""

    csv_directory: str
    csv_pattern: str
    email_lookback_months: int
    csv_directory_source: Literal["env"] = "env"
    csv_pattern_source: Literal["env"] = "env"
    csv_editable: bool = False
    email_lookback_source: Literal["admin", "env"] = "env"
    email_lookback_editable: bool = True
    lock_reason: str | None = None


class KpRepairSourceConfigUpdate(BaseModel):
    """Żądanie aktualizacji źródeł wejściowych dla naprawy KP."""

    csv_directory: str
    csv_pattern: str = "DPLAC*.csv"
    email_lookback_months: int = Field(default=5, ge=0, le=36)


class KpRepairCsvTestResponse(BaseModel):
    """Rezultat testu katalogu CSV dla naprawy KP."""

    success: bool
    message: str
    directory_exists: bool
    files_found: int
    latest_file: str | None = None


class KpRepairSummaryResponse(BaseModel):
    """Podsumowanie źródeł i aktualnych oznaczeń KP."""

    marker_counts: dict[str, int]
    source_counts: dict[str, int]
    matched_counts: dict[str, int]
    latest_csv_file: str | None = None
    report_file: str | None = None


class KpRepairActionRequest(BaseModel):
    """Parametry akcji naprawczych KP."""

    commit: bool = True
    email_lookback_months: int | None = Field(default=None, ge=0, le=36)


class KpRepairActionResponse(BaseModel):
    """Wynik akcji naprawy KP."""

    success: bool
    message: str
    commit: bool
    candidates: int
    updated: int
    skipped: int
    errors: int
    marker_counts_before: dict[str, int]
    marker_counts_after: dict[str, int]
    source_counts: dict[str, int] | None = None
    report_file: str | None = None
    map_file: str | None = None
    rollback_file: str | None = None


class CtipConfigResponse(EnvBackedConfigMetadata):
    """Widok konfiguracji centrali CTIP."""

    host: str
    port: int
    pin_set: bool


class CtipConfigUpdate(BaseModel):
    """Żądanie aktualizacji parametrów CTIP."""

    host: str
    port: int
    pin: str | None = None


class SmsConfigResponse(EnvBackedConfigMetadata):
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


class EmailConfigResponse(EnvBackedConfigMetadata):
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


class FormHandlingConfigResponse(BaseModel):
    """Widok konfiguracji obslugi formularza."""

    public_base_url: str
    invite_sms_template: str
    invite_email_subject: str
    invite_email_body: str
    submission_email_subject: str
    submission_email_body: str
    owner_sms_template: str


class FormHandlingConfigUpdate(BaseModel):
    """Żądanie aktualizacji ustawień obsługi formularza."""

    public_base_url: str = Field(min_length=1, max_length=500)
    invite_sms_template: str = Field(min_length=1, max_length=600)
    invite_email_subject: str = Field(min_length=1, max_length=200)
    invite_email_body: str = Field(min_length=1, max_length=4000)
    submission_email_subject: str = Field(min_length=1, max_length=200)
    submission_email_body: str = Field(min_length=1, max_length=4000)
    owner_sms_template: str = Field(min_length=1, max_length=600)

    @field_validator(
        "public_base_url",
        "invite_sms_template",
        "invite_email_subject",
        "invite_email_body",
        "submission_email_subject",
        "submission_email_body",
        "owner_sms_template",
    )
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Pole nie może być puste.")
        return normalized

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Adres publiczny formularza musi być pełnym adresem HTTP lub HTTPS.")
        return value.rstrip("/")


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


class AdminUserImapConfig(BaseModel):
    """Widok konfiguracji IMAP przypisanej do użytkownika."""

    enabled: bool = False
    email: EmailStr | None = None
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=993, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    use_ssl: bool = True
    folder: str | None = Field(default="INBOX", max_length=255)
    password_set: bool = False

    @field_validator("host", "username", "folder", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdminUserImapUpdate(BaseModel):
    """Aktualizacja konfiguracji IMAP użytkownika (admin-only)."""

    enabled: bool | None = None
    email: EmailStr | None = None
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    use_ssl: bool | None = None
    folder: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=4000)
    clear_password: bool = False

    @field_validator("host", "username", "folder", "password", mode="before")
    @classmethod
    def _strip_optional_update_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdminUserSummary(BaseModel):
    """Skrócony widok użytkownika panelu."""

    id: int
    email: EmailStr
    mobile_phone: str | None
    first_name: str | None
    last_name: str | None
    internal_ext: str | None
    role: PanelRole
    is_salesperson: bool = False
    crm_sales_sms_enabled: bool = False
    crm_sales_email_enabled: bool = False
    crm_operations_sms_enabled: bool = False
    crm_operations_email_enabled: bool = False
    firebird_app_user_id: int | None = None
    firebird_app_user_login: str | None = None
    sections: list[PanelSection] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
    sessions_active: int
    imap: AdminUserImapConfig | None = None

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
    role: PanelRole = "operator"
    is_salesperson: bool = False
    crm_sales_sms_enabled: bool = False
    crm_sales_email_enabled: bool = False
    crm_operations_sms_enabled: bool = False
    crm_operations_email_enabled: bool = False
    firebird_app_user_id: int | None = Field(default=None, ge=1)
    sections: list[PanelSection] | None = None
    password: str | None = None
    mobile_phone: str = Field(min_length=6, max_length=32, pattern=r"^[0-9+\s\-]+$")
    imap: AdminUserImapUpdate | None = None


class AdminUserCreateResponse(BaseModel):
    """Odpowiedź po utworzeniu użytkownika (z hasłem jednorazowym)."""

    user: AdminUserSummary
    password: str
    sms_queued: bool
    sms_recipient: str | None = None


class AdminUserUpdate(BaseModel):
    """Aktualizacja danych użytkownika."""

    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    internal_ext: str | None = None
    role: PanelRole = "operator"
    is_salesperson: bool = False
    crm_sales_sms_enabled: bool = False
    crm_sales_email_enabled: bool = False
    crm_operations_sms_enabled: bool = False
    crm_operations_email_enabled: bool = False
    firebird_app_user_id: int | None = Field(default=None, ge=1)
    sections: list[PanelSection] | None = None
    mobile_phone: str | None = Field(
        default=None, min_length=6, max_length=32, pattern=r"^[0-9+\s\-]+$"
    )
    imap: AdminUserImapUpdate | None = None


class FirebirdMsUserOption(BaseModel):
    """Opcja użytkownika Menadżera Serwisu do mapowania konta CTIP."""

    id: int
    login_user: str
    workstation: str | None = None
    app_name: str | None = None
    label: str


class FirebirdMsUserListResponse(BaseModel):
    """Lista użytkowników Menadżera Serwisu dostępnych do powiązania."""

    items: list[FirebirdMsUserOption] = Field(default_factory=list)


class PortalLoginResponse(BaseModel):
    """Odpowiedź logowania centralnego ze wskazaniem sekcji."""

    token: str
    expires_at: datetime
    sections: list[PanelSection] = Field(default_factory=list)


class PortalUserInfo(BaseModel):
    """Dane zalogowanego użytkownika dla strony głównej."""

    id: int
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    role: str
    is_salesperson: bool = False
    sections: list[PanelSection] = Field(default_factory=list)


class PortalProfile(BaseModel):
    """Widok danych profilu użytkownika po zalogowaniu centralnym."""

    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    internal_ext: str | None = None
    mobile_phone: str | None = None
    role: str
    is_salesperson: bool = False
    sections: list[PanelSection] = Field(default_factory=list)


class PortalProfileUpdate(BaseModel):
    """Aktualizacja danych profilu użytkownika."""

    email: EmailStr
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    internal_ext: str | None = Field(default=None, max_length=16)
    mobile_phone: str | None = Field(
        default=None, min_length=6, max_length=32, pattern=r"^[0-9+\s\-]+$"
    )

    @field_validator("first_name", "last_name", "internal_ext", "mobile_phone", mode="before")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class PortalPasswordChangeRequest(BaseModel):
    """Żądanie zmiany hasła użytkownika po zalogowaniu centralnym."""

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=9, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password_policy(cls, value: str) -> str:
        if re.search(r"[A-Z]", value) is None:
            raise ValueError(
                "Hasło musi mieć co najmniej 9 znaków oraz zawierać co najmniej jedną wielką literę, jedną cyfrę i jeden znak specjalny."
            )
        if re.search(r"\d", value) is None:
            raise ValueError(
                "Hasło musi mieć co najmniej 9 znaków oraz zawierać co najmniej jedną wielką literę, jedną cyfrę i jeden znak specjalny."
            )
        if re.search(r"[^A-Za-z0-9]", value) is None:
            raise ValueError(
                "Hasło musi mieć co najmniej 9 znaków oraz zawierać co najmniej jedną wielką literę, jedną cyfrę i jeden znak specjalny."
            )
        return value


class AdminUserResetPasswordResponse(BaseModel):
    """Wynik resetu hasła."""

    password: str
    sms_queued: bool
    sms_recipient: str | None = None


class AdminUserStatusUpdate(BaseModel):
    """Zmiana statusu aktywności konta."""

    is_active: bool


AdminUserDetail.model_rebuild()
