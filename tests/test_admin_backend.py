# ruff: noqa: E402

import json
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import quote
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from cryptography.fernet import Fernet
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.routes import admin_ctip, admin_status
from app.api.routes.admin_config import settings_store
from app.core.config import settings
from app.main import create_app
from app.models import (
    AdminAuditLog,
    AdminSession,
    AdminSetting,
    AdminUser,
    Call,
    CallEvent,
    Contact,
    ContactDevice,
    ContractsMailboxHistoryCase,
    ContractsMailboxMessage,
    DeviceAuditItem,
    DeviceAuditRun,
    DeviceIntakeOperation,
    DeviceInventoryEvent,
    DeviceInventoryUnit,
    DeviceManualReservation,
    DeviceSheetOutbox,
    FormRequest,
    FormWorkflowCase,
    FormWorkflowDevice,
    IvrMap,
    SmsOut,
    SmsTemplate,
    WorkflowSheetStatusCache,
)
from app.models.base import Base
from app.services import admin_ivr_map, section_permissions
from app.services.admin_users import EmailDeliverySettings
from app.services.backup_retention import RetentionApplyResult, RetentionPlan
from app.services.backup_runner import BackupFileInfo, BackupRunResult
from app.services.contracts_dashboard import (
    FirebirdClientMatch,
    FirebirdClientWriteResult,
    FirebirdRuntimeConfig,
    find_client_in_firebird,
    firebird_writes_enabled,
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)
from app.services.contracts_proforma import FirebirdProformaWriteResult
from app.services.device_intake import (
    DeviceIntakeBatchResult,
    DeviceIntakeResult,
)
from app.services.email_client import EmailSendResult, EmailTestResult
from app.services.firebird_backup import FirebirdBackupResult
from app.services.firebird_client import FirebirdTestResult
from app.services.firebird_ms_users import FirebirdMsUserOption
from app.services.form_handling_config import default_public_base_url
from app.services.grenke_launch import GrenkeLaunchResult
from app.services.office365_backup import (
    Office365ConnectionResult,
    Office365UploadResult,
)
from app.services.optima_backup import OptimaBackupResult, OptimaDatabaseBackup
from app.services.security import hash_password, hash_session_token
from app.services.settings_store import StoredValue
from app.services.workflow_machine_binding import (
    WorkflowDeviceBindingItem,
    WorkflowDeviceOwnershipConflict,
    WorkflowDeviceOwnershipConflictItem,
)
from app.services.workflow_sheet_sync import WorkflowSheetRuntimeConfig
from log_utils import append_log, daily_log_path


def _fake_component_results() -> tuple[FirebirdBackupResult, OptimaBackupResult]:
    """Buduje kompletne wyniki komponentów bez wykonywania zewnętrznych narzędzi."""
    firebird = FirebirdBackupResult(
        backup_path=Path("firebird/ctip_firebird_prod_20260305_080000.fbk"),
        checksum_path=Path("firebird/ctip_firebird_prod_20260305_080000.fbk.sha256"),
        manifest_path=Path("firebird/ctip_firebird_prod_20260305_080000_manifest.json"),
        checksum="fb-checksum",
        size_bytes=1024,
        source_path="BAZAMS.FDB",
        verified=True,
    )
    optima_items = [
        OptimaDatabaseBackup(
            database_name=database,
            backup_path=Path(f"optima/ctip_optima_20260305_080000_{database}.bak"),
            checksum_path=Path(f"optima/ctip_optima_20260305_080000_{database}.bak.sha256"),
            checksum=f"checksum-{database}",
            size_bytes=2048,
        )
        for database in ("CDN_IT_Partner", "CDN_Ksero_Partner1", "CDN_KNF_Ksero_Partner")
    ]
    optima = OptimaBackupResult(
        database_backups=optima_items,
        manifest_path=Path("optima/ctip_optima_20260305_080000_manifest.json"),
        restore_verified_database="CDN_IT_Partner",
        verified=True,
    )
    return firebird, optima


def _empty_retention_result() -> tuple[RetentionPlan, RetentionApplyResult]:
    """Zwraca pusty wynik retencji używany w testach tras API."""
    now = datetime.now(UTC)
    return (
        RetentionPlan(
            retention_days=14,
            cutoff_at=now,
            sets=[],
            deletion_sets=[],
            preserved_newest_key=None,
            unknown_items=[],
            newer_incomplete_sets=[],
        ),
        RetentionApplyResult(dry_run=False),
    )


@compiles(JSONB, "sqlite")  # type: ignore[misc]
def _compile_jsonb_sqlite(_type: JSONB, compiler, **kw):  # noqa: ANN001
    """Pozwala odwzorować kolumny JSONB podczas testów na SQLite."""
    return "TEXT"


