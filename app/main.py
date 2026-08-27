"""Wejście aplikacji FastAPI."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routes.admin_backup import start_backup_scheduler, stop_backup_scheduler
from app.core.config import settings
from app.services.contracts_mailbox_scheduler import (
    start_contracts_mailbox_scheduler,
    stop_contracts_mailbox_scheduler,
)
from app.services.contracts_workflow_maintenance import (
    start_contracts_workflow_maintenance_scheduler,
    stop_contracts_workflow_maintenance_scheduler,
)
from app.services.workflow_sheet_status_cache import (
    ensure_workflow_sheet_status_cache_table,
    start_workflow_sheet_status_cache_scheduler,
    stop_workflow_sheet_status_cache_scheduler,
)
from app.web.admin_ui import router as admin_ui_router
from app.web.assistant_ui import router as assistant_ui_router
from app.web.contracts_ui import router as contracts_ui_router
from app.web.device_ui import router as device_ui_router
from app.web.flow_ui import router as flow_ui_router
from app.web.form_ui import router as form_ui_router
from app.web.genform_ui import router as genform_ui_router
from app.web.mm_ui import router as mm_ui_router
from app.web.operator_ui import router as operator_ui_router
from app.web.root_ui import router as root_ui_router
from app.web.shipping_ui import router as shipping_ui_router


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    """Obsługuje zadania startowe i zamknięcie aplikacji."""
    backup_scheduler_started = False
    workflow_sheet_status_scheduler_started = False
    contracts_workflow_maintenance_scheduler_started = False
    contracts_mailbox_scheduler_started = False
    await ensure_workflow_sheet_status_cache_table()
    if settings.backup_scheduler_enabled and settings.backup_execution_active:
        await start_backup_scheduler()
        backup_scheduler_started = True
    if settings.workflow_sheet_status_cache_scheduler_enabled:
        await start_workflow_sheet_status_cache_scheduler()
        workflow_sheet_status_scheduler_started = True
    if settings.contracts_workflow_maintenance_scheduler_enabled:
        await start_contracts_workflow_maintenance_scheduler()
        contracts_workflow_maintenance_scheduler_started = True
    if settings.contracts_mailbox_scheduler_enabled:
        await start_contracts_mailbox_scheduler()
        contracts_mailbox_scheduler_started = True
    try:
        yield
    finally:
        if backup_scheduler_started:
            await stop_backup_scheduler()
        if workflow_sheet_status_scheduler_started:
            await stop_workflow_sheet_status_cache_scheduler()
        if contracts_workflow_maintenance_scheduler_started:
            await stop_contracts_workflow_maintenance_scheduler()
        if contracts_mailbox_scheduler_started:
            await stop_contracts_mailbox_scheduler()


def create_app() -> FastAPI:
    """Buduje obiekt FastAPI wraz z trasami paneli i statycznymi zasobami."""
    app = FastAPI(title=settings.app_title, version=settings.app_version, lifespan=_app_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        """Dodaje podstawowe naglowki ograniczajace powierzchnie ataku."""
        client_host = request.client.host if request.client else None
        if not settings.is_panel_client_allowed(client_host):
            return JSONResponse(
                status_code=403,
                content={"detail": "Dostęp do panelu jest dozwolony wyłącznie z sieci LAN."},
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                },
            )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.path.startswith(
            (
                "/admin",
                "/auth",
                "/flow",
                "/contracts",
                "/choice",
                "/operator",
                "/shipping",
            )
        ):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(api_router)
    app.include_router(assistant_ui_router)
    app.include_router(admin_ui_router)
    app.include_router(contracts_ui_router)
    app.include_router(device_ui_router)
    app.include_router(flow_ui_router)
    app.include_router(form_ui_router)
    app.include_router(genform_ui_router)
    app.include_router(operator_ui_router)
    app.include_router(shipping_ui_router)
    app.include_router(root_ui_router)
    app.include_router(mm_ui_router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.mount(
        "/raport",
        StaticFiles(directory="docs/raport", html=True),
        name="raport",
    )
    return app


app = create_app()
