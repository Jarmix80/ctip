"""Testy automatycznego uzgadniania aktywnych przesyłek Shipping z MS."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, time
from decimal import Decimal
from unittest.mock import MagicMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import AdminUser, Base, ShippingCase, ShippingEvent, ShippingItem, ShippingShipment
from app.services.shipping_firebird import inspect_active_shipping_orders
from app.services.shipping_ms_reconciliation import (
    CONFLICT_EVENT,
    RECONCILED_EVENT,
    RESTORED_EVENT,
    reconcile_active_shipping_shipments,
)


def _candidate(*, mode: str = "rw", price: str = "60") -> dict:
    return {
        "shipment_id": 1,
        "order_table_id": 1001,
        "order_id": 77,
        "order_year": 2026,
        "client_id": 15,
        "tracking_number": "123456789",
        "document_mode": mode,
        "items": [
            {
                "warehouse_item_id": 501,
                "warehouse_id": 1,
                "quantity": "1.000",
                "price_net": price,
                "vat_rate": "23.000",
            }
        ],
    }


def _inspection(classification: str) -> dict:
    return {
        "order_table_id": 1001,
        "classification": classification,
        "message": (
            "Ręczne zamknięcie jest zgodne."
            if classification == "external_closed"
            else (
                "Numer przesyłki jest inny."
                if classification == "conflict"
                else "Stan jest zgodny."
            )
        ),
        "state_token": f"token-{classification}",
        "snapshot": {
            "order_table_id": 1001,
            "status": "Z" if classification == "external_closed" else "ZR",
        },
        "documents": {
            "rw_id": 38935,
            "rw_number": "RW / 2201 / 2026",
            "wz_id": None,
            "wz_number": None,
            "invoice_id": None,
            "invoice_number": None,
        },
        "closed_at": "2026-09-08T08:10:27+00:00",
        "closing_operator": "Agnieszka Gołembiewska",
    }


class ShippingFirebirdReconciliationTests(unittest.TestCase):
    def _inspect(
        self,
        *,
        candidate: dict,
        order_row: tuple,
        fetchone_rows: list[tuple],
        fetchall_rows: list[list[tuple]],
    ) -> dict:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [order_row, *fetchone_rows]
        cursor.fetchall.side_effect = fetchall_rows
        with patch(
            "app.services.shipping_firebird.firebird_connection",
            return_value=connection,
        ):
            result = inspect_active_shipping_orders([candidate])[0]
        connection.close.assert_called_once()
        return result

    def test_zgodne_reczne_rw_jest_kandydatem_do_synchronizacji(self) -> None:
        result = self._inspect(
            candidate=_candidate(),
            order_row=(
                1001,
                77,
                2026,
                15,
                "Z",
                "123456789",
                None,
                None,
                38935,
                "RW / 2201 / 2026",
                None,
                None,
                date(2026, 9, 8),
                time(10, 10, 27),
                "Utworzył: Joanna, Zamknął :Agnieszka",
            ),
            fetchone_rows=[
                (0,),
                (
                    38935,
                    "RW",
                    "RW / 2201 / 2026",
                    15,
                    77,
                    2026,
                    1,
                    date(2026, 9, 8),
                    "Agnieszka",
                ),
            ],
            fetchall_rows=[
                [("RW", 1)],
                [(501, 1, Decimal("60"), Decimal("1"), "23 %", Decimal("1"))],
            ],
        )

        self.assertEqual(result["classification"], "external_closed")
        self.assertEqual(result["documents"]["rw_id"], 38935)
        self.assertEqual(result["closing_operator"], "Agnieszka")

    def test_zgodne_reczne_wz_jest_kandydatem_do_synchronizacji(self) -> None:
        result = self._inspect(
            candidate=_candidate(mode="wz", price="120"),
            order_row=(
                1001,
                77,
                2026,
                15,
                "Z",
                "123456789",
                None,
                38936,
                None,
                "WZ / 196 / 2026",
                None,
                None,
                date(2026, 9, 8),
                time(10, 10, 27),
                "Zamknął :Agnieszka",
            ),
            fetchone_rows=[
                (0,),
                (
                    38936,
                    "WZ",
                    "WZ / 196 / 2026",
                    15,
                    77,
                    2026,
                    1,
                    date(2026, 9, 8),
                    "Agnieszka",
                ),
            ],
            fetchall_rows=[
                [("WZ", 1)],
                [(501, 1, Decimal("120"), Decimal("1"), "23 %", Decimal("1"))],
            ],
        )

        self.assertEqual(result["classification"], "external_closed")
        self.assertEqual(result["documents"]["wz_id"], 38936)

    def test_zgodna_faktura_z_wz_jest_kandydatem_do_synchronizacji(self) -> None:
        result = self._inspect(
            candidate=_candidate(mode="invoice_wz", price="120"),
            order_row=(
                1001,
                77,
                2026,
                15,
                "Z",
                "123456789",
                64557,
                None,
                None,
                "5514/KPSK/2026",
                None,
                None,
                date(2026, 9, 8),
                time(10, 10, 27),
                "Zamknął :Agnieszka",
            ),
            fetchone_rows=[
                (1,),
                (
                    64557,
                    "KPSK",
                    "5514/KPSK/2026",
                    15,
                    77,
                    2026,
                    1,
                    38936,
                    date(2026, 9, 8),
                    "Agnieszka",
                ),
                (
                    38936,
                    "WZ",
                    "WZ / 196 / 2026",
                    15,
                    77,
                    2026,
                    1,
                    date(2026, 9, 8),
                    "Agnieszka",
                ),
            ],
            fetchall_rows=[
                [("WZ", 1)],
                [
                    (
                        501,
                        1,
                        Decimal("120"),
                        Decimal("1"),
                        "23 %",
                        Decimal("1"),
                        Decimal("1"),
                    )
                ],
                [(501, 1, Decimal("120"), Decimal("1"), "23 %", Decimal("1"))],
            ],
        )

        self.assertEqual(result["classification"], "external_closed")
        self.assertEqual(result["documents"]["invoice_id"], 64557)
        self.assertEqual(result["documents"]["wz_id"], 38936)

    def test_inna_cena_blokuje_automatyczna_synchronizacje(self) -> None:
        result = self._inspect(
            candidate=_candidate(),
            order_row=(
                1001,
                77,
                2026,
                15,
                "Z",
                "123456789",
                None,
                None,
                38935,
                "RW / 2201 / 2026",
                None,
                None,
                date(2026, 9, 8),
                time(10, 10, 27),
                "Zamknął :Agnieszka",
            ),
            fetchone_rows=[
                (0,),
                (
                    38935,
                    "RW",
                    "RW / 2201 / 2026",
                    15,
                    77,
                    2026,
                    1,
                    date(2026, 9, 8),
                    "Agnieszka",
                ),
            ],
            fetchall_rows=[
                [("RW", 1)],
                [(501, 1, Decimal("61"), Decimal("1"), "23 %", Decimal("1"))],
            ],
        )

        self.assertEqual(result["classification"], "conflict")
        self.assertIn("Cena netto", result["message"])


class ShippingMsReconciliationTests(unittest.IsolatedAsyncioTestCase):
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
                    ShippingCase.__table__,
                    ShippingItem.__table__,
                    ShippingShipment.__table__,
                    ShippingEvent.__table__,
                ],
            )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _create_active_shipment(self) -> int:
        async with self.session_factory() as session:
            case = ShippingCase(
                firebird_order_table_id=1001,
                firebird_order_id=77,
                firebird_order_year=2026,
                firebird_client_id=15,
                order_kind="Umowa",
                invoice_required=False,
                status="shipment_created",
                address_snapshot={"company_name": "Klient", "phone": "+48500600700"},
                source_snapshot={"problem": "Wyślij toner"},
                weight_kg=Decimal("2.000"),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(case)
            await session.flush()
            session.add(
                ShippingItem(
                    shipping_case_id=case.id,
                    firebird_warehouse_item_id=501,
                    warehouse_id=1,
                    item_name="Toner",
                    unit="szt.",
                    quantity=Decimal("1.000"),
                    price_net=Decimal("60.0000"),
                    catalog_price_net=Decimal("120.0000"),
                    purchase_price_net=Decimal("60.0000"),
                    price_source="purchase_contract",
                    vat_rate=Decimal("23.000"),
                    created_at=datetime.now(UTC),
                )
            )
            shipment = ShippingShipment(
                shipping_case_id=case.id,
                idempotency_key="shipping-reconcile-test",
                provider="dpd",
                provider_mode="production",
                tracking_number="123456789",
                status="label_ready",
                provider_request={},
                firebird_status="written",
                notification_sms_status="pending",
                notification_email_status="pending",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(shipment)
            await session.commit()
            return int(shipment.id)

    async def test_zgodne_zamkniecie_przejmuje_dokument_bez_powiadomien(self) -> None:
        shipment_id = await self._create_active_shipment()
        async with self.session_factory() as session:
            result = await reconcile_active_shipping_shipments(
                session,
                apply=True,
                trigger_type="scheduler",
                inspector=lambda _candidates: [_inspection("external_closed")],
            )
            await session.commit()

        async with self.session_factory() as session:
            shipment = await session.get(ShippingShipment, shipment_id)
            event = await session.scalar(
                select(ShippingEvent).where(ShippingEvent.event_type == RECONCILED_EVENT)
            )
            self.assertEqual(result["reconciled_count"], 1)
            self.assertEqual(shipment.status, "closed")
            self.assertEqual(shipment.firebird_rw_id, 38935)
            self.assertEqual(shipment.notification_sms_status, "skipped_external")
            self.assertEqual(shipment.notification_email_status, "skipped_external")
            self.assertIsNone(shipment.closed_by)
            self.assertEqual(
                shipment.archive_snapshot["operators"]["closed"]["name"],
                "Agnieszka Gołembiewska",
            )
            self.assertIsNotNone(event)

    async def test_powtorzenie_nie_duplikuje_zdarzenia_zamkniecia(self) -> None:
        await self._create_active_shipment()
        async with self.session_factory() as session:
            await reconcile_active_shipping_shipments(
                session,
                apply=True,
                trigger_type="scheduler",
                inspector=lambda _candidates: [_inspection("external_closed")],
            )
            await session.commit()
        async with self.session_factory() as session:
            result = await reconcile_active_shipping_shipments(
                session,
                apply=True,
                trigger_type="scheduler",
                inspector=lambda _candidates: [],
            )
            await session.commit()
            count = await session.scalar(
                select(func.count())
                .select_from(ShippingEvent)
                .where(ShippingEvent.event_type == RECONCILED_EVENT)
            )

        self.assertEqual(result["scanned_count"], 0)
        self.assertEqual(count, 1)

    async def test_konflikt_jest_blokowany_i_deduplikowany(self) -> None:
        shipment_id = await self._create_active_shipment()
        for _ in range(2):
            async with self.session_factory() as session:
                await reconcile_active_shipping_shipments(
                    session,
                    apply=True,
                    trigger_type="scheduler",
                    inspector=lambda _candidates: [_inspection("conflict")],
                )
                await session.commit()

        async with self.session_factory() as session:
            shipment = await session.get(ShippingShipment, shipment_id)
            count = await session.scalar(
                select(func.count())
                .select_from(ShippingEvent)
                .where(ShippingEvent.event_type == CONFLICT_EVENT)
            )
            self.assertEqual(shipment.status, "reconcile_required")
            self.assertTrue(shipment.firebird_error.startswith("Ręczna zmiana MS:"))
            self.assertEqual(count, 1)

    async def test_usuniecie_konfliktu_przywraca_poprzedni_stan(self) -> None:
        shipment_id = await self._create_active_shipment()
        async with self.session_factory() as session:
            await reconcile_active_shipping_shipments(
                session,
                apply=True,
                trigger_type="scheduler",
                inspector=lambda _candidates: [_inspection("conflict")],
            )
            await session.commit()
        async with self.session_factory() as session:
            result = await reconcile_active_shipping_shipments(
                session,
                apply=True,
                trigger_type="scheduler",
                inspector=lambda _candidates: [_inspection("healthy")],
            )
            await session.commit()

        async with self.session_factory() as session:
            shipment = await session.get(ShippingShipment, shipment_id)
            shipping_case = await session.get(ShippingCase, shipment.shipping_case_id)
            restored = await session.scalar(
                select(ShippingEvent).where(ShippingEvent.event_type == RESTORED_EVENT)
            )
            self.assertEqual(result["restored_count"], 1)
            self.assertEqual(shipment.status, "label_ready")
            self.assertEqual(shipping_case.status, "shipment_created")
            self.assertIsNone(shipment.firebird_error)
            self.assertIsNotNone(restored)

    async def test_dry_run_nie_zmienia_rekordu(self) -> None:
        shipment_id = await self._create_active_shipment()
        async with self.session_factory() as session:
            result = await reconcile_active_shipping_shipments(
                session,
                apply=False,
                trigger_type="script",
                inspector=lambda _candidates: [_inspection("external_closed")],
            )

        async with self.session_factory() as session:
            shipment = await session.get(ShippingShipment, shipment_id)
            count = await session.scalar(select(func.count()).select_from(ShippingEvent))
            self.assertEqual(result["reconciled_count"], 1)
            self.assertEqual(shipment.status, "label_ready")
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
