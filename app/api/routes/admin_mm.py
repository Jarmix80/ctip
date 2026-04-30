"""API raportu MM (przesuniecia miedzymagazynowe) dla widoku /mm."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.services import section_permissions
from app.services.contracts_dashboard import (
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)
from app.services.mm_dashboard import DEST_ALL, load_mm_dashboard_data

router = APIRouter(prefix="/admin/mm", tags=["admin-mm"])


@router.get("/dashboard", summary="Raport MM dla magazynow zlom/wynajem")
async def mm_dashboard_data(
    date_from: date = Query(default=date(2023, 6, 1)),  # noqa: B008
    date_to: date = Query(default_factory=date.today),  # noqa: B008
    destination: Literal["all", "zlom", "wynajem"] = Query(default=DEST_ALL),  # noqa: B008
    model: str | None = Query(default=None, max_length=200),  # noqa: B008
    search: str | None = Query(default=None, max_length=200),  # noqa: B008
    limit: int = Query(default=10000, ge=100, le=50000),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca pozycje MM przyjete na magazyny zlom/wynajem z filtrami."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    runtime = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(runtime):
        try:
            return await asyncio.to_thread(
                load_mm_dashboard_data,
                date_from=date_from,
                date_to=date_to,
                destination_filter=destination,
                model_filter=model,
                search_filter=search,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc


__all__ = ["router"]
