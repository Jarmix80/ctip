"""Generowanie dokumentów PDF dla etykiet i kompletacji wysyłek."""

from __future__ import annotations

import html
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import reportlab
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_FONT_REGULAR = "CTIPShippingRegular"
_FONT_BOLD = "CTIPShippingBold"


def _shipping_font_paths() -> list[tuple[Path, Path]]:
    reportlab_fonts = Path(reportlab.__file__).resolve().parent / "fonts"
    return [
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (reportlab_fonts / "Vera.ttf", reportlab_fonts / "VeraBd.ttf"),
    ]


def shipping_pdf_fonts() -> tuple[str, str]:
    """Rejestruje fonty obsługujące polskie znaki na Linuxie i Windows."""
    registered = set(pdfmetrics.getRegisteredFontNames())
    if {_FONT_REGULAR, _FONT_BOLD}.issubset(registered):
        return _FONT_REGULAR, _FONT_BOLD
    for regular_path, bold_path in _shipping_font_paths():
        if not regular_path.is_file() or not bold_path.is_file():
            continue
        pdfmetrics.registerFont(TTFont(_FONT_REGULAR, str(regular_path)))
        pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(bold_path)))
        return _FONT_REGULAR, _FONT_BOLD
    raise RuntimeError("Nie znaleziono fontu PDF obsługującego polskie znaki.")


def _text(value: Any) -> str:
    return html.escape(str(value or "").strip())


def _quantity(value: Any) -> str:
    try:
        quantity = Decimal(str(value or 0))
    except InvalidOperation:
        return str(value or "")
    normalized = f"{quantity:f}".rstrip("0").rstrip(".")
    return normalized.replace(".", ",") or "0"


def _styles() -> dict[str, ParagraphStyle]:
    regular, bold = shipping_pdf_fonts()
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ShippingTitle",
            parent=sample["Title"],
            fontName=bold,
            fontSize=22,
            leading=27,
            alignment=TA_LEFT,
            spaceAfter=6 * mm,
        ),
        "tracking": ParagraphStyle(
            "ShippingTracking",
            parent=sample["Heading1"],
            fontName=bold,
            fontSize=16,
            leading=20,
            spaceAfter=5 * mm,
        ),
        "heading": ParagraphStyle(
            "ShippingHeading",
            parent=sample["Heading2"],
            fontName=bold,
            fontSize=12,
            leading=15,
            spaceBefore=3 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "ShippingBody",
            parent=sample["BodyText"],
            fontName=regular,
            fontSize=9,
            leading=12,
        ),
        "body_bold": ParagraphStyle(
            "ShippingBodyBold",
            parent=sample["BodyText"],
            fontName=bold,
            fontSize=9,
            leading=12,
        ),
        "small": ParagraphStyle(
            "ShippingSmall",
            parent=sample["BodyText"],
            fontName=regular,
            fontSize=7.5,
            leading=10,
        ),
        "small_center": ParagraphStyle(
            "ShippingSmallCenter",
            parent=sample["BodyText"],
            fontName=regular,
            fontSize=7.5,
            leading=10,
            alignment=TA_CENTER,
        ),
    }


def _page_frame(canvas, document, *, warning: str | None = None) -> None:
    regular, bold = shipping_pdf_fonts()
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#344054"))
    canvas.rect(10 * mm, 10 * mm, document.pagesize[0] - 20 * mm, document.pagesize[1] - 20 * mm)
    canvas.setFont(bold if warning else regular, 8)
    canvas.drawString(14 * mm, 13 * mm, warning or "CTIP — zestawienie kompletacji wysyłek")
    canvas.drawRightString(
        document.pagesize[0] - 14 * mm,
        13 * mm,
        f"Strona {document.page}",
    )
    canvas.restoreState()


def _plain_text(value: Any) -> str:
    return str(value or "").strip()


def _mock_label_party(source: dict[str, Any]) -> dict[str, str]:
    postal_code = _plain_text(source.get("postalCode") or source.get("postal_code"))
    if len(postal_code) == 5 and postal_code.isdigit():
        postal_code = f"{postal_code[:2]}-{postal_code[2:]}"
    return {
        "company": _plain_text(source.get("company") or source.get("companyName")),
        "name": _plain_text(source.get("name") or source.get("contactName")),
        "address": _plain_text(source.get("address") or source.get("street")),
        "postal_code": postal_code,
        "city": _plain_text(source.get("city")),
        "country_code": _plain_text(
            source.get("countryCode") or source.get("country_code") or "PL"
        ),
        "phone": _plain_text(source.get("phone")),
    }


