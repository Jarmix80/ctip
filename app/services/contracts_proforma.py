"""Tworzenie i podglad proform dla workflow FLOW."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from app.services.contracts_dashboard import (
    _firebird_connection,
    _truncate_text,
    find_warehouse_item_in_firebird,
    firebird_writes_enabled,
    normalize_device_key,
    synchronize_device_from_sheet_row,
)
from app.services.contracts_workflow import (
    WORKFLOW_DEVICE_SOURCE_FIREBIRD_SERIAL,
    WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
    normalize_workflow_device_source_type,
)

MONEY_PRECISION = Decimal("0.0001")
DISPLAY_PRECISION = Decimal("0.01")
DEFAULT_VAT_RATE = Decimal("23")
DEFAULT_PAYMENT_METHOD = "Gotówka"
DEFAULT_PAYMENT_DAYS = 14
PROFORMA_PDF_DIR = Path("inbox/faktura/generated")
DOCUMENT_TITLE_BY_KIND = {
    "proforma": "Faktura Pro Forma",
}


@dataclass(frozen=True, slots=True)
class ReportlabFontNames:
    """Nazwy fontow ReportLab dopasowane do zatwierdzonego wygladu proformy."""

    regular: str
    bold: str
    italic: str
    bold_italic: str


@dataclass(slots=True)
class FirebirdWarehouseDetails:
    """Szczegoly pozycji magazynowej potrzebne do wystawienia proformy."""

    id_magazyn_table: int
    id_magazyn: int
    rodzaj: str | None
    indeks: str | None
    nazwa: str | None
    jm: str | None
    ilosc: Decimal | None
    cena_netto: Decimal | None
    cena_brutto: Decimal | None
    cena_z1: Decimal | None
    vat_stawka: str | None
    idvat: int | None
    marka: str | None
    model: str | None


@dataclass(slots=True)
class FirebirdClientDetails:
    """Dane kontrahenta z Firebird do naglowka dokumentu."""

    id_klient: int
    nazwa: str
    adres: str | None
    kod: str | None
    poczta: str | None
    nip: str | None
    telefon: str | None
    email: str | None
    kod_kraju: str | None


@dataclass(slots=True)
class FirebirdSellerDetails:
    """Dane sprzedawcy z tabeli FIRMA."""

    id_firma: int
    nazwa: str
    adres: str | None
    kod: str | None
    poczta: str | None
    miejsce_wyst: str | None
    nip: str | None
    telefon: str | None
    email: str | None
    bank: str | None
    konto: str | None


@dataclass(slots=True)
class FirebirdProformaWriteResult:
    """Wynik utworzenia proformy w Firebird."""

    id_faktura_table: int
    document_number: str
    preview_url: str
    line_count: int
    pdf_path: str | None = None


@dataclass(slots=True)
class FirebirdProformaDeleteResult:
    """Wynik usuniecia proformy z Firebird."""

    id_faktura_table: int
    deleted: bool
    deleted_lines: int
    pdf_deleted: bool


def build_proforma_preview_url(proforma_firebird_id: int, *, variant: str = "v1") -> str:
    """Buduje URL podgladu proformy w FLOW."""
    chosen_variant = "v1" if variant not in {"base", "v1"} else variant
    return f"/flow/proforma/{proforma_firebird_id}?variant={chosen_variant}"


def build_proforma_pdf_url(proforma_firebird_id: int) -> str:
    """Buduje URL backendowego pliku PDF proformy."""
    return f"/flow/proforma/{proforma_firebird_id}/pdf"


def build_proforma_pdf_storage_path(proforma_firebird_id: int) -> Path:
    """Zwraca docelowa sciezke zapisu PDF proformy."""
    return PROFORMA_PDF_DIR / f"proforma_{proforma_firebird_id}.pdf"


def delete_proforma_from_firebird(proforma_firebird_id: int) -> FirebirdProformaDeleteResult:
    """Usuwa proforme i jej pozycje z Firebird oraz kasuje zapisany PDF."""

    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    if proforma_firebird_id <= 0:
        raise ValueError("Brak poprawnego ID proformy Firebird do usuniecia.")

    connection = _firebird_connection()
    cursor = connection.cursor()
    deleted_lines = 0
    deleted = False
    try:
        cursor.execute(
            """
            SELECT FIRST 1 ID_FAKTURA_TABLE
            FROM FAKTURA
            WHERE ID_FAKTURA_TABLE = ? AND RODZAJ_DOK = 'proforma'
            """,
            (proforma_firebird_id,),
        )
        row = cursor.fetchone()
        if row and row[0] is not None:
            cursor.execute(
                "DELETE FROM FPOZYCJA WHERE ID_FAKTURA = ? AND RODZAJ_DOK = 'proforma'",
                (proforma_firebird_id,),
            )
            deleted_lines = int(getattr(cursor, "rowcount", 0) or 0)
            cursor.execute(
                "DELETE FROM FAKTURA WHERE ID_FAKTURA_TABLE = ? AND RODZAJ_DOK = 'proforma'",
                (proforma_firebird_id,),
            )
            deleted = int(getattr(cursor, "rowcount", 0) or 0) > 0
            connection.commit()
        else:
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    pdf_path = build_proforma_pdf_storage_path(proforma_firebird_id)
    pdf_deleted = False
    if pdf_path.exists():
        pdf_path.unlink()
        pdf_deleted = True

    return FirebirdProformaDeleteResult(
        id_faktura_table=proforma_firebird_id,
        deleted=deleted,
        deleted_lines=deleted_lines,
        pdf_deleted=pdf_deleted,
    )


def build_proforma_download_filename(
    document_number: str | None, *, fallback_id: int | None = None
) -> str:
    """Buduje bezpieczna nazwe pliku PDF na podstawie numeru dokumentu."""
    raw_value = str(document_number or "").strip()
    if raw_value:
        sanitized = re.sub(r'[\\/:*?"<>|]+', "_", raw_value)
        sanitized = re.sub(r"\s+", "_", sanitized)
        sanitized = re.sub(r"_+", "_", sanitized).strip("._ ")
        if sanitized:
            return f"{sanitized}.pdf"
    if fallback_id and fallback_id > 0:
        return f"proforma_{fallback_id}.pdf"
    return "proforma.pdf"


def create_proforma_from_workflow(
    *,
    form_request_id: int,
    firebird_client_id: int,
    selected_devices: list[dict[str, Any]],
    issuer_name: str,
) -> FirebirdProformaWriteResult:
    """Tworzy proforme w aktywnej konfiguracji Firebird dla sprawy workflow."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    if firebird_client_id <= 0:
        raise ValueError("Brak ID klienta Menadzera Serwisu dla proformy.")
    if not selected_devices:
        raise ValueError("Nie wybrano urzadzen do proformy.")

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        seller = _fetch_seller_details(cursor)
        client = _fetch_client_details(cursor, firebird_client_id)
        today = date.today()
        year = today.year
        document_no = _next_proforma_document_no(cursor, year)
        document_number = f"{document_no}/proforma/{year}"
        line_items = [
            _build_line_item(cursor, device=device, form_request_id=form_request_id)
            for device in selected_devices
        ]

        vat_buckets = _aggregate_vat_buckets(line_items)
        total_net = _sum_decimal(item["net_value"] for item in line_items)
        total_vat = _sum_decimal(item["vat_value"] for item in line_items)
        total_gross = _sum_decimal(item["gross_value"] for item in line_items)
        payment_due = today + timedelta(days=DEFAULT_PAYMENT_DAYS)

        faktura_columns = [
            "ID_ODDZIAL",
            "ID_FIRMA",
            "ID_MAGAZYN",
            "ID_KLIENT",
            "NAZWA",
            "ADRES",
            "KOD",
            "POCZTA",
            "NIP",
            "RODZAJ_DOK",
            "DOKUMENT",
            "NUMER",
            "DATA_SPRZ",
            "DATA_WYST",
            "DATA_PLAT",
            "PLATNOSC",
            "MIEJSCE_WYST",
            "WYSTAWIL",
            "ODEBRAL",
            "SUMA_NETTO",
            "SUMA_VAT",
            "SUMA_BRUTTO",
            "DO_ZAPLATY",
            "UWAGI",
            "STAN",
            "EDITCNT",
        ]
        faktura_values: list[Any] = [
            1,
            seller.id_firma,
            line_items[0]["warehouse_id_magazyn"],
            client.id_klient,
            _truncate_text(client.nazwa, 500),
            _truncate_text(client.adres, 150),
            _truncate_text(client.kod, 6),
            _truncate_text(client.poczta, 100),
            _truncate_text(client.nip, 15),
            "proforma",
            document_no,
            document_number,
            today,
            today,
            payment_due,
            DEFAULT_PAYMENT_METHOD,
            _truncate_text(seller.miejsce_wyst or seller.poczta, 100),
            _truncate_text(issuer_name, 100),
            "",
            total_net,
            total_vat,
            total_gross,
            total_gross,
            _truncate_text(f"FLOW formularz {form_request_id}", 2000),
            "",
            0,
        ]

        if len(vat_buckets) > 5:
            raise ValueError("Proforma obsluguje maksymalnie 5 roznych stawek VAT.")

        for index in range(1, 6):
            bucket = vat_buckets[index - 1] if index - 1 < len(vat_buckets) else None
            faktura_columns.extend([f"NETTO{index}", f"VAT{index}", f"BRUTTO{index}"])
            if bucket is None:
                faktura_values.extend([None, None, None])
            else:
                faktura_values.extend([bucket["net"], bucket["vat"], bucket["gross"]])

        placeholders = ", ".join("?" for _ in faktura_columns)
        cursor.execute(
            f"INSERT INTO FAKTURA ({', '.join(faktura_columns)}) VALUES ({placeholders})",
            tuple(faktura_values),
        )

        cursor.execute(
            """
            SELECT FIRST 1 ID_FAKTURA_TABLE
            FROM FAKTURA
            WHERE ID_FIRMA = ? AND NUMER = ?
            ORDER BY ID_FAKTURA_TABLE DESC
            """,
            (seller.id_firma, document_number),
        )
        faktura_row = cursor.fetchone()
        if not faktura_row or faktura_row[0] is None:
            raise RuntimeError("Nie udalo sie ustalic ID nowej proformy w Firebird.")
        faktura_id = int(faktura_row[0])

        for item in line_items:
            cursor.execute(
                """
                INSERT INTO FPOZYCJA (
                    ID_FAKTURA,
                    ID_FIRMA,
                    ID_KLIENT,
                    ID_MAGAZYN,
                    ID_MAGPOZ,
                    ID_SERIAL,
                    RODZAJ_DOK,
                    NUMER,
                    DATA_SPRZ,
                    RODZAJ,
                    INDEKS,
                    NAZWA,
                    CENA_NETTO,
                    CENA_BRUTTO,
                    CENA_Z,
                    ILOSC,
                    JM,
                    WARTOSC_NETTO,
                    WARTOSC_Z,
                    STAWKA_VAT,
                    VAT,
                    IDVAT,
                    WARTOSC_BRUTTO,
                    POBRANO,
                    UWAGI
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    faktura_id,
                    seller.id_firma,
                    client.id_klient,
                    item["warehouse_id_magazyn"],
                    item["warehouse_id_magazyn_table"],
                    item["serial_id"],
                    "proforma",
                    document_number,
                    today,
                    item["rodzaj"],
                    item["indeks"],
                    item["nazwa"],
                    item["net_price"],
                    item["gross_price"],
                    item["cost_price"],
                    item["quantity"],
                    item["unit"],
                    item["net_value"],
                    item["cost_value"],
                    item["vat_rate_display"],
                    item["vat_value"],
                    item["idvat"],
                    item["gross_value"],
                    item["quantity"],
                    _truncate_text(f"FLOW formularz {form_request_id}", 200),
                ),
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    preview_url = build_proforma_preview_url(faktura_id)
    pdf_path: str | None = None
    try:
        generated_pdf_path = ensure_proforma_pdf_file(faktura_id)
        pdf_path = generated_pdf_path.as_posix()
        preview_url = build_proforma_pdf_url(faktura_id)
    except Exception:
        # Nie blokujemy zapisu proformy przy problemach z eksportem PDF.
        pdf_path = None

    return FirebirdProformaWriteResult(
        id_faktura_table=faktura_id,
        document_number=document_number,
        preview_url=preview_url,
        line_count=len(line_items),
        pdf_path=pdf_path,
    )


def load_proforma_preview_data(proforma_firebird_id: int) -> dict[str, Any]:
    """Odczytuje dane proformy z Firebird i mapuje je do widoku HTML."""
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT *
            FROM FAKTURA
            WHERE ID_FAKTURA_TABLE = ?
            """,
            (proforma_firebird_id,),
        )
        faktura_row = cursor.fetchone()
        if not faktura_row:
            raise ValueError(f"Nie znaleziono proformy Firebird ID {proforma_firebird_id}.")
        faktura = _row_as_dict(cursor, faktura_row)
        seller = _fetch_seller_details(cursor, company_id=int(faktura.get("ID_FIRMA") or 1))
        line_items = _load_preview_line_items(cursor, proforma_firebird_id)
        total_gross = _decimal_or_zero(faktura.get("SUMA_BRUTTO"))
        total_paid = _decimal_or_zero(faktura.get("ZAPLACONO"))
        total_remaining = _decimal_or_zero(faktura.get("DO_ZAPLATY") or (total_gross - total_paid))

        notes: list[str] = []
        uwagi = _truncate_text(str(faktura.get("UWAGI") or ""), 2000)
        if uwagi:
            notes.append(uwagi)

        payment_method = str(faktura.get("PLATNOSC") or DEFAULT_PAYMENT_METHOD).strip()
        document_kind = str(faktura.get("RODZAJ_DOK") or "proforma").strip().lower()

        return {
            "document_title": DOCUMENT_TITLE_BY_KIND.get(document_kind, "Dokument handlowy"),
            "document_number": str(faktura.get("NUMER") or ""),
            "place_of_issue": str(
                faktura.get("MIEJSCE_WYST") or seller.miejsce_wyst or seller.poczta or ""
            ),
            "service_date": _format_date_pl(faktura.get("DATA_SPRZ")),
            "issue_date": _format_date_pl(faktura.get("DATA_WYST")),
            "payment_due_date": _format_date_pl(faktura.get("DATA_PLAT")),
            "payment_method": payment_method,
            "buyer": {
                "name": str(faktura.get("NAZWA") or ""),
                "street": str(faktura.get("ADRES") or ""),
                "postal_code": str(faktura.get("KOD") or ""),
                "city": str(faktura.get("POCZTA") or ""),
                "country_code": str(faktura.get("KOD_KRAJU") or "PL"),
                "nip": str(faktura.get("NIP") or ""),
            },
            "seller": {
                "name": seller.nazwa,
                "street": seller.adres or "",
                "postal_code": seller.kod or "",
                "city": seller.poczta or "",
                "nip": seller.nip or "",
                "bank_account": _join_bank_account(seller.bank, seller.konto),
            },
            "line_items": line_items,
            "totals": {
                "net": _format_currency(_decimal_or_zero(faktura.get("SUMA_NETTO"))),
                "vat": _format_currency(_decimal_or_zero(faktura.get("SUMA_VAT"))),
                "gross": _format_currency(total_gross),
                "paid": _format_currency(total_paid),
                "remaining": _format_currency(total_remaining),
                "gross_words": amount_to_polish_words(total_gross),
            },
            "notes": notes,
            "issuer": str(faktura.get("WYSTAWIL") or ""),
        }
    finally:
        cursor.close()
        connection.close()


