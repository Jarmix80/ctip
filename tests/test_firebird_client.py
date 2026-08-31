import contextlib
from unittest.mock import patch

from app.services import firebird_client


def test_firebird_connection_requires_host():
    result = firebird_client.test_firebird_connection(
        host="",
        port=3050,
        database="D:/BAZA_MS_KP/BAZAMS.FDB",
        user="SYSDBA",
        password="ctip-test-only",
    )
    assert result.success is False
    assert "Brak hosta Firebird" in result.message


def test_firebird_connection_fallbacks_to_firebirdsql_on_missing_fdb():
    with (
        patch("socket.create_connection", return_value=contextlib.nullcontext()),
        patch(
            "app.services.firebird_client._test_with_fdb",
            side_effect=ModuleNotFoundError("No module named 'fdb'"),
        ),
        patch(
            "app.services.firebird_client._test_with_firebirdsql",
            return_value=firebird_client.FirebirdTestResult(
                success=True,
                message="Połączenie z Firebird zakończone sukcesem.",
                engine_version="2.5.9",
            ),
        ),
    ):
        result = firebird_client.test_firebird_connection(
            host="192.168.0.8",
            port=3050,
            database="D:/BAZA_MS_KP/BAZAMS.FDB",
            user="SYSDBA",
            password="ctip-test-only",
        )
    assert result.success is True
    assert result.engine_version == "2.5.9"


def test_firebird_connection_returns_direct_error_for_auth_failure():
    with (
        patch("socket.create_connection", return_value=contextlib.nullcontext()),
        patch(
            "app.services.firebird_client._test_with_fdb",
            side_effect=Exception("Your user name and password are not defined."),
        ),
    ):
        result = firebird_client.test_firebird_connection(
            host="192.168.0.8",
            port=3050,
            database="D:/BAZA_MS_KP/BAZAMS.FDB",
            user="SYSDBA",
            password="bad-password",
        )
    assert result.success is False
    assert "Błąd logowania do Firebird" in result.message
    assert "password" in result.message


def test_firebird_connection_fallbacks_when_fbclient_is_missing():
    with (
        patch("socket.create_connection", return_value=contextlib.nullcontext()),
        patch(
            "app.services.firebird_client._test_with_fdb",
            side_effect=Exception(
                "The location of Firebird Client Library could not be determined."
            ),
        ),
        patch(
            "app.services.firebird_client._test_with_firebirdsql",
            return_value=firebird_client.FirebirdTestResult(
                success=True,
                message="Połączenie z Firebird zakończone sukcesem.",
                engine_version="2.5.9",
            ),
        ),
    ):
        result = firebird_client.test_firebird_connection(
            host="192.168.0.8",
            port=3050,
            database="D:/BAZA_MS_KP/BAZAMS.FDB",
            user="SYSDBA",
            password="ctip-test-only",
        )
    assert result.success is True
    assert result.engine_version == "2.5.9"


def test_firebird_connection_reports_tcp_timeout():
    with patch("socket.create_connection", side_effect=TimeoutError("timed out")):
        result = firebird_client.test_firebird_connection(
            host="192.168.0.8",
            port=3050,
            database="D:/BAZA_MS_KP/BAZAMS.FDB",
            user="SYSDBA",
            password="ctip-test-only",
            timeout=0.01,
        )

    assert result.success is False
    assert "Błąd połączenia TCP z Firebird" in result.message
    assert "timed out" in result.message


def test_firebird_connection_reports_timeout_in_firebirdsql_fallback():
    with (
        patch("socket.create_connection", return_value=contextlib.nullcontext()),
        patch(
            "app.services.firebird_client._test_with_fdb",
            side_effect=ModuleNotFoundError("No module named 'fdb'"),
        ),
        patch(
            "app.services.firebird_client._test_with_firebirdsql",
            side_effect=TimeoutError("timed out"),
        ),
    ):
        result = firebird_client.test_firebird_connection(
            host="192.168.0.8",
            port=3050,
            database="D:/BAZA_MS_KP/BAZAMS.FDB",
            user="SYSDBA",
            password="ctip-test-only",
        )

    assert result.success is False
    assert "fallback firebirdsql" in result.message
    assert "timed out" in result.message
