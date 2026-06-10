"""Testy logiki automatyzacji mailbox dla modułu umów."""

from __future__ import annotations

from app.services.contracts_mailbox import (
    MAILBOX_EVENT_APPROVAL,
    MAILBOX_EVENT_DECISION,
    build_pdf_password_candidates,
    classify_mail_payload,
    classify_mail_subject,
    detect_rejection_decision,
    extract_application_number,
    extract_data_from_contract_text,
    extract_proforma_number,
    normalize_application_number,
    normalize_proforma_number,
    score_form_match,
)


def test_classify_mail_subject_for_decision() -> None:
    subject = (
        "[WARNING :  MESSAGE ENCRYPTED] Decyzja do wniosku 173-025167 aktualizacja warunków umowy"
    )
    assert classify_mail_subject(subject) == MAILBOX_EVENT_DECISION


def test_classify_mail_subject_for_approval() -> None:
    subject = "Zgoda na realizację zamówienia do wniosku nr: 173-25084"
    assert classify_mail_subject(subject) == MAILBOX_EVENT_APPROVAL


def test_classify_mail_payload_from_body_when_subject_not_matching() -> None:
    assert (
        classify_mail_payload(
            subject="Potwierdzenie dostarczenia dokumentu",
            body="Drodzy Państwo, decyzja do wniosku 173-025167 została wydana.",
        )
        == MAILBOX_EVENT_DECISION
    )


def test_classify_mail_payload_approval_from_body() -> None:
    assert (
        classify_mail_payload(
            subject="Fwd: ważne informacje",
            body=("Przesyłamy dokumenty. Zgoda na realizacje zamówienia do wniosku nr: 173-25084."),
        )
        == MAILBOX_EVENT_APPROVAL
    )


def test_classify_mail_payload_approval_body_overrides_decision_subject() -> None:
    assert (
        classify_mail_payload(
            subject="Decyzja do wniosku 173-025299",
            body="Dzień dobry, zgoda na realizację zamówienia do wniosku nr: 173-025299.",
        )
        == MAILBOX_EVENT_APPROVAL
    )


def test_extract_application_number_and_normalization() -> None:
    parsed = extract_application_number("Decyzja do wniosku 173-025167")
    assert parsed is not None
    assert parsed.raw == "173-025167"
    assert parsed.normalized == "173025167"
    assert normalize_application_number(parsed.raw) == "173025167"


def test_extract_proforma_number_and_normalization() -> None:
    parsed = extract_proforma_number(
        "RE: Decyzja do wniosku 173-025203 / Faktura Pro Forma nr: 1 0 / pro forma / 2 0 2 6"
    )
    assert parsed is not None
    assert parsed.raw == "10/proforma/2026"
    assert parsed.normalized == "10/proforma/2026"
    assert normalize_proforma_number(" 10 / PRO FORMA / 2026 ") == "10/proforma/2026"


def test_extract_proforma_number_with_spaced_word() -> None:
    parsed = extract_proforma_number(
        "Zgoda na realizacje zamówienia do wniosku nr: 173-025203  Faktura Pro Forma nr: 1 0 / p r o f o r m a / 2 0 2 6"
    )
    assert parsed is not None
    assert parsed.raw == "10/proforma/2026"
    assert parsed.normalized == "10/proforma/2026"


def test_build_pdf_password_candidates_uses_pesel_and_initials() -> None:
    payload = {
        "representatives": [
            {
                "first_name": "Jacek",
                "last_name": "Kuś",
                "pesel": "67020505791",
            }
        ]
    }
    candidates = build_pdf_password_candidates(payload)
    assert candidates == ["05791JK$"]


def test_score_form_match_detects_name_and_nip() -> None:
    body = (
        "Pozytywna decyzja dla KANCELARIA RADCY PRAWNEGO JACEK KUŚ "
        "NIP: 9720314010 Reprezentant: JACEK KUŚ"
    )
    payload = {
        "company_name": "KANCELARIA RADCY PRAWNEGO JACEK KUŚ",
        "company_nip": "PL9720314010",
        "representatives": [{"first_name": "Jacek", "last_name": "Kuś", "pesel": "67020505791"}],
    }
    assert score_form_match(body, payload) > 0


def test_extract_data_from_contract_text_returns_key_fields() -> None:
    text = (
        "Przesyłamy zgodę na realizację Zamówienia do WNIOSKU NR: 173-25084. "
        "Wnioskodawca: ACE GLASS M. WOŹNIAK SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ "
        "NIP: 9720314010 Reprezentant: JACEK KUŚ PESEL 67020505791"
    )
    extracted = extract_data_from_contract_text(text)
    assert "173-25084" in extracted["application_numbers"]
    assert "9720314010" in {item.replace("PL", "") for item in extracted["nips"]}
    assert "67020505791" in extracted["pesels"]
    assert extracted["company"] is not None
    assert extracted["representative"] is not None


def test_extract_data_from_contract_text_ignores_invalid_krs_like_nip() -> None:
    text = (
        "FINANSUJĄCY GRENKE KRS 0000175740 REGON 634495137 "
        "Najemca KANCELARIA RADCY PRAWNEGO JACEK KUŚ PL9720314010"
    )
    extracted = extract_data_from_contract_text(text)
    assert extracted["nips"] == ["PL9720314010"]


def test_detect_rejection_decision_handles_phrase_nie_wyraza_zgody() -> None:
    body = (
        "Dzień dobry, niestety w tym przypadku Grenkeleasing sp. z o.o. "
        "nie wyraża zgody na zawarcie umowy przez GENERAL ELECTRICALS KRZYSZTOF KASSIN."
    )
    assert detect_rejection_decision(body) is True


def test_detect_rejection_decision_does_not_mark_positive_decision() -> None:
    body = (
        "Dzień dobry, dziękujemy za zainteresowanie. "
        "Wyrażamy zgodę na realizację zamówienia do wniosku 173-25299."
    )
    assert detect_rejection_decision(body) is False
