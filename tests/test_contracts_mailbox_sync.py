"""Testy zabezpieczeń i stanu skryptu synchronizacji mailbox."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path


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
