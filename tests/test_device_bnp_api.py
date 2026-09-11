"""Testy API procesu wykupu urządzeń BNP."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError
from starlette.testclient import TestClient

from app.api import deps
from app.api.routes.admin_device import (
    DeviceBnpBuyoutCompleteRequest,
    DeviceBnpCatalogCreateRequest,
)
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
            SimpleNamespace(id=7, role="operator", firebird_app_user_id=12),
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
        "machine": {"id_maszyna_table": 5189, "ewidencja": "KP/4579/SRS"},
        "suggested_ewidencja": "WKP/4579/SRS",
        "suggested_index": "WKP/4579/BNP",
        "identifier_mode": "kp",
        "identifier_value": "4579",
        "can_create_catalog": True,
        "can_complete": False,
        "blockers": [],
        "warnings": [],
    }
    with (
        patch(
            "app.api.routes.admin_device._ensure_device_access",
            new=AsyncMock(),
        ),
        patch(
            "app.api.routes.admin_device._run_firebird_read",
            new=AsyncMock(return_value=expected_lookup),
        ) as read_mock,
    ):
        response = client.get(
            "/admin/device/bnp-buyout/lookup?serial=3101RC80528",
            headers={"X-Admin-Session": "test-token"},
        )

    assert response.status_code == 200
    assert response.json()["lookup"]["suggested_index"] == "WKP/4579/BNP"
    assert response.json()["lookup"]["identifier_mode"] == "kp"
    assert response.json()["lookup"]["identifier_value"] == "4579"
    assert read_mock.await_args.kwargs["serial"] == "3101RC80528"


@pytest.mark.parametrize("expected_ewidencja", ["KP/4579/SRS", ""])
def test_device_bnp_catalog_create_zapisuje_audyt(expected_ewidencja: str) -> None:
    """API przekazuje także pustą ewidencję do serwisu i rejestruje przygotowanie kartoteki."""
    client, db_session = _build_client()
    expected = BnpCatalogResult(
        created=True,
        warehouse_item={
            "id_magazyn_table": 18592,
            "id_magazyn": 27,
            "index": "WKP/4579/BNP",
            "name": "Ricoh IM C3000",
            "quantity": Decimal("0"),
        },
    )
    with (
        patch(
            "app.api.routes.admin_device._ensure_device_writer",
            new=AsyncMock(return_value=SimpleNamespace(login_user="OPERATOR")),
        ),
        patch(
            "app.api.routes.admin_device._ensure_firebird_write_enabled",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "app.api.routes.admin_device.use_firebird_runtime_config",
            return_value=nullcontext(),
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
                "expected_ewidencja": expected_ewidencja,
                "warehouse_index": "WKP/4579/BNP",
                "item_name": "Ricoh IM C3000",
            },
        )

    assert response.status_code == 200
    assert response.json()["warehouse_item"]["id_magazyn_table"] == 18592
    create_mock.assert_called_once_with(
        serial="3101RC80528",
        machine_table_id=5189,
        expected_ewidencja=expected_ewidencja,
        warehouse_index="WKP/4579/BNP",
        item_name="Ricoh IM C3000",
        kto="CTIP/OPERATOR",
    )
    assert db_session.commits == 1
    assert any(
        getattr(entry, "action", "") == "device_bnp_catalog_create" for entry in db_session.added
    )


@pytest.mark.parametrize("expected_ewidencja", ["KP/4579/SRS", ""])
def test_device_bnp_complete_tworzy_pz_i_zapisuje_audyt(expected_ewidencja: str) -> None:
    """API dopuszcza pustą ewidencję źródłową i zachowuje audyt finalizacji."""
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
        previous_ewidencja=expected_ewidencja,
        target_ewidencja="WKP/4579/SRS",
        supplier_id=1937,
        external_document="FWK26/06/00093",
        purchase_price_netto=Decimal("23.35"),
    )
    with (
        patch(
            "app.api.routes.admin_device._ensure_device_writer",
            new=AsyncMock(return_value=SimpleNamespace(login_user="OPERATOR")),
        ),
        patch(
            "app.api.routes.admin_device._ensure_firebird_write_enabled",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "app.api.routes.admin_device.use_firebird_runtime_config",
            return_value=nullcontext(),
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
                "expected_ewidencja": expected_ewidencja,
                "target_ewidencja": "WKP/4579/SRS",
                "warehouse_index": "WKP/4579/BNP",
                "item_name": "Ricoh IM C3000",
                "external_document": "FWK26/06/00093",
                "document_date": "2026-06-30",
                "purchase_price_netto": "23.35",
            },
        )

    assert response.status_code == 200
    assert response.json()["buyout"]["warehouse_quantity"] == 1.0
    complete_mock.assert_called_once_with(
        serial="3101RC80528",
        machine_table_id=5189,
        warehouse_item_id=18592,
        expected_ewidencja=expected_ewidencja,
        target_ewidencja="WKP/4579/SRS",
        warehouse_index="WKP/4579/BNP",
        item_name="Ricoh IM C3000",
        external_document="FWK26/06/00093",
        document_date=date(2026, 6, 30),
        purchase_price_netto=Decimal("23.35"),
        issued_by="OPERATOR",
        kto="CTIP/OPERATOR",
    )
    assert db_session.commits == 1
    assert any(
        getattr(entry, "action", "") == "device_bnp_buyout_complete" for entry in db_session.added
    )


def test_device_bnp_catalog_respektuje_blokade_zapisu() -> None:
    client, db_session = _build_client()
    with (
        patch(
            "app.api.routes.admin_device._ensure_device_writer",
            new=AsyncMock(return_value=SimpleNamespace(login_user="OPERATOR")),
        ),
        patch(
            "app.api.routes.admin_device._ensure_firebird_write_enabled",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Zapis testowy jest zablokowany.",
                )
            ),
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
                "item_name": "Ricoh IM C3000",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Zapis testowy jest zablokowany."
    assert db_session.added == []


@pytest.mark.parametrize(
    "request_model", [DeviceBnpCatalogCreateRequest, DeviceBnpBuyoutCompleteRequest]
)
@pytest.mark.parametrize("invalid_value", [None, "A" * 101])
def test_zadanie_bnp_wymaga_pola_ewidencji_o_poprawnej_dlugosci(
    request_model, invalid_value
) -> None:
    """Pusta wartość jest dozwolona, ale null, pominięcie i przekroczenie limitu nie są."""
    payload = {
        "serial": "ABC123",
        "machine_table_id": 1,
        "expected_ewidencja": "",
        "warehouse_index": "WKP/ABC123",
        "item_name": "Urządzenie",
    }
    if request_model is DeviceBnpBuyoutCompleteRequest:
        payload.update(
            warehouse_item_id=2,
            target_ewidencja="WKP/ABC123",
            external_document="FV/TEST",
            document_date="2026-09-10",
            purchase_price_netto="24.00",
        )
    assert request_model.model_validate(payload).expected_ewidencja == ""
    payload["expected_ewidencja"] = invalid_value
    with pytest.raises(ValidationError) as invalid_error:
        request_model.model_validate(payload)
    assert invalid_error.value.errors()[0]["loc"] == ("expected_ewidencja",)
    del payload["expected_ewidencja"]
    with pytest.raises(ValidationError) as missing_error:
        request_model.model_validate(payload)
    assert missing_error.value.errors()[0]["loc"] == ("expected_ewidencja",)
    assert missing_error.value.errors()[0]["type"] == "missing"