def ensure_proforma_pdf_file(
    proforma_firebird_id: int, *, invoice: dict[str, Any] | None = None
) -> Path:
    """Generuje fizyczny plik PDF proformy i zwraca sciezke pliku."""
    output_path = build_proforma_pdf_storage_path(proforma_firebird_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(build_proforma_pdf_bytes(proforma_firebird_id, invoice=invoice))
    return output_path


def build_proforma_pdf_bytes(
    proforma_firebird_id: int, *, invoice: dict[str, Any] | None = None
) -> bytes:
    """Generuje zawartosc PDF proformy bez zapisu na dysku."""
    invoice_payload = (
        invoice if invoice is not None else load_proforma_preview_data(proforma_firebird_id)
    )
    return _render_proforma_pdf(invoice_payload)


def _render_proforma_pdf(invoice: dict[str, Any]) -> bytes:
    try:
        return _render_proforma_pdf_reportlab(invoice)
    except Exception:
        # Fallback zachowuje dzialanie endpointu nawet wtedy, gdy biblioteka PDF
        # albo fonty nie sa chwilowo dostepne w srodowisku uruchomieniowym.
        pass
    lines = _build_proforma_pdf_lines(invoice)
    return _build_simple_pdf(lines)


def _render_proforma_pdf_reportlab(invoice: dict[str, Any]) -> bytes:
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    width, height = (595.2756, 841.8898)
    fonts = _resolve_reportlab_font_names()
    font_regular = fonts.regular
    font_bold = fonts.bold
    font_italic = fonts.italic
    font_bold_italic = fonts.bold_italic
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(str(invoice.get("document_number") or "proforma"))

    buyer = invoice.get("buyer") if isinstance(invoice.get("buyer"), dict) else {}
    seller = invoice.get("seller") if isinstance(invoice.get("seller"), dict) else {}
    totals = invoice.get("totals") if isinstance(invoice.get("totals"), dict) else {}
    line_items = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    notes = invoice.get("notes") if isinstance(invoice.get("notes"), list) else []
    document_title = str(invoice.get("document_title") or "Dokument handlowy").strip()
    document_number = str(invoice.get("document_number") or "").strip()

    seller_city_line = " ".join(
        part.strip()
        for part in [str(seller.get("postal_code") or ""), str(seller.get("city") or "")]
        if part and part.strip()
    ).strip()
    buyer_city_line = " ".join(
        part.strip()
        for part in [str(buyer.get("postal_code") or ""), str(buyer.get("city") or "")]
        if part and part.strip()
    ).strip()

    seller_name_lines = simpleSplit(str(seller.get("name") or "").strip(), font_bold, 8.9, 232)[:2]
    buyer_name_lines = simpleSplit(str(buyer.get("name") or "").strip(), font_bold, 8.9, 232)[:2]

    page_margin = 34
    right_margin_x = width - page_margin

    # Tytul dokumentu (uklad i pozycja zgodna ze wzorcem MS/Fakturka).
    title_y = 807.32
    pdf.setFont(font_bold_italic, 11.9)
    pdf.drawRightString(right_margin_x, title_y, f"{document_title} nr: {document_number}")
    pdf.setLineWidth(1.1)
    pdf.line(212.53, title_y - 10.2, right_margin_x, title_y - 10.2)

    # Blok metadanych.
    pdf.setFont(font_italic, 7.9)
    pdf.drawString(43.35, 779.80, "Miejsce wystawienia")
    pdf.drawString(139.08, 779.80, "Data zakończenia dostaw")
    pdf.drawString(197.62, 770.26, "/usług")
    pdf.drawString(281.12, 779.80, "Data wystawienia")
    pdf.drawString(373.20, 779.80, "Termin płatności")
    pdf.drawString(463.64, 779.80, "Forma płatności")

    pdf.setFont(font_bold, 8.9)
    pdf.drawString(60.91, 767.58, str(invoice.get("place_of_issue") or ""))
    pdf.drawString(159.55, 767.58, str(invoice.get("service_date") or ""))
    pdf.drawString(267.55, 767.58, str(invoice.get("issue_date") or ""))
    pdf.drawString(359.55, 767.58, str(invoice.get("payment_due_date") or ""))
    pdf.drawString(456.95, 767.58, str(invoice.get("payment_method") or ""))

    # Naglowki sekcji kontrahentow (we wzorcu: Sprzedawca po lewej, Nabywca po prawej).
    pdf.setFont(font_bold_italic, 11.9)
    pdf.drawString(55.76, 737.96, "Sprzedawca")
    pdf.drawString(296.70, 737.96, "Nabywca")
    pdf.setLineWidth(1.0)
    pdf.line(55.76, 730.96, 286.70, 730.96)
    pdf.line(296.70, 730.96, 527.20, 730.96)

    seller_x = 38.75
    buyer_x = 279.70
    name_y_slots = [717.47, 706.97]
    for idx, line in enumerate(seller_name_lines[:2]):
        pdf.setFont(font_bold, 8.9)
        pdf.drawString(seller_x, name_y_slots[idx], line)
    for idx, line in enumerate(buyer_name_lines[:2]):
        pdf.setFont(font_bold, 8.9)
        pdf.drawString(buyer_x, name_y_slots[idx], line)

    pdf.setFont(font_regular, 8.9)
    pdf.drawString(seller_x, 669.28, str(seller.get("street") or ""))
    pdf.drawString(buyer_x, 669.28, str(buyer.get("street") or ""))
    pdf.drawString(seller_x, 652.27, seller_city_line)
    pdf.drawString(buyer_x, 652.27, buyer_city_line)
    pdf.drawString(seller_x, 632.43, "NIP:")
    pdf.drawString(buyer_x, 632.43, "NIP:")
    pdf.drawString(seller_x + 34.0, 632.43, str(seller.get("nip") or ""))
    pdf.drawString(buyer_x + 34.0, 632.43, str(buyer.get("nip") or ""))

    row_left = page_margin
    col_widths = [28, 150, 44, 34, 56, 56, 46, 56, 57]
    table_right = row_left + sum(col_widths)

    table_header_top_y = 608.20
    table_header_bottom_y = 586.20
    pdf.setFont(font_bold_italic, 8)
    pdf.setLineWidth(1)
    pdf.line(row_left, table_header_top_y, table_right, table_header_top_y)
    pdf.line(row_left, table_header_bottom_y, table_right, table_header_bottom_y)

    header_x = row_left
    pdf.drawCentredString(header_x + (col_widths[0] / 2), 598.89, "Lp.")
    header_x += col_widths[0]
    pdf.drawCentredString(header_x + (col_widths[1] / 2), 598.89, "Nazwa towaru / usługi")
    header_x += col_widths[1]
    pdf.drawCentredString(header_x + (col_widths[2] / 2), 598.89, "Ilość")
    header_x += col_widths[2]
    pdf.drawCentredString(header_x + (col_widths[3] / 2), 598.89, "Jm.")
    header_x += col_widths[3]
    pdf.drawCentredString(header_x + (col_widths[4] / 2), 598.89, "Cena netto")
    header_x += col_widths[4]
    pdf.drawCentredString(header_x + (col_widths[5] / 2), 598.89, "Wartość")
    header_x += col_widths[5]
    pdf.drawCentredString(header_x + (col_widths[6] / 2), 598.89, "Stawka")
    header_x += col_widths[6]
    pdf.drawCentredString(header_x + (col_widths[7] / 2), 598.89, "Kwota VAT")
    header_x += col_widths[7]
    pdf.drawCentredString(header_x + (col_widths[8] / 2), 598.89, "Wartość")
    pdf.drawCentredString(row_left + sum(col_widths[:6]) + (col_widths[5] / 2), 590.89, "netto")
    pdf.drawCentredString(row_left + sum(col_widths[:7]) + (col_widths[6] / 2), 590.89, "VAT")
    pdf.drawCentredString(row_left + sum(col_widths[:9]) - (col_widths[8] / 2), 590.89, "brutto")

    row_y = 577.12
    row_font_size = 9.2
    row_line_step = 10.6
    pdf.setFont(font_regular, row_font_size)
    for item in line_items:
        if not isinstance(item, dict):
            continue
        line_label = _build_preview_line_label(item)
        wrapped_label = simpleSplit(line_label, font_regular, row_font_size, col_widths[1] - 4)
        predicted_row_height = max(16, len(wrapped_label) * row_line_step + 2)
        if row_y - predicted_row_height < 180:
            break

        values = [
            f"{item.get('lp')}.",
            None,
            str(item.get("quantity") or ""),
            str(item.get("unit") or ""),
            str(item.get("net_price") or ""),
            str(item.get("net_value") or ""),
            str(item.get("vat_rate") or ""),
            str(item.get("vat_value") or ""),
            str(item.get("gross_value") or ""),
        ]
        row_baseline_y = row_y
        lowest_text_y = row_baseline_y
        current_x = row_left
        for index, (value, col_width) in enumerate(zip(values, col_widths, strict=False)):
            if index == 1:
                label_y = row_baseline_y
                for wrapped_line in wrapped_label:
                    pdf.drawString(current_x + 2, label_y, wrapped_line)
                    label_y -= row_line_step
                if wrapped_label:
                    lowest_text_y = min(lowest_text_y, label_y + row_line_step - 1.5)
            elif index in {2, 4, 5, 7, 8}:
                pdf.drawRightString(current_x + col_width - 2, row_baseline_y, value)
            elif index in {0, 3, 6}:
                pdf.drawCentredString(current_x + (col_width / 2), row_baseline_y, value)
            current_x += col_width
        pdf.setLineWidth(0.55)
        pdf.setDash(1, 2)
        row_separator_y = lowest_text_y - 8
        pdf.line(row_left, row_separator_y, table_right, row_separator_y)
        pdf.setDash()
        row_y = row_separator_y - 9

    totals_y = row_y - 4
    right_table_left = row_left + sum(col_widths[:4])
    net_total_right = row_left + sum(col_widths[:6]) - 2
    vat_total_right = row_left + sum(col_widths[:8]) - 2
    gross_total_right = table_right - 2
    pdf.setLineWidth(1)
    pdf.line(right_table_left, totals_y + 6, row_left + sum(col_widths), totals_y + 6)
    pdf.setFont(font_bold, 9)
    pdf.drawString(right_table_left, totals_y - 6, "Razem:")
    pdf.drawRightString(net_total_right, totals_y - 6, str(totals.get("net") or ""))
    pdf.drawRightString(vat_total_right, totals_y - 6, str(totals.get("vat") or ""))
    pdf.drawRightString(gross_total_right, totals_y - 6, str(totals.get("gross") or ""))

    pdf.setFont(font_regular, 8)
    pdf.drawString(right_table_left, totals_y - 19, "wg stawki 23 %")
    pdf.drawRightString(net_total_right, totals_y - 19, str(totals.get("net") or ""))
    pdf.drawRightString(vat_total_right, totals_y - 19, str(totals.get("vat") or ""))
    pdf.drawRightString(gross_total_right, totals_y - 19, str(totals.get("gross") or ""))

    payment_y = totals_y - 29
    pdf.setFont(font_bold_italic, 9)
    pdf.drawString(row_left, payment_y, "Razem do zapłaty:")
    pdf.setFont(font_bold, 9)
    pdf.drawRightString(row_left + 200, payment_y, str(totals.get("gross") or ""))
    pdf.setFont(font_bold_italic, 9)
    pdf.drawString(row_left, payment_y - 14, "Zapłacono:")
    pdf.setFont(font_bold, 9)
    pdf.drawRightString(row_left + 200, payment_y - 14, str(totals.get("paid") or ""))
    pdf.setFont(font_bold_italic, 9)
    pdf.drawString(row_left, payment_y - 28, "Pozostało do zapłaty:")
    pdf.setFont(font_bold, 9)
    pdf.drawRightString(row_left + 200, payment_y - 28, str(totals.get("remaining") or ""))

    pdf.setFont(font_bold_italic, 9)
    pdf.drawString(row_left + 236, payment_y, "Słownie:")
    gross_words = str(totals.get("gross_words") or "")
    wrapped_words = simpleSplit(gross_words, font_regular, 8.5, 240)
    pdf.setFont(font_regular, 8.5)
    words_y = payment_y - 12
    for wrapped_line in wrapped_words[:2]:
        pdf.drawString(row_left + 236, words_y, wrapped_line)
        words_y -= 10

    bank_y = payment_y - 43
    pdf.setFont(font_bold_italic, 9)
    pdf.drawString(row_left, bank_y, "Numer rachunku bankowego:")
    pdf.setFont(font_regular, 9)
    pdf.drawString(row_left, bank_y - 14, str(seller.get("bank_account") or ""))

    issuer_y = payment_y - 81
    pdf.setFont(font_regular, 9)
    pdf.drawString(row_left, issuer_y, str(invoice.get("issuer") or ""))
    pdf.line(row_left, issuer_y - 12, row_left + 210, issuer_y - 12)
    pdf.line(row_left + 280, issuer_y - 12, row_left + 490, issuer_y - 12)
    pdf.setFont(font_italic, 6.9)
    pdf.drawCentredString(row_left + 105, issuer_y - 24, "(podpis osoby wystawiającej fakturę)")
    pdf.drawCentredString(row_left + 385, issuer_y - 24, "(podpis osoby odbierającej fakturę)")

    notes_y = issuer_y - 52
    note_text = " | ".join(str(note).strip() for note in notes if str(note).strip())
    pdf.setFont(font_bold_italic, 9)
    pdf.drawString(row_left, notes_y, "Uwagi:")
    if note_text:
        pdf.setFont(font_regular, 8.5)
        wrapped_notes = simpleSplit(note_text, font_regular, 8.5, 340)
        draw_y = notes_y
        for wrapped_line in wrapped_notes[:3]:
            pdf.drawString(row_left + 42, draw_y, wrapped_line)
            draw_y -= 10

    footer_y = 73.89
    pdf.setFont(font_italic, 8.5)
    pdf.drawString(
        211.66,
        footer_y,
        "ZMIANA NUMERU KONTA ! UWAGA: Towar pozostaje własnością KSERO-PARTNER SK",
    )
    pdf.drawString(
        211.66,
        footer_y - 22.84,
        "do czasu całkowitej zapłaty. Za zwłokę naliczamy",
    )
    pdf.drawString(
        211.66,
        footer_y - 32.38,
        "odsetki.",
    )
    pdf.setFont(font_italic, 5.9)
    pdf.drawString(404.15, 21.34, "Menadżer Serwisu i Fakturka - Serwisoft.pl")
    pdf.setFont(font_italic, 8.9)
    pdf.drawString(500.05, 36.32, "1 z 1")
    pdf.drawString(466.93, 36.32, "Strona")

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@lru_cache(maxsize=1)
def _resolve_reportlab_font_names() -> ReportlabFontNames:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidate_families: list[tuple[str, Path, Path, Path | None, Path | None]] = [
        (
            "VerdanaCTIP",
            Path("C:/Windows/Fonts/verdana.ttf"),
            Path("C:/Windows/Fonts/verdanab.ttf"),
            Path("C:/Windows/Fonts/verdanai.ttf"),
            Path("C:/Windows/Fonts/verdanaz.ttf"),
        ),
        (
            "DejaVuSansCTIP",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
        ),
        (
            "LiberationSansCTIP",
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-BoldItalic.ttf"),
        ),
        (
            "LiberationSansCTIP",
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf"),
        ),
    ]

    registered_names = set(pdfmetrics.getRegisteredFontNames())
    for family_name, regular_path, bold_path, italic_path, bold_italic_path in candidate_families:
        if not (regular_path.exists() and bold_path.exists()):
            continue

        regular_name = family_name
        bold_name = f"{family_name}-Bold"
        italic_name = f"{family_name}-Italic"
        bold_italic_name = f"{family_name}-BoldItalic"

        if regular_name not in registered_names:
            pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
        if bold_name not in registered_names:
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))

        if italic_path and italic_path.exists():
            if italic_name not in registered_names:
                pdfmetrics.registerFont(TTFont(italic_name, str(italic_path)))
        else:
            italic_name = regular_name

        if bold_italic_path and bold_italic_path.exists():
            if bold_italic_name not in registered_names:
                pdfmetrics.registerFont(TTFont(bold_italic_name, str(bold_italic_path)))
        else:
            bold_italic_name = bold_name

        return ReportlabFontNames(
            regular=regular_name,
            bold=bold_name,
            italic=italic_name,
            bold_italic=bold_italic_name,
        )

    return ReportlabFontNames(
        regular="Helvetica",
        bold="Helvetica-Bold",
        italic="Helvetica-Oblique",
        bold_italic="Helvetica-BoldOblique",
    )


