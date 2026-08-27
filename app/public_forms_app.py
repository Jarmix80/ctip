"""Osobna aplikacja publiczna udostępniająca wyłącznie formularze klienta."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes.health import router as health_router
from app.core.config import settings
from app.web.form_ui import router as form_ui_router


def create_public_forms_app() -> FastAPI:
    """Buduje ograniczoną aplikację publiczną dla subdomeny formularzy."""
    app = FastAPI(
        title=f"{settings.app_title} Public Forms",
        version=settings.app_version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.public_form_trusted_hosts,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        """Dodaje nagłówki bezpieczeństwa dla publicznego formularza."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.path == "/" or request.url.path.startswith("/formularz/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def public_forms_root() -> HTMLResponse:
        """Zwraca prostą stronę informacyjną dla subdomeny formularzy."""
        return HTMLResponse(
            """
<!DOCTYPE html>
<html lang="pl">
  <head>
    <meta charset="utf-8">
    <title>Formularze CTIP</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #f3f6fb;
        color: #173055;
        font-family: "Open Sans", Arial, sans-serif;
        padding: 24px;
      }
      main {
        width: min(720px, 100%);
        background: #fff;
        border: 1px solid #d5dce6;
        border-radius: 18px;
        box-shadow: 0 18px 44px rgba(23, 48, 85, 0.12);
        padding: 28px;
      }
      h1 {
        margin: 0;
        font-size: 28px;
      }
      p {
        margin: 14px 0 0;
        line-height: 1.6;
        color: #52637d;
      }
      code {
        background: #eef3fb;
        border-radius: 8px;
        padding: 2px 6px;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Publiczny formularz CTIP</h1>
      <p>Ta subdomena obsługuje wyłącznie jednorazowe formularze klienta.</p>
      <p>Użyj pełnego linku otrzymanego w wiadomości e-mail lub SMS, np. <code>/formularz/&lt;token&gt;</code>.</p>
    </main>
  </body>
</html>
            """.strip()
        )

    app.include_router(health_router)
    app.include_router(form_ui_router)
    return app


app = create_public_forms_app()
