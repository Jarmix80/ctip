"""Operacje pomocnicze związane z konfiguracją Google Sheets dla FLOW."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.schemas.admin import (
    GoogleSheetsBootstrapResponse,
    GoogleSheetsTestRequest,
    GoogleSheetsTestResponse,
)
from app.services.audit import record_audit
from app.services.workflow_sheet_sync import (
    WorkflowSheetRuntimeConfig,
    bootstrap_workflow_sheet_headers,
    load_workflow_sheet_runtime_config,
    normalize_workflow_sheet_spreadsheet_id,
    test_workflow_sheet_connection,
)

router = APIRouter(prefix="/admin/google-sheets", tags=["admin-google-sheets"])


def _build_request_runtime_config(
    base_config: WorkflowSheetRuntimeConfig,
    payload: GoogleSheetsTestRequest | None,
) -> WorkflowSheetRuntimeConfig:
    payload_data = payload or GoogleSheetsTestRequest()
    return replace(
        base_config,
        enabled=True,
        credentials_path=(
            payload_data.credentials_path
            if payload_data.credentials_path is not None
            else base_config.credentials_path
        ),
        spreadsheet_id=(
            normalize_workflow_sheet_spreadsheet_id(payload_data.spreadsheet_id)
            if payload_data.spreadsheet_id is not None
            else base_config.spreadsheet_id
        ),
        workflow_devices_worksheet=(
            payload_data.workflow_devices_worksheet
            if payload_data.workflow_devices_worksheet is not None
            else base_config.workflow_devices_worksheet
        ),
    )


@router.post(
    "/test", response_model=GoogleSheetsTestResponse, summary="Sprawdź połączenie Google Sheets"
)
async def test_google_sheets_configuration(
    payload: GoogleSheetsTestRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GoogleSheetsTestResponse:
    """Weryfikuje konfigurację Google Sheets używaną przez synchronizację FLOW."""

    admin_session, admin_user = admin_context
    if admin_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora.",
        )

    config = await load_workflow_sheet_runtime_config(session)
    test_config = _build_request_runtime_config(config, payload)

    result = test_workflow_sheet_connection(test_config)

    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_google_sheets_test",
        client_ip=admin_session.client_ip,
        payload={
            "success": result.get("success"),
            "message": result.get("message"),
            "source": config.source,
            "credentials_path": test_config.credentials_path,
            "spreadsheet_id": test_config.spreadsheet_id,
            "workflow_devices_worksheet": test_config.workflow_devices_worksheet,
            "spreadsheet_title": result.get("spreadsheet_title"),
            "worksheet_title": result.get("worksheet_title"),
            "missing_headers": result.get("missing_headers") or [],
        },
    )
    await session.commit()

    return GoogleSheetsTestResponse(**result)


@router.post(
    "/bootstrap-headers",
    response_model=GoogleSheetsBootstrapResponse,
    summary="Przygotuj nagłówki FLOW w Google Sheets",
)
async def bootstrap_google_sheets_headers(
    payload: GoogleSheetsTestRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GoogleSheetsBootstrapResponse:
    """Tworzy lub uzupełnia wymagane nagłówki FLOW w aktywnym arkuszu."""

    admin_session, admin_user = admin_context
    if admin_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora.",
        )

    config = await load_workflow_sheet_runtime_config(session)
    bootstrap_config = _build_request_runtime_config(config, payload)
    result = bootstrap_workflow_sheet_headers(bootstrap_config)

    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_google_sheets_bootstrap_headers",
        client_ip=admin_session.client_ip,
        payload={
            "success": result.get("success"),
            "message": result.get("message"),
            "source": config.source,
            "credentials_path": bootstrap_config.credentials_path,
            "spreadsheet_id": bootstrap_config.spreadsheet_id,
            "workflow_devices_worksheet": bootstrap_config.workflow_devices_worksheet,
            "spreadsheet_title": result.get("spreadsheet_title"),
            "worksheet_title": result.get("worksheet_title"),
            "added_headers": result.get("added_headers") or [],
            "existing_headers": result.get("existing_headers") or [],
        },
    )
    await session.commit()

    return GoogleSheetsBootstrapResponse(**result)


__all__ = ["router"]
