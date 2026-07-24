from __future__ import annotations

import unittest

from cryptography.fernet import Fernet

from scripts.reset_test_security import _rotate_payload


class ResetTestSecurityTests(unittest.TestCase):
    def test_rotate_payload_uses_test_key(self) -> None:
        source_cipher = Fernet(Fernet.generate_key())
        target_cipher = Fernet(Fernet.generate_key())
        payload = source_cipher.encrypt(b'{"company_name":"Test"}').decode("ascii")

        rotated, changed = _rotate_payload(
            payload,
            source_cipher=source_cipher,
            target_cipher=target_cipher,
        )

        self.assertTrue(changed)
        self.assertEqual(target_cipher.decrypt(rotated.encode("ascii")), b'{"company_name":"Test"}')

    def test_rotate_payload_is_idempotent(self) -> None:
        source_cipher = Fernet(Fernet.generate_key())
        target_cipher = Fernet(Fernet.generate_key())
        payload = target_cipher.encrypt(b'{"company_name":"Test"}').decode("ascii")

        rotated, changed = _rotate_payload(
            payload,
            source_cipher=source_cipher,
            target_cipher=target_cipher,
        )

        self.assertFalse(changed)
        self.assertEqual(rotated, payload)


if __name__ == "__main__":
    unittest.main()