def _mock_label_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalizuje historyczne i aktualne żądanie DPD dla etykiety testowej."""
    if payload.get("packages"):
        package = payload["packages"][0]
        parcel_source = (package.get("parcels") or [{}])[0]
        references = [
            _plain_text(package.get(field))
            for field in ("ref1", "ref2", "ref3")
            if _plain_text(package.get(field))
        ]
        if not references:
            references = [_plain_text(package.get("reference"))]
        return {
            "receiver": _mock_label_party(package.get("receiver") or {}),
            "sender": _mock_label_party(package.get("sender") or {}),
            "parcel": {
                "weight": parcel_source.get("weight"),
                "content": _plain_text(parcel_source.get("content")),
            },
            "references": [reference for reference in references if reference],
        }
    parcel_source = (payload.get("parcels") or [{}])[0]
    return {
        "receiver": _mock_label_party(payload.get("receiver") or {}),
        "sender": _mock_label_party(payload.get("sender") or {}),
        "parcel": {
            "weight": parcel_source.get("weightKg") or parcel_source.get("weight"),
            "content": _plain_text(parcel_source.get("content")),
        },
        "references": [_plain_text(payload.get("reference"))],
    }


def _fit_label_text(value: Any, font_name: str, font_size: float, max_width: float) -> str:
    text = _plain_text(value)
    if pdfmetrics.stringWidth(text, font_name, font_size) <= max_width:
        return text
    suffix = "…"
    while text and pdfmetrics.stringWidth(text + suffix, font_name, font_size) > max_width:
        text = text[:-1]
    return text.rstrip() + suffix if text else suffix


def _draw_label_line(
    document: pdf_canvas.Canvas,
    value: Any,
    *,
    left: float,
    baseline: float,
    max_width: float,
    font_name: str,
    font_size: float,
) -> None:
    document.setFont(font_name, font_size)
    document.drawString(
        left,
        baseline,
        _fit_label_text(value, font_name, font_size, max_width),
    )


def _draw_test_matrix(
    document: pdf_canvas.Canvas,
    *,
    left: float,
    bottom: float,
    size: float,
) -> None:
    """Rysuje niedekodowalny znacznik 2D jednoznacznie oznaczony jako testowy."""
    modules = 15
    module_size = size / modules
    document.saveState()
    document.setFillColor(colors.black)
    for row in range(modules):
        for column in range(modules):
            if (row * 7 + column * 11 + row * column) % 6 not in {0, 1}:
                continue
            document.rect(
                left + column * module_size,
                bottom + row * module_size,
                module_size,
                module_size,
                stroke=0,
                fill=1,
            )
    document.setStrokeColor(colors.HexColor("#D71920"))
    document.setLineWidth(1.4)
    document.line(left, bottom, left + size, bottom + size)
    document.line(left, bottom + size, left + size, bottom)
    document.setFillColor(colors.white)
    document.rect(
        left + 2 * mm,
        bottom + size / 2 - 2.7 * mm,
        size - 4 * mm,
        5.4 * mm,
        stroke=0,
        fill=1,
    )
    _, bold = shipping_pdf_fonts()
    document.setFillColor(colors.HexColor("#B42318"))
    document.setFont(bold, 6)
    document.drawCentredString(left + size / 2, bottom + size / 2 - 1.8, "KOD 2D TEST")
    document.restoreState()


def _mock_label_item_lines(items: list[dict[str, Any]] | None) -> list[str]:
    """Buduje maksymalnie trzy czytelne wiersze zawartości etykiety testowej."""
    if not items:
        return []
    visible_items = items if len(items) <= 3 else items[:2]
    lines = []
    for item in visible_items:
        order_number = _plain_text(item.get("order_number"))
        quantity = _quantity(item.get("quantity"))
        unit = _plain_text(item.get("unit") or "szt.")
        item_index = _plain_text(item.get("item_index") or "bez indeksu")
        item_name = _plain_text(item.get("item_name") or "bez nazwy")
        prefix = f"{order_number} | " if order_number else ""
        lines.append(f"{prefix}{quantity} {unit} | {item_index} | {item_name}")
    if len(items) > len(visible_items):
        lines.append(f"+{len(items) - len(visible_items)} poz. — pełna lista na kompletacji")
    return lines


def _draw_mock_shipping_label(
    document: pdf_canvas.Canvas,
    *,
    payload: dict[str, Any],
    tracking_number: str,
    left: float,
    bottom: float,
    width: float,
    height: float,
    sequence: str | None = None,
    fallback_references: list[str] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> None:
    """Rysuje pojedyncze bezpieczne pole etykiety wzorowane na układzie DPD."""
    regular, bold = shipping_pdf_fonts()
    label_data = _mock_label_data(payload)
    receiver = label_data["receiver"]
    sender = label_data["sender"]
    parcel = label_data["parcel"]
    references = label_data["references"] or fallback_references or []
    tracking = _plain_text(tracking_number) or "MOCK-BRAK-NUMERU"
    margin = 3 * mm
    right = left + width
    top = bottom + height
    inner_left = left + margin
    inner_right = right - margin
    inner_width = width - 2 * margin
    red = colors.HexColor("#D71920")
    dark = colors.HexColor("#171717")
    grey = colors.HexColor("#555555")
    light_grey = colors.HexColor("#E7E7E7")

    document.saveState()
    document.setStrokeColor(dark)
    document.setLineWidth(0.8)
    document.rect(left, bottom, width, height, stroke=1, fill=0)

    warning_height = 7 * mm
    document.setFillColor(red)
    document.rect(left, top - warning_height, width, warning_height, stroke=0, fill=1)
    document.setFillColor(colors.white)
    document.setFont(bold, 7.2)
    document.drawCentredString(
        left + width / 2,
        top - 4.8 * mm,
        "ETYKIETA TESTOWA — NIE NADAWAĆ",
    )

    header_top = top - warning_height
    header_bottom = header_top - 13 * mm
    document.setFillColor(red)
    document.roundRect(inner_left, header_bottom + 3.1 * mm, 9 * mm, 6.8 * mm, 1.2 * mm, fill=1)
    document.setFillColor(colors.white)
    document.setFont(bold, 7.2)
    document.drawCentredString(inner_left + 4.5 * mm, header_bottom + 5.2 * mm, "DPD")
    document.setFillColor(dark)
    document.setFont(bold, 14)
    document.drawString(inner_left + 11.5 * mm, header_bottom + 4.2 * mm, "DPD")
    document.setFillColor(grey)
    document.setFont(regular, 4.7)
    document.drawRightString(inner_right, header_bottom + 8.3 * mm, "Układ testowy CTIP")
    document.drawRightString(
        inner_right, header_bottom + 5.7 * mm, "Nie jest dokumentem przewozowym"
    )
    if sequence:
        document.setFillColor(dark)
        document.setFont(bold, 5.5)
        document.drawRightString(inner_right, header_bottom + 2.8 * mm, sequence)
    document.setStrokeColor(dark)
    document.line(left, header_bottom, right, header_bottom)

    receiver_bottom = header_bottom - 34 * mm
    document.setFillColor(dark)
    document.setFont(bold, 5.8)
    document.drawString(inner_left, header_bottom - 4.8 * mm, "ADRES DOSTAWY / DELIVERY ADDRESS")
    receiver_lines = [
        receiver.get("company") or receiver.get("name") or "BRAK ODBIORCY",
        receiver.get("name") if receiver.get("company") else "",
        receiver.get("address"),
        " ".join(
            part
            for part in (
                receiver.get("country_code"),
                receiver.get("postal_code"),
                receiver.get("city"),
            )
            if part
        ),
        f"Tel.: {receiver.get('phone')}" if receiver.get("phone") else "Tel.: brak",
    ]
    receiver_baseline = header_bottom - 9.5 * mm
    for line_index, line in enumerate(receiver_lines):
        _draw_label_line(
            document,
            line,
            left=inner_left,
            baseline=receiver_baseline - line_index * 4.2 * mm,
            max_width=inner_width,
            font_name=bold if line_index in {0, 3} else regular,
            font_size=8.2 if line_index == 0 else 6.7,
        )
    document.setStrokeColor(dark)
    document.line(left, receiver_bottom, right, receiver_bottom)

    summary_bottom = receiver_bottom - 14 * mm
    summary_columns = (left, left + width * 0.34, left + width * 0.62, right)
    for boundary in summary_columns[1:-1]:
        document.line(boundary, receiver_bottom, boundary, summary_bottom)
    summary_values = (
        ("DOSTAWA", "1 / 1"),
        ("WAGA", f"{_quantity(parcel.get('weight'))} kg"),
        ("SERWIS", "DPD CLASSIC TEST"),
    )
    for column_index, (label, value) in enumerate(summary_values):
        column_left = summary_columns[column_index] + 2 * mm
        column_width = summary_columns[column_index + 1] - summary_columns[column_index] - 4 * mm
        document.setFillColor(grey)
        document.setFont(bold, 4.8)
        document.drawString(column_left, receiver_bottom - 4 * mm, label)
        _draw_label_line(
            document,
            value,
            left=column_left,
            baseline=receiver_bottom - 9.5 * mm,
            max_width=column_width,
            font_name=bold,
            font_size=7.5,
        )
    document.line(left, summary_bottom, right, summary_bottom)

    parties_bottom = summary_bottom - 22 * mm
    parties_middle = left + width * 0.63
    document.line(parties_middle, summary_bottom, parties_middle, parties_bottom)
    document.setFillColor(grey)
    document.setFont(bold, 4.8)
    document.drawString(inner_left, summary_bottom - 4 * mm, "NADAWCA / SENDER")
    sender_lines = [
        sender.get("company") or sender.get("name") or "Ksero-Partner Test",
        sender.get("name") if sender.get("company") else "",
        sender.get("address") or "Dane testowe",
        " ".join(part for part in (sender.get("postal_code"), sender.get("city")) if part),
        f"Tel.: {sender.get('phone')}" if sender.get("phone") else "",
    ]
    for line_index, line in enumerate(sender_lines):
        _draw_label_line(
            document,
            line,
            left=inner_left,
            baseline=summary_bottom - 7.5 * mm - line_index * 2.9 * mm,
            max_width=parties_middle - inner_left - 2 * mm,
            font_name=bold if line_index == 0 else regular,
            font_size=5.6 if line_index == 0 else 4.9,
        )
    depot_left = parties_middle + 2 * mm
    document.setFillColor(grey)
    document.setFont(bold, 4.8)
    document.drawString(depot_left, summary_bottom - 4 * mm, "DEPOT")
    document.setFillColor(dark)
    document.setFont(bold, 12)
    document.drawString(depot_left, summary_bottom - 10.5 * mm, "TEST")
    document.setFont(regular, 5.2)
    document.drawString(depot_left, summary_bottom - 15 * mm, "CTIP / DPD MOCK")
    document.drawString(depot_left, summary_bottom - 18.5 * mm, "BEZ ROUTINGU")
    document.line(left, parties_bottom, right, parties_bottom)

    routing_bottom = parties_bottom - 26 * mm
    route_middle = left + width * 0.33
    document.line(route_middle, parties_bottom, route_middle, routing_bottom)
    document.setFillColor(grey)
    document.setFont(bold, 4.8)
    document.drawString(inner_left, parties_bottom - 4 * mm, "KIERUNEK / DESTINATION")
    document.setFillColor(dark)
    document.setFont(bold, 11)
    document.drawString(inner_left, parties_bottom - 10.5 * mm, "PL-TEST")
    document.setFont(bold, 5)
    document.drawString(inner_left, parties_bottom - 15 * mm, "TRASA TESTOWA")
    document.setFont(regular, 4.5)
    document.drawString(inner_left, parties_bottom - 19 * mm, "BRAK SORTOWANIA DPD")
    reference_text = " | ".join(references) or "BRAK REFERENCJI"
    document.setFillColor(grey)
    document.setFont(bold, 4.8)
    document.drawString(route_middle + 2 * mm, parties_bottom - 4 * mm, "REFERENCJA")
    _draw_label_line(
        document,
        reference_text,
        left=route_middle + 2 * mm,
        baseline=parties_bottom - 7.5 * mm,
        max_width=right - route_middle - 4 * mm,
        font_name=bold,
        font_size=5.5,
    )
    document.setFillColor(grey)
    document.setFont(bold, 4.8)
    document.drawString(route_middle + 2 * mm, parties_bottom - 11.5 * mm, "ZAWARTOŚĆ PACZKI")
    item_lines = _mock_label_item_lines(items)
    if not item_lines:
        item_lines = [parcel.get("content") or "Materiały serwisowe"]
    for line_index, line in enumerate(item_lines):
        _draw_label_line(
            document,
            line,
            left=route_middle + 2 * mm,
            baseline=parties_bottom - 15.5 * mm - line_index * 3.2 * mm,
            max_width=right - route_middle - 4 * mm,
            font_name=bold if line.startswith("+") else regular,
            font_size=4.5,
        )
    document.line(left, routing_bottom, right, routing_bottom)

    barcode_area_height = routing_bottom - bottom
    matrix_size = min(24 * mm, barcode_area_height - 8 * mm)
    matrix_left = inner_right - matrix_size
    matrix_bottom = bottom + 4 * mm
    _draw_test_matrix(
        document,
        left=matrix_left,
        bottom=matrix_bottom,
        size=matrix_size,
    )
    barcode_left = inner_left
    barcode_width = matrix_left - barcode_left - 3 * mm
    document.setFillColor(grey)
    document.setFont(bold, 4.8)
    document.drawString(barcode_left, routing_bottom - 4 * mm, "NUMER PRZESYŁKI / TRACK")
    _draw_label_line(
        document,
        tracking,
        left=barcode_left,
        baseline=routing_bottom - 9 * mm,
        max_width=barcode_width,
        font_name=bold,
        font_size=7.3,
    )
    barcode = code128.Code128(tracking, barHeight=11 * mm, barWidth=0.26 * mm)
    barcode_scale = min(1.0, barcode_width / barcode.width)
    document.saveState()
    document.translate(barcode_left, bottom + 6 * mm)
    document.scale(barcode_scale, 1)
    barcode.drawOn(document, 0, 0)
    document.restoreState()
    document.setFillColor(grey)
    document.setFont(regular, 4.2)
    document.drawString(barcode_left, bottom + 3 * mm, "Kod zawiera wyłącznie numer MOCK")

    document.saveState()
    try:
        document.setFillAlpha(0.11)
    except AttributeError:
        document.setFillColor(light_grey)
    else:
        document.setFillColor(red)
    document.translate(left + width / 2, bottom + height / 2)
    document.rotate(34)
    document.setFont(bold, 20)
    document.drawCentredString(0, -6, "TEST — NIE NADAWAĆ")
    document.restoreState()
    document.restoreState()


def build_mock_shipping_label(
    payload: dict[str, Any],
    tracking_number: str,
    items: list[dict[str, Any]] | None = None,
) -> bytes:
    """Generuje bezpieczną etykietę testową w lewym górnym polu arkusza A4."""
    output = io.BytesIO()
    document = pdf_canvas.Canvas(
        output,
        pagesize=A4,
        pageCompression=1,
    )
    page_width, page_height = A4
    fallback_references = [
        _plain_text(item.get("order_number"))
        for item in items or []
        if _plain_text(item.get("order_number"))
    ]
    _draw_mock_shipping_label(
        document,
        payload=payload,
        tracking_number=tracking_number,
        left=0,
        bottom=page_height / 2,
        width=page_width / 2,
        height=page_height / 2,
        fallback_references=list(dict.fromkeys(fallback_references)),
        items=items,
    )
    document.save()
    return output.getvalue()


def build_mock_shipping_label_sheet(entries: list[dict[str, Any]]) -> bytes:
    """Buduje realistyczny testowy arkusz A4 2×2 od lewego górnego pola."""
    if not entries:
        raise ValueError("Brak etykiet testowych do wydruku.")
    output = io.BytesIO()
    document = pdf_canvas.Canvas(output, pagesize=A4, pageCompression=1)
    page_width, page_height = A4
    cell_width = page_width / 2
    cell_height = page_height / 2
    positions = ((0, 1), (0, 0), (1, 1), (1, 0))

    for index, entry in enumerate(entries):
        if index and index % 4 == 0:
            document.showPage()
        column, row = positions[index % 4]
        left = column * cell_width
        bottom = row * cell_height
        payload = entry.get("payload") or {}
        _draw_mock_shipping_label(
            document,
            payload=payload,
            tracking_number=_plain_text(entry.get("tracking_number")) or "MOCK-BRAK-NUMERU",
            left=left,
            bottom=bottom,
            width=cell_width,
            height=cell_height,
            sequence=f"{index + 1}/{len(entries)}",
            items=entry.get("items") or [],
        )
    document.save()
    return output.getvalue()


def pack_shipping_labels_four_up(content: bytes, *, label_count: int) -> bytes:
    """Składa osobne górne pola A6 przewoźnika w arkusz A4 2×2 bez skalowania."""
    if label_count <= 0:
        raise ValueError("Brak etykiet do ułożenia na arkuszu A4.")
    reader = PdfReader(io.BytesIO(content))
    pages = list(reader.pages)
    if not pages:
        raise ValueError("Dokument DPD nie zawiera stron etykiet.")
    if label_count == 1 or len(pages) < label_count:
        return content
    if len(pages) != label_count:
        raise ValueError("Liczba stron dokumentu DPD nie odpowiada liczbie etykiet.")

    first_width = float(pages[0].mediabox.width)
    first_height = float(pages[0].mediabox.height)
    cell_width = first_width / 2
    cell_height = first_height / 2
    positions = ((0, 1), (0, 0), (1, 1), (1, 0))
    writer = PdfWriter()

    for index, source_page in enumerate(pages):
        page_width = float(source_page.mediabox.width)
        page_height = float(source_page.mediabox.height)
        if abs(page_width - first_width) > 1 or abs(page_height - first_height) > 1:
            raise ValueError("Strony dokumentu DPD mają różne wymiary.")
        if index % 4 == 0:
            writer.add_blank_page(width=first_width, height=first_height)
        target_page = writer.pages[-1]
        source_page.cropbox.lower_left = (0, cell_height)
        source_page.cropbox.upper_right = (cell_width, first_height)
        column, row = positions[index % 4]
        target_page.merge_transformed_page(
            source_page,
            Transformation().translate(
                tx=column * cell_width,
                ty=row * cell_height - cell_height,
            ),
            expand=False,
        )

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def build_shipping_packing_summary(entries: list[dict[str, Any]]) -> bytes:
    """Buduje tabelę zleceń, fizycznych paczek i części dla magazynu."""
    styles = _styles()
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=20 * mm,
        title="Zestawienie kompletacji wysyłek",
    )
    package_keys = {
        str(entry.get("tracking_number") or f"bez-numeru-{index}")
        for index, entry in enumerate(entries)
    }
    story = [
        Paragraph("Zestawienie kompletacji wysyłek", styles["title"]),
        Paragraph(
            _text(
                f"Wygenerowano: {datetime.now():%Y-%m-%d %H:%M} "
                f"• Zlecenia: {len(entries)} • Paczki: {len(package_keys)}"
            ),
            styles["body"],
        ),
        Spacer(1, 4 * mm),
    ]
    rows = [
        [
            Paragraph("Zlecenie", styles["body_bold"]),
            Paragraph("Klient / odbiorca", styles["body_bold"]),
            Paragraph("Numer przesyłki", styles["body_bold"]),
            Paragraph("Indeks", styles["body_bold"]),
            Paragraph("Część / towar", styles["body_bold"]),
            Paragraph("Ilość", styles["body_bold"]),
        ]
    ]
    for entry in entries:
        items = entry.get("items") or [{}]
        address = entry.get("address") or {}
        customer = "<br/>".join(
            value
            for value in (
                _text(address.get("company_name") or "Brak nazwy"),
                _text(address.get("contact_name")),
            )
            if value
        )
        for item in items:
            rows.append(
                [
                    Paragraph(_text(entry.get("order_number")), styles["small_center"]),
                    Paragraph(customer, styles["small"]),
                    Paragraph(_text(entry.get("tracking_number")), styles["small"]),
                    Paragraph(_text(item.get("item_index") or "—"), styles["small"]),
                    Paragraph(_text(item.get("item_name") or "Brak pozycji"), styles["small"]),
                    Paragraph(
                        f"{_quantity(item.get('quantity'))} {_text(item.get('unit') or 'szt.')}",
                        styles["small_center"],
                    ),
                ]
            )
    table = Table(
        rows,
        colWidths=[24 * mm, 47 * mm, 38 * mm, 34 * mm, 99 * mm, 24 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D1E9FF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#98A2B3")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    document.build(story, onFirstPage=_page_frame, onLaterPages=_page_frame)
    return output.getvalue()


def merge_shipping_pdf_documents(documents: list[bytes]) -> bytes:
    """Łączy dokumenty PDF bez modyfikowania stron etykiet przewoźnika."""
    writer = PdfWriter()
    for content in documents:
        reader = PdfReader(io.BytesIO(content))
        for page in reader.pages:
            writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


__all__ = [
    "build_mock_shipping_label",
    "build_mock_shipping_label_sheet",
    "build_shipping_packing_summary",
    "merge_shipping_pdf_documents",
    "pack_shipping_labels_four_up",
    "shipping_pdf_fonts",
]
