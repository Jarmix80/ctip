# ruff: noqa: E402

"""Testy jednostkowe dispatch narzędzi CTIP AI Asystenta."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.assistant_tools import AssistantDataTools, AssistantToolResult


class AssistantToolsDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_tool_routes_firebird_business_read(self) -> None:
        tools = AssistantDataTools(session=None, settings_store_secret=None)  # type: ignore[arg-type]
        expected = AssistantToolResult(
            tool_name="firebird_business_read",
            status="success",
            payload={"rows": []},
            row_count=0,
            generated_sql=None,
            error_message=None,
            duration_ms=3,
        )
        tools.firebird_business_read = AsyncMock(return_value=expected)  # type: ignore[method-assign]

        result = await tools.execute_tool(
            "firebird_business_read",
            {
                "intent": "devices_by_company",
                "company_name": "Steico",
                "months_back": 12,
                "row_limit": 50,
            },
        )

        self.assertEqual(result, expected)
        tools.firebird_business_read.assert_awaited_once_with(
            intent="devices_by_company",
            company_name="Steico",
            model_name=None,
            serial_number=None,
            months_back=12,
            row_limit=50,
        )

    async def test_execute_tool_routes_firebird_knowledge_read(self) -> None:
        tools = AssistantDataTools(session=None, settings_store_secret=None)  # type: ignore[arg-type]
        expected = AssistantToolResult(
            tool_name="firebird_knowledge_read",
            status="success",
            payload={"tables": []},
            row_count=0,
            generated_sql=None,
            error_message=None,
            duration_ms=2,
        )
        tools.firebird_knowledge_read = AsyncMock(return_value=expected)  # type: ignore[method-assign]

        result = await tools.execute_tool(
            "firebird_knowledge_read",
            {
                "table_name": "MASZYNA",
                "topic": "liczniki",
                "include_columns": False,
                "row_limit": 25,
            },
        )

        self.assertEqual(result, expected)
        tools.firebird_knowledge_read.assert_awaited_once_with(
            table_name="MASZYNA",
            topic="liczniki",
            include_columns=False,
            row_limit=25,
        )


if __name__ == "__main__":
    unittest.main()
