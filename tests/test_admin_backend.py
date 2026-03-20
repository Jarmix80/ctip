# ruff: noqa: E402

import json
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
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
    FormRequest,
    FormWorkflowCase,
    FormWorkflowDevice,
    IvrMap,
    SmsOut,
    SmsTemplate,
)
from app.models.base import Base
from app.services import admin_ivr_map, section_permissions
from app.services.admin_users import EmailDeliverySettings
from app.services.backup_runner import BackupFileInfo, BackupRunResult
from app.services.contracts_dashboard import (
    FirebirdClientMatch,
    FirebirdClientWriteResult,
    FirebirdDeviceSyncResult,
)
from app.services.contracts_proforma import FirebirdProformaWriteResult
from app.services.email_client import EmailSendResult, EmailTestResult
from app.services.firebird_client import FirebirdTestResult
from app.services.office365_backup import Office365ConnectionResult, Office365UploadResult
from app.services.security import hash_password
from app.services.settings_store import StoredValue
from log_utils import append_log, daily_log_path


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
        settings.admin_secret_key = Fernet.generate_key().decode("ascii")
        settings.backup_scheduler_enabled = False

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

    async def test_login_and_me_returns_user_info(self):
        token, payload = await self._login()
        self.assertIn("expires_at", payload)

        response = await self.client.get(
            "/admin/auth/me",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["email"], "admin@example.com")
        self.assertEqual(data["first_name"], "Jan")
        self.assertEqual(data["role"], "admin")
        self.assertIn("mobile_phone", data)

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
        self.assertIn("operator", me["sections"])

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
            "password": "masterkey",
            "charset": "UTF8",
            "role": "RDB$ADMIN",
            "local_copy_path": "inbox/firebird/ms_local.fdb",
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
        self.assertTrue(body["password_set"])

        response = await self.client.get(
            "/admin/config/firebird",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"], update_payload["user"])
        self.assertEqual(body["port"], update_payload["port"])

        async with self.session_factory() as session:
            setting = await session.get(AdminSetting, "firebird.host")
            self.assertIsNotNone(setting)
            self.assertEqual(setting.value, update_payload["host"])

    async def test_update_firebird_vmaintenance_config_persists_values(self):
        token, _ = await self._login()
        update_payload = {
            "host": "192.168.0.8",
            "port": 3050,
            "database": "D:/bazavmantenance/BAZA_CPC.FDB",
            "user": "SYSDBA",
            "password": "masterkey",
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
                "password": "masterkey",
                "charset": "UTF8",
                "role": None,
                "local_copy_path": "inbox/firebird/ms_local.fdb",
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
                "password": "masterkey",
                "charset": "WIN1250",
                "role": None,
                "local_copy_path": "/srv/firebird/local/BAZAMS_LOCAL.FDB",
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
                "password": "masterkey",
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
        )
        try:
            with patch(
                "app.api.routes.admin_backup.create_local_backup",
                return_value=fake_run,
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

    async def test_backup_run_uploads_to_all_enabled_cloud_folders(self):
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
        )

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
                    "app.api.routes.admin_backup.upload_file_to_sharepoint",
                    new=AsyncMock(side_effect=fake_upload),
                ) as upload_mock,
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
        self.assertIn("Wysłano do SharePoint", data["message"])

        called_folders = [call.kwargs.get("folder_path") for call in upload_mock.await_args_list]
        unique_folders = set(called_folders)
        self.assertEqual(
            unique_folders,
            {
                "BackupKP/CTIP",
                "BackupKP/Menadzer_Serwisu/prod",
                "BackupKP/Menadzer_Serwisu/test",
                "BackupKP/Optima",
            },
        )
        self.assertEqual(len(called_folders), 8)

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
        self.assertEqual(created_user["sections"], ["operator"])
        self.assertTrue(body["password"])
        self.send_email_mock.assert_awaited_once()

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
        self.assertEqual(detail["sections"], ["operator"])

        update_payload = {
            "email": "nowy.uzytkownik@example.com",
            "first_name": "Marek",
            "last_name": "Zielinski",
            "internal_ext": "305",
            "role": "admin",
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
        self.assertEqual(updated["mobile_phone"], "+48600111222")
        self.assertEqual(updated["sections"], ["admin", "generator"])

        async with self.session_factory() as session:
            db_user = await session.get(AdminUser, user_id)
            self.assertIsNotNone(db_user)
            old_hash = db_user.password_hash

        response = await self.client.post(
            f"/admin/users/{user_id}/reset-password",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 200)
        reset_payload = response.json()
        self.assertGreaterEqual(len(reset_payload["password"]), 8)

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
        self.assertIn("operator", updated["sections"])

        async with self.session_factory() as session:
            stmt = select(AdminUser).where(AdminUser.email == "operator.updated@example.com")
            db_user = (await session.execute(stmt)).scalar_one_or_none()
            self.assertIsNotNone(db_user)
            self.assertEqual(db_user.internal_ext, "222")
            self.assertEqual(db_user.mobile_phone, "+48699111222")

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
            await session.commit()

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
                        "correspondence_same_as_registered": "false",
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
        self.assertEqual(submit_response.status_code, 200)
        self.assertIn("formularz został zapisany", submit_response.text.lower())

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
            self.assertEqual(len(sms_rows), 1)
            self.assertEqual(sms_rows[0].dest, "+48600700800")

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
                "app.api.routes.admin_contracts.load_devices_from_sheet",
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

    async def test_contracts_dashboard_skips_devices_when_include_devices_disabled(self):
        token, _ = await self._login_operator()
        await self._create_submitted_form_request(
            customer_name="Klient Bez Urzadzen",
            customer_email="bez-urzadzen@test.local",
            customer_phone="+48600303030",
        )

        with (
            patch(
                "app.api.routes.admin_contracts.load_devices_from_sheet",
                side_effect=AssertionError("load_devices_from_sheet nie powinno byc wywolane"),
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
        token, _ = await self._login_operator()
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

    async def test_contracts_action_sync_device_rejects_when_firebird_writes_disabled(self):
        token, _ = await self._login_operator()

        with (
            patch(
                "app.api.routes.admin_contracts.load_device_from_sheet_row",
                return_value={
                    "row": "77",
                    "serial": "SN-77",
                    "ewidencja": "KP/77",
                    "model": "IM 350",
                },
            ),
            patch(
                "app.api.routes.admin_contracts.firebird_writes_enabled",
                return_value=(
                    False,
                    "Zapis do lokalnej Firebird jest zablokowany. Ustaw FB_ALLOW_WRITES=true w srodowisku testowym.",
                ),
            ),
        ):
            response = await self.client.post(
                "/admin/contracts/action",
                headers={"X-Admin-Session": token},
                json={
                    "entity": "device",
                    "action": "synchronizuj",
                    "row": 77,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("FB_ALLOW_WRITES", response.json()["detail"])

    async def test_contracts_action_sync_device_executes_when_enabled(self):
        token, _ = await self._login_operator()
        expected = FirebirdDeviceSyncResult(
            row=78,
            serial="SN-78",
            ewidencja="KP/78",
            model_source="IM 350",
            model_id=458,
            machine_id=9001,
            machine_created=True,
            warehouse_id=19001,
            warehouse_created=True,
        )

        with (
            patch(
                "app.api.routes.admin_contracts.load_device_from_sheet_row",
                return_value={
                    "row": "78",
                    "serial": "SN-78",
                    "ewidencja": "KP/78",
                    "model": "IM 350",
                },
            ),
            patch(
                "app.api.routes.admin_contracts.firebird_writes_enabled",
                return_value=(True, None),
            ),
            patch(
                "app.api.routes.admin_contracts.synchronize_device_from_sheet_row",
                return_value=expected,
            ) as sync_mock,
        ):
            response = await self.client.post(
                "/admin/contracts/action",
                headers={"X-Admin-Session": token},
                json={
                    "entity": "device",
                    "action": "synchronizuj",
                    "row": 78,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["machine_id"], 9001)
        self.assertEqual(body["warehouse_id"], 19001)
        self.assertTrue(body["machine_created"])
        self.assertTrue(body["warehouse_created"])
        self.assertIn("Zsynchronizowano", body["message"])
        sync_mock.assert_called_once_with(78, kto="CTIP")

        async with self.session_factory() as session:
            entries = (
                (
                    await session.execute(
                        select(AdminAuditLog).where(AdminAuditLog.action == "contracts_device_sync")
                    )
                )
                .scalars()
                .all()
            )
            self.assertTrue(
                any(entry.payload and entry.payload.get("row") == 78 for entry in entries)
            )

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
                "app.api.routes.admin_contracts.load_devices_from_sheet",
                return_value=[
                    {
                        "row": "14",
                        "producer": "Ricoh",
                        "model": "IM 350",
                        "serial": "SN-14",
                        "ewidencja": "KP/14",
                        "status": "01. Przed zerowka",
                        "price": "1900",
                        "reservation": "01.Magazyn KP",
                        "reservation_status": "brak rezerwacji",
                        "description": "",
                        "ms_id_maszyna": "7621",
                        "ms_id_klient": "656",
                        "ms_nazwa_klienta": "MAGAZYN",
                        "ms_nip": "7780119742",
                    },
                    {
                        "row": "15",
                        "producer": "Ricoh",
                        "model": "IMC 3000",
                        "serial": "SN-15",
                        "ewidencja": "KP/15",
                        "status": "02. Po zerowce",
                        "price": "2500",
                        "reservation": "01.Magazyn KP",
                        "reservation_status": "brak rezerwacji",
                        "description": "",
                        "ms_id_maszyna": "",
                        "ms_id_klient": "",
                        "ms_nazwa_klienta": "",
                        "ms_nip": "",
                    },
                ],
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
        self.assertEqual(body["workflow"]["business_status"], "PENDING_APPROVAL")
        selected = [item for item in body["available_devices"] if item["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["row"], 14)
        self.assertEqual(selected[0]["price_net"], "1544.72")
        self.assertEqual(selected[0]["price_gross"], "1900.00")
        self.assertTrue(any(item["label"] == "NIP" for item in body["client_preview"]))
        self.assertEqual(body["sales_packet"]["devices"][0]["price_gross"], "1900.00")

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
            self.assertEqual(workflow_case.firebird_client_id, 3210)
            self.assertEqual(workflow_case.client_mode, "basic_proforma")

    async def test_contracts_form_workflow_devices_saves_selected_rows(self):
        token, _ = await self._login_operator()
        form = await self._create_submitted_form_request()

        with patch(
            "app.api.routes.admin_contracts.load_devices_from_sheet",
            return_value=[
                {
                    "row": "21",
                    "producer": "Ricoh",
                    "model": "IM 350",
                    "serial": "SN-21",
                    "ewidencja": "KP/21",
                    "status": "01. Przed zerowka",
                    "price": "1900",
                    "reservation": "01.Magazyn KP",
                    "reservation_status": "brak rezerwacji",
                    "description": "",
                    "ms_id_maszyna": "7621",
                    "ms_id_klient": "656",
                    "ms_nazwa_klienta": "MAGAZYN",
                    "ms_nip": "7780119742",
                },
                {
                    "row": "22",
                    "producer": "Ricoh",
                    "model": "IMC 3000",
                    "serial": "SN-22",
                    "ewidencja": "KP/22",
                    "status": "02. Po zerowce",
                    "price": "2500",
                    "reservation": "01.Magazyn KP",
                    "reservation_status": "brak rezerwacji",
                    "description": "",
                    "ms_id_maszyna": "",
                    "ms_id_klient": "",
                    "ms_nazwa_klienta": "",
                    "ms_nip": "",
                },
            ],
        ):
            response = await self.client.post(
                f"/admin/contracts/forms/{form.id}/workflow/devices",
                headers={"X-Admin-Session": token},
                json={
                    "devices": [
                        {"row": 21, "price_net": "1544.72", "price_gross": "1900.00"},
                        {"row": 22, "price_net": "2032.52", "price_gross": "2500.00"},
                    ]
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected_rows"], [21, 22])
        self.assertEqual(body["workflow"]["devices_selected_count"], 2)

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
            self.assertEqual(devices_by_row[21].price_net, "1544.72")
            self.assertEqual(devices_by_row[21].price_gross, "1900.00")
            self.assertEqual(devices_by_row[22].price_net, "2032.52")
            self.assertEqual(devices_by_row[22].price_gross, "2500.00")

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

        with patch(
            "app.api.routes.admin_contracts.create_proforma_from_workflow",
            return_value=FirebirdProformaWriteResult(
                id_faktura_table=70001,
                document_number="4/proforma/2026",
                preview_url="/flow/proforma/70001/pdf",
                line_count=1,
                pdf_path="inbox/faktura/generated/proforma_70001.pdf",
            ),
        ) as create_proforma_mock:
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
        self.assertEqual(body["workflow"]["business_status"], "PENDING_APPROVAL")
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
            self.assertEqual(workflow_case.business_status, "PENDING_APPROVAL")

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

        with patch(
            "app.api.routes.admin_contracts.find_client_in_firebird_by_id",
            return_value=FirebirdClientMatch(
                found=True,
                id_klient=855,
                nazwa="GRENKELEASING Sp. z o.o.",
                nip="782-22-75-815",
            ),
        ):
            with patch(
                "app.api.routes.admin_contracts.create_proforma_from_workflow",
                return_value=FirebirdProformaWriteResult(
                    id_faktura_table=70011,
                    document_number="5/proforma/2026",
                    preview_url="/flow/proforma/70011/pdf",
                    line_count=1,
                    pdf_path="inbox/faktura/generated/proforma_70011.pdf",
                ),
            ) as create_proforma_mock:
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
