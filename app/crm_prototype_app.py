"""Izolowana aplikacja laboratorium Centrum Obsługi."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.crm import lab_router
from app.core.config import settings
from app.web.crm_ui import router as crm_ui_router


def create_crm_prototype_app() -> FastAPI:
    """Tworzy aplikację udostępniającą wyłącznie LAB CRM i jego API."""
    application = FastAPI(
        title="CTIP Centrum Obsługi — prototyp",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.middleware("http")
    async def secure_lab(request: Request, call_next):
        """Ogranicza LAB do zaufanej sieci i nadaje nagłówki iframe/noindex."""
        client_host = request.client.host if request.client else None
        if not settings.is_panel_client_allowed(client_host):
            return JSONResponse(
                status_code=403,
                content={"detail": "LAB jest dostępny wyłącznie z zaufanej sieci."},
                headers={"Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            f"frame-ancestors {settings.crm_frame_ancestors}"
        )
        return response

    @application.get("/", include_in_schema=False)
    async def prototype_root() -> RedirectResponse:
        """Przekierowuje stronę główną izolowanej instancji do prototypu CRM."""
        return RedirectResponse(url="/crm")

    application.mount("/static", StaticFiles(directory="app/static"), name="static")
    application.include_router(lab_router)
    application.include_router(crm_ui_router)
    return application


app = create_crm_prototype_app()


__all__ = ["app", "create_crm_prototype_app"]
