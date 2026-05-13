"""Testy automatu wiązania urządzeń workflow z klientem MS."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.contracts_dashboard import FirebirdModelMatch
from app.services.workflow_machine_binding import (
    _create_machine_for_device,
    bind_devices_to_workflow_client,
    build_binding_status_payload,
)


class _FakeCursor:
    def __init__(self) -> None:
        self.query: str = ""
        self.params: tuple[object, ...] = tuple()

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.query = query
        self.params = params

    def fetchone(self) -> tuple[object, ...]:
        return (12922,)


def test_create_machine_for_device_uses_model_master_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.find_model_in_firebird",
        lambda _name: FirebirdModelMatch(
            found=True,
            id_model=631,
            marka="Ricoh",
            model="MPC 6003",
            grupa="Druk",
            rodzaj="Platne",
            kolor="TAK",
            plik="ricoh_mpc6003.png",
        ),
    )

    cursor = _FakeCursor()
    workflow_case = SimpleNamespace(firebird_client_id=2897, form_request_id=31)
    device = SimpleNamespace(
        producer="Ricoh", model="MPC 6003", serial="E195M090903", ewidencja="KP/4126"
    )
    snapshot = {
        "name": "Ricoh MPC 6003 S/N: E195M090903",
        "index": "KP/4126",
    }

    machine_id = _create_machine_for_device(
        cursor,
        workflow_case=workflow_case,
        device=device,
        snapshot=snapshot,
        actor_label="Operator Test",
    )

    assert machine_id == 12922
    assert "GRUPA" in cursor.query
    assert "RODZAJ_US" in cursor.query
    assert "KOLOROWA" in cursor.query
    assert cursor.params[2] == 2897
    assert cursor.params[3] == 631
    assert cursor.params[4] == "Ricoh"
    assert cursor.params[5] == "MPC 6003"
    assert cursor.params[6] == "Druk"
    assert cursor.params[7] == "E195M090903"
    assert cursor.params[9] == "KP/4126"
    assert cursor.params[12] == "TAK"
    assert cursor.params[15] == "Platne"
    assert cursor.params[17] == "Platne"


def test_create_machine_for_device_requires_model_match(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.find_model_in_firebird",
        lambda _name: FirebirdModelMatch(found=False),
    )

    cursor = _FakeCursor()
    workflow_case = SimpleNamespace(firebird_client_id=2897, form_request_id=31)
    device = SimpleNamespace(
        producer="Ricoh", model="MPC 6003", serial="E195M090903", ewidencja="KP/4126"
    )

    with pytest.raises(RuntimeError, match="Nie znaleziono modelu"):
        _create_machine_for_device(
            cursor,
            workflow_case=workflow_case,
            device=device,
            snapshot={},
            actor_label="Operator Test",
        )


def test_create_machine_for_device_prefers_model_id_from_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.find_model_in_firebird_by_id",
        lambda _model_id: FirebirdModelMatch(
            found=True,
            id_model=777,
            marka="Ricoh",
            model="MP 401",
            grupa="Druk",
            rodzaj="Platne",
            kolor="NIE",
            plik="ricoh_mp401.png",
        ),
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.find_model_in_firebird",
        lambda _name: FirebirdModelMatch(found=False),
    )

    cursor = _FakeCursor()
    workflow_case = SimpleNamespace(firebird_client_id=2897, form_request_id=31)
    device = SimpleNamespace(
        producer="Ricoh", model="MP 401", serial="C74P370058", ewidencja="KP/1880"
    )
    snapshot = {
        "name": "Ricoh MP 401",
        "index": "KP/1880",
        "ms_id_model": "777",
    }

    machine_id = _create_machine_for_device(
        cursor,
        workflow_case=workflow_case,
        device=device,
        snapshot=snapshot,
        actor_label="Operator Test",
    )

    assert machine_id == 12922
    assert cursor.params[3] == 777
    assert cursor.params[5] == "MP 401"


def test_bind_devices_to_workflow_client_does_not_fail_on_non_kp_ewidencja(monkeypatch) -> None:
    class _FakeCursorForBinding:
        def __init__(self) -> None:
            self.query: str = ""
            self.params: tuple[object, ...] = tuple()
            self.updates: list[tuple[str, tuple[object, ...]]] = []

        def execute(self, query: str, params: tuple[object, ...]) -> None:
            self.query = query
            self.params = params
            if query.strip().upper().startswith("UPDATE MASZYNA SET"):
                self.updates.append((query, params))

        def fetchone(self) -> tuple[object, ...] | None:
            if "FROM MASZYNA" in self.query:
                return (
                    777,
                    1001,
                    "ABC/123",
                    "NIE",
                    0,
                    "E195M090903",
                    None,
                    "Ricoh",
                    "MPC 6003",
                    "",
                    "",
                    "",
                    "",
                )
            return None

        def close(self) -> None:
            return None

    class _FakeConnectionForBinding:
        def __init__(self) -> None:
            self.cursor_obj = _FakeCursorForBinding()
            self.committed = False
            self.rolled_back = False

        def cursor(self) -> _FakeCursorForBinding:
            return self.cursor_obj

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            return None

    fake_connection = _FakeConnectionForBinding()

    monkeypatch.setattr(
        "app.services.workflow_machine_binding.firebird_writes_enabled",
        lambda: (True, None),
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding._get_firebird_connection",
        lambda: fake_connection,
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.find_model_in_firebird",
        lambda _name: FirebirdModelMatch(
            found=True,
            id_model=631,
            marka="Ricoh",
            model="MPC 6003",
            grupa="Druk",
            rodzaj="Platne",
            kolor="TAK",
            plik="ricoh_mpc6003.png",
        ),
    )

    workflow_case = SimpleNamespace(firebird_client_id=2924, form_request_id=31)
    device = SimpleNamespace(
        id=1,
        source_row=21,
        source_type="firebird_magazyn_28",
        producer="Ricoh",
        model="MPC 6003",
        serial="E195M090903",
        ewidencja="ABC/123",
        firebird_machine_id=None,
        snapshot={},
    )

    items, errors = bind_devices_to_workflow_client(
        workflow_case=workflow_case,
        devices=[device],
        actor_label="Operator Test",
    )

    assert errors == []
    assert len(items) == 1
    item = items[0]
    assert item.ok is True
    assert item.machine_id == 777
    assert item.current_client_id == 2924
    assert item.current_ewidencja == "ABC/123"
    assert item.ewidencja_changed is False
    assert "Pominięto normalizację EWIDENCJA" in item.message
    assert "zsynchronizowano dane MODEL" in item.message
    assert fake_connection.committed is True
    assert fake_connection.rolled_back is False
    assert len(fake_connection.cursor_obj.updates) == 1
    update_query, update_params = fake_connection.cursor_obj.updates[0]
    assert "ID_KLIENT = ?" in update_query
    assert "ID_MODEL = ?" in update_query
    assert "GRUPA = ?" in update_query
    assert "AKTYWNA = ?" in update_query
    assert "SYNWP = ?" in update_query
    assert "EWIDENCJA = ?" not in update_query


def test_bind_devices_to_workflow_client_parses_stock_name_and_repairs_machine_fields(
    monkeypatch,
) -> None:
    class _FakeCursorForWarehouseBinding:
        def __init__(self) -> None:
            self.query: str = ""
            self.params: tuple[object, ...] = tuple()
            self.updates: list[tuple[str, tuple[object, ...]]] = []

        def execute(self, query: str, params: tuple[object, ...]) -> None:
            self.query = query
            self.params = params
            if query.strip().upper().startswith("UPDATE MASZYNA SET"):
                self.updates.append((query, params))

        def fetchone(self) -> tuple[object, ...] | None:
            if "FROM MAGAZYN" in self.query:
                return (
                    16809,
                    28,
                    "KP/4717",
                    "Ricoh IMC 5500 S/N: 3139M420306",
                    "",
                    "",
                    "NIE",
                    None,
                )
            if "FROM MASZYNA" in self.query and "COALESCE(SERIAL" in self.query:
                return None
            if "FROM MASZYNA" in self.query and "COALESCE(EWIDENCJA" in self.query:
                return (
                    7265,
                    656,
                    "KP/4717/R/",
                    "NIE",
                    0,
                    None,
                    None,
                    "",
                    "Ricoh IMC 5500 S/N: 3139M420306",
                    "",
                    "",
                    "",
                    "",
                )
            if "FROM MASZYNA" in self.query:
                return (
                    7265,
                    656,
                    "KP/4717/R/",
                    "NIE",
                    0,
                    None,
                    None,
                    "",
                    "Ricoh IMC 5500 S/N: 3139M420306",
                    "",
                    "",
                    "",
                    "",
                )
            return None

        def close(self) -> None:
            return None

    class _FakeConnectionForWarehouseBinding:
        def __init__(self) -> None:
            self.cursor_obj = _FakeCursorForWarehouseBinding()
            self.committed = False
            self.rolled_back = False

        def cursor(self) -> _FakeCursorForWarehouseBinding:
            return self.cursor_obj

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            return None

    fake_connection = _FakeConnectionForWarehouseBinding()

    monkeypatch.setattr(
        "app.services.workflow_machine_binding.firebird_writes_enabled",
        lambda: (True, None),
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding._get_firebird_connection",
        lambda: fake_connection,
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.find_model_in_firebird",
        lambda _name: FirebirdModelMatch(
            found=True,
            id_model=542,
            marka="Ricoh",
            model="IM C5500",
            grupa="Druk",
            rodzaj="MFP A3",
            kolor="TAK",
            plik="ricoh_imc5500.png",
        ),
    )

    workflow_case = SimpleNamespace(firebird_client_id=6485, form_request_id=32)
    device = SimpleNamespace(
        id=1,
        source_row=16809,
        source_type="firebird_magazyn_28",
        producer=None,
        model="Ricoh IMC 5500 S/N: 3139M420306",
        serial=None,
        ewidencja="KP/4717",
        firebird_machine_id=None,
        snapshot={
            "row": 16809,
            "source_type": "firebird_magazyn_28",
            "name": "Ricoh IMC 5500 S/N: 3139M420306",
            "model": "Ricoh IMC 5500 S/N: 3139M420306",
            "index": "KP/4717",
        },
    )

    items, errors = bind_devices_to_workflow_client(
        workflow_case=workflow_case,
        devices=[device],
        actor_label="Operator Test",
    )

    assert errors == []
    assert len(items) == 1
    item = items[0]
    assert item.ok is True
    assert item.machine_id == 7265
    assert item.producer == "Ricoh"
    assert item.model == "IM C5500"
    assert item.serial == "3139M420306"
    assert fake_connection.committed is True
    assert fake_connection.rolled_back is False
    assert len(fake_connection.cursor_obj.updates) == 1
    update_query, update_params = fake_connection.cursor_obj.updates[0]
    assert "SERIAL = ?" in update_query
    assert "ID_MODEL = ?" in update_query
    assert "MARKA = ?" in update_query
    assert "MODEL = ?" in update_query
    assert "GRUPA = ?" in update_query
    assert "TYP = ?" in update_query
    assert "RODZAJ_US = ?" in update_query
    assert "KOLOROWA = ?" in update_query
    assert update_params[0] == 6485


def test_build_binding_status_payload_includes_failed_device_details() -> None:
    devices = [
        SimpleNamespace(
            source_row=16809,
            producer="Ricoh",
            model="IM C5500",
            serial="3139M420306",
            ewidencja="KP/4717/GRENKE/R",
            snapshot={
                "ms_binding_status": "ok",
                "ms_binding_message": "Powiązano urządzenie z klientem MS.",
                "ms_binding_updated_at": "2026-05-13T10:00:00+00:00",
                "producer": "Ricoh",
                "model": "IM C5500",
                "serial": "3139M420306",
            },
        ),
        SimpleNamespace(
            source_row=17383,
            producer="Ricoh",
            model="MP C2011",
            serial="G479M130731",
            ewidencja="WEKP/2680",
            snapshot={
                "ms_binding_status": "error",
                "ms_binding_message": "Nie znaleziono modelu w tabeli MODEL.",
                "ms_binding_updated_at": "2026-05-13T10:01:00+00:00",
                "producer": "Ricoh",
                "model": "MP C2011",
                "serial": "G479M130731",
            },
        ),
    ]

    payload = build_binding_status_payload(devices)

    assert payload["state"] == "warning"
    assert payload["success_count"] == 1
    assert payload["error_count"] == 1
    assert payload["updated_at"] == "2026-05-13T10:01:00+00:00"
    assert payload["text"].startswith("Częściowo powiązano urządzenia (1/2).")
    assert "Ricoh MP C2011 | Nr seryjny: G479M130731" in payload["text"]
    assert payload["errors"] == [
        "Ricoh MP C2011 | Nr seryjny: G479M130731: Nie znaleziono modelu w tabeli MODEL."
    ]


def test_build_binding_status_payload_uses_ewidencja_when_serial_missing() -> None:
    devices = [
        SimpleNamespace(
            source_row=99,
            producer="Ricoh",
            model="IM C300",
            serial=None,
            ewidencja="KP/4699/GRENKE/E",
            snapshot={
                "ms_binding_status": "error",
                "ms_binding_message": "Brak dopasowania do rekordu MASZYNA.",
                "ms_binding_updated_at": "2026-05-13T10:02:00+00:00",
                "producer": "Ricoh",
                "model": "IM C300",
                "ewidencja": "KP/4699/GRENKE/E",
            },
        )
    ]

    payload = build_binding_status_payload(devices)

    assert payload["state"] == "error"
    assert payload["text"].startswith("Nie powiązano urządzeń z klientem (0/1).")
    assert "Ricoh IM C300 | Ewidencja: KP/4699/GRENKE/E" in payload["text"]
