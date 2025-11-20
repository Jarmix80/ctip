import unittest

import collector_full


class CollectorContextTests(unittest.TestCase):
    """Sprawdza, czy kolektor poprawnie rozdziela równoległe połączenia."""

    def setUp(self):
        self.client = collector_full.CTIPClient()
        self.client.conn = object()
        self.client.logged_in = True
        self.client.lookup_ivr_map = lambda ext: (None, None)

        self._originals: dict[str, object] = {}
        self.call_seq = 0
        self.inserted_ids: dict[str, list[int]] = {}
        self.connected: list[tuple[int, str | None]] = []
        self.ended: list[int] = []
        self.enqueued: list[tuple[int, str, str, dict]] = []

        self._patch("call_insert", self._fake_call_insert)
        self._patch("call_update_connected", self._fake_call_update_connected)
        self._patch("call_update_ended", self._fake_call_update_ended)
        self._patch("ev_insert", self._fake_ev_insert)
        self._patch("enqueue_sms", self._fake_enqueue_sms)

    def tearDown(self):
        for name, original in self._originals.items():
            setattr(collector_full, name, original)

    def _patch(self, name: str, func):
        self._originals[name] = getattr(collector_full, name)
        setattr(collector_full, name, func)

    def _fake_call_insert(self, conn, ext, number, direction, state):
        self.call_seq += 1
        call_id = self.call_seq
        key = ext or ""
        self.inserted_ids.setdefault(key, []).append(call_id)
        return call_id

    def _fake_call_update_connected(self, conn, call_id, answered_by):
        self.connected.append((call_id, answered_by))

    def _fake_call_update_ended(self, conn, call_id):
        self.ended.append(call_id)

    def _fake_ev_insert(self, conn, call_id, typ, ext, number, payload_bytes):
        # Dla testu nie potrzebujemy dodatkowych operacji.
        return None

    def _fake_enqueue_sms(self, conn, call_id, dest, text, **kwargs):
        self.enqueued.append((call_id, dest, text, kwargs))
        return None

    def test_parallel_calls_do_not_mix_contexts(self):
        self.client.handle_packet(b"RING 101 555123")
        self.client.handle_packet(b"RING 102 600111")
        self.client.handle_packet(b"CONN 101 555123")
        self.client.handle_packet(b"CONN 102 600111")
        self.client.handle_packet(b"REL 101 OK")
        self.client.handle_packet(b"REL 102 OK")

        call_id_101 = self.inserted_ids["101"][0]
        call_id_102 = self.inserted_ids["102"][0]

        self.assertNotEqual(call_id_101, call_id_102)
        self.assertIn((call_id_101, "101"), self.connected)
        self.assertIn((call_id_102, "102"), self.connected)
        self.assertIn(call_id_101, self.ended)
        self.assertIn(call_id_102, self.ended)

    def test_ivr_map_hit_enqueues_sms_with_metadata(self):
        lookup_called_with: list[str] = []

        def fake_lookup(ext: str):
            lookup_called_with.append(ext)
            return 9, "Instrukcja"

        self.client.lookup_ivr_map = fake_lookup
        self.client.handle_packet(b"RING 500 +48600111222")

        self.assertEqual(lookup_called_with, ["500"])
        self.assertEqual(len(self.enqueued), 1)
        call_id, dest, text, meta = self.enqueued[0]
        self.assertEqual(call_id, 1)
        self.assertEqual(dest, "+48600111222")
        self.assertEqual(text, "Instrukcja")
        self.assertEqual(meta.get("ext"), "500")
        self.assertEqual(meta.get("digit"), 9)


if __name__ == "__main__":
    unittest.main()
