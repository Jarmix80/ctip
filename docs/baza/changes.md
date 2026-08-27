# Zmiany schematu bazy CTIP

## 2026-08-28

- Migracja `a7c4e2f9b1d3` dodaje trwały, idempotentny rejestr `ctip.contracts_mailbox_message` oparty na unikalnym `Message-ID`.
- Dodano tabelę `ctip.contracts_mailbox_history_case`, która grupuje korespondencję historyczną po kanonicznym numerze wniosku bez tworzenia sztucznych rekordów `form_request`.
- Rejestr przechowuje pełną treść tekstową, klasyfikację, stan końcowy, powiązanie z formularzem lub sprawą historyczną oraz manifest załączników. API nie zwraca lokalnych ścieżek plików.
- Kontrolowane stany wiadomości to `pending`, `linked_form`, `historical_archived`, `ignored`, `manual_hold` i `error`; stany końcowe nie są ponawiane przez scheduler.

## 2026-08-27

- Migracja produkcyjna `f9a0b1c2d3e4` tworzy od zera finalny, addytywny schemat modułu Shipping po rewizji `d8f1a2b3c4e5` używanej przez bieżące wydanie produkcyjne.
- Dodano tabele `shipping_address`, `shipping_consumable_compatibility`, `shipping_case`, `shipping_item`, `shipping_day_close`, `shipping_shipment` i `shipping_event` wraz z kluczami obcymi, ograniczeniami idempotencji i indeksami operacyjnymi.
- Katalog zgodności przechowuje relacje wiele-do-wielu między `MODEL.ID_MODEL` i `MAGAZYN.ID_MAGAZYN_TABLE`, niezależne stany decyzji, poziom pewności, dowody JSON i historię przeglądu.
- Sprawa wysyłkowa przechowuje snapshot adresu oraz lokalizacji, decyzję o FV, zaakceptowane ceny, zgodę na kontrolowany stan ujemny i identyfikatory dokumentów RW, WZ oraz FV utworzonych w Firebirdzie.
- Archiwum zapisuje operatora zamknięcia, niezmienny snapshot procesu i znormalizowaną treść wyszukiwarki. Indeks GIN korzysta z rozszerzenia `pg_trgm`.
- Migracja wymaga pustego celu dla tabel Shipping i przerywa działanie po wykryciu lokalnego prototypu. Dane testowe, sugestie i mapowania z `ctip_test` nie są przenoszone na produkcję.

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

## 2026-06-10
- Rozszerzono workflow formularzy o status `RENTAL_WITHOUT_GRENKE` (`Wynajem bez GRENKE`) i nowy bucket archiwum `ksero_partner`.
- `ctip.form_request` otrzymał rozszerzone ograniczenie `form_request_archive_bucket_check` (`accepted|rejected|unfilled|ksero_partner`), a `ctip.form_workflow_case` ograniczenie `form_workflow_case_business_status_check` uwzględnia nowy status.
- `admin_contracts` i interfejs `genform` obsługują teraz scope archiwum `ksero_partner` pod etykietą „Umowy Ksero-Partner”.

## 2026-06-11
- Rozszerzono workflow formularzy o status `CLOSED_NOT_REALIZED` (`Zamknięta bez realizacji`) i bucket archiwum `closed_other`.
- `ctip.form_request` otrzymał rozszerzone ograniczenie `form_request_archive_bucket_check` (`accepted|rejected|unfilled|ksero_partner|closed_other`), a `ctip.form_workflow_case` ograniczenie `form_workflow_case_business_status_check` uwzględnia nowy status.
- Status `CLOSED_NOT_REALIZED` kończy sprawę bez realizacji, uruchamia pełne zwolnienie zasobów i trafia po archiwizacji do menu „Odrzucone inne”.

## 2026-07-23
- Dodano idempotentny rejestr przyjęć urządzeń: `ctip.device_intake_operation` i `ctip.device_inventory_unit`. Rejestr przechowuje mapowania PZ, `ZAKPOZYCJA`, `MAGAZYN`, `MASZYNA`, serialu i numeru KP.
- Dodano niemodyfikowalną historię działań `ctip.device_inventory_event` z indeksem `(unit_id, created_at DESC)` oraz terminowe rezerwacje ręczne `ctip.device_manual_reservation` z unikalnością jednej aktywnej rezerwacji na egzemplarz.
- Dodano kolejkę niezawodnej synchronizacji Google Sheets `ctip.device_sheet_outbox` z licznikiem prób, harmonogramem ponowienia i stanem błędu.
- Rozszerzono `ctip.workflow_sheet_status_cache` o producenta, model, serial, uwagę, cenę, osobny status i termin rezerwacji oraz identyfikator `MASZYNA`.
- Rozszerzono `ctip.workflow_sheet_status_cache` o liczniki `counter_bw` i `counter_color`, aby tabela magazynu korzystała z lokalnego cache zamiast odczytywać Google Sheets przy każdym otwarciu.

## 2026-07-24
- Dodano tabelę `ctip.device_audit_run` przechowującą status, etap, postęp, podsumowanie i historię ręcznych audytów urządzeń.
- Dodano tabelę `ctip.device_audit_item` z wynikiem porównania aktywnego arkusza `Urzadzenia_magazyn`, dostępnego magazynu Firebird nr 28, kartotek `MASZYNA` i rejestru CTIP.
- Audyt jest operacją tylko do odczytu dla źródeł zewnętrznych, zachowuje 20 ostatnich przebiegów i klasyfikuje wyniki według priorytetu: duplikat, rozbieżność, braki, poprawny.
- Dodano tabelę `ctip.device_counter_reading` przechowującą historię liczników B/W, koloru i skanu wraz z datą odczytu, źródłem oraz informacją, czy odczyt zaktualizował stan bieżący.
- Rozszerzono `ctip.workflow_sheet_status_cache` o `counter_scan` oraz kolejkę arkusza o operacje `update_counters` i `delete_device`.
- Rozszerzono `ctip.device_intake_operation` i `ctip.device_inventory_unit` o trwały stan wycofania, datę, operatora, uzasadnienie i zapis podglądu skutków operacji PZ.
- Rozszerzono `ctip.admin_user` o osobne uprawnienie `can_withdraw_device_pz`; administrator zachowuje prawo niezależnie od wartości pola.
- Rozszerzono `ctip.admin_user` o kontrolowaną preferencję `device_theme` (`blue`, `graphite`, `mint`), dzięki czemu kolorystyka modułu `/device` jest przypisana do konta użytkownika.

## 2026-07-31
- Ograniczono unikalność znormalizowanego serialu i numeru KP w `ctip.device_inventory_unit` do wpisów o statusie `active`. Wycofana historia PZ nie blokuje dzięki temu ponownego przyjęcia tego samego fizycznego urządzenia, a równoległe aktywne duplikaty nadal są blokowane.
