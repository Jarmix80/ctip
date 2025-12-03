# ruff: noqa: E402

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import quote
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

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
    IvrMap,
    SmsOut,
    SmsTemplate,
)
from app.models.base import Base
from app.services import admin_ivr_map
from app.services.email_client import EmailSendResult, EmailTestResult
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

        update_payload = {
            "email": "nowy.uzytkownik@example.com",
            "first_name": "Marek",
            "last_name": "Zielinski",
            "internal_ext": "305",
            "role": "admin",
            "mobile_phone": "+48600111222",
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
            self.assertGreaterEqual(len(matching), 1)
            self.assertEqual(matching[-1].dest, "+48600700800")

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

        self.assertEqual(self.send_email_mock.await_count, 1)

    async def test_operator_cannot_manage_users(self):
        token, _ = await self._login_operator()
        response = await self.client.get(
            "/admin/users",
            headers={"X-Admin-Session": token},
        )
        self.assertEqual(response.status_code, 403)

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
            json={"dest": dest, "text": "Nowa wiadomość"},
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
        self.assertEqual(body["username"], payload["username"])
        self.assertEqual(body["sender_address"], payload["sender_address"])
        self.assertTrue(body["password_set"])

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
