"""Schematy Pydantic dla modułu kopii zapasowych."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class BackupHistoryEntry(BaseModel):
    """Pojedyncza pozycja historii kopii zapasowych."""

    name: str
    size_bytes: int | None
    modified_at: datetime | None
    status: str
    checksum: str | None = None
    confirmed: bool = False


class BackupHistoryResponse(BaseModel):
    """Lista kopii zapasowych dostępnych w systemie."""

    generated_at: datetime
    items: list[BackupHistoryEntry]
    note: str | None = None


class BackupConfigResponse(BaseModel):
    """Konfiguracja modułu kopii zapasowych."""

    schedule_morning: str
    schedule_evening: str
    retention_local_copies: int
    retention_cloud_copies: int
    archive_ctip_files: bool
    archive_ctip_db: bool
    archive_firebird_prod: bool
    archive_firebird_test: bool
    archive_optima: bool
    storage_mode: str
    local_directory: str
    network_directory: str | None = None
    cloud_provider: str
    cloud_only_evening: bool
    office_tenant_id: str | None = None
    office_client_id: str | None = None
    office_site_id: str | None = None
    office_drive_id: str | None = None
    office_folder_path: str | None = None
    office_folder_ctip: str
    office_folder_firebird_prod: str
    office_folder_firebird_test: str
    office_folder_optima: str
    office_client_secret_set: bool = False
    optima_server_instance: str | None = None
    optima_host: str | None = None
    optima_port: int = 1433
    optima_auth_mode: str | None = None
    optima_login: str | None = None
    optima_password_set: bool = False
    optima_db_it_partner: str | None = None
    optima_db_ksero_partner: str | None = None
    optima_db_config: str | None = None
    execution_enabled: bool = False
    integration_source: str = "env"
    integration_editable: bool = False
    operational_editable: bool = True
    lock_reason: str | None = None


class BackupConfigUpdate(BaseModel):
    """Aktualizacja konfiguracji kopii zapasowych."""

    schedule_morning: str = "06:00"
    schedule_evening: str = "20:00"
    retention_local_copies: int = Field(default=14, ge=1, le=365)
    retention_cloud_copies: int = Field(default=7, ge=1, le=365)
    archive_ctip_files: bool = True
    archive_ctip_db: bool = True
    archive_firebird_prod: bool = True
    archive_firebird_test: bool = True
    archive_optima: bool = True
    storage_mode: str = Field(default="local", pattern="^(local|network)$")
    local_directory: str
    network_directory: str | None = None
    cloud_provider: str = Field(default="office365", pattern="^(none|office365)$")
    cloud_only_evening: bool = True
    office_tenant_id: str | None = None
    office_client_id: str | None = None
    office_site_id: str | None = None
    office_client_secret: str | None = None
    office_drive_id: str | None = None
    office_folder_path: str | None = None
    office_folder_ctip: str = "BackupKP/CTIP"
    office_folder_firebird_prod: str = "BackupKP/Menadzer_Serwisu/prod"
    office_folder_firebird_test: str = "BackupKP/Menadzer_Serwisu/test"
    office_folder_optima: str = "BackupKP/Optima"
    optima_server_instance: str | None = None
    optima_host: str | None = None
    optima_port: int = Field(default=1433, ge=1, le=65535)
    optima_auth_mode: str = Field(default="mixed", pattern="^(mixed|sql|windows)$")
    optima_login: str | None = None
    optima_password: str | None = None
    optima_db_it_partner: str | None = None
    optima_db_ksero_partner: str | None = None
    optima_db_config: str | None = None

    @field_validator("schedule_morning", "schedule_evening")
    @classmethod
    def validate_hour_format(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("Godzina musi mieć format HH:MM.")
        hh, mm = value.split(":")
        if not hh.isdigit() or not mm.isdigit():
            raise ValueError("Godzina musi mieć format HH:MM.")
        hour = int(hh)
        minute = int(mm)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("Godzina musi mieć format HH:MM.")
        return value


class BackupRunRequest(BaseModel):
    """Parametry uruchomienia zadania kopii zapasowej."""

    label: str | None = Field(default=None, max_length=120)
    compress: bool = True
    dry_run: bool = True


class BackupRunResponse(BaseModel):
    """Wynik inicjacji zadania kopii zapasowej."""

    accepted: bool
    dry_run: bool
    status: Literal["DRY_RUN", "SUCCESS", "PARTIAL"]
    message: str
    backup_name: str | None = None
    postgres_dump_included: bool = False
    uploaded_to_cloud: bool = False


class BackupRestoreRequest(BaseModel):
    """Parametry przywrócenia kopii zapasowej."""

    backup_name: str = Field(..., min_length=1, max_length=240)
    dry_run: bool = True
    confirm: str | None = Field(default=None, max_length=24)


class BackupRestoreResponse(BaseModel):
    """Wynik inicjacji przywrócenia kopii zapasowej."""

    accepted: bool
    dry_run: bool
    message: str


class BackupOffice365TestResponse(BaseModel):
    """Wynik testu połączenia z Office 365 / SharePoint."""

    ok: bool
    message: str
    site_id: str | None = None
    drive_id: str | None = None
    folder_path: str | None = None
