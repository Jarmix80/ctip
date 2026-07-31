from pathlib import Path

DEVICE_JS = Path("app/static/device/device.js")


def test_intake_uuid_is_bound_to_request_payload() -> None:
    source = DEVICE_JS.read_text(encoding="utf-8")

    assert "intakeRequestSignature: null" in source
    assert "const requestSignature = JSON.stringify(requestPayload);" in source
    assert "deviceState.intakeRequestSignature !== requestSignature" in source
    assert "deviceState.intakeRequestSignature = requestSignature;" in source
    assert "document_date: documentDate" in source
    assert "issue_date: issueDate" in source
    assert "payment_method: paymentMethod" in source
    assert "payment_due_date: paymentDueDate" in source


def test_intake_initializes_document_date_with_local_day() -> None:
    source = DEVICE_JS.read_text(encoding="utf-8")

    assert 'document.getElementById("device-intake-document-date")' in source
    assert "documentDateInput.value = today;" in source
    assert "issueDateInput.value = today;" in source
    assert 'paymentMethodInput.value = "Przelew";' in source
    assert "paymentDueDateInput.value = addDaysToIsoDate(issueDateInput.value, 14);" in source


def test_intake_reset_clears_uuid_and_request_signature() -> None:
    source = DEVICE_JS.read_text(encoding="utf-8")
    reset_start = source.index("function resetIntake()")
    reset_end = source.index("async function createSupplier()", reset_start)
    reset_source = source[reset_start:reset_end]

    assert "deviceState.intakeKey = null;" in reset_source
    assert "deviceState.intakeRequestSignature = null;" in reset_source
