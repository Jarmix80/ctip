# ruff: noqa: E402

"""Testy API wykupu urządzeń BNP."""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.api import deps
from app.main import create_app
from app.services.device_bnp_buyout import BnpBuyoutResult, BnpCatalogResult


class _FakeDbSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


def _build_client() -> tuple[TestClient, _FakeDbSession]:
    app = create_app()
    db_session = _FakeDbSession()

    async def fake_admin_context():
        return (
            SimpleNamespace(client_ip="127.0.0.1"),
            SimpleNamespace(
                id=7,
                role="operator",
                email="operator@example.com",
            ),
        )

    async def fake_db_session():
        yield db_session

    app.dependency_overrides[deps.get_admin_session_context] = fake_admin_context
    app.dependency_overrides[deps.get_db_session] = fake_db_session
    return TestClient(app), db_session


def test_device_bnp_lookup_zwraca_maszyne_i_status_kartoteki() -> None:
    client, _db_session = _build_client()
    expected_lookup = {
        "serial": "3101RC80528",
        "machines": [{"id_maszyna_table": 5189}],
        "machine": {
            "id_maszyna_table": 5189,
            "client_name": "KLIENT TESTOWY",
            "ewidencja": "KP/4579/SRS",
        },
        "supplier": {"id_klient": 1937, "name": "BNP PARIBAS LEASE GROUP"},
        "warehouse_rows": [],
        "target_item": None,
        "suggested_ewidencja": "WKP/4579/SRS",
        "suggested_index": "WKP/4579/BNP",
        "can_create_catalog": True,
        "can_complete": False,
        "blockers": [],
        "warnings": [],
    }
    with (
        patch(
            "app.api.routes.admin_device.section_permissions.user_has_section",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.routes.admin_device.lookup_bnp_buyout",
            return_value=expected_lookup,
        ) as lookup_mock,
    ):
        response = client.get(
            "/admin/device/bnp-buyout/lookup?serial=3101RC80528",
            headers={"X-Admin-Session": "test-token"},
        )

    assert response.status_code == 200
    assert response.json()["lookup"]["suggested_index"] == "WKP/4579/BNP"
    lookup_mock.assert_called_once_with(serial="3101RC80528")


def test_device_bnp_catalog_create_zapisuje_audyt() -> None:
    client, db_session = _build_client()
    expected = BnpCatalogResult(
        created=True,
        warehouse_item={
            "id_magazyn_table": 18592,
            "id_magazyn": 27,
            "index": "WKP/4579/BNP",
            "name": "Ricoh IM C3000 S/N: 3101RC80528",
            "quantity": Decimal("0"),
        },
    )
    with (
        patch(
            "app.api.routes.admin_device.section_permissions.user_has_section",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.routes.admin_device.firebird_writes_enabled",
            return_value=(True, None),
        ),
        patch(
            "app.api.routes.admin_device.create_bnp_catalog_item",
            return_value=expected,
        ) as create_mock,
    ):
        response = client.post(
            "/admin/device/bnp-buyout/catalog",
            headers={"X-Admin-Session": "test-token"},
            json={
                "serial": "3101RC80528",
                "machine_table_id": 5189,
                "expected_ewidencja": "KP/4579/SRS",
                "warehouse_index": "WKP/4579/BNP",
                "item_name": "Ricoh IM C3000 S/N: 3101RC80528",
            },
        )

    assert response.status_code == 200
    assert response.json()["warehouse_item"]["id_magazyn_table"] == 18592
    create_mock.assert_called_once_with(
        serial="3101RC80528",
        machine_table_id=5189,
        expected_ewidencja="KP/4579/SRS",
        warehouse_index="WKP/4579/BNP",
        item_name="Ricoh IM C3000 S/N: 3101RC80528",
        kto="operator@example.com",
    )
    assert db_session.commits == 1
    assert any(
        getattr(entry, "action", "") == "device_bnp_catalog_create" for entry in db_session.added
    )


def test_device_bnp_complete_tworzy_pz_i_zapisuje_audyt() -> None:
    client, db_session = _build_client()
    expected = BnpBuyoutResult(
        already_completed=False,
        pz_id=37904,
        pz_number="PZ / 245 / 2026",
        zakpozycja_id=107982,
        warehouse_item_id=18592,
        warehouse_index="WKP/4579/BNP",
        warehouse_quantity=Decimal("1"),
        machine_id=7112,
        machine_table_id=5189,
        previous_ewidencja="KP/4579/SRS",
        target_ewidencja="WKP/4579/SRS",
        supplier_id=1937,
        external_document="FWK26/06/00093",
        purchase_price_netto=Decimal("23.35"),
    )
    with (
        patch(
            "app.api.routes.admin_device.section_permissions.user_has_section",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.routes.admin_device.firebird_writes_enabled",
            return_value=(True, None),
        ),
        patch(
            "app.api.routes.admin_device.complete_bnp_buyout",
            return_value=expected,
        ) as complete_mock,
    ):
        response = client.post(
            "/admin/device/bnp-buyout/complete",
            headers={"X-Admin-Session": "test-token"},
            json={
                "serial": "3101RC80528",
                "machine_table_id": 5189,
                "warehouse_item_id": 18592,
                "expected_ewidencja": "KP/4579/SRS",
                "target_ewidencja": "WKP/4579/SRS",
                "warehouse_index": "WKP/4579/BNP",
                "item_name": "Ricoh IM C3000 S/N: 3101RC80528",
                "external_document": "FWK26/06/00093",
                "document_date": "2026-06-30",
                "purchase_price_netto": "23.35",
            },
        )

    assert response.status_code == 200
    assert response.json()["buyout"]["pz_id"] == 37904
    assert response.json()["buyout"]["warehouse_quantity"] == 1.0
    complete_mock.assert_called_once_with(
        serial="3101RC80528",
        machine_table_id=5189,
        warehouse_item_id=18592,
        expected_ewidencja="KP/4579/SRS",
        target_ewidencja="WKP/4579/SRS",
        warehouse_index="WKP/4579/BNP",
        item_name="Ricoh IM C3000 S/N: 3101RC80528",
        external_document="FWK26/06/00093",
        document_date=date(2026, 6, 30),
        purchase_price_netto=Decimal("23.35"),
        issued_by="operator@example.com",
        kto="operator@example.com",
    )
    assert db_session.commits == 1
    assert any(
        getattr(entry, "action", "") == "device_bnp_buyout_complete" for entry in db_session.added
    )


def test_device_bnp_catalog_respects_firebird_write_lock() -> None:
    client, db_session = _build_client()
    with (
        patch(
            "app.api.routes.admin_device.section_permissions.user_has_section",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.routes.admin_device.firebird_writes_enabled",
            return_value=(False, "Zapis testowy jest zablokowany."),
        ),
    ):
        response = client.post(
            "/admin/device/bnp-buyout/catalog",
            headers={"X-Admin-Session": "test-token"},
            json={
                "serial": "3101RC80528",
                "machine_table_id": 5189,
                "expected_ewidencja": "KP/4579/SRS",
                "warehouse_index": "WKP/4579/BNP",
                "item_name": "Ricoh IM C3000 S/N: 3101RC80528",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Zapis testowy jest zablokowany."
    assert db_session.added == []
