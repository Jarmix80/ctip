"""Testy trwałego rejestru wiadomości umów."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.contracts_mailbox_ledger import serialize_mailbox_message


def test_message_serialization_does_not_expose_server_path() -> None:
    item = SimpleNamespace(
        id=7,
        message_id="<mail@test>",
        processing_status="historical_archived",
        classification="historical_application",
        event_type="decision_for_signature",
        application_no_raw="173-025234",
        application_no_normalized="17325234",
        proforma_no_raw=None,
        subject="Decyzja do wniosku 173-025234",
        sender="robot@example.com",
        body_text="Pełna treść wiadomości.",
        received_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        details="Archiwum",
        form_request_id=None,
        history_case_id=3,
        attachment_manifest=[
            {
                "original_name": "umowa.pdf",
                "content_type": "application/pdf",
                "size_bytes": 120,
                "sha256": "abc",
                "path": "D:/archiwum_dok/sekret/umowa.pdf",
            }
        ],
    )

    payload = serialize_mailbox_message(item, include_body=True)

    assert payload["body_text"] == "Pełna treść wiadomości."
    assert payload["attachments"][0]["file_name"] == "umowa.pdf"
    assert "path" not in payload["attachments"][0]
    assert "D:/archiwum_dok" not in str(payload)
