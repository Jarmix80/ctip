"""Konfiguracja dostępu IMAP per użytkownik (zarządzana przez administratora)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.settings_store import StoredValue, build_store

_NAMESPACE_PREFIX = "user_imap"
_store = build_store(settings.admin_secret_key)


@dataclass(slots=True, frozen=True)
class UserImapConfig:
    """Konfiguracja IMAP przypisana do konta użytkownika."""

    enabled: bool
    email: str | None
    host: str | None
    port: int | None
    username: str | None
    use_ssl: bool
    folder: str | None
    password_set: bool
    password: str | None = None


@dataclass(slots=True)
class UserImapUpdate:
    """Zmiana konfiguracji IMAP użytkownika."""

    enabled: bool | None = None
    email: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    use_ssl: bool | None = None
    folder: str | None = None
    password: str | None = None
    clear_password: bool = False


def _namespace_for_user(user_id: int) -> str:
    return f"{_NAMESPACE_PREFIX}.{user_id}"


def _to_bool(value: str | bool | None, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "on"}
    return default


def _to_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    raw = value.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def load_user_imap_config(
    session: AsyncSession,
    *,
    user_id: int,
    fallback_email: str | None = None,
    include_secret: bool = False,
) -> UserImapConfig:
    """Pobiera konfigurację IMAP użytkownika z `admin_setting`."""
    namespace = _namespace_for_user(user_id)
    stored = await _store.get_namespace(session, namespace)

    enabled = _to_bool(stored.get("enabled"), False)
    email = _strip(stored.get("email")) or _strip(fallback_email)
    host = _strip(stored.get("host"))
    port = _to_int(stored.get("port"))
    username = _strip(stored.get("username"))
    use_ssl = _to_bool(stored.get("use_ssl"), True)
    folder = _strip(stored.get("folder")) or "INBOX"
    password = stored.get("password")

    return UserImapConfig(
        enabled=enabled,
        email=email,
        host=host,
        port=port,
        username=username,
        use_ssl=use_ssl,
        folder=folder,
        password_set=bool(password),
        password=(password if include_secret else None),
    )


async def set_user_imap_config(
    session: AsyncSession,
    *,
    user_id: int,
    update: UserImapUpdate,
    updated_by: int | None,
) -> None:
    """Zapisuje konfigurację IMAP użytkownika."""
    values: dict[str, StoredValue] = {}

    if update.enabled is not None:
        values["enabled"] = StoredValue("true" if update.enabled else "false", False)
    if update.email is not None:
        values["email"] = StoredValue(_strip(update.email) or "", False)
    if update.host is not None:
        values["host"] = StoredValue(_strip(update.host) or "", False)
    if update.port is not None:
        values["port"] = StoredValue(str(update.port), False)
    if update.username is not None:
        values["username"] = StoredValue(_strip(update.username) or "", False)
    if update.use_ssl is not None:
        values["use_ssl"] = StoredValue("true" if update.use_ssl else "false", False)
    if update.folder is not None:
        values["folder"] = StoredValue(_strip(update.folder) or "", False)
    if update.password is not None:
        values["password"] = StoredValue(update.password, True)
    elif update.clear_password:
        values["password"] = StoredValue("", True)

    if not values:
        return

    await _store.set_namespace(
        session,
        _namespace_for_user(user_id),
        values,
        user_id=updated_by,
    )


__all__ = [
    "UserImapConfig",
    "UserImapUpdate",
    "load_user_imap_config",
    "set_user_imap_config",
]
