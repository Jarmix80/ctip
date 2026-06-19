#!/usr/bin/env python3
"""Synchronizacja wiadomości umów z formularzami FLOW."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import imaplib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import FormRequest, FormWorkflowCase
from app.services import form_generator
from app.services.audit import record_audit
from app.services.contracts_mailbox import (
    MAILBOX_EVENT_APPROVAL,
    MAILBOX_EVENT_DECISION,
    build_pdf_password_candidates,
    classify_mail_payload,
    detect_rejection_decision,
    extract_application_number,
    extract_data_from_contract_text,
    extract_proforma_number,
    normalize_application_number,
    normalize_proforma_number,
    score_form_match,
)
from app.services.contracts_workflow import (
    WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
    WORKFLOW_BUSINESS_STATUS_CLOSED_NOT_REALIZED,
    WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE,
    WORKFLOW_BUSINESS_STATUS_RENTAL_WITHOUT_GRENKE,
    WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE,
    get_or_create_form_workflow_case,
    list_form_workflow_devices,
    normalize_workflow_business_status,
    set_form_workflow_business_status,
    set_form_workflow_delivery,
)
from app.services.delivery import ensure_delivery_case_for_workflow
from app.services.workflow_machine_binding import (
    apply_binding_snapshot,
    bind_devices_to_workflow_client,
    notify_binding_issues_to_admins,
)

STATE_PATH = Path("inbox/mailbox/contracts_mailbox_state.json")
ATTACHMENTS_BASE_PATH = Path("inbox/mailbox/contracts")
DEFAULT_IMAP_FOLDER = "INBOX"
UNRESOLVED_REASON_UNSUPPORTED_SUBJECT = "unsupported_subject"
UNRESOLVED_REASON_UNMATCHED_FORM = "unmatched_form"
UNRESOLVED_REASON_AMBIGUOUS_MATCH = "ambiguous_match"
MAX_WARNING_LOG_ITEMS = 40
MAX_WARNING_LOG_CHARS = 600
MAX_EXTRACT_LOG_CHARS = 1200
MAILBOX_GENERIC_DECISION_PROTECTED_STATUSES = {
    WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
    WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE,
    WORKFLOW_BUSINESS_STATUS_RENTAL_WITHOUT_GRENKE,
    WORKFLOW_BUSINESS_STATUS_CLOSED_NOT_REALIZED,
}


@dataclass(slots=True)
class FormContext:
    """Kontekst formularza używany do dopasowania wiadomości."""

    form: FormRequest
    payload: dict[str, Any]
    workflow_case: FormWorkflowCase | None
    application_no_normalized: str | None
    proforma_no_normalized: str | None = None


@dataclass(slots=True)
class MailContext:
    """Sparowane dane pojedynczej wiadomości IMAP."""

    imap_id: str
    message_id: str
    subject: str
    sender: str
    body_text: str
    email_date_utc: datetime
    event_type: str
    application_no_raw: str | None
    application_no_normalized: str | None
    attachments: list[tuple[str, str, bytes]]
    proforma_no_raw: str | None = None
    proforma_no_normalized: str | None = None


@dataclass(slots=True)
class PdfExtractionResult:
    """Wynik próby odszyfrowania i ekstrakcji tekstu PDF."""

    success: bool
    password_used: str | None
    text: str
    method: str
    error: str | None
    decrypted_pdf_bytes: bytes | None = None


@dataclass(slots=True)
class MatchDecision:
    """Wynik dopasowania wiadomości do formularza."""

    context: FormContext | None
    reason: str
    score: int | None = None


def parse_args() -> argparse.Namespace:
    """Parsuje argumenty linii poleceń."""
    parser = argparse.ArgumentParser(
        description=(
            "Synchronizuje wiadomości umów ze skrzynki IMAP z modułem FLOW: "
            "zmienia status sprawy, przypina numer wniosku i wyciąga dane z PDF."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Maksymalna liczba najnowszych wiadomości do analizy (domyślnie: 30).",
    )
    parser.add_argument(
        "--folder",
        default=DEFAULT_IMAP_FOLDER,
        help=f"Nazwa folderu IMAP (domyślnie: {DEFAULT_IMAP_FOLDER}).",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Przetwarzaj ponownie wiadomości już zapisane w stanie lokalnym.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tryb podglądu: nie zapisuje zmian w bazie ani stanie lokalnym.",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help=(
            "Zakoncz skrypt kodem != 0, jezeli wykryto ostrzezenia "
            "(domyslnie: ostrzezenia nie podnosza kodu bledu)."
        ),
    )
    return parser.parse_args()


def _truncate_for_log(value: str, *, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def decode_mime_text(value: str | None) -> str:
    """Dekoduje nagłówki MIME (np. temat, nazwa załącznika)."""
    parts = []
    for chunk, encoding in decode_header(value or ""):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts).strip()


def html_to_text(value: str) -> str:
    """Usuwa znaczniki HTML i zwraca uproszczony tekst."""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_message_body_text(msg: Message) -> str:
    """Wyciąga treść tekstową z wiadomości (plain/html)."""
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = (part.get_content_type() or "").lower()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            if content_type not in {"text/plain", "text/html"}:
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            raw_text = payload.decode(charset, errors="replace")
            parts.append(raw_text if content_type == "text/plain" else html_to_text(raw_text))
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        content_type = (msg.get_content_type() or "").lower()
        raw_text = payload.decode(charset, errors="replace")
        parts.append(raw_text if content_type == "text/plain" else html_to_text(raw_text))
    merged = " ".join(part for part in parts if part)
    return re.sub(r"\s+", " ", merged).strip()


def extract_attachments(msg: Message) -> list[tuple[str, str, bytes]]:
    """Zwraca listę załączników (nazwa, content_type, bytes)."""
    output: list[tuple[str, str, bytes]] = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        decoded_name = decode_mime_text(filename)
        content_type = (part.get_content_type() or "").lower()
        payload = part.get_payload(decode=True) or b""
        if payload:
            output.append((decoded_name, content_type, payload))
    return output


def parse_email_date(value: str | None) -> datetime:
    """Konwertuje datę z nagłówka wiadomości do UTC."""
    if not value:
        return datetime.now(UTC)
    parsed = parsedate_to_datetime(value)
    if parsed is None:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sanitize_message_id_segment(message_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(message_id or ""))
    value = value.strip("._-")
    return value or "unknown_message"


def _sanitize_attachment_name(file_name: str, fallback_index: int) -> str:
    base_name = Path(str(file_name or "")).name
    if not base_name:
        base_name = f"attachment_{fallback_index}"
    cleaned = re.sub(r"[\x00-\x1F\x7F]+", "_", base_name)
    cleaned = cleaned.replace("/", "_").replace("\\", "_")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned or f"attachment_{fallback_index}"


def _sanitize_fs_segment(value: str | None, *, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def _resolve_contract_archive_root() -> Path | None:
    configured = str(settings.contracts_mailbox_archive_root or "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path(r"D:\archiwum_dok")
    return None


def _render_reader_pdf_bytes(reader: PdfReader) -> bytes | None:
    try:
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return None


def persist_decrypted_contract_pdf(
    *,
    form_ctx: FormContext,
    mail_ctx: MailContext,
    original_file_name: str,
    pdf_bytes: bytes,
) -> dict[str, Any] | None:
    """Zapisuje odszyfrowaną umowę PDF do archiwum plików i zwraca metadane."""
    return _persist_contract_pdf(
        form_ctx=form_ctx,
        mail_ctx=mail_ctx,
        original_file_name=original_file_name,
        pdf_bytes=pdf_bytes,
        kind="decrypted_contract_pdf",
        description="Umowa GRENKE (odszyfrowany PDF z e-maila).",
    )


def persist_encrypted_contract_pdf(
    *,
    form_ctx: FormContext,
    mail_ctx: MailContext,
    original_file_name: str,
    pdf_bytes: bytes,
) -> dict[str, Any] | None:
    """Zapisuje zaszyfrowaną umowę PDF do archiwum jako fallback."""
    return _persist_contract_pdf(
        form_ctx=form_ctx,
        mail_ctx=mail_ctx,
        original_file_name=original_file_name,
        pdf_bytes=pdf_bytes,
        kind="encrypted_contract_pdf",
        description="Umowa GRENKE (zaszyfrowany PDF z e-maila).",
    )


def _persist_contract_pdf(
    *,
    form_ctx: FormContext,
    mail_ctx: MailContext,
    original_file_name: str,
    pdf_bytes: bytes,
    kind: str,
    description: str,
) -> dict[str, Any] | None:
    """Zapisuje plik umowy PDF do archiwum i zwraca metadane."""
    archive_root = _resolve_contract_archive_root()
    if archive_root is None:
        return None

    payload_company = str(form_ctx.payload.get("company_name") or "").strip()
    company_name = payload_company or str(getattr(form_ctx.form, "customer_name", "") or "").strip()
    company_segment = _sanitize_fs_segment(company_name, fallback=f"firma_{form_ctx.form.id}")
    form_segment = _sanitize_fs_segment(str(form_ctx.form.id), fallback="brak_formularza")
    target_dir = archive_root / company_segment / form_segment
    target_dir.mkdir(parents=True, exist_ok=True)

    base_name = _sanitize_attachment_name(
        original_file_name or f"umowa_form_{form_ctx.form.id}.pdf",
        fallback_index=1,
    )
    if not base_name.lower().endswith(".pdf"):
        base_name = f"{base_name}.pdf"
    stamped_name = f"{mail_ctx.email_date_utc.strftime('%Y%m%d_%H%M%S')}_{base_name}"
    candidate = target_dir / stamped_name
    counter = 1
    while candidate.exists():
        candidate = target_dir / f"{candidate.stem}_{counter}{candidate.suffix}"
        counter += 1
    candidate.write_bytes(pdf_bytes)

    return {
        "kind": kind,
        "description": description,
        "path": candidate.as_posix(),
        "file_name": candidate.name,
        "original_name": original_file_name,
        "form_request_id": form_ctx.form.id,
        "company_name": company_name or None,
        "message_id": mail_ctx.message_id,
        "saved_at_utc": datetime.now(UTC).isoformat(),
    }


def persist_mail_attachments(*, scope: str, mail_ctx: MailContext) -> list[dict[str, Any]]:
    """Zapisuje załączniki wiadomości do lokalnej struktury katalogów."""
    if not mail_ctx.attachments:
        return []

    target_root = (
        ATTACHMENTS_BASE_PATH
        / scope
        / mail_ctx.email_date_utc.strftime("%Y-%m-%d")
        / _sanitize_message_id_segment(mail_ctx.message_id)
    )
    target_root.mkdir(parents=True, exist_ok=True)

    saved: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for index, (original_name, content_type, data) in enumerate(mail_ctx.attachments, start=1):
        safe_name = _sanitize_attachment_name(original_name, index)
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        candidate_name = safe_name
        counter = 1
        while candidate_name in used_names:
            candidate_name = f"{stem}_{counter}{suffix}"
            counter += 1
        used_names.add(candidate_name)

        file_path = target_root / candidate_name
        file_path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        saved.append(
            {
                "original_name": original_name,
                "saved_name": candidate_name,
                "content_type": content_type,
                "size_bytes": len(data),
                "sha256": digest,
                "path": file_path.as_posix(),
            }
        )
    return saved


def _load_state(path: Path) -> tuple[set[str], dict[str, dict[str, Any]]]:
    """Wczytuje stan synchronizacji (przetworzone i nierozwiązane wiadomości)."""
    if not path.exists():
        return set(), {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set(), {}
    if not isinstance(payload, dict):
        return set(), {}

    values = payload.get("processed")
    processed = (
        {str(item) for item in values if str(item).strip()} if isinstance(values, list) else set()
    )

    unresolved_by_id: dict[str, dict[str, Any]] = {}
    unresolved = payload.get("unresolved")
    if isinstance(unresolved, list):
        for item in unresolved:
            if not isinstance(item, dict):
                continue
            message_id = str(item.get("message_id") or "").strip()
            if not message_id:
                continue
            unresolved_by_id[message_id] = item
    return processed, unresolved_by_id


def _save_state(
    path: Path,
    processed_ids: set[str],
    unresolved_by_id: dict[str, dict[str, Any]],
) -> None:
    """Zapisuje stan synchronizacji wraz z kolejką wyjątków."""
    path.parent.mkdir(parents=True, exist_ok=True)
    unresolved = sorted(
        unresolved_by_id.values(),
        key=lambda item: str(item.get("last_seen_at") or ""),
        reverse=True,
    )[:500]
    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "processed": sorted(processed_ids),
        "unresolved": unresolved,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def register_unresolved_message(
    unresolved_by_id: dict[str, dict[str, Any]],
    *,
    reason: str,
    message_id: str,
    subject: str,
    sender: str,
    email_date_utc: datetime,
    application_no: str | None = None,
    details: str | None = None,
    saved_files: list[dict[str, Any]] | None = None,
) -> None:
    """Rejestruje wiadomość wymagającą ręcznej obsługi lub dodatkowego dopasowania."""
    now_iso = datetime.now(UTC).isoformat()
    existing = unresolved_by_id.get(message_id, {})
    attempts = int(existing.get("attempts") or 0) + 1
    unresolved_by_id[message_id] = {
        "message_id": message_id,
        "reason": reason,
        "subject": subject,
        "sender": sender,
        "email_date_utc": email_date_utc.isoformat(),
        "application_no": application_no,
        "details": details,
        "saved_files": saved_files or [],
        "attempts": attempts,
        "first_seen_at": str(existing.get("first_seen_at") or now_iso),
        "last_seen_at": now_iso,
    }


def _has_ocr_toolchain() -> bool:
    return shutil.which("pdftoppm") is not None and shutil.which("tesseract") is not None


def _ocr_pdf_with_external_tools(reader: PdfReader) -> str:
    """Uruchamia OCR przez pdftoppm+tesseract (jeśli narzędzia są dostępne)."""
    if not _has_ocr_toolchain():
        return ""

    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="ctip_mailbox_ocr_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        decrypted_pdf = tmp_path / "input.pdf"
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        with decrypted_pdf.open("wb") as handle:
            writer.write(handle)

        image_prefix = tmp_path / "page"
        subprocess.run(
            ["pdftoppm", "-png", str(decrypted_pdf), str(image_prefix)],
            check=True,
            capture_output=True,
            text=True,
        )

        text_parts: list[str] = []
        for image_path in sorted(tmp_path.glob("page-*.png")):
            cmd = ["tesseract", str(image_path), "stdout", "-l", "pol+eng", "--psm", "6"]
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout.strip():
                text_parts.append(proc.stdout.strip())
        return "\n".join(text_parts).strip()


def try_extract_text_from_pdf(
    pdf_bytes: bytes,
    *,
    password_candidates: list[str] | None = None,
) -> PdfExtractionResult:
    """Próbuje odszyfrować PDF i wyciągnąć z niego tekst."""
    candidates = password_candidates or [None]
    errors: list[str] = []
    for candidate in candidates:
        try:
            reader = PdfReader(BytesIO(pdf_bytes), strict=False)
            if reader.is_encrypted:
                if not candidate:
                    continue
                decrypt_result = reader.decrypt(candidate)
                if decrypt_result == 0:
                    continue

            text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
            if text:
                return PdfExtractionResult(
                    success=True,
                    password_used=candidate,
                    text=text,
                    method="pypdf-text",
                    error=None,
                    decrypted_pdf_bytes=_render_reader_pdf_bytes(reader),
                )

            try:
                ocr_text = _ocr_pdf_with_external_tools(reader)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"OCR error: {exc}")
                ocr_text = ""

            if ocr_text:
                return PdfExtractionResult(
                    success=True,
                    password_used=candidate,
                    text=ocr_text,
                    method="ocr",
                    error=None,
                    decrypted_pdf_bytes=_render_reader_pdf_bytes(reader),
                )

            return PdfExtractionResult(
                success=True,
                password_used=candidate,
                text="",
                method="empty",
                error=None,
                decrypted_pdf_bytes=_render_reader_pdf_bytes(reader),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            continue

    error_message = "; ".join(errors) if errors else "Nie udało się odszyfrować pliku PDF."
    return PdfExtractionResult(
        success=False,
        password_used=None,
        text="",
        method="none",
        error=error_message,
        decrypted_pdf_bytes=None,
    )


async def load_form_contexts(session: AsyncSession) -> list[FormContext]:
    """Wczytuje formularze SUBMITTED i ich kontekst workflow."""
    forms = list(
        (
            await session.execute(
                select(FormRequest)
                .where(FormRequest.status == "SUBMITTED")
                .order_by(desc(FormRequest.submitted_at), desc(FormRequest.id))
            )
        )
        .scalars()
        .all()
    )
    if not forms:
        return []

    form_ids = [item.id for item in forms]
    cases = list(
        (
            await session.execute(
                select(FormWorkflowCase).where(FormWorkflowCase.form_request_id.in_(form_ids))
            )
        )
        .scalars()
        .all()
    )
    case_by_form_id = {case.form_request_id: case for case in cases}

    contexts: list[FormContext] = []
    for form in forms:
        try:
            payload, _ = form_generator.decode_submitted_payload(form)
        except RuntimeError:
            continue
        if not isinstance(payload, dict):
            continue

        workflow_case = case_by_form_id.get(form.id)
        application_no_normalized = None
        if workflow_case and isinstance(workflow_case.client_payload_snapshot, dict):
            meta = workflow_case.client_payload_snapshot.get("_mailbox_meta")
            if isinstance(meta, dict):
                application_no_normalized = normalize_application_number(
                    str(meta.get("external_application_no") or "")
                )

        contexts.append(
            FormContext(
                form=form,
                payload=payload,
                workflow_case=workflow_case,
                application_no_normalized=application_no_normalized,
                proforma_no_normalized=normalize_proforma_number(
                    workflow_case.proforma_number if workflow_case else None
                ),
            )
        )
    return contexts


def pick_best_form_context(*, mail_ctx: MailContext, contexts: list[FormContext]) -> MatchDecision:
    """Wybiera najlepsze dopasowanie wiadomości do formularza."""
    if not contexts:
        return MatchDecision(context=None, reason="no_forms")

    if mail_ctx.application_no_normalized:
        exact = [
            ctx
            for ctx in contexts
            if ctx.application_no_normalized == mail_ctx.application_no_normalized
        ]
        if len(exact) == 1:
            return MatchDecision(context=exact[0], reason="exact_application", score=100)
        if len(exact) > 1:
            return MatchDecision(context=None, reason="ambiguous_application")

    if mail_ctx.proforma_no_normalized:
        exact_proforma = [
            ctx for ctx in contexts if ctx.proforma_no_normalized == mail_ctx.proforma_no_normalized
        ]
        if len(exact_proforma) == 1:
            return MatchDecision(context=exact_proforma[0], reason="exact_proforma", score=95)
        if len(exact_proforma) > 1:
            return MatchDecision(context=None, reason="ambiguous_proforma")

    scored: list[tuple[int, FormContext]] = []
    for ctx in contexts:
        score = score_form_match(mail_ctx.body_text, ctx.payload)
        if score > 0:
            scored.append((score, ctx))

    if not scored:
        return MatchDecision(context=None, reason="score_zero", score=0)

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_ctx = scored[0]
    if len(scored) > 1 and scored[1][0] == best_score:
        return MatchDecision(context=None, reason="ambiguous_score", score=best_score)
    return MatchDecision(context=best_ctx, reason="score_match", score=best_score)


def find_encrypted_contract_pdf(
    attachments: list[tuple[str, str, bytes]],
) -> tuple[str, bytes] | None:
    """Zwraca pierwszy zaszyfrowany PDF z listy załączników."""
    for name, _, data in attachments:
        if not str(name).lower().endswith(".pdf"):
            continue
        try:
            reader = PdfReader(BytesIO(data), strict=False)
        except Exception:  # noqa: BLE001
            continue
        if reader.is_encrypted:
            return name, data
    return None


def build_mail_context(imap_id: str, msg: Message) -> MailContext | None:
    """Buduje kontekst przetwarzania wiadomości (temat lub treść)."""
    subject = decode_mime_text(msg.get("Subject"))
    body_text = extract_message_body_text(msg)
    event_type = classify_mail_payload(subject=subject, body=body_text)
    if event_type is None:
        return None

    app_from_subject = extract_application_number(subject)
    app_from_body = extract_application_number(body_text)
    app_ref = app_from_subject or app_from_body
    proforma_from_subject = extract_proforma_number(subject)
    proforma_from_body = extract_proforma_number(body_text)
    proforma_ref = proforma_from_subject or proforma_from_body

    message_id = decode_mime_text(msg.get("Message-Id")) or f"imap:{imap_id}"
    sender = decode_mime_text(msg.get("From"))
    email_date_utc = parse_email_date(msg.get("Date"))
    attachments = extract_attachments(msg)

    return MailContext(
        imap_id=imap_id,
        message_id=message_id,
        subject=subject,
        sender=sender,
        body_text=body_text,
        email_date_utc=email_date_utc,
        event_type=event_type,
        application_no_raw=app_ref.raw if app_ref else None,
        application_no_normalized=app_ref.normalized if app_ref else None,
        attachments=attachments,
        proforma_no_raw=proforma_ref.raw if proforma_ref else None,
        proforma_no_normalized=proforma_ref.normalized if proforma_ref else None,
    )


def resolve_mailbox_business_status(
    *,
    mail_ctx: MailContext,
    current_business_status: str | None,
    decision_text: str,
) -> tuple[str, bool, bool]:
    """Wylicza docelowy status i chroni statusy końcowe przed cofnięciem.

    Zwykła decyzja GRENKE bez jawnej odmowy oznacza etap oczekiwania na podpis.
    Przy ponownym przetwarzaniu historycznej skrzynki taka wiadomość nie może
    cofnąć sprawy, która jest już zakończona zgodą, odmową lub ręcznym trybem
    wynajmu bez GRENKE.
    """
    if mail_ctx.event_type == MAILBOX_EVENT_APPROVAL:
        return WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER, False, False
    if mail_ctx.event_type != MAILBOX_EVENT_DECISION:
        return WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE, False, False

    is_rejection = detect_rejection_decision(decision_text)
    if is_rejection:
        return WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE, True, False

    current_status = normalize_workflow_business_status(current_business_status)
    if current_status in MAILBOX_GENERIC_DECISION_PROTECTED_STATUSES:
        return current_status, False, True

    return WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE, False, False


def attach_mailbox_meta(
    workflow_case: FormWorkflowCase,
    *,
    mail_ctx: MailContext,
    message_kind: str,
    extracted_data: dict[str, Any] | None,
    saved_files: list[dict[str, Any]] | None = None,
    archived_contract_file: dict[str, Any] | None = None,
) -> None:
    """Aktualizuje metadane mailbox w snapshotcie sprawy workflow."""
    snapshot = (
        dict(workflow_case.client_payload_snapshot)
        if isinstance(workflow_case.client_payload_snapshot, dict)
        else {}
    )
    raw_meta = snapshot.get("_mailbox_meta")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}

    meta["external_application_no"] = mail_ctx.application_no_raw
    meta["external_application_no_normalized"] = mail_ctx.application_no_normalized
    meta["external_proforma_no"] = mail_ctx.proforma_no_raw
    meta["external_proforma_no_normalized"] = mail_ctx.proforma_no_normalized
    meta["last_message_id"] = mail_ctx.message_id
    meta["last_message_subject"] = mail_ctx.subject
    meta["last_message_sender"] = mail_ctx.sender
    meta["last_message_date_utc"] = mail_ctx.email_date_utc.isoformat()
    meta["last_event_type"] = mail_ctx.event_type
    meta["last_message_kind"] = message_kind
    if extracted_data:
        meta["last_extracted_data"] = extracted_data
    if archived_contract_file:
        meta["last_archived_contract_file"] = archived_contract_file
        raw_archive_history = meta.get("archived_contract_files")
        archive_history = (
            [item for item in raw_archive_history if isinstance(item, dict)]
            if isinstance(raw_archive_history, list)
            else []
        )
        archive_history.append(archived_contract_file)
        meta["archived_contract_files"] = archive_history[-50:]
    if saved_files:
        meta["last_saved_files"] = saved_files
        history = meta.get("saved_files_history")
        if isinstance(history, list):
            history_values = [item for item in history if isinstance(item, dict)]
        else:
            history_values = []
        history_values.append(
            {
                "message_id": mail_ctx.message_id,
                "saved_at_utc": datetime.now(UTC).isoformat(),
                "files": saved_files,
            }
        )
        meta["saved_files_history"] = history_values[-30:]

    processed_ids = meta.get("processed_message_ids")
    if isinstance(processed_ids, list):
        values = [str(item) for item in processed_ids if str(item).strip()]
    else:
        values = []
    if mail_ctx.message_id not in values:
        values.append(mail_ctx.message_id)
    meta["processed_message_ids"] = values[-50:]

    snapshot["_mailbox_meta"] = meta
    workflow_case.client_payload_snapshot = snapshot


async def apply_mail_to_workflow(
    session: AsyncSession,
    *,
    form_ctx: FormContext,
    mail_ctx: MailContext,
    extracted_data: dict[str, Any] | None,
    pdf_text: str = "",
    archived_contract_file: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aktualizuje workflow formularza na podstawie pojedynczej wiadomości.

    Dla zgody na realizację zamówienia automat mailboxa musi uruchomić ten sam
    tor wiązania urządzeń, którego używa ręczna zmiana statusu w panelu.
    """
    workflow_case = form_ctx.workflow_case
    if workflow_case is None:
        workflow_case = await get_or_create_form_workflow_case(
            session,
            form=form_ctx.form,
            user_id=None,
            payload_snapshot=form_ctx.payload,
        )
        form_ctx.workflow_case = workflow_case

    decision_text = " ".join(
        [
            mail_ctx.subject,
            mail_ctx.body_text,
            pdf_text,
            json.dumps(extracted_data or {}, ensure_ascii=False),
        ]
    )
    new_status, is_rejection, status_update_skipped = resolve_mailbox_business_status(
        mail_ctx=mail_ctx,
        current_business_status=getattr(workflow_case, "business_status", None),
        decision_text=decision_text,
    )

    if mail_ctx.event_type == MAILBOX_EVENT_DECISION:
        if not status_update_skipped:
            workflow_case = await set_form_workflow_business_status(
                session,
                workflow_case=workflow_case,
                business_status=new_status,
                updated_by=None,
                signature_deadline_at=(
                    mail_ctx.email_date_utc + timedelta(days=7) if not is_rejection else None
                ),
                changed_at=mail_ctx.email_date_utc,
                status_source="mailbox",
            )
        if is_rejection:
            form_ctx.form.archive_due_at = mail_ctx.email_date_utc + timedelta(days=14)
        if status_update_skipped:
            message_kind = "decyzja_pominieta"
        else:
            message_kind = "odmowa" if is_rejection else "decyzja"
    else:
        workflow_case = await set_form_workflow_business_status(
            session,
            workflow_case=workflow_case,
            business_status=WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
            updated_by=None,
            changed_at=mail_ctx.email_date_utc,
            status_source="mailbox",
        )
        signature_date: date = mail_ctx.email_date_utc.date()
        existing_notes = str(workflow_case.delivery_notes or "").strip()
        signature_note = f"Data podpisania umowy (mail): {signature_date.isoformat()}"
        notes = existing_notes
        if signature_note not in existing_notes:
            notes = f"{existing_notes}\n{signature_note}".strip()

        workflow_case = await set_form_workflow_delivery(
            session,
            workflow_case=workflow_case,
            delivery_date=signature_date,
            delivery_time_window=workflow_case.delivery_time_window,
            delivery_contact_name=workflow_case.delivery_contact_name,
            delivery_contact_phone=workflow_case.delivery_contact_phone,
            delivery_notes=notes,
            updated_by=None,
        )
        message_kind = "zgoda"
        form_ctx.form.archive_due_at = mail_ctx.email_date_utc + timedelta(days=14)

    saved_files = persist_mail_attachments(scope=f"case_{workflow_case.id}", mail_ctx=mail_ctx)

    attach_mailbox_meta(
        workflow_case,
        mail_ctx=mail_ctx,
        message_kind=message_kind,
        extracted_data=extracted_data,
        saved_files=saved_files,
        archived_contract_file=archived_contract_file,
    )
    delivery_meta: dict[str, Any] | None = None
    binding_items_payload: list[dict[str, Any]] = []
    binding_alert_payload: dict[str, Any] | None = None
    if new_status == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER:
        workflow_devices = await list_form_workflow_devices(
            session, workflow_case_id=workflow_case.id
        )
        binding_items, _ = await asyncio.to_thread(
            bind_devices_to_workflow_client,
            workflow_case=workflow_case,
            devices=workflow_devices,
            actor_label="Automat skrzynki GRENKE",
        )
        binding_items_payload = [item.as_dict() for item in binding_items]
        binding_by_device_id = {item.workflow_device_id: item for item in binding_items}
        for workflow_device in workflow_devices:
            apply_binding_snapshot(
                device=workflow_device,
                item=binding_by_device_id.get(workflow_device.id),
            )

        binding_failures = [item for item in binding_items if not item.ok]
        if binding_failures:
            binding_alert_payload = await notify_binding_issues_to_admins(
                session,
                workflow_case=workflow_case,
                form_request_id=form_ctx.form.id,
                failures=binding_failures,
                triggered_by_user_id=None,
            )
        delivery_case, contract_end = await ensure_delivery_case_for_workflow(
            session,
            workflow_case=workflow_case,
            form_request=form_ctx.form,
            devices=workflow_devices,
            updated_by=None,
        )
        delivery_meta = {
            "delivery_case_id": delivery_case.id,
            "grenke_contract_end_id": contract_end.id,
            "grenke_contract_end_status": contract_end.status,
        }
    workflow_case.updated_at = datetime.now(UTC)
    await record_audit(
        session,
        user_id=None,
        action="contracts_mailbox_sync",
        client_ip="mailbox-automation",
        payload={
            "form_request_id": form_ctx.form.id,
            "workflow_case_id": workflow_case.id,
            "message_id": mail_ctx.message_id,
            "subject": mail_ctx.subject,
            "event_type": mail_ctx.event_type,
            "application_no": mail_ctx.application_no_raw,
            "proforma_no": mail_ctx.proforma_no_raw,
            "new_business_status": new_status,
            "status_update_skipped": status_update_skipped,
            "email_date_utc": mail_ctx.email_date_utc.isoformat(),
            "extracted_data": extracted_data,
            "saved_files": saved_files,
            "archived_contract_file": archived_contract_file,
            "binding_items": binding_items_payload,
            "binding_alert": binding_alert_payload,
            "delivery": delivery_meta,
        },
    )
    await session.flush()
    form_ctx.application_no_normalized = mail_ctx.application_no_normalized
    return {
        "form_id": form_ctx.form.id,
        "workflow_case_id": workflow_case.id,
        "new_business_status": new_status,
        "status_update_skipped": status_update_skipped,
        "email_date_utc": mail_ctx.email_date_utc.isoformat(),
        "saved_files_count": len(saved_files),
        "archived_contract_file": archived_contract_file,
        "delivery": delivery_meta,
    }


