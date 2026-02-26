"""Narzędzia pomocnicze do testowania połączeń Firebird."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class FirebirdTestResult:
    success: bool
    message: str
    engine_version: str | None = None


def _build_dsn(host: str, port: int, database: str) -> str:
    host_value = host.strip()
    database_value = database.strip()
    return f"{host_value}/{port}:{database_value}"


def _should_try_firebirdsql(error_message: str) -> bool:
    normalized = error_message.lower()
    return (
        "client library" in normalized
        or "fbclient" in normalized
        or "gds32" in normalized
        or "could not be determined" in normalized
    )


def _extract_engine_version(row: Any) -> str | None:
    if row is None:
        return None
    if isinstance(row, (list, tuple)):
        if not row:
            return None
        value = row[0]
    else:
        value = row
    if value is None:
        return None
    return str(value).strip() or None


def _test_with_fdb(
    *,
    dsn: str,
    user: str,
    password: str | None,
    charset: str,
    role: str | None,
) -> FirebirdTestResult:
    import fdb  # type: ignore[import-not-found]

    connection = None
    cursor = None
    try:
        connection = fdb.connect(
            dsn=dsn,
            user=user,
            password=password or "",
            charset=charset,
            role=role,
        )
        cursor = connection.cursor()
        cursor.execute("SELECT rdb$get_context('SYSTEM', 'ENGINE_VERSION') FROM rdb$database")
        row = cursor.fetchone()
        return FirebirdTestResult(
            True,
            "Połączenie z Firebird zakończone sukcesem.",
            _extract_engine_version(row),
        )
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:  # pragma: no cover - ochrona cleanup
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:  # pragma: no cover - ochrona cleanup
                pass


def _test_with_firebirdsql(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str | None,
    charset: str,
) -> FirebirdTestResult:
    import firebirdsql  # type: ignore[import-not-found]

    connection = firebirdsql.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password or "",
        charset=charset,
    )
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT rdb$get_context('SYSTEM', 'ENGINE_VERSION') FROM rdb$database")
        row = cursor.fetchone()
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:  # pragma: no cover - ochrona cleanup
                pass
        connection.close()
    return FirebirdTestResult(
        True,
        "Połączenie z Firebird zakończone sukcesem.",
        _extract_engine_version(row),
    )


def test_firebird_connection(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str | None,
    charset: str = "WIN1250",
    role: str | None = None,
    timeout: float = 5.0,
) -> FirebirdTestResult:
    """Weryfikuje połączenie TCP i logowanie do bazy Firebird."""

    host_value = (host or "").strip()
    database_value = (database or "").strip()
    user_value = (user or "").strip()
    charset_value = (charset or "UTF8").strip() or "UTF8"
    role_value = (role or "").strip() or None

    if not host_value:
        return FirebirdTestResult(False, "Brak hosta Firebird w konfiguracji.")
    if not database_value:
        return FirebirdTestResult(False, "Brak ścieżki bazy Firebird w konfiguracji.")
    if not user_value:
        return FirebirdTestResult(False, "Brak użytkownika Firebird w konfiguracji.")
    if port < 1 or port > 65535:
        return FirebirdTestResult(False, "Port Firebird musi mieścić się w zakresie 1-65535.")

    try:
        with socket.create_connection((host_value, port), timeout=timeout):
            pass
    except Exception as exc:
        return FirebirdTestResult(False, f"Błąd połączenia TCP z Firebird: {exc}")

    dsn = _build_dsn(host_value, port, database_value)
    fdb_error: str | None = None

    try:
        result = _test_with_fdb(
            dsn=dsn,
            user=user_value,
            password=password,
            charset=charset_value,
            role=role_value,
        )
        engine_version = result.engine_version
    except ModuleNotFoundError:
        fdb_error = "Brak biblioteki Python `fdb`."
        engine_version = None
    except Exception as exc:
        fdb_error = str(exc)
        if not _should_try_firebirdsql(fdb_error):
            return FirebirdTestResult(False, f"Błąd logowania do Firebird: {exc}")
        engine_version = None

    if engine_version is None:
        try:
            result = _test_with_firebirdsql(
                host=host_value,
                port=port,
                database=database_value,
                user=user_value,
                password=password,
                charset=charset_value,
            )
            engine_version = result.engine_version
        except ModuleNotFoundError:
            detail = "Brak biblioteki `firebirdsql` wymaganej do alternatywnego testu połączenia."
            if fdb_error:
                detail = f"Błąd fdb: {fdb_error}. {detail}"
            return FirebirdTestResult(False, detail)
        except Exception as exc:
            detail = f"Błąd logowania do Firebird: {exc}"
            if fdb_error:
                detail = f"Błąd fdb: {fdb_error}; fallback firebirdsql: {exc}"
            return FirebirdTestResult(False, detail)

    if engine_version:
        message = f"Połączenie z Firebird zakończone sukcesem (silnik {engine_version})."
    else:
        message = "Połączenie z Firebird zakończone sukcesem."
    return FirebirdTestResult(True, message, engine_version)


__all__ = ["FirebirdTestResult", "test_firebird_connection"]
