"""Testy kolejności i ponowień kolejki Google Sheets dla urządzeń."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import (
    DeviceInventoryEvent,
    DeviceInventoryUnit,
    DeviceSheetOutbox,
)
from app.models.base import Base
from app.services import device_sheet_worker
from app.services.workflow_sheet_sync import WorkflowSheetRuntimeConfig


def test_outbox_preserves_operation_order_after_transient_failure() -> None:
    """Późniejsza zmiana nie może wyprzedzić nieudanego utworzenia wiersza."""

    async def scenario() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _add_sqlite_functions(dbapi_connection, _record):  # noqa: ANN001
            dbapi_connection.create_function("timezone", 2, lambda _tz, value: value)

        async with engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    DeviceInventoryUnit.__table__,
                    DeviceInventoryEvent.__table__,
                    DeviceSheetOutbox.__table__,
                ],
            )

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            unit = DeviceInventoryUnit(
                source_type="firebird_magazyn_28",
                source_row=19001,
                serial="SN-ORDER-001",
                serial_normalized="SNORDER001",
                ewidencja="KP/ORDER/001",
                ewidencja_normalized="KPORDER001",
                purchase_price_net=Decimal("1200.0000"),
                sheet_sync_status="pending",
                snapshot={"producer": "Ricoh", "model": "MP 401"},
            )
            session.add(unit)
            await session.flush()
            session.add_all(
                [
                    DeviceSheetOutbox(
                        unit_id=unit.id,
                        idempotency_key="order-upsert",
                        operation_type="upsert_device",
                        status="pending",
                        payload={
                            "status": "01. Przed zerówką",
                            "reservation_status": "brak rezerwacji",
                        },
                        next_attempt_at=datetime.now(UTC),
                    ),
                    DeviceSheetOutbox(
                        unit_id=unit.id,
                        idempotency_key="order-note",
                        operation_type="update_note",
                        status="pending",
                        payload={"notes": "Uwaga po przyjęciu"},
                        next_attempt_at=datetime.now(UTC),
                    ),
                ]
            )
            await session.commit()

        config = WorkflowSheetRuntimeConfig(
            enabled=True,
            credentials_path="/srv/google/test.json",
            spreadsheet_id="spreadsheet-test",
            workflow_devices_worksheet="Urzadzenia_magazyn",
            source="test",
        )
        with (
            patch.object(device_sheet_worker, "AsyncSessionLocal", session_factory),
            patch.object(
                device_sheet_worker,
                "load_workflow_sheet_runtime_config",
                new=AsyncMock(return_value=config),
            ),
            patch.object(
                device_sheet_worker,
                "workflow_sheet_sync_configured",
                return_value=(True, None),
            ),
            patch.object(
                device_sheet_worker,
                "sync_device_inventory_to_sheet",
                side_effect=[
                    RuntimeError("Przejściowy błąd arkusza"),
                    {
                        "enabled": True,
                        "sheet_row": 52,
                        "action": "updated",
                    },
                ],
            ) as sync_mock,
        ):
            first_result = await device_sheet_worker.process_device_sheet_outbox_once(limit=10)
            assert first_result == {
                "processed": 1,
                "completed": 0,
                "failed": 1,
                "reason": None,
            }
            assert sync_mock.call_count == 1

            async with session_factory() as session:
                queue = (
                    (
                        await session.execute(
                            select(DeviceSheetOutbox).order_by(DeviceSheetOutbox.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert queue[0].status == "pending"
                assert queue[0].attempt_count == 1
                assert queue[1].status == "pending"
                assert queue[1].attempt_count == 0
                queue[0].status = "completed"
                queue[0].completed_at = datetime.now(UTC)
                await session.commit()

            second_result = await device_sheet_worker.process_device_sheet_outbox_once(limit=10)
            assert second_result == {
                "processed": 1,
                "completed": 1,
                "failed": 0,
                "reason": None,
            }
            assert sync_mock.call_count == 2
            assert sync_mock.call_args.kwargs["operation_type"] == "update_note"

        async with session_factory() as session:
            queue = (
                (await session.execute(select(DeviceSheetOutbox).order_by(DeviceSheetOutbox.id)))
                .scalars()
                .all()
            )
            unit = await session.get(DeviceInventoryUnit, queue[1].unit_id)
            events = (
                (
                    await session.execute(
                        select(DeviceInventoryEvent).order_by(DeviceInventoryEvent.id)
                    )
                )
                .scalars()
                .all()
            )
            assert queue[1].status == "completed"
            assert queue[1].attempt_count == 1
            assert unit is not None
            assert unit.sheet_row == 52
            assert unit.sheet_sync_status == "synced"
            assert [event.event_type for event in events] == [
                "sheet_sync_failed",
                "sheet_sync_completed",
            ]

        await engine.dispose()

    asyncio.run(scenario())