def _build_proforma_pdf_lines(invoice: dict[str, Any]) -> list[str]:
    seller = invoice.get("seller") if isinstance(invoice.get("seller"), dict) else {}
    buyer = invoice.get("buyer") if isinstance(invoice.get("buyer"), dict) else {}
    totals = invoice.get("totals") if isinstance(invoice.get("totals"), dict) else {}
    line_items = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    notes = invoice.get("notes") if isinstance(invoice.get("notes"), list) else []
    document_title = str(invoice.get("document_title") or "Dokument handlowy").strip()
    document_number = str(invoice.get("document_number") or "").strip()
    issue_date = str(invoice.get("issue_date") or "").strip()
    payment_due = str(invoice.get("payment_due_date") or "").strip()
    payment_method = str(invoice.get("payment_method") or "").strip()
    issuer = str(invoice.get("issuer") or "").strip()

    rows: list[str] = [
        f"{document_title}: {document_number}",
        f"Data wystawienia: {issue_date} | Termin platnosci: {payment_due}",
        f"Forma platnosci: {payment_method}",
        "",
        f"Sprzedawca: {seller.get('name', '')}",
        f"  Adres: {seller.get('street', '')}, {seller.get('postal_code', '')} {seller.get('city', '')}",
        f"  NIP: {seller.get('nip', '')}",
        f"  Konto: {seller.get('bank_account', '')}",
        "",
        f"Nabywca: {buyer.get('name', '')}",
        f"  Adres: {buyer.get('street', '')}, {buyer.get('postal_code', '')} {buyer.get('city', '')}",
        f"  NIP: {buyer.get('nip', '')}",
        "",
        "Pozycje:",
    ]

    for item in line_items:
        if not isinstance(item, dict):
            continue
        lp = str(item.get("lp") or "")
        name = _build_preview_line_label(item)
        quantity = str(item.get("quantity") or "")
        unit = str(item.get("unit") or "")
        net_price = str(item.get("net_price") or "")
        net_value = str(item.get("net_value") or "")
        gross_value = str(item.get("gross_value") or "")
        rows.append(f"{lp}. {name} | Ilosc: {quantity} {unit}")
        rows.append(f"    Cena netto: {net_price} | Netto: {net_value} | Brutto: {gross_value}")

    rows.extend(
        [
            "",
            f"Razem netto: {totals.get('net', '')}",
            f"Razem VAT: {totals.get('vat', '')}",
            f"Razem brutto: {totals.get('gross', '')}",
            f"Do zaplaty: {totals.get('remaining', '')}",
            f"Slownie: {totals.get('gross_words', '')}",
            "",
            f"Wystawil: {issuer}",
        ]
    )

    if notes:
        rows.append("")
        rows.append("Uwagi:")
        for note in notes:
            rows.append(f"- {note}")

    output: list[str] = []
    for row in rows:
        wrapped = _wrap_pdf_line(str(row), width=94)
        if wrapped:
            output.extend(wrapped)
        else:
            output.append("")
    return output


