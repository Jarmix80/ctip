"""Interfejs webowy operacyjnego widoku FLOW."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette import status

from app.services.contracts_proforma import ensure_proforma_pdf_file, load_proforma_preview_data

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["flow-ui"])

INVOICE_PREVIEW_SAMPLE = {
    "document_title": "Faktura Pro Forma",
    "document_number": "2/proforma/2026",
    "place_of_issue": "Komorniki",
    "service_date": "13.03.2026",
    "issue_date": "13.03.2026",
    "payment_due_date": "27.03.2026",
    "payment_method": "Gotówka",
    "buyer": {
        "name": "ZANOX SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
        "street": "ul. Grzybowska 80/82/700",
        "postal_code": "00-844",
        "city": "Warszawa",
        "country_code": "PL",
        "nip": "5272917850",
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
            "name": "IMCTEST",
            "serial_number": "test12345",
            "quantity": "1,00",
            "unit": "szt.",
            "net_price": "111,00 zł",
            "net_value": "111,00 zł",
            "vat_rate": "23 %",
            "vat_value": "25,53 zł",
            "gross_value": "136,53 zł",
        }
    ],
    "totals": {
        "net": "111,00 zł",
        "vat": "25,53 zł",
        "gross": "136,53 zł",
        "paid": "0,00 zł",
        "remaining": "136,53 zł",
        "gross_words": "sto trzydzieści sześć złotych 53/100 gr.",
    },
    "notes": [
        "Towar pozostaje własnością KSERO-PARTNER SK do czasu całkowitej zapłaty.",
        "Za zwłokę naliczane są odsetki ustawowe.",
        "Wizualizacja została przygotowana na podstawie dokumentu z inbox/faktura.",
    ],
    "issuer": "Marcin Jarmuszkiewicz",
}


@router.get("/flow", response_class=HTMLResponse)
async def flow_page(request: Request) -> HTMLResponse:
    """Ekran operacyjny FLOW z sekcjami umow i urzadzen."""
    return templates.TemplateResponse("flow/index.html", {"request": request})


@router.get("/flow/proforma-wizualizacja", response_class=HTMLResponse)
async def flow_invoice_preview_page(request: Request) -> HTMLResponse:
    """Podglad wizualny proformy na podstawie przykladowych danych z inbox/faktura."""
    return templates.TemplateResponse(
        "flow/invoice_preview.html",
        {
            "request": request,
            "invoice": INVOICE_PREVIEW_SAMPLE,
            "preview_kicker": "FLOW / Wizualizacja dokumentu",
            "preview_title": INVOICE_PREVIEW_SAMPLE["document_title"],
            "preview_lead": (
                "Podglad oparty o rzeczywisty dokument z katalogu `inbox/faktura`. "
                "To jest wzorzec wizualny do dalszego podpiecia pod dane Firebird i PDF."
            ),
            "back_url": "/flow",
            "alternate_url": "/flow/proforma-wizualizacja1",
            "alternate_label": "Wariant bardziej oryginalny",
        },
    )


@router.get("/flow/proforma-wizualizacja1", response_class=HTMLResponse)
async def flow_invoice_preview_page_v1(request: Request) -> HTMLResponse:
    """Wierniejsza wizualizacja proformy oparta o uklad oryginalnego wydruku."""
    return templates.TemplateResponse(
        "flow/invoice_preview_v1.html",
        {
            "request": request,
            "invoice": INVOICE_PREVIEW_SAMPLE,
            "preview_kicker": "FLOW / Proforma",
            "preview_title": "Wizualizacja 1",
            "preview_lead": (
                "Ten wariant jest celowo blizszy oryginalnemu wydrukowi Menadzera Serwisu: "
                "wezsza kartka, uklad kolumnowy, lekkie kursywy i blok podsumowania po prawej."
            ),
            "back_url": "/flow",
            "alternate_url": "/flow/proforma-wizualizacja",
            "alternate_label": "Wariant bazowy",
        },
    )


@router.get("/flow/proforma/{proforma_firebird_id}", response_class=HTMLResponse)
async def flow_invoice_preview_live_page(
    request: Request,
    proforma_firebird_id: int,
    variant: str = Query(default="v1", pattern="^(base|v1)$"),
) -> HTMLResponse:
    """Podglad wygenerowanej proformy odczytanej bezposrednio z Firebird."""
    try:
        invoice = await _load_invoice_preview_data(proforma_firebird_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if variant == "base":
        return templates.TemplateResponse(
            "flow/invoice_preview.html",
            {
                "request": request,
                "invoice": invoice,
                "preview_kicker": "FLOW / Proforma z Firebird",
                "preview_title": invoice["document_title"],
                "preview_lead": (
                    "Dokument zostal odczytany z lokalnej Firebird i moze byc zapisany do PDF A4 "
                    "z poziomu przegladarki."
                ),
                "back_url": "/flow",
                "alternate_url": f"/flow/proforma/{proforma_firebird_id}?variant=v1",
                "alternate_label": "Wariant bardziej oryginalny",
            },
        )

    return templates.TemplateResponse(
        "flow/invoice_preview_v1.html",
        {
            "request": request,
            "invoice": invoice,
            "preview_kicker": "FLOW / Proforma z Firebird",
            "preview_title": f"Proforma {invoice['document_number']}",
            "preview_lead": (
                "Dokument zostal odczytany z lokalnej Firebird i moze byc zapisany do PDF A4 "
                "z poziomu przegladarki."
            ),
            "back_url": "/flow",
            "alternate_url": f"/flow/proforma/{proforma_firebird_id}?variant=base",
            "alternate_label": "Wariant bazowy",
        },
    )


@router.get("/flow/proforma/{proforma_firebird_id}/pdf")
async def flow_invoice_pdf_file(proforma_firebird_id: int) -> FileResponse:
    """Zwraca backendowy plik PDF wygenerowanej proformy."""
    try:
        pdf_path = await _ensure_invoice_pdf_file(proforma_firebird_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"proforma_{proforma_firebird_id}.pdf",
    )


async def _load_invoice_preview_data(proforma_firebird_id: int) -> dict:
    return await asyncio.to_thread(load_proforma_preview_data, proforma_firebird_id)


async def _ensure_invoice_pdf_file(proforma_firebird_id: int) -> Path:
    return await asyncio.to_thread(ensure_proforma_pdf_file, proforma_firebird_id)


__all__ = ["router"]
