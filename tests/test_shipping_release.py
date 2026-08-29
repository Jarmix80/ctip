"""Testy izolowanego wydania produkcyjnego modułu Shipping."""

from __future__ import annotations

import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.services.section_permissions import default_sections_for_role, normalize_sections


class ShippingReleaseTests(unittest.TestCase):
    """Weryfikuje kanoniczną migrację i brak prototypowych rewizji."""

    def test_infoservices_jest_jedynym_headem_alembic(self) -> None:
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))

        self.assertEqual(scripts.get_heads(), ["c3d5e7f9a1b2"])
        revision = scripts.get_revision("c3d5e7f9a1b2")
        self.assertIsNotNone(revision)
        self.assertEqual(revision.down_revision, "a7c4e2f9b1d3")

        mailbox_revision = scripts.get_revision("a7c4e2f9b1d3")
        self.assertIsNotNone(mailbox_revision)
        self.assertEqual(mailbox_revision.down_revision, "f9a0b1c2d3e4")

        shipping_revision = scripts.get_revision("f9a0b1c2d3e4")
        self.assertIsNotNone(shipping_revision)
        self.assertEqual(shipping_revision.down_revision, "d8f1a2b3c4e5")

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

    def test_skrypt_wdrozenia_restartuje_wylacznie_panel_webowy(self) -> None:
        script = Path("scripts/windows/deploy_shipping_prod_2026-08-27.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            '[string]$ReleaseBranch = "release/shipping-prod-current-2026-08-27"',
            script,
        )
        self.assertIn(
            '[string]$ExpectedCurrentCommit = "e17ad41c2651039d8b00464ddc6dc86a5549b240"',
            script,
        )
        self.assertIn('[string]$ExpectedAlembicCurrent = "d8f1a2b3c4e5"', script)
        self.assertIn("$previousErrorActionPreference = $ErrorActionPreference", script)
        self.assertIn('$ErrorActionPreference = "Continue"', script)
        self.assertIn("$ErrorActionPreference = $previousErrorActionPreference", script)
        self.assertIn('Stop-Service -Name "CTIP-Web"', script)
        self.assertNotIn('Stop-Service -Name "CollectorService"', script)
        self.assertNotIn('Stop-Service -Name "CTIP-SMS"', script)
        self.assertNotIn('Stop-Service -Name "CTIP-FormsPublic"', script)
        self.assertIn('"-m", "alembic", "upgrade", $ExpectedAlembicHead', script)
        self.assertIn('$env:SHIPPING_ENABLED = "false"', script)
        self.assertIn('$env:SHIPPING_CATALOG_MUTATIONS_ENABLED = "false"', script)
        self.assertIn("$validationFlags[$name]", script)
        self.assertIn('$_.Name -ne ".gitkeep"', script)
        self.assertIn("Remove-Item -Path $candidateReportDirectory", script)
        self.assertIn("New-Item -ItemType Junction", script)
        self.assertIn("$removeJunctionCommand", script)
        self.assertIn("& cmd.exe /d /c $removeJunctionCommand", script)
        self.assertLess(
            script.index("& cmd.exe /d /c $removeJunctionCommand"),
            script.index("git worktree remove --force"),
        )
        self.assertIn("if (-not $Apply)", script)

    def test_skrypt_pelnego_uruchomienia_zachowuje_faze_gotowosci(self) -> None:
        script = Path("scripts/windows/deploy_shipping_full_prod_2026-08-28.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            '[string]$ExpectedCurrentCommit = "c206a876aeeaa5b0ed802fb24feb03b323cfca0b"',
            script,
        )
        self.assertIn('[string]$ExpectedAlembicHead = "a7c4e2f9b1d3"', script)
        self.assertIn(
            '[string]$ReleaseBranch = "feature/shipping-full-rollout-2026-08-28"',
            script,
        )
        self.assertIn(
            'Assert-EnvEquals -Name "SHIPPING_FULFILLMENT_ENABLED" -Expected "false"', script
        )
        self.assertIn('Assert-EnvEquals -Name "DPD_ENABLED" -Expected "false"', script)
        self.assertIn('Stop-Service -Name "CTIP-Web"', script)
        self.assertNotIn('Stop-Service -Name "CollectorService"', script)
        self.assertNotIn('Stop-Service -Name "CTIP-SMS"', script)
        self.assertNotIn('Stop-Service -Name "CTIP-FormsPublic"', script)
        self.assertNotIn('"alembic", "upgrade"', script)
        self.assertIn('"http://127.0.0.1:8002/shipping"', script)
        self.assertIn('"http://127.0.0.1:8002/shipping/legacy"', script)
        self.assertIn('"http://127.0.0.1:8002/shipping/v2"', script)
        self.assertIn("if (-not $Apply)", script)

    def test_shipping_wymaga_jawnego_nadania_sekcji(self) -> None:
        self.assertNotIn("shipping", default_sections_for_role("admin"))
        self.assertNotIn("shipping", default_sections_for_role("operator"))
        self.assertNotIn(
            "shipping",
            normalize_sections(["admin", "operator"], role="admin"),
        )
        self.assertIn(
            "shipping",
            normalize_sections(["admin", "shipping"], role="admin"),
        )
        self.assertIn(
            "shipping",
            normalize_sections(["operator", "shipping"], role="operator"),
        )

    def test_operacje_mutacyjne_maja_blokady_a_synchronizacja_jest_odczytowa(self) -> None:
        routes = Path("app/api/routes/admin_shipping.py").read_text(encoding="utf-8")

        self.assertEqual(routes.count("@router.post"), 14)
        self.assertEqual(routes.count("_require_catalog_mutations()"), 6)
        self.assertEqual(routes.count("_require_fulfillment()"), 9)
        self.assertIn('@router.post("/tracking/sync"', routes)
        self.assertIn("synchronize_dpd_infoservices", routes)


if __name__ == "__main__":
    unittest.main()
