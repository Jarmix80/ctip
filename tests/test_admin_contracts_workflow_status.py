# ruff: noqa: E402
"""Testy statusow workflow obslugi umow."""

import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.api import deps
from app.core.config import settings
from app.main import create_app
from app.models import (
    AdminAuditLog,
    AdminSession,
    AdminSetting,
    AdminUser,
    FormRequest,
    FormWorkflowCase,
    FormWorkflowDevice,
    WorkflowSheetStatusCache,
)
from app.models.base import Base
from app.services.contracts_dashboard import FirebirdClientMatch
from app.services.security import hash_password


@compiles(JSONB, "sqlite")  # type: ignore[misc]
def _compile_jsonb_sqlite(_type: JSONB, compiler, **kw):  # noqa: ANN001
    """Pozwala odwzorowac kolumny JSONB podczas testow na SQLite."""
    return "TEXT"


class AdminContractsWorkflowStatusTests(unittest.IsolatedAsyncioTestCase):
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
                    AdminSession.__table__,
                    AdminSetting.__table__,
                    AdminAuditLog.__table__,
                    FormRequest.__table__,
                    FormWorkflowCase.__table__,
                    FormWorkflowDevice.__table__,
                    WorkflowSheetStatusCache.__table__,
                ],
            )

        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

        async def override_get_db_session():
            async with self.session_factory() as session:
                yield session

        self.app = create_app()
        self.app.dependency_overrides[deps.get_db_session] = override_get_db_session
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        self._previous_admin_secret_key = settings.admin_secret_key
        self._previous_backup_scheduler_enabled = settings.backup_scheduler_enabled
        self._previous_workflow_sheet_status_cache_scheduler_enabled = (
            settings.workflow_sheet_status_cache_scheduler_enabled
        )
        self._previous_contracts_workflow_maintenance_scheduler_enabled = (
            settings.contracts_workflow_maintenance_scheduler_enabled
        )
        self._previous_contracts_mailbox_scheduler_enabled = (
            settings.contracts_mailbox_scheduler_enabled
        )
        self._previous_delivery_notifications_scheduler_enabled = (
            settings.delivery_notifications_scheduler_enabled
        )
        self._previous_block_client_communications = settings.block_client_communications
        settings.admin_secret_key = Fernet.generate_key().decode("ascii")
        settings.backup_scheduler_enabled = False
        settings.workflow_sheet_status_cache_scheduler_enabled = False
        settings.contracts_workflow_maintenance_scheduler_enabled = False
        settings.contracts_mailbox_scheduler_enabled = False
        settings.delivery_notifications_scheduler_enabled = False
        settings.block_client_communications = False

        async with self.session_factory() as session:
            now = datetime.now(UTC)
            admin = AdminUser(
                email="admin@example.com",
                first_name="Jan",
                last_name="Kowalski",
                role="admin",
                password_hash=hash_password("Sekret123!"),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            operator = AdminUser(
                email="operator@example.com",
                first_name="Anna",
                last_name="Nowak",
                role="operator",
                password_hash=hash_password("Operator123!"),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add_all([admin, operator])
            await session.commit()

        self._email_patch = patch(
            "app.api.routes.admin_users.admin_users.send_credentials_email",
            AsyncMock(),
        )
        self._email_patch.start()

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.app.dependency_overrides.clear()
        self._email_patch.stop()
        settings.admin_secret_key = self._previous_admin_secret_key
        settings.backup_scheduler_enabled = self._previous_backup_scheduler_enabled
        settings.workflow_sheet_status_cache_scheduler_enabled = (
            self._previous_workflow_sheet_status_cache_scheduler_enabled
        )
        settings.contracts_workflow_maintenance_scheduler_enabled = (
            self._previous_contracts_workflow_maintenance_scheduler_enabled
        )
        settings.contracts_mailbox_scheduler_enabled = (
            self._previous_contracts_mailbox_scheduler_enabled
        )
        settings.delivery_notifications_scheduler_enabled = (
            self._previous_delivery_notifications_scheduler_enabled
        )
        settings.block_client_communications = self._previous_block_client_communications
        await self.engine.dispose()

    async def _login_operator(self) -> tuple[str, dict]:
        response = await self.client.post(
            "/operator/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        token = data["token"]
        self.assertTrue(token)
        return token, data

    async def _create_submitted_form_request(
        self,
        *,
        customer_name: str = "Klient Testowy",
        customer_email: str = "klient@test.local",
        customer_phone: str = "+48600111222",
    ) -> FormRequest:
        now = datetime.now(UTC)
        submitted_payload = {
            "company_name": customer_name,
            "company_nip": "9000001234",
            "company_phone": customer_phone,
            "company_email": customer_email,
            "billing_email": "faktury@test.local",
            "registered_street": "Testowa",
            "registered_building_no": "1",
            "registered_apartment_no": "2",
            "registered_postal_code": "00-001",
            "registered_city": "Warszawa",
            "correspondence_same_as_registered": True,
            "correspondence_street": "Testowa",
            "correspondence_building_no": "1",
            "correspondence_apartment_no": "2",
            "correspondence_postal_code": "00-001",
            "correspondence_city": "Warszawa",
            "representatives": [],
            "consent": True,
        }
        envelope = {
            "payload": submitted_payload,
            "meta": {
                "submitted_at": now.isoformat(),
                "client_ip": "127.0.0.1",
                "user_agent": "pytest",
            },
        }
        cipher = Fernet(settings.admin_secret_key.encode("utf-8"))
        encoded = cipher.encrypt(json.dumps(envelope).encode("utf-8")).decode("utf-8")

        async with self.session_factory() as session:
            item = FormRequest(
                created_at=now,
                updated_at=now,
                created_by=1,
                customer_name=customer_name,
                customer_email=customer_email,
                customer_phone=customer_phone,
                status="SUBMITTED",
                token_hash=f"submitted-{now.timestamp()}-{customer_email}",
                token_expires_at=now + timedelta(days=7),
                submitted_payload=encoded,
                submitted_at=now,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

    async def test_contracts_form_workflow_status_closed_not_realized_releases_resources(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="WAITING_SIGNATURE",
                client_mode="basic_proforma",
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=57,
                    producer="Ricoh",
                    model="IM 350",
                    ewidencja="KP/57",
                    snapshot={
                        "row": 57,
                        "index": "KP/57",
                        "sheet_row": 157,
                        "sheet_sync_status": "synced",
                    },
                )
            )
            await session.commit()

        before = datetime.now(UTC)
        with patch(
            "app.api.routes.admin_contracts.release_workflow_devices_from_sheet",
            return_value={
                "enabled": True,
                "reason": None,
                "worksheet_title": "Urzadzenia magazyn",
                "released_count": 1,
                "rows": [{"source_row": 57, "sheet_row": 157, "action": "released"}],
                "added_headers": [],
            },
        ) as release_mock:
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/status",
                headers={"X-Admin-Session": token},
                json={"business_status": "CLOSED_NOT_REALIZED"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["workflow"]["business_status"], "CLOSED_NOT_REALIZED")
        self.assertEqual(body["resources_release"]["sheet_release"]["released_count"], 1)
        release_mock.assert_called_once()

        async with self.session_factory() as session:
            workflow_case = (
                (
                    await session.execute(
                        select(FormWorkflowCase).where(FormWorkflowCase.form_request_id == form.id)
                    )
                )
                .scalars()
                .one()
            )
            workflow_device = (
                (
                    await session.execute(
                        select(FormWorkflowDevice).where(
                            FormWorkflowDevice.workflow_case_id == workflow_case.id
                        )
                    )
                )
                .scalars()
                .one()
            )
            form_row = await session.get(FormRequest, form.id)
            self.assertEqual(workflow_case.business_status, "CLOSED_NOT_REALIZED")
            self.assertIsNotNone(workflow_case.resources_released_at)
            self.assertIsNone(workflow_case.resources_release_due_at)
            self.assertEqual((workflow_device.snapshot or {}).get("sheet_sync_status"), "released")
            assert form_row is not None
            self.assertIsNotNone(form_row.archive_due_at)
            archive_due_at = (
                form_row.archive_due_at
                if form_row.archive_due_at.tzinfo is not None
                else form_row.archive_due_at.replace(tzinfo=UTC)
            )
            self.assertGreaterEqual(
                archive_due_at, before + timedelta(days=14) - timedelta(minutes=1)
            )
            self.assertLessEqual(archive_due_at, datetime.now(UTC) + timedelta(days=14, minutes=1))

    async def test_contracts_dashboard_scope_includes_closed_other_bucket(self):
        token, _ = await self._login_operator()
        submitted = await self._create_submitted_form_request(
            customer_name="Klient Zamkniety bez realizacji",
            customer_email="zamkniete@example.local",
            customer_phone="+48600600601",
        )

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=submitted.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="CLOSED_NOT_REALIZED",
                client_mode="basic_proforma",
            )
            session.add(case)
            submitted_form = await session.get(FormRequest, submitted.id)
            assert submitted_form is not None
            submitted_form.archive_due_at = datetime.now(UTC) + timedelta(days=14)
            submitted_form.archive_bucket = "closed_other"
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[],
            ),
            patch(
                "app.api.routes.admin_contracts.find_client_in_firebird",
                return_value=FirebirdClientMatch(found=False),
            ),
        ):
            response = await self.client.get(
                "/admin/contracts/dashboard?forms_scope=all&include_devices=0&archive_scope=closed_other",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["forms_scope"], "all")
        self.assertEqual(body["archive_scope"], "closed_other")
        self.assertEqual(body["archive_totals"].get("closed_other"), 1)
        form_ids = {item["id"] for item in body["forms"]}
        self.assertIn(submitted.id, form_ids)
