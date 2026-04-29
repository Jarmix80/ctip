"""Testy zabezpieczeń i stanu skryptu synchronizacji mailbox."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


def _load_sync_module():
    module_name = "contracts_mailbox_sync_for_tests"
    if module_name in sys.modules:
        return sys.modules[module_name]
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "contracts_mailbox_sync.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_state_roundtrip_keeps_unresolved_entries(tmp_path: Path) -> None:
    module = _load_sync_module()
    path = tmp_path / "state.json"
    unresolved = {
        "msg-1": {
            "message_id": "msg-1",
            "reason": "unmatched_form",
            "subject": "Decyzja do wniosku 173-025167",
            "sender": "test@example.com",
            "email_date_utc": "2026-04-01T13:13:12+00:00",
            "application_no": "173-025167",
            "details": "match_reason=score_zero",
            "attempts": 2,
            "first_seen_at": "2026-04-01T13:20:00+00:00",
            "last_seen_at": "2026-04-01T13:25:00+00:00",
        }
    }
    module._save_state(path, {"ok-1"}, unresolved)

    processed, unresolved_loaded = module._load_state(path)
    assert processed == {"ok-1"}
    assert set(unresolved_loaded.keys()) == {"msg-1"}
    assert unresolved_loaded["msg-1"]["attempts"] == 2
    assert unresolved_loaded["msg-1"]["reason"] == "unmatched_form"


def test_register_unresolved_message_increments_attempts() -> None:
    module = _load_sync_module()
    unresolved: dict[str, dict] = {}
    now = datetime(2026, 4, 1, 13, 13, 12, tzinfo=UTC)

    module.register_unresolved_message(
        unresolved,
        reason=module.UNRESOLVED_REASON_UNMATCHED_FORM,
        message_id="msg-2",
        subject="Decyzja do wniosku 173-025167",
        sender="robot@example.com",
        email_date_utc=now,
        application_no="173-025167",
        details="match_reason=score_zero",
    )
    module.register_unresolved_message(
        unresolved,
        reason=module.UNRESOLVED_REASON_UNMATCHED_FORM,
        message_id="msg-2",
        subject="Decyzja do wniosku 173-025167",
        sender="robot@example.com",
        email_date_utc=now,
        application_no="173-025167",
        details="match_reason=score_zero",
    )

    assert unresolved["msg-2"]["attempts"] == 2
    assert unresolved["msg-2"]["reason"] == module.UNRESOLVED_REASON_UNMATCHED_FORM


def test_pick_best_form_context_marks_ambiguous_score() -> None:
    module = _load_sync_module()

    mail_ctx = module.MailContext(
        imap_id="11",
        message_id="msg-amb",
        subject="Decyzja do wniosku 173-025167",
        sender="x@example.com",
        body_text="Pozytywna decyzja dla ABC Sp z o.o.",
        email_date_utc=datetime(2026, 4, 1, 13, 13, 12, tzinfo=UTC),
        event_type=module.MAILBOX_EVENT_DECISION,
        application_no_raw="173-025167",
        application_no_normalized=None,
        attachments=[],
    )
    contexts = [
        module.FormContext(
            form=object(),
            payload={"company_name": "ABC Sp z o.o."},
            workflow_case=None,
            application_no_normalized=None,
        ),
        module.FormContext(
            form=object(),
            payload={"company_name": "ABC Sp z o.o."},
            workflow_case=None,
            application_no_normalized=None,
        ),
    ]

    decision = module.pick_best_form_context(mail_ctx=mail_ctx, contexts=contexts)
    assert decision.context is None
    assert decision.reason == "ambiguous_score"


def test_pick_best_form_context_prefers_exact_proforma_match() -> None:
    module = _load_sync_module()

    mail_ctx = module.MailContext(
        imap_id="12",
        message_id="msg-proforma",
        subject="Decyzja do wniosku 173-025203 / Faktura Pro Forma nr: 10/proforma/2026",
        sender="x@example.com",
        body_text="",
        email_date_utc=datetime(2026, 4, 1, 13, 13, 12, tzinfo=UTC),
        event_type=module.MAILBOX_EVENT_DECISION,
        application_no_raw="173-025203",
        application_no_normalized="173025203",
        attachments=[],
        proforma_no_raw="10/proforma/2026",
        proforma_no_normalized="10/proforma/2026",
    )
    contexts = [
        module.FormContext(
            form=object(),
            payload={"company_name": "ABC Sp z o.o."},
            workflow_case=None,
            application_no_normalized=None,
            proforma_no_normalized="10/proforma/2026",
        ),
        module.FormContext(
            form=object(),
            payload={"company_name": "XYZ Sp z o.o."},
            workflow_case=None,
            application_no_normalized=None,
            proforma_no_normalized="11/proforma/2026",
        ),
    ]

    decision = module.pick_best_form_context(mail_ctx=mail_ctx, contexts=contexts)
    assert decision.context is contexts[0]
    assert decision.reason == "exact_proforma"


def test_persist_mail_attachments_saves_files_and_metadata(tmp_path: Path) -> None:
    module = _load_sync_module()
    mail_ctx = module.MailContext(
        imap_id="11",
        message_id="<abc@test>",
        subject="Decyzja do wniosku 173-025167",
        sender="x@example.com",
        body_text="treść",
        email_date_utc=datetime(2026, 4, 1, 13, 13, 12, tzinfo=UTC),
        event_type=module.MAILBOX_EVENT_DECISION,
        application_no_raw="173-025167",
        application_no_normalized="173025167",
        attachments=[
            ("umowa.pdf", "application/pdf", b"pdf-bytes"),
            ("instrukcja.txt", "text/plain", b"instrukcja"),
        ],
    )

    previous_base = module.ATTACHMENTS_BASE_PATH
    module.ATTACHMENTS_BASE_PATH = tmp_path
    try:
        saved = module.persist_mail_attachments(scope="case_16", mail_ctx=mail_ctx)
    finally:
        module.ATTACHMENTS_BASE_PATH = previous_base

    assert len(saved) == 2
    for item in saved:
        path = Path(item["path"])
        assert path.exists()
        assert path.is_file()
    assert saved[0]["sha256"] == hashlib.sha256(b"pdf-bytes").hexdigest()
    assert saved[1]["sha256"] == hashlib.sha256(b"instrukcja").hexdigest()


def test_persist_decrypted_contract_pdf_saves_file_and_description(tmp_path: Path) -> None:
    module = _load_sync_module()
    form = SimpleNamespace(id=321, customer_name="ACME Sp. z o.o.")
    form_ctx = module.FormContext(
        form=form,
        payload={"company_name": "ACME/TEST:COMPANY"},
        workflow_case=None,
        application_no_normalized=None,
    )
    mail_ctx = module.MailContext(
        imap_id="99",
        message_id="<msg@test>",
        subject="Decyzja do wniosku 173-025167",
        sender="x@example.com",
        body_text="",
        email_date_utc=datetime(2026, 4, 2, 8, 30, 0, tzinfo=UTC),
        event_type=module.MAILBOX_EVENT_DECISION,
        application_no_raw="173-025167",
        application_no_normalized="173025167",
        attachments=[],
    )

    previous_root = module.settings.contracts_mailbox_archive_root
    module.settings.contracts_mailbox_archive_root = str(tmp_path)
    try:
        saved = module.persist_decrypted_contract_pdf(
            form_ctx=form_ctx,
            mail_ctx=mail_ctx,
            original_file_name="umowa grenke.pdf",
            pdf_bytes=b"%PDF-1.4\nfake\n",
        )
    finally:
        module.settings.contracts_mailbox_archive_root = previous_root

    assert isinstance(saved, dict)
    path = Path(saved["path"])
    assert path.exists()
    assert path.is_file()
    assert path.read_bytes() == b"%PDF-1.4\nfake\n"
    assert saved["description"] == "Umowa GRENKE (odszyfrowany PDF z e-maila)."
    assert path.parent.parent.name == "ACME_TEST_COMPANY"
    assert path.parent.name == "321"


def test_persist_encrypted_contract_pdf_saves_file_and_description(tmp_path: Path) -> None:
    module = _load_sync_module()
    form = SimpleNamespace(id=654, customer_name="ALFA Sp. z o.o.")
    form_ctx = module.FormContext(
        form=form,
        payload={"company_name": "ALFA/TEST:COMPANY"},
        workflow_case=None,
        application_no_normalized=None,
    )
    mail_ctx = module.MailContext(
        imap_id="100",
        message_id="<msg-encrypted@test>",
        subject="Decyzja do wniosku 173-025296",
        sender="x@example.com",
        body_text="",
        email_date_utc=datetime(2026, 4, 16, 11, 37, 30, tzinfo=UTC),
        event_type=module.MAILBOX_EVENT_DECISION,
        application_no_raw="173-025296",
        application_no_normalized="173025296",
        attachments=[],
    )

    previous_root = module.settings.contracts_mailbox_archive_root
    module.settings.contracts_mailbox_archive_root = str(tmp_path)
    try:
        saved = module.persist_encrypted_contract_pdf(
            form_ctx=form_ctx,
            mail_ctx=mail_ctx,
            original_file_name="173_25296_wn_a_cr.pdf",
            pdf_bytes=b"%PDF-1.4\\nencrypted\\n",
        )
    finally:
        module.settings.contracts_mailbox_archive_root = previous_root

    assert isinstance(saved, dict)
    path = Path(saved["path"])
    assert path.exists()
    assert path.is_file()
    assert path.read_bytes() == b"%PDF-1.4\\nencrypted\\n"
    assert saved["kind"] == "encrypted_contract_pdf"
    assert saved["description"] == "Umowa GRENKE (zaszyfrowany PDF z e-maila)."
    assert path.parent.parent.name == "ALFA_TEST_COMPANY"
    assert path.parent.name == "654"