def _build_preview_line_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    serial_number = str(item.get("serial_number") or "").strip()
    internal_number = str(item.get("internal_number") or "").strip()
    parts = [name] if name else []
    if serial_number and serial_number != "—" and serial_number not in name:
        parts.append(f"S/N: {serial_number}")
    if internal_number and internal_number not in name:
        parts.append(f"nr.wew: {internal_number}")
    return ", ".join(part for part in parts if part).strip() or "Pozycja proformy"


def _wrap_pdf_line(text: str, *, width: int) -> list[str]:
    value = " ".join(text.strip().split())
    if not value:
        return []
    if len(value) <= width:
        return [value]

    words = value.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def _build_simple_pdf(lines: list[str]) -> bytes:
    max_lines = 56
    rendered_lines = [line for line in lines if line is not None]
    if len(rendered_lines) > max_lines:
        rendered_lines = rendered_lines[: max_lines - 1]
        rendered_lines.append("[... obcieto nadmiar tresci dokumentu ...]")

    y_start = 810
    line_step = 14
    commands = ["BT", "/F1 10 Tf"]
    for index, line in enumerate(rendered_lines):
        y_pos = y_start - (index * line_step)
        escaped = _escape_pdf_text(line)
        commands.append(f"1 0 0 1 40 {y_pos} Tm ({escaped}) Tj")
    commands.append("ET")

    stream = "\n".join(commands).encode("latin-1", errors="ignore")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]

    content = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")

    xref_offset = len(content)
    object_count = len(objects) + 1
    content.extend(f"xref\n0 {object_count}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer\n<< /Size {object_count} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(content)


def _escape_pdf_text(value: str) -> str:
    text = _ascii_pdf_text(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("(", "\\(").replace(")", "\\)")
    return text


def _ascii_pdf_text(value: str) -> str:
    return "".join(ch if 32 <= ord(ch) <= 126 else " " for ch in value).strip()


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_workflow_device_warehouse_id(
    device: dict[str, Any], *, source_type: str
) -> int | None:
    """Ustala ID_MAGAZYN_TABLE dla urządzenia workflow niezależnie od typu źródła."""
    if source_type == WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE:
        return _coerce_int(device.get("row"))

    for key in ("ms_id_magazyn_table", "warehouse_id_magazyn_table", "id_magazyn_table"):
        warehouse_id = _coerce_int(device.get(key))
        if warehouse_id is not None:
            return warehouse_id
    return None


def _build_line_item(cursor, *, device: dict[str, Any], form_request_id: int) -> dict[str, Any]:
    row_value = device.get("row")
    try:
        source_row = int(row_value) if row_value not in (None, "") else 0
    except (TypeError, ValueError):
        source_row = 0

    ewidencja = _truncate_text(device.get("ewidencja"), 100)
    source_type = normalize_workflow_device_source_type(
        device.get("source_type"),
        default=WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
    )
    if source_type == WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE:
        if source_row <= 0:
            raise ValueError("Wybrane urzadzenie Firebird nie ma ID pozycji magazynowej.")
        warehouse = _fetch_warehouse_details(cursor, source_row)
        if not ewidencja:
            ewidencja = _truncate_text(warehouse.indeks, 100)
    elif source_type == WORKFLOW_DEVICE_SOURCE_FIREBIRD_SERIAL:
        warehouse_id = _resolve_workflow_device_warehouse_id(device, source_type=source_type)
        if warehouse_id is None:
            raise ValueError(
                "Wybrane urzadzenie z toru SERIAL nie ma powiazania z ID_MAGAZYN_TABLE."
            )
        warehouse = _fetch_warehouse_details(cursor, warehouse_id)
        if not ewidencja:
            ewidencja = _truncate_text(device.get("index") or warehouse.indeks, 100)
    else:
        warehouse_match = find_warehouse_item_in_firebird(ewidencja)
        if warehouse_match.error:
            raise RuntimeError(
                f"Nie udalo sie odczytac pozycji magazynowej dla wiersza {source_row}: {warehouse_match.error}"
            )
        if not warehouse_match.found or warehouse_match.id_magazyn_table is None:
            if source_row <= 0:
                raise ValueError(
                    "Wybrane urzadzenie nie ma pozycji magazynowej i nie zawiera numeru wiersza arkusza do synchronizacji."
                )
            synchronize_device_from_sheet_row(source_row, kto="CTIP/FLOW")
            warehouse_match = find_warehouse_item_in_firebird(ewidencja)
            if (
                warehouse_match.error
                or not warehouse_match.found
                or warehouse_match.id_magazyn_table is None
            ):
                raise RuntimeError(
                    f"Po synchronizacji nadal brak pozycji magazynowej dla wiersza {source_row}."
                )
        warehouse = _fetch_warehouse_details(cursor, warehouse_match.id_magazyn_table)

    available_qty = _decimal_or_zero(warehouse.ilosc)
    if available_qty <= 0:
        raise ValueError(
            f"Urzadzenie z wiersza {source_row or '?'} nie ma dodatniego stanu magazynowego."
        )

    vat_rate_value = _parse_vat_rate(warehouse.vat_stawka)
    net_price, gross_price = _resolve_line_prices(
        device,
        warehouse=warehouse,
        vat_rate=vat_rate_value,
    )
    quantity = Decimal("1.0000")
    net_value = _quantize_money(net_price * quantity)
    gross_value = _quantize_money(gross_price * quantity)
    vat_total = _quantize_money(gross_value - net_value)
    cost_price = _quantize_money(_decimal_or_zero(warehouse.cena_z1))
    cost_value = _quantize_money(cost_price * quantity)

    return {
        "warehouse_id_magazyn_table": warehouse.id_magazyn_table,
        "warehouse_id_magazyn": warehouse.id_magazyn,
        "serial_id": _find_serial_id(
            cursor,
            serial=device.get("serial"),
            ewidencja=ewidencja,
            warehouse_id=warehouse.id_magazyn_table,
        ),
        "rodzaj": _truncate_text(warehouse.rodzaj, 50) or "2. Towar inny",
        "indeks": _truncate_text(warehouse.indeks, 100),
        "nazwa": _build_line_name(device, warehouse, form_request_id=form_request_id),
        "quantity": quantity,
        "unit": _truncate_text(warehouse.jm, 10) or "szt.",
        "net_price": net_price,
        "gross_price": gross_price,
        "cost_price": cost_price,
        "net_value": net_value,
        "gross_value": gross_value,
        "vat_value": vat_total,
        "cost_value": cost_value,
        "vat_rate_text": _truncate_text(warehouse.vat_stawka, 10) or "23 %",
        "vat_rate_display": _truncate_text(warehouse.vat_stawka, 10) or "23 %",
        "idvat": warehouse.idvat or 1,
    }


def _aggregate_vat_buckets(line_items: list[dict[str, Any]]) -> list[dict[str, Decimal]]:
    grouped: dict[str, dict[str, Decimal]] = {}
    for item in line_items:
        key = str(item["vat_rate_text"])
        if key not in grouped:
            grouped[key] = {
                "net": Decimal("0.0000"),
                "vat": Decimal("0.0000"),
                "gross": Decimal("0.0000"),
            }
        grouped[key]["net"] = _quantize_money(grouped[key]["net"] + item["net_value"])
        grouped[key]["vat"] = _quantize_money(grouped[key]["vat"] + item["vat_value"])
        grouped[key]["gross"] = _quantize_money(grouped[key]["gross"] + item["gross_value"])

    ordered = sorted(grouped.items(), key=lambda item: _parse_vat_rate(item[0]), reverse=True)
    return [bucket for _label, bucket in ordered[:8]]


def _fetch_seller_details(cursor, *, company_id: int = 1) -> FirebirdSellerDetails:
    cursor.execute(
        """
        SELECT ID_FIRMA, NAZWA, ADRES, KOD, POCZTA, MIEJSCE_WYST, NIP, TELEFON, EMAIL, BANK, KONTO
        FROM FIRMA
        WHERE ID_FIRMA = ?
        """,
        (company_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(f"Brak rekordu FIRMA ID {company_id} w Firebird.")
    return FirebirdSellerDetails(
        id_firma=int(row[0]),
        nazwa=str(row[1] or ""),
        adres=_truncate_text(str(row[2] or ""), 100),
        kod=_truncate_text(str(row[3] or ""), 10),
        poczta=_truncate_text(str(row[4] or ""), 100),
        miejsce_wyst=_truncate_text(str(row[5] or ""), 100),
        nip=_truncate_text(str(row[6] or ""), 15),
        telefon=_truncate_text(str(row[7] or ""), 100),
        email=_truncate_text(str(row[8] or ""), 100),
        bank=_truncate_text(str(row[9] or ""), 100),
        konto=_truncate_text(str(row[10] or ""), 50),
    )


def _fetch_client_details(cursor, client_id: int) -> FirebirdClientDetails:
    cursor.execute(
        """
        SELECT ID_KLIENT, NAZWA, ADRES, KOD, POCZTA, NIP, TELEFON, E_MAIL, KOD_KRAJU
        FROM KLIENT
        WHERE ID_KLIENT = ?
        """,
        (client_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Nie znaleziono klienta Menadzera Serwisu ID {client_id}.")
    return FirebirdClientDetails(
        id_klient=int(row[0]),
        nazwa=str(row[1] or ""),
        adres=_truncate_text(str(row[2] or ""), 150),
        kod=_truncate_text(str(row[3] or ""), 6),
        poczta=_truncate_text(str(row[4] or ""), 100),
        nip=_truncate_text(str(row[5] or ""), 15),
        telefon=_truncate_text(str(row[6] or ""), 100),
        email=_truncate_text(str(row[7] or ""), 200),
        kod_kraju=_truncate_text(str(row[8] or ""), 4),
    )


def _fetch_warehouse_details(cursor, warehouse_id: int) -> FirebirdWarehouseDetails:
    cursor.execute(
        """
        SELECT
            ID_MAGAZYN_TABLE,
            ID_MAGAZYN,
            RODZAJ,
            INDEKS,
            NAZWA,
            JM,
            ILOSC,
            CENA_NETTO,
            CENA_BRUTTO,
            CENA_Z1,
            VAT_STAWKA,
            IDVAT,
            MARKA,
            MODEL
        FROM MAGAZYN
        WHERE ID_MAGAZYN_TABLE = ?
        """,
        (warehouse_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Nie znaleziono pozycji magazynowej ID {warehouse_id}.")
    return FirebirdWarehouseDetails(
        id_magazyn_table=int(row[0]),
        id_magazyn=int(row[1]),
        rodzaj=_truncate_text(str(row[2] or ""), 50),
        indeks=_truncate_text(str(row[3] or ""), 100),
        nazwa=_truncate_text(str(row[4] or ""), 500),
        jm=_truncate_text(str(row[5] or ""), 10),
        ilosc=_decimal_or_zero(row[6]),
        cena_netto=_decimal_or_none(row[7]),
        cena_brutto=_decimal_or_none(row[8]),
        cena_z1=_decimal_or_none(row[9]),
        vat_stawka=_truncate_text(str(row[10] or ""), 10),
        idvat=int(row[11]) if row[11] is not None else None,
        marka=_truncate_text(str(row[12] or ""), 50),
        model=_truncate_text(str(row[13] or ""), 50),
    )


def _resolve_line_prices(
    device: dict[str, Any],
    *,
    warehouse: FirebirdWarehouseDetails,
    vat_rate: Decimal,
) -> tuple[Decimal, Decimal]:
    explicit_gross = _parse_price(device.get("price_gross"))
    if explicit_gross is not None and explicit_gross > 0:
        gross_price = _quantize_money(explicit_gross)
        return _gross_to_net(gross_price, vat_rate), gross_price

    explicit_net = _parse_price(device.get("price_net"))
    if explicit_net is not None and explicit_net > 0:
        net_price = _quantize_money(explicit_net)
        return net_price, _net_to_gross(net_price, vat_rate)

    gross_price = _resolve_gross_price(device, warehouse)
    return _gross_to_net(gross_price, vat_rate), gross_price


def _resolve_gross_price(device: dict[str, Any], warehouse: FirebirdWarehouseDetails) -> Decimal:
    explicit_price = _parse_price(device.get("price"))
    if explicit_price is not None and explicit_price > 0:
        return explicit_price
    if warehouse.cena_brutto is not None and warehouse.cena_brutto > 0:
        return _quantize_money(warehouse.cena_brutto)
    raise ValueError(
        f"Brak ceny dla urzadzenia {device.get('ewidencja') or device.get('serial') or '?'}."
    )


def _build_line_name(
    device: dict[str, Any],
    warehouse: FirebirdWarehouseDetails,
    *,
    form_request_id: int,
) -> str:
    label = " ".join(
        part.strip()
        for part in [str(device.get("producer") or ""), str(device.get("model") or "")]
        if part and part.strip()
    ).strip()
    if label:
        return _truncate_text(label, 500) or f"FLOW formularz {form_request_id}"
    if warehouse.nazwa:
        return _truncate_text(warehouse.nazwa, 500) or f"FLOW formularz {form_request_id}"
    return f"FLOW formularz {form_request_id}"


def _find_serial_id(
    cursor,
    *,
    serial: str | None,
    ewidencja: str | None,
    warehouse_id: int,
) -> int | None:
    serial_key = normalize_device_key(serial)
    ewidencja_key = normalize_device_key(ewidencja)
    if not serial_key and not ewidencja_key:
        return None

    cursor.execute(
        """
        SELECT FIRST 1 ID_SERIAL
        FROM SERIAL
        WHERE ID_MAGPOZ = ?
          AND (
                UPPER(REPLACE(REPLACE(REPLACE(SERIAL, '/', ''), '-', ''), ' ', '')) = ?
             OR UPPER(REPLACE(REPLACE(REPLACE(EWIDENCJA, '/', ''), '-', ''), ' ', '')) = ?
          )
        ORDER BY ID_SERIAL DESC
        """,
        (
            warehouse_id,
            serial_key or "___NO_MATCH___",
            ewidencja_key or "___NO_MATCH___",
        ),
    )
    row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    return int(row[0])


def _next_proforma_document_no(cursor, year: int) -> int:
    cursor.execute(
        """
        SELECT MAX(DOKUMENT)
        FROM FAKTURA
        WHERE RODZAJ_DOK = 'proforma'
          AND EXTRACT(YEAR FROM COALESCE(DATA_WYST, DATA_SPRZ)) = ?
        """,
        (year,),
    )
    row = cursor.fetchone()
    return int(row[0] or 0) + 1


def _load_preview_line_items(cursor, proforma_firebird_id: int) -> list[dict[str, str]]:
    cursor.execute(
        """
        SELECT
            p.ID_FPOZYCJA_TABLE,
            p.ID_SERIAL,
            p.NAZWA,
            p.INDEKS,
            p.ILOSC,
            p.JM,
            p.CENA_NETTO,
            p.WARTOSC_NETTO,
            p.STAWKA_VAT,
            p.VAT,
            p.WARTOSC_BRUTTO
        FROM FPOZYCJA p
        WHERE p.ID_FAKTURA = ?
        ORDER BY p.ID_FPOZYCJA_TABLE
        """,
        (proforma_firebird_id,),
    )
    output: list[dict[str, str]] = []
    for index, row in enumerate(cursor.fetchall(), start=1):
        serial_id = int(row[1]) if row[1] is not None else None
        output.append(
            {
                "lp": index,
                "name": str(row[2] or ""),
                "internal_number": str(row[3] or ""),
                "serial_number": _load_serial_number(cursor, serial_id),
                "quantity": _format_quantity(_decimal_or_zero(row[4])),
                "unit": str(row[5] or ""),
                "net_price": _format_currency(_decimal_or_zero(row[6])),
                "net_value": _format_currency(_decimal_or_zero(row[7])),
                "vat_rate": str(row[8] or ""),
                "vat_value": _format_currency(_decimal_or_zero(row[9])),
                "gross_value": _format_currency(_decimal_or_zero(row[10])),
            }
        )
    return output


def _load_serial_number(cursor, serial_id: int | None) -> str:
    if not serial_id:
        return "—"
    cursor.execute("SELECT SERIAL FROM SERIAL WHERE ID_SERIAL = ?", (serial_id,))
    row = cursor.fetchone()
    if not row or row[0] is None:
        return "—"
    return str(row[0])


def _parse_price(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = (
        text.replace("zł", "")
        .replace("PLN", "")
        .replace(" ", "")
        .replace("\u00a0", "")
        .replace(",", ".")
    )
    try:
        parsed = Decimal(normalized)
    except Exception:  # noqa: BLE001
        return None
    return _quantize_money(parsed)


def _parse_vat_rate(value: str | None) -> Decimal:
    if not value:
        return DEFAULT_VAT_RATE
    digits = "".join(char for char in str(value) if char.isdigit() or char in {",", "."})
    if not digits:
        return DEFAULT_VAT_RATE
    try:
        return Decimal(digits.replace(",", "."))
    except Exception:  # noqa: BLE001
        return DEFAULT_VAT_RATE


def _gross_to_net(gross: Decimal, vat_rate: Decimal) -> Decimal:
    multiplier = Decimal("1.0000") + (vat_rate / Decimal("100"))
    if multiplier == 0:
        return _quantize_money(gross)
    return _quantize_money(gross / multiplier)


def _net_to_gross(net: Decimal, vat_rate: Decimal) -> Decimal:
    multiplier = Decimal("1.0000") + (vat_rate / Decimal("100"))
    if multiplier == 0:
        return _quantize_money(net)
    return _quantize_money(net * multiplier)


def _format_date_pl(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return ""


def _format_quantity(value: Decimal) -> str:
    return f"{value.quantize(DISPLAY_PRECISION, rounding=ROUND_HALF_UP):.2f}".replace(".", ",")


def _format_currency(value: Decimal) -> str:
    formatted = f"{value.quantize(DISPLAY_PRECISION, rounding=ROUND_HALF_UP):,.2f}"
    return f"{formatted.replace(',', 'X').replace('.', ',').replace('X', ' ')} zł"


def _join_bank_account(bank: str | None, account: str | None) -> str:
    if bank and account:
        return f"{bank} {account}"
    return bank or account or ""


def _row_as_dict(cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: value for column, value in zip(cursor.description, row, strict=False)}


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return _quantize_money(value)
    try:
        return _quantize_money(Decimal(str(value)))
    except Exception:  # noqa: BLE001
        return None


def _decimal_or_zero(value: Any) -> Decimal:
    return _decimal_or_none(value) or Decimal("0.0000")


def _sum_decimal(values) -> Decimal:
    total = Decimal("0.0000")
    for value in values:
        total = _quantize_money(total + _decimal_or_zero(value))
    return total


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


def amount_to_polish_words(value: Decimal) -> str:
    """Zamienia kwote na prosty zapis slowny w jezyku polskim."""
    rounded = value.quantize(DISPLAY_PRECISION, rounding=ROUND_HALF_UP)
    integer = int(rounded)
    grosze = int((rounded - Decimal(integer)) * 100)
    return f"{_number_to_words(integer)} {_zloty_form(integer)} {grosze:02d}/100 gr."


def _number_to_words(value: int) -> str:
    if value == 0:
        return "zero"

    ones = [
        "",
        "jeden",
        "dwa",
        "trzy",
        "cztery",
        "piec",
        "szesc",
        "siedem",
        "osiem",
        "dziewiec",
    ]
    teens = [
        "dziesiec",
        "jedenascie",
        "dwanascie",
        "trzynascie",
        "czternascie",
        "pietnascie",
        "szesnascie",
        "siedemnascie",
        "osiemnascie",
        "dziewietnascie",
    ]
    tens = [
        "",
        "",
        "dwadziescia",
        "trzydziesci",
        "czterdziesci",
        "piecdziesiat",
        "szescdziesiat",
        "siedemdziesiat",
        "osiemdziesiat",
        "dziewiecdziesiat",
    ]
    hundreds = [
        "",
        "sto",
        "dwiescie",
        "trzysta",
        "czterysta",
        "piecset",
        "szescset",
        "siedemset",
        "osiemset",
        "dziewiecset",
    ]
    groups = [
        ("", "", ""),
        ("tysiac", "tysiace", "tysiecy"),
        ("milion", "miliony", "milionow"),
        ("miliard", "miliardy", "miliardow"),
    ]

    result: list[str] = []
    group_index = 0
    remaining = value
    while remaining > 0:
        chunk = remaining % 1000
        if chunk:
            chunk_words: list[str] = []
            hundreds_digit = chunk // 100
            tens_ones = chunk % 100
            tens_digit = tens_ones // 10
            ones_digit = tens_ones % 10

            if hundreds_digit:
                chunk_words.append(hundreds[hundreds_digit])
            if 10 <= tens_ones <= 19:
                chunk_words.append(teens[tens_ones - 10])
            else:
                if tens_digit:
                    chunk_words.append(tens[tens_digit])
                if ones_digit:
                    chunk_words.append(ones[ones_digit])

            if group_index > 0:
                if chunk == 1:
                    chunk_words = [groups[group_index][0]]
                else:
                    chunk_words.append(_plural_form(chunk, groups[group_index]))

            result.insert(0, " ".join(part for part in chunk_words if part))

        remaining //= 1000
        group_index += 1

    return " ".join(part for part in result if part).strip()


def _plural_form(value: int, forms: tuple[str, str, str]) -> str:
    last_two = value % 100
    last_one = value % 10
    if last_two in {12, 13, 14}:
        return forms[2]
    if last_one == 1:
        return forms[0]
    if last_one in {2, 3, 4}:
        return forms[1]
    return forms[2]


def _zloty_form(value: int) -> str:
    return _plural_form(value, ("zloty", "zlote", "zlotych"))


__all__ = [
    "FirebirdProformaDeleteResult",
    "FirebirdProformaWriteResult",
    "amount_to_polish_words",
    "build_proforma_download_filename",
    "build_proforma_pdf_bytes",
    "build_proforma_pdf_url",
    "build_proforma_preview_url",
    "ensure_proforma_pdf_file",
    "create_proforma_from_workflow",
    "delete_proforma_from_firebird",
    "load_proforma_preview_data",
]
