import unittest

from fastapi import HTTPException

from app.api import deps


class AuthDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_current_user_id_requires_header(self):
        with self.assertRaises(HTTPException) as ctx:
            await deps.get_current_user_id(None)
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_get_current_user_id_accepts_numeric_header(self):
        self.assertEqual(await deps.get_current_user_id(42), 42)


if __name__ == "__main__":
    unittest.main()
