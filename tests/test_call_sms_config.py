import unittest

from app.services.call_sms_config import APP_LINK, CallSmsConfig


class CallSmsConfigDefaultsTests(unittest.TestCase):
    """Testy domyslnej konfiguracji SMS dla dzwoniacych."""

    def test_default_texts_include_app_link(self):
        config = CallSmsConfig()
        texts = [
            config.inbound_answered_text,
            config.inbound_missed_text,
            config.inbound_repeat_answered_text,
            config.inbound_repeat_missed_text,
            config.outbound_answered_text,
            config.outbound_missed_text,
            config.outbound_repeat_answered_text,
            config.outbound_repeat_missed_text,
            config.after_hours_text,
        ]
        for text in texts:
            self.assertIn(APP_LINK, text)

    def test_after_hours_exts_default(self):
        config = CallSmsConfig()
        self.assertEqual(config.after_hours_exts, "500")


if __name__ == "__main__":
    unittest.main()
