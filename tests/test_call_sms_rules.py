import unittest

from app.services.call_sms_config import CallSmsConfig
from app.services.call_sms_rules import (
    is_polish_mobile,
    parse_after_hours_exts,
    parse_opt_out_numbers,
    pick_call_sms_scenarios,
)


class CallSmsRulesTests(unittest.TestCase):
    """Testy regul automatycznych SMS dla dzwoniacych."""

    def test_is_polish_mobile_accepts_mobile_prefix(self):
        self.assertTrue(is_polish_mobile("+48600111222"))
        self.assertFalse(is_polish_mobile("+48221234567"))
        self.assertFalse(is_polish_mobile("+49301234567"))

    def test_parse_opt_out_numbers_normalizes_values(self):
        raw = "+48 600 111 222\n600333444\ninvalid"
        expected = {"+48600111222", "+48600333444"}
        self.assertEqual(parse_opt_out_numbers(raw), expected)

    def test_parse_after_hours_exts_normalizes_values(self):
        raw = "500\n501_, 502"
        expected = {"500", "501", "502"}
        self.assertEqual(parse_after_hours_exts(raw), expected)

    def test_pick_call_sms_scenarios_inbound_repeat(self):
        config = CallSmsConfig(
            enabled=True,
            inbound_enabled=True,
            inbound_answered_enabled=True,
            inbound_answered_text="Dziekujemy",
            inbound_repeat_answered_enabled=True,
            inbound_repeat_answered_text="Ponowne polaczenie",
        )
        scenarios = pick_call_sms_scenarios(config, "IN", "ANSWERED", True)
        codes = [scenario.code for scenario in scenarios]
        self.assertEqual(codes, ["inbound_repeat_answered"])

    def test_pick_call_sms_scenarios_repeat_fallback_to_base(self):
        config = CallSmsConfig(
            enabled=True,
            inbound_enabled=True,
            inbound_answered_enabled=True,
            inbound_answered_text="Dziekujemy",
            inbound_repeat_answered_enabled=False,
            inbound_repeat_answered_text="Ponowne polaczenie",
        )
        scenarios = pick_call_sms_scenarios(config, "IN", "ANSWERED", True)
        codes = [scenario.code for scenario in scenarios]
        self.assertEqual(codes, ["inbound_answered"])


if __name__ == "__main__":
    unittest.main()
