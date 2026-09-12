"""Testy uprawnienia finansów Orbit w kontach, serializacji i audycie administratora."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes import admin_users as admin_users_api
from app.models import AdminAuditLog, AdminSession, AdminSetting, AdminUser, Base
from app.schemas.admin import AdminUserCreate, AdminUserDetail, AdminUserUpdate
from app.services import admin_users, section_permissions


def _sample_user(role: str, permission: bool | None) -> AdminUser:
    """Buduje konto z zapisaną flagą Orbit do sprawdzania serializacji."""
    now = datetime.now(UTC)
    return AdminUser(
        id=1,
        email="konto@example.com",
        role=role,
        password_hash="testowy-skrot",
        is_active=True,
        is_salesperson=False,
        can_withdraw_device_pz=False,
        can_edit_toner_yields=False,
        can_view_orbit_finance=permission,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_create_schema_defaults_orbit_finance_to_false(role):
    """Żadna rola nie otrzymuje zapisanej flagi Orbit automatycznie."""
    payload = AdminUserCreate(email="konto@example.com", mobile_phone="600111222", role=role)

    assert payload.can_view_orbit_finance is False
    assert payload.can_edit_toner_yields is False
    assert payload.can_withdraw_device_pz is False
    assert payload.sections is None


def test_update_schema_distinguishes_omitted_and_false_permission():
    """Pominięcie flagi pozwala zachować nadanie, a false jawnie je odbiera."""
    omitted = AdminUserUpdate(email="konto@example.com")
    revoked = AdminUserUpdate(email="konto@example.com", can_view_orbit_finance=False)

    assert omitted.can_view_orbit_finance is None
    assert "can_view_orbit_finance" not in omitted.model_fields_set
    assert revoked.can_view_orbit_finance is False
    assert "can_view_orbit_finance" in revoked.model_fields_set


def test_orbit_finance_column_defaults_to_false():
    """Kolumna wymaga wartości logicznej i domyślnie odmawia dostępu."""
    column = AdminUser.__table__.c.can_view_orbit_finance

    assert column.nullable is False
    assert column.default.arg is False
    assert str(column.server_default.arg) == "false"


@pytest.mark.parametrize("role", ["operator", "admin"])
@pytest.mark.parametrize("permission", [None, False, True])
def test_summary_and_detail_serialize_stored_orbit_finance(role, permission):
    """Serializacja zwraca flagę konta bez zastępowania jej uprawnieniem roli admin."""
    user = _sample_user(role, permission)
    sections = section_permissions.default_sections_for_role(role)
    row = admin_users.UserRow(user=user, sessions_active=0, last_login_at=None)

    summary = admin_users_api._map_summary(row, sections, None)
    detail = AdminUserDetail(**summary.model_dump(), sessions=[])

    assert summary.model_dump(mode="json")["can_view_orbit_finance"] is bool(permission)
    assert detail.model_dump(mode="json")["can_view_orbit_finance"] is bool(permission)
    assert summary.can_edit_toner_yields is False
    assert summary.can_withdraw_device_pz is (role == "admin")
    assert summary.sections == sections


class OrbitUserPermissionTests(unittest.IsolatedAsyncioTestCase):
    """Sprawdza serwis i API użytkowników na SQLite w pamięci, bez wysyłania wiadomości."""

    async def asyncSetUp(self):
        """Przygotowuje tabele kont oraz izolowane zależności API administratora."""
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )
        self.addAsyncCleanup(self.engine.dispose)

        @event.listens_for(self.engine.sync_engine, "connect")
        def _add_sqlite_functions(connection, _record):
            """Udostępnia odpowiednik funkcji czasu używanej przez modele PostgreSQL."""
            connection.create_function("timezone", 2, lambda _zone, value: value)

        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    AdminUser.__table__,
                    AdminSession.__table__,
                    AdminSetting.__table__,
                    AdminAuditLog.__table__,
                ],
            )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.sessions() as session:
            session.add(_sample_user("admin", False))
            await session.commit()

        self.actor = SimpleNamespace(id=1, role="admin")

        async def _admin_context():
            """Zwraca lokalnego administratora bez odczytu rzeczywistych sesji."""
            return SimpleNamespace(client_ip="127.0.0.1"), self.actor

        async def _db_session():
            """Udostępnia wyłącznie bazę SQLite bieżącego testu."""
            async with self.sessions() as session:
                yield session

        app = FastAPI()
        app.include_router(admin_users_api.router)
        app.dependency_overrides[get_admin_session_context] = _admin_context
        app.dependency_overrides[get_db_session] = _db_session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)
        for name, result in (
            ("resolve_email_delivery_settings", None),
            ("queue_credentials_sms", False),
            ("send_credentials_email", None),
        ):
            self.enterContext(patch.object(admin_users, name, AsyncMock(return_value=result)))

    async def test_orm_insert_without_permission_defaults_to_false(self):
        """Bezpośrednie utworzenie modelu nie nadaje operatorowi nowego prawa."""
        async with self.sessions() as session:
            user = AdminUser(
                email="orm@example.com", role="operator", password_hash="testowy-skrot"
            )
            session.add(user)
            await session.commit()
            user_id = user.id

        async with self.sessions() as session:
            stored = await session.get(AdminUser, user_id)
            self.assertFalse(stored.can_view_orbit_finance)
            self.assertFalse(stored.can_edit_toner_yields)
            self.assertFalse(stored.can_withdraw_device_pz)

    async def test_service_create_persists_explicit_permission_for_both_roles(self):
        """Serwis zapisuje jawne nadanie i zachowuje odmowę domyślną dla obu ról."""
        variants = ({}, {"can_view_orbit_finance": False}, {"can_view_orbit_finance": True})
        for role in ("operator", "admin"):
            for index, permissions in enumerate(variants):
                with self.subTest(role=role, permissions=permissions):
                    async with self.sessions() as session:
                        user, _password = await admin_users.create_user(
                            session,
                            email=f"serwis-{role}-{index}@example.com",
                            first_name=None,
                            last_name=None,
                            internal_ext=None,
                            role=role,
                            **permissions,
                        )
                        await session.commit()
                        user_id = user.id

                    async with self.sessions() as session:
                        stored = await session.get(AdminUser, user_id)
                        self.assertIs(
                            stored.can_view_orbit_finance,
                            permissions.get("can_view_orbit_finance", False),
                        )
                        self.assertFalse(stored.can_edit_toner_yields)
                        self.assertFalse(stored.can_withdraw_device_pz)
                        self.assertFalse(stored.is_salesperson)

    async def test_service_update_preserves_omitted_and_revokes_explicit_permission(self):
        """Zmiana Orbit zachowuje pominięte flagi i nie narusza pozostałych nadań."""
        async with self.sessions() as session:
            user, _password = await admin_users.create_user(
                session,
                email="aktualizacja@example.com",
                first_name=None,
                last_name=None,
                internal_ext=None,
                role="operator",
                is_salesperson=True,
                can_withdraw_device_pz=True,
                can_edit_toner_yields=True,
                can_view_orbit_finance=True,
            )
            await session.commit()
            user_id = user.id

        for permissions, expected in (
            ({}, True),
            ({"can_view_orbit_finance": None}, True),
            ({"can_view_orbit_finance": False}, False),
            ({}, False),
            ({"can_view_orbit_finance": True}, True),
        ):
            with self.subTest(permissions=permissions, expected=expected):
                async with self.sessions() as session:
                    user = await session.get(AdminUser, user_id)
                    await admin_users.update_user(
                        session,
                        user,
                        email=user.email,
                        first_name=user.first_name,
                        last_name=user.last_name,
                        internal_ext=user.internal_ext,
                        role=user.role,
                        is_salesperson=user.is_salesperson,
                        can_withdraw_device_pz=user.can_withdraw_device_pz,
                        crm_sales_sms_enabled=user.crm_sales_sms_enabled,
                        crm_sales_email_enabled=user.crm_sales_email_enabled,
                        crm_operations_sms_enabled=user.crm_operations_sms_enabled,
                        crm_operations_email_enabled=user.crm_operations_email_enabled,
                        mobile_phone=user.mobile_phone,
                        firebird_app_user_id=user.firebird_app_user_id,
                        firebird_app_user_login=user.firebird_app_user_login,
                        **permissions,
                    )
                    await session.commit()

                async with self.sessions() as session:
                    stored = await session.get(AdminUser, user_id)
                    self.assertIs(stored.can_view_orbit_finance, expected)
                    self.assertTrue(stored.can_edit_toner_yields)
                    self.assertTrue(stored.can_withdraw_device_pz)
                    self.assertTrue(stored.is_salesperson)

    async def test_api_create_list_detail_and_audit_preserve_stored_permission(self):
        """Tworzenie, lista, szczegóły i audyt pokazują tę samą zapisaną flagę Orbit."""
        variants = ({}, {"can_view_orbit_finance": False}, {"can_view_orbit_finance": True})
        for role in ("operator", "admin"):
            for index, permissions in enumerate(variants):
                with self.subTest(role=role, permissions=permissions):
                    response = await self.client.post(
                        "/admin/users",
                        json={
                            "email": f"api-{role}-{index}@example.com",
                            "mobile_phone": "600111222",
                            "role": role,
                            **permissions,
                        },
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    summary = response.json()["user"]
                    user_id = summary["id"]
                    expected = permissions.get("can_view_orbit_finance", False)
                    self.assertIs(summary["can_view_orbit_finance"], expected)
                    self.assertFalse(summary["can_edit_toner_yields"])
                    self.assertEqual(
                        summary["sections"], section_permissions.default_sections_for_role(role)
                    )

                    detail = await self.client.get(f"/admin/users/{user_id}")
                    self.assertEqual(detail.status_code, 200, detail.text)
                    self.assertIs(detail.json()["can_view_orbit_finance"], expected)
                    listing = await self.client.get("/admin/users")
                    self.assertEqual(listing.status_code, 200, listing.text)
                    listed = next(item for item in listing.json()["items"] if item["id"] == user_id)
                    self.assertIs(listed["can_view_orbit_finance"], expected)

                    async with self.sessions() as session:
                        stored = await session.get(AdminUser, user_id)
                        self.assertIs(stored.can_view_orbit_finance, expected)
                        self.assertFalse(stored.can_withdraw_device_pz)
                        entries = await session.scalars(
                            select(AdminAuditLog).where(AdminAuditLog.action == "user_create")
                        )
                        entry = next(item for item in entries if item.payload["user_id"] == user_id)
                        self.assertIs(entry.payload["can_view_orbit_finance"], expected)

    async def test_api_update_audits_grant_preservation_and_revocation(self):
        """API audytuje wynik nadania, pominięcia, null i odebrania prawa Orbit."""
        account = {
            "email": "zmiana-api@example.com",
            "mobile_phone": "600111222",
            "role": "operator",
            "is_salesperson": True,
            "can_withdraw_device_pz": True,
            "can_edit_toner_yields": True,
            "sections": ["operator", "generator", "shipping"],
        }
        created = await self.client.post("/admin/users", json=account)
        self.assertEqual(created.status_code, 201, created.text)
        user_id = created.json()["user"]["id"]
        update = {key: value for key, value in account.items() if key != "can_edit_toner_yields"}

        for permissions, expected in (
            ({"can_view_orbit_finance": True}, True),
            ({}, True),
            ({"can_view_orbit_finance": None}, True),
            ({"can_view_orbit_finance": False}, False),
            ({}, False),
        ):
            with self.subTest(permissions=permissions, expected=expected):
                response = await self.client.put(
                    f"/admin/users/{user_id}", json={**update, **permissions}
                )
                self.assertEqual(response.status_code, 200, response.text)
                detail = response.json()
                self.assertIs(detail["can_view_orbit_finance"], expected)
                self.assertTrue(detail["can_edit_toner_yields"])
                self.assertTrue(detail["can_withdraw_device_pz"])
                self.assertTrue(detail["is_salesperson"])
                self.assertEqual(detail["sections"], account["sections"])

                async with self.sessions() as session:
                    stored = await session.get(AdminUser, user_id)
                    self.assertIs(stored.can_view_orbit_finance, expected)
                    entry = await session.scalar(
                        select(AdminAuditLog)
                        .where(AdminAuditLog.action == "user_update")
                        .order_by(AdminAuditLog.id.desc())
                        .limit(1)
                    )
                    self.assertEqual(entry.payload["user_id"], user_id)
                    self.assertIs(entry.payload["can_view_orbit_finance"], expected)
                    self.assertTrue(entry.payload["can_edit_toner_yields"])
