# ruff: noqa: E402

"""Testy runtime konfiguracji CTIP AI Asystenta."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.models import AdminSetting
from app.models.base import Base
from app.services.assistant_runtime import AssistantRuntime, load_assistant_runtime_config


class AssistantRuntimeConfigTests(unittest.IsolatedAsyncioTestCase):
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
                    AdminSetting.__table__,
                ],
            )

        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_uses_openai_api_chat_kp_env_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_CHAT_KP": "chat-kp-key-from-env",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            async with self.session_factory() as session:
                config = await load_assistant_runtime_config(session, secret_key=None)

        self.assertEqual(config.api_key, "chat-kp-key-from-env")
        self.assertEqual(config.imap_row_limit, 30)
        self.assertEqual(config.schema_row_limit, 50)

    async def test_admin_setting_key_has_priority_over_env(self) -> None:
        async with self.session_factory() as session:
            session.add(
                AdminSetting(
                    key="assistant.openai_api_key",
                    value="stored-priority-key",
                    is_secret=False,
                )
            )
            await session.commit()

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_CHAT_KP": "chat-kp-key-from-env",
                "OPENAI_API_KEY": "default-key-from-env",
            },
            clear=False,
        ):
            async with self.session_factory() as session:
                config = await load_assistant_runtime_config(session, secret_key=None)

        self.assertEqual(config.api_key, "stored-priority-key")

    async def test_tool_definitions_include_firebird_business_read(self) -> None:
        runtime = AssistantRuntime(session=None, secret_key=None)  # type: ignore[arg-type]
        tools = runtime._tool_definitions(
            firebird_limit=200,
            sheets_limit=200,
            imap_limit=30,
            schema_limit=50,
        )
        names = {item.get("name") for item in tools}
        self.assertIn("firebird_business_read", names)
        self.assertIn("firebird_knowledge_read", names)
        self.assertIn("email_send_report", names)
        business_tool = next(item for item in tools if item.get("name") == "firebird_business_read")
        intents = business_tool["parameters"]["properties"]["intent"]["enum"]
        self.assertIn("active_devices_on_contracts_count", intents)
        self.assertIn("contract_settlement_period_explainer", intents)

    async def test_build_input_messages_includes_worker_prompt(self) -> None:
        runtime = AssistantRuntime(session=None, secret_key=None)  # type: ignore[arg-type]
        messages = runtime._build_input_messages(
            "Pokaż dane",
            history=[{"role": "user", "content": "Test"}],
            learning_context="Użytkownik preferuje krótkie odpowiedzi.",
            worker_prompt="Twoja rola: Opiekun Klienta.",
        )
        self.assertGreaterEqual(len(messages), 3)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Kontekst uczenia użytkownika", messages[0]["content"])
        self.assertIn("Profil pracownika AI", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
