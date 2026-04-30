"""Widok modułu CTIP AI Asystent."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["assistant-ui"])


@router.get("/assistant", response_class=HTMLResponse)
async def assistant_index(request: Request) -> HTMLResponse:
    """Zwraca ekran czatu asystenta."""
    return templates.TemplateResponse("assistant/index.html", {"request": request})


__all__ = ["router"]
