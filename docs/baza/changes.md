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

## 2026-05-14
- Rozszerzono ograniczenie `admin_user_role_check` o rolę `serwisant`, przeznaczoną do dostępu do modułu obsługi dostaw.
- Dodano tabelę `ctip.delivery_case`, która przechowuje sprawy dostaw tworzone automatycznie z workflow GRENKE po statusie `APPROVED_ORDER` albo ręcznie z poziomu modułu `/delivery`.
- Dodano tabelę `ctip.delivery_case_device`, która przechowuje urządzenia przypisane do sprawy dostawy wraz z referencją do urządzenia workflow, numerem seryjnym, ewidencją i identyfikatorem `MASZYNA`.
- Dodano tabelę `ctip.grenke_contract_end`, która przechowuje kandydatów oraz potwierdzone daty końca umów GRENKE; dopiero status `confirmed` aktywuje przypomnienia.
- Dodano indeksy `idx_delivery_case_source_status`, `idx_delivery_case_delivery_date`, `idx_delivery_case_firebird_client`, `idx_delivery_case_device_case`, `idx_grenke_contract_end_status_date` i `idx_grenke_contract_end_pending_prefill` dla list dostaw oraz kalendarza końców umów.

## 2026-05-15
- Rozszerzono `ctip.delivery_case` o kolumnę `case_type`, aby jedna lista obsługiwała dostawy oraz odbiory urządzeń od klienta.
- Rozszerzono `ctip.delivery_case_device` o kolumnę `device_role`, rozróżniającą urządzenia dowożone i odbierane.
- Dodano tabele `ctip.delivery_case_task`, `ctip.delivery_case_file` oraz `ctip.delivery_document_template` do planowania prac serwisu, przechowywania plików spraw i rejestrowania wzorów dokumentów.
- Dodano indeksy `idx_delivery_case_type_status`, `idx_delivery_case_device_machine`, `idx_delivery_case_task_case`, `idx_delivery_case_task_due`, `idx_delivery_case_file_case` i `idx_delivery_document_template_active` dla widoku `/delivery`.

## 2026-05-20
- Rozszerzono ograniczenie `assistant_tool_call_log_tool_name_check` o narzędzia `workflow_devices_audit` oraz `email_send_report`, aby log narzędzi asystenta akceptował deterministyczny audyt urządzeń i raporty e-mail.

## 2026-06-10
- Rozszerzono workflow formularzy o status `RENTAL_WITHOUT_GRENKE` (`Wynajem bez GRENKE`) i nowy bucket archiwum `ksero_partner`.
- `ctip.form_request` otrzymał rozszerzone ograniczenie `form_request_archive_bucket_check` (`accepted|rejected|unfilled|ksero_partner`), a `ctip.form_workflow_case` ograniczenie `form_workflow_case_business_status_check` uwzględnia nowy status.
- `admin_contracts` i interfejs `genform` obsługują teraz scope archiwum `ksero_partner` pod etykietą „Umowy Ksero-Partner”.
