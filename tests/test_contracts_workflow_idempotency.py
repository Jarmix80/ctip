"""Testy odporności workflow na równoległe zatwierdzanie formularza."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from app.services import contracts_workflow, form_generator


def test_get_or_create_workflow_case_recovers_after_unique_conflict(monkeypatch) -> None:
    """Konflikt równoległego INSERT-u zwraca sprawę utworzoną przez drugi proces."""
    existing = SimpleNamespace(
        client_payload_snapshot=None,
        updated_at=None,
        updated_by=None,
    )
    lookups = 0

    async def fake_get_form_workflow_case(session, *, form_request_id):
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else existing

    class _Savepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class _FakeSession:
        def begin_nested(self):
            return _Savepoint()

        def add(self, item) -> None:
            self.item = item

        async def flush(self) -> None:
            raise IntegrityError("INSERT", {}, RuntimeError("duplicate key"))

    monkeypatch.setattr(
        contracts_workflow,
        "get_form_workflow_case",
        fake_get_form_workflow_case,
    )
    payload = {"company_name": "Klient testowy"}

    result = asyncio.run(
        contracts_workflow.get_or_create_form_workflow_case(
            _FakeSession(),
            form=SimpleNamespace(id=68),
            user_id=17,
            payload_snapshot=payload,
        )
    )

    assert result is existing
    assert lookups == 2
    assert existing.client_payload_snapshot == payload
    assert existing.updated_by == 17


def test_get_form_by_token_uses_row_lock_for_submission() -> None:
    """Tryb zapisu dodaje blokadę rekordu chroniącą przed podwójnym POST-em."""
    form = SimpleNamespace(
        status="GENERATED",
        token_expires_at=datetime.now(UTC) + timedelta(days=1),
        updated_at=datetime.now(UTC),
    )

    class _Result:
        def scalar_one_or_none(self):
            return form

    class _FakeSession:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return _Result()

    session = _FakeSession()
    result = asyncio.run(
        form_generator.get_form_by_token(session, "token-testowy", for_update=True)
    )

    assert result is form
    assert session.statement._for_update_arg is not None
