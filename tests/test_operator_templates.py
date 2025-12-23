"""Testy obecnosci informacji o wersji w panelu operatora."""

import unittest
from pathlib import Path


class OperatorTemplateTests(unittest.TestCase):
    def test_index_contains_build_info(self) -> None:
        content = Path("app/templates/operator/index.html").read_text(encoding="utf-8")
        self.assertIn('class="op-build-info"', content)
        self.assertIn("Wersja 0.1.0", content)
        self.assertIn("Aktualizacja 2025-12-23", content)

    def test_settings_contains_build_info(self) -> None:
        content = Path("app/templates/operator/settings.html").read_text(encoding="utf-8")
        self.assertIn('class="op-build-info"', content)
        self.assertIn("Wersja 0.1.0", content)
        self.assertIn("Aktualizacja 2025-12-23", content)


if __name__ == "__main__":
    unittest.main()
