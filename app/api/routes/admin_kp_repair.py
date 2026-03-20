"""API operacji naprawczych dla MASZYNA.EWIDENCJA (KP/xxxx)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_config import (
    load_firebird_config,
    load_firebird_vmaintenance_config,
    load_kp_repair_source_config,
    settings_store,
)
from app.core.config import settings
from app.schemas.admin import (
    KpRepairActionRequest,
    KpRepairActionResponse,
    KpRepairCsvTestResponse,
    KpRepairSummaryResponse,
)
from app.services.audit import record_audit
from app.services.kp_repair import (
    FirebirdConnConfig,
    KpRepairSourceConfig,
    build_summary,
    clear_markers,
    rebuild_markers,
    test_csv_source,
)

router = APIRouter(prefix="/admin/kp-repair", tags=["admin-kp-repair"])
KP_REPAIR_TIMEOUT_SECONDS = 900


def _assert_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora.",
        )


async def _run_blocking_kp_operation(
    operation_name: str,
    func,
    *args,
    **kwargs,
):
    """Uruchamia blokującą operację KP w wątku roboczym z limitem czasu."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args, **kwargs),
            timeout=KP_REPAIR_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"Przekroczono limit czasu operacji `{operation_name}` "
                f"({KP_REPAIR_TIMEOUT_SECONDS}s)."
            ),
        ) from exc


def _main_firebird_connection(
    *,
    mode: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str | None,
    charset: str,
    role: str | None,
    local_copy_path: str,
) -> FirebirdConnConfig:
    mode_value = (mode or "network").strip().lower()
    if mode_value == "local":
        path = Path(local_copy_path.strip()).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return FirebirdConnConfig(
            host="127.0.0.1",
            port=port,
            database=str(path),
            user=user,
            password=password,
            charset=charset,
            role=role,
        )
    return FirebirdConnConfig(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        charset=charset,
        role=role,
    )


async def _load_configs(
    session: AsyncSession,
) -> tuple[FirebirdConnConfig, FirebirdConnConfig, KpRepairSourceConfig]:
    fb_main = await load_firebird_config(session)
    fb_v = await load_firebird_vmaintenance_config(session)
    source = await load_kp_repair_source_config(session)

    stored_main = await settings_store.get_namespace(session, "firebird")
    main_password = stored_main.get("password") or settings.fb_password

    stored_v = await settings_store.get_namespace(session, "firebird_vmaintenance")
    v_password = stored_v.get("password") or settings.fb_v_password

    main_cfg = _main_firebird_connection(
        mode=fb_main.mode,
        host=fb_main.host,
        port=fb_main.port,
        database=fb_main.database,
        user=fb_main.user,
        password=main_password,
        charset=fb_main.charset,
        role=fb_main.role,
        local_copy_path=fb_main.local_copy_path,
    )
    v_cfg = FirebirdConnConfig(
        host=fb_v.host,
        port=fb_v.port,
        database=fb_v.database,
        user=fb_v.user,
        password=v_password,
        charset=fb_v.charset,
        role=fb_v.role,
    )
    source_cfg = KpRepairSourceConfig(
        csv_directory=source.csv_directory,
        csv_pattern=source.csv_pattern,
        email_lookback_months=source.email_lookback_months,
    )
    return main_cfg, v_cfg, source_cfg


