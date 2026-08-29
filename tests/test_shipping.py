"""Testy procesu wysyłki części, etykiety DPD i zamknięcia dnia."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import (
    AdminUser,
    Base,
    ShippingAddress,
    ShippingCase,
    ShippingConsumableCompatibility,
    ShippingDayClose,
    ShippingEvent,
    ShippingItem,
    ShippingShipment,
)
from app.schemas.shipping import ShippingReviewRequest
from app.services.dpd_shipping import DpdConfigurationError, DpdShippingClient
from app.services.shipping_firebird import _vat_rate
from app.services.shipping_workflow import (
    close_shipping_day,
    create_shipping_shipment,
    review_shipping_order,
)


def _order(order_table_id: int = 1001) -> dict:
    return {
        "order_table_id": order_table_id,
        "order_id": 77,
        "order_year": 2026,
        "client_id": 15,
        "machine_id": 44,
        "model_id": 900,
        "order_kind": "Umowa",
        "order_location": "Sekretariat, parter",
        "machine_location": None,
        "device_brand": "Ricoh",
        "device_model": "IM C3000",
        "problem": "Proszę wysłać toner czarny",
    }


def _stock(quantity: float = 5.0) -> list[dict]:
    return [
        {
            "warehouse_item_id": 501,
            "warehouse_id": 1,
            "item_kind": "1. Część zamienna",
            "item_index": "IMC3000-BK",
            "item_name": "Toner Ricoh IM C3000 czarny",
            "unit": "szt.",
            "stock_quantity": quantity,
            "reserved_quantity": 0.0,
            "available_quantity": quantity,
            "price_net": 120.0,
            "purchase_price_net": 60.0,
            "vat_rate": 23.0,
        }
    ]


def _review_payload() -> ShippingReviewRequest:
    return ShippingReviewRequest.model_validate(
        {
            "address": {
                "company_name": "Przykładowa Firma",
                "contact_name": "Jan Kowalski",
                "street": "Testowa 10",
                "postal_code": "00-001",
                "city": "Warszawa",
                "country_code": "PL",
                "phone": "500 600 700",
                "email": "klient@example.com",
                "source": "location",
                "location_text": "Sekretariat, parter",
            },
            "weight_kg": "2.000",
            "items": [
                {
                    "firebird_warehouse_item_id": 501,
                    "quantity": "1.000",
                    "remember_for_model": True,
                }
            ],
            "save_address": True,
        }
    )


class DpdShippingClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = {
            "dpd_enabled": settings.dpd_enabled,
            "dpd_test_mode": settings.dpd_test_mode,
        }

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            setattr(settings, key, value)

    def test_mock_generuje_etykiete_pdf_a4(self) -> None:
        settings.dpd_enabled = True
        settings.dpd_test_mode = True
        payload, result = DpdShippingClient().create_shipment(
            idempotency_key=str(uuid4()),
            reference="MS-2026-77",
            receiver=_review_payload().address.model_dump(mode="json"),
            weight_kg=2.0,
        )
        self.assertEqual(payload["service"]["type"], "DOMESTIC_STANDARD")
        self.assertTrue(result.tracking_number.startswith("MOCK"))
        self.assertTrue(result.label_content.startswith(b"%PDF"))
        self.assertEqual(result.label_format, "A4")

    def test_wylaczona_integracja_blokuje_nadanie(self) -> None:
        settings.dpd_enabled = False
        settings.dpd_test_mode = True
        with self.assertRaises(DpdConfigurationError):
            DpdShippingClient().create_shipment(
                idempotency_key=str(uuid4()),
                reference="MS-2026-77",
                receiver=_review_payload().address.model_dump(mode="json"),
                weight_kg=2.0,
            )


class ShippingWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    AdminUser.__table__,
                    ShippingAddress.__table__,
                    ShippingConsumableCompatibility.__table__,
                    ShippingCase.__table__,
                    ShippingItem.__table__,
                    ShippingDayClose.__table__,
                    ShippingShipment.__table__,
                    ShippingEvent.__table__,
                ],
            )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )
        self.previous_dpd_enabled = settings.dpd_enabled
        self.previous_dpd_test_mode = settings.dpd_test_mode
        settings.dpd_enabled = True
        settings.dpd_test_mode = True
        async with self.session_factory() as session:
            session.add(
                AdminUser(
                    id=1,
                    email="operator@example.com",
                    role="operator",
                    password_hash="test",
                    is_active=True,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        settings.dpd_enabled = self.previous_dpd_enabled
        settings.dpd_test_mode = self.previous_dpd_test_mode
        await self.engine.dispose()

    async def test_review_zapisuje_adres_rezerwacje_i_zgodnosc(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_toner_stock", return_value=_stock()):
                result = await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["address"]["phone"], "+48500600700")
            self.assertEqual(result["items"][0]["firebird_warehouse_item_id"], 501)
            self.assertEqual(
                await session.scalar(
                    select(func.count()).select_from(ShippingConsumableCompatibility)
                ),
                1,
            )

    async def test_mock_nadanie_jest_idempotentne_i_nie_zapisuje_firebirda(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_toner_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            key = str(uuid4())
            first = await create_shipping_shipment(
                session,
                order_table_id=1001,
                idempotency_key=key,
                user_id=1,
            )
            second = await create_shipping_shipment(
                session,
                order_table_id=1001,
                idempotency_key=key,
                user_id=1,
            )
            self.assertEqual(first["shipment"]["id"], second["shipment"]["id"])
            self.assertEqual(first["shipment"]["firebird_status"], "simulated")
            shipment = await session.get(ShippingShipment, first["shipment"]["id"])
            self.assertTrue(shipment.label_content.startswith(b"%PDF"))

    async def test_zamkniecie_dnia_mock_zamyka_umowe_i_symuluje_powiadomienia(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_toner_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            created = await create_shipping_shipment(
                session,
                order_table_id=1001,
                idempotency_key=str(uuid4()),
                user_id=1,
            )
            result = await close_shipping_day(
                session,
                business_date=datetime.now(ZoneInfo("Europe/Warsaw")).date(),
                user_id=1,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["closed_count"], 1)
            shipment = await session.get(ShippingShipment, created["shipment"]["id"])
            self.assertEqual(shipment.status, "closed")
            self.assertEqual(shipment.notification_sms_status, "simulated")
            self.assertEqual(shipment.notification_email_status, "simulated")

    async def test_zamkniecie_dnia_przekazuje_do_rw_zaakceptowane_ilosci(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_toner_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            case = await session.scalar(select(ShippingCase))
            shipment = ShippingShipment(
                shipping_case_id=case.id,
                idempotency_key=str(uuid4()),
                provider_mode="manual",
                provider_shipment_id="123456789",
                tracking_number="123456789",
                status="label_ready",
                provider_request={},
                provider_response={},
                firebird_status="written",
                created_by=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(shipment)
            case.status = "shipment_created"
            await session.commit()

            with (
                patch(
                    "app.services.shipping_workflow.create_rw_and_close_order",
                    return_value={"status": "created", "rw_id": 1, "rw_number": "1/2026"},
                ) as create_rw,
                patch(
                    "app.services.shipping_workflow._send_notifications",
                    return_value=[],
                ),
            ):
                await close_shipping_day(
                    session,
                    business_date=datetime.now(ZoneInfo("Europe/Warsaw")).date(),
                    user_id=1,
                )

            self.assertEqual(
                create_rw.call_args.kwargs["items"],
                [{"firebird_warehouse_item_id": 501, "quantity": 1.0}],
            )


class ShippingSchemaTests(unittest.TestCase):
    def test_polski_numer_telefonu_jest_normalizowany(self) -> None:
        payload = _review_payload()
        self.assertEqual(payload.address.phone, "+48500600700")
        self.assertEqual(payload.weight_kg, Decimal("2.000"))

    def test_tekstowa_stawka_vat_z_firebirda_jest_normalizowana(self) -> None:
        self.assertEqual(_vat_rate("23 %"), Decimal("23"))
