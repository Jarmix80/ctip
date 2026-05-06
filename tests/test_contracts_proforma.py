# ruff: noqa: E402

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services import contracts_proforma
from app.services.contracts_proforma import _render_proforma_pdf, build_proforma_download_filename


def test_render_proforma_pdf_contains_invoice_layout_text():
    invoice = {
        "document_title": "Faktura Pro Forma",
        "document_number": "19/proforma/2026",
        "place_of_issue": "Komorniki",
        "service_date": "21.04.2026",
        "issue_date": "21.04.2026",
        "payment_due_date": "05.05.2026",
        "payment_method": "Gotówka",
        "buyer": {
            "name": '"GRENKELEASING" SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ',
            "street": "ul. abpa Antoniego Baraniaka 88",
            "postal_code": "61-131",
            "city": "Poznań",
            "country_code": "PL",
            "nip": "782-22-75-815",
        },
        "seller": {
            "name": "KSERO - PARTNER MIKOŁAJ FRĄSZCZAK SPÓŁKA KOMANDYTOWA",
            "street": "ul. Fabianowska 165",
            "postal_code": "62-052",
            "city": "Komorniki",
            "nip": "7773404157",
            "bank_account": "PKO BP S.A. 33102040270000190218474209",
        },
        "line_items": [
            {
                "lp": 1,
                "name": "Ricoh MP 401",
                "serial_number": "T605H900327",
                "internal_number": "KP/4066",
                "quantity": "1,00",
                "unit": "szt.",
                "net_price": "1 920,00 zł",
                "net_value": "1 920,00 zł",
                "vat_rate": "23 %",
                "vat_value": "441,60 zł",
                "gross_value": "2 361,60 zł",
            }
        ],
        "totals": {
            "net": "1 920,00 zł",
            "vat": "441,60 zł",
            "gross": "2 361,60 zł",
            "paid": "0,00 zł",
            "remaining": "2 361,60 zł",
            "gross_words": "dwa tysiące trzysta sześćdziesiąt jeden złotych 60/100 gr.",
        },
        "notes": ["FLOW formularz 24"],
        "issuer": "Marcin Jarmuszkiewicz",
    }

    pdf_bytes = _render_proforma_pdf(invoice)

    assert pdf_bytes.startswith(b"%PDF")
    reader = PdfReader(BytesIO(pdf_bytes))
    text = reader.pages[0].extract_text() or ""
    assert "Faktura Pro Forma nr: 19/proforma/2026" in text
    assert "GRENKELEASING" in text
    assert "Ricoh MP 401" in text
    assert "FLOW formularz 24" in text
    assert "Numer rachunku bankowego:" in text


def test_build_proforma_download_filename_uses_document_alias():
    assert build_proforma_download_filename("20/proforma/2026") == "20_proforma_2026.pdf"
    assert build_proforma_download_filename("  20 / proforma / 2026  ") == "20_proforma_2026.pdf"
    assert build_proforma_download_filename("", fallback_id=70001) == "proforma_70001.pdf"


def test_render_proforma_pdf_keeps_seven_line_items_visible():
    base_item = {
        "name": "Ricoh MP 401",
        "serial_number": "T605H900327",
        "internal_number": "KP/4066",
        "quantity": "1,00",
        "unit": "szt.",
        "net_price": "100,00 zł",
        "net_value": "100,00 zł",
        "vat_rate": "23 %",
        "vat_value": "23,00 zł",
        "gross_value": "123,00 zł",
    }
    invoice = {
        "document_title": "Faktura Pro Forma",
        "document_number": "20/proforma/2026",
        "place_of_issue": "Komorniki",
        "service_date": "21.04.2026",
        "issue_date": "21.04.2026",
        "payment_due_date": "05.05.2026",
        "payment_method": "Gotówka",
        "buyer": {
            "name": '"GRENKELEASING" SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ',
            "street": "ul. abpa Antoniego Baraniaka 88",
            "postal_code": "61-131",
            "city": "Poznań",
            "country_code": "PL",
            "nip": "782-22-75-815",
        },
        "seller": {
            "name": "KSERO - PARTNER MIKOŁAJ FRĄSZCZAK SPÓŁKA KOMANDYTOWA",
            "street": "ul. Fabianowska 165",
            "postal_code": "62-052",
            "city": "Komorniki",
            "nip": "7773404157",
            "bank_account": "PKO BP S.A. 33102040270000190218474209",
        },
        "line_items": [{"lp": index, **base_item} for index in range(1, 8)],
        "totals": {
            "net": "700,00 zł",
            "vat": "161,00 zł",
            "gross": "861,00 zł",
            "paid": "0,00 zł",
            "remaining": "861,00 zł",
            "gross_words": "osiemset sześćdziesiąt jeden złotych 00/100 gr.",
        },
        "notes": ["FLOW formularz 25"],
        "issuer": "Marcin Jarmuszkiewicz",
    }

    pdf_bytes = _render_proforma_pdf(invoice)
    reader = PdfReader(BytesIO(pdf_bytes))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    assert "7." in text


def test_resolve_reportlab_font_names_prefers_windows_verdana():
    contracts_proforma._resolve_reportlab_font_names.cache_clear()
    verdana_files = {
        "c:/windows/fonts/verdana.ttf",
        "c:/windows/fonts/verdanab.ttf",
        "c:/windows/fonts/verdanai.ttf",
        "c:/windows/fonts/verdanaz.ttf",
    }

    def fake_exists(path_obj):
        normalized = str(path_obj).replace("\\", "/").lower()
        return normalized in verdana_files

    with (
        patch.object(Path, "exists", fake_exists),
        patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
        patch("reportlab.pdfbase.pdfmetrics.registerFont"),
        patch("reportlab.pdfbase.ttfonts.TTFont", side_effect=lambda name, _path: object()),
    ):
        names = contracts_proforma._resolve_reportlab_font_names()

    assert names.regular == "VerdanaCTIP"
    assert names.bold == "VerdanaCTIP-Bold"
    assert names.italic == "VerdanaCTIP-Italic"
    assert names.bold_italic == "VerdanaCTIP-BoldItalic"
    contracts_proforma._resolve_reportlab_font_names.cache_clear()


def test_resolve_reportlab_font_names_falls_back_to_helvetica_when_fonts_missing():
    contracts_proforma._resolve_reportlab_font_names.cache_clear()

    with patch.object(Path, "exists", return_value=False):
        names = contracts_proforma._resolve_reportlab_font_names()

    assert names.regular == "Helvetica"
    assert names.bold == "Helvetica-Bold"
    assert names.italic == "Helvetica-Oblique"
    assert names.bold_italic == "Helvetica-BoldOblique"
    contracts_proforma._resolve_reportlab_font_names.cache_clear()