class AdminBackendTests(unittest.IsolatedAsyncioTestCase):
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
                    ContractsMailboxHistoryCase.__table__,
                    ContractsMailboxMessage.__table__,
                    WorkflowSheetStatusCache.__table__,
                    DeviceAuditRun.__table__,
                    DeviceAuditItem.__table__,
                    DeviceIntakeOperation.__table__,
                    DeviceInventoryUnit.__table__,
                    DeviceInventoryEvent.__table__,
                    DeviceManualReservation.__table__,
                    DeviceSheetOutbox.__table__,
                    Call.__table__,
                    CallEvent.__table__,
                    Contact.__table__,
                    ContactDevice.__table__,
                    IvrMap.__table__,
                    SmsTemplate.__table__,
                    SmsOut.__table__,
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
        self._previous_device_sheet_outbox_scheduler_enabled = (
            settings.device_sheet_outbox_scheduler_enabled
        )
        self._previous_block_client_communications = settings.block_client_communications
        settings.admin_secret_key = Fernet.generate_key().decode("ascii")
        settings.backup_scheduler_enabled = False
        settings.workflow_sheet_status_cache_scheduler_enabled = False
        settings.contracts_workflow_maintenance_scheduler_enabled = False
        settings.contracts_mailbox_scheduler_enabled = False
        settings.delivery_notifications_scheduler_enabled = False
        settings.device_sheet_outbox_scheduler_enabled = False
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
        self.send_email_mock = self._email_patch.start()

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
        settings.device_sheet_outbox_scheduler_enabled = (
            self._previous_device_sheet_outbox_scheduler_enabled
        )
        settings.block_client_communications = self._previous_block_client_communications
        await self.engine.dispose()

    async def _login_as(self, email: str, password: str) -> tuple[str, dict]:
        response = await self.client.post(
            "/admin/auth/login",
            json={"email": email, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        token = data["token"]
        self.assertTrue(token)
        return token, data

    async def _login(self) -> tuple[str, dict]:
        return await self._login_as("admin@example.com", "Sekret123!")

    async def _login_operator(self, remember: bool = False) -> tuple[str, dict]:
        response = await self.client.post(
            "/operator/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": remember,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        token = data["token"]
        self.assertTrue(token)
        return token, data

    async def _login_device_operator(self) -> tuple[str, dict]:
        """Nadaje testowe prawo urządzeń i loguje operatora."""
        async with self.session_factory() as session:
            user = await session.get(AdminUser, 2)
            assert user is not None
            await section_permissions.set_user_sections(
                session,
                user_id=user.id,
                role=user.role,
                sections=["operator", "generator", "device"],
                updated_by=1,
            )
            await session.commit()
        return await self._login_operator()

    async def _create_submitted_form_request(
        self,
        *,
        customer_name: str = "Klient Testowy",
        customer_email: str = "klient@test.local",
        customer_phone: str = "+48600111222",
        payload: dict | None = None,
        created_by: int = 1,
    ) -> FormRequest:
        now = datetime.now(UTC)
        submitted_payload = payload or {
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
                created_by=created_by,
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

    @staticmethod
    def _valid_public_form_post_data() -> dict[str, str]:
        representatives = [
            {
                "first_name": "Anna",
                "last_name": "Nowak",
                "representative_email": "anna.nowak@example.com",
                "representative_phone": "+48600111222",
                "pesel": "02270803624",
                "birth_date": "08:07:2002",
                "document_type": "Dowod osobisty",
                "document_number": "ABC123456",
                "document_issue_date": "02:05:2019",
                "document_expiry_date": "01:05:2029",
            }
        ]
        return {
            "company_name": "Firma Testowa Sp. z o.o.",
            "company_nip": "5250000000",
            "company_phone": "+48601122334",
            "company_email": "firma@example.com",
            "billing_email": "faktury@example.com",
            "registered_street": "Testowa",
            "registered_building_no": "1",
            "registered_apartment_no": "2",
            "registered_postal_code": "00-001",
            "registered_city": "Warszawa",
            "correspondence_street": "Korespondencyjna",
            "correspondence_building_no": "2",
            "correspondence_apartment_no": "3",
            "correspondence_postal_code": "00-002",
            "correspondence_city": "Warszawa",
            "representatives_json": json.dumps(representatives),
            "consent": "true",
            "website": "",
        }

    async def test_login_and_me_returns_user_info(self):
        token, payload = await self._login()
        self.assertIn("expires_at", payload)

        async with self.session_factory() as session:
            stored_token = (await session.execute(select(AdminSession.token))).scalar_one()
        self.assertNotEqual(stored_token, token)
        self.assertEqual(stored_token, hash_session_token(token))

        response = await self.client.get(
            "/admin/auth/me",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["email"], "admin@example.com")
        self.assertEqual(data["first_name"], "Jan")
        self.assertEqual(data["role"], "admin")
        self.assertFalse(data["is_salesperson"])
        self.assertIn("mobile_phone", data)
        self.assertEqual(data["sections"], list(section_permissions.DEFAULT_ADMIN_SECTIONS))

    async def test_login_is_blocked_after_failure_limit(self):
        with (
            patch.object(settings, "login_failure_limit", 2),
            patch.object(settings, "login_failure_window_minutes", 15),
        ):
            for _ in range(2):
                response = await self.client.post(
                    "/admin/auth/login",
                    json={"email": "admin@example.com", "password": "BledneHaslo!"},
                )
                self.assertEqual(response.status_code, 401)

            blocked = await self.client.post(
                "/admin/auth/login",
                json={"email": "admin@example.com", "password": "Sekret123!"},
            )

        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["retry-after"], "900")
        async with self.session_factory() as session:
            failures = (
                (
                    await session.execute(
                        select(AdminAuditLog).where(AdminAuditLog.action == "security_login_failed")
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(len(failures), 2)
        self.assertNotIn("admin@example.com", json.dumps(failures[0].payload))

    async def test_direct_access_outside_lan_is_blocked(self):
        outside_client = AsyncClient(
            transport=ASGITransport(app=self.app, client=("10.10.10.10", 43120)),
            base_url="http://testserver",
        )
        try:
            with patch.object(
                settings,
                "panel_allowed_networks_raw",
                "127.0.0.0/8,::1/128,192.168.0.0/24",
            ):
                response = await outside_client.get("/health")
        finally:
            await outside_client.aclose()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "Dostęp do panelu jest dozwolony wyłącznie z sieci LAN.",
        )

    async def test_admin_login_sets_cookie_and_accepts_cookie_session(self):
        response = await self.client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "Sekret123!"},
        )
        self.assertEqual(response.status_code, 200)
        set_cookie = response.headers.get("set-cookie", "")
        self.assertIn(f"{settings.auth_cookie_name}=", set_cookie)
        self.assertIn("httponly", set_cookie.lower())
        self.assertIn(f"samesite={settings.auth_cookie_samesite}", set_cookie.lower())

        me_response = await self.client.get("/admin/auth/me")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["email"], "admin@example.com")

    async def test_operator_login_allowed(self):
        token, payload = await self._login_operator()
        self.assertIn("expires_at", payload)

        response = await self.client.get(
            "/operator/api/me",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["email"], "operator@example.com")
        self.assertEqual(data["role"], "operator")
        self.assertFalse(data["is_salesperson"])
        self.assertIn("operator", data["sections"])

    async def test_portal_login_and_me_returns_sections(self):
        response = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        token = body["token"]
        self.assertTrue(token)
        self.assertIn("operator", body["sections"])
        self.assertIn("generator", body["sections"])

        me_response = await self.client.get(
            "/auth/me",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(me_response.status_code, 200)
        me = me_response.json()
        self.assertEqual(me["email"], "operator@example.com")
        self.assertEqual(me["role"], "operator")
        self.assertFalse(me["is_salesperson"])
        self.assertIn("operator", me["sections"])

    async def test_portal_logout_clears_cookie_session(self):
        response = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": True,
            },
        )
        self.assertEqual(response.status_code, 200)

        me_response = await self.client.get("/auth/me")
        self.assertEqual(me_response.status_code, 200)

        logout_response = await self.client.post("/auth/logout")
        self.assertEqual(logout_response.status_code, 204)
        set_cookie = logout_response.headers.get("set-cookie", "")
        self.assertIn(f"{settings.auth_cookie_name}=", set_cookie)
        self.assertIn("max-age=0", set_cookie.lower())

        after_logout_response = await self.client.get("/auth/me")
        self.assertEqual(after_logout_response.status_code, 401)

    async def test_portal_device_theme_is_saved_on_user_account(self):
        login_response = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": True,
            },
        )
        self.assertEqual(login_response.status_code, 200)

        update_response = await self.client.put(
            "/auth/preferences/device-theme",
            json={"theme": "mint"},
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["theme"], "mint")

        me_response = await self.client.get("/auth/me")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["device_theme"], "mint")

        invalid_response = await self.client.put(
            "/auth/preferences/device-theme",
            json={"theme": "sand"},
        )
        self.assertEqual(invalid_response.status_code, 422)

    async def test_portal_shipping_layout_is_saved_on_user_account(self):
        login_response = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": True,
            },
        )
        self.assertEqual(login_response.status_code, 200)

        initial_me_response = await self.client.get("/auth/me")
        self.assertEqual(initial_me_response.status_code, 200)
        self.assertEqual(initial_me_response.json()["shipping_layout"], "v2")

        update_response = await self.client.put(
            "/auth/preferences/shipping-layout",
            json={"layout": "legacy"},
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["layout"], "legacy")

        me_response = await self.client.get("/auth/me")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["shipping_layout"], "legacy")

        async with self.session_factory() as session:
            audit_entry = (
                await session.execute(
                    select(AdminAuditLog).where(
                        AdminAuditLog.action == "portal_shipping_layout_update"
                    )
                )
            ).scalar_one()
        self.assertEqual(audit_entry.payload["layout"], "legacy")

        invalid_response = await self.client.put(
            "/auth/preferences/shipping-layout",
            json={"layout": "compact"},
        )
        self.assertEqual(invalid_response.status_code, 422)

    async def test_operator_login_requires_operator_section(self):
        async with self.session_factory() as session:
            user = (await session.execute(select(AdminUser).where(AdminUser.id == 2))).scalar_one()
            await section_permissions.set_user_sections(
                session,
                user_id=user.id,
                role=user.role,
                sections=["generator"],
                updated_by=1,
            )
            await session.commit()

        response = await self.client.post(
            "/operator/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": False,
            },
        )
        self.assertEqual(response.status_code, 403)
        detail = response.json().get("detail", "")
        self.assertIn("uprawnień operatora", detail)

        portal_response = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": False,
            },
        )
        self.assertEqual(portal_response.status_code, 200)
        sections = portal_response.json()["sections"]
        self.assertEqual(sections, ["generator"])

    async def test_login_remember_me_extends_session(self):
        response_default = await self.client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "Sekret123!", "remember_me": False},
        )
        self.assertEqual(response_default.status_code, 200)
        default_expires = datetime.fromisoformat(response_default.json()["expires_at"])
        delta_default = default_expires - datetime.now(UTC)

        response_extended = await self.client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "Sekret123!", "remember_me": True},
        )
        self.assertEqual(response_extended.status_code, 200)
        extended_expires = datetime.fromisoformat(response_extended.json()["expires_at"])
        delta_extended = extended_expires - datetime.now(UTC)

        self.assertGreater(delta_default.total_seconds(), 0)
        self.assertGreater(delta_extended, delta_default + timedelta(hours=10))

    async def test_operator_login_remember_me_extends_session(self):
        response_default = await self.client.post(
            "/operator/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": False,
            },
        )
        self.assertEqual(response_default.status_code, 200)
        default_expires = datetime.fromisoformat(response_default.json()["expires_at"])
        delta_default = default_expires - datetime.now(UTC)

        response_extended = await self.client.post(
            "/operator/auth/login",
            json={"email": "operator@example.com", "password": "Operator123!", "remember_me": True},
        )
        self.assertEqual(response_extended.status_code, 200)
        extended_expires = datetime.fromisoformat(response_extended.json()["expires_at"])
        delta_extended = extended_expires - datetime.now(UTC)

        self.assertGreater(delta_default.total_seconds(), 0)
        self.assertGreater(delta_extended, delta_default + timedelta(hours=10))

    async def test_operator_profile_update(self):
        token, _ = await self._login_operator()
        update_payload = {
            "email": "operator.updated@example.com",
            "first_name": "Joanna",
            "last_name": "Nowak",
            "internal_ext": "105",
            "mobile_phone": "+48 600 700 800",
        }
        response = await self.client.put(
            "/operator/api/profile",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["email"], update_payload["email"])
        self.assertEqual(data["first_name"], "Joanna")
        self.assertEqual(data["internal_ext"], "105")
        self.assertEqual(data["mobile_phone"], "+48600700800")

        response = await self.client.get(
            "/operator/api/profile",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        fetched = response.json()
        self.assertEqual(fetched["email"], update_payload["email"])

        async with self.session_factory() as session:
            stmt = select(AdminUser).where(AdminUser.email == update_payload["email"])
            user = (await session.execute(stmt)).scalar_one()
            self.assertEqual(user.first_name, "Joanna")
            self.assertEqual(user.internal_ext, "105")
            self.assertEqual(user.mobile_phone, "+48600700800")

    async def test_operator_change_password(self):
        token, _ = await self._login_operator()
        bad_response = await self.client.post(
            "/operator/api/profile/change-password",
            json={"current_password": "Niepoprawne!", "new_password": "NoweHaslo123!"},
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(bad_response.status_code, 400)

        good_response = await self.client.post(
            "/operator/api/profile/change-password",
            json={"current_password": "Operator123!", "new_password": "NoweHaslo123!"},
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(good_response.status_code, 204)

        old_password = await self.client.post(
            "/operator/auth/login",
            json={"email": "operator@example.com", "password": "Operator123!"},
        )
        self.assertEqual(old_password.status_code, 401)

        new_password = await self.client.post(
            "/operator/auth/login",
            json={"email": "operator@example.com", "password": "NoweHaslo123!"},
        )
        self.assertEqual(new_password.status_code, 200)

    async def test_legacy_operator_apis_are_not_published(self):
        for path in ("/calls", "/contacts?search=a", "/sms/templates"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 404)

    async def test_legacy_sms_api_rejects_spoofed_x_user_id_header(self):
        response = await self.client.get("/sms/templates", headers={"X-User-Id": "1"})
        self.assertEqual(response.status_code, 404)

    async def test_public_form_double_submit_returns_already_submitted_without_mutation(self):
        token, _ = await self._login()
        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Podwojny Submit",
                "customer_email": "podwojny@example.com",
                "customer_phone": "+48600123456",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_id = create_response.json()["item"]["id"]
        form_url = create_response.json()["form_url"]
        link_token = form_url.rsplit("/formularz/", maxsplit=1)[-1]

        post_data = self._valid_public_form_post_data()
        with (
            patch(
                "app.services.form_generator._sync_submitted_form_with_firebird_ms",
                AsyncMock(),
            ),
            patch(
                "app.services.form_generator._dispatch_submission_notifications",
                AsyncMock(),
            ),
        ):
            first_submit = await self.client.post(f"/formularz/{link_token}", data=post_data)
            self.assertEqual(first_submit.status_code, 200)
            self.assertIn("formularz został zapisany", first_submit.text.lower())

            async with self.session_factory() as session:
                form_after_first = await session.get(FormRequest, form_id)
                self.assertIsNotNone(form_after_first)
                assert form_after_first is not None
                first_submitted_at = form_after_first.submitted_at
                self.assertIsNotNone(first_submitted_at)

            second_submit = await self.client.post(f"/formularz/{link_token}", data=post_data)
            self.assertEqual(second_submit.status_code, 200)
            self.assertIn("formularz został już wysłany", second_submit.text.lower())

            async with self.session_factory() as session:
                form_after_second = await session.get(FormRequest, form_id)
                self.assertIsNotNone(form_after_second)
                assert form_after_second is not None
                self.assertEqual(form_after_second.submitted_at, first_submitted_at)

    async def test_operator_templates_crud(self):
        token, _ = await self._login_operator()

        async with self.session_factory() as session:
            global_template = SmsTemplate(
                name="Globalny komunikat",
                body="Dzień dobry, prosimy o kontakt.",
                scope="global",
                owner_id=None,
                is_active=True,
                created_by=1,
                updated_by=1,
            )
            session.add(global_template)
            await session.commit()
            global_id = global_template.id

        response_list = await self.client.get(
            "/operator/api/sms/templates",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response_list.status_code, 200)
        templates = response_list.json()
        self.assertTrue(any(item["id"] == global_id for item in templates))
        global_entry = next(item for item in templates if item["id"] == global_id)
        self.assertFalse(global_entry["editable"])

        create_payload = {"name": "Awaria", "body": "Wykryto awarię urządzenia.", "is_active": True}
        response_create = await self.client.post(
            "/operator/api/sms/templates",
            json=create_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response_create.status_code, 201)
        created = response_create.json()
        self.assertTrue(created["editable"])
        template_id = created["id"]

        update_payload = {
            "name": "Awaria - pilne",
            "body": "Pilny kontakt wymagany.",
            "is_active": False,
        }
        response_update = await self.client.put(
            f"/operator/api/sms/templates/{template_id}",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response_update.status_code, 200)
        updated = response_update.json()
        self.assertEqual(updated["name"], "Awaria - pilne")
        self.assertFalse(updated["is_active"])

        response_delete = await self.client.delete(
            f"/operator/api/sms/templates/{template_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response_delete.status_code, 204)

        response_forbidden_update = await self.client.put(
            f"/operator/api/sms/templates/{global_id}",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response_forbidden_update.status_code, 403)

        response_forbidden_delete = await self.client.delete(
            f"/operator/api/sms/templates/{global_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response_forbidden_delete.status_code, 403)

    async def test_update_database_config_persists_values(self):
        token, _ = await self._login()
        update_payload = {
            "host": "10.0.0.5",
            "port": 5544,
            "database": "ctip_prod",
            "user": "collector",
            "sslmode": "require",
            "password": "NoweHaslo!",
        }
        response = await self.client.put(
            "/admin/config/database",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["password_set"])
        self.assertEqual(body["host"], update_payload["host"])
        self.assertEqual(body["port"], update_payload["port"])

        # ponowny odczyt powinien zwrócić te same wartości
        response = await self.client.get(
            "/admin/config/database",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"], update_payload["user"])
        self.assertEqual(body["sslmode"], "require")

        # w bazie powinno być zapisane ustawienie host
        async with self.session_factory() as session:
            setting = await session.get(AdminSetting, "database.host")
            self.assertIsNotNone(setting)
            self.assertEqual(setting.value, "10.0.0.5")

    async def test_update_firebird_config_persists_values(self):
        token, _ = await self._login()
        update_payload = {
            "mode": "network",
            "host": "192.168.0.8",
            "port": 3050,
            "database": "C:/MS/BAZA/MS.FDB",
            "user": "SYSDBA",
            "password": "ctip-test-only",
            "charset": "UTF8",
            "role": "RDB$ADMIN",
            "local_copy_path": "inbox/firebird/ms_local.fdb",
            "allow_writes": True,
        }
        response = await self.client.put(
            "/admin/config/firebird",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "network")
        self.assertEqual(body["host"], update_payload["host"])
        self.assertEqual(body["database"], update_payload["database"])
        self.assertEqual(body["charset"], "UTF8")
        self.assertEqual(body["role"], "RDB$ADMIN")
        self.assertEqual(body["local_copy_path"], update_payload["local_copy_path"])
        self.assertTrue(body["allow_writes"])
        self.assertTrue(body["password_set"])

        response = await self.client.get(
            "/admin/config/firebird",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"], update_payload["user"])
        self.assertEqual(body["port"], update_payload["port"])
        self.assertTrue(body["allow_writes"])

        async with self.session_factory() as session:
            setting = await session.get(AdminSetting, "firebird.host")
            self.assertIsNotNone(setting)
            self.assertEqual(setting.value, update_payload["host"])
            allow_writes_setting = await session.get(AdminSetting, "firebird.allow_writes")
            self.assertIsNotNone(allow_writes_setting)
            assert allow_writes_setting is not None
            self.assertEqual(allow_writes_setting.value, "true")

    async def test_update_firebird_vmaintenance_config_persists_values(self):
        token, _ = await self._login()
        update_payload = {
            "host": "192.168.0.8",
            "port": 3050,
            "database": "D:/bazavmantenance/BAZA_CPC.FDB",
            "user": "SYSDBA",
            "password": "ctip-test-only",
            "charset": "WIN1250",
            "role": "RDB$ADMIN",
        }
        response = await self.client.put(
            "/admin/config/firebird-vmaintenance",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["host"], update_payload["host"])
        self.assertEqual(body["database"], update_payload["database"])
        self.assertEqual(body["role"], update_payload["role"])
        self.assertTrue(body["password_set"])

        response = await self.client.get(
            "/admin/config/firebird-vmaintenance",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"], update_payload["user"])
        self.assertEqual(body["port"], update_payload["port"])

        async with self.session_factory() as session:
            setting = await session.get(AdminSetting, "firebird_vmaintenance.host")
            self.assertIsNotNone(setting)
            self.assertEqual(setting.value, update_payload["host"])

    async def test_update_google_sheets_config_persists_values(self):
        token, _ = await self._login()
        update_payload = {
            "enabled": True,
            "credentials_path": "/home/marcin/projects/secrets/google-sheets.json",
            "spreadsheet_id": "https://docs.google.com/spreadsheets/d/spreadsheet-test-id/edit#gid=0",
            "workflow_devices_worksheet": "Urzadzenia_magazyn",
        }
        response = await self.client.put(
            "/admin/config/google-sheets",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["credentials_path"], update_payload["credentials_path"])
        self.assertEqual(body["spreadsheet_id"], "spreadsheet-test-id")
        self.assertEqual(
            body["workflow_devices_worksheet"],
            update_payload["workflow_devices_worksheet"],
        )
        self.assertEqual(body["source"], "admin")

        response = await self.client.get(
            "/admin/config/google-sheets",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["spreadsheet_id"], "spreadsheet-test-id")

        async with self.session_factory() as session:
            setting = await session.get(AdminSetting, "google_sheets.spreadsheet_id")
            self.assertIsNotNone(setting)
            self.assertEqual(setting.value, "spreadsheet-test-id")

    async def test_update_google_sheets_config_blocked_when_lock_enabled(self):
        token, _ = await self._login()

        baseline_payload = {
            "enabled": True,
            "credentials_path": "/srv/google/prod.json",
            "spreadsheet_id": "spreadsheet-prod",
            "workflow_devices_worksheet": "Urzadzenia_magazyn",
        }
        response = await self.client.put(
            "/admin/config/google-sheets",
            json=baseline_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)

        previous_lock = settings.google_sheets_config_lock
        settings.google_sheets_config_lock = True
        try:
            locked_payload = {
                "enabled": True,
                "credentials_path": "/srv/google/test.json",
                "spreadsheet_id": "spreadsheet-test",
                "workflow_devices_worksheet": "Urzadzenia_magazyn",
            }
            response = await self.client.put(
                "/admin/config/google-sheets",
                json=locked_payload,
                headers={"X-Admin-Session": token},
            )
            self.assertEqual(response.status_code, 423)
            self.assertIn("GOOGLE_SHEETS_CONFIG_LOCK=true", response.text)

            response = await self.client.get(
                "/admin/config/google-sheets",
                headers={"X-Admin-Session": token},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["credentials_path"], baseline_payload["credentials_path"])
            self.assertEqual(body["spreadsheet_id"], baseline_payload["spreadsheet_id"])
        finally:
            settings.google_sheets_config_lock = previous_lock

    @patch("app.api.routes.admin_google_sheets.test_workflow_sheet_connection")
    async def test_google_sheets_test_endpoint_uses_current_configuration(self, mock_test):
        mock_test.return_value = {
            "success": True,
            "message": "Połączenie z arkuszem Google dla FLOW zakończone sukcesem.",
            "service_account_email": "bot@example.com",
            "spreadsheet_title": "zerowki_testowy",
            "worksheet_found": True,
            "worksheet_title": "Urzadzenia_magazyn",
            "missing_headers": [],
        }
        token, _ = await self._login()
        await self.client.put(
            "/admin/config/google-sheets",
            json={
                "enabled": True,
                "credentials_path": "/srv/google/current.json",
                "spreadsheet_id": "spreadsheet-current",
                "workflow_devices_worksheet": "Urzadzenia_magazyn",
            },
            headers={"X-Admin-Session": token},
        )

        response = await self.client.post(
            "/admin/google-sheets/test",
            json={
                "credentials_path": "/srv/google/override.json",
                "spreadsheet_id": "https://docs.google.com/spreadsheets/d/spreadsheet-override/edit",
                "workflow_devices_worksheet": "Urzadzenia_magazyn",
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["spreadsheet_title"], "zerowki_testowy")
        mock_test.assert_called_once()
        config = mock_test.call_args.args[0]
        self.assertTrue(config.enabled)
        self.assertEqual(config.credentials_path, "/srv/google/override.json")
        self.assertEqual(config.spreadsheet_id, "spreadsheet-override")
        self.assertEqual(config.workflow_devices_worksheet, "Urzadzenia_magazyn")

    @patch("app.api.routes.admin_google_sheets.bootstrap_workflow_sheet_headers")
    async def test_google_sheets_bootstrap_headers_endpoint_uses_current_configuration(
        self,
        mock_bootstrap,
    ):
        mock_bootstrap.return_value = {
            "success": True,
            "message": "Przygotowano nagłówki FLOW. Dodano: SERIAL.",
            "service_account_email": "bot@example.com",
            "spreadsheet_title": "zerowki_testowy",
            "worksheet_title": "Urzadzenia_magazyn",
            "added_headers": ["SERIAL"],
            "existing_headers": ["PRODUCENT", "MODEL"],
        }
        token, _ = await self._login()
        await self.client.put(
            "/admin/config/google-sheets",
            json={
                "enabled": True,
                "credentials_path": "/srv/google/current.json",
                "spreadsheet_id": "spreadsheet-current",
                "workflow_devices_worksheet": "Urzadzenia_magazyn",
            },
            headers={"X-Admin-Session": token},
        )

        response = await self.client.post(
            "/admin/google-sheets/bootstrap-headers",
            json={
                "credentials_path": "/srv/google/override.json",
                "spreadsheet_id": "https://docs.google.com/spreadsheets/d/spreadsheet-bootstrap/edit",
                "workflow_devices_worksheet": "Urzadzenia_magazyn",
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["added_headers"], ["SERIAL"])
        self.assertEqual(body["existing_headers"], ["PRODUCENT", "MODEL"])
        mock_bootstrap.assert_called_once()
        config = mock_bootstrap.call_args.args[0]
        self.assertTrue(config.enabled)
        self.assertEqual(config.credentials_path, "/srv/google/override.json")
        self.assertEqual(config.spreadsheet_id, "spreadsheet-bootstrap")
        self.assertEqual(config.workflow_devices_worksheet, "Urzadzenia_magazyn")

    async def test_update_kp_repair_source_config_persists_values(self):
        token, _ = await self._login()
        update_payload = {
            "csv_directory": "inbox/ewidencja",
            "csv_pattern": "DPLAC*.csv",
            "email_lookback_months": 5,
        }
        response = await self.client.put(
            "/admin/config/kp-repair-source",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["csv_directory"], update_payload["csv_directory"])
        self.assertEqual(body["csv_pattern"], update_payload["csv_pattern"])
        self.assertEqual(body["email_lookback_months"], update_payload["email_lookback_months"])

        async with self.session_factory() as session:
            setting = await session.get(AdminSetting, "kp_repair.csv_directory")
            self.assertIsNotNone(setting)
            self.assertEqual(setting.value, update_payload["csv_directory"])

    @patch("app.api.routes.admin_firebird.test_firebird_connection")
    async def test_firebird_test_endpoint_uses_current_configuration(self, mock_test):
        mock_test.return_value = FirebirdTestResult(
            success=True,
            message="Połączenie z Firebird zakończone sukcesem.",
            engine_version="4.0.4",
        )
        token, _ = await self._login()
        await self.client.put(
            "/admin/config/firebird",
            json={
                "mode": "network",
                "host": "192.168.0.8",
                "port": 3050,
                "database": "C:/MS/BAZA/MS.FDB",
                "user": "SYSDBA",
                "password": "ctip-test-only",
                "charset": "UTF8",
                "role": None,
                "local_copy_path": "inbox/firebird/ms_local.fdb",
                "allow_writes": False,
            },
            headers={"X-Admin-Session": token},
        )

        response = await self.client.post(
            "/admin/firebird/test",
            json={
                "mode": "network",
                "host": "192.168.0.9",
                "port": 3051,
                "database": "D:/SERWIS/BAZA.FDB",
                "user": "TESTER",
                "password": "Sekret!",
                "charset": "WIN1250",
                "role": "RDB$ADMIN",
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["engine_version"], "4.0.4")
        mock_test.assert_called_once()
        kwargs = mock_test.call_args.kwargs
        self.assertEqual(kwargs["host"], "192.168.0.9")
        self.assertEqual(kwargs["port"], 3051)
        self.assertEqual(kwargs["database"], "D:/SERWIS/BAZA.FDB")
        self.assertEqual(kwargs["user"], "TESTER")
        self.assertEqual(kwargs["password"], "Sekret!")
        self.assertEqual(kwargs["charset"], "WIN1250")
        self.assertEqual(kwargs["role"], "RDB$ADMIN")

    @patch("app.api.routes.admin_firebird.test_firebird_connection")
    async def test_firebird_test_endpoint_uses_local_database_in_local_mode(self, mock_test):
        mock_test.return_value = FirebirdTestResult(
            success=True,
            message="Połączenie z lokalną bazą Firebird zakończone sukcesem.",
            engine_version="2.5.9",
        )
        token, _ = await self._login()
        await self.client.put(
            "/admin/config/firebird",
            json={
                "mode": "local",
                "host": "192.168.0.8",
                "port": 3050,
                "database": "D:/PROD/BAZAMS.FDB",
                "user": "SYSDBA",
                "password": "ctip-test-only",
                "charset": "WIN1250",
                "role": None,
                "local_copy_path": "/srv/firebird/local/BAZAMS_LOCAL.FDB",
                "allow_writes": False,
            },
            headers={"X-Admin-Session": token},
        )

        response = await self.client.post(
            "/admin/firebird/test",
            json={"mode": "local"},
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["engine_version"], "2.5.9")
        kwargs = mock_test.call_args.kwargs
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["database"], "/srv/firebird/local/BAZAMS_LOCAL.FDB")

    async def test_firebird_client_lookup_uses_runtime_configuration_from_admin_settings(self):
        token, _ = await self._login()
        response = await self.client.put(
            "/admin/config/firebird",
            json={
                "mode": "network",
                "host": "192.168.0.8",
                "port": 3050,
                "database": "D:/MS/BAZAMS.FDB",
                "user": "SYSDBA",
                "password": "sekret-ms",
                "charset": "WIN1250",
                "role": "RDB$ADMIN",
                "local_copy_path": "inbox/firebird/test_ms_local.fdb",
                "allow_writes": True,
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)

        async with self.session_factory() as session:
            config = await load_firebird_runtime_config(session)

        self.assertTrue(config.allow_writes)

        connect_calls: list[dict[str, object]] = []

        class DummyCursor:
            def execute(self, _query, _params=None):
                return None

            def fetchone(self):
                return None

            def close(self):
                return None

        class DummyConnection:
            def cursor(self):
                return DummyCursor()

            def close(self):
                return None

        class DummyFirebirdSql:
            @staticmethod
            def connect(**kwargs):
                connect_calls.append(kwargs)
                return DummyConnection()

        with patch.dict(sys.modules, {"firebirdsql": DummyFirebirdSql}):
            with use_firebird_runtime_config(config):
                match = find_client_in_firebird("525-000-11-11")

        self.assertFalse(match.found)
        self.assertIsNone(match.error)
        self.assertEqual(len(connect_calls), 1)
        self.assertEqual(connect_calls[0]["host"], "192.168.0.8")
        self.assertEqual(connect_calls[0]["port"], 3050)
        self.assertEqual(connect_calls[0]["database"], "D:/MS/BAZAMS.FDB")
        self.assertEqual(connect_calls[0]["user"], "SYSDBA")
        self.assertEqual(connect_calls[0]["password"], "sekret-ms")
        self.assertEqual(connect_calls[0]["charset"], "WIN1250")
        self.assertEqual(connect_calls[0]["role"], "RDB$ADMIN")

    def test_firebird_writes_enabled_accepts_existing_local_database_outside_repo(self):
        runtime = FirebirdRuntimeConfig(
            mode="local",
            host="127.0.0.1",
            port=3050,
            database="D:/BAZA_MS_KP/BAZAMS.FDB",
            user="SYSDBA",
            password="ctip-test-only",
            charset="WIN1250",
            role=None,
            local_copy_path="D:/BAZA_MS_KP/BAZAMS.FDB",
            allow_writes=True,
        )

        with (
            patch(
                "app.services.contracts_dashboard._resolve_firebird_runtime_config",
                return_value=runtime,
            ),
            patch(
                "app.services.contracts_dashboard._resolve_local_firebird_path",
                return_value=Path("D:/BAZA_MS_KP/BAZAMS.FDB"),
            ),
            patch("pathlib.Path.exists", return_value=True),
        ):
            enabled, reason = firebird_writes_enabled()

        self.assertTrue(enabled)
        self.assertIsNone(reason)

    @patch("app.api.routes.admin_firebird.test_firebird_connection")
    async def test_firebird_vmaintenance_test_endpoint_uses_current_configuration(self, mock_test):
        mock_test.return_value = FirebirdTestResult(
            success=True,
            message="Połączenie z Firebird zakończone sukcesem.",
            engine_version="4.0.4",
        )
        token, _ = await self._login()
        await self.client.put(
            "/admin/config/firebird-vmaintenance",
            json={
                "host": "192.168.0.8",
                "port": 3050,
                "database": "D:/bazavmantenance/BAZA_CPC.FDB",
                "user": "SYSDBA",
                "password": "ctip-test-only",
                "charset": "WIN1250",
                "role": None,
            },
            headers={"X-Admin-Session": token},
        )
        response = await self.client.post(
            "/admin/firebird/test-vmaintenance",
            json={
                "host": "192.168.0.9",
                "port": 3051,
                "database": "D:/test/BAZA_CPC.FDB",
                "user": "TESTER",
                "password": "Sekret!",
                "charset": "UTF8",
                "role": "RDB$ADMIN",
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        kwargs = mock_test.call_args.kwargs
        self.assertEqual(kwargs["host"], "192.168.0.9")
        self.assertEqual(kwargs["port"], 3051)
        self.assertEqual(kwargs["database"], "D:/test/BAZA_CPC.FDB")
        self.assertEqual(kwargs["user"], "TESTER")
        self.assertEqual(kwargs["password"], "Sekret!")
        self.assertEqual(kwargs["charset"], "UTF8")
        self.assertEqual(kwargs["role"], "RDB$ADMIN")

    async def test_ctip_status_endpoint_returns_diagnostics(self):
        token, _ = await self._login()
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            session.add(
                CallEvent(
                    ts=now - timedelta(minutes=2),
                    typ="RINGING",
                    ext="101",
                    number="123456789",
                    payload=None,
                )
            )
            await session.commit()

        response = await self.client.get(
            "/admin/status/ctip",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["card"]["title"], "Centrala CTIP")
        self.assertIsNotNone(data["last_event_at"])

    async def test_backup_history_returns_items(self):
        token, _ = await self._login()
        now = datetime.now(UTC)
        fake_entries = [
            BackupFileInfo(
                name="backup_2025-10-11.dump",
                size_bytes=1024,
                modified_at=now,
                status="READY",
            ),
            BackupFileInfo(
                name="backup_2025-10-10.dump",
                size_bytes=2048,
                modified_at=now - timedelta(days=1),
                status="READY",
            ),
        ]

        with patch("app.api.routes.admin_backup.list_backup_files", return_value=fake_entries):
            response = await self.client.get(
                "/admin/backup/history",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["items"][0]["name"], "backup_2025-10-11.dump")
        self.assertEqual(data["items"][0]["status"], "READY")

    async def test_backup_history_requires_admin_role(self):
        token, _ = await self._login_operator()
        response = await self.client.get(
            "/admin/backup/history",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

    async def test_backup_config_get_and_update(self):
        token, _ = await self._login()

        response = await self.client.get(
            "/admin/backup/config",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["schedule_morning"], "06:00")
        self.assertEqual(data["schedule_evening"], "20:00")
        self.assertEqual(data["retention_local_copies"], 14)
        self.assertEqual(data["storage_mode"], "local")

        update_payload = {
            "schedule_morning": "05:30",
            "schedule_evening": "21:15",
            "retention_local_copies": 14,
            "retention_cloud_copies": 7,
            "archive_ctip_files": True,
            "archive_ctip_db": True,
            "archive_firebird_prod": True,
            "archive_firebird_test": False,
            "archive_optima": True,
            "storage_mode": "network",
            "local_directory": "D:\\\\Backup_CTIP_MS",
            "network_directory": "\\\\NAS\\\\CTIP",
            "cloud_provider": "office365",
            "cloud_only_evening": True,
            "office_tenant_id": "tenant-id",
            "office_client_id": "client-id",
            "office_site_id": "tenant.sharepoint.com,site-id,web-id",
            "office_drive_id": "drive-id",
            "office_folder_path": "CTIP-Backup",
            "office_folder_ctip": "BackupKP/CTIP",
            "office_folder_firebird_prod": "BackupKP/Menadzer_Serwisu/prod",
            "office_folder_firebird_test": "BackupKP/Menadzer_Serwisu/test",
            "office_folder_optima": "BackupKP/Optima",
            "office_client_secret": "top-secret",
            "optima_server_instance": "SERWER1\\\\OPTIMA",
            "optima_host": "192.168.0.8",
            "optima_port": 1433,
            "optima_auth_mode": "mixed",
            "optima_login": "automate_backup",
            "optima_password": "secret123",
            "optima_db_it_partner": "CDN_IT_Partner",
            "optima_db_ksero_partner": "CDN_Ksero_Partner1",
            "optima_db_config": "CDN_KNF_Ksero_Partner",
        }
        response = await self.client.put(
            "/admin/backup/config",
            headers={"X-Admin-Session": token},
            json=update_payload,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["schedule_morning"], "05:30")
        self.assertEqual(data["schedule_evening"], "21:15")
        self.assertEqual(data["storage_mode"], "network")
        self.assertTrue(data["office_client_secret_set"])
        self.assertEqual(data["optima_server_instance"], "SERWER1\\\\OPTIMA")
        self.assertEqual(data["optima_host"], "192.168.0.8")
        self.assertEqual(data["optima_port"], 1433)
        self.assertEqual(data["optima_auth_mode"], "mixed")
        self.assertEqual(data["optima_login"], "automate_backup")
        self.assertEqual(data["office_folder_ctip"], "BackupKP/CTIP")
        self.assertEqual(data["office_folder_firebird_prod"], "BackupKP/Menadzer_Serwisu/prod")
        self.assertEqual(data["office_folder_firebird_test"], "BackupKP/Menadzer_Serwisu/test")
        self.assertEqual(data["office_folder_optima"], "BackupKP/Optima")
        self.assertTrue(data["optima_password_set"])
        self.assertEqual(data["optima_db_it_partner"], "CDN_IT_Partner")
        self.assertEqual(data["optima_db_ksero_partner"], "CDN_Ksero_Partner1")
        self.assertEqual(data["optima_db_config"], "CDN_KNF_Ksero_Partner")

        async with self.session_factory() as session:
            stored = await settings_store.get_namespace(session, "backup")
            self.assertEqual(stored.get("schedule_morning"), "05:30")
            self.assertEqual(stored.get("network_directory"), "\\\\NAS\\\\CTIP")
            self.assertEqual(stored.get("office_tenant_id"), "tenant-id")
            self.assertEqual(stored.get("office_client_secret"), "top-secret")
            self.assertEqual(stored.get("office_site_id"), "tenant.sharepoint.com,site-id,web-id")
            self.assertEqual(stored.get("office_folder_ctip"), "BackupKP/CTIP")
            self.assertEqual(
                stored.get("office_folder_firebird_prod"), "BackupKP/Menadzer_Serwisu/prod"
            )
            self.assertEqual(
                stored.get("office_folder_firebird_test"), "BackupKP/Menadzer_Serwisu/test"
            )
            self.assertEqual(stored.get("office_folder_optima"), "BackupKP/Optima")
            self.assertEqual(stored.get("optima_server_instance"), "SERWER1\\\\OPTIMA")
            self.assertEqual(stored.get("optima_host"), "192.168.0.8")
            self.assertEqual(stored.get("optima_port"), "1433")
            self.assertEqual(stored.get("optima_auth_mode"), "mixed")
            self.assertEqual(stored.get("optima_login"), "automate_backup")
            self.assertEqual(stored.get("optima_password"), "secret123")
            self.assertEqual(stored.get("optima_db_it_partner"), "CDN_IT_Partner")
            self.assertEqual(stored.get("optima_db_ksero_partner"), "CDN_Ksero_Partner1")
            self.assertEqual(stored.get("optima_db_config"), "CDN_KNF_Ksero_Partner")

            result = await session.execute(
                select(AdminAuditLog).where(AdminAuditLog.action == "backup_config_update")
            )
            entry = result.scalars().first()
            self.assertIsNotNone(entry)

    async def test_backup_office365_test_resolves_drive(self):
        token, _ = await self._login()
        async with self.session_factory() as session:
            await settings_store.set_namespace(
                session,
                "backup",
                {
                    "office_tenant_id": StoredValue("tenant-id", False),
                    "office_client_id": StoredValue("client-id", False),
                    "office_client_secret": StoredValue("secret", True),
                    "office_site_id": StoredValue("kseropartner.sharepoint.com,site,web", False),
                    "office_folder_path": StoredValue("CTIP-Backup/Prod", False),
                    "office_folder_ctip": StoredValue("BackupKP/CTIP", False),
                },
                user_id=1,
            )
            await session.commit()

        fake_result = Office365ConnectionResult(
            ok=True,
            message="Połączenie z Office 365 (SharePoint) działa poprawnie.",
            site_id="kseropartner.sharepoint.com,site,web",
            drive_id="b!drive",
            folder_path="CTIP-Backup/Prod",
        )
        with patch(
            "app.api.routes.admin_backup.test_office365_connection",
            new=AsyncMock(return_value=fake_result),
        ):
            response = await self.client.post(
                "/admin/backup/office365/test",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["drive_id"], "b!drive")

        async with self.session_factory() as session:
            stored = await settings_store.get_namespace(session, "backup")
            self.assertEqual(stored.get("office_drive_id"), "b!drive")

    async def test_backup_run_dry_creates_audit_entry(self):
        token, _ = await self._login()
        response = await self.client.post(
            "/admin/backup/run",
            headers={"X-Admin-Session": token},
            json={"label": "nocny", "compress": True, "dry_run": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["accepted"])
        self.assertTrue(data["dry_run"])
        self.assertIsNone(data["backup_name"])

        async with self.session_factory() as session:
            result = await session.execute(
                select(AdminAuditLog).where(AdminAuditLog.action == "backup_run_dry")
            )
            entry = result.scalars().first()
            self.assertIsNotNone(entry)
            self.assertIsNotNone(entry.payload)
            self.assertEqual(entry.payload.get("label"), "nocny")
            self.assertTrue(entry.payload.get("compress"))
            self.assertTrue(entry.payload.get("dry_run"))

    async def test_backup_run_blocked_records_audit(self):
        token, _ = await self._login()
        prev = settings.backup_execution_enabled
        settings.backup_execution_enabled = False
        try:
            response = await self.client.post(
                "/admin/backup/run",
                headers={"X-Admin-Session": token},
                json={"label": "reczny", "compress": False, "dry_run": False},
            )
        finally:
            settings.backup_execution_enabled = prev
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertEqual(data["detail"], "Backup jest wyłączony poza środowiskiem produkcyjnym.")

        async with self.session_factory() as session:
            result = await session.execute(
                select(AdminAuditLog).where(AdminAuditLog.action == "backup_run_blocked_non_prod")
            )
            entry = result.scalars().first()
            self.assertIsNotNone(entry)
            self.assertIsNotNone(entry.payload)
            self.assertEqual(entry.payload.get("label"), "reczny")
            self.assertFalse(entry.payload.get("compress"))

    async def test_backup_run_enabled_creates_backup(self):
        token, _ = await self._login()
        prev = settings.backup_execution_enabled
        settings.backup_execution_enabled = True
        fake_run = BackupRunResult(
            backup_name="backup_20260304_170000.tar.gz",
            backup_path=Path("backups/backup_20260304_170000.tar.gz"),
            checksum="abc123",
            checksum_path=Path("backups/backup_20260304_170000.tar.gz.sha256"),
            size_bytes=2048,
            notes=[],
            included_components=["postgresql_ctip"],
            postgres_dump_included=True,
        )
        fake_firebird, fake_optima = _fake_component_results()
        try:
            with (
                patch(
                    "app.api.routes.admin_backup.create_local_backup",
                    return_value=fake_run,
                ),
                patch(
                    "app.api.routes.admin_backup.create_firebird_backup",
                    return_value=fake_firebird,
                ),
                patch(
                    "app.api.routes.admin_backup.create_optima_backup",
                    return_value=fake_optima,
                ),
                patch(
                    "app.api.routes.admin_backup.run_local_retention",
                    return_value=_empty_retention_result(),
                ),
            ):
                response = await self.client.post(
                    "/admin/backup/run",
                    headers={"X-Admin-Session": token},
                    json={"label": "reczny", "compress": True, "dry_run": False},
                )
        finally:
            settings.backup_execution_enabled = prev

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["accepted"])
        self.assertFalse(data["dry_run"])
        self.assertEqual(data["backup_name"], "backup_20260304_170000.tar.gz")
        self.assertTrue(data["postgres_dump_included"])

    async def test_backup_run_uploads_complete_archive_once(self):
        token, _ = await self._login()
        prev = settings.backup_execution_enabled
        settings.backup_execution_enabled = True
        fake_run = BackupRunResult(
            backup_name="backup_20260305_080000.tar.gz",
            backup_path=Path("backups/backup_20260305_080000.tar.gz"),
            checksum="def456",
            checksum_path=Path("backups/backup_20260305_080000.tar.gz.sha256"),
            size_bytes=4096,
            notes=[],
            included_components=["ctip_files", "postgresql_ctip"],
            postgres_dump_included=True,
        )
        fake_firebird, fake_optima = _fake_component_results()

        async def fake_upload(**kwargs):
            folder = kwargs.get("folder_path")
            file_path = kwargs.get("file_path")
            return Office365UploadResult(
                drive_id="drive-test",
                item_id="item-test",
                web_url=f"https://sharepoint.test/{folder}",
                name=file_path.name,
                size=1,
            )

        try:
            with (
                patch(
                    "app.api.routes.admin_backup.create_local_backup",
                    return_value=fake_run,
                ),
                patch(
                    "app.api.routes.admin_backup.create_firebird_backup",
                    return_value=fake_firebird,
                ),
                patch(
                    "app.api.routes.admin_backup.create_optima_backup",
                    return_value=fake_optima,
                ),
                patch(
                    "app.api.routes.admin_backup.upload_file_to_sharepoint",
                    new=AsyncMock(side_effect=fake_upload),
                ) as upload_mock,
                patch(
                    "app.api.routes.admin_backup.run_sharepoint_retention",
                    new=AsyncMock(return_value=_empty_retention_result()),
                ),
                patch(
                    "app.api.routes.admin_backup.run_local_retention",
                    return_value=_empty_retention_result(),
                ),
            ):
                response = await self.client.post(
                    "/admin/backup/run",
                    headers={"X-Admin-Session": token},
                    json={"label": "auto", "compress": True, "dry_run": False},
                )
        finally:
            settings.backup_execution_enabled = prev

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["accepted"])
        self.assertEqual(data["status"], "SUCCESS")
        self.assertTrue(data["postgres_dump_included"])
        self.assertTrue(data["uploaded_to_cloud"])
        self.assertIn("do SharePoint", data["message"])

        called_folders = [call.kwargs.get("folder_path") for call in upload_mock.await_args_list]
        self.assertEqual(
            set(called_folders),
            {"BackupKP/CTIP", "BackupKP/Menadzer_Serwisu/prod", "BackupKP/Optima"},
        )
        self.assertEqual(len(called_folders), 12)

    async def test_backup_restore_dry_creates_audit_entry(self):
        token, _ = await self._login()
        response = await self.client.post(
            "/admin/backup/restore",
            headers={"X-Admin-Session": token},
            json={"backup_name": "backup_2025-10-11.dump", "dry_run": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["accepted"])
        self.assertTrue(data["dry_run"])

        async with self.session_factory() as session:
            result = await session.execute(
                select(AdminAuditLog).where(AdminAuditLog.action == "backup_restore_dry")
            )
            entry = result.scalars().first()
            self.assertIsNotNone(entry)
            self.assertIsNotNone(entry.payload)
            self.assertEqual(entry.payload.get("backup_name"), "backup_2025-10-11.dump")
            self.assertTrue(entry.payload.get("dry_run"))

    async def test_backup_restore_blocked_records_audit(self):
        token, _ = await self._login()
        prev = settings.backup_execution_enabled
        settings.backup_execution_enabled = False
        try:
            response = await self.client.post(
                "/admin/backup/restore",
                headers={"X-Admin-Session": token},
                json={"backup_name": "backup_2025-10-11.dump", "dry_run": False},
            )
        finally:
            settings.backup_execution_enabled = prev
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertEqual(
            data["detail"], "Przywracanie kopii jest wyłączone poza środowiskiem produkcyjnym."
        )

        async with self.session_factory() as session:
            result = await session.execute(
                select(AdminAuditLog).where(
                    AdminAuditLog.action == "backup_restore_blocked_non_prod"
                )
            )
            entry = result.scalars().first()
            self.assertIsNotNone(entry)
            self.assertIsNotNone(entry.payload)
            self.assertEqual(entry.payload.get("backup_name"), "backup_2025-10-11.dump")

    async def test_contacts_crud_flow(self):
        token, _ = await self._login()
        create_payload = {
            "number": "+48600700800",
            "ext": "105",
            "first_name": "Adam",
            "last_name": "Nowak",
            "email": "adam.nowak@example.com",
            "company": "Dostawca Serwisu",
            "firebird_id": "FB-12345",
            "nip": "5250000000",
            "notes": "Kontakt testowy",
            "source": "manual",
        }
        response = await self.client.post(
            "/admin/contacts",
            headers={"X-Admin-Session": token},
            json=create_payload,
        )
        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertEqual(created["number"], create_payload["number"])
        self.assertEqual(created["firebird_id"], create_payload["firebird_id"])
        contact_id = created["id"]

        response = await self.client.get(
            "/admin/contacts",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["id"], contact_id)

        update_payload = {
            **create_payload,
            "company": "Nowa Firma Sp. z o.o.",
            "firebird_id": "FB-99999",
            "notes": "Zmienione notatki",
        }
        response = await self.client.put(
            f"/admin/contacts/{contact_id}",
            headers={"X-Admin-Session": token},
            json=update_payload,
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["company"], "Nowa Firma Sp. z o.o.")
        self.assertEqual(updated["firebird_id"], "FB-99999")

        response = await self.client.get(
            f"/admin/contacts/{contact_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["notes"], "Zmienione notatki")

        response = await self.client.delete(
            f"/admin/contacts/{contact_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 204)

        response = await self.client.get(
            "/admin/contacts",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["items"]), 0)

        async with self.session_factory() as session:
            stmt = select(Contact).where(Contact.id == contact_id)
            result = await session.execute(stmt)
            self.assertIsNone(result.scalar_one_or_none())

    async def test_operator_can_manage_contacts(self):
        token, _ = await self._login_operator()
        response = await self.client.post(
            "/admin/contacts",
            headers={"X-Admin-Session": token},
            json={"number": "+48600111222"},
        )
        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertEqual(created["number"], "+48600111222")
        contact_id = created["id"]

        response = await self.client.put(
            f"/admin/contacts/{contact_id}",
            headers={"X-Admin-Session": token},
            json={
                "number": "+48600111222",
                "ext": "106",
                "first_name": "Operator",
                "last_name": "Test",
                "company": "Panel",
                "email": None,
                "firebird_id": "FB-OP-1",
                "nip": None,
                "notes": "Aktualizacja przez operatora",
                "source": "ctip",
            },
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["first_name"], "Operator")
        self.assertEqual(updated["firebird_id"], "FB-OP-1")

        lookup_response = await self.client.get(
            "/admin/contacts/by-number/+48600111222",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(lookup_response.status_code, 200)
        lookup_body = lookup_response.json()
        self.assertEqual(lookup_body["first_name"], "Operator")

        delete_response = await self.client.delete(
            f"/admin/contacts/{contact_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(delete_response.status_code, 403)

    async def test_contact_lookup_by_number(self):
        token, _ = await self._login()
        payload = {
            "number": "+48655111222",
            "first_name": "Jan",
            "last_name": "Nowy",
            "firebird_id": "FB-LOOKUP",
            "notes": "Z testu lookup",
            "source": "manual",
        }
        response = await self.client.post(
            "/admin/contacts",
            headers={"X-Admin-Session": token},
            json=payload,
        )
        self.assertEqual(response.status_code, 201)

        response = await self.client.get(
            "/admin/contacts/by-number/+48655111222",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["number"], "+48655111222")
        self.assertEqual(detail["firebird_id"], "FB-LOOKUP")

        response = await self.client.get(
            "/admin/contacts/by-number/+48123456789",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 404)

    async def test_operator_cannot_access_admin_config(self):
        token, _ = await self._login_operator()

        response = await self.client.get(
            "/admin/config/database",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.put(
            "/admin/config/database",
            headers={"X-Admin-Session": token},
            json={
                "host": "127.0.0.1",
                "port": 5432,
                "database": "ctip",
                "user": "operator",
                "sslmode": "disable",
                "password": "secret",
            },
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.get(
            "/admin/config/firebird",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.put(
            "/admin/config/firebird",
            headers={"X-Admin-Session": token},
            json={
                "host": "192.168.0.8",
                "port": 3050,
                "database": "C:/MS/BAZA/MS.FDB",
                "user": "SYSDBA",
                "charset": "UTF8",
                "role": None,
                "local_copy_path": "inbox/firebird/ms_local.fdb",
                "password": "secret",
            },
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.post(
            "/admin/firebird/test",
            headers={"X-Admin-Session": token},
            json={},
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.get(
            "/admin/config/firebird-vmaintenance",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.put(
            "/admin/config/firebird-vmaintenance",
            headers={"X-Admin-Session": token},
            json={
                "host": "192.168.0.8",
                "port": 3050,
                "database": "D:/bazavmantenance/BAZA_CPC.FDB",
                "user": "SYSDBA",
                "charset": "WIN1250",
                "role": None,
                "password": "secret",
            },
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.post(
            "/admin/firebird/test-vmaintenance",
            headers={"X-Admin-Session": token},
            json={},
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.get(
            "/admin/config/kp-repair-source",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.put(
            "/admin/config/kp-repair-source",
            headers={"X-Admin-Session": token},
            json={
                "csv_directory": "inbox/ewidencja",
                "csv_pattern": "DPLAC*.csv",
                "email_lookback_months": 5,
            },
        )
        self.assertEqual(response.status_code, 403)

        response = await self.client.get(
            "/admin/kp-repair/summary",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

    async def test_sms_status_reports_failures_and_credentials(self):
        token, _ = await self._login()
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            await settings_store.set_namespace(
                session,
                "sms",
                {"api_token": StoredValue(value="sekret-token", is_secret=True)},
                user_id=1,
            )
            session.add(
                SmsOut(
                    dest="+48123123123",
                    text="Awaria CTIP",
                    status="ERROR",
                    error_msg="Błąd gateway",
                    created_at=now - timedelta(minutes=10),
                    provider_status="ERR42",
                )
            )
            session.add(
                SmsOut(
                    dest="+48123123123",
                    text="Test...",
                    status="NEW",
                    created_at=now,
                )
            )
            await session.commit()

        response = await self.client.get(
            "/admin/status/sms",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["credentials_present"])
        self.assertGreaterEqual(data["failed_messages"], 1)
        self.assertGreaterEqual(data["pending_messages"], 1)
        self.assertIn("recent_messages", data)
        self.assertGreaterEqual(len(data["recent_messages"]), 2)
        self.assertEqual(data["card"]["title"], "SerwerSMS")
        self.assertEqual(data["card"]["diagnostics_endpoint"], "/admin/status/sms")

    async def test_admin_users_crud_flow(self):
        token, _ = await self._login()
        create_payload = {
            "email": "nowy.uzytkownik@example.com",
            "first_name": "Marek",
            "last_name": "Zielinski",
            "internal_ext": "205",
            "role": "operator",
            "is_salesperson": True,
            "crm_sales_sms_enabled": True,
            "crm_sales_email_enabled": True,
            "crm_operations_sms_enabled": False,
            "crm_operations_email_enabled": True,
            "mobile_phone": "+48600700800",
            "sections": ["operator"],
        }
        response = await self.client.post(
            "/admin/users",
            json=create_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        created_user = body["user"]
        user_id = created_user["id"]
        self.assertEqual(created_user["email"], create_payload["email"])
        self.assertEqual(created_user["mobile_phone"], "+48600700800")
        self.assertTrue(created_user["is_salesperson"])
        self.assertTrue(created_user["crm_sales_sms_enabled"])
        self.assertTrue(created_user["crm_sales_email_enabled"])
        self.assertFalse(created_user["crm_operations_sms_enabled"])
        self.assertTrue(created_user["crm_operations_email_enabled"])
        self.assertEqual(created_user["sections"], ["operator"])
        self.assertTrue(body["password"])
        self.assertTrue(body["sms_queued"])
        self.assertEqual(body["sms_recipient"], "+48600700800")
        self.send_email_mock.assert_awaited_once()

        async with self.session_factory() as session:
            sms_entries = await session.execute(select(SmsOut).order_by(SmsOut.id))
            sms_rows = sms_entries.scalars().all()
            matching = [
                row
                for row in sms_rows
                if isinstance(row.meta, dict) and row.meta.get("type") == "admin_user_credentials"
            ]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0].dest, "+48600700800")
            self.assertEqual(matching[0].origin, "admin_user_credentials")
            self.assertEqual(matching[0].status, "NEW")
            self.assertEqual(matching[0].meta.get("action"), "create")
            self.assertIn("Login: nowy.uzytkownik@example.com", matching[0].text)
            self.assertIn("Hasło:", matching[0].text)

        response = await self.client.get(
            "/admin/users",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertTrue(any(item["id"] == user_id for item in items))

        response = await self.client.get(
            f"/admin/users/{user_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["email"], create_payload["email"])
        self.assertEqual(detail["sessions_active"], 0)
        self.assertEqual(detail["mobile_phone"], "+48600700800")
        self.assertTrue(detail["is_salesperson"])
        self.assertTrue(detail["crm_sales_sms_enabled"])
        self.assertTrue(detail["crm_sales_email_enabled"])
        self.assertFalse(detail["crm_operations_sms_enabled"])
        self.assertTrue(detail["crm_operations_email_enabled"])
        self.assertEqual(detail["sections"], ["operator"])

        update_payload = {
            "email": "nowy.uzytkownik@example.com",
            "first_name": "Marek",
            "last_name": "Zielinski",
            "internal_ext": "305",
            "role": "admin",
            "is_salesperson": False,
            "crm_sales_sms_enabled": False,
            "crm_sales_email_enabled": True,
            "crm_operations_sms_enabled": True,
            "crm_operations_email_enabled": False,
            "mobile_phone": "+48600111222",
            "sections": ["admin", "generator"],
        }
        response = await self.client.put(
            f"/admin/users/{user_id}",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["internal_ext"], "305")
        self.assertEqual(updated["role"], "admin")
        self.assertFalse(updated["is_salesperson"])
        self.assertFalse(updated["crm_sales_sms_enabled"])
        self.assertTrue(updated["crm_sales_email_enabled"])
        self.assertTrue(updated["crm_operations_sms_enabled"])
        self.assertFalse(updated["crm_operations_email_enabled"])
        self.assertEqual(updated["mobile_phone"], "+48600111222")
        self.assertEqual(updated["sections"], list(section_permissions.DEFAULT_ADMIN_SECTIONS))

        async with self.session_factory() as session:
            db_user = await session.get(AdminUser, user_id)
            self.assertIsNotNone(db_user)
            self.assertFalse(db_user.is_salesperson)
            self.assertFalse(db_user.crm_sales_sms_enabled)
            self.assertTrue(db_user.crm_sales_email_enabled)
            self.assertTrue(db_user.crm_operations_sms_enabled)
            self.assertFalse(db_user.crm_operations_email_enabled)
            old_hash = db_user.password_hash

        response = await self.client.post(
            f"/admin/users/{user_id}/reset-password",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        reset_payload = response.json()
        self.assertGreaterEqual(len(reset_payload["password"]), 8)
        self.assertTrue(reset_payload["sms_queued"])
        self.assertEqual(reset_payload["sms_recipient"], "+48600111222")

        async with self.session_factory() as session:
            db_user = await session.get(AdminUser, user_id)
            self.assertIsNotNone(db_user)
            self.assertNotEqual(db_user.password_hash, old_hash)
            sms_entries = await session.execute(select(SmsOut).order_by(SmsOut.id))
            sms_rows = sms_entries.scalars().all()
            matching = [
                row
                for row in sms_rows
                if isinstance(row.meta, dict) and row.meta.get("type") == "admin_user_credentials"
            ]
            self.assertGreaterEqual(len(matching), 2)
            self.assertEqual(matching[-1].dest, "+48600111222")
            self.assertEqual(matching[-1].meta.get("action"), "password_reset")

        response = await self.client.patch(
            f"/admin/users/{user_id}/status",
            json={"is_active": False},
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        status_body = response.json()
        self.assertFalse(status_body["is_active"])

        async with self.session_factory() as session:
            actions = {"user_create", "user_update", "user_reset_password", "user_status_update"}
            result = await session.execute(select(AdminAuditLog))
            entries = [item for item in result.scalars().all() if item.action in actions]
            self.assertEqual(len(entries), 4)
            self.assertTrue(
                all(item.payload and item.payload.get("user_id") == user_id for item in entries)
            )

        response = await self.client.delete(
            f"/admin/users/{user_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 204)

        async with self.session_factory() as session:
            self.assertIsNone(await session.get(AdminUser, user_id))
            actions = {
                "user_create",
                "user_update",
                "user_reset_password",
                "user_status_update",
                "user_delete",
            }
            result = await session.execute(select(AdminAuditLog))
            entries = [item for item in result.scalars().all() if item.action in actions]
            self.assertTrue(
                any(
                    item.action == "user_delete"
                    and item.payload
                    and item.payload.get("deleted_user_id") == user_id
                    for item in entries
                )
            )

        self.assertEqual(self.send_email_mock.await_count, 2)

    async def test_admin_user_can_store_firebird_ms_mapping(self):
        token, _ = await self._login()
        create_payload = {
            "email": "ms.powiazanie@example.com",
            "first_name": "Marek",
            "last_name": "Serwis",
            "role": "operator",
            "mobile_phone": "+48600700800",
            "firebird_app_user_id": 208,
        }
        firebird_user = FirebirdMsUserOption(
            id=208,
            login_user="Marcin",
            workstation="MARCINJKP",
            app_name="Menadżer Serwisu Ksero",
        )
        updated_firebird_user = FirebirdMsUserOption(
            id=151,
            login_user="JoannaG",
            workstation="ROZLICZENIA",
            app_name="Menadżer Serwisu Ksero",
        )

        with patch(
            "app.api.routes.admin_users.firebird_ms_users.resolve_firebird_ms_user",
            AsyncMock(return_value=firebird_user),
        ):
            response = await self.client.post(
                "/admin/users",
                json=create_payload,
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        user_id = body["user"]["id"]
        self.assertEqual(body["user"]["firebird_app_user_id"], 208)
        self.assertEqual(body["user"]["firebird_app_user_login"], "Marcin")

        update_payload = {
            "email": "ms.powiazanie@example.com",
            "first_name": "Marek",
            "last_name": "Serwis",
            "role": "operator",
            "mobile_phone": "+48600700800",
            "firebird_app_user_id": 151,
        }
        with patch(
            "app.api.routes.admin_users.firebird_ms_users.resolve_firebird_ms_user",
            AsyncMock(return_value=updated_firebird_user),
        ):
            response = await self.client.put(
                f"/admin/users/{user_id}",
                json=update_payload,
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["firebird_app_user_id"], 151)
        self.assertEqual(body["firebird_app_user_login"], "JoannaG")

        async with self.session_factory() as session:
            db_user = await session.get(AdminUser, user_id)
            self.assertIsNotNone(db_user)
            self.assertEqual(db_user.firebird_app_user_id, 151)
            self.assertEqual(db_user.firebird_app_user_login, "JoannaG")

    async def test_admin_user_can_manage_imap_configuration(self):
        token, _ = await self._login()
        create_payload = {
            "email": "imap.konfiguracja@example.com",
            "first_name": "Iwona",
            "last_name": "Poczta",
            "role": "operator",
            "mobile_phone": "+48600666777",
            "imap": {
                "enabled": True,
                "email": "imap.konfiguracja@example.com",
                "host": "imap.example.com",
                "port": 993,
                "username": "imap.konfiguracja@example.com",
                "use_ssl": True,
                "folder": "INBOX",
                "password": "SekretneHaslo123!",
            },
        }
        response = await self.client.post(
            "/admin/users",
            json=create_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        user_id = int(body["user"]["id"])
        self.assertTrue(body["user"]["imap"]["enabled"])
        self.assertEqual(body["user"]["imap"]["host"], "imap.example.com")
        self.assertTrue(body["user"]["imap"]["password_set"])

        detail_response = await self.client.get(
            f"/admin/users/{user_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()
        self.assertTrue(detail_payload["imap"]["enabled"])
        self.assertEqual(detail_payload["imap"]["email"], "imap.konfiguracja@example.com")
        self.assertTrue(detail_payload["imap"]["password_set"])

        update_payload = {
            "email": "imap.konfiguracja@example.com",
            "first_name": "Iwona",
            "last_name": "Poczta",
            "internal_ext": None,
            "role": "operator",
            "is_salesperson": False,
            "mobile_phone": "+48600666777",
            "imap": {
                "enabled": False,
                "clear_password": True,
            },
        }
        update_response = await self.client.put(
            f"/admin/users/{user_id}",
            json=update_payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(update_response.status_code, 200)
        updated_payload = update_response.json()
        self.assertFalse(updated_payload["imap"]["enabled"])
        self.assertFalse(updated_payload["imap"]["password_set"])

        async with self.session_factory() as session:
            key_prefix = f"user_imap.{user_id}."
            rows = (
                (
                    await session.execute(
                        select(AdminSetting).where(AdminSetting.key.like(f"{key_prefix}%"))
                    )
                )
                .scalars()
                .all()
            )
            keys = {row.key: row for row in rows}
            self.assertIn(f"{key_prefix}host", keys)
            self.assertIn(f"{key_prefix}password", keys)
            self.assertTrue(keys[f"{key_prefix}password"].is_secret)

    async def test_admin_can_load_firebird_ms_user_options(self):
        token, _ = await self._login()
        options = [
            FirebirdMsUserOption(
                id=208,
                login_user="Marcin",
                workstation="MARCINJKP",
                app_name="Menadżer Serwisu Ksero",
            ),
            FirebirdMsUserOption(
                id=151,
                login_user="JoannaG",
                workstation="ROZLICZENIA",
                app_name="Menadżer Serwisu Ksero",
            ),
        ]
        with patch(
            "app.api.routes.admin_users.firebird_ms_users.list_firebird_ms_users",
            AsyncMock(return_value=options),
        ):
            response = await self.client.get(
                "/admin/users/firebird-ms-users",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["items"][0]["id"], 208)
        self.assertEqual(body["items"][0]["login_user"], "Marcin")
        self.assertEqual(body["items"][0]["label"], "Marcin (MARCINJKP)")

    async def test_portal_user_can_update_own_profile(self):
        response = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]

        response = await self.client.get(
            "/auth/profile",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        profile = response.json()
        self.assertEqual(profile["email"], "operator@example.com")

        update_payload = {
            "email": "operator.updated@example.com",
            "first_name": "Anna",
            "last_name": "Nowak-Nowa",
            "internal_ext": "222",
            "mobile_phone": "+48699111222",
        }
        response = await self.client.put(
            "/auth/profile",
            headers={"X-Admin-Session": token},
            json=update_payload,
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["email"], "operator.updated@example.com")
        self.assertEqual(updated["first_name"], "Anna")
        self.assertEqual(updated["last_name"], "Nowak-Nowa")
        self.assertEqual(updated["internal_ext"], "222")
        self.assertEqual(updated["mobile_phone"], "+48699111222")
        self.assertFalse(updated["is_salesperson"])
        self.assertIn("operator", updated["sections"])

        async with self.session_factory() as session:
            stmt = select(AdminUser).where(AdminUser.email == "operator.updated@example.com")
            db_user = (await session.execute(stmt)).scalar_one_or_none()
            self.assertIsNotNone(db_user)
            self.assertEqual(db_user.internal_ext, "222")
            self.assertEqual(db_user.mobile_phone, "+48699111222")
            self.assertFalse(db_user.is_salesperson)

            audit_stmt = select(AdminAuditLog).where(
                AdminAuditLog.action == "portal_profile_update"
            )
            audit_entries = (await session.execute(audit_stmt)).scalars().all()
            self.assertGreaterEqual(len(audit_entries), 1)

    async def test_portal_user_can_change_password_with_policy(self):
        response = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]

        weak_payload = {
            "current_password": "Operator123!",
            "new_password": "Abcdefg12",
        }
        response = await self.client.post(
            "/auth/profile/change-password",
            headers={"X-Admin-Session": token},
            json=weak_payload,
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn(
            "Hasło musi mieć co najmniej 9 znaków", str(response.json().get("detail", ""))
        )

        strong_payload = {
            "current_password": "Operator123!",
            "new_password": "NoweHaslo123!",
        }
        response = await self.client.post(
            "/auth/profile/change-password",
            headers={"X-Admin-Session": token},
            json=strong_payload,
        )
        self.assertEqual(response.status_code, 204)

        old_login = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "Operator123!",
                "remember_me": False,
            },
        )
        self.assertEqual(old_login.status_code, 401)

        new_login = await self.client.post(
            "/auth/login",
            json={
                "email": "operator@example.com",
                "password": "NoweHaslo123!",
                "remember_me": False,
            },
        )
        self.assertEqual(new_login.status_code, 200)

    async def test_operator_cannot_manage_users(self):
        token, _ = await self._login_operator()
        response = await self.client.get(
            "/admin/users",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

    async def test_operator_can_generate_form_link_and_list_requests(self):
        token, _ = await self._login_operator()
        custom_expiry = date.today() + timedelta(days=10)
        payload = {
            "customer_name": "Klient Testowy",
            "customer_email": "klient.testowy@example.com",
            "customer_phone": "+48 600 700 800",
            "expires_on": custom_expiry.isoformat(),
        }
        response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("/formularz/", body["form_url"])
        self.assertEqual(body["item"]["status"], "DISPATCHED")
        self.assertTrue(body["item"]["created_by_name"])
        self.assertTrue(body["sms_queued"])
        self.assertFalse(body["email_sent"])
        self.assertGreaterEqual(len(body["warnings"]), 1)
        self.assertEqual(
            datetime.fromisoformat(body["item"]["token_expires_at"]).date(),
            custom_expiry,
        )

        response_default = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient Domyślny",
                "customer_email": "domyslny@example.com",
                "customer_phone": "+48 600 800 900",
            },
        )
        self.assertEqual(response_default.status_code, 201)
        expires_default = datetime.fromisoformat(
            response_default.json()["item"]["token_expires_at"]
        )
        delta_default = expires_default - datetime.now(UTC)
        self.assertGreater(delta_default, timedelta(days=6, hours=20))
        self.assertLess(delta_default, timedelta(days=7, hours=4))

        response = await self.client.get(
            "/admin/forms",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertGreaterEqual(len(items), 2)
        emails = {item["customer_email"] for item in items}
        self.assertIn("klient.testowy@example.com", emails)
        self.assertIn("domyslny@example.com", emails)
        self.assertTrue(all(item["created_by_name"] for item in items[:2]))

        async with self.session_factory() as session:
            forms = (await session.execute(select(FormRequest))).scalars().all()
            self.assertEqual(len(forms), 2)
            self.assertTrue(forms[0].token_hash)
            self.assertEqual(forms[0].status, "DISPATCHED")
            sms_rows = (
                (
                    await session.execute(
                        select(SmsOut).where(SmsOut.origin == "form_link_generated")
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(sms_rows), 2)
            self.assertEqual(sms_rows[0].dest, "+48600700800")

    async def test_operator_can_list_forms_with_reserved_email_domain(self):
        token, _ = await self._login_operator()
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            legacy_form = FormRequest(
                created_at=now,
                updated_at=now,
                created_by=1,
                customer_name="Klient Legacy",
                customer_email="flow-4x-4@test.local",
                customer_phone="+48600111222",
                status="DISPATCHED",
                token_hash=f"legacy-{int(now.timestamp() * 1_000_000)}",
                token_expires_at=now + timedelta(days=7),
                sms_status="QUEUED",
                email_status=None,
                ms_status="Automat MS: powiazano z klientem ID 9001 (09.04.2026 10:00 UTC).",
            )
            session.add(legacy_form)
            await session.commit()
            await session.refresh(legacy_form)

        response = await self.client.get(
            "/admin/forms",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        legacy_row = next((item for item in items if item["id"] == legacy_form.id), None)
        self.assertIsNotNone(legacy_row)
        assert legacy_row is not None
        self.assertEqual(legacy_row["customer_email"], "flow-4x-4@test.local")
        self.assertEqual(
            legacy_row["ms_status"],
            "Automat MS: powiazano z klientem ID 9001 (09.04.2026 10:00 UTC).",
        )

    async def test_operator_cannot_generate_form_with_past_expiry_date(self):
        token, _ = await self._login_operator()
        response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient Przeterminowany",
                "customer_email": "przeterminowany@example.com",
                "customer_phone": "+48 600 000 111",
                "expires_on": (date.today() - timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Data ważności formularza", response.json().get("detail", ""))

    async def test_generator_api_requires_generator_section(self):
        async with self.session_factory() as session:
            user = (await session.execute(select(AdminUser).where(AdminUser.id == 2))).scalar_one()
            await section_permissions.set_user_sections(
                session,
                user_id=user.id,
                role=user.role,
                sections=["operator"],
                updated_by=1,
            )
            await session.commit()

        token, _ = await self._login_operator()
        response = await self.client.get(
            "/admin/forms",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)
        detail = response.json().get("detail", "")
        self.assertIn("generatora formularzy", detail)

    async def test_operator_can_view_form_detail_before_submission(self):
        token, _ = await self._login_operator()
        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient Szczegoly",
                "customer_email": "szczegoly@example.com",
                "customer_phone": "+48 500 600 700",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_id = create_response.json()["item"]["id"]

        detail_response = await self.client.get(
            f"/admin/forms/{form_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(detail_response.status_code, 200)
        body = detail_response.json()
        self.assertEqual(body["item"]["id"], form_id)
        self.assertEqual(body["item"]["status"], "DISPATCHED")
        self.assertIsNone(body["submitted_payload"])
        self.assertIn("nie został jeszcze wypełniony", body["status_message"])
        self.assertFalse(body["data_entered_email"]["sent"])

    async def test_operator_can_send_data_entered_email_once_for_submitted_form(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request(
            customer_name="Firma Mailingowa",
            customer_email="formularz@firma-mailingowa.pl",
            payload={
                "company_name": "Firma Mailingowa Sp. z o.o.",
                "company_nip": "7778889990",
                "company_phone": "+48600900100",
                "company_email": "biuro@firma-mailingowa.pl",
                "billing_email": "faktury@firma-mailingowa.pl",
                "registered_street": "Wiosenna",
                "registered_building_no": "8",
                "registered_apartment_no": "1",
                "registered_postal_code": "60-001",
                "registered_city": "Poznań",
                "correspondence_same_as_registered": True,
                "correspondence_street": "Wiosenna",
                "correspondence_building_no": "8",
                "correspondence_apartment_no": "1",
                "correspondence_postal_code": "60-001",
                "correspondence_city": "Poznań",
                "representatives": [
                    {"first_name": "Alicja", "last_name": "Kowalska"},
                    {"first_name": "Piotr", "last_name": "Nowak"},
                ],
                "consent": True,
            },
            created_by=2,
        )
        async with self.session_factory() as session:
            operator = await session.get(AdminUser, 2)
            self.assertIsNotNone(operator)
            assert operator is not None
            operator.mobile_phone = "+48600111222"
            await session.commit()

        email_settings = EmailDeliverySettings(
            host="smtp.test.local",
            port=587,
            username="smtp-user",
            password="smtp-pass",
            sender_name="CTIP Test",
            sender_address="noreply@test.local",
            use_tls=True,
            use_ssl=False,
        )
        with (
            patch(
                "app.api.routes.admin_forms.admin_users.resolve_email_delivery_settings",
                AsyncMock(return_value=email_settings),
            ),
            patch(
                "app.api.routes.admin_forms.send_smtp_message",
                AsyncMock(return_value=EmailSendResult(True, "Wysłano")),
            ) as send_mock,
        ):
            first_response = await self.client.post(
                f"/admin/forms/{form.id}/notify-data-entered",
                headers={"X-Admin-Session": token},
            )
            second_response = await self.client.post(
                f"/admin/forms/{form.id}/notify-data-entered",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(first_response.status_code, 200)
        first_body = first_response.json()
        self.assertTrue(first_body["ok"])
        self.assertFalse(first_body["already_sent"])
        self.assertEqual(first_body["recipient_email"], "biuro@firma-mailingowa.pl")
        self.assertIsNotNone(first_body["sent_at"])
        self.assertIn("została wysłana", first_body["message"])

        self.assertEqual(second_response.status_code, 200)
        second_body = second_response.json()
        self.assertTrue(second_body["ok"])
        self.assertTrue(second_body["already_sent"])
        self.assertEqual(second_body["recipient_email"], "biuro@firma-mailingowa.pl")
        send_mock.assert_awaited_once()

        sent_message = send_mock.call_args.kwargs["message"]
        self.assertEqual(sent_message["Subject"], "Informacja o dalszych krokach umowy najmu")
        self.assertEqual(sent_message["To"], "biuro@firma-mailingowa.pl")
        content = sent_message.get_content()
        self.assertIn("Firma Mailingowa Sp. z o.o.", content)
        self.assertIn("NIP: 7778889990", content)
        self.assertIn("Alicja Kowalska", content)
        self.assertIn("Piotr Nowak", content)
        self.assertIn("Anna Nowak", content)
        self.assertIn("operator@example.com", content)
        self.assertIn("+48600111222", content)

        detail_response = await self.client.get(
            f"/admin/forms/{form.id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertTrue(detail["data_entered_email"]["sent"])
        self.assertEqual(
            detail["data_entered_email"]["recipient_email"],
            "biuro@firma-mailingowa.pl",
        )

        async with self.session_factory() as session:
            entries = (
                (
                    await session.execute(
                        select(AdminAuditLog).where(
                            AdminAuditLog.action == "form_request_data_entered_email_sent"
                        )
                    )
                )
                .scalars()
                .all()
            )
            matching = [
                entry
                for entry in entries
                if isinstance(entry.payload, dict)
                and entry.payload.get("form_request_id") == form.id
            ]
            self.assertEqual(len(matching), 1)

    async def test_create_form_blocks_client_notifications_when_flag_is_enabled(self):
        token, _ = await self._login_operator()
        settings.block_client_communications = True

        email_settings = EmailDeliverySettings(
            host="smtp.test.local",
            port=587,
            username="smtp-user",
            password="smtp-pass",
            sender_name="CTIP Test",
            sender_address="noreply@test.local",
            use_tls=True,
            use_ssl=False,
        )
        with (
            patch(
                "app.services.form_generator.admin_users.resolve_email_delivery_settings",
                AsyncMock(return_value=email_settings),
            ),
            patch(
                "app.services.form_generator.send_smtp_message",
                AsyncMock(return_value=EmailSendResult(True, "Wysłano")),
            ) as send_mock,
        ):
            response = await self.client.post(
                "/admin/forms",
                headers={"X-Admin-Session": token},
                json={
                    "customer_name": "Klient Blokada",
                    "customer_email": "blokada@example.com",
                    "customer_phone": "+48600700700",
                },
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["sms_queued"], False)
        self.assertEqual(body["email_sent"], False)
        self.assertEqual(body["item"]["status"], "GENERATED")
        self.assertTrue(any("zablokowana" in message for message in body["warnings"]))
        send_mock.assert_not_awaited()

    async def test_submit_form_skips_client_email_when_communications_blocked(self):
        settings.block_client_communications = True
        token, _ = await self._login_operator()

        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient Formularz",
                "customer_email": "formularz@blokada.pl",
                "customer_phone": "+48600700800",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_id = create_response.json()["item"]["id"]
        form_url = create_response.json()["form_url"]
        link_token = form_url.rsplit("/formularz/", maxsplit=1)[-1]

        with patch(
            "app.services.form_generator.send_smtp_message",
            AsyncMock(return_value=EmailSendResult(True, "Wysłano")),
        ) as send_mock:
            submit_response = await self.client.post(
                f"/formularz/{link_token}",
                data=self._valid_public_form_post_data(),
            )

        self.assertEqual(submit_response.status_code, 200)
        self.assertIn("formularz został zapisany", submit_response.text.lower())
        send_mock.assert_not_awaited()

        async with self.session_factory() as session:
            sms_rows = (
                (
                    await session.execute(
                        select(SmsOut).where(SmsOut.origin == "form_submission_completed")
                    )
                )
                .scalars()
                .all()
            )
            self.assertFalse(
                any(
                    isinstance(row.meta, dict) and row.meta.get("form_request_id") == form_id
                    for row in sms_rows
                )
            )

    async def test_notify_data_entered_endpoint_is_blocked(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request(
            customer_name="Firma Blokada",
            customer_email="blokada2@example.com",
            payload={
                "company_name": "Firma Blokada Sp. z o.o.",
                "company_nip": "7778889999",
                "company_phone": "+48600900100",
                "company_email": "biuro@firma-blokada.pl",
                "billing_email": "faktury@firma-blokada.pl",
                "registered_street": "Wyporza",
                "registered_building_no": "1",
                "registered_apartment_no": "1",
                "registered_postal_code": "60-001",
                "registered_city": "Poznań",
                "correspondence_same_as_registered": True,
                "correspondence_street": "Wyporza",
                "correspondence_building_no": "1",
                "correspondence_apartment_no": "1",
                "correspondence_postal_code": "60-001",
                "correspondence_city": "Poznań",
                "representatives": [],
                "consent": True,
            },
            created_by=2,
        )
        settings.block_client_communications = True

        response = await self.client.post(
            f"/admin/forms/{form.id}/notify-data-entered",
            headers={"X-Admin-Session": token},
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("zablokowana", response.json().get("detail", ""))

    async def test_notify_data_entered_requires_submitted_form(self):
        token, _ = await self._login_operator()
        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient Bez Danych",
                "customer_email": "bezdanych@example.com",
                "customer_phone": "+48 600 222 333",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_id = create_response.json()["item"]["id"]

        response = await self.client.post(
            f"/admin/forms/{form_id}/notify-data-entered",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("dopiero po wypełnieniu", response.json().get("detail", ""))

    async def test_operator_can_delete_form_request(self):
        token, _ = await self._login_operator()
        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient Usuniecie",
                "customer_email": "usuniecie@example.com",
                "customer_phone": "+48 510 610 710",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_id = create_response.json()["item"]["id"]

        delete_response = await self.client.delete(
            f"/admin/forms/{form_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(delete_response.status_code, 204)

        list_response = await self.client.get(
            "/admin/forms",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(list_response.status_code, 200)
        ids = {item["id"] for item in list_response.json()["items"]}
        self.assertNotIn(form_id, ids)

        async with self.session_factory() as session:
            self.assertIsNone(await session.get(FormRequest, form_id))
            audit_entries = (
                (
                    await session.execute(
                        select(AdminAuditLog).where(AdminAuditLog.action == "form_request_delete")
                    )
                )
                .scalars()
                .all()
            )
            self.assertTrue(
                any(
                    entry.payload and entry.payload.get("deleted_form_request_id") == form_id
                    for entry in audit_entries
                )
            )

    async def test_public_form_submission_updates_status_and_encrypts_payload(self):
        token, _ = await self._login()
        async with self.session_factory() as session:
            admin = await session.get(AdminUser, 1)
            self.assertIsNotNone(admin)
            assert admin is not None
            admin.mobile_phone = "+48600700800"
            admin.is_salesperson = True
            operator = await session.get(AdminUser, 2)
            self.assertIsNotNone(operator)
            assert operator is not None
            operator.mobile_phone = "+48600800800"
            operator.is_salesperson = True
            await session.commit()

        config_response = await self.client.put(
            "/admin/config/form-handling",
            headers={"X-Admin-Session": token},
            json={
                "public_base_url": "https://form.example.com",
                "invite_sms_template": "Link: {form_url}",
                "invite_email_subject": "Formularz dla {customer_name}",
                "invite_email_body": "Adres: {form_url}",
                "submission_email_subject": "Potwierdzenie dla {company_name}",
                "submission_email_body": "Firma {company_name} zostala zapisana przez {sender_name}.",
                "owner_sms_template": "Operator: klient {company_name} wypelnil formularz.",
            },
        )
        self.assertEqual(config_response.status_code, 200)

        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient Publiczny",
                "customer_email": "publiczny@example.com",
                "customer_phone": "+48 601 602 603",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_id = create_response.json()["item"]["id"]
        form_url = create_response.json()["form_url"]
        link_token = form_url.rsplit("/formularz/", maxsplit=1)[-1]
        self.assertTrue(link_token)

        history_response = await self.client.get(
            "/admin/sms/history?limit=10",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(history_response.status_code, 200)
        history_items = history_response.json()["items"]
        self.assertTrue(
            any(
                item["origin"] == "form_link_generated" and "Treść ukryta" in item["text"]
                for item in history_items
            )
        )

        page_response = await self.client.get(f"/formularz/{link_token}")
        self.assertEqual(page_response.status_code, 200)
        self.assertIn("Bezpieczny formularz klienta", page_response.text)

        reps = [
            {
                "first_name": "Jan",
                "last_name": "Kowalski",
                "representative_email": "jan.kowalski@example.com",
                "representative_phone": "+48600100100",
                "pesel": "85010112345",
                "birth_date": "01:01:1985",
                "document_type": "Dowód osobisty",
                "document_number": "ABC123456",
                "document_issue_date": "01:01:2020",
                "document_expiry_date": "01:01:2030",
            },
            {
                "first_name": "Anna",
                "last_name": "Nowak",
                "representative_email": "anna.nowak@example.com",
                "representative_phone": "+48600200200",
                "pesel": "02270803624",
                "birth_date": "08:07:2002",
                "document_type": "Paszport",
                "document_number": "PZ998877",
                "document_issue_date": "02:05:2019",
                "document_expiry_date": "01:05:2029",
            },
        ]
        email_settings = EmailDeliverySettings(
            host="smtp.test.local",
            port=587,
            username="smtp-user",
            password="smtp-pass",
            sender_name="CTIP Test",
            sender_address="noreply@test.local",
            use_tls=True,
            use_ssl=False,
        )
        with patch(
            "app.services.form_generator.admin_users.resolve_email_delivery_settings",
            AsyncMock(return_value=email_settings),
        ):
            with patch(
                "app.services.form_generator.send_smtp_message",
                AsyncMock(return_value=EmailSendResult(True, "Wysłano")),
            ) as send_mock:
                submit_response = await self.client.post(
                    f"/formularz/{link_token}",
                    data={
                        "company_name": "Firma Publiczna Sp. z o.o.",
                        "company_nip": "5250000000",
                        "company_phone": "+48601602603",
                        "company_email": "publiczny@example.com",
                        "billing_email": "faktury@example.com",
                        "registered_street": "Testowa",
                        "registered_building_no": "1",
                        "registered_apartment_no": "2",
                        "registered_postal_code": "00-001",
                        "registered_city": "Warszawa",
                        "correspondence_street": "Korespondencyjna",
                        "correspondence_building_no": "2",
                        "correspondence_apartment_no": "3",
                        "correspondence_postal_code": "00-002",
                        "correspondence_city": "Warszawa",
                        "representatives_json": json.dumps(reps),
                        "consent": "true",
                        "website": "",
                    },
                )
                send_mock.assert_awaited_once()
                submission_message = send_mock.call_args.kwargs["message"]
        self.assertEqual(submit_response.status_code, 200)
        self.assertIn("formularz został zapisany", submit_response.text.lower())
        self.assertEqual(
            submission_message["Subject"], "Potwierdzenie dla Firma Publiczna Sp. z o.o."
        )
        self.assertIn(
            "Firma Firma Publiczna Sp. z o.o. zostala zapisana przez CTIP Test.",
            submission_message.get_content(),
        )

        list_response = await self.client.get(
            "/admin/forms",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(list_response.status_code, 200)
        statuses = [item["status"] for item in list_response.json()["items"]]
        self.assertIn("SUBMITTED", statuses)

        detail_response = await self.client.get(
            f"/admin/forms/{form_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["item"]["status"], "SUBMITTED")
        self.assertIn("wypełniony", detail["status_message"].lower())
        self.assertEqual(detail["submitted_payload"]["company_name"], "Firma Publiczna Sp. z o.o.")
        self.assertEqual(detail["submitted_payload"]["company_email"], "publiczny@example.com")
        self.assertEqual(len(detail["submitted_payload"]["representatives"]), 2)
        self.assertEqual(
            detail["submitted_payload"]["representatives"][0]["representative_email"],
            "jan.kowalski@example.com",
        )
        self.assertEqual(
            detail["submitted_payload"]["representatives"][0]["representative_phone"],
            "+48600100100",
        )
        self.assertEqual(
            detail["submitted_payload"]["representatives"][1]["representative_email"],
            "anna.nowak@example.com",
        )
        self.assertEqual(
            detail["submitted_payload"]["representatives"][1]["representative_phone"],
            "+48600200200",
        )

        history_response = await self.client.get(
            "/admin/sms/history?limit=20",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(history_response.status_code, 200)
        history_items = history_response.json()["items"]
        self.assertTrue(
            any(
                item["origin"] == "form_submission_completed" and "Treść ukryta" in item["text"]
                for item in history_items
            )
        )

        async with self.session_factory() as session:
            form_row = (await session.execute(select(FormRequest))).scalars().first()
            self.assertIsNotNone(form_row)
            assert form_row is not None
            self.assertEqual(form_row.status, "SUBMITTED")
            self.assertTrue(form_row.ms_status)
            self.assertIsNotNone(form_row.submitted_payload)
            cipher = Fernet(settings.admin_secret_key.encode("utf-8"))
            decrypted = cipher.decrypt(form_row.submitted_payload.encode("utf-8")).decode("utf-8")
            payload = json.loads(decrypted)
            self.assertEqual(payload["payload"]["company_name"], "Firma Publiczna Sp. z o.o.")
            self.assertEqual(payload["payload"]["company_email"], "publiczny@example.com")
            self.assertEqual(len(payload["payload"]["representatives"]), 2)
            sms_rows = (
                (
                    await session.execute(
                        select(SmsOut).where(SmsOut.origin == "form_submission_completed")
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(sms_rows), 2)
            self.assertEqual({row.dest for row in sms_rows}, {"+48600700800", "+48600800800"})
            self.assertTrue(
                all(
                    row.text == "Operator: klient Firma Publiczna Sp. z o.o. wypelnil formularz."
                    for row in sms_rows
                )
            )
            self.assertTrue(
                all(
                    isinstance(row.meta, dict) and row.meta.get("recipient_group") == "salespeople"
                    for row in sms_rows
                )
            )
            self.assertEqual(
                sms_rows[0].text,
                "Operator: klient Firma Publiczna Sp. z o.o. wypelnil formularz.",
            )

    async def test_public_form_validation_errors_are_localized_in_polish(self):
        token, _ = await self._login_operator()
        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Walidacja Formularza",
                "customer_email": "walidacja@example.com",
                "customer_phone": "+48600123456",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_url = create_response.json()["form_url"]
        link_token = form_url.rsplit("/formularz/", maxsplit=1)[-1]

        submit_response = await self.client.post(
            f"/formularz/{link_token}",
            data={
                "company_name": "A",
                "company_nip": "5250000000",
                "company_phone": "+48600111222",
                "company_email": "firma@example.com",
                "billing_email": "faktury@example.com",
                "registered_street": "Testowa",
                "registered_building_no": "1",
                "registered_apartment_no": "",
                "registered_postal_code": "00-001",
                "registered_city": "Poznan",
                "correspondence_same_as_registered": "true",
                "correspondence_street": "Testowa",
                "correspondence_building_no": "1",
                "correspondence_apartment_no": "",
                "correspondence_postal_code": "00-001",
                "correspondence_city": "Poznan",
                "representatives_json": json.dumps(
                    [
                        {
                            "first_name": "A",
                            "last_name": "Nowak",
                            "representative_email": "anna.nowak@example.com",
                            "representative_phone": "+48600111222",
                            "pesel": "02270803624",
                            "birth_date": "08:07:2002",
                            "document_type": "Dowód osobisty",
                            "document_number": "ABC123456",
                            "document_issue_date": "02:05:2019",
                            "document_expiry_date": "01:05:2029",
                        }
                    ]
                ),
                "consent": "true",
                "website": "",
            },
        )

        self.assertEqual(submit_response.status_code, 422)
        self.assertIn("Pole „Nazwa firmy” musi mieć co najmniej 2 znaki.", submit_response.text)
        self.assertIn(
            "Reprezentant 1: pole „Imię” musi mieć co najmniej 2 znaki.",
            submit_response.text,
        )
        self.assertNotIn("String should have at least 2 characters", submit_response.text)

    async def test_public_form_submission_links_existing_firebird_client_by_nip(self):
        token, _ = await self._login()
        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient MS Link",
                "customer_email": "link-ms@example.com",
                "customer_phone": "+48 601 111 222",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_id = create_response.json()["item"]["id"]
        form_url = create_response.json()["form_url"]
        link_token = form_url.rsplit("/formularz/", maxsplit=1)[-1]
        representatives = json.dumps(
            [
                {
                    "first_name": "Jan",
                    "last_name": "Linkowski",
                    "representative_email": "jan.linkowski@example.com",
                    "representative_phone": "+48601111000",
                    "pesel": "85010112345",
                    "birth_date": "01:01:1985",
                    "document_type": "Dowód osobisty",
                    "document_number": "AAA111222",
                    "document_issue_date": "01:01:2020",
                    "document_expiry_date": "01:01:2030",
                }
            ]
        )

        with patch(
            "app.services.form_generator.find_client_in_firebird",
            return_value=FirebirdClientMatch(
                found=True,
                id_klient=4815,
                nazwa="Klient MS Link",
                nip="5250001111",
            ),
        ):
            submit_response = await self.client.post(
                f"/formularz/{link_token}",
                data={
                    "company_name": "Klient MS Link",
                    "company_nip": "5250001111",
                    "company_phone": "+48601111222",
                    "company_email": "link-ms@example.com",
                    "billing_email": "faktury-link@example.com",
                    "registered_street": "Testowa",
                    "registered_building_no": "7",
                    "registered_apartment_no": "",
                    "registered_postal_code": "60-101",
                    "registered_city": "Poznan",
                    "correspondence_same_as_registered": "true",
                    "correspondence_street": "Testowa",
                    "correspondence_building_no": "7",
                    "correspondence_apartment_no": "",
                    "correspondence_postal_code": "60-101",
                    "correspondence_city": "Poznan",
                    "representatives_json": representatives,
                    "consent": "true",
                    "website": "",
                },
            )

        self.assertEqual(submit_response.status_code, 200)

        async with self.session_factory() as session:
            form_row = await session.get(FormRequest, form_id)
            self.assertIsNotNone(form_row)
            assert form_row is not None
            self.assertIn("Automat MS: powiazano z klientem ID 4815", str(form_row.ms_status))
            workflow_case = (
                (
                    await session.execute(
                        select(FormWorkflowCase).where(FormWorkflowCase.form_request_id == form_id)
                    )
                )
                .scalars()
                .one()
            )
            self.assertEqual(workflow_case.firebird_client_id, 4815)
            self.assertEqual(workflow_case.firebird_client_status, "linked")
            self.assertEqual(workflow_case.client_mode, "basic_proforma")

    async def test_public_form_submission_creates_firebird_client_when_missing(self):
        token, _ = await self._login()
        create_response = await self.client.post(
            "/admin/forms",
            headers={"X-Admin-Session": token},
            json={
                "customer_name": "Klient MS Create",
                "customer_email": "create-ms@example.com",
                "customer_phone": "+48 602 111 333",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        form_id = create_response.json()["item"]["id"]
        form_url = create_response.json()["form_url"]
        link_token = form_url.rsplit("/formularz/", maxsplit=1)[-1]
        representatives = json.dumps(
            [
                {
                    "first_name": "Anna",
                    "last_name": "Nowak",
                    "representative_email": "anna.nowak@example.com",
                    "representative_phone": "+48602111000",
                    "pesel": "02270803624",
                    "birth_date": "08:07:2002",
                    "document_type": "Paszport",
                    "document_number": "PZ998877",
                    "document_issue_date": "02:05:2019",
                    "document_expiry_date": "01:05:2029",
                }
            ]
        )
        result = FirebirdClientWriteResult(
            created=True,
            match=FirebirdClientMatch(
                found=True,
                id_klient=6321,
                nazwa="Klient MS Create",
                nip="5250002222",
            ),
        )

        with (
            patch(
                "app.services.form_generator.find_client_in_firebird",
                return_value=FirebirdClientMatch(found=False),
            ),
            patch(
                "app.services.form_generator.firebird_writes_enabled",
                return_value=(True, None),
            ),
            patch(
                "app.services.form_generator.create_client_from_submitted_payload",
                return_value=result,
            ),
        ):
            submit_response = await self.client.post(
                f"/formularz/{link_token}",
                data={
                    "company_name": "Klient MS Create",
                    "company_nip": "5250002222",
                    "company_phone": "+48602111333",
                    "company_email": "create-ms@example.com",
                    "billing_email": "faktury-create@example.com",
                    "registered_street": "Fabryczna",
                    "registered_building_no": "12",
                    "registered_apartment_no": "4",
                    "registered_postal_code": "61-001",
                    "registered_city": "Poznan",
                    "correspondence_same_as_registered": "true",
                    "correspondence_street": "Fabryczna",
                    "correspondence_building_no": "12",
                    "correspondence_apartment_no": "4",
                    "correspondence_postal_code": "61-001",
                    "correspondence_city": "Poznan",
                    "representatives_json": representatives,
                    "consent": "true",
                    "website": "",
                },
            )

        self.assertEqual(submit_response.status_code, 200)

        async with self.session_factory() as session:
            form_row = await session.get(FormRequest, form_id)
            self.assertIsNotNone(form_row)
            assert form_row is not None
            self.assertIn("Automat MS: dodano klienta ID 6321", str(form_row.ms_status))
            workflow_case = (
                (
                    await session.execute(
                        select(FormWorkflowCase).where(FormWorkflowCase.form_request_id == form_id)
                    )
                )
                .scalars()
                .one()
            )
            self.assertEqual(workflow_case.firebird_client_id, 6321)
            self.assertEqual(workflow_case.firebird_client_status, "created")
            self.assertEqual(workflow_case.client_mode, "basic_proforma")

    async def test_contracts_dashboard_returns_all_forms_for_flow(self):
        token, _ = await self._login_operator()
        submitted = await self._create_submitted_form_request(
            customer_name="Klient Wypelniony",
            customer_email="wypelniony@test.local",
            customer_phone="+48600101010",
        )
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            dispatched = FormRequest(
                created_at=now,
                updated_at=now,
                created_by=1,
                customer_name="Klient Wyslany",
                customer_email="wyslany@test.local",
                customer_phone="+48600202020",
                status="DISPATCHED",
                token_hash=f"dispatched-{now.timestamp()}",
                token_expires_at=now + timedelta(days=7),
                sms_status="QUEUED",
                email_status="SENT",
            )
            session.add(dispatched)
            await session.commit()
            await session.refresh(dispatched)

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
                "/admin/contracts/dashboard?forms_scope=all",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["forms_scope"], "all")
        self.assertIn("mailbox_sync", body)
        self.assertFalse(body["mailbox_sync"]["available"])
        self.assertEqual(body["forms_status_totals"]["DISPATCHED"], 1)
        self.assertEqual(body["forms_status_totals"]["SUBMITTED"], 1)
        forms_by_id = {item["id"]: item for item in body["forms"]}
        self.assertIn(submitted.id, forms_by_id)
        self.assertIn(dispatched.id, forms_by_id)
        self.assertEqual(forms_by_id[submitted.id]["status"], "SUBMITTED")
        self.assertEqual(forms_by_id[submitted.id]["contract_action"], "utworz_klienta")
        self.assertEqual(forms_by_id[dispatched.id]["status"], "DISPATCHED")
        self.assertIsNone(forms_by_id[dispatched.id]["contract_action"])
        self.assertEqual(forms_by_id[dispatched.id]["customer_name"], "Klient Wyslany")

    async def test_contracts_dashboard_returns_last_mailbox_sync_summary(self):
        token, _ = await self._login_operator()
        await self._create_submitted_form_request(
            customer_name="Klient Mailbox",
            customer_email="mailbox@test.local",
            customer_phone="+48600404040",
        )
        finished_at = datetime(2026, 4, 29, 8, 4, 5, tzinfo=UTC)
        started_at = finished_at - timedelta(seconds=9)
        async with self.session_factory() as session:
            session.add(
                AdminAuditLog(
                    created_at=finished_at,
                    user_id=2,
                    action="contracts_mailbox_sync_scheduler",
                    payload={
                        "result": "ok",
                        "started_at": started_at.isoformat(),
                        "finished_at": finished_at.isoformat(),
                        "exit_code": 0,
                        "summary": {
                            "analysed": 19,
                            "updated": 7,
                            "skipped_state": 0,
                            "warnings": 14,
                            "unknown_subjects": 2,
                            "unmatched_forms": 12,
                            "ambiguous_matches": 0,
                            "unresolved_open": 14,
                        },
                    },
                    client_ip="scheduler",
                )
            )
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
                "/admin/contracts/dashboard?forms_scope=all&include_devices=0",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        sync = body["mailbox_sync"]
        self.assertTrue(sync["available"])
        self.assertEqual(sync["source"], "scheduler")
        self.assertEqual(sync["result"], "ok")
        self.assertEqual(sync["last_run_at"], finished_at.isoformat())
        self.assertEqual(sync["started_at"], started_at.isoformat())
        self.assertEqual(sync["finished_at"], finished_at.isoformat())
        self.assertEqual(sync["exit_code"], 0)
        self.assertEqual(sync["summary"]["updated"], 7)
        self.assertEqual(sync["summary"]["warnings"], 14)

    async def test_contracts_dashboard_skips_devices_when_include_devices_disabled(self):
        token, _ = await self._login_operator()
        await self._create_submitted_form_request(
            customer_name="Klient Bez Urzadzen",
            customer_email="bez-urzadzen@test.local",
            customer_phone="+48600303030",
        )

        with (
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                side_effect=AssertionError(
                    "load_available_devices_from_firebird_warehouse nie powinno byc wywolane"
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.find_client_in_firebird",
                return_value=FirebirdClientMatch(found=False),
            ),
        ):
            response = await self.client.get(
                "/admin/contracts/dashboard?forms_scope=all&include_devices=0",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["forms_scope"], "all")
        self.assertEqual(body["devices_total"], 0)
        self.assertEqual(body["devices_matched"], 0)
        self.assertEqual(body["devices"], [])

    async def test_device_dashboard_returns_recent_intakes_and_model_quality(self):
        token, _ = await self._login_device_operator()
        payload = {
            "summary": {
                "pz_count": 1,
                "device_rows": 1,
                "serial_linked_rows": 1,
                "machine_linked_rows": 1,
                "ready_rows": 0,
                "critical_rows": 0,
                "warn_rows": 1,
            },
            "recent_intakes": [
                {
                    "pz_id": 35962,
                    "pz_number": "PZ / 74 / 2026",
                    "process_status": {
                        "code": "machine_alignment",
                        "severity": "warn",
                        "label": "Uzupelnij MASZYNA",
                        "detail": "Karta MASZYNA wymaga domkniecia.",
                    },
                    "next_actions": ["Jako numer wew przyjmij `KP/test1` z `SERIAL.EWIDENCJA`."],
                    "internal_number": {
                        "recommended": "KP/test1",
                        "source": "SERIAL.EWIDENCJA",
                        "purchase": "KP/test1",
                        "serial": "KP/test1",
                        "machine": "KP/test1v",
                        "consistent": False,
                    },
                    "issue_summary": {"highest_severity": "warn"},
                    "issues": [
                        {
                            "code": "MACHINE_MODEL_EMPTY",
                            "severity": "warn",
                            "message": "MASZYNA nie przejela ID_MODEL.",
                        }
                    ],
                }
            ],
            "model_quality": {
                "total": 650,
                "duplicate_signatures_count": 30,
                "missing_grupa_count": 0,
                "missing_rodzaj_count": 127,
                "missing_kolor_count": 178,
                "missing_plik_count": 437,
                "top_duplicate_signatures": [
                    {
                        "signature": "RICOH|IMC 300",
                        "count": 2,
                        "marka": "Ricoh",
                        "model": "IMC 300",
                        "id_models": [501, 777],
                    }
                ],
            },
            "process_rules": ["Przy pozycji seryjnej numer wew traktuj z `SERIAL.EWIDENCJA`."],
            "operational_notes": [
                "Przy PZ z wlaczonymi numerami seryjnymi numer wew trafia do SERIAL.EWIDENCJA."
            ],
        }

        with patch(
            "app.api.routes.admin_device.load_device_dashboard_payload",
            return_value=payload,
        ):
            response = await self.client.get(
                "/admin/device/dashboard",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["pz_count"], 1)
        self.assertEqual(body["recent_intakes"][0]["pz_number"], "PZ / 74 / 2026")
        self.assertEqual(body["recent_intakes"][0]["process_status"]["code"], "machine_alignment")
        self.assertEqual(body["recent_intakes"][0]["internal_number"]["recommended"], "KP/test1")
        self.assertEqual(body["model_quality"]["duplicate_signatures_count"], 30)
        self.assertEqual(len(body["process_rules"]), 1)
        self.assertEqual(len(body["operational_notes"]), 1)

    async def test_device_intake_defaults_returns_next_ewidencja(self):
        token, _ = await self._login_device_operator()
        with patch(
            "app.api.routes.admin_device.get_next_ewidencja_suggestion",
            return_value={
                "prefix": "KP/",
                "next_number": 5074,
                "width": 4,
                "suggested": "KP/5074",
            },
        ):
            response = await self.client.get(
                "/admin/device/intake/defaults?ewidencja_prefix=KP/",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["defaults"]["suggested"], "KP/5074")

    async def test_device_models_lookup_returns_rows(self):
        token, _ = await self._login_device_operator()
        with patch(
            "app.api.routes.admin_device.search_device_models",
            return_value=[
                {
                    "id_model": 33,
                    "marka": "Canon",
                    "model": "IR 2520",
                    "grupa": "Druk",
                    "rodzaj": "MFP",
                    "kolor": "NIE",
                    "plik": "",
                    "auto_item_id": 18170,
                }
            ],
        ):
            response = await self.client.get(
                "/admin/device/models?query=Canon&limit=20",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["rows"]), 1)
        self.assertEqual(body["rows"][0]["id_model"], 33)

    async def test_device_model_form_options_returns_brand_group_kind_lists(self):
        token, _ = await self._login_device_operator()
        with (
            patch(
                "app.api.routes.admin_device._get_or_seed_device_brands",
                new=AsyncMock(return_value=["Ricoh", "Canon"]),
            ),
            patch(
                "app.api.routes.admin_device.load_device_model_taxonomy",
                return_value={"groups": ["Druk"], "kinds": ["MFP A3"]},
            ),
        ):
            response = await self.client.get(
                "/admin/device/model-form-options",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["options"]["default_brand"], "Ricoh")
        self.assertEqual(body["options"]["brands"], ["Ricoh", "Canon"])
        self.assertEqual(body["options"]["groups"], ["Druk"])
        self.assertEqual(body["options"]["kinds"], ["MFP A3"])

    async def test_device_suppliers_lookup_returns_rows(self):
        token, _ = await self._login_device_operator()
        with patch(
            "app.api.routes.admin_device.search_device_suppliers",
            return_value=[
                {
                    "id_klient": 656,
                    "nazwa": "CTIP TEST",
                    "nip": "9000000656",
                    "adres": "Testowa 1",
                    "kod": "60-001",
                    "poczta": "Poznan",
                    "telefon": "",
                    "email": "",
                }
            ],
        ):
            response = await self.client.get(
                "/admin/device/suppliers?query=9000&limit=20",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["rows"][0]["id_klient"], 656)

    async def test_device_supplier_create_executes_when_enabled(self):
        token, _ = await self._login_device_operator()
        firebird_user = FirebirdMsUserOption(
            id=208,
            login_user="OperatorMS",
            workstation="TEST",
            app_name="CONFIG",
        )
        with (
            patch(
                "app.api.routes.admin_device._ensure_device_writer",
                new=AsyncMock(return_value=firebird_user),
            ),
            patch(
                "app.api.routes.admin_device._ensure_firebird_write_enabled",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_device.create_device_supplier",
                return_value={
                    "id_klient": 2900,
                    "nazwa": "NOWY DOSTAWCA",
                    "nip": "9000002900",
                    "adres": "Testowa 2",
                    "kod": "60-002",
                    "poczta": "Poznan",
                },
            ),
        ):
            response = await self.client.post(
                "/admin/device/suppliers",
                headers={"X-Admin-Session": token},
                json={
                    "name": "NOWY DOSTAWCA",
                    "nip": "9000002900",
                    "address": "Testowa 2",
                    "postal_code": "60-002",
                    "city": "Poznan",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["supplier"]["id_klient"], 2900)

    async def test_device_model_create_executes_when_enabled(self):
        token, _ = await self._login_device_operator()
        firebird_user = FirebirdMsUserOption(
            id=208,
            login_user="OperatorMS",
            workstation="TEST",
            app_name="CONFIG",
        )
        with (
            patch(
                "app.api.routes.admin_device._ensure_device_writer",
                new=AsyncMock(return_value=firebird_user),
            ),
            patch(
                "app.api.routes.admin_device._ensure_firebird_write_enabled",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_device.create_device_model",
                return_value={
                    "created": True,
                    "id_model": 30003000,
                    "marka": "Canon",
                    "model": "IR TEST",
                    "catalog": {
                        "action": "created",
                        "warehouse_item_id": 20001,
                        "index": "AUTO/2001",
                    },
                },
            ),
        ):
            response = await self.client.post(
                "/admin/device/models",
                headers={"X-Admin-Session": token},
                json={
                    "marka": "Canon",
                    "model": "IR TEST",
                    "grupa": "Druk",
                    "rodzaj": "MFP",
                    "kolor": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["model"]["id_model"], 30003000)

    async def test_device_catalog_sync_is_permanently_disabled(self):
        token, _ = await self._login_device_operator()
        response = await self.client.post(
            "/admin/device/catalog/sync",
            headers={"X-Admin-Session": token},
            json={"model_ids": [472, 545], "only_missing": False},
        )

        self.assertEqual(response.status_code, 410)
        self.assertIn("Każdy egzemplarz", response.json()["detail"])

    async def test_device_api_requires_explicit_device_permission(self):
        token, _ = await self._login_operator()
        response = await self.client.get(
            "/admin/device/dashboard",
            headers={"X-Admin-Session": token},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Obsługa urządzeń", response.json()["detail"])

    async def test_device_write_requires_firebird_user_mapping(self):
        token, _ = await self._login_device_operator()
        response = await self.client.post(
            "/admin/device/models",
            headers={"X-Admin-Session": token},
            json={
                "marka": "Ricoh",
                "model": "IM C TEST",
                "grupa": "Druk",
                "rodzaj": "MFP",
                "kolor": True,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("powiązane z użytkownikiem Menadżera Serwisu", response.json()["detail"])

    async def test_device_intake_create_is_idempotent_and_uses_ms_mapping(self):
        token, _ = await self._login_device_operator()
        idempotency_key = "11111111-1111-4111-8111-111111111111"
        firebird_user = FirebirdMsUserOption(
            id=208,
            login_user="OperatorMS",
            workstation="TEST",
            app_name="CONFIG",
        )
        expected = DeviceIntakeBatchResult(
            pz_id=40111,
            pz_number="PZ / 111 / 2026",
            supplier_id=656,
            items=[
                DeviceIntakeResult(
                    model_id=472,
                    producer="Ricoh",
                    model="MP 401",
                    warehouse_item_id=19001,
                    warehouse_index="KP/DEV/001",
                    pz_id=40111,
                    pz_number="PZ / 111 / 2026",
                    zakpozycja_id=109001,
                    serial_id=None,
                    serial="SN-DEV-001",
                    ewidencja="KP/DEV/001",
                    supplier_id=656,
                    machine_id=7633,
                    machine_table_id=5721,
                    purchase_price_netto=Decimal("1200.00"),
                )
            ],
        )
        request_payload = {
            "idempotency_key": idempotency_key,
            "model_id": 472,
            "serial": "SN-DEV-001",
            "ewidencja": "KP/DEV/001",
            "purchase_price_netto": "1200.00",
            "supplier_id": 656,
            "external_document": "FV/DEV/001",
            "document_date": "2026-07-14",
            "issue_date": "2026-07-13",
            "payment_method": "Gotówka",
            "payment_due_date": "2026-07-27",
            "allow_exception": False,
            "exception_reason": None,
            "ewidencja_prefix": "KP/",
        }

        with (
            patch(
                "app.api.routes.admin_device._ensure_device_writer",
                new=AsyncMock(return_value=firebird_user),
            ),
            patch(
                "app.api.routes.admin_device._ensure_firebird_write_enabled",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_device.create_device_intake_batch",
                return_value=expected,
            ) as intake_mock,
        ):
            first_response = await self.client.post(
                "/admin/device/intake",
                headers={"X-Admin-Session": token},
                json=request_payload,
            )
            replay_response = await self.client.post(
                "/admin/device/intake",
                headers={"X-Admin-Session": token},
                json=request_payload,
            )

        self.assertEqual(first_response.status_code, 200)
        first_body = first_response.json()
        self.assertTrue(first_body["ok"])
        self.assertTrue(first_body["deprecated"])
        self.assertFalse(first_body["replayed"])
        self.assertEqual(first_body["batch"]["pz_id"], 40111)
        self.assertIsNone(first_body["batch"]["items"][0]["serial_id"])
        self.assertEqual(first_body["batch"]["items"][0]["warehouse_item_id"], 19001)

        self.assertEqual(replay_response.status_code, 200)
        self.assertTrue(replay_response.json()["replayed"])
        intake_mock.assert_called_once()
        intake_kwargs = intake_mock.call_args.kwargs
        self.assertEqual(intake_kwargs["issued_by"], "OperatorMS")
        self.assertEqual(intake_kwargs["kto"], "CTIP/OperatorMS")
        self.assertEqual(intake_kwargs["idempotency_key"], idempotency_key)
        self.assertEqual(intake_kwargs["document_date"], date(2026, 7, 14))
        self.assertEqual(intake_kwargs["issue_date"], date(2026, 7, 13))
        self.assertEqual(intake_kwargs["payment_method"], "Gotówka")
        self.assertEqual(intake_kwargs["payment_due_date"], date(2026, 7, 27))
        self.assertEqual(intake_kwargs["items"][0].purchase_price_netto, Decimal("1200.00"))

        async with self.session_factory() as session:
            operations = (await session.execute(select(DeviceIntakeOperation))).scalars().all()
            units = (await session.execute(select(DeviceInventoryUnit))).scalars().all()
            outbox = (await session.execute(select(DeviceSheetOutbox))).scalars().all()
            events = (await session.execute(select(DeviceInventoryEvent))).scalars().all()
            audits = (
                (
                    await session.execute(
                        select(AdminAuditLog).where(
                            AdminAuditLog.action == "device_intake_batch_create"
                        )
                    )
                )
                .scalars()
                .all()
            )

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].status, "completed")
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].serial, "SN-DEV-001")
        self.assertEqual(units[0].firebird_machine_id, 7633)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].operation_type, "upsert_device")
        self.assertEqual(
            outbox[0].payload["notes"],
            "dodana automatem PZ z CTIP",
        )
        self.assertTrue(outbox[0].payload["notes_red"])
        self.assertEqual(
            len([event for event in events if event.event_type == "intake_created"]), 1
        )
        self.assertEqual(len(audits), 1)

    async def test_device_intake_batch_create_executes_when_enabled(self):
        token, _ = await self._login_device_operator()
        firebird_user = FirebirdMsUserOption(
            id=208,
            login_user="OperatorMS",
            workstation="TEST",
            app_name="CONFIG",
        )
        expected = DeviceIntakeBatchResult(
            pz_id=40222,
            pz_number="PZ / 222 / 2026",
            supplier_id=656,
            items=[
                DeviceIntakeResult(
                    model_id=472,
                    producer="Ricoh",
                    model="MP 401",
                    warehouse_item_id=19001,
                    warehouse_index="KP/BATCH/001",
                    pz_id=40222,
                    pz_number="PZ / 222 / 2026",
                    zakpozycja_id=109101,
                    serial_id=None,
                    serial="SN-BATCH-001",
                    ewidencja="KP/BATCH/001",
                    supplier_id=656,
                    machine_id=7634,
                    machine_table_id=5722,
                    purchase_price_netto=Decimal("1000.00"),
                ),
                DeviceIntakeResult(
                    model_id=545,
                    producer="Ricoh",
                    model="MPC 307",
                    warehouse_item_id=19002,
                    warehouse_index="KP/BATCH/002",
                    pz_id=40222,
                    pz_number="PZ / 222 / 2026",
                    zakpozycja_id=109102,
                    serial_id=None,
                    serial="SN-BATCH-002",
                    ewidencja="KP/BATCH/002",
                    supplier_id=656,
                    machine_id=7635,
                    machine_table_id=5723,
                    purchase_price_netto=Decimal("2000.00"),
                ),
            ],
        )

        with (
            patch(
                "app.api.routes.admin_device._ensure_device_writer",
                new=AsyncMock(return_value=firebird_user),
            ),
            patch(
                "app.api.routes.admin_device._ensure_firebird_write_enabled",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_device.create_device_intake_batch",
                return_value=expected,
            ) as intake_mock,
        ):
            response = await self.client.post(
                "/admin/device/intake/batch",
                headers={"X-Admin-Session": token},
                json={
                    "idempotency_key": "22222222-2222-4222-8222-222222222222",
                    "items": [
                        {
                            "model_id": 472,
                            "serial": "SN-BATCH-001",
                            "ewidencja": "KP/BATCH/001",
                            "purchase_price_netto": "1000.00",
                        },
                        {
                            "model_id": 545,
                            "serial": "SN-BATCH-002",
                            "ewidencja": "KP/BATCH/002",
                            "purchase_price_netto": "2000.00",
                        },
                    ],
                    "supplier_id": 656,
                    "external_document": "FV/BATCH/001",
                    "document_date": "2026-07-15",
                    "issue_date": "2026-07-15",
                    "payment_method": "Przelew",
                    "payment_due_date": "2026-07-29",
                    "ewidencja_prefix": "KP/BATCH/",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["batch"]["pz_id"], 40222)
        self.assertEqual(len(body["batch"]["items"]), 2)
        self.assertIn("Utworzono przyjęcie", body["message"])

        called_items = intake_mock.call_args.kwargs["items"]
        self.assertEqual(len(called_items), 2)
        self.assertEqual(called_items[0].model_id, 472)
        self.assertEqual(called_items[1].ewidencja, "KP/BATCH/002")
        self.assertEqual(intake_mock.call_args.kwargs["ewidencja_prefix"], "KP/BATCH/")
        self.assertEqual(
            intake_mock.call_args.kwargs["document_date"],
            date(2026, 7, 15),
        )
        self.assertEqual(intake_mock.call_args.kwargs["issue_date"], date(2026, 7, 15))
        self.assertEqual(intake_mock.call_args.kwargs["payment_method"], "Przelew")
        self.assertEqual(
            intake_mock.call_args.kwargs["payment_due_date"],
            date(2026, 7, 29),
        )
        self.assertEqual(intake_mock.call_args.kwargs["issued_by"], "OperatorMS")

        async with self.session_factory() as session:
            entries = (
                (
                    await session.execute(
                        select(AdminAuditLog).where(
                            AdminAuditLog.action == "device_intake_batch_create"
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertTrue(
                any(entry.payload and entry.payload.get("item_count") == 2 for entry in entries)
            )
            units = (await session.execute(select(DeviceInventoryUnit))).scalars().all()
            outbox = (await session.execute(select(DeviceSheetOutbox))).scalars().all()
            self.assertEqual(len(units), 2)
            self.assertEqual(len(outbox), 2)

    async def test_device_intake_exception_requires_checkbox_and_reason(self):
        token, _ = await self._login_device_operator()
        firebird_user = FirebirdMsUserOption(
            id=208,
            login_user="OperatorMS",
            workstation="TEST",
            app_name="CONFIG",
        )
        payload = {
            "idempotency_key": "33333333-3333-4333-8333-333333333333",
            "items": [
                {
                    "model_id": 472,
                    "serial": "SN-ZERO-001",
                    "ewidencja": "KP/ZERO/001",
                    "purchase_price_netto": "0",
                }
            ],
            "supplier_id": 656,
            "external_document": None,
            "allow_exception": False,
            "exception_reason": None,
        }

        with (
            patch(
                "app.api.routes.admin_device._ensure_device_writer",
                new=AsyncMock(return_value=firebird_user),
            ),
            patch(
                "app.api.routes.admin_device._ensure_firebird_write_enabled",
                new=AsyncMock(return_value=None),
            ),
            patch("app.api.routes.admin_device.create_device_intake_batch") as intake_mock,
        ):
            response = await self.client.post(
                "/admin/device/intake/batch",
                headers={"X-Admin-Session": token},
                json=payload,
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("wymaga zaznaczenia wyjątku", response.json()["detail"])
        intake_mock.assert_not_called()

    async def test_device_warehouse_registers_history_only_after_explicit_change(self):
        token, _ = await self._login_device_operator()
        warehouse_row = {
            "row": 19001,
            "source_type": "firebird_magazyn_28",
            "producer": "Ricoh",
            "model": "MP 401",
            "serial": "SN-HISTORY-001",
            "ewidencja": "KP/HISTORY/001",
            "status": "Dostępne",
            "warehouse_quantity": "1",
            "available_quantity": "1",
            "reserved_quantity": "0",
            "price_net": "1500.00",
            "price_gross": "1845.00",
            "purchase_price_net": "1100.00",
            "is_color": True,
            "machine_id": 7701,
            "machine_table_id": 6701,
            "model_id": 472,
        }

        async with self.session_factory() as session:
            session.add(
                WorkflowSheetStatusCache(
                    source_key="firebird_magazyn_28:19001",
                    source_type="firebird_magazyn_28",
                    source_row=19001,
                    sheet_row=21,
                    sheet_status="01. Przed zerówką",
                    sheet_notes="Uwaga historyczna z arkusza.",
                    counter_bw="5678",
                    counter_color="9584",
                    synced_at=datetime.now(UTC),
                )
            )
            await session.commit()

        with patch(
            "app.api.routes.admin_device.load_available_devices_from_firebird_warehouse",
            return_value=[warehouse_row],
        ):
            list_response = await self.client.get(
                "/admin/device/warehouse",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(list_response.status_code, 200)
        warehouse_item = list_response.json()["items"][0]
        self.assertTrue(warehouse_item["audit_only"])
        self.assertTrue(warehouse_item["is_color"])
        self.assertEqual(warehouse_item["counter_bw"], "5678")
        self.assertEqual(warehouse_item["counter_color"], "9584")
        self.assertEqual(warehouse_item["purchase_price_net"], "1100.00")
        self.assertEqual(warehouse_item["note"], "Uwaga historyczna z arkusza.")
        self.assertEqual(
            warehouse_item["source_presence"],
            {
                "sheet": True,
                "warehouse": True,
                "machine": False,
                "ctip": False,
            },
        )
        async with self.session_factory() as session:
            self.assertEqual(
                len((await session.execute(select(DeviceInventoryUnit))).scalars().all()),
                0,
            )

        with patch(
            "app.api.routes.admin_device.load_available_devices_from_firebird_warehouse",
            return_value=[warehouse_row],
        ):
            note_response = await self.client.post(
                "/admin/device/warehouse/19001/notes",
                headers={"X-Admin-Session": token},
                json={"note": "Sprawdzić licznik przed wydaniem."},
            )

        self.assertEqual(note_response.status_code, 200)
        expires_at = (datetime.now(UTC) + timedelta(days=21)).isoformat()
        with patch(
            "app.api.routes.admin_device.load_available_devices_from_firebird_warehouse",
            return_value=[warehouse_row],
        ):
            reservation_response = await self.client.put(
                "/admin/device/warehouse/19001/reservation",
                headers={"X-Admin-Session": token},
                json={
                    "reserved_for": "Klient testowy",
                    "reason": "Rezerwacja do czasu decyzji klienta.",
                    "expires_at": expires_at,
                },
            )

        self.assertEqual(reservation_response.status_code, 200)
        async with self.session_factory() as session:
            units = (await session.execute(select(DeviceInventoryUnit))).scalars().all()
            events = (
                (
                    await session.execute(
                        select(DeviceInventoryEvent).order_by(DeviceInventoryEvent.id)
                    )
                )
                .scalars()
                .all()
            )
            outbox = (
                (await session.execute(select(DeviceSheetOutbox).order_by(DeviceSheetOutbox.id)))
                .scalars()
                .all()
            )
            reservations = (await session.execute(select(DeviceManualReservation))).scalars().all()

        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].source_row, 19001)
        self.assertEqual(
            [event.event_type for event in events],
            ["note_changed", "reservation_created"],
        )
        self.assertEqual(
            [item.operation_type for item in outbox],
            ["update_note", "update_reservation"],
        )
        self.assertEqual(outbox[0].payload["notes"], "Sprawdzić licznik przed wydaniem.")
        self.assertNotIn("notes", outbox[1].payload)
        self.assertEqual(len(reservations), 1)
        self.assertIsNone(reservations[0].released_at)

        with patch(
            "app.api.routes.admin_device.load_available_devices_from_firebird_warehouse",
            return_value=[warehouse_row],
        ):
            detail_response = await self.client.get(
                "/admin/device/warehouse/19001",
                headers={"X-Admin-Session": token},
            )
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(
            detail_response.json()["item"]["note"],
            "Sprawdzić licznik przed wydaniem.",
        )
        self.assertEqual(
            detail_response.json()["item"]["reservation_kind"],
            "manual",
        )

    async def test_device_audit_api_rejects_second_active_run_and_returns_results(self):
        token, _ = await self._login_device_operator()
        first = await self.client.post(
            "/admin/device/audits",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(first.status_code, 202)
        run_id = first.json()["run"]["id"]

        second = await self.client.post(
            "/admin/device/audits",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(second.status_code, 409)

        async with self.session_factory() as session:
            run = await session.get(DeviceAuditRun, run_id)
            run.status = "completed"
            run.phase = "Zakończono"
            run.summary = {
                "total": 1,
                "ok": 0,
                "missing": 0,
                "discrepancy": 1,
                "duplicate": 0,
            }
            run.completed_at = datetime.now(UTC)
            session.add(
                DeviceAuditItem(
                    run_id=run_id,
                    canonical_key="magazyn:16332",
                    producer="Ricoh",
                    model="P502",
                    serial="5381P800615",
                    ewidencja="KP/4617",
                    source_row=16332,
                    sheet_row=60,
                    sheet_present=True,
                    warehouse_present=True,
                    machine_present=False,
                    ctip_present=False,
                    result_status="discrepancy",
                    issue_codes=["MISSING_MACHINE", "MISSING_CTIP", "SERIAL_MISMATCH"],
                    issue_summary="brak kartoteki; rozbieżny numer seryjny",
                    source_details={"sheet": [{"sheet_row": 60}]},
                )
            )
            session.add(
                DeviceAuditItem(
                    run_id=run_id,
                    canonical_key="machine:ORPHAN001",
                    producer="Ricoh",
                    model="MP 2554",
                    serial="ORPHAN001",
                    ewidencja="KP/9999",
                    machine_id=999,
                    sheet_present=False,
                    warehouse_present=False,
                    machine_present=True,
                    ctip_present=False,
                    result_status="missing",
                    issue_codes=["MISSING_SHEET", "MISSING_WAREHOUSE", "MISSING_CTIP"],
                    issue_summary="brak w arkuszu i magazynie",
                    source_details={"machine": [{"machine_id": 999}]},
                )
            )
            await session.commit()

        latest = await self.client.get(
            "/admin/device/audits/latest",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["run"]["id"], run_id)

        detail = await self.client.get(
            f"/admin/device/audits/{run_id}?result=discrepancy&query=5381P800615",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["total"], 1)
        self.assertEqual(detail.json()["source"], "operational")
        self.assertEqual(detail.json()["filtered_summary"]["total"], 1)
        self.assertEqual(
            detail.json()["items"][0]["source_presence"],
            {
                "sheet": True,
                "warehouse": True,
                "machine": False,
                "ctip": False,
            },
        )

        machine_detail = await self.client.get(
            f"/admin/device/audits/{run_id}?source=machine",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(machine_detail.status_code, 200)
        self.assertEqual(machine_detail.json()["total"], 1)
        self.assertEqual(machine_detail.json()["items"][0]["serial"], "ORPHAN001")

        all_detail = await self.client.get(
            f"/admin/device/audits/{run_id}?source=all",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(all_detail.status_code, 200)
        self.assertEqual(all_detail.json()["total"], 2)

    async def test_device_warehouse_matches_historical_sheet_cache_by_serial(self):
        token, _ = await self._login_device_operator()
        warehouse_row = {
            "row": 18478,
            "source_type": "firebird_magazyn_28",
            "producer": "Ricoh",
            "model": "IM 350",
            "serial": "3381P100862",
            "ewidencja": "KP/5147",
            "index": "KP/5147",
            "status": "Dostępne",
            "warehouse_quantity": "1",
            "available_quantity": "1",
            "reserved_quantity": "0",
            "purchase_price_net": "1900.00",
            "is_color": False,
        }

        async with self.session_factory() as session:
            session.add(
                WorkflowSheetStatusCache(
                    source_key=None,
                    source_type="firebird_magazyn_28",
                    source_row=None,
                    sheet_row=99,
                    producer="Ricoh",
                    model="IM 350",
                    serial="3381P100862",
                    device_index="KP/5147",
                    device_index_normalized="KP5147",
                    sheet_status="02. Po zerówce",
                    counter_bw="31788",
                    sheet_notes="imperial",
                    synced_at=datetime.now(UTC),
                )
            )
            await session.commit()

        with patch(
            "app.api.routes.admin_device.load_available_devices_from_firebird_warehouse",
            return_value=[warehouse_row],
        ):
            response = await self.client.get(
                "/admin/device/warehouse",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["source_row"], 18478)
        self.assertEqual(item["zeroing_status"], "02. Po zerówce")
        self.assertEqual(item["counter_bw"], "31788")
        self.assertEqual(item["note"], "imperial")

    async def test_device_warehouse_matches_historical_sheet_cache_by_index(self):
        token, _ = await self._login_device_operator()
        warehouse_row = {
            "row": 18479,
            "source_type": "firebird_magazyn_28",
            "producer": "Ricoh",
            "model": "IM 350",
            "serial": "3381P100863",
            "ewidencja": "KP/5148",
            "index": "KP/5148",
            "status": "Dostępne",
            "warehouse_quantity": "1",
            "available_quantity": "1",
            "reserved_quantity": "0",
            "is_color": False,
        }

        async with self.session_factory() as session:
            session.add(
                WorkflowSheetStatusCache(
                    source_key=None,
                    source_type="firebird_magazyn_28",
                    source_row=None,
                    sheet_row=102,
                    serial=None,
                    device_index="KP / 5148",
                    device_index_normalized="KP5148",
                    sheet_status="01. Przed zerówką",
                    synced_at=datetime.now(UTC),
                )
            )
            await session.commit()

        with patch(
            "app.api.routes.admin_device.load_available_devices_from_firebird_warehouse",
            return_value=[warehouse_row],
        ):
            response = await self.client.get(
                "/admin/device/warehouse",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["source_row"], 18479)
        self.assertEqual(item["zeroing_status"], "01. Przed zerówką")

    async def test_device_warehouse_ignores_ambiguous_historical_cache(self):
        token, _ = await self._login_device_operator()
        warehouse_row = {
            "row": 19002,
            "source_type": "firebird_magazyn_28",
            "producer": "Ricoh",
            "model": "IM 350",
            "serial": "DUPLICATE-SERIAL",
            "ewidencja": "KP/DUPLICATE",
            "index": "KP/DUPLICATE",
            "status": "Dostępne",
            "warehouse_quantity": "1",
            "available_quantity": "1",
            "reserved_quantity": "0",
            "is_color": False,
        }

        async with self.session_factory() as session:
            session.add_all(
                [
                    WorkflowSheetStatusCache(
                        source_key=None,
                        source_type="firebird_magazyn_28",
                        source_row=None,
                        sheet_row=100,
                        serial="DUPLICATE-SERIAL",
                        device_index="KP/DUPLICATE",
                        sheet_status="01. Przed zerówką",
                        synced_at=datetime.now(UTC),
                    ),
                    WorkflowSheetStatusCache(
                        source_key=None,
                        source_type="firebird_magazyn_28",
                        source_row=None,
                        sheet_row=101,
                        serial="DUPLICATE-SERIAL",
                        device_index="KP/DUPLICATE",
                        sheet_status="02. Po zerówce",
                        synced_at=datetime.now(UTC),
                    ),
                ]
            )
            await session.commit()

        with patch(
            "app.api.routes.admin_device.load_available_devices_from_firebird_warehouse",
            return_value=[warehouse_row],
        ):
            response = await self.client.get(
                "/admin/device/warehouse",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["zeroing_status"], "")
        self.assertEqual(item["counter_bw"], "")

    async def test_contracts_action_create_client_rejects_when_firebird_writes_disabled(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        with patch(
            "app.api.routes.admin_contracts.firebird_writes_enabled",
            return_value=(
                False,
                "Zapis do lokalnej Firebird jest zablokowany. Ustaw FB_ALLOW_WRITES=true w srodowisku testowym.",
            ),
        ):
            response = await self.client.post(
                "/admin/contracts/action",
                headers={"X-Admin-Session": token},
                json={
                    "entity": "form",
                    "action": "utworz_klienta",
                    "target_id": form.id,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("FB_ALLOW_WRITES", response.json()["detail"])

    async def test_contracts_action_create_client_executes_write_when_enabled(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request(
            payload={
                "company_name": "Firma Zapis",
                "company_nip": "9000005678",
                "company_phone": "+48600444555",
                "company_email": "firma-zapis@test.local",
                "billing_email": "faktury@test.local",
                "registered_street": "Prosta",
                "registered_building_no": "5",
                "registered_apartment_no": "",
                "registered_postal_code": "60-001",
                "registered_city": "Poznan",
                "correspondence_same_as_registered": True,
                "correspondence_street": "Prosta",
                "correspondence_building_no": "5",
                "correspondence_apartment_no": "",
                "correspondence_postal_code": "60-001",
                "correspondence_city": "Poznan",
                "representatives": [],
                "consent": True,
            }
        )
        expected = FirebirdClientWriteResult(
            created=True,
            match=FirebirdClientMatch(
                found=True,
                id_klient=3210,
                nazwa="Firma Zapis",
                nip="9000005678",
            ),
        )

        with (
            patch(
                "app.api.routes.admin_contracts.firebird_writes_enabled",
                return_value=(True, None),
            ),
            patch(
                "app.api.routes.admin_contracts.create_client_from_submitted_payload",
                return_value=expected,
            ) as create_mock,
        ):
            response = await self.client.post(
                "/admin/contracts/action",
                headers={"X-Admin-Session": token},
                json={
                    "entity": "form",
                    "action": "utworz_klienta",
                    "target_id": form.id,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["created"])
        self.assertEqual(body["id_klient"], 3210)
        self.assertIn("Utworzono klienta", body["message"])
        create_mock.assert_called_once()

        async with self.session_factory() as session:
            form_row = await session.get(FormRequest, form.id)
            self.assertIsNotNone(form_row)
            assert form_row is not None
            self.assertIn("MS: dodano klienta ID 3210", str(form_row.ms_status))
            entries = (
                (
                    await session.execute(
                        select(AdminAuditLog).where(
                            AdminAuditLog.action == "contracts_client_create"
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertTrue(
                any(
                    entry.payload and entry.payload.get("form_request_id") == form.id
                    for entry in entries
                )
            )

    async def test_contracts_action_sync_device_requires_pz_intake(self):
        token, _ = await self._login_operator()
        response = await self.client.post(
            "/admin/contracts/action",
            headers={"X-Admin-Session": token},
            json={
                "entity": "device",
                "action": "synchronizuj",
                "row": 77,
            },
        )

        self.assertEqual(response.status_code, 410)
        self.assertIn("dokumentem PZ", response.json()["detail"])

    async def test_contracts_form_workflow_detail_returns_case_and_selected_devices(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request(
            payload={
                "company_name": "FLOW TEST",
                "company_nip": "6112998877",
                "company_phone": "+48600123456",
                "company_email": "flow@test.local",
                "billing_email": "faktury-flow@test.local",
                "registered_street": "Testowa",
                "registered_building_no": "10",
                "registered_apartment_no": "4",
                "registered_postal_code": "00-010",
                "registered_city": "Poznan",
                "correspondence_same_as_registered": True,
                "representatives": [],
                "consent": True,
            }
        )

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                firebird_client_status="created",
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="google_sheet",
                    source_row=14,
                    producer="Ricoh",
                    model="IM 350",
                    serial="SN-14",
                    ewidencja="KP/14",
                    device_status="01. Przed zerowka",
                    price_net="1544.72",
                    price_gross="1900.00",
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.find_client_in_firebird",
                return_value=FirebirdClientMatch(
                    found=True,
                    id_klient=2897,
                    nazwa="FLOW TEST",
                    nip="6112998877",
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[
                    {
                        "row": "14",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "serial": "",
                        "ewidencja": "KP/14",
                        "index": "KP/14",
                        "name": "Ricoh IM 350",
                        "status": "Dostepne",
                        "price": "1900.00",
                        "price_net": "1544.72",
                        "price_gross": "1900.00",
                        "vat_rate": "23",
                        "reservation": "",
                        "reservation_status": "brak rezerwacji",
                        "description": "Ricoh IM 350",
                        "available_quantity": "1",
                        "reserved_quantity": "0",
                        "warehouse_quantity": "1",
                        "serial_required": "TAK",
                        "source_type": "firebird_magazyn_28",
                    },
                    {
                        "row": "15",
                        "producer": "Ricoh",
                        "model": "IMC 3000",
                        "serial": "",
                        "ewidencja": "KP/15",
                        "index": "KP/15",
                        "name": "Ricoh IMC 3000",
                        "status": "Dostepne",
                        "price": "2500.00",
                        "price_net": "2032.52",
                        "price_gross": "2500.00",
                        "vat_rate": "23",
                        "reservation": "",
                        "reservation_status": "brak rezerwacji",
                        "description": "Ricoh IMC 3000",
                        "available_quantity": "1",
                        "reserved_quantity": "0",
                        "warehouse_quantity": "1",
                        "serial_required": "TAK",
                        "source_type": "firebird_magazyn_28",
                    },
                ],
            ),
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_status_cache_lookup",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "last_sync_at": "2026-04-20T12:00:00+00:00",
                    "last_error": None,
                    "stale": False,
                    "row_count": 0,
                    "refresh_interval_seconds": 900,
                    "by_source_key": {},
                    "by_index": {},
                },
            ),
            patch(
                "app.api.routes.admin_contracts.list_workflow_sheet_assignee_options",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = await self.client.get(
                f"/admin/contracts/forms/{form.id}/workflow",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["workflow"]["firebird_client_id"], 2897)
        self.assertEqual(body["workflow"]["devices_selected_count"], 1)
        self.assertEqual(body["workflow"]["business_status"], "WAITING_SIGNATURE")
        selected = [item for item in body["available_devices"] if item["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["row"], 14)
        self.assertEqual(selected[0]["source_type"], "google_sheet")
        self.assertEqual(selected[0]["source_key"], "google_sheet:14")
        self.assertEqual(selected[0]["price_net"], "1544.72")
        self.assertEqual(selected[0]["price_gross"], "1900.00")
        warehouse_row = next(
            item
            for item in body["available_devices"]
            if item["row"] == 14 and item["source_type"] == "firebird_magazyn_28"
        )
        self.assertFalse(warehouse_row["selected"])
        self.assertTrue(any(item["label"] == "NIP" for item in body["client_preview"]))
        self.assertEqual(body["sales_packet"]["devices"][0]["price_gross"], "1900.00")

    async def test_contracts_mailbox_history_search_and_detail_hide_server_path(self):
        token, _ = await self._login_operator()
        now = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            history_case = ContractsMailboxHistoryCase(
                application_no_raw="173-25234",
                application_no_normalized="17325234",
                title="Decyzja do wniosku 173-25234",
                status="historical_closed",
                source="mailbox_backfill",
                message_count=1,
                first_message_at=now,
                last_message_at=now,
                archived_at=now,
            )
            session.add(history_case)
            await session.flush()
            message = ContractsMailboxMessage(
                message_id="<history-25234@test>",
                mailbox_folder="INBOX",
                processing_status="historical_archived",
                classification="historical_application",
                event_type="decision_for_signature",
                application_no_raw="173-25234",
                application_no_normalized="17325234",
                subject="Decyzja do wniosku 173-25234",
                sender="robot@example.com",
                body_text="Pełna treść decyzji historycznej.",
                received_at=now,
                history_case_id=history_case.id,
                attachment_manifest=[
                    {
                        "original_name": "umowa.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 100,
                        "path": "/etc/passwd",
                    }
                ],
            )
            session.add(message)
            await session.commit()
            history_case_id = history_case.id
            message_id = message.id

        response = await self.client.get(
            "/admin/contracts/mailbox/history?q=173-025234",
            headers={"X-Admin-Session": token},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        response = await self.client.get(
            f"/admin/contracts/mailbox/history/{history_case_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["messages"][0]["body_text"], "Pełna treść decyzji historycznej.")
        self.assertNotIn("path", body["messages"][0]["attachments"][0])
        self.assertNotIn("/etc/passwd", str(body))

        response = await self.client.get(
            f"/admin/contracts/mailbox/messages/{message_id}/attachments/0",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 404)

    async def test_contracts_form_workflow_detail_marks_device_reserved_by_other_form(self):
        token, _ = await self._login_operator()
        current_form = await self._create_submitted_form_request(
            payload={
                "company_name": "FLOW CURRENT",
                "company_nip": "6112000001",
                "company_phone": "+48600111111",
                "company_email": "current@test.local",
                "billing_email": "current-billing@test.local",
                "registered_street": "Biezaca",
                "registered_building_no": "1",
                "registered_apartment_no": "",
                "registered_postal_code": "60-100",
                "registered_city": "Poznan",
                "correspondence_same_as_registered": True,
                "representatives": [],
                "consent": True,
            }
        )
        reserved_form = await self._create_submitted_form_request(
            payload={
                "company_name": "FLOW RESERVED",
                "company_nip": "6112000002",
                "company_phone": "+48600222222",
                "company_email": "reserved@test.local",
                "billing_email": "reserved-billing@test.local",
                "registered_street": "Rezerwacyjna",
                "registered_building_no": "2",
                "registered_apartment_no": "",
                "registered_postal_code": "60-101",
                "registered_city": "Poznan",
                "correspondence_same_as_registered": True,
                "representatives": [],
                "consent": True,
            }
        )

        async with self.session_factory() as session:
            reserved_case = FormWorkflowCase(
                form_request_id=reserved_form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
            )
            session.add(reserved_case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=reserved_case.id,
                    source_type="firebird_magazyn_28",
                    source_row=15,
                    producer="Ricoh",
                    model="IMC 3000",
                    ewidencja="KP/15",
                    price_net="2032.52",
                    price_gross="2500.00",
                    snapshot={
                        "source_type": "firebird_magazyn_28",
                        "row": 15,
                        "sheet_assignee": "Leszek Sprzedaz",
                        "sheet_sync_status": "synced",
                    },
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.find_client_in_firebird",
                return_value=FirebirdClientMatch(found=False),
            ),
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[
                    {
                        "row": "15",
                        "producer": "Ricoh",
                        "model": "IMC 3000",
                        "serial": "",
                        "ewidencja": "KP/15",
                        "index": "KP/15",
                        "name": "Ricoh IMC 3000",
                        "status": "Dostepne",
                        "price": "2500.00",
                        "price_net": "2032.52",
                        "price_gross": "2500.00",
                        "vat_rate": "23",
                        "reservation": "",
                        "reservation_status": "brak rezerwacji",
                        "description": "Ricoh IMC 3000",
                        "available_quantity": "1",
                        "reserved_quantity": "0",
                        "warehouse_quantity": "1",
                        "serial_required": "TAK",
                        "source_type": "firebird_magazyn_28",
                    }
                ],
            ),
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_status_cache_lookup",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "last_sync_at": "2026-04-20T12:00:00+00:00",
                    "last_error": None,
                    "stale": False,
                    "row_count": 1,
                    "refresh_interval_seconds": 900,
                    "by_source_key": {
                        "firebird_magazyn_28:15": {
                            "sheet_row": "5",
                            "status": "02. Po zerówce",
                            "reservation_grenke": "Leszek Sprzedaz",
                            "form_ctip": str(reserved_form.id),
                            "ctip_form_id": str(reserved_form.id),
                            "ctip_workflow_case_id": "1",
                            "business_status_legacy": "Robocza",
                            "ms_id_magazyn_table": "15",
                            "index": "KP/15",
                        }
                    },
                    "by_index": {},
                },
            ),
            patch(
                "app.api.routes.admin_contracts.list_workflow_sheet_assignee_options",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = await self.client.get(
                f"/admin/contracts/forms/{current_form.id}/workflow",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        row = body["available_devices"][0]
        self.assertEqual(row["device_label"], "Ricoh IMC 3000")
        self.assertEqual(row["status"], "02. Po zerówce")
        self.assertEqual(row["reservation_status"], "Zarezerwowana przez LS")
        self.assertEqual(row["reservation_form_id"], reserved_form.id)
        self.assertEqual(row["reservation_initials"], "LS")
        self.assertTrue(row["locked_by_other"])

    async def test_contracts_form_workflow_detail_reads_status_from_local_sheet_cache(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request(
            payload={
                "company_name": "FLOW CACHE",
                "company_nip": "6112000010",
                "company_phone": "+48600999999",
                "company_email": "cache@test.local",
                "billing_email": "cache-billing@test.local",
                "registered_street": "Cache",
                "registered_building_no": "10",
                "registered_apartment_no": "",
                "registered_postal_code": "60-110",
                "registered_city": "Poznan",
                "correspondence_same_as_registered": True,
                "representatives": [],
                "consent": True,
            }
        )
        sync_time = datetime.now(UTC)
        async with self.session_factory() as session:
            session.add(
                WorkflowSheetStatusCache(
                    source_key="firebird_magazyn_28:33",
                    source_type="firebird_magazyn_28",
                    source_row=33,
                    device_index="KP/33",
                    device_index_normalized="KP33",
                    sheet_row=8,
                    sheet_status="02. Po zerowce",
                    reservation_grenke="Anna Nowak",
                    form_ctip="77",
                    ctip_form_id=77,
                    ctip_workflow_case_id=12,
                    business_status_legacy="Robocza",
                    synced_at=sync_time,
                )
            )
            await settings_store.set_namespace(
                session,
                "workflow_sheet_status_cache",
                {
                    "last_sync_at": StoredValue(
                        value=sync_time.isoformat(),
                        is_secret=False,
                    ),
                    "worksheet_title": StoredValue(
                        value="Urzadzenia_magazyn",
                        is_secret=False,
                    ),
                    "row_count": StoredValue(value="1", is_secret=False),
                    "last_error": StoredValue(value="", is_secret=False),
                },
                user_id=1,
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.find_client_in_firebird",
                return_value=FirebirdClientMatch(found=False),
            ),
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[
                    {
                        "row": "33",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "serial": "",
                        "ewidencja": "KP/33",
                        "index": "KP/33",
                        "name": "Ricoh IM 350",
                        "status": "01. Przed zerowka",
                        "price": "2500.00",
                        "price_net": "2032.52",
                        "price_gross": "2500.00",
                        "vat_rate": "23",
                        "reservation": "",
                        "reservation_status": "brak rezerwacji",
                        "description": "Ricoh IM 350",
                        "available_quantity": "1",
                        "reserved_quantity": "0",
                        "warehouse_quantity": "1",
                        "serial_required": "TAK",
                        "source_type": "firebird_magazyn_28",
                    }
                ],
            ),
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_runtime_config",
                AsyncMock(
                    return_value=WorkflowSheetRuntimeConfig(
                        enabled=True,
                        credentials_path="/tmp/google.json",
                        spreadsheet_id="sheet-test",
                        workflow_devices_worksheet="Urzadzenia_magazyn",
                        source="admin",
                    )
                ),
            ),
            patch(
                "app.services.workflow_sheet_status_cache.workflow_sheet_sync_configured",
                return_value=(True, None),
            ),
            patch(
                "app.api.routes.admin_contracts.list_workflow_sheet_assignee_options",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = await self.client.get(
                f"/admin/contracts/forms/{form.id}/workflow",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        row = body["available_devices"][0]
        self.assertEqual(row["status"], "02. Po zerowce")
        self.assertEqual(body["sheet_status_cache"]["row_count"], 1)
        self.assertEqual(body["sheet_status_cache"]["last_sync_at"], sync_time.isoformat())
        self.assertFalse(body["sheet_status_cache"]["stale"])

    async def test_contracts_workflow_sheet_status_refresh_endpoint_updates_local_cache(self):
        token, _ = await self._login_operator()

        with (
            patch(
                "app.services.workflow_sheet_status_cache.ensure_workflow_sheet_status_cache_table",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.services.workflow_sheet_status_cache.load_workflow_sheet_runtime_config",
                AsyncMock(
                    return_value=WorkflowSheetRuntimeConfig(
                        enabled=True,
                        credentials_path="/tmp/google.json",
                        spreadsheet_id="sheet-test",
                        workflow_devices_worksheet="Urzadzenia_magazyn",
                        source="admin",
                    )
                ),
            ),
            patch(
                "app.services.workflow_sheet_status_cache.workflow_sheet_sync_configured",
                return_value=(True, None),
            ),
            patch(
                "app.services.workflow_sheet_status_cache.load_workflow_sheet_devices_lookup",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "by_source_key": {
                        "firebird_magazyn_28:44": {
                            "sheet_row": "11",
                            "status": "01. Przed zerowka",
                            "counter_bw": "1234",
                            "counter_color": "5678",
                            "reservation_grenke": "",
                            "form_ctip": "",
                            "ctip_form_id": "",
                            "ctip_workflow_case_id": "",
                            "business_status_legacy": "",
                            "ms_id_magazyn_table": "44",
                            "index": "KP/44",
                        }
                    },
                    "by_index": {},
                },
            ),
        ):
            response = await self.client.post(
                "/admin/contracts/workflow/sheet-status-refresh",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["refreshed_count"], 1)

        async with self.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(WorkflowSheetStatusCache).order_by(WorkflowSheetStatusCache.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].source_key, "firebird_magazyn_28:44")
            self.assertEqual(rows[0].sheet_status, "01. Przed zerowka")
            self.assertEqual(rows[0].counter_bw, "1234")
            self.assertEqual(rows[0].counter_color, "5678")
            stored = await settings_store.get_namespace(session, "workflow_sheet_status_cache")
            self.assertEqual(stored.get("worksheet_title"), "Urzadzenia_magazyn")
            self.assertEqual(stored.get("row_count"), "1")
            self.assertTrue(stored.get("last_sync_at"))

    async def test_contracts_form_workflow_devices_rejects_rows_reserved_by_other_form(self):
        token, _ = await self._login_operator()
        reserved_form = await self._create_submitted_form_request()
        target_form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            reserved_case = FormWorkflowCase(
                form_request_id=reserved_form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
            )
            session.add(reserved_case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=reserved_case.id,
                    source_type="firebird_magazyn_28",
                    source_row=21,
                    producer="Ricoh",
                    model="IM 350",
                    ewidencja="KP/21",
                    snapshot={
                        "source_type": "firebird_magazyn_28",
                        "row": 21,
                        "sheet_assignee": "Leszek Sprzedaz",
                        "sheet_sync_status": "synced",
                    },
                )
            )
            await session.commit()

        with patch(
            "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
            return_value=[
                {
                    "row": "21",
                    "producer": "Ricoh",
                    "model": "IM 350",
                    "serial": "",
                    "ewidencja": "KP/21",
                    "index": "KP/21",
                    "name": "Ricoh IM 350",
                    "status": "Dostepne",
                    "price": "1900.00",
                    "price_net": "1544.72",
                    "price_gross": "1900.00",
                    "vat_rate": "23",
                    "reservation": "",
                    "reservation_status": "brak rezerwacji",
                    "description": "Ricoh IM 350",
                    "available_quantity": "1",
                    "reserved_quantity": "0",
                    "warehouse_quantity": "1",
                    "serial_required": "TAK",
                    "source_type": "firebird_magazyn_28",
                }
            ],
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{target_form.id}/workflow/devices",
                headers={"X-Admin-Session": token},
                json={
                    "devices": [
                        {
                            "row": 21,
                            "source_type": "firebird_magazyn_28",
                            "price_net": "1544.72",
                            "price_gross": "1900.00",
                        }
                    ]
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn(f"formularz {reserved_form.id}", response.json()["detail"])

    async def test_contracts_form_workflow_devices_rejects_entire_batch_for_foreign_owner(self):
        token, _ = await self._login_operator()
        target_form = await self._create_submitted_form_request()

        with patch(
            "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
            return_value=[
                {
                    "row": "21",
                    "producer": "Ricoh",
                    "model": "IM 350",
                    "serial": "SAFE-21",
                    "ewidencja": "KP/21",
                    "index": "KP/21",
                    "name": "Ricoh IM 350",
                    "status": "Dostepne",
                    "price": "1900.00",
                    "price_net": "1544.72",
                    "price_gross": "1900.00",
                    "vat_rate": "23",
                    "reservation": "",
                    "reservation_status": "brak rezerwacji",
                    "description": "Ricoh IM 350",
                    "available_quantity": "1",
                    "reserved_quantity": "0",
                    "warehouse_quantity": "1",
                    "serial_required": "TAK",
                    "source_type": "firebird_magazyn_28",
                    "machine_match_state": "warehouse",
                    "machine_owner_conflict": False,
                    "machine_id": 701,
                    "machine_client_id": 656,
                },
                {
                    "row": "22",
                    "producer": "Ricoh",
                    "model": "IMC 3000",
                    "serial": "FOREIGN-22",
                    "ewidencja": "KP/22",
                    "index": "KP/22",
                    "name": "Ricoh IMC 3000",
                    "status": "Dostepne",
                    "price": "2500.00",
                    "price_net": "2032.52",
                    "price_gross": "2500.00",
                    "vat_rate": "23",
                    "reservation": "",
                    "reservation_status": "brak rezerwacji",
                    "description": "Ricoh IMC 3000",
                    "available_quantity": "1",
                    "reserved_quantity": "0",
                    "warehouse_quantity": "1",
                    "serial_required": "TAK",
                    "source_type": "firebird_magazyn_28",
                    "machine_match_state": "foreign",
                    "machine_owner_conflict": True,
                    "machine_owner_reason": (
                        "Urządzenie jest przypisane do klienta Inny klient (ID 1001)."
                    ),
                    "machine_id": 702,
                    "machine_client_id": 1001,
                    "machine_client_name": "Inny klient",
                },
            ],
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{target_form.id}/workflow/devices",
                headers={"X-Admin-Session": token},
                json={
                    "devices": [
                        {"row": 21, "source_type": "firebird_magazyn_28"},
                        {"row": 22, "source_type": "firebird_magazyn_28"},
                    ]
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Nie zapisano żadnego urządzenia", response.json()["detail"])
        self.assertIn("Inny klient (ID 1001)", response.json()["detail"])

        async with self.session_factory() as session:
            workflow_case = (
                (
                    await session.execute(
                        select(FormWorkflowCase).where(
                            FormWorkflowCase.form_request_id == target_form.id
                        )
                    )
                )
                .scalars()
                .one_or_none()
            )
            if workflow_case is not None:
                devices = (
                    (
                        await session.execute(
                            select(FormWorkflowDevice).where(
                                FormWorkflowDevice.workflow_case_id == workflow_case.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                self.assertEqual(devices, [])

    async def test_contracts_form_workflow_client_creates_case_and_client(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request(
            payload={
                "company_name": "Nowy FLOW",
                "company_nip": "9000005678",
                "company_phone": "+48600444555",
                "company_email": "nowy-flow@test.local",
                "billing_email": "faktury-nowy@test.local",
                "registered_street": "Prosta",
                "registered_building_no": "5",
                "registered_apartment_no": "",
                "registered_postal_code": "60-001",
                "registered_city": "Poznan",
                "correspondence_same_as_registered": True,
                "representatives": [],
                "consent": True,
            }
        )
        expected = FirebirdClientWriteResult(
            created=True,
            match=FirebirdClientMatch(
                found=True,
                id_klient=3210,
                nazwa="Nowy FLOW",
                nip="9000005678",
            ),
        )

        with (
            patch(
                "app.api.routes.admin_contracts.find_client_in_firebird",
                return_value=FirebirdClientMatch(found=False),
            ),
            patch(
                "app.api.routes.admin_contracts.firebird_writes_enabled",
                return_value=(True, None),
            ),
            patch(
                "app.api.routes.admin_contracts.create_client_from_submitted_payload",
                return_value=expected,
            ),
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/client",
                headers={"X-Admin-Session": token},
                json={"mode": "basic_proforma"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["id_klient"], 3210)
        self.assertTrue(body["created"])
        self.assertEqual(body["workflow"]["firebird_client_id"], 3210)
        self.assertEqual(body["workflow"]["stage"], "CLIENT_READY")

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
            form_row = await session.get(FormRequest, form.id)
            self.assertIsNotNone(form_row)
            assert form_row is not None
            self.assertIn("MS: dodano klienta ID 3210", str(form_row.ms_status))
            self.assertEqual(workflow_case.firebird_client_id, 3210)
            self.assertEqual(workflow_case.client_mode, "basic_proforma")
            self.assertEqual(workflow_case.firebird_client_status, "created")

    async def test_contracts_form_workflow_devices_saves_selected_rows(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        with (
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[
                    {
                        "row": "21",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "serial": "",
                        "ewidencja": "KP/21",
                        "index": "KP/21",
                        "name": "Ricoh IM 350",
                        "status": "Dostepne",
                        "price": "1900.00",
                        "price_net": "1544.72",
                        "price_gross": "1900.00",
                        "vat_rate": "23",
                        "reservation": "",
                        "reservation_status": "brak rezerwacji",
                        "description": "Ricoh IM 350",
                        "available_quantity": "1",
                        "reserved_quantity": "0",
                        "warehouse_quantity": "1",
                        "serial_required": "TAK",
                        "source_type": "firebird_magazyn_28",
                    },
                    {
                        "row": "22",
                        "producer": "Ricoh",
                        "model": "IMC 3000",
                        "serial": "",
                        "ewidencja": "KP/22",
                        "index": "KP/22",
                        "name": "Ricoh IMC 3000",
                        "status": "Dostepne",
                        "price": "2500.00",
                        "price_net": "2032.52",
                        "price_gross": "2500.00",
                        "vat_rate": "23",
                        "reservation": "",
                        "reservation_status": "brak rezerwacji",
                        "description": "Ricoh IMC 3000",
                        "available_quantity": "1",
                        "reserved_quantity": "0",
                        "warehouse_quantity": "1",
                        "serial_required": "TAK",
                        "source_type": "firebird_magazyn_28",
                    },
                ],
            ),
            patch(
                "app.api.routes.admin_contracts.resolve_workflow_sheet_assignee",
                new=AsyncMock(
                    return_value={"id": 17, "login_user": "ls", "label": "Leszek Sprzedaz"}
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_runtime_config",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "synced_count": 2,
                    "rows": [
                        {"source_row": 21, "sheet_row": 11, "action": "updated"},
                        {"source_row": 22, "sheet_row": 12, "action": "updated"},
                    ],
                    "added_headers": [],
                },
            ) as sync_mock,
            patch(
                "app.api.routes.admin_contracts.release_workflow_devices_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "released_count": 0,
                    "rows": [],
                    "added_headers": [],
                },
            ) as release_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/devices",
                headers={"X-Admin-Session": token},
                json={
                    "devices": [
                        {
                            "row": 21,
                            "source_type": "firebird_magazyn_28",
                            "price_net": "1544.72",
                            "price_gross": "1900.00",
                        },
                        {
                            "row": 22,
                            "source_type": "firebird_magazyn_28",
                            "price_net": "2032.52",
                            "price_gross": "2500.00",
                        },
                    ],
                    "sheet_assignee_id": 17,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn(
            "Arkusz zsynchronizowany (2 urzadzen, rezerwacja: Leszek Sprzedaz).", body["message"]
        )
        self.assertEqual(body["selected_rows"], [21, 22])
        self.assertEqual(
            [item["source_key"] for item in body["selected_devices"]],
            ["firebird_magazyn_28:21", "firebird_magazyn_28:22"],
        )
        self.assertEqual(body["workflow"]["devices_selected_count"], 2)
        self.assertEqual(body["sheet_assignee_id"], 17)
        self.assertEqual(body["sheet_assignee_label"], "Leszek Sprzedaz")
        release_mock.assert_not_called()
        sync_mock.assert_called_once()
        _, sync_kwargs = sync_mock.call_args
        self.assertEqual(sync_kwargs["assignee_label"], "Leszek Sprzedaz")
        self.assertEqual(sync_kwargs["proforma_number"], "")
        self.assertEqual(sync_kwargs["form_request_id"], form.id)

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
            devices = (
                (
                    await session.execute(
                        select(FormWorkflowDevice).where(
                            FormWorkflowDevice.workflow_case_id == workflow_case.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(devices), 2)
            self.assertEqual(sorted(device.source_row for device in devices), [21, 22])
            devices_by_row = {device.source_row: device for device in devices}
            self.assertEqual(devices_by_row[21].source_type, "firebird_magazyn_28")
            self.assertEqual(devices_by_row[21].price_net, "1544.72")
            self.assertEqual(devices_by_row[21].price_gross, "1900.00")
            self.assertEqual(devices_by_row[22].price_net, "2032.52")
            self.assertEqual(devices_by_row[22].price_gross, "2500.00")
            self.assertEqual(devices_by_row[21].snapshot["sheet_sync_status"], "synced")
            self.assertEqual(devices_by_row[21].snapshot["sheet_row"], 11)
            self.assertEqual(devices_by_row[21].snapshot["sheet_assignee"], "Leszek Sprzedaz")
            self.assertEqual(devices_by_row[22].snapshot["sheet_row"], 12)

        self.assertEqual(sync_kwargs["workflow_case_id"], workflow_case.id)
        self.assertEqual(sync_kwargs["business_status_label"], "Wypełniony formularz klienta")

    async def test_contracts_form_workflow_devices_approved_order_runs_binding_automation(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            workflow_case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="APPROVED_ORDER",
                firebird_client_id=2897,
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(workflow_case)
            await session.commit()

        def _binding_side_effect(*, workflow_case, devices, actor_label):  # noqa: ANN001
            self.assertEqual(workflow_case.firebird_client_id, 2897)
            self.assertEqual(len(devices), 1)
            device = devices[0]
            self.assertEqual(device.source_row, 21)
            self.assertTrue(actor_label)
            return (
                [
                    WorkflowDeviceBindingItem(
                        workflow_device_id=device.id,
                        source_row=device.source_row,
                        source_type=device.source_type,
                        ok=True,
                        message="Powiązano urządzenie z klientem MS.",
                        producer=device.producer,
                        model=device.model,
                        serial=device.serial,
                        machine_id=12922,
                        current_client_id=2897,
                        current_ewidencja="KP/21/GRENKE",
                    )
                ],
                [],
            )

        with (
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[
                    {
                        "row": "21",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "serial": "RICOH-21",
                        "ewidencja": "KP/21",
                        "index": "KP/21",
                        "name": "Ricoh IM 350",
                        "status": "Dostepne",
                        "price": "1900.00",
                        "price_net": "1544.72",
                        "price_gross": "1900.00",
                        "vat_rate": "23",
                        "reservation": "",
                        "reservation_status": "brak rezerwacji",
                        "description": "Ricoh IM 350",
                        "available_quantity": "1",
                        "reserved_quantity": "0",
                        "warehouse_quantity": "1",
                        "serial_required": "TAK",
                        "source_type": "firebird_magazyn_28",
                    }
                ],
            ),
            patch(
                "app.api.routes.admin_contracts.resolve_workflow_sheet_assignee",
                new=AsyncMock(
                    return_value={"id": 17, "login_user": "ls", "label": "Leszek Sprzedaz"}
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_runtime_config",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_contracts.bind_devices_to_workflow_client",
                side_effect=_binding_side_effect,
            ) as bind_mock,
            patch(
                "app.api.routes.admin_contracts.notify_binding_issues_to_admins",
                new=AsyncMock(),
            ) as notify_mock,
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "synced_count": 1,
                    "rows": [{"source_row": 21, "sheet_row": 11, "action": "updated"}],
                    "added_headers": [],
                },
            ) as sync_mock,
            patch(
                "app.api.routes.admin_contracts.release_workflow_devices_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "released_count": 0,
                    "rows": [],
                    "added_headers": [],
                },
            ) as release_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/devices",
                headers={"X-Admin-Session": token},
                json={
                    "devices": [
                        {
                            "row": 21,
                            "source_type": "firebird_magazyn_28",
                            "price_net": "1544.72",
                            "price_gross": "1900.00",
                        }
                    ],
                    "sheet_assignee_id": 17,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("Powiązano urządzenia z klientem MS (1).", body["message"])
        self.assertEqual(body["workflow"]["service_manager_binding"]["state"], "ok")
        self.assertEqual(body["binding"]["items"][0]["machine_id"], 12922)
        self.assertEqual(body["binding"]["items"][0]["current_ewidencja"], "KP/21/GRENKE")
        bind_mock.assert_called_once()
        notify_mock.assert_not_called()
        release_mock.assert_not_called()
        sync_mock.assert_called_once()
        sync_kwargs = sync_mock.call_args.kwargs
        self.assertEqual(sync_kwargs["devices"][0]["ms_id_maszyna"], 12922)
        self.assertEqual(sync_kwargs["devices"][0]["ewidencja"], "KP/21/GRENKE")

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
            device = (
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
            self.assertEqual(device.firebird_machine_id, 12922)
            self.assertEqual(device.firebird_client_id, 2897)
            self.assertEqual(device.ewidencja, "KP/21/GRENKE")
            self.assertEqual(device.snapshot.get("ms_binding_status"), "ok")
            self.assertEqual(device.snapshot.get("ms_id_maszyna"), 12922)
            self.assertEqual(device.snapshot.get("ms_id_klient"), 2897)

    async def test_contracts_form_workflow_devices_releases_removed_rows_from_sheet(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            workflow_case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
            )
            session.add(workflow_case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=workflow_case.id,
                    source_type="firebird_magazyn_28",
                    source_row=21,
                    producer="Ricoh",
                    model="IM 350",
                    ewidencja="KP/21",
                    price_net="1544.72",
                    price_gross="1900.00",
                    snapshot={
                        "row": 21,
                        "source_type": "firebird_magazyn_28",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "ewidencja": "KP/21",
                        "sheet_row": 11,
                        "sheet_sync_status": "synced",
                    },
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[],
            ),
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_runtime_config",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_contracts.release_workflow_devices_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "released_count": 1,
                    "rows": [{"source_row": 21, "sheet_row": 11, "action": "released"}],
                    "added_headers": [],
                },
            ) as release_mock,
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "synced_count": 0,
                    "rows": [],
                    "added_headers": [],
                },
            ) as sync_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/devices",
                headers={"X-Admin-Session": token},
                json={"devices": []},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("Zwolniono rezerwacje arkusza dla 1 urzadzen.", body["message"])
        release_mock.assert_called_once()
        released_payload = release_mock.call_args.kwargs["devices"]
        self.assertEqual(len(released_payload), 1)
        self.assertEqual(released_payload[0]["source_row"], 21)
        sync_mock.assert_not_called()

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
            devices = (
                (
                    await session.execute(
                        select(FormWorkflowDevice).where(
                            FormWorkflowDevice.workflow_case_id == workflow_case.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(devices, [])

    async def test_contracts_form_workflow_devices_reports_partial_sheet_release(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            workflow_case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
            )
            session.add(workflow_case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=workflow_case.id,
                    source_type="firebird_magazyn_28",
                    source_row=21,
                    producer="Ricoh",
                    model="IM 350",
                    ewidencja="KP/21",
                    price_net="1544.72",
                    price_gross="1900.00",
                    snapshot={
                        "row": 21,
                        "source_type": "firebird_magazyn_28",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "ewidencja": "KP/21",
                        "sheet_row": 11,
                        "sheet_sync_status": "synced",
                    },
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[],
            ),
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_runtime_config",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_contracts.release_workflow_devices_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "released_count": 0,
                    "rows": [],
                    "added_headers": [],
                },
            ) as release_mock,
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "synced_count": 0,
                    "rows": [],
                    "added_headers": [],
                },
            ) as sync_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/devices",
                headers={"X-Admin-Session": token},
                json={"devices": []},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("Usunieto powiazane urzadzenia ze sprawy CTIP.", body["message"])
        self.assertIn(
            "Uwaga: nie udalo sie zwolnic poprzednich rezerwacji arkusza.",
            body["message"],
        )
        self.assertEqual(
            body["sheet_release_warning"],
            "Nie udalo sie zwolnic wszystkich poprzednich rezerwacji arkusza (0/1).",
        )
        release_mock.assert_called_once()
        sync_mock.assert_not_called()

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
            devices = (
                (
                    await session.execute(
                        select(FormWorkflowDevice).where(
                            FormWorkflowDevice.workflow_case_id == workflow_case.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(devices, [])

    async def test_contracts_form_workflow_devices_survives_sheet_sync_non_runtime_error(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        with (
            patch(
                "app.api.routes.admin_contracts.load_available_devices_from_firebird_warehouse",
                return_value=[
                    {
                        "row": "21",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "serial": "",
                        "ewidencja": "KP/21",
                        "index": "KP/21",
                        "name": "Ricoh IM 350",
                        "status": "Dostepne",
                        "price": "1900.00",
                        "price_net": "1544.72",
                        "price_gross": "1900.00",
                        "vat_rate": "23",
                        "reservation": "",
                        "reservation_status": "brak rezerwacji",
                        "description": "Ricoh IM 350",
                        "available_quantity": "1",
                        "reserved_quantity": "0",
                        "warehouse_quantity": "1",
                        "serial_required": "TAK",
                        "source_type": "firebird_magazyn_28",
                    }
                ],
            ),
            patch(
                "app.api.routes.admin_contracts.resolve_workflow_sheet_assignee",
                new=AsyncMock(
                    return_value={"id": 17, "login_user": "ls", "label": "Leszek Sprzedaz"}
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_runtime_config",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                side_effect=ValueError("blad API Google Sheets"),
            ) as sync_mock,
            patch(
                "app.api.routes.admin_contracts.release_workflow_devices_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "released_count": 0,
                    "rows": [],
                    "added_headers": [],
                },
            ) as release_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/devices",
                headers={"X-Admin-Session": token},
                json={
                    "devices": [
                        {
                            "row": 21,
                            "source_type": "firebird_magazyn_28",
                            "price_net": "1544.72",
                            "price_gross": "1900.00",
                        }
                    ],
                    "sheet_assignee_id": 17,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("Wybor urzadzen zapisany po stronie CTIP.", body["message"])
        self.assertIn(
            "Uwaga: nie udalo sie zsynchronizowac arkusza Google. Zapis pozostaje w CTIP, ale nic nie zapisano w arkuszu.",
            body["message"],
        )
        self.assertEqual(body["selected_rows"], [21])
        sync_mock.assert_called_once()
        release_mock.assert_not_called()

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
            devices = (
                (
                    await session.execute(
                        select(FormWorkflowDevice).where(
                            FormWorkflowDevice.workflow_case_id == workflow_case.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0].source_row, 21)
            self.assertEqual(devices[0].snapshot.get("sheet_sync_status"), "error")
            self.assertIn(
                "Google Sheets",
                str(devices[0].snapshot.get("sheet_sync_error") or ""),
            )

    async def test_contracts_form_workflow_status_updates_business_status(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
            )
            session.add(case)
            await session.commit()

        response = await self.client.post(
            f"/admin/contracts/forms/{form.id}/workflow/status",
            headers={"X-Admin-Session": token},
            json={"business_status": "ZEROWKA"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["workflow"]["business_status"], "ZEROWKA")

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
            self.assertEqual(workflow_case.business_status, "ZEROWKA")

    async def test_contracts_form_workflow_status_rental_without_grenke_sets_archive_due(self):
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
            await session.commit()

        before = datetime.now(UTC)
        response = await self.client.post(
            f"/admin/contracts/forms/{form.id}/workflow/status",
            headers={"X-Admin-Session": token},
            json={"business_status": "RENTAL_WITHOUT_GRENKE"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["workflow"]["business_status"], "RENTAL_WITHOUT_GRENKE")

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
            form_row = await session.get(FormRequest, form.id)
            self.assertEqual(workflow_case.business_status, "RENTAL_WITHOUT_GRENKE")
            self.assertIsNone(workflow_case.resources_release_due_at)
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

    async def test_contracts_dashboard_scope_includes_ksero_partner_bucket(self):
        token, _ = await self._login_operator()
        submitted = await self._create_submitted_form_request(
            customer_name="Klient Wynajem bez GRENKE",
            customer_email="wynajem@example.local",
            customer_phone="+48600600600",
        )

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=submitted.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="RENTAL_WITHOUT_GRENKE",
                client_mode="basic_proforma",
            )
            session.add(case)
            submitted_form = await session.get(FormRequest, submitted.id)
            assert submitted_form is not None
            submitted_form.archive_due_at = datetime.now(UTC) + timedelta(days=14)
            submitted_form.archive_bucket = "ksero_partner"
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
                "/admin/contracts/dashboard?forms_scope=all&include_devices=0&archive_scope=ksero_partner",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["forms_scope"], "all")
        self.assertEqual(body["archive_scope"], "ksero_partner")
        self.assertEqual(body["archive_totals"].get("ksero_partner"), 1)
        form_ids = {item["id"] for item in body["forms"]}
        self.assertIn(submitted.id, form_ids)

    async def test_contracts_form_workflow_status_approved_order_runs_binding_automation(self):
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
                firebird_client_id=2897,
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=33,
                    producer="Ricoh",
                    model="IM 350",
                    serial="RICOH-33",
                    ewidencja="KP/33",
                    snapshot={
                        "row": 33,
                        "source_type": "firebird_magazyn_28",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "serial": "RICOH-33",
                        "ewidencja": "KP/33",
                        "index": "KP/33",
                    },
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.bind_devices_to_workflow_client",
                return_value=(
                    [
                        WorkflowDeviceBindingItem(
                            workflow_device_id=1,
                            source_row=33,
                            source_type="firebird_magazyn_28",
                            ok=False,
                            message="Niepoprawny numer EWIDENCJA.",
                            producer="Ricoh",
                            model="IM 350",
                            serial="RICOH-33",
                        )
                    ],
                    ["Niepoprawny numer EWIDENCJA."],
                ),
            ) as bind_mock,
            patch(
                "app.api.routes.admin_contracts.notify_binding_issues_to_admins",
                AsyncMock(
                    return_value={
                        "sent": True,
                        "sms_queued": 1,
                        "email_sent": 1,
                        "recipients": 1,
                    }
                ),
            ) as notify_mock,
            patch(
                "app.api.routes.admin_contracts.load_workflow_sheet_runtime_config",
                AsyncMock(
                    return_value=WorkflowSheetRuntimeConfig(
                        enabled=True,
                        credentials_path="/tmp/google.json",
                        spreadsheet_id="sheet-test",
                        workflow_devices_worksheet="Urzadzenia_magazyn",
                        source="admin",
                    )
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "synced_count": 1,
                    "rows": [{"source_row": 33, "sheet_row": 9, "action": "updated"}],
                    "added_headers": [],
                },
            ) as sheet_sync_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/status",
                headers={"X-Admin-Session": token},
                json={"business_status": "APPROVED_ORDER"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["workflow"]["business_status"], "APPROVED_ORDER")
        self.assertEqual(body["workflow"]["service_manager_binding"]["state"], "error")
        self.assertTrue(body["binding"]["alert"]["sent"])
        self.assertIn("błędy", body["message"])
        bind_mock.assert_called_once()
        notify_mock.assert_awaited_once()
        sheet_sync_mock.assert_called_once()

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
            device = (
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
            self.assertEqual(device.snapshot.get("ms_binding_status"), "error")

    async def test_contracts_form_workflow_status_rejects_foreign_device_before_change(self):
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
                firebird_client_id=2897,
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=33,
                    producer="Ricoh",
                    model="IM 350",
                    serial="FOREIGN-33",
                    ewidencja="KP/33",
                    snapshot={"row": 33, "source_type": "firebird_magazyn_28"},
                )
            )
            await session.commit()

        ownership_error = WorkflowDeviceOwnershipConflict(
            [
                WorkflowDeviceOwnershipConflictItem(
                    workflow_device_id=1,
                    source_row=33,
                    machine_id=703,
                    current_client_id=1001,
                    current_client_name="Inny klient",
                    reason="Urządzenie jest przypisane do klienta Inny klient (ID 1001).",
                )
            ]
        )
        with (
            patch(
                "app.api.routes.admin_contracts.validate_workflow_device_ownership",
                side_effect=ownership_error,
            ) as validate_mock,
            patch("app.api.routes.admin_contracts.bind_devices_to_workflow_client") as bind_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/status",
                headers={"X-Admin-Session": token},
                json={"business_status": "APPROVED_ORDER"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Inny klient (ID 1001)", response.json()["detail"])
        validate_mock.assert_called_once()
        bind_mock.assert_not_called()

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
            self.assertEqual(workflow_case.business_status, "WAITING_SIGNATURE")

    async def test_contracts_form_workflow_delivery_save_and_delete(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()
        delivery_date = date.today() + timedelta(days=2)

        save_response = await self.client.post(
            f"/admin/contracts/forms/{form.id}/workflow/delivery",
            headers={"X-Admin-Session": token},
            json={
                "delivery_date": delivery_date.isoformat(),
                "delivery_time_window": "10:00-12:00",
                "delivery_contact_name": "Jan Kowalski",
                "delivery_contact_phone": "+48600111222",
                "delivery_notes": "Wjazd od tylu budynku",
            },
        )

        self.assertEqual(save_response.status_code, 200)
        save_body = save_response.json()
        self.assertTrue(save_body["ok"])
        self.assertTrue(save_body["workflow"]["delivery_planned"])
        self.assertEqual(save_body["workflow"]["delivery_date"], delivery_date.isoformat())
        self.assertEqual(save_body["workflow"]["delivery_time_window"], "10:00-12:00")

        delete_response = await self.client.delete(
            f"/admin/contracts/forms/{form.id}/workflow/delivery",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(delete_response.status_code, 200)
        delete_body = delete_response.json()
        self.assertTrue(delete_body["ok"])
        self.assertFalse(delete_body["workflow"]["delivery_planned"])
        self.assertIsNone(delete_body["workflow"]["delivery_date"])

    async def test_contracts_delivery_schedule_move_and_delete(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()
        initial_date = date.today() + timedelta(days=3)
        moved_date = initial_date + timedelta(days=1)

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                delivery_date=initial_date,
                delivery_time_window="08:00-10:00",
                delivery_contact_name="Magazyn",
                delivery_contact_phone="+48600999888",
                delivery_notes="Wniesienie na pietro",
            )
            session.add(case)
            await session.commit()

        schedule_response = await self.client.get(
            (
                "/admin/contracts/delivery/schedule"
                f"?day_from={(date.today() + timedelta(days=1)).isoformat()}"
                f"&day_to={(date.today() + timedelta(days=10)).isoformat()}"
            ),
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(schedule_response.status_code, 200)
        schedule_body = schedule_response.json()
        self.assertTrue(schedule_body["ok"])
        self.assertEqual(len(schedule_body["items"]), 1)
        workflow_case_id = schedule_body["items"][0]["workflow_case_id"]

        move_response = await self.client.post(
            f"/admin/contracts/delivery/{workflow_case_id}/move",
            headers={"X-Admin-Session": token},
            json={"delivery_date": moved_date.isoformat()},
        )
        self.assertEqual(move_response.status_code, 200)
        self.assertEqual(move_response.json()["delivery_date"], moved_date.isoformat())

        delete_response = await self.client.delete(
            f"/admin/contracts/delivery/{workflow_case_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["ok"])

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
            self.assertIsNone(workflow_case.delivery_date)
            self.assertIsNone(workflow_case.delivery_time_window)

    async def test_contracts_form_workflow_proforma_creates_document_and_updates_case(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                firebird_client_status="created",
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="google_sheet",
                    source_row=23,
                    producer="Ricoh",
                    model="IMC 3500",
                    serial="3111RB80109",
                    ewidencja="KP/5032",
                    device_status="01. Przed zerowka",
                    reservation_status="brak rezerwacji",
                    price="3720",
                    price_net="3024.39",
                    price_gross="3720.00",
                    snapshot={
                        "row": 23,
                        "producer": "Ricoh",
                        "model": "IMC 3500",
                        "serial": "3111RB80109",
                        "ewidencja": "KP/5032",
                        "status": "01. Przed zerowka",
                        "reservation_status": "brak rezerwacji",
                        "price": "3720",
                        "price_net": "3024.39",
                        "price_gross": "3720.00",
                    },
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.create_proforma_from_workflow",
                return_value=FirebirdProformaWriteResult(
                    id_faktura_table=70001,
                    document_number="4/proforma/2026",
                    preview_url="/flow/proforma/70001/pdf",
                    line_count=1,
                    pdf_path="inbox/faktura/generated/proforma_70001.pdf",
                ),
            ) as create_proforma_mock,
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "synced_count": 1,
                    "rows": [{"source_row": 23, "sheet_row": 7, "action": "updated"}],
                    "added_headers": [],
                },
            ),
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/proforma",
                headers={"X-Admin-Session": token},
                json={"for_bank": False},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["created"])
        self.assertEqual(body["proforma_firebird_id"], 70001)
        self.assertEqual(body["proforma_number"], "4/proforma/2026")
        self.assertEqual(body["preview_url"], "/flow/proforma/70001/pdf")
        self.assertEqual(body["workflow"]["stage"], "PROFORMA_CREATED")
        self.assertEqual(body["workflow"]["business_status"], "WAITING_SIGNATURE")
        create_payload = create_proforma_mock.call_args.kwargs["selected_devices"][0]
        self.assertEqual(create_payload["price_net"], "3024.39")
        self.assertEqual(create_payload["price_gross"], "3720.00")

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
            self.assertEqual(workflow_case.proforma_firebird_id, 70001)
            self.assertEqual(workflow_case.proforma_number, "4/proforma/2026")
            self.assertEqual(
                workflow_case.proforma_pdf_path,
                "inbox/faktura/generated/proforma_70001.pdf",
            )
            self.assertEqual(workflow_case.business_status, "WAITING_SIGNATURE")

    async def test_contracts_form_workflow_proforma_returns_400_when_device_has_no_price(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                firebird_client_status="created",
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=18078,
                    producer="Ricoh",
                    model="IMC 5500",
                    serial="3139M330149",
                    ewidencja="KP/5060",
                    reservation_status="brak rezerwacji",
                    price="0.00",
                    price_net="0.00",
                    price_gross="0.00",
                    snapshot={
                        "row": 18078,
                        "source_row": 18078,
                        "source_type": "firebird_magazyn_28",
                        "producer": "Ricoh",
                        "model": "IMC 5500",
                        "serial": "3139M330149",
                        "ewidencja": "KP/5060",
                        "index": "KP/5060",
                        "price": "0.00",
                        "price_net": "0.00",
                        "price_gross": "0.00",
                        "ms_id_magazyn_table": 18078,
                    },
                )
            )
            await session.commit()

        response = await self.client.post(
            f"/admin/contracts/forms/{form.id}/workflow/proforma",
            headers={"X-Admin-Session": token},
            json={"for_bank": False},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Brak ceny dla urzadzenia KP/5060.", response.json()["detail"])

    async def test_contracts_form_workflow_proforma_defaults_to_bank_client(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                firebird_client_status="created",
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="google_sheet",
                    source_row=23,
                    producer="Ricoh",
                    model="IMC 3500",
                    serial="3111RB80109",
                    ewidencja="KP/5032",
                    device_status="01. Przed zerowka",
                    reservation_status="brak rezerwacji",
                    price="3720",
                    price_net="3024.39",
                    price_gross="3720.00",
                    snapshot={
                        "row": 23,
                        "producer": "Ricoh",
                        "model": "IMC 3500",
                        "serial": "3111RB80109",
                        "ewidencja": "KP/5032",
                        "status": "01. Przed zerowka",
                        "reservation_status": "brak rezerwacji",
                        "price": "3720",
                        "price_net": "3024.39",
                        "price_gross": "3720.00",
                    },
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.find_client_in_firebird_by_id",
                return_value=FirebirdClientMatch(
                    found=True,
                    id_klient=855,
                    nazwa="GRENKELEASING Sp. z o.o.",
                    nip="782-22-75-815",
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.create_proforma_from_workflow",
                return_value=FirebirdProformaWriteResult(
                    id_faktura_table=70011,
                    document_number="5/proforma/2026",
                    preview_url="/flow/proforma/70011/pdf",
                    line_count=1,
                    pdf_path="inbox/faktura/generated/proforma_70011.pdf",
                ),
            ) as create_proforma_mock,
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia_magazyn",
                    "synced_count": 1,
                    "rows": [{"source_row": 23, "sheet_row": 7, "action": "updated"}],
                    "added_headers": [],
                },
            ),
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/proforma",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["for_bank"])
        self.assertEqual(body["recipient_client_id"], 855)
        self.assertIn("odbiorca: bank", body["message"])
        self.assertEqual(create_proforma_mock.call_args.kwargs["firebird_client_id"], 855)

    async def test_contracts_form_workflow_proforma_returns_existing_document_without_recreating(
        self,
    ):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                firebird_client_status="created",
                proforma_firebird_id=70021,
                proforma_number="21/proforma/2026",
                proforma_pdf_path="inbox/faktura/generated/proforma_70021.pdf",
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=18070,
                    producer="Ricoh",
                    model="IMC 300",
                    serial="3920P401043",
                    ewidencja="KP/5045",
                    reservation_status="brak rezerwacji",
                    price="2361.60",
                    price_net="1920.00",
                    price_gross="2361.60",
                    snapshot={
                        "row": 18070,
                        "source_type": "firebird_magazyn_28",
                        "producer": "Ricoh",
                        "model": "IMC 300",
                        "serial": "3920P401043",
                        "ewidencja": "KP/5045",
                        "price": "2361.60",
                        "price_net": "1920.00",
                        "price_gross": "2361.60",
                    },
                )
            )
            await session.commit()

        with patch("app.api.routes.admin_contracts.create_proforma_from_workflow") as create_mock:
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/proforma",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["created"])
        self.assertEqual(body["proforma_number"], "21/proforma/2026")
        self.assertIn("Proforma jest juz zapisana", body["message"])
        create_mock.assert_not_called()

    async def test_contracts_form_workflow_proforma_reset_deletes_firebird_and_clears_sheet(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                firebird_client_status="created",
                proforma_firebird_id=70021,
                proforma_number="21/proforma/2026",
                proforma_pdf_path="inbox/faktura/generated/proforma_70021.pdf",
                client_payload_snapshot={"company_name": "FLOW TEST"},
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=18070,
                    producer="Ricoh",
                    model="IMC 300",
                    serial="3920P401043",
                    ewidencja="KP/5045",
                    reservation_status="brak rezerwacji",
                    price="2361.60",
                    price_net="1920.00",
                    price_gross="2361.60",
                    snapshot={
                        "row": 18070,
                        "sheet_row": 77,
                        "sheet_sync_status": "synced",
                        "sheet_proforma_number": "21/proforma/2026",
                    },
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.delete_proforma_from_firebird",
                return_value=SimpleNamespace(
                    id_faktura_table=70021,
                    deleted=True,
                    deleted_lines=1,
                    pdf_deleted=True,
                ),
            ) as delete_mock,
            patch(
                "app.api.routes.admin_contracts.clear_workflow_proforma_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia magazyn",
                    "cleared_count": 1,
                    "rows": [{"source_row": 18070, "sheet_row": 77, "action": "proforma_cleared"}],
                    "added_headers": [],
                },
            ) as sheet_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/proforma-reset",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["firebird_delete"]["deleted"])
        self.assertEqual(body["sheet_clear"]["cleared_count"], 1)
        self.assertIn("Menadzera Serwisu", body["message"])
        delete_mock.assert_called_once_with(70021)
        sheet_mock.assert_called_once()

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
            self.assertIsNone(workflow_case.proforma_firebird_id)
            self.assertIsNone(workflow_case.proforma_number)
            self.assertIsNone(workflow_case.proforma_pdf_path)

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
            snapshot = workflow_device.snapshot or {}
            self.assertEqual(snapshot.get("sheet_proforma_number"), "")

    async def test_contracts_form_workflow_proforma_reset_returns_409_when_firebird_delete_did_not_happen(
        self,
    ):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                proforma_firebird_id=70021,
                proforma_number="21/proforma/2026",
            )
            session.add(case)
            await session.commit()

        with patch(
            "app.api.routes.admin_contracts.delete_proforma_from_firebird",
            return_value=SimpleNamespace(
                id_faktura_table=70021,
                deleted=False,
                deleted_lines=0,
                pdf_deleted=False,
            ),
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/proforma-reset",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Nie znaleziono proformy", response.json()["detail"])

    async def test_contracts_form_workflow_proforma_reset_returns_409_when_sheet_rows_not_found(
        self,
    ):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                proforma_firebird_id=70021,
                proforma_number="21/proforma/2026",
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=18070,
                    producer="Ricoh",
                    model="IMC 300",
                    ewidencja="KP/5045",
                    snapshot={"row": 18070, "sheet_row": 77},
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.delete_proforma_from_firebird",
                return_value=SimpleNamespace(
                    id_faktura_table=70021,
                    deleted=True,
                    deleted_lines=1,
                    pdf_deleted=True,
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.clear_workflow_proforma_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia magazyn",
                    "cleared_count": 0,
                    "rows": [],
                    "added_headers": [],
                },
            ),
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/proforma-reset",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Arkusz Google nie odnalazl", response.json()["detail"])

    async def test_contracts_form_workflow_grenke_launch_requires_proforma_stage(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="DEVICES_SELECTED",
                business_status="DRAFT",
                client_mode="basic_proforma",
                firebird_client_id=2897,
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=220,
                    producer="Ricoh",
                    model="IM C300",
                    serial="X123",
                    ewidencja="KP/220",
                    price_net="9800.00",
                    price_gross="12054.00",
                )
            )
            await session.commit()

        response = await self.client.post(
            f"/admin/contracts/forms/{form.id}/workflow/grenke-launch",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("PROFORMA_CREATED", response.json()["detail"])

    async def test_contracts_form_workflow_grenke_launch_returns_full_prefill(self):
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
                firebird_client_id=2897,
                proforma_firebird_id=70035,
                proforma_number="35/proforma/2026",
            )
            session.add(case)
            await session.commit()

        with patch(
            "app.api.routes.admin_contracts.launch_grenke_prefill",
            AsyncMock(
                return_value=GrenkeLaunchResult(
                    url="https://newonline.leasingoptymalny.pl/kalkulacja/a1b2c3d4",
                    prefill_state="full",
                    warnings=[],
                    session_key="a1b2c3d4",
                )
            ),
        ) as launch_mock:
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/grenke-launch",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["prefill_state"], "full")
        self.assertEqual(body["warnings"], [])
        self.assertIn("/kalkulacja/", body["url"])
        self.assertIn("pelny prefill", body["message"])
        launch_kwargs = launch_mock.await_args.kwargs
        self.assertEqual(launch_kwargs["form"].id, form.id)
        self.assertEqual(launch_kwargs["workflow_case"].form_request_id, form.id)

    async def test_contracts_form_workflow_grenke_launch_returns_partial_prefill(self):
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
                firebird_client_id=2897,
                proforma_firebird_id=70035,
                proforma_number="35/proforma/2026",
            )
            session.add(case)
            await session.commit()

        with patch(
            "app.api.routes.admin_contracts.launch_grenke_prefill",
            AsyncMock(
                return_value=GrenkeLaunchResult(
                    url=(
                        "https://newonline.leasingoptymalny.pl/kalkulacja/e5f6g7h8?"
                        "p=Ricoh+IM+C300&c=9800.00"
                    ),
                    prefill_state="partial",
                    warnings=["saveCalculation.php: timeout"],
                    session_key="e5f6g7h8",
                )
            ),
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/grenke-launch",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["prefill_state"], "partial")
        self.assertEqual(body["warnings"], ["saveCalculation.php: timeout"])
        self.assertIn("czesciowy prefill", body["message"])

    async def test_contracts_form_workflow_sheet_sync_endpoint_updates_device_snapshot(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                proforma_firebird_id=70001,
                proforma_number="4/proforma/2026",
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=23,
                    producer="Ricoh",
                    model="IMC 3500",
                    serial="",
                    ewidencja="KP/5032",
                    snapshot={"row": 23, "index": "KP/5032"},
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_contracts.resolve_workflow_sheet_assignee",
                AsyncMock(
                    return_value={
                        "id": 208,
                        "login_user": "Marcin",
                        "label": "Marcin",
                    }
                ),
            ),
            patch(
                "app.api.routes.admin_contracts.sync_workflow_devices_to_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia magazyn",
                    "synced_count": 1,
                    "rows": [{"source_row": 23, "sheet_row": 77, "action": "updated"}],
                    "added_headers": [],
                },
            ) as sync_mock,
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/sheet-sync",
                headers={"X-Admin-Session": token},
                json={"sheet_assignee_id": 208},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["sheet_sync"]["synced_count"], 1)
        self.assertEqual(sync_mock.call_args.kwargs["assignee_label"], "Marcin")

        async with self.session_factory() as session:
            workflow_device = (
                (
                    await session.execute(
                        select(FormWorkflowDevice).where(FormWorkflowDevice.source_row == 23)
                    )
                )
                .scalars()
                .one()
            )
            snapshot = workflow_device.snapshot or {}
            self.assertEqual(snapshot.get("sheet_sync_status"), "synced")
            self.assertEqual(snapshot.get("sheet_row"), 77)
            self.assertEqual(snapshot.get("sheet_assignee_id"), 208)

    async def test_contracts_form_workflow_sheet_release_endpoint_marks_release(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                proforma_firebird_id=70001,
                proforma_number="4/proforma/2026",
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=23,
                    producer="Ricoh",
                    model="IMC 3500",
                    ewidencja="KP/5032",
                    snapshot={
                        "row": 23,
                        "index": "KP/5032",
                        "sheet_row": 77,
                        "sheet_sync_status": "synced",
                        "sheet_assignee_id": 208,
                        "sheet_assignee": "Marcin",
                    },
                )
            )
            await session.commit()

        with patch(
            "app.api.routes.admin_contracts.release_workflow_devices_from_sheet",
            return_value={
                "enabled": True,
                "reason": None,
                "worksheet_title": "Urzadzenia magazyn",
                "released_count": 1,
                "rows": [{"source_row": 23, "sheet_row": 77, "action": "released"}],
                "added_headers": [],
            },
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/sheet-release",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["sheet_release"]["released_count"], 1)

        async with self.session_factory() as session:
            workflow_device = (
                (
                    await session.execute(
                        select(FormWorkflowDevice).where(FormWorkflowDevice.source_row == 23)
                    )
                )
                .scalars()
                .one()
            )
            snapshot = workflow_device.snapshot or {}
            self.assertEqual(snapshot.get("sheet_sync_status"), "released")

    async def test_contracts_form_workflow_status_rejected_triggers_sheet_release(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                proforma_firebird_id=70001,
                proforma_number="4/proforma/2026",
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=21,
                    producer="Ricoh",
                    model="IM 350",
                    ewidencja="KP/21",
                    snapshot={
                        "row": 21,
                        "index": "KP/21",
                        "sheet_row": 91,
                        "sheet_sync_status": "synced",
                    },
                )
            )
            await session.commit()

        with patch(
            "app.api.routes.admin_contracts.release_workflow_devices_from_sheet",
            return_value={
                "enabled": True,
                "reason": None,
                "worksheet_title": "Urzadzenia magazyn",
                "released_count": 1,
                "rows": [{"source_row": 21, "sheet_row": 91, "action": "released"}],
                "added_headers": [],
            },
        ) as release_mock:
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/status",
                headers={"X-Admin-Session": token},
                json={"business_status": "REJECTED"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["workflow"]["business_status"], "REJECTED_GRENKE")
        self.assertNotIn("sheet_release", body)
        release_mock.assert_not_called()

    async def test_delete_form_releases_sheet_reservation_when_workflow_exists(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                proforma_firebird_id=70001,
                proforma_number="4/proforma/2026",
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=23,
                    producer="Ricoh",
                    model="IMC 3500",
                    ewidencja="KP/5032",
                    snapshot={"row": 23, "index": "KP/5032", "sheet_row": 77},
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_forms.release_workflow_devices_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia magazyn",
                    "released_count": 1,
                    "rows": [{"source_row": 23, "sheet_row": 77, "action": "released"}],
                },
            ) as release_mock,
            patch(
                "app.api.routes.admin_forms.delete_proforma_from_firebird",
                return_value=SimpleNamespace(
                    id_faktura_table=70001,
                    deleted=True,
                    deleted_lines=1,
                    pdf_deleted=True,
                ),
            ) as firebird_mock,
        ):
            response = await self.client.delete(
                f"/admin/forms/{form.id}",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 204)
        release_mock.assert_called_once()
        firebird_mock.assert_called_once_with(70001)

    async def test_delete_form_also_deletes_proforma_from_firebird(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        async with self.session_factory() as session:
            case = FormWorkflowCase(
                form_request_id=form.id,
                created_by=2,
                updated_by=2,
                stage="PROFORMA_CREATED",
                business_status="PENDING_APPROVAL",
                client_mode="basic_proforma",
                firebird_client_id=2897,
                proforma_firebird_id=70001,
                proforma_number="4/proforma/2026",
            )
            session.add(case)
            await session.flush()
            session.add(
                FormWorkflowDevice(
                    workflow_case_id=case.id,
                    source_type="firebird_magazyn_28",
                    source_row=23,
                    producer="Ricoh",
                    model="IMC 3500",
                    ewidencja="KP/5032",
                    snapshot={"row": 23, "index": "KP/5032", "sheet_row": 77},
                )
            )
            await session.commit()

        with (
            patch(
                "app.api.routes.admin_forms.release_workflow_devices_from_sheet",
                return_value={
                    "enabled": True,
                    "reason": None,
                    "worksheet_title": "Urzadzenia magazyn",
                    "released_count": 1,
                    "rows": [{"source_row": 23, "sheet_row": 77, "action": "released"}],
                },
            ) as release_mock,
            patch(
                "app.api.routes.admin_forms.delete_proforma_from_firebird",
                return_value=SimpleNamespace(
                    id_faktura_table=70001,
                    deleted=True,
                    deleted_lines=1,
                    pdf_deleted=True,
                ),
            ) as firebird_mock,
        ):
            response = await self.client.delete(
                f"/admin/forms/{form.id}",
                headers={"X-Admin-Session": token},
            )

        self.assertEqual(response.status_code, 204)
        release_mock.assert_called_once()
        firebird_mock.assert_called_once_with(70001)

    async def test_admin_users_duplicate_email_returns_400(self):
        token, _ = await self._login()
        payload = {
            "email": "duplikat@example.com",
            "first_name": "Adam",
            "last_name": "Test",
            "role": "operator",
            "mobile_phone": "+48600500500",
        }
        response = await self.client.post(
            "/admin/users",
            json=payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 201)

        response = await self.client.post(
            "/admin/users",
            json=payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("Adres e-mail jest już zajęty", body["detail"])

    async def test_admin_cannot_delete_self(self):
        token, _ = await self._login()
        response = await self.client.delete(
            "/admin/users/1",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 400)

    async def test_email_test_endpoint_sends_message(self):
        token, _ = await self._login()
        payload = {
            "host": "smtp.example.com",
            "port": 587,
            "username": "user",
            "password": "pass",
            "sender_name": "CTIP",
            "sender_address": "noreply@example.com",
            "test_recipient": "dest@example.com",
            "test_subject": "Temat testowy",
            "test_body": "Wiadomość testowa",
        }
        with patch(
            "app.api.routes.admin_email.test_smtp_connection",
            return_value=EmailTestResult(True, "OK"),
        ):
            with patch(
                "app.api.routes.admin_email.send_smtp_message",
                AsyncMock(return_value=EmailSendResult(True, "Wysłano")),
            ) as send_mock:
                response = await self.client.post(
                    "/admin/email/test",
                    json=payload,
                    headers={"X-Admin-Session": token},
                )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("Wysłano", data["message"])
        send_mock.assert_awaited_once()

    async def test_operator_cannot_create_users(self):
        token, _ = await self._login_operator()
        payload = {
            "email": "novice@example.com",
            "first_name": "Nowy",
            "last_name": "Operator",
            "role": "operator",
            "mobile_phone": "+48600000000",
        }
        response = await self.client.post(
            "/admin/users",
            json=payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

    async def test_sms_logs_endpoint_returns_tail(self):
        token, _ = await self._login()
        now = datetime.now()
        path = daily_log_path("sms", "sms_sender", now=now)
        existed_before = path.exists()
        original_content = path.read_text(encoding="utf-8") if existed_before else ""
        append_log("sms", "sms_sender", "Test wpis 1", now=now)
        append_log("sms", "sms_sender", "Test wpis 2", now=now + timedelta(seconds=1))
        try:
            response = await self.client.get(
                "/admin/sms/logs?limit=2",
                headers={"X-Admin-Session": token},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["log_path"], path.as_posix())
            self.assertLessEqual(len(payload["lines"]), 2)
            self.assertEqual(payload["lines"][-1]["message"], "Test wpis 2")
        finally:
            if existed_before:
                path.write_text(original_content, encoding="utf-8")
            else:
                path.unlink(missing_ok=True)

    async def test_sms_history_endpoint_lists_recent_messages(self):
        token, _ = await self._login()
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            session.add_all(
                [
                    SmsOut(
                        dest="+48500111222",
                        text="Pierwsza wiadomość",
                        status="SENT",
                        provider_status="OK",
                        provider_msg_id="abc-1",
                        created_at=now - timedelta(minutes=3),
                    ),
                    SmsOut(
                        dest="+48500111333",
                        text="Druga wiadomość z błędem",
                        status="ERROR",
                        error_msg="Invalid number",
                        created_at=now - timedelta(minutes=1),
                    ),
                ]
            )
            await session.commit()

        response = await self.client.get(
            "/admin/sms/history?limit=5",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertLessEqual(payload["limit"], 5)
        self.assertGreaterEqual(len(payload["items"]), 2)
        statuses = {item["status"] for item in payload["items"]}
        self.assertIn("SENT", statuses)
        self.assertIn("ERROR", statuses)
        self.assertIsNone(payload["status"])

    async def test_sms_history_endpoint_filters_by_status(self):
        token, _ = await self._login()
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            session.add_all(
                [
                    SmsOut(
                        dest="+48500111222",
                        text="Wiadomość OK",
                        status="SENT",
                        provider_status="OK",
                        provider_msg_id="abc-1",
                        created_at=now - timedelta(minutes=2),
                    ),
                    SmsOut(
                        dest="+48500111333",
                        text="Wiadomość z błędem",
                        status="ERROR",
                        error_msg="Invalid number",
                        created_at=now - timedelta(minutes=1),
                    ),
                ]
            )
            await session.commit()

        response = await self.client.get(
            "/admin/sms/history?status=SENT",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "SENT")
        statuses = {item["status"] for item in payload["items"]}
        self.assertEqual(statuses, {"SENT"})

    async def test_sms_history_endpoint_rejects_invalid_status(self):
        token, _ = await self._login()
        response = await self.client.get(
            "/admin/sms/history?status=UNKNOWN",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 422)

    async def test_sms_test_endpoint_rejects_invalid_number(self):
        token, _ = await self._login()
        response = await self.client.post(
            "/admin/sms/test",
            headers={"X-Admin-Session": token},
            json={"dest": "12345", "text": "Test"},
        )
        self.assertEqual(response.status_code, 422)

    async def test_operator_calls_api_returns_data(self):
        token, _ = await self._login_operator()
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            contact = Contact(
                number="+48670070000",
                ext="101",
                first_name="Adam",
                last_name="Nowak",
                company="Serwis X",
                email="adam.nowak@example.com",
                firebird_id="FB-200",
                notes="Klient VIP",
            )
            session.add(contact)
            call = Call(
                ext="101",
                number="+48670070000",
                direction="IN",
                answered_by="120",
                started_at=now - timedelta(minutes=2),
                connected_at=now - timedelta(minutes=1, seconds=30),
                ended_at=now - timedelta(minutes=1),
                duration_s=60,
                disposition="ANSWERED",
                last_state="END",
            )
            session.add(call)
            await session.flush()
            session.add_all(
                [
                    CallEvent(
                        call_id=call.id,
                        ts=now - timedelta(minutes=1, seconds=50),
                        typ="RING",
                        ext="101",
                        number="+48670070000",
                        payload=None,
                    ),
                    CallEvent(
                        call_id=call.id,
                        ts=now - timedelta(minutes=1, seconds=40),
                        typ="CONNECT",
                        ext="101",
                        number="+48670070000",
                        payload="",
                    ),
                ]
            )
            session.add(
                SmsOut(
                    dest="+48670070000",
                    text="Przykładowa wiadomość",
                    status="SENT",
                    created_by=1,
                    origin="ui",
                )
            )
            await session.commit()

        response = await self.client.get(
            "/operator/api/calls",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        calls = response.json()
        self.assertTrue(any(item["number"] == "+48670070000" for item in calls))

        call_id = calls[0]["id"]
        detail_response = await self.client.get(
            f"/operator/api/calls/{call_id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["call"]["number"], "+48670070000")
        self.assertGreaterEqual(len(detail["events"]), 2)
        self.assertGreaterEqual(len(detail["sms_history"]), 1)

    async def test_operator_call_detail_handles_normalized_sms_number(self):
        token, _ = await self._login_operator()
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            call = Call(
                ext="105",
                number="600700800",
                direction="IN",
                answered_by="120",
                started_at=now - timedelta(minutes=3),
                connected_at=None,
                ended_at=None,
                duration_s=None,
                disposition="NO_ANSWER",
                last_state="END",
            )
            session.add(call)
            await session.flush()
            session.add(
                SmsOut(
                    dest="+48600700800",
                    text="Brak prefiksu w numerze połączenia",
                    status="SENT",
                    origin="operator",
                    call_id=call.id,
                    created_by=1,
                    created_at=now - timedelta(minutes=1),
                )
            )
            await session.commit()

        response = await self.client.get(
            f"/operator/api/calls/{call.id}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["call"]["number"], "600700800")
        sms_dests = [entry["dest"] for entry in detail["sms_history"]]
        self.assertIn("+48600700800", sms_dests)

    async def test_operator_contact_lookup_by_number(self):
        token, _ = await self._login_operator()
        async with self.session_factory() as session:
            contact = Contact(
                number="+48650101010",
                first_name="Jan",
                last_name="Kontakt",
                firebird_id="FB-LOOKUP",
            )
            session.add(contact)
            await session.commit()

        ok_response = await self.client.get(
            "/operator/api/contacts/by-number/+48650101010",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(ok_response.status_code, 200)
        payload = ok_response.json()
        self.assertEqual(payload["firebird_id"], "FB-LOOKUP")

        not_found = await self.client.get(
            "/operator/api/contacts/by-number/+48999999999",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(not_found.status_code, 404)

    async def test_operator_sms_history_and_send(self):
        token, _ = await self._login_operator()
        dest = "+48670111222"
        raw_dest = "0048670111222"
        async with self.session_factory() as session:
            session.add(
                SmsOut(
                    dest=dest,
                    text="Historia",
                    status="SENT",
                    origin="ui",
                    created_by=1,
                )
            )
            await session.commit()

        history_resp = await self.client.get(
            f"/operator/api/sms/history?number={quote(dest, safe='')}",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(history_resp.status_code, 200)
        history = history_resp.json()
        self.assertGreaterEqual(len(history), 1)

        send_resp = await self.client.post(
            "/operator/api/sms/send",
            headers={"X-Admin-Session": token},
            json={"dest": raw_dest, "text": "Nowa wiadomość"},
        )
        self.assertEqual(send_resp.status_code, 201)
        payload = send_resp.json()
        self.assertEqual(payload["sms"]["dest"], dest)
        self.assertEqual(payload["sms"]["status"], "NEW")

        async with self.session_factory() as session:
            stmt = select(SmsOut).where(SmsOut.dest == dest).order_by(SmsOut.created_at.desc())
            result = await session.execute(stmt)
            entries = result.scalars().all()
            self.assertGreaterEqual(len(entries), 2)

    async def test_operator_sms_send_rejects_missing_call(self):
        token, _ = await self._login_operator()
        payload = {
            "dest": "+48600111222",
            "text": "Test bez powiązanego połączenia",
            "call_id": 999999,
        }
        response = await self.client.post(
            "/operator/api/sms/send",
            headers={"X-Admin-Session": token},
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("połączenie", detail.lower())

    async def test_operator_stats_endpoint(self):
        token, _ = await self._login_operator()
        local_now = datetime.now(UTC).astimezone(ZoneInfo("Europe/Warsaw"))
        month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_start_utc = month_start.astimezone(UTC)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        async with self.session_factory() as session:
            session.add_all(
                [
                    SmsOut(
                        dest="+48600200300",
                        text="Dzisiejszy",
                        status="SENT",
                        origin="ui",
                        created_by=1,
                        created_at=day_start + timedelta(hours=1),
                    ),
                    SmsOut(
                        dest="+48600200301",
                        text="Tego miesiąca",
                        status="SENT",
                        origin="ui",
                        created_by=1,
                        created_at=month_start_utc + timedelta(days=1, hours=1),
                    ),
                ]
            )
            await session.commit()

        response = await self.client.get(
            "/operator/api/stats",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        stats = response.json()
        self.assertGreaterEqual(stats["sms_today"], 1)
        self.assertGreaterEqual(stats["sms_month"], 2)

    async def test_operator_create_and_update_contact(self):
        token, _ = await self._login_operator()
        create_payload = {
            "number": "+48 600 700 900",
            "first_name": "Karol",
            "last_name": "Kontakt",
            "firebird_id": "FB-900",
            "notes": "Dodany z panelu operatora",
        }
        create_resp = await self.client.post(
            "/operator/api/contacts",
            headers={"X-Admin-Session": token},
            json=create_payload,
        )
        self.assertEqual(create_resp.status_code, 201)
        created = create_resp.json()
        self.assertEqual(created["number"], "600700900")
        contact_id = created["id"]

        update_payload = {
            "number": "600700900",
            "first_name": "Karolina",
            "last_name": "Kontakt",
            "firebird_id": "FB-901",
            "notes": "Zmienione dane",
        }
        update_resp = await self.client.put(
            f"/operator/api/contacts/{contact_id}",
            headers={"X-Admin-Session": token},
            json=update_payload,
        )
        self.assertEqual(update_resp.status_code, 200)
        updated = update_resp.json()
        self.assertEqual(updated["first_name"], "Karolina")
        self.assertEqual(updated["firebird_id"], "FB-901")

        lookup_resp = await self.client.get(
            "/operator/api/contacts/by-number/600700900",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(lookup_resp.status_code, 200)
        lookup = lookup_resp.json()
        self.assertEqual(lookup["first_name"], "Karolina")
        self.assertEqual(lookup["number"], "600700900")

    async def test_admin_manage_ivr_map(self):
        async with self.session_factory() as session:
            entry = await admin_ivr_map.upsert_entry(
                session,
                ext="777",
                digit=7,
                sms_text="Testowa wiadomość IVR",
                enabled=True,
            )
            await session.commit()
            self.assertEqual(entry.ext, "777")
            self.assertEqual(entry.digit, 7)
            self.assertTrue(entry.enabled)

            entry = await admin_ivr_map.upsert_entry(
                session,
                ext="777",
                digit=9,
                sms_text="Zmieniona treść",
                enabled=False,
            )
            await session.commit()
            self.assertEqual(entry.digit, 9)
            self.assertFalse(entry.enabled)

            listing = await admin_ivr_map.list_entries(session)
            self.assertTrue(any(item.ext == "777" for item in listing))

            now = datetime.now(UTC)
            call = Call(
                ext="900",
                number="+48600111222",
                direction="IN",
                started_at=now,
                last_state="RING",
                disposition="UNKNOWN",
            )
            session.add(call)
            await session.flush()
            session.add(
                SmsOut(
                    dest="+48600111222",
                    text="Instrukcja instalacji",
                    status="SENT",
                    source="ivr",
                    origin="ivr",
                    call_id=call.id,
                    created_at=now,
                    meta={"reason": "ivr_map", "ext": "900", "digit": 9},
                )
            )
            await session.commit()

            history = await admin_ctip.load_ivr_sms_history(session, 5)
            self.assertTrue(any(item.internal_ext == "900" and item.digit == 9 for item in history))

            await admin_ivr_map.delete_entry(session, "777")
            await session.commit()
        final_listing = await admin_ivr_map.list_entries(session)
        self.assertFalse(any(item.ext == "777" for item in final_listing))

    async def test_load_ivr_sms_history_filters_and_requires_admin(self):
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            call = Call(
                ext="500",
                number="+48600111222",
                direction="IN",
                started_at=now,
                last_state="RING",
                disposition="UNKNOWN",
            )
            session.add(call)
            await session.flush()
            session.add_all(
                [
                    SmsOut(
                        dest="+48600111222",
                        text="Instrukcja instalacji",
                        status="SENT",
                        source="ivr",
                        origin="ivr",
                        call_id=call.id,
                        created_at=now,
                        meta={"reason": "ivr_map", "ext": "500", "digit": 9},
                    ),
                    SmsOut(
                        dest="+48600111333",
                        text="UI test",
                        status="NEW",
                        source="ui",
                        origin="ui",
                        call_id=call.id,
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

            history = await admin_ctip.load_ivr_sms_history(session, 10)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].internal_ext, "500")
            self.assertEqual(history[0].digit, 9)

            filtered = await admin_ctip.load_ivr_sms_history(session, 10, ext_filter="999")
            self.assertEqual(filtered, [])

            sent_only = await admin_ctip.load_ivr_sms_history(session, 10, status_filter="SENT")
            self.assertEqual(len(sent_only), 1)
            self.assertEqual(sent_only[0].status, "SENT")

        with self.assertRaises(HTTPException):
            admin_ctip._ensure_admin("operator")

    async def test_ivr_dashboard_card_reports_errors(self):
        async with self.session_factory() as session:
            card, diagnostics = await admin_status._ivr_automation_status(session)
            self.assertIn("Automatyczne SMS", card["title"])
            self.assertIn("recent", diagnostics)

    async def test_update_email_config_persists_values(self):
        token, _ = await self._login()
        payload = {
            "host": "smtp.mail.local",
            "port": 2525,
            "username": "mailer",
            "password": "Sekret!123",
            "sender_name": "Powiadomienia CTIP",
            "sender_address": "powiadomienia@example.com",
            "use_tls": True,
            "use_ssl": False,
        }
        response = await self.client.put(
            "/admin/config/email",
            json=payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["host"], payload["host"])
        self.assertEqual(body["port"], payload["port"])
        self.assertEqual(body["username"], payload["username"])
        self.assertEqual(body["sender_address"], payload["sender_address"])
        self.assertTrue(body["password_set"])
        self.assertEqual(body["username"], payload["username"])
        self.assertEqual(body["sender_name"], payload["sender_name"])
        self.assertEqual(body["sender_address"], payload["sender_address"])
        self.assertTrue(body["use_tls"])
        self.assertFalse(body["use_ssl"])
        self.assertTrue(body["password_set"])

        response = await self.client.get(
            "/admin/config/email",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["host"], payload["host"])
        self.assertEqual(body["port"], payload["port"])

    async def test_update_form_handling_config_persists_values(self):
        token, _ = await self._login()
        payload = {
            "public_base_url": "https://form.example.com",
            "invite_sms_template": "Link dla {customer_name}: {form_url} do {expires_at}",
            "invite_email_subject": "Formularz dla {customer_name}",
            "invite_email_body": "Link: {form_url}\nTermin: {expires_at}\nPodpis: {sender_name}",
            "submission_email_subject": "Przyjeto formularz {company_name}",
            "submission_email_body": "Firma: {company_name}\nOpiekun: {sender_name}",
            "owner_sms_template": "Operator: formularz klienta {company_name} jest gotowy.",
        }
        response = await self.client.put(
            "/admin/config/form-handling",
            json=payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["public_base_url"], payload["public_base_url"])
        self.assertEqual(body["invite_sms_template"], payload["invite_sms_template"])
        self.assertEqual(body["invite_email_subject"], payload["invite_email_subject"])
        self.assertEqual(body["invite_email_body"], payload["invite_email_body"])
        self.assertEqual(body["submission_email_subject"], payload["submission_email_subject"])
        self.assertEqual(body["submission_email_body"], payload["submission_email_body"])
        self.assertEqual(body["owner_sms_template"], payload["owner_sms_template"])

        response = await self.client.get(
            "/admin/config/form-handling",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["public_base_url"], payload["public_base_url"])
        self.assertEqual(body["owner_sms_template"], payload["owner_sms_template"])

    async def test_default_form_handling_config_returns_business_templates(self):
        token, _ = await self._login()
        response = await self.client.get(
            "/admin/config/form-handling",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["public_base_url"], default_public_base_url())
        self.assertIn("Ksero Partner", body["invite_sms_template"])
        self.assertIn("formularza serwisowego", body["invite_email_subject"])
        self.assertIn("{form_url}", body["invite_email_body"])
        self.assertIn("{expires_at}", body["invite_email_body"])
        self.assertEqual(
            body["submission_email_subject"],
            "Potwierdzenie przyjecia formularza serwisowego",
        )
        self.assertIn("{customer_name}", body["owner_sms_template"])

    async def test_update_form_handling_config_rejects_unknown_placeholder(self):
        token, _ = await self._login()
        response = await self.client.put(
            "/admin/config/form-handling",
            json={
                "public_base_url": "https://form.example.com",
                "invite_sms_template": "Link {unsupported}",
                "invite_email_subject": "Formularz dla {customer_name}",
                "invite_email_body": "Link: {form_url}",
                "submission_email_subject": "Przyjeto formularz {company_name}",
                "submission_email_body": "Firma: {company_name}",
                "owner_sms_template": "Klient {company_name}",
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("nieobslugiwana zmienna", response.json().get("detail", ""))

    async def test_form_generation_uses_configured_public_base_url_and_templates(self):
        token, _ = await self._login()
        response = await self.client.put(
            "/admin/config/form-handling",
            json={
                "public_base_url": "https://form.example.com",
                "invite_sms_template": "SMS dla {customer_name}: {form_url}",
                "invite_email_subject": "Link dla {customer_name}",
                "invite_email_body": "Adres: {form_url}\nTermin: {expires_at}\nPodpis: {sender_name}",
                "submission_email_subject": "Przyjeto formularz {company_name}",
                "submission_email_body": "Firma: {company_name}\nPodpis: {sender_name}",
                "owner_sms_template": "Klient {company_name} zakonczyl formularz.",
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)

        email_settings = EmailDeliverySettings(
            host="smtp.test.local",
            port=587,
            username="smtp-user",
            password="smtp-pass",
            sender_name="Biuro Test",
            sender_address="noreply@test.local",
            use_tls=True,
            use_ssl=False,
        )
        with (
            patch(
                "app.services.form_generator.admin_users.resolve_email_delivery_settings",
                AsyncMock(return_value=email_settings),
            ),
            patch(
                "app.services.form_generator.send_smtp_message",
                AsyncMock(return_value=EmailSendResult(True, "Wyslano")),
            ) as send_mock,
        ):
            response = await self.client.post(
                "/admin/forms",
                headers={"X-Admin-Session": token},
                json={
                    "customer_name": "Klient Link",
                    "customer_email": "klient.link@example.com",
                    "customer_phone": "+48 600 700 900",
                },
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["sms_queued"])
        self.assertTrue(body["email_sent"])
        self.assertTrue(body["form_url"].startswith("https://form.example.com/formularz/"))

        message = send_mock.call_args.kwargs["message"]
        self.assertEqual(message["Subject"], "Link dla Klient Link")
        self.assertIn("https://form.example.com/formularz/", message.get_content())
        self.assertIn("Biuro Test", message.get_content())

        async with self.session_factory() as session:
            sms_row = (
                (
                    await session.execute(
                        select(SmsOut)
                        .where(SmsOut.origin == "form_link_generated")
                        .order_by(SmsOut.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
            self.assertIsNotNone(sms_row)
            assert sms_row is not None
            self.assertIn("SMS dla Klient Link:", sms_row.text)
            self.assertIn("https://form.example.com/formularz/", sms_row.text)

    async def test_update_call_sms_config_persists_values(self):
        token, _ = await self._login()
        payload = {
            "enabled": True,
            "inbound_enabled": True,
            "outbound_enabled": False,
            "inbound_answered_enabled": True,
            "inbound_answered_text": "Dziekujemy za rozmowe",
            "inbound_missed_enabled": True,
            "inbound_missed_text": "Oddzwonimy najszybciej jak to mozliwe",
            "inbound_repeat_answered_enabled": False,
            "inbound_repeat_answered_text": "",
            "inbound_repeat_missed_enabled": False,
            "inbound_repeat_missed_text": "",
            "outbound_answered_enabled": False,
            "outbound_answered_text": "",
            "outbound_missed_enabled": False,
            "outbound_missed_text": "",
            "outbound_repeat_answered_enabled": False,
            "outbound_repeat_answered_text": "",
            "outbound_repeat_missed_enabled": False,
            "outbound_repeat_missed_text": "",
            "after_hours_enabled": True,
            "after_hours_text": "Po godzinach prosimy o kontakt przez aplikacje: https://www.ksero-partner.com.pl/app/",
            "after_hours_exts": "500",
            "cooldown_mode": "after_days",
            "cooldown_days": 7,
            "opt_out_numbers": "+48600111222",
        }
        response = await self.client.put(
            "/admin/call-sms/config",
            json=payload,
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["cooldown_mode"], "after_days")
        self.assertEqual(body["cooldown_days"], 7)

        response = await self.client.get(
            "/admin/call-sms/config",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["inbound_answered_text"], payload["inbound_answered_text"])
        self.assertEqual(body["opt_out_numbers"], payload["opt_out_numbers"])
        self.assertEqual(body["after_hours_text"], payload["after_hours_text"])

    async def test_bulk_call_sms_creates_queue_entries(self):
        token, _ = await self._login()
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            session.add_all(
                [
                    Call(
                        ext="500",
                        number="600111222",
                        direction="IN",
                        started_at=now,
                        disposition="NO_ANSWER",
                    ),
                    Call(
                        ext="500",
                        number="600111222",
                        direction="IN",
                        started_at=now,
                        disposition="NO_ANSWER",
                    ),
                    Call(
                        ext="500",
                        number="221234567",
                        direction="IN",
                        started_at=now,
                        disposition="NO_ANSWER",
                    ),
                    Call(
                        ext="500",
                        number="0049301234567",
                        direction="IN",
                        started_at=now,
                        disposition="NO_ANSWER",
                    ),
                ]
            )
            await session.commit()

        response = await self.client.post(
            "/admin/call-sms/bulk",
            json={"text": "Test masowej wysylki", "direction": "IN"},
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["created"], 1)
        self.assertEqual(payload["total_unique"], 1)

        async with self.session_factory() as session:
            result = await session.execute(select(SmsOut).where(SmsOut.source == "call_sms"))
            rows = result.scalars().all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].dest, "+48600111222")

    async def test_update_email_config_rejects_conflicting_encryption(self):
        token, _ = await self._login()
        response = await self.client.put(
            "/admin/config/email",
            json={
                "host": "smtp.mail.local",
                "port": 465,
                "use_tls": True,
                "use_ssl": True,
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 422)

    @patch("app.api.routes.admin_email.test_smtp_connection")
    async def test_email_test_endpoint_uses_current_configuration(self, mock_test):
        mock_test.return_value = EmailTestResult(True, "Połączenie OK")
        token, _ = await self._login()
        # zapisz konfigurację, żeby ustawić hasło
        await self.client.put(
            "/admin/config/email",
            json={
                "host": "smtp.mail.local",
                "port": 2525,
                "username": "mailer",
                "password": "Sekret!",
                "sender_address": "powiadomienia@example.com",
                "use_tls": True,
                "use_ssl": False,
            },
            headers={"X-Admin-Session": token},
        )

        response = await self.client.post(
            "/admin/email/test",
            json={
                "host": "smtp.test.local",
                "port": 2500,
                "use_tls": False,
                "use_ssl": True,
                "username": "tester",
                "password": "NoweHaslo!",
                "sender_address": "alerts@example.com",
            },
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        mock_test.assert_called_once()
        args, kwargs = mock_test.call_args
        self.assertEqual(kwargs["host"], "smtp.test.local")
        self.assertEqual(kwargs["port"], 2500)
        self.assertEqual(kwargs["username"], "tester")
        self.assertEqual(kwargs["password"], "NoweHaslo!")
        self.assertFalse(kwargs["use_tls"])
        self.assertTrue(kwargs["use_ssl"])

    async def test_ctip_events_endpoint_returns_recent_entries(self):
        token, _ = await self._login()
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            session.add_all(
                [
                    CallEvent(
                        ts=now - timedelta(seconds=10),
                        typ="RING",
                        ext="101",
                        number="123456789",
                        payload="{}",
                    ),
                    CallEvent(
                        ts=now - timedelta(seconds=5),
                        typ="ANSWER",
                        ext="102",
                        number="987654321",
                        payload=None,
                    ),
                    CallEvent(
                        ts=now - timedelta(seconds=3),
                        typ="T",
                        ext="103",
                        number=None,
                        payload=None,
                    ),
                ]
            )
            await session.commit()

        response = await self.client.get(
            "/admin/ctip/events?limit=5",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["limit"], 5)
        self.assertGreaterEqual(len(payload["items"]), 2)
        self.assertIsNone(payload["ext"])

    async def test_ctip_events_endpoint_filters_extension(self):
        token, _ = await self._login()
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            session.add_all(
                [
                    CallEvent(
                        ts=now - timedelta(seconds=10),
                        typ="RING",
                        ext="101",
                        number="123456789",
                        payload="{}",
                    ),
                    CallEvent(
                        ts=now - timedelta(seconds=5),
                        typ="ANSWER",
                        ext="102",
                        number="987654321",
                        payload=None,
                    ),
                    CallEvent(
                        ts=now - timedelta(seconds=3),
                        typ="T",
                        ext="103",
                        number=None,
                        payload=None,
                    ),
                ]
            )
            await session.commit()

        response = await self.client.get(
            "/admin/ctip/events?ext=102",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["ext"], "102")
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["ext"], "102")


if __name__ == "__main__":
    unittest.main()
