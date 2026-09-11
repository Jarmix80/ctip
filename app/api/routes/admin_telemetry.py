"""Administracyjny podgląd pozyskiwania danych, bez sterowania źródłami."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.models import telemetry as tables

router = APIRouter(prefix="/admin/telemetry", tags=["telemetria"])
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
AdminContext = Annotated[tuple, Depends(get_admin_session_context)]
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))


async def telemetry_status(session, admin_context):
    """Udostępnia metadane wyłącznie administratorowi, bez surowych wiadomości i sekretów."""
    if admin_context[1].role != "admin":
        raise HTTPException(status_code=403, detail="Podgląd telemetrii wymaga administratora.")
    source_rows = (
        (
            await session.execute(
                select(
                    tables.source.c.id,
                    tables.source.c.name,
                    tables.source.c.kind,
                    tables.source.c.enabled,
                    tables.source.c.last_success_at,
                    tables.source.c.last_error,
                ).order_by(tables.source.c.name)
            )
        )
        .mappings()
        .all()
    )
    imports = (
        (
            await session.execute(
                select(tables.imports).order_by(tables.imports.c.started_at.desc()).limit(50)
            )
        )
        .mappings()
        .all()
    )
    issues = (
        (
            await session.execute(
                select(tables.issue).order_by(tables.issue.c.created_at.desc()).limit(50)
            )
        )
        .mappings()
        .all()
    )
    count = (await session.execute(select(func.count()).select_from(tables.record))).scalar_one()
    groups = (
        await session.execute(select(func.count(func.distinct(tables.record.c.semantic_key))))
    ).scalar_one()
    pending = (
        await session.execute(
            select(func.count())
            .select_from(tables.origin)
            .where(tables.origin.c.archive_status == "pending")
        )
    ).scalar_one()
    freshness = (
        (
            await session.execute(
                select(
                    tables.record.c.source_id,
                    func.max(tables.record.c.observed_at).label("observed_at"),
                )
                .where(
                    tables.record.c.kind.in_(["reading", "supply_event", "remote_event"]),
                    tables.record.c.measurements != {},
                    tables.record.c.observed_at <= func.now(),
                )
                .group_by(tables.record.c.source_id)
            )
        )
        .mappings()
        .all()
    )
    dates = {row["source_id"]: row["observed_at"] for row in freshness}
    return {
        "sources": [{**row, "last_observed_at": dates.get(row["id"])} for row in source_rows],
        "imports": [dict(row) for row in imports],
        "issues": [dict(row) for row in issues],
        "records": count,
        "semantic_groups": groups,
        "archive_pending": pending,
    }


@router.get("/status")
async def status(session: DatabaseSession, admin_context: AdminContext):
    """Zwraca stan źródeł, ostatnie importy i ostrzeżenia jakości."""
    return await telemetry_status(session, admin_context)


@router.get("")
async def page(
    request: Request,
    session: DatabaseSession,
    admin_context: AdminContext,
):
    """Pokazuje niezależny panel bez automatycznego uruchamiania importów."""
    result = await telemetry_status(session, admin_context)
    return templates.TemplateResponse(request=request, name="admin/telemetry.html", context=result)
