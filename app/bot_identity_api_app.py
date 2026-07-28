"""Izolowana aplikacja API katalogu tożsamości dla botów."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.bot_identity import router as identity_router
from app.api.routes.crm import service_router


def create_bot_identity_api_app() -> FastAPI:
    """Buduje minimalną aplikację bez paneli i publicznych tras CTIP."""
    application = FastAPI(
        title="CTIP Bot Identity API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.include_router(identity_router)
    application.include_router(service_router)

    @application.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Potwierdza gotowość procesu bez ujawniania stanu katalogu."""
        return {"status": "ok", "service": "ctip-bot-identity-api"}

    return application


app = create_bot_identity_api_app()
