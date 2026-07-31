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


def test_intake_add_clears_section_fields() -> None:
    source = DEVICE_JS.read_text(encoding="utf-8")
    reset_start = source.index("function resetIntakeAddFields()")
    reset_end = source.index("function renumberIntakeKp()", reset_start)
    reset_source = source[reset_start:reset_end]
    add_start = source.index("function addIntakeItems()")
    add_end = source.index("async function submitIntake()", add_start)
    add_source = source[add_start:add_end]

    assert 'modelInput.value = "";' in reset_source
    assert 'quantityInput.value = "1";' in reset_source
    assert 'priceInput.value = "";' in reset_source
    assert "modelInput.focus();" in reset_source
    assert "resetIntakeAddFields();" in add_source


def test_intake_remove_renumbers_remaining_kp_values() -> None:
    source = DEVICE_JS.read_text(encoding="utf-8")
    renumber_start = source.index("function renumberIntakeKp()")
    renumber_end = source.index("async function createSupplier()", renumber_start)
    renumber_source = source[renumber_start:renumber_end]
    remove_marker = "deviceState.intakeItems.splice(Number(button.dataset.itemRemove), 1);"
    remove_start = source.index(remove_marker)
    remove_source = source[remove_start : remove_start + 220]

    assert "item.ewidencja = kpNumber(index);" in renumber_source
    assert "renumberIntakeKp();" in remove_source
    assert remove_source.index("renumberIntakeKp();") < remove_source.index("renderIntakeItems();")
