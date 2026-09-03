"""Testy automatu wiązania urządzeń workflow z klientem MS."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.contracts_dashboard import FirebirdModelMatch
from app.services.workflow_machine_binding import (
    WorkflowDeviceMixedOwnershipHold,
    WorkflowDeviceOwnershipConflict,
    WorkflowDeviceSourceContext,
    _create_machine_for_device,
    _resolve_machine_id_for_device,
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
                    656,
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
                    "Ksero Partner",
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
    assert "AND ID_KLIENT = ?" in update_query
    assert update_params[-1] == 656


def test_bind_devices_to_workflow_client_blocks_foreign_owner_for_entire_batch(
    monkeypatch,
) -> None:
    class _ConflictCursor:
        def __init__(self) -> None:
            self.query = ""
            self.updates = 0

        def execute(self, query: str, params: tuple[object, ...]) -> None:
            self.query = query
            if query.strip().upper().startswith("UPDATE MASZYNA SET"):
                self.updates += 1

        def fetchone(self) -> tuple[object, ...] | None:
            if "FROM MASZYNA" not in self.query:
                return None
            return (
                778,
                1001,
                "KP/778",
                "TAK",
                1,
                "FOREIGN-778",
                631,
                "Ricoh",
                "MPC 6003",
                "Druk",
                "Platne",
                "Platne",
                "TAK",
                "Inny klient",
            )

        def close(self) -> None:
            return None

    class _ConflictConnection:
        def __init__(self) -> None:
            self.cursor_obj = _ConflictCursor()
            self.committed = False
            self.rolled_back = False

        def cursor(self) -> _ConflictCursor:
            return self.cursor_obj

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            return None

    connection = _ConflictConnection()
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.firebird_writes_enabled",
        lambda: (True, None),
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding._get_firebird_connection",
        lambda: connection,
    )

    workflow_case = SimpleNamespace(firebird_client_id=2924, form_request_id=31)
    devices = [
        SimpleNamespace(
            id=1,
            source_row=778,
            source_type="firebird_magazyn_28",
            producer="Ricoh",
            model="MPC 6003",
            serial="FOREIGN-778",
            ewidencja="KP/778",
            firebird_machine_id=None,
            snapshot={},
        ),
        SimpleNamespace(
            id=2,
            source_row=779,
            source_type="firebird_magazyn_28",
            producer="Ricoh",
            model="MPC 6003",
            serial="SAFE-779",
            ewidencja="KP/779",
            firebird_machine_id=None,
            snapshot={},
        ),
    ]

    with pytest.raises(WorkflowDeviceOwnershipConflict, match="Inny klient"):
        bind_devices_to_workflow_client(
            workflow_case=workflow_case,
            devices=devices,
            actor_label="Operator Test",
        )

    assert connection.cursor_obj.updates == 0
    assert connection.committed is False
    assert connection.rolled_back is True


def test_bind_target_device_records_success_without_firebird_update(monkeypatch) -> None:
    class _TargetCursor:
        def __init__(self) -> None:
            self.query = ""
            self.updates = 0

        def execute(self, query: str, params: tuple[object, ...]) -> None:
            self.query = query
            if query.strip().upper().startswith("UPDATE MASZYNA SET"):
                self.updates += 1

        def fetchone(self) -> tuple[object, ...] | None:
            if "FROM MASZYNA" not in self.query:
                return None
            return (
                777,
                2924,
                "KP/777/GRENKE",
                "TAK",
                1,
                "TARGET-777",
                631,
                "Ricoh",
                "IM C300",
                "Druk",
                "Platne",
                "Platne",
                "TAK",
                "Klient docelowy",
            )

        def close(self) -> None:
            return None

    class _TargetConnection:
        def __init__(self) -> None:
            self.cursor_obj = _TargetCursor()
            self.committed = False

        def cursor(self) -> _TargetCursor:
            return self.cursor_obj

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    connection = _TargetConnection()
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.firebird_writes_enabled",
        lambda: (True, None),
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding._get_firebird_connection",
        lambda: connection,
    )
    workflow_case = SimpleNamespace(firebird_client_id=2924, form_request_id=59)
    device = SimpleNamespace(
        id=1,
        source_row=777,
        source_type="google_sheet",
        producer="Ricoh",
        model="IM C300",
        serial="TARGET-777",
        ewidencja="KP/777/GRENKE",
        firebird_machine_id=777,
        snapshot={},
    )

    items, errors = bind_devices_to_workflow_client(
        workflow_case=workflow_case,
        devices=[device],
        actor_label="Automat skrzynki GRENKE",
    )

    assert errors == []
    assert items[0].ok is True
    assert items[0].current_client_id == 2924
    assert "nie wykonano zapisu właściciela" in items[0].message
    assert connection.cursor_obj.updates == 0
    assert connection.committed is True


def test_bind_mixed_target_and_warehouse_batch_stops_before_update(monkeypatch) -> None:
    class _MixedCursor:
        def __init__(self) -> None:
            self.query = ""
            self.params: tuple[object, ...] = ()
            self.updates = 0

        def execute(self, query: str, params: tuple[object, ...]) -> None:
            self.query = query
            self.params = params
            if query.strip().upper().startswith("UPDATE MASZYNA SET"):
                self.updates += 1

        def fetchone(self) -> tuple[object, ...] | None:
            if "FROM MASZYNA" not in self.query:
                return None
            machine_id = int(self.params[0])
            client_id = 2924 if machine_id == 777 else 656
            return (
                machine_id,
                client_id,
                f"KP/{machine_id}",
                "TAK",
                1,
                f"SERIAL-{machine_id}",
                631,
                "Ricoh",
                "IM C300",
                "Druk",
                "Platne",
                "Platne",
                "TAK",
                "Klient docelowy" if client_id == 2924 else "Ksero Partner",
            )

        def close(self) -> None:
            return None

    class _MixedConnection:
        def __init__(self) -> None:
            self.cursor_obj = _MixedCursor()
            self.rolled_back = False

        def cursor(self) -> _MixedCursor:
            return self.cursor_obj

        def commit(self) -> None:
            raise AssertionError("Pakiet mieszany nie może zostać zatwierdzony.")

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            return None

    connection = _MixedConnection()
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.firebird_writes_enabled",
        lambda: (True, None),
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding._get_firebird_connection",
        lambda: connection,
    )
    workflow_case = SimpleNamespace(firebird_client_id=2924, form_request_id=60)
    devices = [
        SimpleNamespace(
            id=machine_id,
            source_row=machine_id,
            source_type="google_sheet",
            producer="Ricoh",
            model="IM C300",
            serial=f"SERIAL-{machine_id}",
            ewidencja=f"KP/{machine_id}",
            firebird_machine_id=machine_id,
            snapshot={},
        )
        for machine_id in (777, 778)
    ]

    with pytest.raises(WorkflowDeviceMixedOwnershipHold, match="ręcznego wyjaśnienia"):
        bind_devices_to_workflow_client(
            workflow_case=workflow_case,
            devices=devices,
            actor_label="Automat skrzynki GRENKE",
        )

    assert connection.cursor_obj.updates == 0
    assert connection.rolled_back is True


def test_resolve_machine_id_reports_ambiguous_serial_matches() -> None:
    class _AmbiguousCursor:
        def __init__(self) -> None:
            self.query = ""

        def execute(self, query: str, params: tuple[object, ...]) -> None:
            self.query = query

        def fetchall(self) -> list[tuple[object, ...]]:
            first = (
                778,
                656,
                "KP/778",
                "TAK",
                1,
                "DUPLICATE-778",
                631,
                "Ricoh",
                "MPC 6003",
                "Druk",
                "Platne",
                "Platne",
                "TAK",
                "Ksero Partner",
            )
            second = (779, 1001, *first[2:])
            if "SERIAL2" in self.query:
                return [first, second]
            return [first]

    machine_ids = _resolve_machine_id_for_device(
        _AmbiguousCursor(),
        device=SimpleNamespace(firebird_machine_id=None),
        snapshot={},
        source_context=WorkflowDeviceSourceContext(
            source_type="firebird_magazyn_28",
            source_row=778,
            producer="Ricoh",
            model="MPC 6003",
            serial="DUPLICATE-778",
            ewidencja="KP/778",
        ),
    )

    assert machine_ids == [778, 779]


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


def test_bind_devices_uses_existing_machine_model_when_warehouse_model_is_missing(
    monkeypatch,
) -> None:
    class _FallbackCursor:
        def __init__(self) -> None:
            self.query = ""
            self.updates: list[tuple[str, tuple[object, ...]]] = []
            self.rowcount = 1

        def execute(self, query: str, params: tuple[object, ...]) -> None:
            self.query = query
            if query.strip().upper().startswith("UPDATE MASZYNA SET"):
                self.updates.append((query, params))

        def fetchone(self) -> tuple[object, ...] | None:
            if "FROM MAGAZYN" in self.query:
                return (
                    18479,
                    28,
                    "KP/5152",
                    "Ricoh IM430 S/N:3359PA02610",
                    "",
                    "",
                    "NIE",
                    None,
                )
            if "FROM MASZYNA" in self.query:
                return (
                    7712,
                    656,
                    "KP/5152/R/E",
                    "TAK",
                    1,
                    "3359PA02610",
                    460,
                    "Ricoh",
                    "IM 430",
                    "Druk",
                    "MFP A4",
                    "Płatne",
                    "NIE",
                    "MAGAZYN KSERO-PARTNER",
                )
            return None

        def close(self) -> None:
            return None

    class _FallbackConnection:
        def __init__(self) -> None:
            self.cursor_obj = _FallbackCursor()
            self.committed = False
            self.rolled_back = False

        def cursor(self) -> _FallbackCursor:
            return self.cursor_obj

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            return None

    requested_model_ids: list[int] = []

    def find_model_by_id(model_id: int) -> FirebirdModelMatch:
        requested_model_ids.append(model_id)
        return FirebirdModelMatch(
            found=model_id == 460,
            id_model=460 if model_id == 460 else None,
            marka="Ricoh" if model_id == 460 else None,
            model="IM 430" if model_id == 460 else None,
            grupa="Druk" if model_id == 460 else None,
            rodzaj="MFP A4" if model_id == 460 else None,
            kolor="NIE" if model_id == 460 else None,
            plik="ricoh_im430.png" if model_id == 460 else None,
        )

    fake_connection = _FallbackConnection()
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.firebird_writes_enabled",
        lambda: (True, None),
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding._get_firebird_connection",
        lambda: fake_connection,
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.find_model_in_firebird_by_id",
        find_model_by_id,
    )
    monkeypatch.setattr(
        "app.services.workflow_machine_binding.find_model_in_firebird",
        lambda _name: FirebirdModelMatch(found=False),
    )

    workflow_case = SimpleNamespace(firebird_client_id=6485, form_request_id=70)
    device = SimpleNamespace(
        id=76,
        source_row=18479,
        source_type="firebird_magazyn_28",
        producer="Ricoh",
        model="IM430",
        serial="3359PA02610",
        ewidencja="KP/5152",
        firebird_machine_id=None,
        snapshot={
            "row": 18479,
            "source_type": "firebird_magazyn_28",
            "name": "Ricoh IM430 S/N:3359PA02610",
            "model": "IM430",
            "index": "KP/5152",
            "serial": "3359PA02610",
            "ms_id_model": "",
        },
    )

    items, errors = bind_devices_to_workflow_client(
        workflow_case=workflow_case,
        devices=[device],
        actor_label="Operator Test",
    )

    assert errors == []
    assert requested_model_ids == [460]
    assert len(items) == 1
    assert items[0].ok is True
    assert items[0].machine_id == 7712
    assert items[0].model == "IM 430"
    assert "MASZYNA.ID_MODEL" in items[0].message
    assert fake_connection.committed is True
    assert fake_connection.rolled_back is False
    assert len(fake_connection.cursor_obj.updates) == 1
    update_query, _ = fake_connection.cursor_obj.updates[0]
    assert "ID_KLIENT = ?" in update_query
    assert "ID_MODEL = ?" not in update_query


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
