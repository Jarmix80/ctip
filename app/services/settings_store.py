"""Magazyn ustawień konfiguracyjnych panelu administratora."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminSetting


@dataclass(frozen=True)
class StoredValue:
    """Opis wartości przechowywanej w tabeli admin_setting."""

    value: str
    is_secret: bool


class SettingsStore:
    """Obsługa odczytu/zapisu ustawień konfiguracyjnych."""

    def __init__(self, secret_key: str | None):
        self._cipher = Fernet(secret_key) if secret_key else None

    def _encrypt(self, value: str, is_secret: bool) -> str:
        if is_secret and self._cipher:
            return self._cipher.encrypt(value.encode("utf-8")).decode("utf-8")
        return value

    def _decrypt(self, value: str, is_secret: bool) -> str:
        if not is_secret or not self._cipher:
            return value
        try:
            decrypted = self._cipher.decrypt(value.encode("utf-8"))
        except InvalidToken:
            return ""
        return decrypted.decode("utf-8")

    async def get_namespace(self, session: AsyncSession, namespace: str) -> dict[str, str]:
        """Zwraca wartości ustawień z danego prefiksu (np. `database`)."""
        stmt = select(AdminSetting).where(AdminSetting.key.like(f"{namespace}.%"))
        rows = await session.execute(stmt)
        settings_map: dict[str, str] = {}
        for row in rows.scalars():
            key = row.key.removeprefix(f"{namespace}.")
            settings_map[key] = self._decrypt(row.value, row.is_secret)
        return settings_map

    async def get_many(self, session: AsyncSession, keys: Iterable[str]) -> dict[str, str]:
        """Zwraca mapę klucz→wartość dla dowolnych kluczy."""
        keys = list(keys)
        if not keys:
            return {}
        stmt = select(AdminSetting).where(AdminSetting.key.in_(keys))
        rows = await session.execute(stmt)
        return {row.key: self._decrypt(row.value, row.is_secret) for row in rows.scalars()}

    async def set_namespace(
        self,
        session: AsyncSession,
        namespace: str,
        values: Mapping[str, StoredValue],
        *,
        user_id: int | None,
    ) -> None:
        """
        Aktualizuje wartości w danym prefiksie. Jeśli w values brakuje klucza, pozostaje on bez zmian.
        """
        now = datetime.now(UTC)
        full_keys = {f"{namespace}.{key}": stored for key, stored in values.items()}

        stmt = select(AdminSetting).where(AdminSetting.key.in_(full_keys.keys()))
        existing_rows = {row.key: row for row in (await session.execute(stmt)).scalars()}

        for full_key, stored in full_keys.items():
            encoded = self._encrypt(stored.value, stored.is_secret)
            if existing := existing_rows.get(full_key):
                existing.value = encoded
                existing.is_secret = stored.is_secret
                existing.updated_at = now
                existing.updated_by = user_id
            else:
                session.add(
                    AdminSetting(
                        key=full_key,
                        value=encoded,
                        is_secret=stored.is_secret,
                        updated_at=now,
                        updated_by=user_id,
                    )
                )


def build_store(secret_key: str | None) -> SettingsStore:
    """Pomocnicza funkcja tworząca magazyn na podstawie klucza z konfiguracji."""
    return SettingsStore(secret_key)
