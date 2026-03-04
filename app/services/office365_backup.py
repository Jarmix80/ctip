"""Integracja testowa Office 365 (Microsoft Graph) dla modułu backupu."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx


class Office365BackupError(RuntimeError):
    """Błąd integracji z Microsoft Graph."""


@dataclass(slots=True)
class Office365ConnectionResult:
    """Wynik testu połączenia z SharePoint/Drive."""

    ok: bool
    message: str
    site_id: str | None = None
    drive_id: str | None = None
    folder_path: str | None = None


@dataclass(slots=True)
class Office365UploadResult:
    """Wynik uploadu pliku do SharePoint/Drive."""

    drive_id: str
    item_id: str | None
    web_url: str | None
    name: str
    size: int | None


def _sanitize_folder_path(path: str | None) -> str | None:
    if path is None:
        return None
    normalized = path.strip().strip("/")
    return normalized or None


def _sanitize_credential(value: str | None) -> str:
    """Usuwa nadmiarowe spacje i opcjonalne cudzyslowy z wartosci sekretow/id."""
    if value is None:
        return ""
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


async def _fetch_token(
    client: httpx.AsyncClient,
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
) -> str:
    tenant_id = _sanitize_credential(tenant_id)
    client_id = _sanitize_credential(client_id)
    client_secret = _sanitize_credential(client_secret)
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    response = await client.post(token_url, data=payload)
    if response.status_code >= 400:
        raise Office365BackupError(
            f"Błąd autoryzacji OAuth ({response.status_code}): {response.text[:240]}"
        )
    data = response.json()
    token = data.get("access_token")
    if not token:
        raise Office365BackupError("Brak access_token w odpowiedzi OAuth.")
    return str(token)


async def _resolve_drive_id(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    site_id: str,
) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = await client.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive", headers=headers
    )
    if response.status_code >= 400:
        raise Office365BackupError(
            f"Nie udało się pobrać Drive dla Site ID ({response.status_code}): {response.text[:240]}"
        )
    data = response.json()
    drive_id = data.get("id")
    if not drive_id:
        raise Office365BackupError("Microsoft Graph nie zwrócił Drive ID dla wskazanego Site ID.")
    return str(drive_id)


async def test_office365_connection(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    site_id: str | None,
    drive_id: str | None,
    folder_path: str | None,
) -> Office365ConnectionResult:
    """Wykonuje test połączenia z Graph i weryfikuje dostęp do dysku/folderu."""
    site_id_clean = (site_id or "").strip() or None
    drive_id_clean = (drive_id or "").strip() or None
    folder_clean = _sanitize_folder_path(folder_path)

    if (
        not _sanitize_credential(tenant_id)
        or not _sanitize_credential(client_id)
        or not _sanitize_credential(client_secret)
    ):
        raise Office365BackupError("Brakuje wymaganych danych Office 365 (tenant/client/secret).")
    if not site_id_clean and not drive_id_clean:
        raise Office365BackupError("Podaj Office Site ID lub Office Drive ID.")

    timeout = httpx.Timeout(20.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token = await _fetch_token(
            client,
            tenant_id=tenant_id.strip(),
            client_id=client_id.strip(),
            client_secret=client_secret.strip(),
        )
        resolved_drive_id = drive_id_clean
        if not resolved_drive_id and site_id_clean:
            resolved_drive_id = await _resolve_drive_id(
                client,
                access_token=token,
                site_id=site_id_clean,
            )
        if not resolved_drive_id:
            raise Office365BackupError("Nie udało się ustalić Drive ID.")

        headers = {"Authorization": f"Bearer {token}"}
        if folder_clean:
            url = (
                f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}/root:/{folder_clean}"
            )
        else:
            url = f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}/root"
        response = await client.get(url, headers=headers)
        if folder_clean and response.status_code == 404:
            return Office365ConnectionResult(
                ok=True,
                message="Połączenie działa, ale folder docelowy nie istnieje (utwórz go lub zmień ścieżkę).",
                site_id=site_id_clean,
                drive_id=resolved_drive_id,
                folder_path=folder_clean,
            )
        if response.status_code >= 400:
            raise Office365BackupError(
                f"Błąd odczytu zasobu docelowego ({response.status_code}): {response.text[:240]}"
            )

    return Office365ConnectionResult(
        ok=True,
        message="Połączenie z Office 365 (SharePoint) działa poprawnie.",
        site_id=site_id_clean,
        drive_id=resolved_drive_id,
        folder_path=folder_clean,
    )


async def upload_file_to_sharepoint(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    site_id: str | None,
    drive_id: str | None,
    folder_path: str | None,
    file_path: Path,
) -> Office365UploadResult:
    """Wysyła plik do wskazanego folderu SharePoint przez Microsoft Graph."""
    if not file_path.exists() or not file_path.is_file():
        raise Office365BackupError(f"Plik do wysłania nie istnieje: {file_path}")

    site_id_clean = (site_id or "").strip() or None
    drive_id_clean = (drive_id or "").strip() or None
    folder_clean = _sanitize_folder_path(folder_path)

    if (
        not _sanitize_credential(tenant_id)
        or not _sanitize_credential(client_id)
        or not _sanitize_credential(client_secret)
    ):
        raise Office365BackupError("Brakuje wymaganych danych Office 365 (tenant/client/secret).")
    if not site_id_clean and not drive_id_clean:
        raise Office365BackupError("Podaj Office Site ID lub Office Drive ID.")

    timeout = httpx.Timeout(90.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token = await _fetch_token(
            client,
            tenant_id=tenant_id.strip(),
            client_id=client_id.strip(),
            client_secret=client_secret.strip(),
        )
        resolved_drive_id = drive_id_clean
        if not resolved_drive_id and site_id_clean:
            resolved_drive_id = await _resolve_drive_id(
                client,
                access_token=token,
                site_id=site_id_clean,
            )
        if not resolved_drive_id:
            raise Office365BackupError("Nie udało się ustalić Drive ID.")

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
        target_name = file_path.name
        if folder_clean:
            url = f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}/root:/{folder_clean}/{target_name}:/content"
        else:
            url = f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}/root:/{target_name}:/content"

        response = await client.put(url, headers=headers, content=file_path.read_bytes())
        if response.status_code >= 400:
            raise Office365BackupError(
                f"Upload do SharePoint nie powiódł się ({response.status_code}): {response.text[:240]}"
            )
        data = response.json()
        return Office365UploadResult(
            drive_id=resolved_drive_id,
            item_id=data.get("id"),
            web_url=data.get("webUrl"),
            name=data.get("name") or target_name,
            size=data.get("size"),
        )


__all__ = [
    "Office365BackupError",
    "Office365ConnectionResult",
    "Office365UploadResult",
    "test_office365_connection",
    "upload_file_to_sharepoint",
]
