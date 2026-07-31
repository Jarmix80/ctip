from pathlib import Path

DEVICE_JS = Path("app/static/device/device.js")


def test_intake_uuid_is_bound_to_request_payload() -> None:
    source = DEVICE_JS.read_text(encoding="utf-8")

    assert "intakeRequestSignature: null" in source
    assert "const requestSignature = JSON.stringify(requestPayload);" in source
    assert "deviceState.intakeRequestSignature !== requestSignature" in source
    assert "deviceState.intakeRequestSignature = requestSignature;" in source


def test_intake_reset_clears_uuid_and_request_signature() -> None:
    source = DEVICE_JS.read_text(encoding="utf-8")
    reset_start = source.index("function resetIntake()")
    reset_end = source.index("async function createSupplier()", reset_start)
    reset_source = source[reset_start:reset_end]

    assert "deviceState.intakeKey = null;" in reset_source
    assert "deviceState.intakeRequestSignature = null;" in reset_source
