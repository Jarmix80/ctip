"""Testy dat startu umów i przypomnień GRENKE."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.models import AdminUser, FormWorkflowCase, GrenkeContractEnd, SmsOut, SmsTemplate
from app.models.base import Base
from app.services.admin_users import EmailDeliverySettings
from app.services.contracts_workflow import (
    WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
    clear_form_workflow_delivery,
    resolve_workflow_grenke_contract_start_date,
    set_form_workflow_business_status,
    set_form_workflow_delivery,
)
from app.services.delivery import send_grenke_contract_end_reminders
from app.services.email_client import EmailSendResult
from app.services.security import hash_password


@compiles(JSONB, "sqlite")  # type: ignore[misc]
def _compile_jsonb_sqlite(_type: JSONB, compiler, **kw):  # noqa: ANN001
    """Pozwala odwzorować kolumny JSONB podczas testów na SQLite."""
    return "TEXT"


class _DummySession:
    def __init__(self) -> None:
        self.flushed = False

    async def flush(self) -> None:
        self.flushed = True


class GrenkeContractDateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _add_sqlite_functions(dbapi_connection, _record):  # pragma: no cover
            dbapi_connection.create_function("timezone", 2, lambda _tz, value: value)

        async with self.engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[
                    AdminUser.__table__,
                    GrenkeContractEnd.__table__,
                    SmsTemplate.__table__,
                    SmsOut.__table__,
                ],
            )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_akceptacja_grenke_ustawia_start_z_pierwszej_historii(self) -> None:
        history_date = date(2026, 1, 15)
        workflow_case = FormWorkflowCase(
            form_request_id=1,
            business_status="WAITING_SIGNATURE",
            status_history=[
                {
                    "status": "APPROVED",
                    "label": "Zgoda na realizację zamówienia",
                    "source": "mailbox",
                    "changed_at": f"{history_date.isoformat()}T08:00:00+00:00",
                }
            ],
        )

        session = _DummySession()
        await set_form_workflow_business_status(
            session,  # type: ignore[arg-type]
            workflow_case=workflow_case,
            business_status=WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
            updated_by=7,
        )

        self.assertTrue(session.flushed)
        self.assertEqual(workflow_case.grenke_contract_start_date, history_date)
        self.assertEqual(resolve_workflow_grenke_contract_start_date(workflow_case), history_date)

    async def test_dowoz_ustawia_i_czysci_start_umowy_kp(self) -> None:
        delivery_date = date(2026, 7, 10)
        workflow_case = FormWorkflowCase(form_request_id=1, business_status="DRAFT")
        session = _DummySession()

        await set_form_workflow_delivery(
            session,  # type: ignore[arg-type]
            workflow_case=workflow_case,
            delivery_date=delivery_date,
            delivery_time_window="10:00-12:00",
            delivery_contact_name="Jan Kowalski",
            delivery_contact_phone="+48600111222",
            delivery_notes="Dostawa od magazynu",
            updated_by=7,
        )

        self.assertEqual(workflow_case.delivery_date, delivery_date)
        self.assertEqual(workflow_case.kp_contract_start_date, delivery_date)

        await clear_form_workflow_delivery(
            session,  # type: ignore[arg-type]
            workflow_case=workflow_case,
            updated_by=7,
        )
        self.assertIsNone(workflow_case.delivery_date)
        self.assertIsNone(workflow_case.kp_contract_start_date)

    async def test_akceptacja_bez_historii_uzywa_przekazanej_daty_zdarzenia(self) -> None:
        approval_at = datetime(2026, 2, 3, 9, 30, tzinfo=UTC)
        workflow_case = FormWorkflowCase(
            form_request_id=1,
            business_status="WAITING_SIGNATURE",
            status_history=[],
        )
        session = _DummySession()

        await set_form_workflow_business_status(
            session,  # type: ignore[arg-type]
            workflow_case=workflow_case,
            business_status=WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
            updated_by=None,
            changed_at=approval_at,
            status_source="mailbox",
        )

        self.assertEqual(workflow_case.grenke_contract_start_date, approval_at.date())
        self.assertEqual(workflow_case.status_changed_at, approval_at)

    async def test_przypomnienia_trafiaja_tylko_do_handlowcow_i_obsluguja_catchup(self) -> None:
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            session.add_all(
                [
                    AdminUser(
                        email="admin@example.com",
                        role="admin",
                        password_hash=hash_password("Admin123!"),
                        is_active=True,
                        is_salesperson=False,
                        mobile_phone="+48500111000",
                        created_at=now,
                        updated_at=now,
                    ),
                    AdminUser(
                        email="handlowiec@example.com",
                        role="operator",
                        password_hash=hash_password("Handel123!"),
                        is_active=True,
                        is_salesperson=True,
                        mobile_phone="+48500111222",
                        created_at=now,
                        updated_at=now,
                    ),
                    GrenkeContractEnd(
                        status="confirmed",
                        customer_name="Klient z umową",
                        grenke_contract_start_date=date.today() - timedelta(days=365),
                        confirmed_end_date=date.today() + timedelta(days=29),
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await session.commit()

        email_settings = EmailDeliverySettings(
            host="smtp.example.com",
            port=587,
            username=None,
            password=None,
            sender_name="CTIP",
            sender_address="ctip@example.com",
            use_tls=True,
            use_ssl=False,
        )
        with (
            patch(
                "app.services.delivery.admin_users.resolve_email_delivery_settings",
                AsyncMock(return_value=email_settings),
            ),
            patch(
                "app.services.delivery.send_smtp_message",
                AsyncMock(return_value=EmailSendResult(True, "OK")),
            ),
        ):
            async with self.session_factory() as session:
                result = await send_grenke_contract_end_reminders(session)
                self.assertEqual(result["eligible"], 1)
                self.assertEqual(result["sms_queued"], 1)
                self.assertEqual(result["emails_sent"], 1)

                sms_rows = (await session.execute(select(SmsOut))).scalars().all()
                self.assertEqual(len(sms_rows), 1)
                self.assertEqual(sms_rows[0].dest, "+48500111222")
                self.assertEqual(sms_rows[0].origin, "grenke_contract_end_reminder")
                self.assertEqual(sms_rows[0].meta["threshold_days"], 30)

                contract_end = (await session.execute(select(GrenkeContractEnd))).scalars().one()
                self.assertEqual(contract_end.notification_history[0]["threshold_days"], 30)

                second_result = await send_grenke_contract_end_reminders(session)
                self.assertEqual(second_result["sms_queued"], 0)


if __name__ == "__main__":
    unittest.main()
