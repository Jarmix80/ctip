#!/usr/bin/env python3
"""Ręczne pobranie i archiwizacja PDF umów GRENKE przez SMB.

Skrypt działa z tego środowiska (WSL/Linux) i:
1. loguje się do API produkcyjnego tylko odczytowo (pobranie payloadu formularza),
2. pobiera wskazane wiadomości z IMAP po Message-ID,
3. próbuje odszyfrować PDF umowy na podstawie kandydatów haseł z reprezentantów,
4. zapisuje pliki do udziału SMB,
5. opcjonalnie dopisuje metadane do PostgreSQL (jeśli podano działające dane DB).

Uwaga: skrypt nie wywołuje endpointów wysyłających SMS/e-mail.
"""

from __future__ import annotations

import argparse
import imaplib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes
from pathlib import PureWindowsPath
from typing import Any

import psycopg
import requests
import smbclient
from contracts_mailbox_sync import (
    decode_mime_text,
    extract_attachments,
    parse_email_date,
    try_extract_text_from_pdf,
)
from psycopg.types.json import Json

from app.services.contracts_mailbox import build_pdf_password_candidates


def _read_env(path: str) -> dict[str, str]:
    output: dict[str, str] = {}
    if not os.path.exists(path):
        return output
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            output[key.strip()] = value.strip().strip('"').strip("'")
    return output


