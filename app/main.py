"""Wejście aplikacji FastAPI."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.web.admin_ui import router as admin_ui_router
from app.web.operator_ui import router as operator_ui_router
from app.web.root_ui import router as root_ui_router


def create_app() -> FastAPI:
    """Buduje obiekt FastAPI wraz z trasami paneli i statycznymi zasobami."""
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
    app.include_router(admin_ui_router)
    app.include_router(operator_ui_router)
    app.include_router(root_ui_router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.mount(
        "/raport",
        StaticFiles(directory="docs/raport", html=True),
        name="raport",
    )
    return app


app = create_app()
