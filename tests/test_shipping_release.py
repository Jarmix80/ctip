"""Testy izolowanego wydania produkcyjnego modułu Shipping."""

from __future__ import annotations

import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


class ShippingReleaseTests(unittest.TestCase):
    """Weryfikuje kanoniczną migrację i brak prototypowych rewizji."""

    def test_shipping_jest_jedynym_headem_alembic(self) -> None:
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))

        self.assertEqual(scripts.get_heads(), ["f9a0b1c2d3e4"])
        revision = scripts.get_revision("f9a0b1c2d3e4")
        self.assertIsNotNone(revision)
        self.assertEqual(revision.down_revision, "8a4d1f7c2b90")

    def test_release_nie_zawiera_prototypowych_migracji_shipping(self) -> None:
        versions = Path("alembic/versions")
        obsolete_revisions = (
            "f1a2b3c4d5e6",
            "a4b5c6d7e8f9",
            "b5c6d7e8f901",
            "c6d7e8f901a2",
            "d7e8f901a2b3",
            "e8f901a2b3c4",
        )

        present_names = {path.name for path in versions.glob("*.py")}
        for revision in obsolete_revisions:
            self.assertFalse(any(name.startswith(revision) for name in present_names))

    def test_migracja_blokuje_adopte_nieznanego_prototypu(self) -> None:
        migration = Path("alembic/versions/f9a0b1c2d3e4_add_shipping_prod.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("_assert_clean_target()", migration)
        self.assertIn("Migracja produkcyjna Shipping wymaga pustego celu", migration)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS pg_trgm", migration)


if __name__ == "__main__":
    unittest.main()
