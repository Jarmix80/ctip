"""Obsługa uprawnień sekcji paneli CTIP."""

from __future__ import annotations

import json
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminSetting, AdminUser

AVAILABLE_SECTIONS = ("admin", "operator", "generator", "delivery", "device")
_USER_SECTIONS_PREFIX = "user_sections."


def default_sections_for_role(role: str | None) -> list[str]:
    """Zwraca domyślny zestaw sekcji dla wskazanej roli."""
    if role == "admin":
        return list(AVAILABLE_SECTIONS)
    if role == "serwisant":
        return ["delivery"]
    return ["operator", "generator"]


def normalize_sections(sections: Iterable[str] | None, *, role: str | None) -> list[str]:
    """Normalizuje listę sekcji, zawsze nadając administratorowi pełny dostęp."""
    if role == "admin":
        return default_sections_for_role(role)
    if sections is None:
        return default_sections_for_role(role)

    normalized: list[str] = []
    seen: set[str] = set()
    for item in sections:
        value = (item or "").strip().lower()
        if not value or value not in AVAILABLE_SECTIONS:
            continue
        if value == "admin" and role != "admin":
            continue
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    if not normalized:
        return default_sections_for_role(role)
    return normalized


def serialize_sections(sections: Iterable[str], *, role: str | None) -> str:
    """Koduje listę sekcji do postaci tekstowej."""
    normalized = normalize_sections(sections, role=role)
    return json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))


def deserialize_sections(value: str | None, *, role: str | None) -> list[str]:
    """Dekoduje sekcje zapisane w AdminSetting."""
    if not value:
        return default_sections_for_role(role)
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return default_sections_for_role(role)
    if not isinstance(raw, list):
        return default_sections_for_role(role)
    raw_items = [str(item) for item in raw]
    return normalize_sections(raw_items, role=role)


def _setting_key(user_id: int) -> str:
    return f"{_USER_SECTIONS_PREFIX}{user_id}"


async def get_user_sections(session: AsyncSession, user: AdminUser) -> list[str]:
    """Zwraca sekcje przypisane użytkownikowi."""
    stmt = select(AdminSetting).where(AdminSetting.key == _setting_key(user.id))
    setting = (await session.execute(stmt)).scalar_one_or_none()
    if setting is None:
        return default_sections_for_role(user.role)
    return deserialize_sections(setting.value, role=user.role)


async def user_has_section(session: AsyncSession, user: AdminUser, section: str) -> bool:
    """Sprawdza, czy użytkownik ma dostęp do wskazanej sekcji."""
    normalized_section = (section or "").strip().lower()
    if normalized_section not in AVAILABLE_SECTIONS:
        return False
    sections = await get_user_sections(session, user)
    return normalized_section in sections


async def set_user_sections(
    session: AsyncSession,
    *,
    user_id: int,
    role: str | None,
    sections: Iterable[str] | None,
    updated_by: int | None = None,
) -> list[str]:
    """Zapisuje konfigurację sekcji dla użytkownika i zwraca wynik normalizacji."""
    normalized = normalize_sections(sections, role=role)
    value = serialize_sections(normalized, role=role)
    key = _setting_key(user_id)
    stmt = select(AdminSetting).where(AdminSetting.key == key)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        session.add(
            AdminSetting(
                key=key,
                value=value,
                is_secret=False,
                updated_by=updated_by,
            )
        )
    else:
        existing.value = value
        existing.is_secret = False
        existing.updated_by = updated_by
    return normalized


async def list_user_sections(
    session: AsyncSession, users: Iterable[AdminUser]
) -> dict[int, list[str]]:
    """Buduje mapę sekcji dla listy użytkowników."""
    users_list = [user for user in users if user and user.id is not None]
    if not users_list:
        return {}

    user_ids = [int(user.id) for user in users_list]
    keys = [_setting_key(user_id) for user_id in user_ids]
    stmt = select(AdminSetting).where(AdminSetting.key.in_(keys))
    rows = (await session.execute(stmt)).scalars().all()
    raw_map = {row.key: row.value for row in rows}

    mapped: dict[int, list[str]] = {}
    for user in users_list:
        value = raw_map.get(_setting_key(int(user.id)))
        mapped[int(user.id)] = deserialize_sections(value, role=user.role)
    return mapped


async def delete_user_sections(session: AsyncSession, user_id: int) -> None:
    """Usuwa konfigurację sekcji użytkownika."""
    await session.execute(delete(AdminSetting).where(AdminSetting.key == _setting_key(user_id)))


__all__ = [
    "AVAILABLE_SECTIONS",
    "default_sections_for_role",
    "delete_user_sections",
    "deserialize_sections",
    "get_user_sections",
    "list_user_sections",
    "normalize_sections",
    "serialize_sections",
    "set_user_sections",
    "user_has_section",
]
