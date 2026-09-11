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
                    tables.source.c.checkpoint,
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
                    tables.record.c.kind.in_(
                        ["reading", "supply_event", "remote_event", "daily_snapshot"]
                    ),
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
    billing = (
        (
            await session.execute(
                select(
                    tables.record.c.source_id,
                    func.max(
                        tables.record.c.payload["__ctip_billing__"]["start"].as_string()
                    ).label("period"),
                )
                .where(tables.record.c.kind == "billing_period")
                .group_by(tables.record.c.source_id)
            )
        )
        .mappings()
        .all()
    )
    periods = {row["source_id"]: row["period"] for row in billing}
    daily = (
        (
            await session.execute(
                select(
                    tables.record.c.source_id,
                    func.count(func.distinct(tables.record.c.external_key)).label("days"),
                )
                .where(tables.record.c.kind == "daily_snapshot")
                .group_by(tables.record.c.source_id)
            )
        )
        .mappings()
        .all()
    )
    daily_counts = {row["source_id"]: row["days"] for row in daily}
    mail_counts = (
        (
            await session.execute(
                select(
                    tables.mail_delivery.c.decision,
                    tables.mail_delivery.c.move_status,
                    func.count().label("count"),
                ).group_by(tables.mail_delivery.c.decision, tables.mail_delivery.c.move_status)
            )
        )
        .mappings()
        .all()
    )
    return {
        "sources": [
            {
                **{key: value for key, value in row.items() if key != "checkpoint"},
                "last_observed_at": dates.get(row["id"]),
                "last_billing_period": periods.get(row["id"]),
                "daily_snapshots": daily_counts.get(row["id"], 0),
                "daily_completed": row["checkpoint"].get("daily2", {}).get("completed"),
                "daily_in_progress": row["checkpoint"].get("daily2", {}).get("in_progress", False),
                "mail_retry": (
                    len(row["checkpoint"].get("retry", [])) if row["name"] == "remote_mail" else 0
                ),
            }
            for row in source_rows
        ],
        "imports": [dict(row) for row in imports],
        "issues": [dict(row) for row in issues],
        "records": count,
        "semantic_groups": groups,
        "archive_pending": pending,
        "mail_deliveries": [dict(row) for row in mail_counts],
        "mail_move_pending": sum(
            row["count"] for row in mail_counts if row["move_status"] == "pending"
        ),
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
