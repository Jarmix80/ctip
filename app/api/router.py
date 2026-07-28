"""Główny router FastAPI składający poszczególne moduły."""

from fastapi import APIRouter

from app.api.routes import (
    admin_auth,
    admin_backup,
    admin_bot_identity,
    admin_call_sms,
    admin_config,
    admin_contacts,
    admin_contracts,
    admin_ctip,
    admin_delivery,
    admin_device,
    admin_email,
    admin_firebird,
    admin_forms,
    admin_google_sheets,
    admin_kp_repair,
    admin_mm,
    admin_sms,
    admin_status,
    admin_users,
    assistant,
    bot_identity,
    crm,
    health,
    operator_auth,
    operator_portal,
    portal_auth,
)

api_router = APIRouter()
api_router.include_router(bot_identity.router)
api_router.include_router(admin_bot_identity.router)
api_router.include_router(crm.operator_router)
api_router.include_router(crm.service_router)
api_router.include_router(assistant.router)
api_router.include_router(admin_backup.router)
api_router.include_router(admin_auth.router)
api_router.include_router(admin_call_sms.router)
api_router.include_router(admin_ctip.router)
api_router.include_router(admin_config.router)
api_router.include_router(admin_contracts.router)
api_router.include_router(admin_delivery.router)
api_router.include_router(admin_device.router)
api_router.include_router(admin_contacts.router)
api_router.include_router(admin_email.router)
api_router.include_router(admin_forms.router)
api_router.include_router(admin_firebird.router)
api_router.include_router(admin_google_sheets.router)
api_router.include_router(admin_kp_repair.router)
api_router.include_router(admin_mm.router)
api_router.include_router(admin_status.router)
api_router.include_router(admin_sms.router)
api_router.include_router(admin_users.router)
api_router.include_router(health.router)
api_router.include_router(operator_auth.router)
api_router.include_router(portal_auth.router)
api_router.include_router(operator_portal.router)
