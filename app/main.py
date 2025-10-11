"""Wejście aplikacji FastAPI."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings


def create_app() -> FastAPI:
    """Buduje obiekt FastAPI."""
    app = FastAPI(title=settings.app_title, version=settings.app_version)

    # Domyślne CORS – w razie potrzeby zawęzimy listę domen.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    return app


app = create_app()
