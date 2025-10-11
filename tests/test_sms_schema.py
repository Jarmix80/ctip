import unittest

from app.schemas.sms import SmsSendRequest


class SmsSchemaTests(unittest.TestCase):
    def test_requires_text_or_template(self):
        with self.assertRaises(ValueError):
            SmsSendRequest(dest="123")

    def test_accepts_manual_text(self):
        req = SmsSendRequest(dest="123", text="Hello")
        self.assertEqual(req.text, "Hello")

    def test_accepts_template(self):
        req = SmsSendRequest(dest="123", template_id=5)
        self.assertEqual(req.template_id, 5)


if __name__ == "__main__":
    unittest.main()
