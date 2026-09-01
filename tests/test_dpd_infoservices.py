"""Testy klienta SOAP i lokalnego rejestru statusów DPD InfoServices."""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import (
    AdminUser,
    Base,
    ShippingCase,
    ShippingEvent,
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
from app.services.dpd_infoservices_sync import (
    classify_dpd_event,
    is_dpd_pickup_confirmation,
    persist_dpd_info_events,
)
from app.services.dpd_tracking_dedupe import (
    apply_dpd_tracking_dedupe,
    preview_dpd_tracking_dedupe,
    rollback_dpd_tracking_dedupe,
)
from app.services.shipping_milestones import (
    PILOT_APPLIED_EVENT,
    PILOT_ROLLED_BACK_EVENT,
    ShippingMilestonePilotValidationError,
    apply_archived_shipping_milestone_pilot,
    preview_archived_shipping_milestone_pilot,
    reconcile_shipping_milestones,
    rollback_archived_shipping_milestone_pilot,
)
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

    def test_gotowa_do_nadania_nie_jest_potwierdzeniem_odbioru(self) -> None:
        self.assertFalse(is_dpd_pickup_confirmation("030103", "Gotowa do nadania"))
        self.assertTrue(is_dpd_pickup_confirmation("040101", "Przesyłka odebrana przez Kuriera"))


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
                    ShippingEvent.__table__,
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

    async def _add_archived_pilot_target(
        self,
        session: AsyncSession,
        *,
        order_id: int = 18517,
        order_table_id: int = 83517,
        waybill: str = "1050059395731U",
    ) -> ShippingShipment:
        now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        case = ShippingCase(
            firebird_order_table_id=order_table_id,
            firebird_order_id=order_id,
            firebird_order_year=2026,
            firebird_client_id=15,
            order_kind="Umowa",
            invoice_required=False,
            status="closed",
            address_snapshot={},
            source_snapshot={},
            weight_kg=Decimal("2.000"),
            created_at=now,
            updated_at=now,
        )
        session.add(case)
        await session.flush()
        shipment = ShippingShipment(
            shipping_case_id=case.id,
            idempotency_key=f"pilot-{order_id}",
            provider="dpd",
            provider_mode="production",
            provider_shipment_id=f"sesja-{order_id}",
            tracking_number=waybill,
            status="closed",
            provider_request={},
            firebird_status="written",
            firebird_label_metadata_synced_at=None,
            created_at=now,
            updated_at=now,
            closed_at=now,
        )
        session.add(shipment)
        await session.flush()
        return shipment

    async def _add_pilot_events(
        self,
        session: AsyncSession,
        *,
        waybill: str = "1050059395731U",
    ) -> None:
        now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        session.add_all(
            [
                ShippingTrackingEvent(
                    source_event_key=f"pickup-{waybill}",
                    waybill=waybill,
                    business_code="040101",
                    operation_type="INSERT",
                    description="Przesyłka odebrana przez Kuriera",
                    event_time=datetime(2026, 8, 31, 12, 42, tzinfo=UTC),
                    event_data=[],
                    raw_payload={},
                    received_at=now,
                ),
                ShippingTrackingEvent(
                    source_event_key=f"delivery-{waybill}",
                    waybill=waybill,
                    business_code="190101",
                    operation_type="INSERT",
                    description="Przesyłka doręczona",
                    event_time=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
                    event_data=[],
                    raw_payload={},
                    received_at=now,
                ),
            ]
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

    async def test_rozne_identyfikatory_soap_tworzace_jeden_status_sa_grupowane(self) -> None:
        first_event = self._event(object_id="obiekt-kanal")
        alias_event = replace(
            first_event,
            event_id="id-historia",
            object_id="obiekt-historia",
        )
        alias_cancel = replace(
            alias_event,
            event_id="cancel-historia",
            waybill="",
            business_code="",
            description="Anulowanie zdarzenia",
            operation_type="CANCEL",
        )
        async with self.session_factory() as session:
            first = await persist_dpd_info_events(
                session,
                events=(first_event,),
                channel="kanal-klienta",
            )
            await session.commit()
            alias = await persist_dpd_info_events(
                session,
                events=(alias_event,),
                channel="historia-listu",
            )
            await session.commit()

            rows = list(
                (
                    await session.execute(
                        select(ShippingTrackingEvent)
                        .where(ShippingTrackingEvent.operation_type == "INSERT")
                        .order_by(ShippingTrackingEvent.id)
                    )
                )
                .scalars()
                .all()
            )
            detail_before = await get_shipping_tracking_detail(
                session,
                waybill=first_event.waybill,
            )
            cancellation = await persist_dpd_info_events(
                session,
                events=(alias_cancel,),
                channel="historia-listu",
            )
            await session.commit()
            detail_after = await get_shipping_tracking_detail(
                session,
                waybill=first_event.waybill,
            )

        self.assertEqual(first["duplicates"], 0)
        self.assertEqual(alias["duplicates"], 1)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0].canonical_event_id)
        self.assertEqual(rows[1].canonical_event_id, rows[0].id)
        self.assertEqual(rows[0].semantic_event_key, rows[1].semantic_event_key)
        self.assertEqual(len(detail_before["events"]), 1)
        self.assertEqual(cancellation["cancelled"], 1)
        self.assertEqual(len(detail_after["events"]), 2)
        self.assertTrue(all(row.is_cancelled for row in rows))
        self.assertTrue(
            next(item for item in detail_after["events"] if item["operation_type"] == "INSERT")[
                "is_cancelled"
            ]
        )

    async def test_historyczna_kanonizacja_ma_dry_run_apply_i_rollback(self) -> None:
        first_event = self._event(object_id="historyczny-1")
        alias_event = replace(
            first_event,
            event_id="historyczny-alias",
            object_id="historyczny-2",
        )
        async with self.session_factory() as session:
            await persist_dpd_info_events(
                session,
                events=(first_event,),
                channel="kanal-klienta",
            )
            await persist_dpd_info_events(
                session,
                events=(alias_event,),
                channel="historia-listu",
            )
            await session.flush()
            rows = list(
                (
                    await session.execute(
                        select(ShippingTrackingEvent).where(
                            ShippingTrackingEvent.operation_type == "INSERT"
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.semantic_event_key = None
                row.canonical_event_id = None
            await session.commit()

            preview = await preview_dpd_tracking_dedupe(session)
            applied = await apply_dpd_tracking_dedupe(
                session,
                expected_state_token=preview["state_token"],
            )
            await session.commit()
            canonicalized = list(
                (
                    await session.execute(
                        select(ShippingTrackingEvent)
                        .where(ShippingTrackingEvent.operation_type == "INSERT")
                        .order_by(ShippingTrackingEvent.id)
                    )
                )
                .scalars()
                .all()
            )
            canonical_links = [row.canonical_event_id for row in canonicalized]
            rolled_back = await rollback_dpd_tracking_dedupe(
                session,
                run_id=applied["run_id"],
                rollback_state=applied["rollback_state"],
            )
            await session.commit()
            restored = list(
                (
                    await session.execute(
                        select(ShippingTrackingEvent)
                        .where(ShippingTrackingEvent.operation_type == "INSERT")
                        .order_by(ShippingTrackingEvent.id)
                    )
                )
                .scalars()
                .all()
            )

        self.assertEqual(preview["technical_insert_count"], 2)
        self.assertEqual(preview["logical_event_count"], 1)
        self.assertEqual(preview["duplicate_count"], 1)
        self.assertEqual(applied["change_count"], 2)
        self.assertIsNone(canonical_links[0])
        self.assertEqual(canonical_links[1], canonicalized[0].id)
        self.assertEqual(rolled_back["restored_count"], 2)
        self.assertTrue(all(row.semantic_event_key is None for row in restored))
        self.assertTrue(all(row.canonical_event_id is None for row in restored))

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

    async def test_kamienie_milowe_sa_zapisywane_raz_i_anulowanie_wymaga_uzgodnienia(
        self,
    ) -> None:
        now = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
        waybill = "0000111122223A"
        async with self.session_factory() as session:
            case = ShippingCase(
                firebird_order_table_id=83416,
                firebird_order_id=18416,
                firebird_order_year=2026,
                firebird_client_id=15,
                order_kind="Umowa",
                invoice_required=False,
                status="closed",
                address_snapshot={},
                source_snapshot={},
                weight_kg=Decimal("2.000"),
                created_at=now,
                updated_at=now,
            )
            session.add(case)
            await session.flush()
            shipment = ShippingShipment(
                shipping_case_id=case.id,
                idempotency_key="kamienie-milowe-18416",
                provider="dpd",
                provider_mode="production",
                provider_shipment_id="sesja-1",
                tracking_number=waybill,
                status="closed",
                provider_request={},
                firebird_status="written",
                firebird_label_metadata_synced_at=now,
                created_at=now,
                updated_at=now,
                closed_at=now,
            )
            pickup = ShippingTrackingEvent(
                source_event_key="pickup-1",
                waybill=waybill,
                business_code="040101",
                operation_type="INSERT",
                description="Przesyłka odebrana przez Kuriera",
                event_time=datetime(2026, 9, 2, 9, 15, tzinfo=UTC),
                event_data=[],
                raw_payload={},
                received_at=now,
            )
            delivery = ShippingTrackingEvent(
                source_event_key="delivery-1",
                waybill=waybill,
                business_code="190101",
                operation_type="INSERT",
                description="Przesyłka doręczona odbiorcy",
                event_time=datetime(2026, 9, 3, 12, 30, tzinfo=UTC),
                event_data=[],
                raw_payload={},
                received_at=now,
            )
            session.add_all([shipment, pickup, delivery])
            await session.commit()

            writer = MagicMock(
                return_value={
                    "status": "written",
                    "changed_fields": [
                        "DATA_PRZES",
                        "WYKONANIE",
                        "DATA_PRZES_WE",
                        "PRZESYLKA_WE",
                    ],
                }
            )
            with (
                patch.object(settings, "shipping_dpd_firebird_milestones_enabled", True),
                patch(
                    "app.services.shipping_milestones.write_shipping_milestones_to_order",
                    writer,
                ),
            ):
                first = await reconcile_shipping_milestones(session)
                await session.commit()
                second = await reconcile_shipping_milestones(session)
                await session.commit()

            self.assertEqual(first["written"], 1)
            self.assertEqual(second["written"], 0)
            writer.assert_called_once()
            arguments = writer.call_args.kwargs
            self.assertEqual(arguments["order_table_id"], 83416)
            self.assertEqual(arguments["pickup_date"].isoformat(), "2026-09-02")
            self.assertEqual(arguments["delivery_date"].isoformat(), "2026-09-03")
            self.assertIn("Doręczona", arguments["description_text"])
            self.assertEqual(shipment.firebird_pickup_event_key, "pickup-1")
            self.assertEqual(shipment.firebird_delivery_event_key, "delivery-1")
            self.assertEqual(shipment.firebird_description_event_key, "delivery-1")

            pickup.is_cancelled = True
            await session.commit()
            with (
                patch.object(settings, "shipping_dpd_firebird_milestones_enabled", True),
                patch(
                    "app.services.shipping_milestones.write_shipping_milestones_to_order",
                    MagicMock(),
                ) as cancelled_writer,
            ):
                conflict = await reconcile_shipping_milestones(session)
                await session.commit()

            self.assertEqual(conflict["conflicts"], 1)
            self.assertIn("anulowane", shipment.firebird_milestone_error)
            cancelled_writer.assert_not_called()

    async def test_pilot_archiwalny_obejmuje_tylko_wskazane_zlecenie_wspolnego_listu(
        self,
    ) -> None:
        waybill = "1050059395731U"
        firebird_preview = {
            "state_token": "firebird-token",
            "order": {
                "order_table_id": 83517,
                "order_id": 18517,
                "order_year": 2026,
                "status": "Z",
                "tracking_number": waybill,
            },
            "changed_fields": ["DATA_PRZES_WE", "PRZESYLKA_WE"],
            "before": {
                "DATA_PRZES": "2026-08-31",
                "WYKONANIE": f"Wysłana paczka 31.08.2026 {waybill}",
                "DATA_PRZES_WE": None,
                "PRZESYLKA_WE": None,
            },
            "after": {
                "DATA_PRZES": "2026-08-31",
                "WYKONANIE": f"Wysłana paczka 31.08.2026 {waybill}",
                "DATA_PRZES_WE": "2026-09-01",
                "PRZESYLKA_WE": "01.09.2026 10:01 — Doręczona: Przesyłka doręczona",
            },
        }
        async with self.session_factory() as session:
            target = await self._add_archived_pilot_target(session, waybill=waybill)
            other = await self._add_archived_pilot_target(
                session,
                order_id=18518,
                order_table_id=83518,
                waybill=waybill,
            )
            await self._add_pilot_events(session, waybill=waybill)
            await session.commit()
            writer = MagicMock(
                return_value={
                    "status": "written",
                    "changed_fields": ["DATA_PRZES_WE", "PRZESYLKA_WE"],
                    "state_token_before": "firebird-token",
                }
            )
            restorer = MagicMock(
                return_value={
                    "status": "restored",
                    "changed_fields": ["DATA_PRZES_WE", "PRZESYLKA_WE"],
                }
            )
            with (
                patch.object(settings, "shipping_dpd_firebird_milestones_enabled", False),
                patch(
                    "app.services.shipping_milestones.preview_shipping_milestones_to_order",
                    return_value=firebird_preview,
                ) as previewer,
                patch(
                    "app.services.shipping_milestones.write_shipping_milestones_to_order",
                    writer,
                ),
                patch(
                    "app.services.shipping_milestones.restore_shipping_milestones_to_order",
                    restorer,
                ),
            ):
                preview = await preview_archived_shipping_milestone_pilot(
                    session,
                    order_id=18517,
                    order_year=2026,
                    waybill=waybill,
                )
                writer.assert_not_called()
                applied = await apply_archived_shipping_milestone_pilot(
                    session,
                    order_id=18517,
                    order_year=2026,
                    waybill=waybill,
                    expected_state_token=preview["state_token"],
                )
                await session.commit()
                await session.refresh(target)
                await session.refresh(other)
                applied_events = list(
                    (
                        await session.execute(
                            select(ShippingEvent).where(
                                ShippingEvent.event_type == PILOT_APPLIED_EVENT
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                self.assertEqual(target.firebird_pickup_event_key, f"pickup-{waybill}")
                self.assertEqual(target.firebird_delivery_event_key, f"delivery-{waybill}")
                self.assertIsNone(other.firebird_pickup_event_key)
                self.assertIsNone(other.firebird_delivery_event_key)
                self.assertEqual(len(applied_events), 1)
                self.assertEqual(applied_events[0].payload["pilot_run_id"], applied["pilot_run_id"])
                previewer.assert_called()
                writer.assert_called_once()
                self.assertEqual(writer.call_args.kwargs["expected_state_token"], "firebird-token")

                rolled_back = await rollback_archived_shipping_milestone_pilot(
                    session,
                    order_id=18517,
                    order_year=2026,
                    waybill=waybill,
                    pilot_run_id=applied["pilot_run_id"],
                )
                await session.commit()
                await session.refresh(target)

            self.assertEqual(rolled_back["status"], "restored")
            self.assertIsNone(target.firebird_pickup_event_key)
            self.assertIsNone(target.firebird_delivery_event_key)
            restorer.assert_called_once()
            rollback_count = await session.scalar(
                select(func.count(ShippingEvent.id)).where(
                    ShippingEvent.event_type == PILOT_ROLLED_BACK_EVENT
                )
            )
            self.assertEqual(rollback_count, 1)

    async def test_pilot_archiwalny_blokuje_nieaktualny_token(self) -> None:
        waybill = "1050059395731U"
        firebird_preview = {
            "state_token": "firebird-token",
            "order": {
                "order_table_id": 83517,
                "order_id": 18517,
                "order_year": 2026,
                "status": "Z",
                "tracking_number": waybill,
            },
            "changed_fields": ["DATA_PRZES_WE"],
            "before": {
                "DATA_PRZES": "2026-08-31",
                "WYKONANIE": None,
                "DATA_PRZES_WE": None,
                "PRZESYLKA_WE": None,
            },
            "after": {
                "DATA_PRZES": "2026-08-31",
                "WYKONANIE": None,
                "DATA_PRZES_WE": "2026-09-01",
                "PRZESYLKA_WE": None,
            },
        }
        async with self.session_factory() as session:
            await self._add_archived_pilot_target(session, waybill=waybill)
            await self._add_pilot_events(session, waybill=waybill)
            await session.commit()
            writer = MagicMock()
            with (
                patch.object(settings, "shipping_dpd_firebird_milestones_enabled", False),
                patch(
                    "app.services.shipping_milestones.preview_shipping_milestones_to_order",
                    return_value=firebird_preview,
                ),
                patch(
                    "app.services.shipping_milestones.write_shipping_milestones_to_order",
                    writer,
                ),
                self.assertRaisesRegex(
                    ShippingMilestonePilotValidationError,
                    "Ponów podgląd",
                ),
            ):
                await apply_archived_shipping_milestone_pilot(
                    session,
                    order_id=18517,
                    order_year=2026,
                    waybill=waybill,
                    expected_state_token="nieaktualny-token",
                )

            writer.assert_not_called()
            event_count = await session.scalar(select(func.count(ShippingEvent.id)))
            self.assertEqual(event_count, 0)

    async def test_pilot_archiwalny_wymaga_wylaczonej_globalnej_synchronizacji(
        self,
    ) -> None:
        async with self.session_factory() as session:
            await self._add_archived_pilot_target(session)
            await self._add_pilot_events(session)
            await session.commit()
            with (
                patch.object(settings, "shipping_dpd_firebird_milestones_enabled", True),
                self.assertRaisesRegex(
                    ShippingMilestonePilotValidationError,
                    "Globalna synchronizacja",
                ),
            ):
                await preview_archived_shipping_milestone_pilot(
                    session,
                    order_id=18517,
                    order_year=2026,
                    waybill="1050059395731U",
                )


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
            return {"inserted": 1, "duplicates": 0, "cancelled": 0}

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
