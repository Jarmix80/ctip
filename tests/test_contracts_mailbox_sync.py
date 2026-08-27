"""Testy zabezpieczeń i stanu skryptu synchronizacji mailbox."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
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


def test_build_mail_context_uses_body_for_event_classification() -> None:
    module = _load_sync_module()
    msg = EmailMessage()
    msg["Subject"] = "Potwierdzenie"
    msg["Message-Id"] = "<body-classify@test>"
    msg["From"] = "noreply@grenke.pl"
    msg["Date"] = "Tue, 3 Jun 2026 12:00:00 +0000"
    msg.set_content("Decyzja do wniosku 173-025167: pozytywna odpowiedź systemu.")

    mail_ctx = module.build_mail_context("88", msg)
    assert mail_ctx is not None
    assert mail_ctx.event_type == module.MAILBOX_EVENT_DECISION
    assert mail_ctx.application_no_raw == "173-025167"


def test_build_mail_context_prefers_approval_body_over_decision_subject() -> None:
    module = _load_sync_module()
    msg = EmailMessage()
    msg["Subject"] = "Decyzja do wniosku 173-025299"
    msg["Message-Id"] = "<approval-body@test>"
    msg["From"] = "noreply@grenke.pl"
    msg["Date"] = "Tue, 3 Jun 2026 12:00:00 +0000"
    msg.set_content("Zgoda na realizację zamówienia do wniosku nr: 173-025299.")

    mail_ctx = module.build_mail_context("89", msg)
    assert mail_ctx is not None
    assert mail_ctx.event_type == module.MAILBOX_EVENT_APPROVAL
    assert mail_ctx.application_no_raw == "173-025299"


def test_resolve_mailbox_business_status_does_not_downgrade_final_status() -> None:
    module = _load_sync_module()
    mail_ctx = module.MailContext(
        imap_id="90",
        message_id="<old-decision@test>",
        subject="Decyzja do wniosku 173-025299",
        sender="noreply@grenke.pl",
        body_text="Decyzja do wniosku 173-025299 została wydana.",
        email_date_utc=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
        event_type=module.MAILBOX_EVENT_DECISION,
        application_no_raw="173-025299",
        application_no_normalized="173025299",
        attachments=[],
    )

    status, is_rejection, skipped = module.resolve_mailbox_business_status(
        mail_ctx=mail_ctx,
        current_business_status=module.WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
        decision_text="Decyzja do wniosku 173-025299 została wydana.",
    )

    assert status == module.WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER
    assert is_rejection is False
    assert skipped is True


def test_resolve_mailbox_business_status_does_not_downgrade_closed_not_realized() -> None:
    module = _load_sync_module()
    mail_ctx = module.MailContext(
        imap_id="91",
        message_id="<old-decision-closed@test>",
        subject="Decyzja do wniosku 173-025300",
        sender="noreply@grenke.pl",
        body_text="Decyzja do wniosku 173-025300 została wydana.",
        email_date_utc=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
        event_type=module.MAILBOX_EVENT_DECISION,
        application_no_raw="173-025300",
        application_no_normalized="173025300",
        attachments=[],
    )

    status, is_rejection, skipped = module.resolve_mailbox_business_status(
        mail_ctx=mail_ctx,
        current_business_status=module.WORKFLOW_BUSINESS_STATUS_CLOSED_NOT_REALIZED,
        decision_text="Decyzja do wniosku 173-025300 została wydana.",
    )

    assert status == module.WORKFLOW_BUSINESS_STATUS_CLOSED_NOT_REALIZED
    assert is_rejection is False
    assert skipped is True


def test_parse_args_fail_on_warnings_defaults_to_false(monkeypatch) -> None:
    module = _load_sync_module()
    monkeypatch.setattr(sys, "argv", ["contracts_mailbox_sync.py", "--limit", "10"])
    args = module.parse_args()
    assert args.limit == 10
    assert args.fail_on_warnings is False


def test_parse_args_fail_on_warnings_flag_sets_true(monkeypatch) -> None:
    module = _load_sync_module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["contracts_mailbox_sync.py", "--limit", "10", "--fail-on-warnings"],
    )
    args = module.parse_args()
    assert args.limit == 10
    assert args.fail_on_warnings is True


def test_truncate_for_log_shortens_over_limit() -> None:
    module = _load_sync_module()
    value = "x" * 50
    result = module._truncate_for_log(value, max_chars=20)
    assert result.endswith("...")
    assert len(result) == 20


def test_apply_mail_to_workflow_runs_binding_after_mailbox_approval(monkeypatch) -> None:
    module = _load_sync_module()

    form = SimpleNamespace(id=39, archive_due_at=None)
    workflow_case = SimpleNamespace(
        id=11,
        delivery_notes="",
        delivery_time_window=None,
        delivery_contact_name=None,
        delivery_contact_phone=None,
        updated_at=None,
        client_payload_snapshot={},
        firebird_client_id=2926,
    )
    device = SimpleNamespace(
        id=19,
        snapshot={},
        source_row=18410,
        source_type="firebird_magazyn_28",
        producer="NASHUATEC",
        model="IMC 3000",
        serial="3101R131123",
        ewidencja="KP/5139",
    )
    form_ctx = module.FormContext(
        form=form,
        payload={"company_name": "ACME"},
        workflow_case=workflow_case,
        application_no_normalized="173025167",
    )
    mail_ctx = module.MailContext(
        imap_id="100",
        message_id="<approval@test>",
        subject="Zgoda na realizację zamówienia",
        sender="system@grenke.pl",
        body_text="Umowa została podpisana.",
        email_date_utc=datetime(2026, 5, 22, 12, 22, 36, tzinfo=UTC),
        event_type="approval_for_delivery",
        application_no_raw="173-025167",
        application_no_normalized="173025167",
        attachments=[],
        proforma_no_raw="39/proforma/2026",
        proforma_no_normalized="39/proforma/2026",
    )

    captured: dict[str, object] = {}

    async def fake_set_form_workflow_business_status(*args, **kwargs):
        captured["status_source"] = kwargs["status_source"]
        workflow_case.business_status = kwargs["business_status"]
        return workflow_case

    async def fake_set_form_workflow_delivery(*args, **kwargs):
        workflow_case.delivery_notes = kwargs["delivery_notes"]
        return workflow_case

    async def fake_list_form_workflow_devices(*args, **kwargs):
        captured["workflow_case_id"] = kwargs["workflow_case_id"]
        return [device]

    async def fake_validate_no_active_workflow_device_duplicates(*args, **kwargs):
        captured["active_duplicate_check"] = True

    def fake_bind_devices_to_workflow_client(*, workflow_case, devices, actor_label):
        captured["binding_actor_label"] = actor_label
        captured["binding_device_ids"] = [item.id for item in devices]

        class _BindingItem:
            workflow_device_id = 19
            source_row = 18410
            source_type = "firebird_magazyn_28"
            ok = True
            message = "Powiązano urządzenie z klientem MS."
            producer = "NASHUATEC"
            model = "IMC 3000"
            serial = "3101R131123"
            machine_id = 7001
            previous_client_id = None
            current_client_id = 2926
            previous_ewidencja = None
            current_ewidencja = "KP/5139/GRENKE/1"
            ewidencja_changed = True

            def as_dict(self):
                return {
                    "workflow_device_id": self.workflow_device_id,
                    "ok": self.ok,
                    "message": self.message,
                    "machine_id": self.machine_id,
                    "current_client_id": self.current_client_id,
                    "current_ewidencja": self.current_ewidencja,
                }

        return ([_BindingItem()], [])

    def fake_validate_workflow_device_ownership(*, workflow_case, devices):
        captured["validated_workflow_case_id"] = workflow_case.id
        captured["validated_device_ids"] = [item.id for item in devices]

    async def fake_notify_binding_issues_to_admins(*args, **kwargs):
        raise AssertionError("Alert nie powinien być wysłany dla poprawnego wiązania.")

    async def fake_record_audit(*args, **kwargs):
        captured["audit_payload"] = kwargs["payload"]

    class _FakeSession:
        async def flush(self) -> None:
            captured["flushed"] = True

    monkeypatch.setattr(
        module,
        "set_form_workflow_business_status",
        fake_set_form_workflow_business_status,
    )
    monkeypatch.setattr(module, "set_form_workflow_delivery", fake_set_form_workflow_delivery)
    monkeypatch.setattr(module, "list_form_workflow_devices", fake_list_form_workflow_devices)
    monkeypatch.setattr(
        module,
        "validate_no_active_workflow_device_duplicates",
        fake_validate_no_active_workflow_device_duplicates,
    )
    monkeypatch.setattr(
        module,
        "validate_workflow_device_ownership",
        fake_validate_workflow_device_ownership,
    )
    monkeypatch.setattr(
        module, "bind_devices_to_workflow_client", fake_bind_devices_to_workflow_client
    )
    monkeypatch.setattr(
        module,
        "notify_binding_issues_to_admins",
        fake_notify_binding_issues_to_admins,
    )
    monkeypatch.setattr(module, "record_audit", fake_record_audit)
    monkeypatch.setattr(module, "persist_mail_attachments", lambda **kwargs: [])
    monkeypatch.setattr(module, "attach_mailbox_meta", lambda *args, **kwargs: None)

    result = asyncio.run(
        module.apply_mail_to_workflow(
            _FakeSession(),
            form_ctx=form_ctx,
            mail_ctx=mail_ctx,
            extracted_data={"application_no": "173-025167"},
        )
    )

    assert result["new_business_status"] == module.WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER
    assert captured["status_source"] == "mailbox"
    assert captured["validated_workflow_case_id"] == 11
    assert captured["active_duplicate_check"] is True
    assert captured["validated_device_ids"] == [19]
    assert captured["binding_actor_label"] == "Automat skrzynki GRENKE"
    assert captured["binding_device_ids"] == [19]
    assert captured["flushed"] is True
    assert device.snapshot["ms_binding_status"] == "ok"
    assert device.snapshot["ms_binding_message"] == "Powiązano urządzenie z klientem MS."
    assert device.snapshot["ms_id_maszyna"] == 7001
    assert device.snapshot["ms_id_klient"] == 2926
    assert device.snapshot["ewidencja"] == "KP/5139/GRENKE/1"
    assert workflow_case.delivery_notes == "Data podpisania umowy (mail): 2026-05-22"
    audit_payload = captured["audit_payload"]
    assert audit_payload["binding_items"][0]["workflow_device_id"] == 19
    assert audit_payload["binding_alert"] is None


def test_apply_mail_to_workflow_blocks_approval_for_foreign_device(monkeypatch) -> None:
    module = _load_sync_module()
    form = SimpleNamespace(id=40, archive_due_at=None)
    workflow_case = SimpleNamespace(
        id=12,
        business_status=module.WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE,
        delivery_notes="",
        delivery_time_window=None,
        delivery_contact_name=None,
        delivery_contact_phone=None,
        client_payload_snapshot={},
        firebird_client_id=2926,
    )
    device = SimpleNamespace(
        id=20,
        source_row=18411,
        snapshot={},
    )
    form_ctx = module.FormContext(
        form=form,
        payload={"company_name": "ACME"},
        workflow_case=workflow_case,
        application_no_normalized="173025168",
    )
    mail_ctx = module.MailContext(
        imap_id="101",
        message_id="<approval-conflict@test>",
        subject="Zgoda na realizację zamówienia",
        sender="system@grenke.pl",
        body_text="Umowa została podpisana.",
        email_date_utc=datetime(2026, 5, 22, 13, 0, 0, tzinfo=UTC),
        event_type="approval_for_delivery",
        application_no_raw="173-025168",
        application_no_normalized="173025168",
        attachments=[],
    )
    status_called = False

    async def fake_list_form_workflow_devices(*args, **kwargs):
        return [device]

    async def fake_set_form_workflow_business_status(*args, **kwargs):
        nonlocal status_called
        status_called = True
        return workflow_case

    async def fake_validate_no_active_workflow_device_duplicates(*args, **kwargs):
        return None

    ownership_error = module.WorkflowDeviceOwnershipConflict(
        [
            SimpleNamespace(
                source_row=18411,
                reason="Urządzenie jest przypisane do klienta Inny klient (ID 1001).",
            )
        ]
    )
    monkeypatch.setattr(module, "list_form_workflow_devices", fake_list_form_workflow_devices)
    monkeypatch.setattr(
        module,
        "validate_no_active_workflow_device_duplicates",
        fake_validate_no_active_workflow_device_duplicates,
    )
    monkeypatch.setattr(
        module,
        "validate_workflow_device_ownership",
        lambda **kwargs: (_ for _ in ()).throw(ownership_error),
    )
    monkeypatch.setattr(
        module,
        "set_form_workflow_business_status",
        fake_set_form_workflow_business_status,
    )

    try:
        asyncio.run(
            module.apply_mail_to_workflow(
                SimpleNamespace(),
                form_ctx=form_ctx,
                mail_ctx=mail_ctx,
                extracted_data={"application_no": "173-025168"},
            )
        )
    except module.WorkflowDeviceOwnershipConflict as exc:
        assert "Inny klient (ID 1001)" in str(exc)
    else:
        raise AssertionError("Konflikt właściciela powinien przerwać akceptację mailboxa.")

    assert status_called is False
    assert workflow_case.business_status == module.WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE


def test_transactional_mailbox_update_keeps_session_after_ownership_conflict(monkeypatch) -> None:
    module = _load_sync_module()
    ownership_error = module.WorkflowDeviceOwnershipConflict(
        [SimpleNamespace(source_row=18411, reason="Urządzenie ma innego właściciela.")]
    )
    calls = {"commit": 0, "savepoint": 0}

    class _Savepoint:
        async def __aenter__(self):
            calls["savepoint"] += 1
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class _FakeSession:
        def begin_nested(self):
            return _Savepoint()

        async def commit(self) -> None:
            calls["commit"] += 1

        async def rollback(self) -> None:
            raise AssertionError("Konflikt nie może wycofywać całej sesji mailboxa.")

    async def fake_apply_mail_to_workflow(*args, **kwargs):
        raise ownership_error

    monkeypatch.setattr(module, "apply_mail_to_workflow", fake_apply_mail_to_workflow)

    try:
        asyncio.run(
            module._apply_mail_to_workflow_transactionally(
                _FakeSession(),
                form_ctx=SimpleNamespace(),
                mail_ctx=SimpleNamespace(),
                extracted_data=None,
                pdf_text="",
                archived_contract_file=None,
            )
        )
    except module.WorkflowDeviceOwnershipConflict:
        pass
    else:
        raise AssertionError("Konflikt właściciela powinien zostać przekazany do pętli mailboxa.")

    assert calls == {"commit": 1, "savepoint": 1}
