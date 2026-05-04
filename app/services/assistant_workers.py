"""Profile wirtualnych pracowników AI dostępnych w module asystenta."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_WORKER_KEY = "ksero_partner_analyst"
WORKER_KEYS = (
    "ksero_partner_analyst",
    "opiekun_klienta",
    "diagnosta_bazy_ms",
)


@dataclass(frozen=True, slots=True)
class AssistantWorkerProfile:
    """Definicja profilu pracownika AI."""

    key: str
    name: str
    description: str
    prompt_addendum: str


_WORKERS: dict[str, AssistantWorkerProfile] = {
    "ksero_partner_analyst": AssistantWorkerProfile(
        key="ksero_partner_analyst",
        name="Analityk Ksero-Partner",
        description=(
            "Domyślny profil do analiz wydruków, urządzeń, modeli i klientów na bazie Firebird MS "
            "oraz danych pomocniczych z Google Sheets."
        ),
        prompt_addendum=(
            "Twoja rola: Analityk Ksero-Partner. "
            "Priorytet: szybkie odpowiedzi biznesowe, konkretne liczby, krótkie podsumowanie na końcu."
        ),
    ),
    "opiekun_klienta": AssistantWorkerProfile(
        key="opiekun_klienta",
        name="Opiekun Klienta",
        description=(
            "Profil wspierający obsługę handlową i serwisową: tłumaczy wyniki prostym językiem, "
            "proponuje kolejne kroki i podkreśla ryzyka dla klienta."
        ),
        prompt_addendum=(
            "Twoja rola: Opiekun Klienta. "
            "Tłumacz dane zrozumiale dla handlowca i serwisu, wskazuj praktyczne następne kroki."
        ),
    ),
    "diagnosta_bazy_ms": AssistantWorkerProfile(
        key="diagnosta_bazy_ms",
        name="Diagnosta Bazy MS",
        description=(
            "Profil techniczny do pracy ze strukturą Firebird MS, relacjami tabel i walidacją źródeł "
            "wiedzy z lokalnego indeksu repozytorium."
        ),
        prompt_addendum=(
            "Twoja rola: Diagnosta Bazy MS. "
            "Najpierw ustal strukturę danych, pola i relacje, potem prezentuj odpowiedź biznesową."
        ),
    ),
}


def list_worker_profiles() -> list[AssistantWorkerProfile]:
    """Zwraca listę dostępnych pracowników AI w stałej kolejności."""
    return [_WORKERS[key] for key in WORKER_KEYS]


def get_worker_profile(worker_key: str | None) -> AssistantWorkerProfile:
    """Zwraca profil pracownika AI; dla brakującego/nieznanego klucza wybiera domyślny."""
    key = (worker_key or "").strip()
    return _WORKERS.get(key, _WORKERS[DEFAULT_WORKER_KEY])


__all__ = [
    "AssistantWorkerProfile",
    "DEFAULT_WORKER_KEY",
    "WORKER_KEYS",
    "get_worker_profile",
    "list_worker_profiles",
]
