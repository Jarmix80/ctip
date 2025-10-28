"""Główny router FastAPI składający poszczególne moduły."""

from fastapi import APIRouter

from app.api.routes import (
    admin_auth,
    admin_config,
    admin_contacts,
    admin_ctip,
    admin_email,
    admin_sms,
    admin_status,
    admin_users,
    calls,
    contacts,
    health,
    operator_auth,
    operator_portal,
    sms,
)

api_router = APIRouter()
api_router.include_router(admin_auth.router)
api_router.include_router(admin_ctip.router)
api_router.include_router(admin_config.router)
api_router.include_router(admin_contacts.router)
api_router.include_router(admin_email.router)
api_router.include_router(admin_status.router)
api_router.include_router(admin_sms.router)
api_router.include_router(admin_users.router)
api_router.include_router(health.router)
api_router.include_router(calls.router)
api_router.include_router(contacts.router)
api_router.include_router(operator_auth.router)
api_router.include_router(operator_portal.router)
api_router.include_router(sms.router)
