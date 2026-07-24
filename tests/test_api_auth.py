import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api import deps
from app.services import section_permissions


class AuthDependencyTests(unittest.IsolatedAsyncioTestCase):
    def test_admin_default_includes_every_available_section(self):
        self.assertEqual(
            section_permissions.default_sections_for_role("admin"),
            list(section_permissions.AVAILABLE_SECTIONS),
        )

    def test_admin_legacy_section_selection_is_expanded(self):
        self.assertEqual(
            section_permissions.deserialize_sections(
                '["admin","operator","generator"]',
                role="admin",
            ),
            list(section_permissions.AVAILABLE_SECTIONS),
        )

    async def test_operator_dependency_rejects_wrong_role(self):
        user = SimpleNamespace(role="serwisant")
        with self.assertRaises(HTTPException) as ctx:
            await deps.get_operator_user((SimpleNamespace(), user), AsyncMock())
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_operator_dependency_accepts_authorized_operator(self):
        user = SimpleNamespace(role="operator")
        with patch(
            "app.api.deps.section_permissions.user_has_section",
            new=AsyncMock(return_value=True),
        ):
            result = await deps.get_operator_user((SimpleNamespace(), user), AsyncMock())
        self.assertIs(result, user)


if __name__ == "__main__":
    unittest.main()
