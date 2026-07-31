"""Testy routingu i symulacji powiadomień o sprawach CRM."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import AdminAuditLog, AdminUser, CrmCase, CrmCaseEvent
from app.models.base import Base
from app.schemas.crm import CrmCaseActionRequest, CrmCaseCreateRequest
from app.services.crm_cases import apply_case_action, create_case
from app.services.crm_notifications import (
    crm_case_url,
    dispatch_new_case_notifications,
)


class CrmNotificationTests(unittest.IsolatedAsyncioTestCase):
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
                    AdminAuditLog.__table__,
                    CrmCase.__table__,
                    CrmCaseEvent.__table__,
                ],
            )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.previous_admin_panel_url = settings.admin_panel_url
        self.previous_crm_lab_mode = settings.crm_lab_mode
        self.previous_database = settings.pg_database
        settings.admin_panel_url = "http://127.0.0.1:8000/admin"
        settings.crm_lab_mode = True
        settings.pg_database = "ctip_test"

    async def asyncTearDown(self) -> None:
        settings.admin_panel_url = self.previous_admin_panel_url
        settings.crm_lab_mode = self.previous_crm_lab_mode
        settings.pg_database = self.previous_database
        await self.engine.dispose()

    @staticmethod
    def _user(
        *,
        user_id: int,
        first_name: str,
        mobile_phone: str | None = None,
        sales_sms: bool = False,
        sales_email: bool = False,
        operations_sms: bool = False,
        operations_email: bool = False,
        active: bool = True,
    ) -> AdminUser:
        now = datetime.now(UTC)
        return AdminUser(
            id=user_id,
            first_name=first_name,
            last_name="Testowy",
            email=f"{first_name.lower()}@example.test",
            role="operator",
            password_hash="test",
            is_active=active,
            mobile_phone=mobile_phone,
            crm_sales_sms_enabled=sales_sms,
            crm_sales_email_enabled=sales_email,
            crm_operations_sms_enabled=operations_sms,
            crm_operations_email_enabled=operations_email,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _payload(
        *,
        external_ref: str,
        queue: str,
        category: str,
    ) -> CrmCaseCreateRequest:
        return CrmCaseCreateRequest(
            external_ref=external_ref,
            channel="form",
            queue=queue,
            category=category,
            priority="normal",
            subject="Testowa sprawa formularzowa",
            message="Treść testowej sprawy.",
            company_name="Firma Testowa",
            contact_name="Jan Testowy",
            phone="600100200",
            email="jan@example.com",
            is_lab=True,
        )

    async def test_sales_and_operations_use_independent_user_flags(self) -> None:
        async with self.sessions() as session:
            session.add_all(
                [
                    self._user(user_id=1, first_name="Anna", sales_email=True),
                    self._user(
                        user_id=2,
                        first_name="Piotr",
                        mobile_phone="600000002",
                        operations_sms=True,
                    ),
                    self._user(
                        user_id=3,
                        first_name="Nieaktywny",
                        sales_sms=True,
                        operations_email=True,
                        active=False,
                    ),
                ]
            )
            await session.flush()

            sales_case, _ = await create_case(
                session,
                self._payload(
                    external_ref="www-sales-1",
                    queue="sales",
                    category="product",
                ),
                idempotency_key="sales-request-1",
                service_channel="www",
                declared_operator=None,
                force_lab=True,
            )
            sales_result = await dispatch_new_case_notifications(session, sales_case)

            operations_case, _ = await create_case(
                session,
                self._payload(
                    external_ref="www-service-1",
                    queue="service_it",
                    category="service",
                ),
                idempotency_key="service-request-1",
                service_channel="www",
                declared_operator=None,
                force_lab=True,
            )
            operations_result = await dispatch_new_case_notifications(session, operations_case)

            self.assertEqual(sales_result.recipients, 1)
            self.assertEqual(sales_result.email_simulated, 1)
            self.assertEqual(sales_result.sms_simulated, 0)
            self.assertEqual(operations_result.recipients, 1)
            self.assertEqual(operations_result.sms_simulated, 1)
            self.assertEqual(operations_result.email_simulated, 0)
            self.assertEqual(
                sales_case.events[-1].payload["recipients"][0]["name"],
                "Anna Testowy",
            )
            self.assertEqual(
                operations_case.events[-1].payload["recipients"][0]["name"],
                "Piotr Testowy",
            )

    async def test_meter_case_uses_contracts_queue_and_meter_action(self) -> None:
        async with self.sessions() as session:
            session.add(self._user(user_id=10, first_name="Operator"))
            await session.flush()
            meter_case, _ = await create_case(
                session,
                self._payload(
                    external_ref="www-meter-1",
                    queue="contracts",
                    category="meters",
                ),
                idempotency_key="meter-request-1",
                service_channel="www",
                declared_operator=None,
                force_lab=True,
            )

            await apply_case_action(
                session,
                meter_case,
                CrmCaseActionRequest(
                    action="meter_update",
                    declared_operator_id=10,
                    meters={"bw": 100, "color": None, "scan": None},
                ),
            )

            self.assertEqual(meter_case.queue, "contracts")
            self.assertEqual(meter_case.category, "meters")
            self.assertEqual(meter_case.status, "done")
            self.assertEqual(meter_case.events[-1].payload["firebird_write"], False)

    def test_case_link_points_directly_to_crm_case(self) -> None:
        self.assertEqual(
            crm_case_url("KP-LAB-123"),
            "http://127.0.0.1:8000/crm?case=KP-LAB-123",
        )


if __name__ == "__main__":
    unittest.main()
