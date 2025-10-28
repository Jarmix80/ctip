# Zmiany schematu bazy CTIP

## 2025-10-12
- Dodano moduł administracyjny: tabele `admin_user`, `admin_session`, `admin_setting`, `admin_audit_log` wraz z indeksami i sekwencjami.
- Ustanowiono uprawnienia `appuser` do nowych tabel i sekwencji.

## 2025-10-22
- Rozszerzono tabelę `admin_user` o kolumnę `mobile_phone` (numer telefonu używany do powiadomień SMS).

## 2025-10-25
- Dodano kolumnę `firebird_id` do tabeli `contact` oraz indeks `idx_contact_firebird_id`, aby umożliwić powiązanie wpisów książki adresowej z rekordami bazy Firebird.

## 2025-10-28
- Dodano ograniczenie `uq_ivr_map_ext` zapewniające unikalność numeru wewnętrznego w tabeli `ctip.ivr_map` oraz domyślną regułę IVR (cyfra `9` → wewnętrzny `500`) z komunikatem o instalacji aplikacji Ksero Partner.
