import unittest

from app.services.sms_provider import HttpSmsProvider


class DummyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class DummyClient:
    def __init__(self, response: dict):
        self.response = response
        self.last_payload: dict | None = None

    def __enter__(self) -> "DummyClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, path: str, json: dict) -> DummyResponse:
        self.last_payload = json
        return DummyResponse(self.response)


class SmsProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = HttpSmsProvider(
            "https://api.example.com",
            None,
            "Sender",
            username="user",
            password="pass",
            test_mode=False,
        )

    def test_unique_id_from_sms_id_is_padded(self) -> None:
        unique_id = self.provider._build_unique_id_from_sms_id(7)
        self.assertEqual(unique_id, "CTIP-000007")

    def test_unique_id_sanitizes_custom_value(self) -> None:
        unique_id = self.provider._sanitize_unique_id("abc-12")
        self.assertEqual(unique_id, "CTIP-0ABC12")

    def test_send_sms_injects_unique_id(self) -> None:
        client = DummyClient({"success": True, "items": [{"status": "queued", "id": "MSG"}]})
        self.provider._client = lambda: client  # type: ignore[assignment]
        result = self.provider.send_sms(
            "+48123123123",
            "Treść testowa",
            metadata={"sms_id": 42},
        )
        self.assertTrue(result.success)
        assert client.last_payload is not None
        self.assertIn("unique_id", client.last_payload)
        self.assertEqual(client.last_payload["unique_id"], ["CTIP-000042"])

    def test_send_sms_sets_utf_for_non_ascii(self) -> None:
        client = DummyClient({"success": True, "items": [{"status": "queued", "id": "MSG"}]})
        provider = HttpSmsProvider(
            "https://api.example.com",
            None,
            "Sender",
            username="user",
            password="pass",
            sms_type="eco+",
            test_mode=False,
        )
        provider._client = lambda: client  # type: ignore[assignment]
        provider.send_sms("+48123123123", "Test \u0105")
        assert client.last_payload is not None
        self.assertTrue(client.last_payload.get("utf"))
        self.assertEqual(client.last_payload.get("type"), "full")

    def test_send_sms_forces_full_for_long_gsm_text(self) -> None:
        client = DummyClient({"success": True, "items": [{"status": "queued", "id": "MSG"}]})
        provider = HttpSmsProvider(
            "https://api.example.com",
            None,
            "Sender",
            username="user",
            password="pass",
            sms_type="eco+",
            test_mode=False,
        )
        provider._client = lambda: client  # type: ignore[assignment]
        provider.send_sms("+48123123123", "A" * 161)
        assert client.last_payload is not None
        self.assertEqual(client.last_payload.get("type"), "full")
        self.assertNotIn("utf", client.last_payload)


if __name__ == "__main__":
    unittest.main()
