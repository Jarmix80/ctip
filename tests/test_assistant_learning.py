# ruff: noqa: E402

"""Testy mechanizmów uczenia i heurystyk NL CTIP AI Asystenta."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.assistant_learning import (
    build_learning_prompt_context,
    infer_business_intent_from_prompt,
    merge_learning_preferences,
)
from app.services.assistant_tools import AssistantToolResult


class AssistantLearningTests(unittest.TestCase):
    def test_infers_devices_by_company_from_natural_language(self) -> None:
        inferred = infer_business_intent_from_prompt("Wyswietl urzadzenia firmy Steico")
        self.assertIsNotNone(inferred)
        self.assertEqual(inferred["intent"], "devices_by_company")
        self.assertEqual(inferred["company_name"], "Steico")

    def test_infers_monthly_average_with_custom_period(self) -> None:
        inferred = infer_business_intent_from_prompt(
            "Pokaz ile srednio miesiecznie drukuja urzadzenia model MPC3004 za 6 miesiecy"
        )
        self.assertIsNotNone(inferred)
        self.assertEqual(inferred["intent"], "monthly_average_print_by_model")
        self.assertEqual(inferred["model_name"].upper(), "MPC3004")
        self.assertEqual(inferred["months_back"], 6)

    def test_merge_learning_preferences_updates_stats_and_aliases(self) -> None:
        result = AssistantToolResult(
            tool_name="firebird_business_read",
            status="success",
            payload={
                "intent": "devices_by_company",
                "criteria": {"company_name": "Steico"},
                "rows": [],
            },
            row_count=0,
            generated_sql=None,
            error_message=None,
            duration_ms=4,
        )
        merged = merge_learning_preferences(
            {},
            prompt="Wyswietl urzadzenia firmy steico",
            tool_results=[result],
        )
        learning = merged.get("business_learning")
        self.assertIsInstance(learning, dict)
        counts = learning.get("intent_success_counts")
        self.assertEqual(counts["devices_by_company"], 1)
        aliases = learning.get("company_aliases")
        self.assertEqual(aliases.get("steico"), "Steico")

    def test_build_learning_prompt_context(self) -> None:
        context = build_learning_prompt_context(
            {
                "business_learning": {
                    "intent_success_counts": {"devices_by_company": 3},
                    "company_aliases": {"steico": "Steico Sp. z o.o."},
                    "model_aliases": {"mpc3004": "MP C3004"},
                }
            }
        )
        self.assertIn("Statystyki trafnych intentów", context)
        self.assertIn("steico->Steico Sp. z o.o.", context)
        self.assertIn("mpc3004->MP C3004", context)

    def test_infers_top_models_intent(self) -> None:
        inferred = infer_business_intent_from_prompt(
            "Pokaz top modele po liczbie wydrukow za 3 miesiecy"
        )
        self.assertIsNotNone(inferred)
        self.assertEqual(inferred["intent"], "top_models_by_volume")
        self.assertEqual(inferred["months_back"], 3)

    def test_infers_company_print_summary(self) -> None:
        inferred = infer_business_intent_from_prompt(
            "Ile miesiecznie drukuje firma Steico za 6 miesiecy"
        )
        self.assertIsNotNone(inferred)
        self.assertEqual(inferred["intent"], "company_monthly_print_summary")
        self.assertEqual(inferred["company_name"], "Steico")
        self.assertEqual(inferred["months_back"], 6)

    def test_infers_serial_history(self) -> None:
        inferred = infer_business_intent_from_prompt(
            "Pokaz historie wydrukow dla serial RNP12345 za 4 miesiecy"
        )
        self.assertIsNotNone(inferred)
        self.assertEqual(inferred["intent"], "device_monthly_print_by_serial")
        self.assertEqual(inferred["serial_number"].upper(), "RNP12345")
        self.assertEqual(inferred["months_back"], 4)

    def test_infers_active_devices_on_contracts(self) -> None:
        inferred = infer_business_intent_from_prompt(
            "Wyswietl mi wszystkie urzadzenia aktywne na umowach"
        )
        self.assertIsNotNone(inferred)
        self.assertEqual(inferred["intent"], "active_devices_on_contracts")

    def test_infers_active_devices_on_contracts_count(self) -> None:
        inferred = infer_business_intent_from_prompt("Podaj mi ilość urządzeń aktywnych na umowach")
        self.assertIsNotNone(inferred)
        self.assertEqual(inferred["intent"], "active_devices_on_contracts_count")


if __name__ == "__main__":
    unittest.main()
