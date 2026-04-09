"""Wspolna konfiguracja obslugi formularza i powiadomien."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.settings_store import build_store

DEFAULT_INVITE_SMS_TEMPLATE = (
    "Ksero Partner: prosimy uzupelnic formularz do obslugi zgloszenia: "
    "{form_url} Link wazny do {expires_at}."
)
DEFAULT_INVITE_EMAIL_SUBJECT = "Prosba o uzupelnienie formularza serwisowego"
DEFAULT_INVITE_EMAIL_BODY = (
    "Dzien dobry {customer_name},\n\n"
    "przesylamy formularz potrzebny do dalszej obslugi zgloszenia serwisowego.\n\n"
    "Link do formularza:\n{form_url}\n\n"
    "Link jest wazny do {expires_at}.\n"
    "Po zapisaniu formularza dane trafia bezposrednio do obslugi w Ksero Partner.\n\n"
    "W razie pytan prosimy o kontakt z naszym biurem.\n\n"
    "Pozdrawiamy,\n{sender_name}"
)
DEFAULT_SUBMISSION_EMAIL_SUBJECT = "Potwierdzenie przyjecia formularza serwisowego"
DEFAULT_SUBMISSION_EMAIL_BODY = (
    "Dzien dobry,\n\n"
    "potwierdzamy poprawne przyjecie formularza dla firmy {company_name}.\n"
    "Dane zostaly zapisane i przekazane do dalszej obslugi.\n\n"
    "Pozdrawiamy,\n{sender_name}"
)
DEFAULT_OWNER_SMS_TEMPLATE = (
    "CTIP: formularz klienta {company_name} ({customer_name}) zostal zapisany."
)

INVITE_SMS_PLACEHOLDERS = frozenset({"customer_name", "expires_at", "form_url"})
INVITE_EMAIL_SUBJECT_PLACEHOLDERS = frozenset({"customer_name", "expires_at"})
INVITE_EMAIL_BODY_PLACEHOLDERS = frozenset(
    {"customer_name", "expires_at", "form_url", "sender_name"}
)
SUBMISSION_EMAIL_SUBJECT_PLACEHOLDERS = frozenset({"company_name", "customer_name"})
SUBMISSION_EMAIL_BODY_PLACEHOLDERS = frozenset({"company_name", "customer_name", "sender_name"})
OWNER_SMS_PLACEHOLDERS = frozenset({"company_name", "customer_name"})

FORM_TEMPLATE_RULES = {
    "invite_sms_template": INVITE_SMS_PLACEHOLDERS,
    "invite_email_subject": INVITE_EMAIL_SUBJECT_PLACEHOLDERS,
    "invite_email_body": INVITE_EMAIL_BODY_PLACEHOLDERS,
    "submission_email_subject": SUBMISSION_EMAIL_SUBJECT_PLACEHOLDERS,
    "submission_email_body": SUBMISSION_EMAIL_BODY_PLACEHOLDERS,
    "owner_sms_template": OWNER_SMS_PLACEHOLDERS,
}

settings_store = build_store(settings.admin_secret_key)


@dataclass(frozen=True, slots=True)
class FormHandlingConfig:
    """Pelna konfiguracja obslugi formularza."""

    public_base_url: str
    invite_sms_template: str
    invite_email_subject: str
    invite_email_body: str
    submission_email_subject: str
    submission_email_body: str
    owner_sms_template: str


def default_public_base_url() -> str:
    """Wylicza domyslny adres publiczny, gdy brak wpisu w panelu."""
    configured = (settings.form_public_base_url or "").strip()
    if configured:
        return configured.rstrip("/")

    panel_url = (settings.admin_panel_url or "").strip()
    if panel_url:
        parsed = urlparse(panel_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    return "http://localhost:8000"


def normalize_public_base_url(value: str | None) -> str:
    """Normalizuje adres bazowy formularza."""
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError("Adres publiczny formularza nie moze byc pusty.")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Adres publiczny formularza musi byc pelnym adresem HTTP lub HTTPS.")
    return normalized.rstrip("/")


async def load_form_handling_config(session: AsyncSession) -> FormHandlingConfig:
    """Zwraca aktywna konfiguracje obslugi formularza."""
    stored = await settings_store.get_namespace(session, "form_handling")
    return FormHandlingConfig(
        public_base_url=normalize_public_base_url(
            stored.get("public_base_url") or default_public_base_url()
        ),
        invite_sms_template=stored.get("invite_sms_template") or DEFAULT_INVITE_SMS_TEMPLATE,
        invite_email_subject=stored.get("invite_email_subject") or DEFAULT_INVITE_EMAIL_SUBJECT,
        invite_email_body=stored.get("invite_email_body") or DEFAULT_INVITE_EMAIL_BODY,
        submission_email_subject=stored.get("submission_email_subject")
        or DEFAULT_SUBMISSION_EMAIL_SUBJECT,
        submission_email_body=stored.get("submission_email_body") or DEFAULT_SUBMISSION_EMAIL_BODY,
        owner_sms_template=stored.get("owner_sms_template") or DEFAULT_OWNER_SMS_TEMPLATE,
    )


def validate_template_placeholders(field_name: str, template: str) -> None:
    """Sprawdza, czy szablon uzywa tylko dozwolonych zmiennych."""
    allowed = FORM_TEMPLATE_RULES[field_name]
    used: set[str] = set()
    formatter = Formatter()
    for _, field_name_raw, _, _ in formatter.parse(template):
        if field_name_raw is None:
            continue
        if not field_name_raw:
            raise ValueError("Wykryto pusty placeholder w szablonie.")
        if field_name_raw != field_name_raw.strip():
            raise ValueError("Nazwy placeholderow nie moga zawierac spacji.")
        if field_name_raw not in allowed:
            allowed_list = ", ".join(sorted(allowed))
            raise ValueError(
                f"Szablon '{field_name}' zawiera nieobslugiwana zmienna '{field_name_raw}'. "
                f"Dozwolone zmienne: {allowed_list}."
            )
        used.add(field_name_raw)
    if not template.strip():
        raise ValueError(f"Szablon '{field_name}' nie moze byc pusty.")


def validate_form_handling_templates(values: Mapping[str, str]) -> None:
    """Waliduje komplet szablonow edytowanych w panelu."""
    for field_name in FORM_TEMPLATE_RULES:
        validate_template_placeholders(field_name, values[field_name])


def render_template(template: str, context: Mapping[str, object]) -> str:
    """Renderuje szablon komunikatu za pomoca podstawowych placeholderow."""
    normalized_context = {
        key: "" if value is None else str(value) for key, value in context.items()
    }
    return template.format_map(normalized_context)


__all__ = [
    "DEFAULT_INVITE_EMAIL_BODY",
    "DEFAULT_INVITE_EMAIL_SUBJECT",
    "DEFAULT_INVITE_SMS_TEMPLATE",
    "DEFAULT_OWNER_SMS_TEMPLATE",
    "DEFAULT_SUBMISSION_EMAIL_BODY",
    "DEFAULT_SUBMISSION_EMAIL_SUBJECT",
    "FORM_TEMPLATE_RULES",
    "FormHandlingConfig",
    "default_public_base_url",
    "load_form_handling_config",
    "normalize_public_base_url",
    "render_template",
    "settings_store",
    "validate_form_handling_templates",
    "validate_template_placeholders",
]
