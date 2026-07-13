"""Integracja Office 365 (Microsoft Graph) dla modułu kopii zapasowych."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from app.services.backup_retention import (
    RetentionApplyResult,
    RetentionItem,
    RetentionPlan,
    build_retention_plan,
)

SIMPLE_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024
UPLOAD_CHUNK_SIZE_BYTES = 10 * 1024 * 1024
_BACKUP_ARCHIVE_PATTERN = re.compile(r"^backup_.+\.tar(?:\.gz)?$")


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


@dataclass(slots=True)
class Office365PruneResult:
    """Wynik zastosowania retencji w folderze SharePoint."""

    deleted_archives: int
    deleted_files: int


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


def _drive_item_path(folder_path: str | None, file_name: str | None = None) -> str:
    """Buduje bezpiecznie zakodowaną ścieżkę elementu Microsoft Graph."""
    parts = [part for part in (folder_path, file_name) if part]
    return quote("/".join(parts), safe="/")


def _upload_result(
    *,
    payload: dict[str, object],
    drive_id: str,
    expected_name: str,
    expected_size: int,
) -> Office365UploadResult:
    """Waliduje metadane pliku zwrócone po zakończonym uploadzie."""
    raw_size = payload.get("size")
    try:
        uploaded_size = int(raw_size) if raw_size is not None else None
    except (TypeError, ValueError):
        uploaded_size = None
    if uploaded_size != expected_size:
        raise Office365BackupError(
            "Rozmiar pliku w SharePoint nie zgadza się z lokalnym archiwum "
            f"({uploaded_size!r} != {expected_size})."
        )
    return Office365UploadResult(
        drive_id=drive_id,
        item_id=str(payload["id"]) if payload.get("id") else None,
        web_url=str(payload["webUrl"]) if payload.get("webUrl") else None,
        name=str(payload.get("name") or expected_name),
        size=uploaded_size,
    )


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
            encoded_folder = _drive_item_path(folder_clean)
            url = (
                f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
                f"/root:/{encoded_folder}"
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
    """Wysyła plik do SharePoint, używając sesji fragmentowej dla dużych plików."""
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

    timeout = httpx.Timeout(180.0, connect=10.0)
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

        auth_headers = {"Authorization": f"Bearer {token}"}
        target_name = file_path.name
        target_path = _drive_item_path(folder_clean, target_name)
        file_size = file_path.stat().st_size
        if file_size <= SIMPLE_UPLOAD_LIMIT_BYTES:
            headers = {**auth_headers, "Content-Type": "application/octet-stream"}
            url = (
                f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
                f"/root:/{target_path}:/content"
            )
            response = await client.put(url, headers=headers, content=file_path.read_bytes())
            if response.status_code >= 400:
                raise Office365BackupError(
                    "Upload do SharePoint nie powiódł się "
                    f"({response.status_code}): {response.text[:240]}"
                )
            return _upload_result(
                payload=response.json(),
                drive_id=resolved_drive_id,
                expected_name=target_name,
                expected_size=file_size,
            )

        session_url = (
            f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
            f"/root:/{target_path}:/createUploadSession"
        )
        session_response = await client.post(
            session_url,
            headers={**auth_headers, "Content-Type": "application/json"},
            json={
                "item": {
                    "@microsoft.graph.conflictBehavior": "replace",
                    "name": target_name,
                }
            },
        )
        if session_response.status_code >= 400:
            raise Office365BackupError(
                "Nie udało się utworzyć sesji uploadu SharePoint "
                f"({session_response.status_code}): {session_response.text[:240]}"
            )
        upload_url = str(session_response.json().get("uploadUrl") or "")
        if not upload_url:
            raise Office365BackupError("Microsoft Graph nie zwrócił adresu sesji uploadu.")

        final_payload: dict[str, object] | None = None
        with file_path.open("rb") as stream:
            offset = 0
            while offset < file_size:
                chunk = stream.read(UPLOAD_CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                end_offset = offset + len(chunk) - 1
                chunk_response = await client.put(
                    upload_url,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end_offset}/{file_size}",
                    },
                    content=chunk,
                )
                if chunk_response.status_code not in {200, 201, 202}:
                    raise Office365BackupError(
                        "Fragmentowy upload do SharePoint nie powiódł się "
                        f"({chunk_response.status_code}): {chunk_response.text[:240]}"
                    )
                if chunk_response.status_code in {200, 201}:
                    final_payload = chunk_response.json()
                offset = end_offset + 1

        if final_payload is None or offset != file_size:
            raise Office365BackupError("Upload SharePoint nie został poprawnie zakończony.")
        return _upload_result(
            payload=final_payload,
            drive_id=resolved_drive_id,
            expected_name=target_name,
            expected_size=file_size,
        )


async def prune_sharepoint_backups(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    site_id: str | None,
    drive_id: str | None,
    folder_path: str | None,
    retention_count: int,
) -> Office365PruneResult:
    """Usuwa archiwa i ich sumy kontrolne przekraczające retencję SharePoint."""
    if retention_count < 1:
        return Office365PruneResult(deleted_archives=0, deleted_files=0)

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

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token = await _fetch_token(
            client,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
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
            encoded_folder = _drive_item_path(folder_clean)
            next_url: str | None = (
                f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
                f"/root:/{encoded_folder}:/children?$select=id,name,lastModifiedDateTime&$top=200"
            )
        else:
            next_url = (
                f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
                "/root/children?$select=id,name,lastModifiedDateTime&$top=200"
            )

        items: list[dict[str, object]] = []
        while next_url:
            response = await client.get(next_url, headers=headers)
            if response.status_code >= 400:
                raise Office365BackupError(
                    "Nie udało się odczytać retencji SharePoint "
                    f"({response.status_code}): {response.text[:240]}"
                )
            payload = response.json()
            items.extend(item for item in payload.get("value", []) if isinstance(item, dict))
            next_url = str(payload.get("@odata.nextLink") or "") or None

        archives = [
            item for item in items if _BACKUP_ARCHIVE_PATTERN.match(str(item.get("name") or ""))
        ]
        archives.sort(
            key=lambda item: str(item.get("lastModifiedDateTime") or ""),
            reverse=True,
        )
        items_by_name = {str(item.get("name") or ""): item for item in items}
        deleted_archives = 0
        deleted_files = 0
        for archive in archives[retention_count:]:
            archive_name = str(archive.get("name") or "")
            delete_items = [archive, items_by_name.get(f"{archive_name}.sha256")]
            for item in delete_items:
                if not item or not item.get("id"):
                    continue
                delete_response = await client.delete(
                    f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
                    f"/items/{quote(str(item['id']), safe='')}",
                    headers=headers,
                )
                if delete_response.status_code not in {204, 404}:
                    raise Office365BackupError(
                        "Nie udało się zastosować retencji SharePoint "
                        f"({delete_response.status_code}): {delete_response.text[:240]}"
                    )
                if delete_response.status_code == 204:
                    deleted_files += 1
            deleted_archives += 1

    return Office365PruneResult(
        deleted_archives=deleted_archives,
        deleted_files=deleted_files,
    )


def _parse_graph_datetime(value: object) -> datetime:
    """Konwertuje datę Microsoft Graph na świadomy czas UTC."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return datetime.fromtimestamp(0, tz=UTC)
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def run_sharepoint_retention(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    site_id: str | None,
    drive_id: str | None,
    folder_path: str | None,
    retention_days: int,
    dry_run: bool,
    preserve_newest_complete: bool = True,
    now: datetime | None = None,
) -> tuple[RetentionPlan, RetentionApplyResult]:
    """Planuje i opcjonalnie wykonuje czasową retencję w folderze SharePoint."""
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

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token = await _fetch_token(
            client,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
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
            encoded_folder = _drive_item_path(folder_clean)
            next_url: str | None = (
                f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
                f"/root:/{encoded_folder}:/children"
                "?$select=id,name,size,lastModifiedDateTime,file,folder&$top=200"
            )
        else:
            next_url = (
                f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
                "/root/children?$select=id,name,size,lastModifiedDateTime,file,folder&$top=200"
            )

        graph_items: list[dict[str, object]] = []
        while next_url:
            response = await client.get(next_url, headers=headers)
            if response.status_code >= 400:
                raise Office365BackupError(
                    "Nie udało się odczytać retencji SharePoint "
                    f"({response.status_code}): {response.text[:240]}"
                )
            payload = response.json()
            graph_items.extend(
                item
                for item in payload.get("value", [])
                if isinstance(item, dict) and not item.get("folder")
            )
            next_url = str(payload.get("@odata.nextLink") or "") or None

        retention_items = [
            RetentionItem(
                name=str(item.get("name") or ""),
                modified_at=_parse_graph_datetime(item.get("lastModifiedDateTime")),
                size_bytes=int(item.get("size") or 0),
                identifier=str(item.get("id") or "") or None,
            )
            for item in graph_items
            if item.get("name")
        ]
        plan = build_retention_plan(
            retention_items,
            retention_days=retention_days,
            now=now,
            preserve_newest_complete=preserve_newest_complete,
        )
        result = RetentionApplyResult(dry_run=dry_run)
        if dry_run:
            return plan, result

        for item_set in plan.deletion_sets:
            set_failed = False
            set_deleted_files = 0
            set_deleted_bytes = 0
            for managed in item_set.items:
                item_id = managed.item.identifier
                if not item_id:
                    result.errors.append(f"Brak identyfikatora pliku: {managed.item.name}")
                    set_failed = True
                    continue
                delete_response = await client.delete(
                    f"https://graph.microsoft.com/v1.0/drives/{resolved_drive_id}"
                    f"/items/{quote(item_id, safe='')}",
                    headers=headers,
                )
                if delete_response.status_code not in {204, 404}:
                    result.errors.append(
                        f"{managed.item.name}: HTTP {delete_response.status_code} "
                        f"{delete_response.text[:160]}"
                    )
                    set_failed = True
                    continue
                if delete_response.status_code == 204:
                    set_deleted_files += 1
                    set_deleted_bytes += max(managed.item.size_bytes, 0)
            result.deleted_files += set_deleted_files
            result.deleted_bytes += set_deleted_bytes
            if not set_failed:
                result.deleted_sets += 1
        return plan, result


__all__ = [
    "Office365BackupError",
    "Office365ConnectionResult",
    "Office365PruneResult",
    "Office365UploadResult",
    "prune_sharepoint_backups",
    "run_sharepoint_retention",
    "test_office365_connection",
    "upload_file_to_sharepoint",
]