def _pick(env_data: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value.strip()
    for key in keys:
        value = env_data.get(key)
        if value:
            return value.strip()
    return None


def _normalize_unc(path_value: str) -> tuple[str, str]:
    text = path_value.replace("/", "\\")
    if not text.startswith("\\\\"):
        text = "\\\\" + text.lstrip("\\")
    match = re.match(r"^\\\\([^\\]+)\\([^\\]+)(.*)$", text)
    if match is None:
        raise ValueError(f"Nieprawidlowy UNC: {path_value}")
    server = match.group(1)
    share = match.group(2)
    rest = match.group(3).strip("\\")
    base_unc = f"\\\\{server}\\{share}"
    if rest:
        base_unc += "\\" + rest
    return server, base_unc


def _sanitize_segment(value: str, fallback: str) -> str:
    text = (value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def _sanitize_file_name(value: str, fallback: str) -> str:
    name = PureWindowsPath(value or "").name
    if not name:
        name = fallback
    name = re.sub(r'[\x00-\x1F<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", "_", name).strip(" ._")
    return name or fallback


def _ensure_smb_dir(path_unc: str) -> None:
    smbclient.makedirs(path_unc, exist_ok=True)


def _smb_exists(path_unc: str) -> bool:
    try:
        smbclient.stat(path_unc)
        return True
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "0xc0000034" in text or "No such file" in text:
            return False
        raise


def _save_smb_file(
    base_unc: str, *, company: str, form_id: int, file_name: str, data: bytes
) -> str:
    company_seg = _sanitize_segment(company, f"firma_{form_id}")
    form_seg = _sanitize_segment(str(form_id), str(form_id))
    target_dir = base_unc.rstrip("\\") + f"\\{company_seg}\\{form_seg}"
    _ensure_smb_dir(target_dir)

    safe_name = _sanitize_file_name(file_name, f"umowa_form_{form_id}.pdf")
    candidate = target_dir + "\\" + safe_name
    if _smb_exists(candidate):
        stem, dot, suffix = safe_name.rpartition(".")
        if not stem:
            stem = safe_name
            suffix = ""
            dot = ""
        counter = 1
        while True:
            next_name = f"{stem}_{counter}{dot}{suffix}"
            next_candidate = target_dir + "\\" + next_name
            if not _smb_exists(next_candidate):
                candidate = next_candidate
                break
            counter += 1

    with smbclient.open_file(candidate, mode="wb") as handle:
        handle.write(data)
    return candidate


def _load_mapping(path: str, target_forms: set[int]) -> dict[int, list[dict[str, Any]]]:
    raw = json.loads(open(path, encoding="utf-8").read())
    by_form: dict[int, list[dict[str, Any]]] = {form_id: [] for form_id in target_forms}
    for item in raw:
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("message_id") or "").strip()
        if not message_id:
            continue
        matches = item.get("matches")
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, dict):
                continue
            try:
                form_id = int(match.get("form_id"))
            except (TypeError, ValueError):
                continue
            if form_id not in by_form:
                continue
            by_form[form_id].append(
                {
                    "message_id": message_id,
                    "subject": str(item.get("subject") or ""),
                }
            )
    return by_form


def _fetch_message_by_id(client: imaplib.IMAP4_SSL, message_id: str) -> tuple[bytes, str] | None:
    status, data = client.search(None, "HEADER", "Message-ID", f'"{message_id}"')
    if status != "OK" or not data or not data[0]:
        return None
    ids = data[0].split()
    if not ids:
        return None
    imap_id = ids[-1]
    fetch_status, full_data = client.fetch(imap_id, "(BODY.PEEK[])")
    if fetch_status != "OK":
        return None
    payload = b""
    for item in full_data:
        if isinstance(item, tuple):
            payload += item[1]
    if not payload:
        return None
    return payload, imap_id.decode(errors="ignore")


@dataclass(slots=True)
class FormContextLite:
    form_id: int
    company_name: str
    payload: dict[str, Any]
    workflow_case_id: int | None
    message_ids: list[str]


def _load_form_contexts_from_api(
    *,
    api_base: str,
    admin_email: str,
    admin_password: str,
    form_ids: list[int],
) -> list[FormContextLite]:
    login = requests.post(
        f"{api_base}/admin/auth/login",
        json={"email": admin_email, "password": admin_password},
        timeout=20,
    )
    login.raise_for_status()
    token = login.json().get("token")
    if not token:
        raise RuntimeError("Brak tokenu sesji admina.")

    headers = {"X-Admin-Session": token}
    contexts: list[FormContextLite] = []
    for form_id in form_ids:
        resp = requests.get(
            f"{api_base}/admin/contracts/forms/{form_id}/workflow",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        payload = body.get("form", {}).get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Formularz {form_id}: brak payloadu.")
        company_name = str(payload.get("company_name") or f"firma_{form_id}")
        workflow_case_id = body.get("workflow", {}).get("id")
        if workflow_case_id is not None:
            try:
                workflow_case_id = int(workflow_case_id)
            except (TypeError, ValueError):
                workflow_case_id = None
        contexts.append(
            FormContextLite(
                form_id=form_id,
                company_name=company_name,
                payload=payload,
                workflow_case_id=workflow_case_id,
                message_ids=[],
            )
        )
    return contexts


def _update_db_mailbox_meta(
    *,
    dsn: str,
    workflow_case_id: int,
    message_id: str,
    subject: str,
    sender: str,
    email_date_utc: datetime,
    archived_contract_file: dict[str, Any],
) -> None:
    """Dopisuje metadane archiwum do client_payload_snapshot._mailbox_meta."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT client_payload_snapshot
                FROM ctip.form_workflow_case
                WHERE id = %s
                """,
                (workflow_case_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"Brak workflow_case_id={workflow_case_id} w bazie.")
            snapshot = row[0] if isinstance(row[0], dict) else {}
            meta_raw = snapshot.get("_mailbox_meta")
            meta = dict(meta_raw) if isinstance(meta_raw, dict) else {}

            meta["last_message_id"] = message_id
            meta["last_message_subject"] = subject
            meta["last_message_sender"] = sender
            meta["last_message_date_utc"] = email_date_utc.astimezone(UTC).isoformat()
            meta["last_archived_contract_file"] = archived_contract_file

            history_raw = meta.get("archived_contract_files")
            history = (
                [item for item in history_raw if isinstance(item, dict)]
                if isinstance(history_raw, list)
                else []
            )
            history.append(archived_contract_file)
            meta["archived_contract_files"] = history[-50:]

            processed_raw = meta.get("processed_message_ids")
            processed = (
                [str(item) for item in processed_raw if str(item).strip()]
                if isinstance(processed_raw, list)
                else []
            )
            if message_id not in processed:
                processed.append(message_id)
            meta["processed_message_ids"] = processed[-50:]

            snapshot["_mailbox_meta"] = meta

            cur.execute(
                """
                UPDATE ctip.form_workflow_case
                SET client_payload_snapshot = %s, updated_at = now()
                WHERE id = %s
                """,
                (Json(snapshot), workflow_case_id),
            )
        conn.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ręczna archiwizacja PDF umów przez SMB.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--api-base", default="http://192.168.0.8:8000")
    parser.add_argument("--admin-email", default="marcin@ksero-partner.com.pl")
    parser.add_argument("--mapping-file", default="inbox/wynik_pdf_match_25_26.json")
    parser.add_argument("--forms", default="25,26", help="Lista form_id oddzielona przecinkami.")
    parser.add_argument("--imap-folder", default="INBOX")
    parser.add_argument(
        "--skip-db-update",
        action="store_true",
        help="Pomiń zapis metadanych do PostgreSQL.",
    )
    parser.add_argument(
        "--db-dsn",
        default="",
        help="Opcjonalny DSN PostgreSQL (wymagany gdy --skip-db-update nie ustawiono).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env_data = _read_env(args.env_file)

    admin_password = _pick(env_data, "pass_admin_web", "PASS_ADMIN_WEB")
    if not admin_password:
        raise SystemExit("Brak hasla admina (pass_admin_web/PASS_ADMIN_WEB).")

    mailbox_user = _pick(env_data, "MAILBOX_EMAIL_ADDRESS")
    mailbox_pass = _pick(env_data, "MAILBOX_EMAIL_PASSWORD")
    mailbox_host = _pick(env_data, "MAILBOX_IMAP_HOST")
    mailbox_port = int(_pick(env_data, "MAILBOX_IMAP_PORT") or "993")
    if not mailbox_user or not mailbox_pass or not mailbox_host:
        raise SystemExit("Brak konfiguracji MAILBOX_*.")

    # Dla UNC preferujemy wartosci surowe z pliku .env (bash source potrafi znieksztalcic backslashe).
    share_path = (
        env_data.get("sciezka_dok_umow")
        or env_data.get("SCIEZKA_DOK_UMOW")
        or _pick(env_data, "SMB_SHARE_PATH")
    )
    share_user = (
        env_data.get("login_dok_umow")
        or env_data.get("LOGIN_DOK_UMOW")
        or _pick(env_data, "SMB_USERNAME")
    )
    share_pass = (
        env_data.get("pass_dok_umow")
        or env_data.get("PASS_DOK_UMOW")
        or _pick(env_data, "SMB_PASSWORD")
    )
    if not share_path or not share_user or not share_pass:
        raise SystemExit("Brak konfiguracji SMB (sciezka_dok_umow/login_dok_umow/pass_dok_umow).")

    server, base_unc = _normalize_unc(share_path)
    smbclient.register_session(server=server, username=share_user, password=share_pass, port=445)

    form_ids = [int(item.strip()) for item in args.forms.split(",") if item.strip()]
    contexts = _load_form_contexts_from_api(
        api_base=args.api_base,
        admin_email=args.admin_email,
        admin_password=admin_password,
        form_ids=form_ids,
    )
    by_form = {ctx.form_id: ctx for ctx in contexts}

    mapped = _load_mapping(args.mapping_file, set(form_ids))
    for form_id, items in mapped.items():
        if form_id in by_form:
            by_form[form_id].message_ids = [
                str(item.get("message_id") or "").strip()
                for item in items
                if str(item.get("message_id") or "").strip()
            ]

    reports: list[dict[str, Any]] = []
    with imaplib.IMAP4_SSL(mailbox_host, mailbox_port, timeout=20) as client:
        client.login(mailbox_user, mailbox_pass)
        status, _ = client.select(args.imap_folder, readonly=True)
        if status != "OK":
            raise SystemExit(f"Nie mozna otworzyc folderu IMAP: {args.imap_folder}")

        for form_id in form_ids:
            ctx = by_form.get(form_id)
            if ctx is None:
                reports.append(
                    {"form_id": form_id, "ok": False, "error": "Brak kontekstu formularza z API."}
                )
                continue
            candidates = build_pdf_password_candidates(ctx.payload)
            if not ctx.message_ids:
                reports.append(
                    {"form_id": form_id, "ok": False, "error": "Brak message_id w mapowaniu."}
                )
                continue

            saved_any = False
            for message_id in ctx.message_ids:
                fetched = _fetch_message_by_id(client, message_id)
                if fetched is None:
                    reports.append(
                        {
                            "form_id": form_id,
                            "message_id": message_id,
                            "ok": False,
                            "error": "Nie znaleziono wiadomosci po Message-ID.",
                        }
                    )
                    continue
                raw_email, _imap_id = fetched
                msg = message_from_bytes(raw_email)
                subject = decode_mime_text(msg.get("Subject"))
                sender = decode_mime_text(msg.get("From"))
                email_date_utc = parse_email_date(msg.get("Date"))
                attachments = extract_attachments(msg)

                for att_name, content_type, blob in attachments:
                    if not (
                        content_type == "application/pdf" or str(att_name).lower().endswith(".pdf")
                    ):
                        continue
                    password_used = None
                    output_bytes = blob
                    kind = "encrypted_contract_pdf"
                    description = "Umowa GRENKE (zaszyfrowany PDF z e-maila)."

                    result = try_extract_text_from_pdf(
                        blob, password_candidates=candidates or [None]
                    )
                    if result.success and result.decrypted_pdf_bytes:
                        output_bytes = result.decrypted_pdf_bytes
                        password_used = result.password_used
                        kind = "decrypted_contract_pdf"
                        description = "Umowa GRENKE (odszyfrowany PDF z e-maila)."

                    stamped_name = f"{email_date_utc.strftime('%Y%m%d_%H%M%S')}_{_sanitize_file_name(att_name, f'umowa_form_{form_id}.pdf')}"
                    saved_path = _save_smb_file(
                        base_unc,
                        company=ctx.company_name,
                        form_id=form_id,
                        file_name=stamped_name,
                        data=output_bytes,
                    )

                    archived_contract_file = {
                        "kind": kind,
                        "description": description,
                        "path": saved_path.replace("\\", "/"),
                        "file_name": PureWindowsPath(saved_path).name,
                        "original_name": att_name,
                        "form_request_id": form_id,
                        "company_name": ctx.company_name,
                        "message_id": message_id,
                        "saved_at_utc": datetime.now(UTC).isoformat(),
                        "password_used": password_used,
                    }

                    db_updated = False
                    db_error = None
                    if not args.skip_db_update:
                        if not ctx.workflow_case_id:
                            db_error = (
                                "Brak workflow_case_id - nie mozna zapisac metadanych do bazy."
                            )
                        elif not args.db_dsn:
                            db_error = "Brak --db-dsn dla zapisu do bazy."
                        else:
                            try:
                                _update_db_mailbox_meta(
                                    dsn=args.db_dsn,
                                    workflow_case_id=ctx.workflow_case_id,
                                    message_id=message_id,
                                    subject=subject,
                                    sender=sender,
                                    email_date_utc=email_date_utc,
                                    archived_contract_file=archived_contract_file,
                                )
                                db_updated = True
                            except Exception as exc:  # noqa: BLE001
                                db_error = f"{type(exc).__name__}: {exc}"

                    reports.append(
                        {
                            "form_id": form_id,
                            "workflow_case_id": ctx.workflow_case_id,
                            "message_id": message_id,
                            "subject": subject,
                            "attachment_name": att_name,
                            "saved_path": saved_path,
                            "kind": kind,
                            "password_used": bool(password_used),
                            "db_updated": db_updated,
                            "db_error": db_error,
                            "ok": True,
                        }
                    )
                    saved_any = True
            if not saved_any:
                reports.append(
                    {
                        "form_id": form_id,
                        "ok": False,
                        "error": "Nie zapisano zadnego PDF dla formularza.",
                    }
                )

    smbclient.reset_connection_cache()
    print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
