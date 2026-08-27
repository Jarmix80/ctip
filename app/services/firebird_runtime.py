"""Wspólna konfiguracja i fabryka połączeń Firebird Menadżera Serwisu."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


@dataclass(slots=True, frozen=True)
class FirebirdRuntimeConfig:
    """Aktywna konfiguracja Firebird pobrana wyłącznie ze środowiska."""

    mode: str
    host: str
    port: int
    database: str
    user: str
    password: str
    charset: str
    role: str | None
    local_copy_path: str
    allow_writes: bool


_runtime_config_var: ContextVar[FirebirdRuntimeConfig | None] = ContextVar(
    "firebird_runtime_config",
    default=None,
)


def _coerce_port(value: str | int | None, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _normalize_mode(value: str | None) -> str:
    configured = (settings.fb_mode or "").strip().lower()
    mode = (value or "").strip().lower() or configured
    if mode in {"network", "local"}:
        return mode
    return configured if configured in {"network", "local"} else "local"


def default_firebird_runtime_config() -> FirebirdRuntimeConfig:
    """Buduje konfigurację Firebird z aktywnego pliku środowiskowego."""
    return FirebirdRuntimeConfig(
        mode=_normalize_mode(settings.fb_mode),
        host=(settings.fb_host or "").strip(),
        port=_coerce_port(settings.fb_port, 3050),
        database=(settings.fb_database or "").strip(),
        user=(settings.fb_user or "").strip(),
        password=settings.fb_password or "",
        charset=(settings.fb_charset or "").strip() or "UTF8",
        role=(settings.fb_role or "").strip() or None,
        local_copy_path=(settings.fb_local_copy_path or "").strip()
        or "inbox/firebird/test_ms_local.fdb",
        allow_writes=bool(settings.fb_allow_writes),
    )


async def load_firebird_runtime_config(session: AsyncSession) -> FirebirdRuntimeConfig:
    """Ładuje konfigurację Firebird; sesja pozostaje parametrem zgodności API."""
    del session
    return default_firebird_runtime_config()


def resolve_firebird_runtime_config() -> FirebirdRuntimeConfig:
    """Zwraca konfigurację kontekstową albo ustawienia środowiskowe."""
    return _runtime_config_var.get() or default_firebird_runtime_config()


@contextmanager
def use_firebird_runtime_config(config: FirebirdRuntimeConfig | None):
    """Aktywuje konfigurację Firebird dla bieżącego zadania lub wątku."""
    if config is None:
        yield
        return
    token = _runtime_config_var.set(config)
    try:
        yield
    finally:
        _runtime_config_var.reset(token)


def resolve_local_firebird_path(config: FirebirdRuntimeConfig | None = None) -> Path:
    """Rozwiązuje ścieżkę lokalnej kopii Firebird względem repozytorium."""
    runtime = config or resolve_firebird_runtime_config()
    db_path = Path(runtime.local_copy_path)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[2] / db_path
    return db_path


def firebird_writes_enabled() -> tuple[bool, str | None]:
    """Sprawdza, czy zapis do aktywnej bazy Firebird jest jawnie odblokowany."""
    runtime = resolve_firebird_runtime_config()
    if not runtime.allow_writes:
        return (
            False,
            "Zapis do Firebird jest zablokowany w konfiguracji środowiskowej. "
            "Ustaw `FB_ALLOW_WRITES=true` w aktywnym pliku środowiskowym i uruchom usługę ponownie.",
        )
    if runtime.mode == "network":
        if not runtime.host:
            return False, "Brak hosta Firebird w aktywnej konfiguracji."
        if not runtime.database:
            return False, "Brak bazy Firebird w aktywnej konfiguracji."
        return True, None
    db_path = resolve_local_firebird_path(runtime)
    if not db_path.exists():
        return False, f"Brak lokalnej kopii Firebird do zapisu: {db_path}"
    return True, None


def firebird_connection():
    """Otwiera połączenie zgodne z aktywną konfiguracją runtime."""
    import firebirdsql  # type: ignore[import-not-found]

    runtime = resolve_firebird_runtime_config()
    connect_kwargs: dict[str, Any] = {
        "port": runtime.port,
        "user": runtime.user,
        "password": runtime.password,
        "charset": runtime.charset,
    }
    if runtime.role:
        connect_kwargs["role"] = runtime.role

    if runtime.mode == "network":
        if not runtime.host:
            raise FileNotFoundError("Brak hosta Firebird w aktywnej konfiguracji.")
        if not runtime.database:
            raise FileNotFoundError("Brak bazy Firebird w aktywnej konfiguracji.")
        return firebirdsql.connect(
            host=runtime.host,
            database=runtime.database,
            **connect_kwargs,
        )

    db_path = resolve_local_firebird_path(runtime)
    if not db_path.exists():
        raise FileNotFoundError(f"Brak lokalnej kopii Firebird: {db_path}")
    return firebirdsql.connect(
        host="127.0.0.1",
        database=str(db_path),
        **connect_kwargs,
    )


__all__ = [
    "FirebirdRuntimeConfig",
    "default_firebird_runtime_config",
    "firebird_connection",
    "firebird_writes_enabled",
    "load_firebird_runtime_config",
    "resolve_firebird_runtime_config",
    "resolve_local_firebird_path",
    "use_firebird_runtime_config",
]