@router.post(
    "/csv-source/test", response_model=KpRepairCsvTestResponse, summary="Test katalogu CSV"
)
async def test_kp_csv_source(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> KpRepairCsvTestResponse:
    """Weryfikuje, czy katalog CSV zawiera pliki wejściowe DPLAC."""
    _, admin_user = admin_context
    _assert_admin(admin_user.role)
    _, _, source_cfg = await _load_configs(session)
    result = await _run_blocking_kp_operation(
        "csv-source-test",
        test_csv_source,
        source_cfg,
    )
    return KpRepairCsvTestResponse(
        success=result.success,
        message=result.message,
        directory_exists=result.directory_exists,
        files_found=result.files_found,
        latest_file=result.latest_file,
    )


@router.get("/summary", response_model=KpRepairSummaryResponse, summary="Raport ilości V/E/R")
async def kp_repair_summary(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> KpRepairSummaryResponse:
    """Tworzy raport ilości oznaczeń V/E/R i źródeł wejściowych."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    main_cfg, v_cfg, source_cfg = await _load_configs(session)
    try:
        result = await _run_blocking_kp_operation(
            "summary",
            build_summary,
            main_config=main_cfg,
            v_config=v_cfg,
            source_config=source_cfg,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Nie udało się wygenerować raportu KP: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="kp_repair_summary",
        client_ip=admin_session.client_ip,
        payload={
            "marker_counts": result.marker_counts,
            "matched_counts": result.matched_counts,
            "report_file": result.report_file,
        },
    )
    await session.commit()

    return KpRepairSummaryResponse(
        marker_counts=result.marker_counts,
        source_counts=result.source_counts,
        matched_counts=result.matched_counts,
        latest_csv_file=result.latest_csv_file,
        report_file=result.report_file,
    )


@router.post("/clear", response_model=KpRepairActionResponse, summary="Usuń markery V/E/R")
async def kp_repair_clear(
    payload: KpRepairActionRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> KpRepairActionResponse:
    """Usuwa markery V/E/R i formy legacy (EMAIL/REMOTE/VM)."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    main_cfg, _, _ = await _load_configs(session)
    try:
        result = await _run_blocking_kp_operation(
            "clear",
            clear_markers,
            main_config=main_cfg,
            commit=payload.commit,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Nie udało się wykonać czyszczenia KP: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="kp_repair_clear",
        client_ip=admin_session.client_ip,
        payload={
            "commit": payload.commit,
            "candidates": result.candidates,
            "updated": result.updated,
            "skipped": result.skipped,
            "errors": result.errors,
            "report_file": result.report_file,
            "map_file": result.map_file,
            "rollback_file": result.rollback_file,
        },
    )
    await session.commit()

    return KpRepairActionResponse(
        success=result.success,
        message=result.message,
        commit=result.commit,
        candidates=result.candidates,
        updated=result.updated,
        skipped=result.skipped,
        errors=result.errors,
        marker_counts_before=result.marker_counts_before,
        marker_counts_after=result.marker_counts_after,
        source_counts=result.source_counts,
        report_file=result.report_file,
        map_file=result.map_file,
        rollback_file=result.rollback_file,
    )


@router.post("/rebuild", response_model=KpRepairActionResponse, summary="Retaguj markery V/E/R")
async def kp_repair_rebuild(
    payload: KpRepairActionRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> KpRepairActionResponse:
    """Czyści i odtwarza markery V/E/R na podstawie źródeł wejściowych."""
    admin_session, admin_user = admin_context
    _assert_admin(admin_user.role)

    main_cfg, v_cfg, source_cfg = await _load_configs(session)
    try:
        result = await _run_blocking_kp_operation(
            "rebuild",
            rebuild_markers,
            main_config=main_cfg,
            v_config=v_cfg,
            source_config=source_cfg,
            commit=payload.commit,
            email_lookback_months=payload.email_lookback_months,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Nie udało się wykonać retagowania KP: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="kp_repair_rebuild",
        client_ip=admin_session.client_ip,
        payload={
            "commit": payload.commit,
            "email_lookback_months_override": payload.email_lookback_months,
            "candidates": result.candidates,
            "updated": result.updated,
            "skipped": result.skipped,
            "errors": result.errors,
            "report_file": result.report_file,
            "map_file": result.map_file,
            "rollback_file": result.rollback_file,
        },
    )
    await session.commit()

    return KpRepairActionResponse(
        success=result.success,
        message=result.message,
        commit=result.commit,
        candidates=result.candidates,
        updated=result.updated,
        skipped=result.skipped,
        errors=result.errors,
        marker_counts_before=result.marker_counts_before,
        marker_counts_after=result.marker_counts_after,
        source_counts=result.source_counts,
        report_file=result.report_file,
        map_file=result.map_file,
        rollback_file=result.rollback_file,
    )


__all__ = ["router"]
