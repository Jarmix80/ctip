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

## 2026-03-16
- Uzupełniono schemat o tabelę `ctip.form_request`, która przechowuje wygenerowane i wypełnione formularze klienta wraz ze statusem, tokenem i zaszyfrowanym payloadem.
- Dodano tabele `ctip.form_workflow_case` oraz `ctip.form_workflow_device` do trwałego zapisu sprawy workflow w module `/flow`, w tym powiązania formularza z klientem Menadżera Serwisu i wybranymi urządzeniami po stronie CTIP.
- Dodano indeksy `idx_form_request_status_created`, `idx_form_request_created_by`, `idx_form_workflow_case_form_request` i `idx_form_workflow_device_case` dla odczytu list workflow oraz szczegółów sprawy.

## 2026-03-17
- Rozszerzono `ctip.form_workflow_case` o kolumnę `business_status` z kontrolowanym zestawem wartości (`DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `ZEROWKA`, `REJECTED`) do ręcznego prowadzenia etapu handlowego po proformie.
- Rozszerzono `ctip.form_workflow_device` o kolumny `price_net` i `price_gross`, aby zapisywać ręcznie ustalaną wycenę urządzeń niezależnie od ceny źródłowej z arkusza Google.

## 2026-03-18
- Rozszerzono `ctip.form_workflow_case` o pola harmonogramu dowozu: `delivery_date`, `delivery_time_window`, `delivery_contact_name`, `delivery_contact_phone`, `delivery_notes`.
- Dane harmonogramu dowozu są teraz zapisywane i edytowane wyłącznie po stronie CTIP/FLOW, bez przenoszenia tej informacji do wydruku proformy.

## 2026-04-09
- Rozszerzono `ctip.form_request` o kolumnę `ms_status`, która przechowuje czytelny wynik automatycznej lub ręcznej synchronizacji klienta z Menadżerem Serwisu po formularzu `SUBMITTED`.
- Rozszerzono `ctip.admin_user` o kolumnę `is_salesperson`, aby niezależnie od roli i sekcji oznaczać użytkowników jako handlowców na potrzeby grupowych powiadomień formularzy.

## 2026-04-10
- Rozszerzono `ctip.admin_user` o kolumny `firebird_app_user_id` i `firebird_app_user_login`, aby trwale mapować konto CTIP do wybranego użytkownika Menadżera Serwisu podczas tworzenia i edycji konta.
- Rozszerzono ograniczenie `form_workflow_device_source_type_check` o wariant `firebird_serial` oraz doprecyzowano, że identyfikacja urządzenia workflow opiera się na parze `source_type + source_row`, co zabezpiecza przyszły drugi adapter źródła urządzeń bez kolizji numerów rekordów.

## 2026-04-20
- Dodano tabelę `ctip.workflow_sheet_status_cache`, która przechowuje lokalny cache statusów urządzeń z arkusza Google (`source_key`, indeks, status, rezerwację i znaczniki CTIP) na potrzeby szybkiego otwierania modalu `/flow` bez odczytu Google Sheets przy każdym żądaniu.
- Dodano indeks `idx_workflow_sheet_status_cache_index_norm`, aby przyspieszyć fallbackowe dopasowanie po znormalizowanym indeksie urządzenia.

## 2026-04-24
- Rozszerzono `ctip.form_request` o kolumny `archive_bucket`, `archived_at` i `archive_due_at` do obsługi sekcji archiwum GenForm (`accepted`, `rejected`, `unfilled`) oraz terminów automatycznego przenoszenia.
- Rozszerzono `ctip.form_workflow_case` o statusy `WAITING_SIGNATURE`, `APPROVED_ORDER`, `REJECTED_GRENKE` oraz pola terminów i historii: `signature_deadline_at`, `resources_release_due_at`, `resources_released_at`, `status_changed_at`, `status_source`, `status_history`.
- Dodano indeksy `ix_form_request_archive_bucket`, `ix_form_request_archive_due_at` i `ix_form_workflow_case_resources_release_due_at` dla list archiwum i automatycznego zwalniania zasobów.

## 2026-05-20
- Rozszerzono ograniczenie `assistant_tool_call_log_tool_name_check` o narzędzia `workflow_devices_audit` oraz `email_send_report`, aby log narzędzi asystenta akceptował deterministyczny audyt urządzeń i raporty e-mail.
