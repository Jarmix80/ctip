import unittest

from pydantic import ValidationError

from app.schemas.sms import SmsSendRequest


class SmsSchemaTests(unittest.TestCase):
    def test_requires_text_or_template(self):
        with self.assertRaises(ValueError):
            SmsSendRequest(dest="+48123456789")

    def test_accepts_manual_text(self):
        req = SmsSendRequest(dest="+48123456789", text="Hello")
        self.assertEqual(req.text, "Hello")

    def test_accepts_template(self):
        req = SmsSendRequest(dest="+48123456789", template_id=5)
        self.assertEqual(req.template_id, 5)

    def test_normalizes_international_prefix(self):
        req = SmsSendRequest(dest="0048 123 456 789", text="Hello")
        self.assertEqual(req.dest, "+48123456789")

    def test_normalizes_leading_zero(self):
        req = SmsSendRequest(dest="0600700800", text="Hello")
        self.assertEqual(req.dest, "+48600700800")

    def test_normalizes_plus_zero_prefix(self):
        req = SmsSendRequest(dest="+0600700800", text="Hello")
        self.assertEqual(req.dest, "+48600700800")

    def test_rejects_invalid_number(self):
        with self.assertRaises(ValidationError):
            SmsSendRequest(dest="123", text="Hello")


if __name__ == "__main__":
    unittest.main()
