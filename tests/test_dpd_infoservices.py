"""Testy klienta SOAP i lokalnego rejestru statusów DPD InfoServices."""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import (
    AdminUser,
    Base,
    ShippingCase,
    ShippingShipment,
    ShippingTrackingEvent,
    ShippingTrackingParcel,
    ShippingTrackingSyncRun,
)
from app.services import dpd_infoservices_sync
from app.services.dpd_infoservices import (
    DPD_INFO_PRODUCTION_URL,
    DpdInfoBatch,
    DpdInfoEvent,
    DpdInfoEventData,
    DpdInfoServicesClient,
    DpdInfoTransportError,
)
from app.services.dpd_infoservices_sync import classify_dpd_event, persist_dpd_info_events
from app.services.shipping_tracking import (
    get_shipping_tracking_detail,
    list_shipping_tracking,
)

EVENTS_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
  <S:Body>
    <ns2:getEventsForCustomerV4Response xmlns:ns2="http://events.dpdinfoservices.dpd.com.pl/">
      <return>
        <confirmId>potwierdzenie-1</confirmId>
        <eventsList>
          <businessCode>040101</businessCode>
          <country>PL</country>
          <depot>POZ</depot>
          <depotName>Poznań</depotName>
          <description>Przesyłka odebrana przez Kuriera</description>
          <eventTime>2026-08-28T12:30:15</eventTime>
          <id>991</id>
          <objectId>123456</objectId>
          <operationType>INSERT</operationType>
          <packageReference>18495/2026</packageReference>
          <parcelReference>MS-18495</parcelReference>
          <waybill>0000111122223A</waybill>
        </eventsList>
      </return>
    </ns2:getEventsForCustomerV4Response>
  </S:Body>
</S:Envelope>""".encode()

ACK_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
  <S:Body><return>true</return></S:Body>
</S:Envelope>"""


class DpdInfoServicesClientTests(unittest.TestCase):
    def _settings(self):
        return (
            patch.object(settings, "dpd_info_enabled", True),
            patch.object(settings, "dpd_info_api_url", DPD_INFO_PRODUCTION_URL),
            patch.object(settings, "dpd_info_channel", "123456"),
            patch.object(settings, "dpd_login", "login-testowy"),
            patch.object(settings, "dpd_password", "haslo-testowe"),
        )

    def test_pobiera_zdarzenia_v4_i_potwierdza_partie(self) -> None:
        requests: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content
            requests.append(body)
            response = ACK_RESPONSE if b"markEventsAsProcessedV1" in body else EVENTS_RESPONSE
            return httpx.Response(200, content=response, headers={"Content-Type": "text/xml"})

        contexts = self._settings()
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
            client = DpdInfoServicesClient(transport=httpx.MockTransport(handler))
            batch = client.get_customer_events()
            acknowledged = client.mark_events_processed(str(batch.confirm_id))

        self.assertEqual(batch.confirm_id, "potwierdzenie-1")
        self.assertEqual(len(batch.events), 1)
        self.assertEqual(batch.events[0].waybill, "0000111122223A")
        self.assertEqual(batch.events[0].business_code, "040101")
        self.assertEqual(batch.events[0].depot_name, "Poznań")
        self.assertEqual(batch.events[0].event_time.tzinfo, UTC)
        self.assertTrue(acknowledged)
        self.assertIn(b"<language>PL</language>", requests[0])
        self.assertIn(b"<channel>123456</channel>", requests[0])
        self.assertNotIn("haslo-testowe", str(batch.events[0].as_dict()))

    def test_odrzuca_niepoprawny_xml(self) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"<uszkodzone"))
        contexts = self._settings()
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            contexts[4],
            self.assertRaises(DpdInfoTransportError),
        ):
            DpdInfoServicesClient(transport=transport).get_customer_events()

    def test_klasyfikuje_pusty_podjazd_i_przyjecie_w_sortowni(self) -> None:
        failed_pickup = classify_dpd_event("040200", "Pusty podjazd")
        sorting = classify_dpd_event("050101", "Przyjęcie przesyłki w sortowni")

        self.assertEqual(failed_pickup["category"], "undelivered")
        self.assertTrue(failed_pickup["requires_attention"])
        self.assertEqual(sorting["category"], "in_transit")


class DpdInfoPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
            execution_options={"schema_translate_map": {"ctip": None}},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    AdminUser.__table__,
                    ShippingCase.__table__,
                    ShippingShipment.__table__,
                    ShippingTrackingParcel.__table__,
                    ShippingTrackingEvent.__table__,
                    ShippingTrackingSyncRun.__table__,
                ],
            )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _event(
        *,
        code: str = "040101",
        waybill: str = "0000111122223A",
        object_id: str = "123456",
        operation_type: str = "INSERT",
        description: str = "Przesyłka odebrana przez Kuriera",
        event_data: tuple[DpdInfoEventData, ...] = (),
    ) -> DpdInfoEvent:
        return DpdInfoEvent(
            event_id=f"id-{code}-{operation_type}",
            object_id=object_id,
            business_code=code,
            waybill=waybill,
            description=description,
            event_time=datetime(2026, 8, 28, 12, 30, tzinfo=UTC),
            depot="POZ",
            depot_name="Poznań",
            country="PL",
            package_reference="18495/2026",
            parcel_reference="MS-18495",
            operation_type=operation_type,
            event_data=event_data,
        )

    async def test_zapis_jest_idempotentny_i_cancel_przelicza_status(self) -> None:
        insert = self._event()
        insert_without_response_id = replace(insert, event_id=None)
        cancel = self._event(
            waybill="",
            operation_type="CANCEL",
            code="",
            description="Anulowanie zdarzenia",
        )
        async with self.session_factory() as session:
            first = await persist_dpd_info_events(
                session,
                events=(insert,),
                channel="123456",
            )
            await session.commit()
            duplicate = await persist_dpd_info_events(
                session,
                events=(insert_without_response_id,),
                channel="123456",
            )
            await session.commit()
            cancellation = await persist_dpd_info_events(
                session,
                events=(cancel,),
                channel="123456",
            )
            await session.commit()

            parcel = await session.scalar(select(ShippingTrackingParcel))
            event_count = await session.scalar(select(func.count(ShippingTrackingEvent.id)))

        self.assertEqual(first["inserted"], 1)
        self.assertEqual(duplicate["inserted"], 0)
        self.assertEqual(cancellation["cancelled"], 1)
        self.assertEqual(event_count, 2)
        self.assertEqual(parcel.status_category, "other")
        self.assertIsNone(parcel.latest_business_code)

    async def test_przenumerowanie_i_wspolna_paczka_sa_widoczne_w_rejestrze(self) -> None:
        replacement = "0000999988887B"
        event = self._event(
            code="230403",
            description="Zwrot przesyłki",
            event_data=(DpdInfoEventData(code=None, description=None, value=replacement),),
        )
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            session.add(
                AdminUser(
                    id=1,
                    email="operator@example.com",
                    role="operator",
                    password_hash="test",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            for index in range(2):
                case = ShippingCase(
                    firebird_order_table_id=1001 + index,
                    firebird_order_id=77 + index,
                    firebird_order_year=2026,
                    firebird_client_id=15,
                    order_kind="Umowa",
                    invoice_required=False,
                    status="closed",
                    address_snapshot={"company_name": "Przykładowa Firma", "city": "Poznań"},
                    source_snapshot={},
                    weight_kg=Decimal("2.000"),
                    created_at=now,
                    updated_at=now,
                )
                session.add(case)
                await session.flush()
                session.add(
                    ShippingShipment(
                        shipping_case_id=case.id,
                        idempotency_key=f"wspolna-{index}",
                        provider="dpd",
                        provider_mode="production",
                        provider_shipment_id="sesja-1",
                        tracking_number=event.waybill,
                        status="closed",
                        provider_request={},
                        firebird_status="written",
                        created_at=now,
                        updated_at=now,
                        closed_at=now,
                    )
                )
            await persist_dpd_info_events(session, events=(event,), channel="123456")
            await session.commit()

            listing = await list_shipping_tracking(session)
            search_listing = await list_shipping_tracking(
                session,
                query="przesylka przykladowa",
            )
            detail = await get_shipping_tracking_detail(session, waybill=event.waybill)

        self.assertEqual(listing["total"], 1)
        self.assertTrue(listing["items"][0]["linked"])
        self.assertEqual(len(listing["items"][0]["links"]), 2)
        self.assertEqual(listing["items"][0]["replacement_waybill"], replacement)
        self.assertEqual(search_listing["total"], 1)
        self.assertEqual(detail["parcel"]["category"], "returning")
        self.assertEqual(detail["parcel"]["depot_name"], "Poznań")
        self.assertEqual(len(detail["events"]), 1)

    async def test_cancel_odebrany_przed_zdarzeniem_trafia_do_historii_listu(self) -> None:
        cancel = self._event(
            waybill="",
            operation_type="CANCEL",
            code="",
            description="Anulowanie zdarzenia",
        )
        insert = self._event()
        async with self.session_factory() as session:
            await persist_dpd_info_events(session, events=(cancel,), channel="123456")
            await session.commit()
            await persist_dpd_info_events(session, events=(insert,), channel="123456")
            await session.commit()

            detail = await get_shipping_tracking_detail(session, waybill=insert.waybill)

        self.assertEqual(len(detail["events"]), 2)
        by_operation = {event["operation_type"]: event for event in detail["events"]}
        self.assertTrue(by_operation["INSERT"]["is_cancelled"])
        self.assertFalse(by_operation["CANCEL"]["is_cancelled"])
        self.assertEqual(detail["parcel"]["category"], "other")


class DpdInfoSynchronizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_potwierdzenie_dpd_nastepuje_po_commicie_partii(self) -> None:
        steps: list[str] = []
        event = DpdInfoPersistenceTests._event()
        batches = [DpdInfoBatch(confirm_id="confirm-1", events=(event,)), DpdInfoBatch(None, ())]

        class Client:
            def get_customer_events(self) -> DpdInfoBatch:
                steps.append("fetch")
                return batches.pop(0)

            def mark_events_processed(self, confirm_id: str) -> bool:
                self.confirm_id = confirm_id
                steps.append("ack")
                return True

        class Session:
            async def commit(self) -> None:
                steps.append("commit")

        class SessionContext:
            async def __aenter__(self) -> Session:
                return Session()

            async def __aexit__(self, *_args) -> None:
                return None

        @asynccontextmanager
        async def sync_lock():
            yield True

        async def persist(*_args, **_kwargs) -> dict[str, int]:
            steps.append("persist")
            return {"inserted": 1, "cancelled": 0}

        with (
            patch.object(settings, "dpd_info_channel", "123456"),
            patch.object(settings, "dpd_info_max_batches_per_sync", 2),
            patch.object(dpd_infoservices_sync, "AsyncSessionLocal", return_value=SessionContext()),
            patch.object(dpd_infoservices_sync, "_database_sync_lock", sync_lock),
            patch.object(dpd_infoservices_sync, "_create_sync_run", AsyncMock(return_value=1)),
            patch.object(dpd_infoservices_sync, "_update_sync_run", AsyncMock()),
            patch.object(dpd_infoservices_sync, "persist_dpd_info_events", persist),
        ):
            result = await dpd_infoservices_sync.synchronize_dpd_infoservices(client=Client())

        self.assertEqual(result["status"], "success")
        self.assertEqual(steps, ["fetch", "persist", "commit", "ack", "fetch"])
