import unittest
from unittest.mock import patch

from app.services.contract_pdf_parser import extract_contract_data_from_text, parse_contract_pdf


class ContractPdfParserTests(unittest.TestCase):
    """Testy ekstrakcji danych umowy z tekstu PDF."""

    def test_prefers_najemca_nip_over_financing_footer(self):
        text = """
        NAJEMCA
        KRS / inny rejestr NIP: 525-000-00-09
        FINANSUJĄCY GRENKELEASING Sp. z o.o. NIP 782-22-75-815
        """

        data = extract_contract_data_from_text(text)

        self.assertEqual(data.nip, "5250000009")
        self.assertIn("5250000009", data.nips_found)
        self.assertIn("7822275815", data.nips_found)

    def test_ignores_only_financing_nip_when_no_customer_data(self):
        text = "FINANSUJĄCY GRENKELEASING Sp. z o.o. NIP 782-22-75-815"

        data = extract_contract_data_from_text(text)

        self.assertIsNone(data.nip)
        self.assertEqual(data.nips_found, ("7822275815",))

    def test_extracts_contract_number_from_same_line(self):
        text = "NR UMOWY: FR/2026/000123"

        data = extract_contract_data_from_text(text)

        self.assertEqual(data.contract_number, "FR/2026/000123")

    def test_extracts_contract_number_from_next_line(self):
        text = "NR UMOWY\nFR-2026-000124\nNR WNIOSKU"

        data = extract_contract_data_from_text(text)

        self.assertEqual(data.contract_number, "FR-2026-000124")

    def test_template_labels_without_value_return_none(self):
        text = "NR UMOWY\nNR WNIOSKU\nDODATKOWE OZNACZENIE"

        data = extract_contract_data_from_text(text)

        self.assertIsNone(data.contract_number)
        self.assertEqual(data.contract_candidates, ())

    def test_parse_contract_pdf_uses_text_extraction(self):
        with patch(
            "app.services.contract_pdf_parser.extract_text_from_pdf",
            return_value="NR UMOWY: FR/2026/000125 NIP: 526-104-08-28",
        ):
            data = parse_contract_pdf("/tmp/dummy.pdf")

        self.assertEqual(data.contract_number, "FR/2026/000125")
        self.assertEqual(data.nip, "5261040828")


if __name__ == "__main__":
    unittest.main()
