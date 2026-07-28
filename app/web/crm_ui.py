"""Interfejs prototypowy Centrum Obsługi."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["crm-ui"])


@router.get("/crm", response_class=HTMLResponse)
async def crm_page(request: Request) -> HTMLResponse:
    """Renderuje interaktywny prototyp Centrum Obsługi."""
    return templates.TemplateResponse(
        request=request,
        name="crm/index.html",
        context={
            "crm_public_prototype_mode": settings.crm_public_prototype_mode,
        },
    )


__all__ = ["router"]
