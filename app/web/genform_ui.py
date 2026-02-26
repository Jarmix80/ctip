"""Osobny interfejs generatora formularzy dla handlowca."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["genform-ui"])


@router.get("/genform", response_class=HTMLResponse)
async def genform_page(request: Request) -> HTMLResponse:
    """Ekran generatora formularzy (flow poza panelem administratora)."""
    return templates.TemplateResponse("genform/index.html", {"request": request})


__all__ = ["router"]
