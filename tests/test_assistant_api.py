# ruff: noqa: E402

"""Testy API modułu CTIP AI Asystent."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.api import deps
from app.main import create_app
from app.models import (
    AdminSession,
    AdminSetting,
    AdminUser,
    AssistantChangeRequest,
    AssistantChatMessage,
    AssistantChatThread,
    AssistantToolCallLog,
    AssistantUserProfile,
    AssistantWeeklyInsight,
)
from app.models.base import Base
from app.services.assistant_runtime import AssistantGenerationResult
from app.services.assistant_tools import AssistantToolResult
from app.services.security import hash_password, hash_session_token


class AssistantApiTests(unittest.IsolatedAsyncioTestCase):
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
                    AssistantChatThread.__table__,
                    AssistantChatMessage.__table__,
                    AssistantToolCallLog.__table__,
                    AssistantChangeRequest.__table__,
                    AssistantUserProfile.__table__,
                    AssistantWeeklyInsight.__table__,
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
            transport=ASGITransport(app=self.app),
            base_url="http://testserver",
        )

        now = datetime.now(UTC)
        async with self.session_factory() as session:
            admin = AdminUser(
                id=1,
                email="admin@example.com",
                first_name="Admin",
                last_name="One",
                role="admin",
                password_hash=hash_password("Admin123!"),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            operator = AdminUser(
                id=2,
                email="operator@example.com",
                first_name="Operator",
                last_name="Two",
                role="operator",
                password_hash=hash_password("Operator123!"),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add_all([admin, operator])
            await session.flush()

            admin_session = AdminSession(
                user_id=1,
                token=hash_session_token("admin-token"),
                created_at=now,
                expires_at=now + timedelta(hours=2),
            )
            operator_session = AdminSession(
                user_id=2,
                token=hash_session_token("operator-token"),
                created_at=now,
                expires_at=now + timedelta(hours=2),
            )
            session.add_all([admin_session, operator_session])
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.app.dependency_overrides.clear()
        await self.engine.dispose()

    async def test_chat_lifecycle_for_user(self) -> None:
        create_response = await self.client.post(
            "/assistant/chats",
            headers={"X-Admin-Session": "admin-token"},
            json={"title": "Test AI"},
        )
        self.assertEqual(create_response.status_code, status.HTTP_200_OK)
        chat_id = create_response.json()["id"]
        self.assertEqual(create_response.json()["worker_key"], "ksero_partner_analyst")

        list_response = await self.client.get(
            "/assistant/chats",
            headers={"X-Admin-Session": "admin-token"},
        )
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.json()), 1)
        self.assertEqual(list_response.json()[0]["id"], chat_id)

        detail_response = await self.client.get(
            f"/assistant/chats/{chat_id}",
            headers={"X-Admin-Session": "admin-token"},
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.json()["thread"]["id"], chat_id)
        self.assertEqual(detail_response.json()["thread"]["worker_key"], "ksero_partner_analyst")
        self.assertEqual(detail_response.json()["messages"], [])

    async def test_workers_catalog_and_chat_creation_with_selected_worker(self) -> None:
        workers_response = await self.client.get(
            "/assistant/workers",
            headers={"X-Admin-Session": "admin-token"},
        )
        self.assertEqual(workers_response.status_code, status.HTTP_200_OK)
        workers = workers_response.json()
        keys = {item["key"] for item in workers}
        self.assertIn("ksero_partner_analyst", keys)
        self.assertIn("opiekun_klienta", keys)
        self.assertIn("diagnosta_bazy_ms", keys)

        create_response = await self.client.post(
            "/assistant/chats",
            headers={"X-Admin-Session": "admin-token"},
            json={"title": "Nowy pracownik", "worker_key": "opiekun_klienta"},
        )
        self.assertEqual(create_response.status_code, status.HTTP_200_OK)
        self.assertEqual(create_response.json()["worker_key"], "opiekun_klienta")

    async def test_message_response_and_tool_sources(self) -> None:
        create_response = await self.client.post(
            "/assistant/chats",
            headers={"X-Admin-Session": "admin-token"},
            json={"title": "Integracja"},
        )
        chat_id = create_response.json()["id"]

        generation = AssistantGenerationResult(
            answer_text="Odpowiedz testowa",
            response_id="resp_123",
            model_name="gpt-4.1-mini",
            input_tokens=10,
            output_tokens=20,
            tool_results=[],
            sources=[
                {"tool": "firebird_read", "row_count": 3, "duration_ms": 11},
                {"tool": "firebird_business_read", "row_count": 4, "duration_ms": 9},
                {"tool": "firebird_knowledge_read", "row_count": 1, "duration_ms": 6},
                {"tool": "imap_read", "row_count": 2, "duration_ms": 7},
                {"tool": "ctip_schema_read", "row_count": 1, "duration_ms": 5},
                {"tool": "email_send_report", "row_count": 1, "duration_ms": 4},
                {"tool": "workflow_devices_audit", "row_count": 2, "duration_ms": 8},
            ],
            blocked_as_change_request=False,
        )
        with patch(
            "app.api.routes.assistant.AssistantRuntime.generate", AsyncMock(return_value=generation)
        ):
            message_response = await self.client.post(
                f"/assistant/chats/{chat_id}/messages",
                headers={"X-Admin-Session": "admin-token"},
                json={"prompt": "Pokaż dane", "stream": False},
            )
        self.assertEqual(message_response.status_code, status.HTTP_200_OK)
        payload = message_response.json()
        self.assertEqual(payload["assistant_message"]["content"], "Odpowiedz testowa")
        self.assertEqual(payload["assistant_message"]["openai_response_id"], "resp_123")
        self.assertEqual(payload["sources"][0]["tool"], "firebird_read")
        self.assertEqual(payload["sources"][1]["tool"], "firebird_business_read")
        self.assertEqual(payload["sources"][2]["tool"], "firebird_knowledge_read")
        self.assertEqual(payload["sources"][3]["tool"], "imap_read")
        self.assertEqual(payload["sources"][4]["tool"], "ctip_schema_read")
        self.assertEqual(payload["sources"][5]["tool"], "email_send_report")
        self.assertEqual(payload["sources"][6]["tool"], "workflow_devices_audit")
        self.assertFalse(payload["blocked_as_change_request"])

    async def test_message_with_pending_workflow_devices_action_creates_change_request(
        self,
    ) -> None:
        create_response = await self.client.post(
            "/assistant/chats",
            headers={"X-Admin-Session": "operator-token"},
            json={"title": "Audyt urządzeń"},
        )
        chat_id = create_response.json()["id"]

        generation = AssistantGenerationResult(
            answer_text="Raport audytu urządzeń.",
            response_id=None,
            model_name="rule-based-workflow-devices-audit",
            input_tokens=None,
            output_tokens=None,
            tool_results=[],
            sources=[{"tool": "workflow_devices_audit", "row_count": 1, "duration_ms": 11}],
            blocked_as_change_request=False,
            pending_action={
                "type": "workflow_devices_chat_sheet_stage",
                "label": "Zapisz do urzadzenia_chat",
                "request_text": "Zapisz wynik audytu do zakładki roboczej.",
                "justification": "Docelowy arkusz pozostaje bez zmian.",
                "summary": {
                    "stage_rows_count": 1,
                    "stage_fill_ms_id_count": 1,
                    "stage_append_count": 0,
                },
                "stage": {
                    "type": "workflow_devices_chat_sheet_stage",
                    "headers": ["MS_ID_MAGAZYN_TABLE"],
                    "rows": [{"target_values": ["18408"]}],
                    "row_count": 1,
                },
            },
        )
        with patch(
            "app.api.routes.assistant.AssistantRuntime.generate", AsyncMock(return_value=generation)
        ):
            response = await self.client.post(
                f"/assistant/chats/{chat_id}/messages",
                headers={"X-Admin-Session": "operator-token"},
                json={"prompt": "Sprawdź urządzenia w arkuszu i Firebird", "stream": False},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIsNotNone(body["change_request_id"])
        self.assertEqual(body["pending_action"]["id"], body["change_request_id"])
        self.assertEqual(
            body["pending_action"]["type"],
            "workflow_devices_chat_sheet_stage",
        )
        self.assertEqual(body["pending_action"]["summary"]["stage_rows_count"], 1)

        async with self.session_factory() as session:
            item = await session.get(AssistantChangeRequest, body["change_request_id"])
            self.assertIsNotNone(item)
            self.assertEqual(item.status, "pending")
            self.assertEqual(item.created_by, 2)
            self.assertEqual(item.payload["type"], "workflow_devices_chat_sheet_stage")

    async def test_access_to_foreign_thread_is_forbidden(self) -> None:
        create_response = await self.client.post(
            "/assistant/chats",
            headers={"X-Admin-Session": "admin-token"},
            json={"title": "Prywatny"},
        )
        chat_id = create_response.json()["id"]

        other_user_response = await self.client.get(
            f"/assistant/chats/{chat_id}",
            headers={"X-Admin-Session": "operator-token"},
        )
        self.assertEqual(other_user_response.status_code, status.HTTP_403_FORBIDDEN)

    async def test_auto_change_request_when_write_intent_blocked(self) -> None:
        create_response = await self.client.post(
            "/assistant/chats",
            headers={"X-Admin-Session": "admin-token"},
            json={"title": "Zmiany"},
        )
        chat_id = create_response.json()["id"]

        generation = AssistantGenerationResult(
            answer_text="Tryb tylko odczytu.",
            response_id=None,
            model_name="gpt-4.1-mini",
            input_tokens=None,
            output_tokens=None,
            tool_results=[],
            sources=[],
            blocked_as_change_request=True,
        )
        with patch(
            "app.api.routes.assistant.AssistantRuntime.generate", AsyncMock(return_value=generation)
        ):
            response = await self.client.post(
                f"/assistant/chats/{chat_id}/messages",
                headers={"X-Admin-Session": "admin-token"},
                json={"prompt": "Zmien status klienta", "stream": False},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertTrue(body["blocked_as_change_request"])
        self.assertIsNotNone(body["change_request_id"])

        change_requests = await self.client.get(
            "/assistant/change-requests",
            headers={"X-Admin-Session": "admin-token"},
        )
        self.assertEqual(change_requests.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(change_requests.json()), 1)

    async def test_change_request_approval_requires_admin(self) -> None:
        create_response = await self.client.post(
            "/assistant/change-requests",
            headers={"X-Admin-Session": "operator-token"},
            json={"request_text": "Prośba testowa"},
        )
        self.assertEqual(create_response.status_code, status.HTTP_200_OK)
        request_id = create_response.json()["id"]

        forbidden_response = await self.client.post(
            f"/assistant/change-requests/{request_id}/approve",
            headers={"X-Admin-Session": "operator-token"},
            json={"note": "nie mam uprawnien"},
        )
        self.assertEqual(forbidden_response.status_code, status.HTTP_403_FORBIDDEN)

        approve_response = await self.client.post(
            f"/assistant/change-requests/{request_id}/approve",
            headers={"X-Admin-Session": "admin-token"},
            json={"note": "zatwierdzone testowo"},
        )
        self.assertEqual(approve_response.status_code, status.HTTP_200_OK)
        self.assertEqual(approve_response.json()["status"], "approved")
        self.assertEqual(approve_response.json()["decided_by"], 1)

    async def test_operator_can_execute_own_workflow_devices_stage_request(self) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            item = AssistantChangeRequest(
                created_by=2,
                request_text="Zapisz staging urządzeń.",
                justification="Test stagingu.",
                payload={
                    "type": "workflow_devices_chat_sheet_stage",
                    "stage": {
                        "headers": ["MS_ID_MAGAZYN_TABLE"],
                        "rows": [{"target_values": ["18408"]}],
                        "row_count": 1,
                    },
                },
                status="pending",
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            request_id = item.id

        result_payload = {
            "spreadsheet_id": "sheet-1",
            "spreadsheet_title": "Zerowki_prod",
            "worksheet_title": "urzadzenia_chat",
            "written_rows": 1,
        }
        with patch(
            "app.api.routes.assistant.execute_workflow_devices_chat_sheet_stage",
            AsyncMock(return_value=result_payload),
        ) as execute_mock:
            response = await self.client.post(
                f"/assistant/change-requests/{request_id}/execute-workflow-devices-chat-sheet",
                headers={"X-Admin-Session": "operator-token"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["result"]["written_rows"], 1)
        self.assertEqual(body["change_request"]["status"], "executed")
        self.assertEqual(body["change_request"]["decided_by"], 2)
        execute_mock.assert_awaited_once()
        async with self.session_factory() as session:
            item = await session.get(AssistantChangeRequest, request_id)
            self.assertEqual(item.status, "executed")
            self.assertEqual(item.payload["execution_result"]["written_rows"], 1)

    async def test_weekly_insight_generation_for_admin(self) -> None:
        response = await self.client.get(
            "/assistant/insights/weekly",
            headers={"X-Admin-Session": "admin-token"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertGreaterEqual(len(payload), 1)
        self.assertIn("Raport tygodniowy asystenta", payload[0]["summary"])

    async def test_admin_can_get_and_update_learning_profile(self) -> None:
        get_response = await self.client.get(
            "/assistant/users/2/learning-profile",
            headers={"X-Admin-Session": "admin-token"},
        )
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        body = get_response.json()
        self.assertEqual(body["user_id"], 2)
        self.assertIn("preferences", body)

        update_response = await self.client.put(
            "/assistant/users/2/learning-profile",
            headers={"X-Admin-Session": "admin-token"},
            json={
                "personalization_enabled": True,
                "preferences": {
                    "business_learning": {
                        "company_aliases": {"steico": "Steico Sp. z o.o."},
                        "model_aliases": {"mpc3004": "MP C3004"},
                    }
                },
                "memory_notes": "Preferowane krótkie odpowiedzi.",
            },
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        updated = update_response.json()
        self.assertEqual(updated["user_id"], 2)
        self.assertTrue(updated["personalization_enabled"])
        self.assertEqual(
            updated["preferences"]["business_learning"]["company_aliases"]["steico"],
            "Steico Sp. z o.o.",
        )
        self.assertEqual(updated["memory_notes"], "Preferowane krótkie odpowiedzi.")

    async def test_operator_cannot_access_learning_profile_admin_endpoint(self) -> None:
        response = await self.client.get(
            "/assistant/users/1/learning-profile",
            headers={"X-Admin-Session": "operator-token"},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    async def test_message_updates_learning_profile_from_business_tool(self) -> None:
        create_response = await self.client.post(
            "/assistant/chats",
            headers={"X-Admin-Session": "admin-token"},
            json={"title": "Nauka"},
        )
        chat_id = create_response.json()["id"]

        generation = AssistantGenerationResult(
            answer_text="Znaleziono urządzenia.",
            response_id=None,
            model_name="rule-based-business-router",
            input_tokens=None,
            output_tokens=None,
            tool_results=[
                AssistantToolResult(
                    tool_name="firebird_business_read",
                    status="success",
                    payload={
                        "intent": "devices_by_company",
                        "criteria": {"company_name": "Steico"},
                        "rows": [],
                    },
                    row_count=0,
                    generated_sql=None,
                    error_message=None,
                    duration_ms=5,
                )
            ],
            sources=[{"tool": "firebird_business_read", "row_count": 0, "duration_ms": 5}],
            blocked_as_change_request=False,
        )
        with patch(
            "app.api.routes.assistant.AssistantRuntime.generate", AsyncMock(return_value=generation)
        ):
            response = await self.client.post(
                f"/assistant/chats/{chat_id}/messages",
                headers={"X-Admin-Session": "admin-token"},
                json={"prompt": "Wyswietl urzadzenia firmy steico", "stream": False},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        async with self.session_factory() as session:
            profile = (
                await session.execute(
                    select(AssistantUserProfile).where(AssistantUserProfile.user_id == 1)
                )
            ).scalar_one_or_none()
            self.assertIsNotNone(profile)
            self.assertIsInstance(profile.preferences, dict)
            learning = profile.preferences.get("business_learning")
            self.assertIsInstance(learning, dict)
            counts = learning.get("intent_success_counts")
            self.assertEqual(counts.get("devices_by_company"), 1)


if __name__ == "__main__":
    unittest.main()
