"""Główny router FastAPI składający poszczególne moduły."""

from fastapi import APIRouter

from app.api.routes import calls, contacts, health, sms

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(calls.router)
api_router.include_router(contacts.router)
api_router.include_router(sms.router)
