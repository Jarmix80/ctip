from app.models import Call, CallEvent, Contact, ContactDevice, SmsOut, SmsTemplate


def _is_timezone_aware(column) -> bool:
    return bool(getattr(column.type, "timezone", False))


def test_timestamp_columns_are_timezone_aware():
    columns = {
        "sms_out.created_at": SmsOut.__table__.c.created_at,
        "calls.started_at": Call.__table__.c.started_at,
        "calls.connected_at": Call.__table__.c.connected_at,
        "calls.ended_at": Call.__table__.c.ended_at,
        "call_events.ts": CallEvent.__table__.c.ts,
        "contact.created_at": Contact.__table__.c.created_at,
        "contact.updated_at": Contact.__table__.c.updated_at,
        "contact_device.created_at": ContactDevice.__table__.c.created_at,
        "sms_template.created_at": SmsTemplate.__table__.c.created_at,
        "sms_template.updated_at": SmsTemplate.__table__.c.updated_at,
    }
    for name, column in columns.items():
        assert _is_timezone_aware(column), f"Kolumna {name} musi mieć timezone=True"
