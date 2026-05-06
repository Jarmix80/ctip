"""Interfejs webowy operacyjnego widoku FLOW."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.db.session import get_db_session
from app.services.contracts_dashboard import (
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)
from app.services.contracts_proforma import (
    build_proforma_download_filename,
    build_proforma_pdf_bytes,
    load_proforma_preview_data,
)

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["flow-ui"])

INVOICE_PREVIEW_SAMPLE = {
    "document_title": "Faktura Pro Forma",
    "document_number": "18/proforma/2026",
    "place_of_issue": "Komorniki",
    "service_date": "20.04.2026",
    "issue_date": "20.04.2026",
    "payment_due_date": "04.05.2026",
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


@router.get("/flow", response_class=HTMLResponse)
async def flow_page(request: Request) -> HTMLResponse:
    """Ekran operacyjny FLOW z sekcjami umow i urzadzen."""
    return templates.TemplateResponse("flow/index.html", {"request": request})


@router.get("/flow/proforma-wizualizacja", response_class=HTMLResponse)
async def flow_invoice_preview_page(request: Request) -> HTMLResponse:
    """Podglad wizualny proformy na podstawie wzorca z inbox/FPROFORMA.pdf."""
    return templates.TemplateResponse(
        "flow/invoice_preview.html",
        {
            "request": request,
            "invoice": INVOICE_PREVIEW_SAMPLE,
            "preview_kicker": "FLOW / Wizualizacja dokumentu",
            "preview_title": INVOICE_PREVIEW_SAMPLE["document_title"],
            "preview_lead": (
                "Podglad oparty o rzeczywisty wzorzec z pliku `inbox/FPROFORMA.pdf`. "
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
                "Ten wariant jest celowo blizszy oryginalnemu wydrukowi z `inbox/FPROFORMA.pdf`: "
                "blok nabywcy u gory, wąska kartka, uklad metadanych, podpisy i blok ostrzeżenia w stopce."
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
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Podglad wygenerowanej proformy odczytanej bezposrednio z Firebird."""
    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
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
                "backend_pdf_url": f"/flow/proforma/{proforma_firebird_id}/pdf",
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
            "backend_pdf_url": f"/flow/proforma/{proforma_firebird_id}/pdf",
            "back_url": "/flow",
            "alternate_url": f"/flow/proforma/{proforma_firebird_id}?variant=base",
            "alternate_label": "Wariant bazowy",
        },
    )


@router.get("/flow/proforma/{proforma_firebird_id}/pdf")
async def flow_invoice_pdf_file(
    proforma_firebird_id: int,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Zwraca backendowy plik PDF wygenerowanej proformy."""
    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
        try:
            invoice = await _load_invoice_preview_data(proforma_firebird_id)
            pdf_bytes = await _build_invoice_pdf_bytes(proforma_firebird_id, invoice=invoice)
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
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Nie udalo sie wygenerowac PDF: {type(exc).__name__}: {exc}",
            ) from exc

    download_filename = build_proforma_download_filename(
        str(invoice.get("document_number") or ""),
        fallback_id=proforma_firebird_id,
    )
    encoded_filename = quote(download_filename, safe="")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Cache-Control": "no-store",
        },
    )


async def _load_invoice_preview_data(proforma_firebird_id: int) -> dict:
    return await asyncio.to_thread(load_proforma_preview_data, proforma_firebird_id)


async def _build_invoice_pdf_bytes(
    proforma_firebird_id: int, *, invoice: dict | None = None
) -> bytes:
    return await asyncio.to_thread(build_proforma_pdf_bytes, proforma_firebird_id, invoice=invoice)


__all__ = ["router"]
