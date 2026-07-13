import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api import deps


class AuthDependencyTests(unittest.IsolatedAsyncioTestCase):
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
