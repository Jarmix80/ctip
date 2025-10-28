"""Endpoint kontrolny stanu aplikacji."""

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health", summary="Status serwera")
async def healthcheck() -> dict[str, str]:
    """Zwraca podstawową informację o stanie aplikacji."""
    return {
        "status": "ok",
        "app": settings.app_title,
        "version": settings.app_version,
    }
