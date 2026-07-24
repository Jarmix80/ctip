"""Testy kontrolowanego wycofania PZ urządzeń."""

from __future__ import annotations

import pytest

from app.services import device_withdrawal


class FakeCursor:
    """Minimalny kursor Firebird z zapisem kolejności poleceń."""

    def __init__(self, *, dependencies: dict[str, int] | None = None) -> None:
        self.dependencies = dependencies or {}
        self.commands: list[str] = []
        self._rows: list[tuple] = []

    def execute(self, query: str, params=()) -> None:
        del params
        normalized = " ".join(query.split())
        self.commands.append(normalized)
        if normalized.startswith("SELECT NUMER"):
            self._rows = [("PZ / 1 / 2026", "FV/1")]
        elif normalized.startswith("SELECT ID_ZAKPOZYCJA_TABLE"):
            self._rows = [(101,), (102,)]
        elif normalized.startswith("SELECT COUNT(*) FROM"):
            table = normalized.split()[3]
            self._rows = [(self.dependencies.get(table, 0),)]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self) -> None:
        return None


class FakeConnection:
    """Połączenie rejestrujące commit i rollback."""

    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_value = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        return None


def _snapshot() -> dict:
    return {
        "pz_number": "PZ / 1 / 2026",
        "items": [
            {
                "zakpozycja_id": 101,
                "warehouse_item_id": 201,
                "machine_id": 301,
                "machine_table_id": 401,
            },
            {
                "zakpozycja_id": 102,
                "warehouse_item_id": 202,
                "machine_id": 302,
                "machine_table_id": 402,
            },
        ],
    }


def test_preview_blocks_document_with_later_machine_reference(monkeypatch) -> None:
    cursor = FakeCursor(dependencies={"ZLECENIE": 1})
    connection = FakeConnection(cursor)
    monkeypatch.setattr(device_withdrawal, "firebird_connection", lambda: connection)

    preview = device_withdrawal.preview_device_pz_withdrawal(
        pz_id=10,
        expected=_snapshot(),
    )

    assert preview["baseline_complete"] is True
    assert preview["dependencies"] == {"ZLECENIE": 1}
    assert preview["can_withdraw_normally"] is False
    assert connection.rolled_back is True


def test_normal_withdrawal_rejects_later_reference(monkeypatch) -> None:
    cursor = FakeCursor(dependencies={"UMOWA": 1})
    connection = FakeConnection(cursor)
    monkeypatch.setattr(device_withdrawal, "firebird_connection", lambda: connection)

    with pytest.raises(ValueError, match="późniejsze powiązania"):
        device_withdrawal.withdraw_device_pz(
            pz_id=10,
            expected=_snapshot(),
            force=False,
        )

    assert connection.rolled_back is True
    assert not any(command.startswith("DELETE") for command in cursor.commands)


def test_forced_withdrawal_detaches_orders_before_machine_delete(monkeypatch) -> None:
    cursor = FakeCursor(dependencies={"ZLECENIE": 1})
    connection = FakeConnection(cursor)
    monkeypatch.setattr(device_withdrawal, "firebird_connection", lambda: connection)

    result = device_withdrawal.withdraw_device_pz(
        pz_id=10,
        expected=_snapshot(),
        force=True,
    )

    detach_index = next(
        index
        for index, command in enumerate(cursor.commands)
        if command.startswith("UPDATE ZLECENIE SET ID_MASZYNA = 0")
    )
    machine_delete_index = next(
        index
        for index, command in enumerate(cursor.commands)
        if command.startswith("DELETE FROM MASZYNA")
    )
    assert detach_index < machine_delete_index
    assert result["already_withdrawn"] is False
    assert connection.committed is True