async def run_sync(args: argparse.Namespace) -> int:
    """Uruchamia pełną synchronizację mailbox -> FLOW."""
    if not settings.mailbox_imap_host or not settings.mailbox_email_address:
        print("[ERR] Brak konfiguracji MAILBOX_* w środowisku.")
        return 2

    processed_state, unresolved_state = _load_state(STATE_PATH)
    processed_now: set[str] = set()
    state_changed = False
    analysed = 0
    updated = 0
    skipped = 0
    unknown_subjects = 0
    unmatched_forms = 0
    ambiguous_matches = 0
    warnings: list[str] = []
    reports: list[dict[str, Any]] = []

    async with AsyncSessionLocal() as session:
        form_contexts = await load_form_contexts(session)
        if not form_contexts:
            print("[WARN] Brak formularzy SUBMITTED do powiązania.")

        with imaplib.IMAP4_SSL(
            settings.mailbox_imap_host,
            settings.mailbox_imap_port,
            timeout=20,
        ) as client:
            client.login(settings.mailbox_email_address, settings.mailbox_email_password or "")
            status, _ = client.select(args.folder, readonly=True)
            if status != "OK":
                print(f"[ERR] Nie można otworzyć folderu IMAP: {args.folder}")
                return 3

            typ, data = client.search(None, "ALL")
            if typ != "OK":
                print("[ERR] Nie udało się pobrać listy wiadomości.")
                return 4

            ids = data[0].split()
            for raw_id in ids[-max(1, int(args.limit)) :]:
                imap_id = raw_id.decode(errors="ignore")
                fetch_status, full_data = client.fetch(raw_id, "(BODY.PEEK[])")
                if fetch_status != "OK":
                    warnings.append(f"Nie udało się pobrać wiadomości IMAP id={imap_id}.")
                    continue

                raw_email = b""
                for item in full_data:
                    if isinstance(item, tuple):
                        raw_email += item[1]
                if not raw_email:
                    continue

                msg = message_from_bytes(raw_email)
                message_id = decode_mime_text(msg.get("Message-Id")) or f"imap:{imap_id}"
                if not args.reprocess and message_id in processed_state:
                    skipped += 1
                    continue

                mail_ctx = build_mail_context(imap_id, msg)
                if mail_ctx is None:
                    unknown_subjects += 1
                    subject = decode_mime_text(msg.get("Subject"))
                    sender = decode_mime_text(msg.get("From"))
                    email_date_utc = parse_email_date(msg.get("Date"))
                    register_unresolved_message(
                        unresolved_state,
                        reason=UNRESOLVED_REASON_UNSUPPORTED_SUBJECT,
                        message_id=message_id,
                        subject=subject,
                        sender=sender,
                        email_date_utc=email_date_utc,
                        application_no=None,
                        details="Brak zgodnego wzorca tematu lub treści.",
                        saved_files=[],
                    )
                    state_changed = True
                    warnings.append(
                        f"Nierozpoznany temat wiadomości: {subject} (message_id={message_id})."
                    )
                    reports.append(
                        {
                            "message_id": message_id,
                            "subject": subject,
                            "matched": False,
                            "match_reason": UNRESOLVED_REASON_UNSUPPORTED_SUBJECT,
                            "saved_files_count": 0,
                            "pdf_success": None,
                            "pdf_error": None,
                            "extracted_data": None,
                        }
                    )
                    continue

                analysed += 1
                match_decision = pick_best_form_context(mail_ctx=mail_ctx, contexts=form_contexts)
                matched_ctx = match_decision.context
                body_extracted = extract_data_from_contract_text(mail_ctx.body_text)
                extracted_data: dict[str, Any] | None = {"from_body": body_extracted}
                pdf_result: PdfExtractionResult | None = None

                encrypted_pdf = find_encrypted_contract_pdf(mail_ctx.attachments)
                if encrypted_pdf is not None:
                    _, pdf_bytes = encrypted_pdf
                    if matched_ctx is not None:
                        candidates = build_pdf_password_candidates(matched_ctx.payload)
                        pdf_result = try_extract_text_from_pdf(
                            pdf_bytes,
                            password_candidates=candidates,
                        )
                    else:
                        for candidate_ctx in form_contexts:
                            candidates = build_pdf_password_candidates(candidate_ctx.payload)
                            probe_result = try_extract_text_from_pdf(
                                pdf_bytes,
                                password_candidates=candidates,
                            )
                            if probe_result.success:
                                matched_ctx = candidate_ctx
                                pdf_result = probe_result
                                break
                        if pdf_result is None:
                            pdf_result = try_extract_text_from_pdf(
                                pdf_bytes,
                                password_candidates=[],
                            )

                    if pdf_result.success and pdf_result.text.strip():
                        extracted_data["from_pdf"] = extract_data_from_contract_text(
                            pdf_result.text
                        )

                if matched_ctx is None:
                    unresolved_reason = UNRESOLVED_REASON_UNMATCHED_FORM
                    if match_decision.reason in {
                        "ambiguous_application",
                        "ambiguous_proforma",
                        "ambiguous_score",
                    }:
                        unresolved_reason = UNRESOLVED_REASON_AMBIGUOUS_MATCH
                        ambiguous_matches += 1
                    else:
                        unmatched_forms += 1

                    unresolved_saved_files: list[dict[str, Any]] = []
                    if not args.dry_run:
                        unresolved_saved_files = persist_mail_attachments(
                            scope="unresolved",
                            mail_ctx=mail_ctx,
                        )

                    register_unresolved_message(
                        unresolved_state,
                        reason=unresolved_reason,
                        message_id=mail_ctx.message_id,
                        subject=mail_ctx.subject,
                        sender=mail_ctx.sender,
                        email_date_utc=mail_ctx.email_date_utc,
                        application_no=mail_ctx.application_no_raw,
                        details=(
                            f"match_reason={match_decision.reason}; score={match_decision.score}; "
                            f"proforma_no={mail_ctx.proforma_no_raw}"
                        ),
                        saved_files=unresolved_saved_files,
                    )
                    state_changed = True
                    warnings.append(
                        f"Brak dopasowania formularza dla wiadomości: {mail_ctx.subject} "
                        f"(message_id={mail_ctx.message_id}, reason={match_decision.reason})."
                    )
                    reports.append(
                        {
                            "message_id": mail_ctx.message_id,
                            "subject": mail_ctx.subject,
                            "matched": False,
                            "match_reason": unresolved_reason,
                            "proforma_no": mail_ctx.proforma_no_raw,
                            "saved_files_count": len(unresolved_saved_files),
                            "pdf_success": pdf_result.success if pdf_result else None,
                            "pdf_error": pdf_result.error if pdf_result else None,
                            "extracted_data": extracted_data,
                        }
                    )
                    continue

                archived_contract_file: dict[str, Any] | None = None
                if not args.dry_run and matched_ctx is not None and encrypted_pdf is not None:
                    encrypted_pdf_name, _ = encrypted_pdf
                    if (
                        pdf_result is not None
                        and pdf_result.success
                        and pdf_result.decrypted_pdf_bytes
                    ):
                        try:
                            archived_contract_file = persist_decrypted_contract_pdf(
                                form_ctx=matched_ctx,
                                mail_ctx=mail_ctx,
                                original_file_name=encrypted_pdf_name,
                                pdf_bytes=pdf_result.decrypted_pdf_bytes,
                            )
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(
                                "Nie udalo sie zapisac odszyfrowanej umowy PDF "
                                f"dla formularza {matched_ctx.form.id}: {exc}"
                            )
                    else:
                        try:
                            archived_contract_file = persist_encrypted_contract_pdf(
                                form_ctx=matched_ctx,
                                mail_ctx=mail_ctx,
                                original_file_name=encrypted_pdf_name,
                                pdf_bytes=encrypted_pdf[1],
                            )
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(
                                "Nie udalo sie zapisac zaszyfrowanej umowy PDF "
                                f"dla formularza {matched_ctx.form.id}: {exc}"
                            )

                if not args.dry_run:
                    update_result = await apply_mail_to_workflow(
                        session,
                        form_ctx=matched_ctx,
                        mail_ctx=mail_ctx,
                        extracted_data=extracted_data,
                        pdf_text=pdf_result.text if pdf_result else "",
                        archived_contract_file=archived_contract_file,
                    )
                    await session.commit()
                else:
                    dry_decision_text = " ".join(
                        [
                            mail_ctx.subject,
                            mail_ctx.body_text,
                            pdf_result.text if pdf_result else "",
                        ]
                    )
                    dry_status, _, dry_status_update_skipped = resolve_mailbox_business_status(
                        mail_ctx=mail_ctx,
                        current_business_status=(
                            matched_ctx.workflow_case.business_status
                            if matched_ctx.workflow_case is not None
                            else None
                        ),
                        decision_text=dry_decision_text,
                    )
                    update_result = {
                        "form_id": matched_ctx.form.id,
                        "workflow_case_id": (
                            matched_ctx.workflow_case.id if matched_ctx.workflow_case else None
                        ),
                        "new_business_status": dry_status,
                        "status_update_skipped": dry_status_update_skipped,
                        "email_date_utc": mail_ctx.email_date_utc.isoformat(),
                        "saved_files_count": len(mail_ctx.attachments),
                        "archived_contract_file": archived_contract_file,
                    }

                updated += 1
                processed_now.add(mail_ctx.message_id)
                if mail_ctx.message_id in unresolved_state:
                    unresolved_state.pop(mail_ctx.message_id, None)
                    state_changed = True

                reports.append(
                    {
                        "message_id": mail_ctx.message_id,
                        "subject": mail_ctx.subject,
                        "matched": True,
                        "match_reason": match_decision.reason,
                        "form_id": update_result["form_id"],
                        "workflow_case_id": update_result["workflow_case_id"],
                        "new_business_status": update_result["new_business_status"],
                        "status_update_skipped": update_result.get("status_update_skipped"),
                        "application_no": mail_ctx.application_no_raw,
                        "proforma_no": mail_ctx.proforma_no_raw,
                        "email_date_utc": update_result["email_date_utc"],
                        "saved_files_count": update_result.get("saved_files_count"),
                        "archived_contract_file": update_result.get("archived_contract_file"),
                        "pdf_success": pdf_result.success if pdf_result else None,
                        "pdf_method": pdf_result.method if pdf_result else None,
                        "pdf_password_used": pdf_result.password_used if pdf_result else None,
                        "pdf_error": pdf_result.error if pdf_result else None,
                        "extracted_data": extracted_data,
                    }
                )

    if not args.dry_run and (processed_now or state_changed):
        _save_state(STATE_PATH, processed_state.union(processed_now), unresolved_state)

    print(
        f"[INFO] Analizowane: {analysed}, zaktualizowane: {updated}, "
        f"pominięte (stan): {skipped}, ostrzeżenia: {len(warnings)}, "
        f"nierozpoznane: {unknown_subjects}, niedopasowane: {unmatched_forms}, "
        f"wieloznaczne: {ambiguous_matches}, otwarte wyjątki: {len(unresolved_state)}"
    )
    for item in reports:
        print("-" * 80)
        print(f"[INFO] Message-ID: {item.get('message_id')}")
        print(f"[INFO] Temat: {item.get('subject')}")
        if not item.get("matched"):
            print(f"[WARN] Nie dopasowano formularza. Powód: {item.get('match_reason')}")
        else:
            print(
                f"[OK] Form ID: {item.get('form_id')}, Workflow ID: {item.get('workflow_case_id')}, "
                f"Nowy status: {item.get('new_business_status')}"
            )
            print(f"[INFO] Nr wniosku: {item.get('application_no')}")
            if item.get("proforma_no"):
                print(f"[INFO] Nr proformy: {item.get('proforma_no')}")
            print(f"[INFO] Data e-mail (UTC): {item.get('email_date_utc')}")
        if item.get("saved_files_count") is not None:
            print(f"[INFO] Zapisane załączniki: {item.get('saved_files_count')}")
        archived_contract_file = item.get("archived_contract_file")
        if isinstance(archived_contract_file, dict):
            archived_path = str(archived_contract_file.get("path") or "").strip()
            archived_description = str(archived_contract_file.get("description") or "").strip()
            if archived_path:
                print(
                    "[INFO] Zapisana umowa: "
                    + archived_path
                    + (f" ({archived_description})" if archived_description else "")
                )
        if item.get("pdf_success") is not None:
            print(
                f"[INFO] PDF success={item.get('pdf_success')} "
                f"method={item.get('pdf_method')} password={item.get('pdf_password_used')}"
            )
            if item.get("pdf_error"):
                print(f"[WARN] PDF error: {item.get('pdf_error')}")
        extracted = item.get("extracted_data")
        if extracted:
            extracted_json = json.dumps(extracted, ensure_ascii=False)
            print(
                "[INFO] OCR/ekstrakcja: "
                f"{_truncate_for_log(extracted_json, max_chars=MAX_EXTRACT_LOG_CHARS)}"
            )

    if warnings:
        total_warnings = len(warnings)
        for index, warning in enumerate(warnings[:MAX_WARNING_LOG_ITEMS], start=1):
            print(
                f"[WARN] ({index}/{total_warnings}) "
                f"{_truncate_for_log(warning, max_chars=MAX_WARNING_LOG_CHARS)}"
            )
        hidden_warnings = total_warnings - MAX_WARNING_LOG_ITEMS
        if hidden_warnings > 0:
            print(
                f"[WARN] Pominięto {hidden_warnings} dalszych ostrzeżeń w logu "
                "(limit wypisywania przekroczony)."
            )

    if warnings and args.fail_on_warnings:
        return 1
    return 0


def main() -> int:
    """Punkt startowy CLI."""
    args = parse_args()
    return __import__("asyncio").run(run_sync(args))


if __name__ == "__main__":
    raise SystemExit(main())
