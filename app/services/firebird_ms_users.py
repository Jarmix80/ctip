"""Odczyt listy użytkowników Menadżera Serwisu z aktywnej konfiguracji Firebird."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.contracts_dashboard import FirebirdRuntimeConfig, load_firebird_runtime_config


@dataclass(slots=True)
class FirebirdMsUserOption:
    """Pojedyncza opcja użytkownika Menadżera Serwisu dla panelu CTIP."""

    id: int
    login_user: str
    workstation: str | None
    app_name: str | None

    @property
    def label(self) -> str:
        workstation = (self.workstation or "").strip()
        if workstation and workstation.casefold() != self.login_user.casefold():
            return f"{self.login_user} ({workstation})"
        return self.login_user


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _connect_firebird(config: FirebirdRuntimeConfig):
    import firebirdsql  # type: ignore[import-not-found]

    kwargs: dict[str, object] = {
        "user": config.user,
        "password": config.password,
        "charset": config.charset,
    }
    if config.role:
        kwargs["role"] = config.role
    if config.mode == "network":
        if not config.host:
            raise RuntimeError("Brak hosta Firebird w aktywnej konfiguracji.")
        if not config.database:
            raise RuntimeError("Brak ścieżki bazy Firebird w aktywnej konfiguracji.")
        return firebirdsql.connect(
            host=config.host,
            port=config.port,
            database=config.database,
            **kwargs,
        )

    db_path = (config.local_copy_path or config.database or "").strip()
    if not db_path:
        raise RuntimeError("Brak lokalnej kopii Firebird w aktywnej konfiguracji.")
    resolved_path = Path(db_path).expanduser()
    if not resolved_path.is_absolute():
        resolved_path = (Path.cwd() / resolved_path).resolve()
    if not resolved_path.exists():
        raise RuntimeError(f"Brak lokalnej kopii Firebird: {resolved_path}")
    return firebirdsql.connect(host="127.0.0.1", database=str(resolved_path), **kwargs)


def _sort_users(rows: list[FirebirdMsUserOption]) -> list[FirebirdMsUserOption]:
    return sorted(
        rows,
        key=lambda item: (
            item.login_user.casefold(),
            (item.workstation or "").casefold(),
            item.id,
        ),
    )


def load_firebird_ms_users(config: FirebirdRuntimeConfig) -> list[FirebirdMsUserOption]:
    """Zwraca listę trwałych użytkowników Menadżera Serwisu z konfiguracji systemu."""

    connection = _connect_firebird(config)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                ID_CONFIG_TABLE,
                TRIM(WYSTAWIA),
                TRIM(EMAIL)
            FROM CONFIG
            WHERE ID_CONFIG_TABLE > 0
              AND COALESCE(TRIM(WYSTAWIA), '') <> ''
            ORDER BY UPPER(TRIM(WYSTAWIA)), ID_CONFIG_TABLE
            """
        )
        results: list[FirebirdMsUserOption] = []
        for user_id, display_name, email in cursor.fetchall():
            name = _normalize_text(display_name)
            if not name:
                continue
            results.append(
                FirebirdMsUserOption(
                    id=int(user_id),
                    login_user=name,
                    workstation=_normalize_text(email),
                    app_name="CONFIG",
                )
            )
        return _sort_users(results)
    except Exception as exc:  # pragma: no cover - zabezpieczenie dla błędów sterownika
        raise RuntimeError(f"Nie udało się pobrać użytkowników Menadżera Serwisu: {exc}") from exc
    finally:
        connection.close()


async def list_firebird_ms_users(session: AsyncSession) -> list[FirebirdMsUserOption]:
    """Pobiera opcje użytkowników MS z aktywnej konfiguracji runtime Firebird."""

    config = await load_firebird_runtime_config(session)
    return load_firebird_ms_users(config)


async def resolve_firebird_ms_user(
    session: AsyncSession, firebird_app_user_id: int
) -> FirebirdMsUserOption:
    """Zwraca pojedynczą opcję użytkownika MS lub zgłasza błąd walidacji."""

    users = await list_firebird_ms_users(session)
    for item in users:
        if item.id == firebird_app_user_id:
            return item
    raise ValueError(
        "Nie znaleziono wskazanego użytkownika Menadżera Serwisu w aktywnej liście Firebird."
    )


__all__ = [
    "FirebirdMsUserOption",
    "list_firebird_ms_users",
    "load_firebird_ms_users",
    "resolve_firebird_ms_user",
]
