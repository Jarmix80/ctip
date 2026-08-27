![CI](https://github.com/Jarmix80/ctip/actions/workflows/ci.yml/badge.svg)

# CTIP – kolektor zdarzeń CTI i dystrybucja alertów SMS

CTIP agreguje zdarzenia telefoniczne emitowane przez centralę Slican, zapisuje je w bazie PostgreSQL oraz inicjuje wysyłkę powiadomień SMS na podstawie mapowania IVR. Projekt przeznaczony jest do wdrożeń on-premise, w których administrator musi zapewnić niezawodny odbiór strumienia CTIP i dalsze przetwarzanie danych.

## Dokumenty wdrożeniowe
- Produkcyjny runbook dla zmian GENFORM/FLOW (backup, migracje, konfiguracja skrzynki i arkusza, rollback): `docs/instal/wdrozenie_genform_flow_prod_2026-04-29.md`.
- Runbook wyrownania konfiguracji Windows TEST/PROD do modelu `.env` (backup `D:\backup_temp`, bramki potwierdzen, wdrozenie i rollback): `docs/instal/windows_test_prod_env_runbook_2026-05-22.md`.
- Runbook zabezpieczenia API, logicznego backupu PostgreSQL do Office 365 i monitorowania publicznego TLS: `docs/instal/bezpieczenstwo_backup_tls.md`.
- Runbook zweryfikowanych kopii Firebird/SQL Optima i retencji czasowej 21/14 dni: `docs/instal/backup_firebird_optima_retencja.md`.
- Pomocniczy skrypt operatorski (Windows, bez `Read-Host`) do wykonania kroku Google Sheets + mailbox dry-run po wdrozeniu: `inbox/krok9_10_google_sheets_mailbox_noninteractive.ps1`.

## Dokumentacja operacyjna (poza zakresem CTIP)
- Raport z optymalizacji wydajnosci testowej bazy Menadzer Serwisu (Firebird), obejmujacy benchmarki zapytan i dodane indeksy: `docs/firebird/przyspieszenie_bazy_bazams_test_2026-05-14.md`.
- Audyt wydajności produkcyjnej bazy Menadżer Serwisu w trybie tylko do odczytu (`192.168.0.8`, `BAZAMS.FDB`) z analizą długich transakcji, planów zapytań i kandydatów indeksów do testów na kopii: `docs/firebird/audyt_wydajnosci_bazams_prod_2026-06-30.md`.

## Najważniejsze komponenty
- `collector_full.py` – produkcyjny kolektor CTIP: łączy się z centralą, koreluje zdarzenia, persystuje rekordy w schemacie `ctip` oraz rejestruje zadania SMS.
- `collector_service.py` – wrapper w formie usługi Windows utrzymujący działanie `collector_full.py` i restartujący proces po awarii; automatycznie dopina ścieżki `pywin32` oraz dodaje katalog `pywin32_system32` do ścieżki DLL (start jako `pythonservice.exe`), wymagane wcześniejsze `pywin32_postinstall`.
- `sms_sender.py` – pętla pollingująca kolejkę `sms_out`; implementacja `send_sms` wymaga podpięcia właściwego operatora.
- `ctip_sniff.py` – narzędzie diagnostyczne zapisujące surowy strumień CTIP do pliku w celu analizy protokołu.
- `conect_sli.py` – lekki monitor CTIP uruchamiany w trybie interaktywnym, wykonujący `aWHO`/`aLOGA` i wypisujący zdarzenia na STDOUT.
- `collector_fullwork.py` oraz katalog `docs/` – materiały warsztatowe i referencyjne, niezalecane do użycia w produkcji.
- `app/api/routes/admin_*` – moduł API panelu administratora (logowanie, konfiguracja PostgreSQL/Firebird/CTIP/SerwerSMS/SMTP/obsługi formularza, audyt zmian oraz health-checki `/admin/status/summary`, `/admin/status/database`, `/admin/status/ctip`, `/admin/status/sms`).
- `app/web/admin_ui.py` + `app/templates/admin/` – interfejs administracyjny w technologii HTMX + Alpine (adres `/admin`).
- `app/api/routes/admin_contacts.py` + `app/services/admin_contacts.py` – warstwa API i logika książki adresowej z obsługą pola `firebird_id`.
- `app/api/routes/admin_forms.py` + `app/services/form_generator.py` – generator jednorazowych formularzy klienta (token haszowany, zapis danych zaszyfrowanych, automatyczna weryfikacja klienta po NIP w Menadżerze Serwisu po statusie `SUBMITTED`, z użyciem efektywnej konfiguracji Firebird odczytywanej z `.env` i prezentowanej w panelu administratora w trybie tylko do odczytu).
- `app/web/genform_ui.py` + `app/templates/genform/` – osobny flow handlowca pod adresem `/genform` (logowanie, generowanie linku, tabela formularzy z kolumnami operacyjnymi FLOW, osobny modal wyboru urządzeń i osobny modal proformy oraz dezaktywacja formularza z przywracaniem rezerwacji arkusza).
- `app/web/flow_ui.py` + `app/templates/flow/` + `app/static/flow/` – widok `/flow` z bocznym menu dla sekcji „Obsługa umów”, „Obsługa urządzeń” i „Harmonogram dowozów”, nagłówkiem użytkownika, podglądem danych formularza z kopiowaniem pojedynczych pól, osobnym modalem workflow do prowadzenia sprawy klienta i wyboru urządzeń po stronie CTIP oraz stronami wizualizacji proformy `/flow/proforma-wizualizacja` i `/flow/proforma-wizualizacja1`.
- `app/web/delivery_ui.py` + `app/templates/delivery/` + `app/static/delivery/` + `app/api/routes/admin_delivery.py` + `app/services/delivery.py` – moduł `/delivery` („Obsługa dostaw”): lista dostaw z FLOW GRENKE po statusie `APPROVED_ORDER`, ręczne zakładanie dostaw spoza GRENKE, powiązanie z klientem Firebird oraz kalendarz końców umów GRENKE wymagający potwierdzenia operatora i wysyłający przypomnienia aktywnym handlowcom.
- `app/web/shipping_ui.py` + `app/templates/shipping/` + `app/static/shipping/` + `app/api/routes/admin_shipping.py` + `app/services/shipping_*` – moduł `/shipping`: realizacja wysyłek części, wyróżniona pełna treść zlecenia, jawne źródła adresu, wybór aktywnej osoby kontaktowej z Firebirda, automatyczne dane autora zlecenia z aplikacji mobilnej oraz zachowawczy parser telefonu z `ZLECENIE.PROBLEM`. Numer rozpoznany w treści zastępuje automatyczny telefon z aplikacji lub zlecenia, jest jawnie oznaczony w formularzu i pozostaje edytowalny. Kolejka zawiera wyłącznie niezakończone zlecenia `TYP_US=8` bez rzeczywistego technika terenowego; puste `ZLECENIE.TECHNIK` i `TECHNIK2` oraz dokładna wartość techniczna `Wysyłka Wysyłka` są dopuszczone, natomiast każdy inny technik wyklucza wysyłkę magazynową. Moduł obsługuje pojedyncze i zbiorcze generowanie etykiet PDF A4, osobny wydruk listy części na zwykły papier, osobny natywny arkusz etykiet DPD A4 oraz idempotencję działającą także przez HTTP w lokalnej sieci. Oficjalny adapter DPD Services REST używa Basic Auth, nagłówka `X-DPD-FID`, osobnego `payerFID` i sekwencji `generatePackagesNumbers` → `generateSpedLabels`; tryb Demo zastępuje odbiorcę adresem Ksero-Partner. Dla co najmniej dwóch gotowych zleceń tej samej firmy na identyczny adres operator może zaznaczyć wykrytą grupę i utworzyć jedną fizyczną paczkę z jedną etykietą oraz wspólnym numerem DPD; każde zlecenie zachowuje własne części, ceny i osobny dokument RW, WZ lub FV, a zamknięcie jednego z nich finalizuje całą powiązaną grupę. Kolejka używa polskich nazw i kolorów etapów oraz domyślnie układa od góry sprawy do weryfikacji, gotowe do etykiety i z wygenerowaną etykietą, pozostawiając klientów z nieopłaconymi FV na samym dole; operator może przełączyć sortowanie na najstarsze, najnowsze albo klienta A–Z. Kolejka rozróżnia także zlecenia z aplikacji mobilnej od ręcznie wpisanych w MS, pozwala oznaczyć rozliczenie przez FV, czerwonym wierszem sygnalizuje przeterminowane nieopłacone faktury klienta i ostrzega wyłącznie o rzeczywiście wykrytych gotowych zleceniach tej samej firmy kierowanych na identyczny adres. Dla zleceń poza umową FV jest domyślną podpowiedzią, ale operator może ją odznaczyć i zakończyć wysyłkę dokumentem WZ bez faktury. Zlecenie umowne bez FV kończy się magazynowym RW zapisanym w `ZAKUPY` i `ZAKPOZYCJA`; natywny trigger ustawia ilość pobraną i zmniejsza stan magazynu. RW używa numeracji `RW / N / RRRR` wspólnej z Menadżerem Serwisu i nie jest zapisywane jako `FAKTURA/ROK`. Wariant z FV automatycznie tworzy WZ i fakturę sprzedaży. Panel pokazuje zaakceptowaną cenę netto: dla umowy cenę zakupu, a poza umową edytowalną cenę sprzedaży z wyróżnionym fallbackiem do ceny zakupu. Przy odbiorze przez kuriera CTIP zapisuje datę i numer paczki w wykonaniu zlecenia, tworzy wymagane dokumenty oraz zamyka zlecenie w jednej transakcji. Operator może uruchomić ten sam proces zbiorczo dla dnia albo osobno dla aktualnie otwartego zlecenia. Interfejs odświeża kolejkę i bieżący stan MS co 30 sekund oraz po powrocie do karty. Akceptacja, etykieta i finalizacja ponownie kontrolują stan, technika i numer przesyłki; rekord jest blokowany także w transakcji Firebirda, więc zakończone zlecenie nie może zostać otwarte ponownie ani ponownie rozchodować magazynu. Konflikt wyświetla czerwone ostrzeżenie, zwraca HTTP `409` i kieruje sprawę do `reconcile_required`. Szczegóły zlecenia pokazują numery zaległych FV, kwoty brutto i pozostałe do zapłaty, terminy oraz liczbę dni opóźnienia. Puste źródła adresu są pomijane, a przycisk „Użyj danych” pozwala scalić niepełny adres z formularzem bez kasowania istniejącego telefonu, e-maila lub innych wartości. Testowe PDF-y używają fontu Unicode i realistycznego układu DPD, ale mają numer `MOCK`, niedekodowalny znacznik 2D oraz trwałe ostrzeżenie „NIE NADAWAĆ”. Na etykiecie widoczne są maksymalnie trzy wiersze części z ilością, indeksem i nazwą; dłuższa zawartość otrzymuje skrót odsyłający do osobnego zestawienia kompletacyjnego. Oficjalne żądanie DPD otrzymuje dodatkowo skrócony opis pozycji w polu `content` ograniczonym do 54 znaków, a zwrócone strony przewoźnika nie są modyfikowane. Moduł zachowuje częściową podpowiedź adresu z jednego źródła MS, polską walidację brakujących pól, domyślny filtr części potwierdzonych dla modelu z ręcznym przełączeniem na cały magazyn oraz częścio-centryczny katalog relacji wiele-do-wielu model–kartoteka. Pozycje ze stanem zerowym wymagają jawnej zgody operatora zapisywanej przy konkretnej części; zgoda pozwala MS czasowo utworzyć ujemny stan, ale nie omija konfliktów miękkich rezerwacji dla dodatniego stanu. Snapshot i fingerprint bieżącej `MASZYNA.STOI` blokują zaakceptowany adres oraz nadanie DPD, gdy lokalizacja urządzenia zmieni się w MS. Katalog obsługuje zbiorcze przypisywanie modeli, niezależne decyzje, wyszukiwanie wielowyrazowe, sugestie z nazw/kodów/historii i opcjonalny Web Search z klikalnymi cytowaniami; Firebird pozostaje źródłem odczytowym katalogu, a mapowania nie są zapisywane do `MAGAZYN.ID_MODEL`. Funkcjonalna trasa `/shipping/v2` korzysta z tego samego API i skryptu procesu, ale prezentuje wybrany układ 07 w kolorystyce 03 z techniczną siatką w tle, dokumentową kartą realizacji, bocznym postępem i dodatkowo powiększoną typografią etykiet, danych zlecenia, tabeli magazynowej oraz panelu audytu. Pod tabelą części pokazuje dynamiczną listę indeksów, nazw i ilości już dodanych do paczki. Dynamiczna aktualizacja lokalizacji zachowuje klasę ciemnego motywu, więc komunikat o braku lokalizacji nie wraca do jasnego tła. Ostrzeżenie po godzinie granicznej używa przygaszonego morsko-granatowego panelu spójnego z V2, a błędy pozostają czerwone. Dotychczasowy `/shipping` pozostaje dostępny, a oba widoki mają link pozwalający przełączać wersję. Funkcjonalne Archiwum w V2 zapisuje przy zamknięciu niezmienny snapshot zlecenia, odbiorcy, urządzenia, części, cen, operatorów, DPD oraz dokumentów RW/WZ/FV. Rejestr działa bez odczytu Firebirda, udostępnia wyszukiwanie wielowyrazowe i przybliżone, filtry daty, operatora, dokumentu, źródła, trybu DPD i wspólnej paczki, pełny podgląd oraz ponowne otwarcie etykiety. Osobna trasa `/shipping/prototypes` nadal udostępnia siedem niefunkcjonalnych makiet; wariant 06 łączy układ 01 z kolorystyką 03, a wariant 07 łączy czytelność 04 z kolorystyką 03.
- `app/web/mm_ui.py` + `app/templates/mm/` + `app/static/mm/` + `app/api/routes/admin_mm.py` + `app/services/mm_dashboard.py` – raport MM pod adresem `/mm` (frontend) oraz `GET /admin/mm/dashboard` (backend) do analizy przesunięć międzymagazynowych z Firebird z filtrami: zakres dat, magazyn docelowy (`złom`/`wynajem`), model urządzenia, wyszukiwanie po numerze MM/indeksie/serialu/ewidencji, zawężenie magazynu wydającego do `Urządzenia Magazyn` i `Urządzenia Wynajem`, kolumna `cena zakupu netto` i eksport CSV.
- `app/web/device_ui.py` + `app/templates/device/` + `app/static/device/` + `app/api/routes/admin_device.py` + `app/services/device_dashboard.py` + `app/services/device_intake.py` + `app/services/device_bnp_buyout.py` – wydzielony widok `/device` dla procesu urzadzen: audyt przyjec `PZ` na magazyn `28`, kontrola powiazan `MAGAZYN` / `SERIAL` / `MASZYNA`, lista problemow danych, audyt tabeli `MODEL`, automatyzacja kartoteki `AUTO/XXXX` i przyjecia `PZ` oraz osobny proces wykupu BNP na magazyn `27`.
- `app/web/crm_ui.py` + `app/templates/crm/` + `app/static/crm/` + `app/api/routes/crm.py` + `app/services/crm_cases.py` + `app/services/crm_notifications.py` – trwałe Centrum Obsługi pod adresem `/crm`: cztery kolejki (`Handel`, `Serwis + IT`, `Umowy i liczniki`, `Inne`), Kanban Handlu, pulpit dyspozytora, oś zdarzeń, wybór deklarowanego operatora testowego, idempotentne przyjmowanie spraw i retencja 360 dni. Powiadomienia SMS/e-mail są wybierane niezależnie dla każdego użytkownika i grupy `Handel`/`Pozostałe`; w LAB zapisują wyłącznie zdarzenie symulacji w `ctip_test`.
- `app/crm_prototype_app.py` – izolowana aplikacja LAB udostępniająca `/crm`, chronione `/api/crm/v1/*` i statyczne zasoby. Tryb wymaga `CRM_PUBLIC_PROTOTYPE_MODE=true`, `CRM_ENABLED=true`, `CRM_LAB_MODE=true`, `PGDATABASE=ctip_test`, lokalnego Firebird i `SMS_TEST_MODE=true`; CSP dopuszcza iframe wyłącznie ze skonfigurowanych źródeł.
- `app/lab_portal_app.py` + `app/templates/lab/` + `app/static/lab/` – brama laboratorium na porcie `8790`: formularze zastępujące wejścia Bitrix, generator scenariuszy oraz ograniczone proxy lokalnego widgetu CHAT_KP. Lokalna wtyczka PageKP `kp-ctip-forms` udostępnia wymagany telefon, modal produktu, globalny panel kontaktowy, widoczne pole zgody i zamianę osadzeń Bitrix w treści. Brama przyjmuje krótko ważny bilet HMAC dla widoków LAB oraz osobny token serwerowy `CRM_WWW_TOKEN` dla `POST /v1/form-cases`; formularze zapisuje wyłącznie jako sprawy `is_lab=true`.
- `app/web/contracts_ui.py` + `app/templates/contracts/` + `app/api/routes/admin_contracts.py` – techniczny dashboard workflow pod adresem `/contracts` (formularze SUBMITTED, weryfikacja klienta w Firebird, lista pozycji magazynowych Firebird dla magazynu `28`); `/flow` korzysta z tego samego backendu danych.
- `app/services/grenke_launch.py` – integracja API-only do przycisku `Wniosek GRENKE` w `/genform`: budowa `calculationKey`, prefill kalkulacji (`setSession.php`, `calculate.php`, `saveCalculation.php`) i fallback URL `partial` z query kodowanym pod parser `decodeURI` po stronie GRENKE (bez formatu `+` dla spacji); w trybie pełnym usługa uzupełnia dane dostawcy (`provider*`) na podstawie sprzedawcy z zapisanej proformy Firebird, zapisuje pole `rate` jako listę opcji (`kwartalna`, `miesieczna`) kompatybilną z frontendem GRENKE, ustawia opłatę początkową `0%`, odczytuje limity `default/min/maxMonths` po `setSession.php` i wybiera najwyższy poprawny okres leasingu w granicach tych limitów. Przejście do kroku 2 pozostaje fizycznym kliknięciem `Wypełnij wniosek online` w aplikacji GRENKE.
- `app/services/workflow_machine_binding.py` – automat dla statusu `APPROVED_ORDER`: wiązanie urządzeń workflow z klientem w `MASZYNA.ID_KLIENT` (główna operacja), wymuszenie `AKTYWNA=TAK` i `SYNWP=1`, próba normalizacji `MASZYNA.EWIDENCJA` do `KP/<numer>/GRENKE/<reszta>` (brak poprawnego formatu nie blokuje powiązania klienta), tworzenie nowych rekordów `MASZYNA` z mapowaniem danych z tabeli `MODEL` (`ID_MODEL`, `MARKA`, `MODEL`, `GRUPA`, `RODZAJ`, `KOLOROWA`, `TYP`, `RODZAJ_US`) oraz synchronizacja tych pól dla istniejących kart; dla źródła `firebird_magazyn_28` automat dociąga bieżący rekord `MAGAZYN`, parsuje techniczne `NAZWA` (`S/N`, `nr.wew`) i normalizuje warianty modeli Ricoh (`IMC` -> `IM C`, `MPC` -> `MP C`) zanim dopasuje `MODEL`; status zwracany do `/genform` pokazuje też licznik `powiązane/wszystkie` i skrót pierwszych błędów z identyfikatorem urządzenia (`producent`, `model`, `serial` albo `ewidencja`).
- `app/web/form_ui.py` + `app/templates/public/` – publiczny, jednorazowy formularz klienta pod adresem `/formularz/{token}`.
- `app/public_forms_app.py` – osobna aplikacja ASGI do publicznego wystawienia wyłącznie `/`, `/health` i `/formularz/{token}` pod niezależną subdomeną.
- `app/api/routes/portal_auth.py` + `app/static/root/root.js` – centralne logowanie na stronie głównej (`/`) oraz wybór sekcji na osobnym widoku `/choice`.
- `app/api/routes/assistant.py` + `app/services/assistant_*` + `app/web/assistant_ui.py` + `app/templates/assistant/` + `app/static/assistant/` – moduł CTIP AI Asystent (chat z historią, streaming SSE, narzędzia `firebird_read`, `firebird_business_read`, `firebird_knowledge_read`, `workflow_devices_audit`, `sheets_read`, `imap_read`, `ctip_schema_read` i `email_send_report`; odczyt danych pozostaje read-only, a wysyłka raportu idzie przez systemową skrzynkę SMTP CTIP, automatyczne wnioski o zmiany). Wyjątkiem wykonawczym jest kontrolowana akcja operatora po audycie urządzeń: zapis świeżej paczki do zakładki roboczej `urzadzenia_chat`, bez modyfikacji docelowej zakładki `Urzadzenia_magazyn`.
- `app/api/routes/admin_firebird.py` + `app/services/firebird_client.py` – konfiguracja i test połączenia z bazą Firebird programu Menadżer Serwisu.
- `app/services/bot_identity_directory.py` + `app/api/routes/bot_identity.py` + `app/api/routes/admin_bot_identity.py` – centralny katalog rozpoznawania kont klientów dla botów voice/chat, synchronizowany z Firebird wyłącznie do odczytu; zasady i kontrakt opisuje `docs/bot_identity_directory.md`.
- `inbox/` – lokalny katalog wymiany plików z Windows (dropzone), celowo odcięty od repozytorium Git.
- `inbox/firebird_audit/` – lokalne artefakty audytów Firebird MS, w tym surowe raporty JSON i zakomentowane propozycje SQL; katalog jest roboczy i nie służy do przechowywania sekretów.
- `scripts/inbox_samba.sh` – uruchamianie udziału SMB dla `inbox/` (mapowany dysk w Windows).
- `scripts/firebird_clone_local.py` – utworzenie lokalnej kopii roboczej pliku `.fdb` na podstawie `FB_DATABASE` i `FB_LOCAL_COPY_PATH`.
- `scripts/sync_prod_forms_to_test.py` – import najnowszych formularzy workflow z produkcyjnego PostgreSQL do lokalnego `ctip_test` z odczytem `read_only` po stronie źródła i upsertami po stronie testu.
- `scripts/manual_archive_contracts_via_smb.py` – reczny import wskazanych wiadomosci umow GRENKE: pobranie PDF z IMAP, proba odszyfrowania haslem z danych reprezentanta, zapis do SMB (`sciezka_dok_umow`) oraz opcjonalny zapis metadanych do PostgreSQL (po podaniu DSN).
- `scripts/prod_workflow_devices_sync.py` – zestaw operacji produkcyjnych dla FLOW urzadzen (`audit`, `sync-sheet`, `sync-machines`, `move-serial`, `append-notes`, `fill-msid-by-index`) z raportami JSON do `inbox/`.
- `scripts/build_firebird_knowledge_index.py` + `docs/firebird/knowledge/firebird_ms_knowledge.json` – trwała baza wiedzy o Firebird MS (tabele/kolumny/dokumentacja), regenerowana ze źródła `integrations/bazams`, używana przez chat i inne moduły repo do ograniczenia kosztów analizy.
- `integrations/bazams/` – lokalny klon repozytorium wiedzy o MS Firebird (`Jarmix80/bazams`), używany jako źródło do budowy indeksu wiedzy.
- `integrations/google_sheets/update_calendar_and_devices.py` – aktualizacja arkuszy Google (`Kalendarz_wiersze`, `Urzadzenia`) z formatowaniem i slotami zdarzeń dziennych.

## Wymagania systemowe
- Python 3.11 lub nowszy (z bibliotekami `psycopg` oraz – opcjonalnie dla Windows – `pywin32`; `uvloop` instalowane tylko na Linux dzięki warunkowi w `requirements.txt`).
- Dostępny serwer PostgreSQL >= 13 z utworzonym schematem `ctip`.
- Łącze sieciowe TCP z centralą Slican (port CTIP domyślnie 5524).
- System Linux lub Windows (dla usługi Windows wymagane uprawnienia administratora).

## Konfiguracja środowiskowa
Lokalna praca w repozytorium odbywa się wyłącznie na `.env.test` oraz bazie `ctip_test`. Produkcyjny `.env` pozostaje poza repo i jest używany dopiero na serwerze wdrożeniowym. Artefakty lokalne (`.codex/*` poza `.codex/session.json`, `backups/`, lokalne binaria w `tools/`) pozostają poza wersjonowaniem; sekrety z obu plików środowiskowych również nie trafiają do Git.

### Źródła prawdy dla konfiguracji
- Dane połączeniowe i sekrety integracyjne są utrzymywane wyłącznie w plikach środowiskowych: `.env.test` dla pracy lokalnej oraz `.env` na serwerze Windows.
- Dotyczy to sekcji: PostgreSQL, Firebird (Menadżer Serwisu), Firebird v-maintenance, Google Sheets (FLOW), CTIP, SerwerSMS, E-mail SMTP oraz integracyjnej części modułu kopii zapasowych (lokalizacja docelowa, Office 365, SQL Optima).
- Panel administratora pokazuje te sekcje w trybie tylko do odczytu; próba `PUT /admin/config/...` lub zapisu env-zależnych pól backupu kończy się `423 Locked` i wpisem do audytu.
- Wyjątki operacyjne pozostające w `ctip.admin_setting`: konfiguracja `form_handling`, harmonogramy/retencja backupu oraz `kp_repair.email_lookback_months`.
- `collector_service.py` uruchamia proces `collector_full.py` z wartościami z pliku wskazanego przez `collector_service_config.json -> env_file` (domyślnie `D:\CTIP\.env`), aby usługa Windows nie dziedziczyła przypadkowych globalnych zmiennych systemowych.

### Operacyjne sync urzadzen FLOW (prod)
Do powtarzalnego audytu i aktualizacji arkusza `Urzadzenia_magazyn` oraz uzupelnienia `MASZYNA` w Firebird sluzy:

```bash
python scripts/prod_workflow_devices_sync.py --env-file .env audit
python scripts/prod_workflow_devices_sync.py --env-file .env --apply sync-sheet
python scripts/prod_workflow_devices_sync.py --env-file .env --apply sync-machines --sheet-report auto
python scripts/prod_workflow_devices_sync.py --env-file .env --apply move-serial
python scripts/prod_workflow_devices_sync.py --env-file .env --apply append-notes --sheet-report auto --serial-report auto
```

Tryb bez `--apply` wykonuje dry-run i zapisuje tylko raporty. Wszystkie raporty trafiaja do `inbox/`:
- `raport_urzadzenia_prod_audit_*.json`
- `raport_urzadzenia_prod_sync_sheet_*.json`
- `raport_urzadzenia_prod_sync_maszyna_*.json`
- `raport_urzadzenia_prod_move_serial_*.json`
- `raport_urzadzenia_prod_append_notes_*.json`

### Automat workflow dla `APPROVED_ORDER`
Po ręcznym ustawieniu statusu biznesowego sprawy na `APPROVED_ORDER` endpoint `POST /admin/contracts/forms/{form_id}/workflow/status` uruchamia automat:
- wiąże każde zapisane urządzenie workflow z klientem sprawy (`firebird_client_id`) po stronie Firebird,
- dopina `GRENKE` do `EWIDENCJA` zgodnie z regułą `KP/<numer>/GRENKE/<reszta>`; gdy `EWIDENCJA` nie ma formatu `KP/<numer>/...`, klient i tak jest wiązany, a status urządzenia dostaje ostrzeżenie o pominiętej normalizacji,
- uzupełnia `MS_ID_MASZYNA` oraz aktualne `INDEKS/EWIDENCJA` w arkuszu FLOW,
- aktualizuje status „Menadżer Serwisu” w `/genform` (zielony/żółty/czerwony); przy częściowym albo pełnym błędzie status zawiera stosunek `powiązane/wszystkie` oraz skrót pierwszych błędów z nazwą urządzenia i `Nr seryjny` lub `EWIDENCJA`,
- przy błędach wysyła alert SMS i e-mail do aktywnych administratorów, ale nie blokuje zmiany statusu sprawy.

Jeżeli sprawa ma już status `APPROVED_ORDER`, ten sam automat uruchamia się ponownie również przy zapisie wyboru urządzeń (`POST /admin/contracts/forms/{form_id}/workflow/devices`), żeby utrzymać spójność po zmianach listy urządzeń.

W arkuszu FLOW wymagane są nagłówki techniczne:
- `MS_ID_MAGAZYN_TABLE`,
- `MS_ID_MASZYNA`.

Kolumna `REZERWACJA GRENKE` jest zapisywana jako dwie linie:
- linia 1: handlowiec (assignee),
- linia 2: nazwa klienta z formularza.

### Obsługa dostaw i kalendarz końców umów GRENKE
Status `APPROVED_ORDER` przekazuje formularz z FLOW do modułu `/delivery`. W tym momencie CTIP tworzy albo odświeża sprawę `delivery_case`, kopiuje listę urządzeń do `delivery_case_device`, zapisuje `grenke_contract_start_date` jako datę pierwszej akceptacji GRENKE i zakłada wpis `grenke_contract_end` jako kandydat do kalendarza końca umowy. Data końca umowy jest traktowana jako roboczy prefill: operator musi ją potwierdzić, zanim system zacznie wysyłać przypomnienia.

Workflow rozróżnia dwie daty startu:
- `grenke_contract_start_date` – początek umowy GRENKE, wyprowadzany z pierwszego statusu `APPROVED_ORDER` albo historycznego `APPROVED`; automat mailboxa zapisuje tu datę wiadomości o akceptacji, a nie godzinę przetworzenia backlogu;
- `kp_contract_start_date` – początek umowy Ksero-Partner, ustawiany z daty dowozu `delivery_date` i aktualizowany przy przeniesieniu terminu.

Moduł `/delivery` obejmuje:
- listę dostaw GRENKE, dostaw ręcznych oraz odbiorów urządzeń od klientów,
- ręczne utworzenie dostawy spoza GRENKE z wyszukaniem istniejącego klienta Firebird albo utworzeniem klienta tym samym mechanizmem, którego używają formularze,
- zaplanowanie odbioru urządzeń: operator wyszukuje klienta MS, wybiera jego aktywne urządzenia i tworzy sprawę bez modyfikowania bazy Firebird na etapie planowania,
- planowanie terminu, okna czasowego, danych kontaktowych, zadań przygotowania, dowozu, odbioru, zerówki i kontaktu z klientem,
- dodawanie plików sprawy oraz generowanie dokumentów DOCX z szablonów trzymanych poza repo,
- kalendarz końców umów GRENKE z ręcznym potwierdzeniem daty oraz przypomnieniami SMS/e-mail dla aktywnych handlowców.

Widok użytkownika `/delivery` jest pulpitem roboczym: górny pasek zawiera główne akcje, lewa kolumna pokazuje karty spraw, a prawa kolumna otwiera szczegóły wybranej dostawy albo odbioru. Szczegóły są podzielone na zakładki `Ustalenia`, `Urządzenia`, `Zadania` i `Dokumenty`, aby operator nie musiał przechodzić między osobnymi ekranami przy codziennej obsłudze.

Konfiguracja modułu dostaw:
- katalog plików spraw określa `DELIVERY_FILES_ROOT` (domyślnie `inbox/delivery/files`),
- katalog szablonów dokumentów określa `DELIVERY_DOCUMENT_TEMPLATES_ROOT` (domyślnie `inbox/doku`),
- scheduler przypomnień końców umów kontrolują `DELIVERY_NOTIFICATIONS_SCHEDULER_ENABLED` i `DELIVERY_NOTIFICATIONS_INTERVAL_SECONDS`.

Scheduler przypomnień działa na progach 60/30/7 dni przed potwierdzoną datą końca umowy, zapisuje przebiegi w `admin_audit_log` oraz używa `notification_history` w `ctip.grenke_contract_end`, aby nie dublować tego samego progu. Jeżeli serwer był wyłączony w dniu progu, kolejny przebieg wykonuje najbliższe zaległe przypomnienie przed datą końca umowy.

Uprawnienia:
- sekcja `delivery` jest wymagana do API `/admin/delivery/*` i widoku `/delivery`,
- rola `serwisant` domyślnie dostaje tylko sekcję `delivery`,
- administrator może przypisać sekcję `delivery` także innym rolom z poziomu panelu użytkowników.

### Uruchomienie Codex
Skrypt `scripts/run_codex.sh` uruchamia Codex w kontekście repozytorium i automatyzuje kroki wymagane przez `AGENTS.md`:
- ustawia katalog roboczy na root projektu i weryfikuje obecność `AGENTS.md`,
- wczytuje lokalne `.env.test` z eksportem zmiennych,
- tworzy (jeśli brak) i aktywuje `.venv`,
- wykonuje preflight (`scripts/codex_preflight.py`): sprawdza, czy konfiguracja pozostaje testowa, czy działa web `/health`, czy odpowiada lokalna baza PostgreSQL i czy działa port CTIP/mock; jeśli system testowy nie jest uruchomiony, skrypt pyta, czy wystartować `./ctiptest`,
- ustawia `CODEX_HOME` na `.codex` i uruchamia `codex` lub `openai codex`.

Przykład: `./scripts/run_codex.sh` (opcjonalnie z parametrami, np. `./scripts/run_codex.sh --help`).

Uwaga komunikacyjna: w pracy Codex z tym repo wszystkie opisy i wyjasnienia dla uzytkownika musza byc prowadzone po polsku (kod, komendy i logi pozostaja bez zmian).

### Trwale wznowienie sesji Codex
Do repo dodano prosty mechanizm utrzymania kontekstu pracy:
- reczny stan sesji: `docs/session_state.md` (sekcja `Biezacy Kontekst`),
- automatyczny snapshot: `scripts/update_session_state.sh` (sekcja `Historia Snapshotow`).

Skrypt dopisuje do `docs/session_state.md`:
- date i czas,
- biezaca galaz Git,
- wynik `git status --short`,
- ostatnie 20 commitow (`git log --oneline -20`),
- opcjonalna notatke przekazana jako argument.

Uzycie:
```bash
./scripts/update_session_state.sh
./scripts/update_session_state.sh "Po poprawce walidacji formularza i przed uruchomieniem testow"
```

Rekomendacja operacyjna: po kazdej wiekszej zmianie uzupelnij recznie sekcje `Biezacy Kontekst`, a nastepnie uruchom skrypt snapshotu.

### Katalog wymiany plikow (mapowany dysk Windows)
Do wygodnego wrzucania plikow bez komend po stronie Windows przygotowany jest katalog `inbox/` oraz udział SMB.

`inbox/` jest katalogiem lokalnym i nie jest wersjonowany przez Git. Po świeżym klonie repo utwórz go ręcznie:
```bash
mkdir -p inbox/firebird
```

Start/stop/uslugi SMB na serwerze:
- start: `./scripts/inbox_samba.sh start`
- status: `./scripts/inbox_samba.sh status`
- logi: `./scripts/inbox_samba.sh logs`
- stop: `./scripts/inbox_samba.sh stop`

Po starcie skrypt wypisze dane dostepowe i sciezke udzialu, domyslnie:
- udzial: `\\192.168.0.9\ctip-inbox`
- konto: `ctipdrop`
- haslo: generowane automatycznie i zapisywane lokalnie w `inbox/.smb_credentials`

Mapowanie dysku w Windows (GUI):
1. Otworz `Ten komputer` -> `Mapuj dysk sieciowy`.
2. W polu folder wpisz `\\192.168.0.9\ctip-inbox`.
3. Zaznacz `Polacz przy uzyciu innych poswiadczen` i podaj konto/haslo SMB.
4. Po mapowaniu kopiujesz pliki metoda drag&drop bez zadnych komend.

Uwaga: jeśli zapora UFW jest aktywna, otworz porty SMB: `sudo ufw allow 139/tcp` oraz `sudo ufw allow 445/tcp`.

### Automat odczytu NIP i numeru umowy z PDF
W repo dostepny jest automat `scripts/inbox_contract_watcher.py`, ktory przetwarza pliki `*.pdf` z katalogu `inbox/` i zapisuje wynik do plikow `*.pdf.parsed.json` (w tym samym katalogu co PDF).

Uruchomienie jednorazowe:
```bash
source .venv/bin/activate
set -a && source .env.test && set +a
python scripts/inbox_contract_watcher.py --inbox-dir inbox
```

Tryb ciagly (nasluch nowych/zmienionych PDF):
```bash
source .venv/bin/activate
set -a && source .env.test && set +a
python scripts/inbox_contract_watcher.py --inbox-dir inbox --watch
```

Uwagi operacyjne:
- parser wykorzystuje `pypdf` i najpierw probuje odczytu warstwy tekstowej PDF (bez OCR),
- jezeli dokument jest pustym wzorem (bez wypelnionych pol), wynik moze nie zawierac `nip` i/lub `contract_number`,
- lista wszystkich wykrytych kandydatow jest zapisywana w polach `nips_found` i `contract_number_candidates`.

### Test połączenia skrzynki umów (IMAP + SMTP SSL)
Pierwszy krok automatyzacji obsługi umów to test połączenia skrzynki e-mail przez IMAP (odbiór) oraz SMTP (wysyłka). W repo dostępny jest skrypt `scripts/mailbox_connection_check.py`.

Uruchomienie testu:
```bash
source .venv/bin/activate
set -a && source .env.test && set +a
python scripts/mailbox_connection_check.py
```

Skrypt czyta konfigurację `MAILBOX_*`, wykonuje logowanie do `INBOX` przez IMAP SSL i test logowania SMTP SSL/STARTTLS.

### Synchronizacja wiadomości umów z FLOW
Skrypt `scripts/contracts_mailbox_sync.py` analizuje wiadomości z `INBOX`, rozpoznaje temat i treść wiadomości:
- `Decyzja do wniosku ...` -> ustawia status `WAITING_SIGNATURE`, a gdy treść wiadomości/PDF zawiera odmowę (`odmowa`, `decyzja negatywna`, `wniosek odrzucony`, `brak zgody`) ustawia `REJECTED_GRENKE` i termin zwolnienia zasobów,
- `Zgoda na realizację zamówienia do wniosku ...` -> ustawia status `APPROVED_ORDER`, zapisuje datę e-mail jako datę podpisania umowy (`delivery_date`), uruchamia automat wiązania urządzeń z klientem Menadżera Serwisu, przekazuje sprawę do modułu `/delivery` i wyznacza termin archiwizacji.
- gdy temat zawiera ogólną `Decyzję do wniosku`, ale treść zawiera jawną zgodę na realizację zamówienia, wiadomość jest traktowana jako `APPROVED_ORDER`; ponowne przetwarzanie starych decyzji nie cofa statusów końcowych do `WAITING_SIGNATURE`.

Dodatkowo skrypt:
- próbuje powiązać wiadomość z formularzem `SUBMITTED` po numerze wniosku, numerze proformy (`.../proforma/...`) i treści (NIP/nazwa/reprezentant),
- przy ekstrakcji danych z PDF odrzuca błędne numery NIP na podstawie sumy kontrolnej, żeby nie mylić ich z numerami KRS lub innymi identyfikatorami,
- zapisuje numer wniosku i metadane e-maila w snapshotcie sprawy (`_mailbox_meta`),
- zapisuje odszyfrowaną umowę PDF do archiwum plików (`CONTRACTS_MAILBOX_ARCHIVE_ROOT`, katalog `nazwa_firmy/<nr_formularza>`) oraz zapisuje w bazie ścieżkę i opis pliku (`_mailbox_meta.archived_contract_files`),
- jeżeli odszyfrowanie PDF nie powiedzie się, zapisuje fallbackowo zaszyfrowaną wersję umowy (`kind=encrypted_contract_pdf`) i również utrwala ścieżkę w `_mailbox_meta`,
- dla zaszyfrowanego PDF próbuje odszyfrować dokument hasłem wyliczonym z danych reprezentanta i zwraca wynik ekstrakcji/OCR,
- format hasła PDF: `ostatnie 5 cyfr PESEL + inicjały ImięNazwisko + $` (np. `05791JK$`),
- zapisuje wszystkie załączniki lokalnie w strukturze `inbox/mailbox/contracts/<scope>/<YYYY-MM-DD>/<message_id>/`,
- zapisuje metadane plików (ścieżka, nazwa, rozmiar, SHA-256) do bazy w `form_workflow_case.client_payload_snapshot._mailbox_meta`,
- zapisuje nierozpoznane lub niedopasowane wiadomości do kolejki wyjątków w `inbox/mailbox/contracts_mailbox_state.json` (`unresolved`).

Powody wpisu do kolejki wyjątków:
- `unsupported_subject` – temat nie pasuje do obsługiwanych wzorców,
- `unmatched_form` – wiadomość nie została powiązana z formularzem,
- `ambiguous_match` – wykryto wieloznaczne dopasowanie (np. remis punktacji).

Wynik działania skryptu pokazuje liczniki bezpieczeństwa:
- `nierozpoznane`,
- `niedopasowane`,
- `wieloznaczne`,
- `otwarte wyjątki`.

Uruchomienie:
```bash
source .venv/bin/activate
set -a && source .env.test && set +a
python scripts/contracts_mailbox_sync.py --limit 30
```

Tryb podglądu (bez zmian w bazie):
```bash
python scripts/contracts_mailbox_sync.py --limit 30 --dry-run
```

Tryb restrykcyjny (kod wyjścia `!= 0` także dla ostrzeżeń, np. `unmatched_form`):
```bash
python scripts/contracts_mailbox_sync.py --limit 30 --fail-on-warnings
```

Domyślnie ostrzeżenia nie oznaczają błędu procesu (skrypt kończy się kodem `0`), żeby scheduler i panel admina nie raportowały `error` tylko przez niedopasowane wiadomości. Logowanie OCR i listy ostrzeżeń jest automatycznie skracane, aby nie zalewać audytu bardzo długim `stdout_tail`.

Wyzwolenie synchronizacji z poziomu panelu/API (wymaga sesji admin/operator i sekcji `generator`):
```bash
curl -X POST http://127.0.0.1:8000/admin/contracts/workflow/mailbox-sync \
  -H "Content-Type: application/json" \
  -H "X-Admin-Session: <TOKEN>" \
  -d '{"limit":60,"folder":"INBOX","reprocess":false,"dry_run":false,"timeout_seconds":300}'
```

Endpoint zwraca skrócony raport (`summary`, `stdout_tail`, `stderr_tail`) i zapisuje zdarzenie audytowe `contracts_mailbox_sync_trigger`. Odcinanie ogona logów jest limitowane (`max_lines=80`, `max_chars=4000`), identycznie jak w schedulerze.

### Test dostepu do zasobu SMB z .env
Do szybkiej diagnostyki dostepu do udzialu sieciowego dostepny jest skrypt:
- `scripts/check_smb_resource_access.py`

Skrypt pobiera z `.env` (lub env) klucze:
- `sciezka_dok_umow`
- `login_dok_umow`
- `pass_dok_umow`

i wykonuje test logowania SMB + listowanie katalogu + odczyt pliku testowego.

Przyklad:
```bash
source .venv/bin/activate
python scripts/check_smb_resource_access.py --env-file .env --read-file test.txt
```

Automat mailboxa działa też cyklicznie w tle po starcie backendu (`app/main.py`) jako scheduler `contracts-mailbox-scheduler`. Każdy przebieg zapisuje wpis audytu `contracts_mailbox_sync_scheduler` z podsumowaniem, kodem wyjścia i ogonem logów (`stdout_tail`/`stderr_tail`).

Retencja audytu mailboxa (tylko gdy jawnie wlaczona) jest realizowana automatycznie przez ten sam scheduler:
- `CONTRACTS_MAILBOX_AUDIT_CLEANUP_ENABLED=1` - wlacza automat retencji (domyslnie wylaczone),
- `CONTRACTS_MAILBOX_AUDIT_CLEANUP_INTERVAL_SECONDS=21600` - co ile sekund uruchamiac retencje,
- `CONTRACTS_MAILBOX_AUDIT_COMPACT_AFTER_DAYS=7` - po ilu dniach przycinac dlugie `stdout_tail/stderr_tail`,
- `CONTRACTS_MAILBOX_AUDIT_COMPACT_MAX_CHARS=1000` - dlugosc zachowywanego ogona historycznego logu,
- `CONTRACTS_MAILBOX_AUDIT_DELETE_AFTER_DAYS=90` - po ilu dniach usuwac wpisy `contracts_mailbox_sync_*` (0 = bez usuwania).

Zalecenie: wlaczac retencje tylko na produkcji (`.env` na Windows Server), a lokalnie pozostawic `CONTRACTS_MAILBOX_AUDIT_CLEANUP_ENABLED=0`.

Dashboard `GET /admin/contracts/dashboard` zwraca dodatkowo sekcję `mailbox_sync` z metadanymi ostatniego przebiegu synchronizacji e-mail (`source`, `result`, `last_run_at`, `summary`, `exit_code`), dzięki czemu operator widzi aktualność automatu bez przeglądania logów.

### Smoke-test logowania web i GENFORM/FLOW
Do szybkiej walidacji po wdrozeniu dostepny jest skrypt:
- `scripts/smoke_web_genform_flow.py`

Zakres testu:
- logowanie przez `POST /admin/auth/login`,
- walidacja sesji przez `GET /admin/auth/me`,
- odczyt dashboardu GENFORM/FLOW dla `active/accepted/rejected/unfilled`,
- kontrola spojnosci kluczowych przyciskow (`summary`, `release_resources`),
- opcjonalny dry-run synchronizacji mailboxa.

Przyklad (lokalnie):
```bash
source .venv/bin/activate
set -a && source .env.test && set +a
python scripts/smoke_web_genform_flow.py \
  --base-url http://127.0.0.1:8000 \
  --email admin@example.com \
  --password-env PASS_ADMIN_WEB
```

Przyklad (produkcja, haslo z `.env` pod kluczem `pass_admin_web`):
```bash
source .venv/bin/activate
set -a && source .env && set +a
python scripts/smoke_web_genform_flow.py \
  --base-url http://192.168.0.8:8000 \
  --email marcin@ksero-partner.com.pl \
  --password-env pass_admin_web \
  --check-mailbox-dry-run \
  --out inbox/wynik_smoke_genform_flow.json
```

Kod wyjscia:
- `0` - test zakonczony powodzeniem,
- `2` - wykryto bledy (szczegoly w JSON na STDOUT lub pliku `--out`).

### Zmienne środowiskowe kolektora (`collector_full.py`)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `PBX_HOST` | `127.0.0.1` | Lokalny mock CTIP dla pracy testowej; produkcyjna centrala to `192.168.0.11` i wymaga jawnego startu produkcyjnego. |
| `PBX_PORT` | `5525` | Port TCP lokalnego mocka CTIP. |
| `PBX_PIN` | `1234` | PIN do komendy `LOGA`. |
| `PGHOST` / `PGPORT` | `127.0.0.1` / `5432` | Adres/port lokalnego PostgreSQL dla pracy w repo. |
| `PGDATABASE`, `PGUSER`, `PGPASSWORD` | `ctip_test`, `ctip_test`, `ctip_test` | Dane lokalnej, jedynej kanonicznej bazy testowej. |
| `PGSSLMODE` | `disable` | Tryb TLS (ustaw na `require`, jeśli serwer wymusza TLS). |
| `SOCK_CONNECT_TIMEOUT`, `SOCK_RECV_TIMEOUT` | `5`, `5` | Limity czasowe gniazda w sekundach. |
| `RECONNECT_DELAY_SEC` | `3` | Odstęp między próbami ponownego połączenia. |
| `LOGIN_ACK_TIMEOUT` | `8` | Limit czasu (s) oczekiwania na `aOK LOGA` po wysłaniu polecenia. |
| `PAYLOAD_ENCODING` | `latin-1` | Kodowanie zapisu surowego payloadu. |
| `LOG_PREFIX` | `[CTIP]` | Prefiks logów widocznych na STDOUT. |

Uwaga operacyjna: zasoby `192.168.0.8` (PostgreSQL/Firebird) oraz `192.168.0.11` (PBX) są traktowane jako produkcyjne. Lokalny start repozytorium nie może z nich korzystać bez wyraźnego polecenia użytkownika.

### Zmienne środowiskowe modułu SMS (`sms_sender.py`)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` | jak wyżej | Dostęp do PostgreSQL. |
| `POLL_SEC` | `3` | Okres odpytywania kolejki `sms_out`. |
| `SMS_DEFAULT_SENDER` | `CTIP-Test` | Domyślna nazwa nadawcy w środowisku lokalnym. |
| `SMS_TYPE` | `eco+` | Kanał/typ wiadomości (zgodnie z konfiguracją operatora). |
| `SMS_API_URL` | *(puste)* | Puste pole wymusza lokalną symulację `SIMULATED`, jeśli nie podasz operatora. |
| `SMS_API_TOKEN` | *(puste)* | Token dostępowy (opcjonalnie, gdy operator go udostępnia). |
| `SMS_API_USERNAME`, `SMS_API_PASSWORD` | *(puste)* | Login i hasło do HTTPS API (jeśli nie używamy tokenu). |
| `SMS_TEST_MODE` | `true` | W lokalnym repo musi pozostać `true`, aby nie generować realnych wiadomości. |

### Zmienne środowiskowe DPD Services REST
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `DPD_ENABLED` | `false` | Główna blokada operacji DPD. |
| `DPD_MODE` | *(fallback do `DPD_TEST_MODE`)* | `mock`, `demo` albo `production`. Historyczne `DPD_TEST_MODE=true|false` działa tylko wtedy, gdy `DPD_MODE` jest puste. |
| `DPD_API_URL` | *(zależnie od trybu)* | Opcjonalne jawne wskazanie oficjalnego adresu; host niezgodny z `DPD_MODE` jest blokowany. |
| `DPD_LOGIN`, `DPD_PASSWORD` | *(puste)* | Dane Basic Auth otrzymane od DPD; nie zapisuj ich w Git. |
| `DPD_MASTER_FID` | *(puste)* | Master FID przekazywany w nagłówku `X-DPD-FID`. |
| `DPD_PAYER_FID` | *(puste)* | FID płatnika/numkat przekazywany jako `payerFID` w paczce. |
| `DPD_GENERATE_PACKAGES_PATH` | `/public/shipment/v1/generatePackagesNumbers` | Oficjalna metoda tworzenia numerów paczek i parceli. |
| `DPD_GENERATE_LABELS_PATH` | `/public/shipment/v1/generateSpedLabels` | Oficjalna metoda pobierania etykiet PDF. |
| `DPD_TIMEOUT_SECONDS` | `20` | Limit pojedynczego żądania HTTPS. |
| `DPD_SENDER_*` | *(dane Ksero-Partner)* | Firma, kontakt, ulica, kod, miasto, telefon i e-mail nadawcy; w trybie Demo są także odbiorcą testowym. |

Formularz i konfiguracja przechowują polski kod pocztowy w czytelnym formacie `00-000`. Adapter usuwa myślnik wyłącznie w żądaniu do DPD REST, ponieważ API przewoźnika wymaga pięciu cyfr, np. `00000`; dokumenty lokalne nadal pokazują format z myślnikiem.

### Zmienne środowiskowe modułu Firebird (Menadżer Serwisu)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `FB_HOST` | `127.0.0.1` | Host lokalnego Firebird lub kontenera testowego. |
| `FB_PORT` | `3050` | Port usługi Firebird. |
| `FB_MODE` | `local` | Domyślny tryb pracy lokalnej kopii Firebird. |
| `FB_DATABASE` | `/tmp/test_ms.fdb` | Ścieżka lokalnej bazy Firebird dla testów. |
| `FB_USER`, `FB_PASSWORD` | `SYSDBA`, `masterkey` | Dane logowania Firebird. |
| `FB_CHARSET` | `UTF8` | Kodowanie sesji Firebird. |
| `FB_ROLE` | *(puste)* | Rola Firebird (opcjonalnie). |
| `FB_LOCAL_COPY_PATH` | `inbox/firebird/test_ms_local.fdb` | Docelowa ścieżka lokalnej kopii roboczej bazy. |
| `FB_ALLOW_WRITES` | `false` | Jawna zgoda na zapis do aktywnej bazy Firebird. Flaga jest odczytywana bezpośrednio z `.env`; panel tylko pokazuje jej bieżący stan. |
| `SHIPPING_TEST_FIREBIRD_WRITES` | `false` | Pozwala etykiecie `mock` albo DPD Demo zapisać pozycje zlecenia i dokumenty wyłącznie przy `ctip_test`, lokalnym Firebirdzie z `TEST` w nazwie, `FB_ALLOW_WRITES=true`, `DPD_MODE=mock|demo` i `SMS_TEST_MODE=true`. |
| `FB_WAREHOUSE_CLIENT_ID` | `656` | Domyślny `ID_KLIENT` dla technicznych zapisów urządzeń magazynowych w lokalnej Firebird. |
| `FB_WAREHOUSE_ID` | `28` | Domyślny `ID_MAGAZYN` dla pozycji magazynowych tworzonych przez synchronizację urządzeń. |

W lokalnym środowisku odtworzeniowym klient Menadżera Serwisu używa `192.168.0.9:3050` oraz aliasu `BAZAMS_TEST`. Backend hosta łączy się przez stały, dostępny wyłącznie lokalnie port `127.0.0.1:3051`, który prowadzi bezpośrednio do testowego Firebirda i omija bramę HAProxy niezgodną ze sterownikiem `firebirdsql`. Nie należy wpisywać do `FB_HOST` zmiennego adresu kontenera z sieci Docker.

### Zmienne środowiskowe Centrum Obsługi i katalogu tożsamości
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `CRM_ENABLED` | `false` | Włącza trwałe API Centrum Obsługi. |
| `CRM_LAB_MODE` | `false` | Wymusza oznaczanie nowych spraw jako LAB i udostępnia kontrolowany reset danych testowych. |
| `CRM_PUBLIC_PROTOTYPE_MODE` | `false` | Włącza interfejs bez logowania wyłącznie w izolowanej aplikacji LAB. |
| `CRM_WWW_TOKEN` | *(puste)* | Odrębny sekret połączenia serwer-serwer WordPress → `POST /v1/form-cases`; nie może być przekazywany do przeglądarki ani zapisywany w Git. |
| `CRM_RETENTION_DAYS` | `360` | Retencja treści spraw i ich osi zdarzeń. |
| `CRM_AUTO_ARCHIVE_DAYS` | `30` | Termin automatycznej archiwizacji po zakończeniu lub przekazaniu sprawy. |
| `CRM_RETENTION_SCHEDULER_ENABLED` | `false` | Włącza okresowe usuwanie spraw po retencji. |
| `CRM_FRAME_ANCESTORS` | domeny Ksero Partner | Lista originów dopuszczonych przez CSP do osadzenia LAB w iframe. |
| `CRM_LAB_IFRAME_SECRET` | brak | Wspólny sekret HMAC bramy LAB i wtyczki `pagekp`; wartość musi odpowiadać `KP_CTIP_LAB_IFRAME_SECRET`. |
| `CRM_LAB_CHAT_BASE_URL` | `http://192.168.0.9:8787` | Stały adres lokalnej instancji publicznego widgetu CHAT_KP używany wyłącznie przez ograniczone proxy. |
| `CRM_LAB_PROXY_TIMEOUT_SECONDS` | `60` | Limit czasu połączenia bramy z lokalnym CHAT_KP. |
| `BOT_IDENTITY_ENABLED` | `false` | Włącza synchronizację centralnego katalogu z Firebird. |
| `BOT_IDENTITY_SECRET_KEY` | *(puste)* | Klucz Fernet oraz źródło kluczy HMAC dla telefonu, NIP i numeru seryjnego. |
| `BOT_IDENTITY_VOICE_TOKEN`, `BOT_IDENTITY_CHAT_TOKEN` | *(puste)* | Różne tokeny usługowe związane odpowiednio z kanałem voice i chat. |
| `BOT_IDENTITY_TEST_SMS_CODE` | *(puste)* | Opcjonalny sześciocyfrowy kod wyłącznie dla izolowanego testu SMS w `ctip_test`; LAB używa `123456`, kod nie jest zwracany przez API i zmienna nie może występować w produkcyjnym `.env`. |
| `BOT_IDENTITY_IMAGE_HTTPS_HOSTS` | domeny Ksero Partner | Lista hostów HTTPS, z których katalog może zwracać zdjęcia modeli zapisane w `MODEL.PLIK`. |
| `BOT_IDENTITY_IMAGE_LAB_HTTP_HOSTS` | lokalne hosty LAB | Lista hostów HTTP dopuszczonych wyłącznie przy `CRM_LAB_MODE=true`, `PGDATABASE=ctip_test` i `SMS_TEST_MODE=true`. |
| `BOT_IDENTITY_MODEL_IMAGE_ROOT` | *(puste)* | Opcjonalny bezwzględny katalog kontrolowanych obrazów obsługiwanych przez `/v1/device-model-images/{image_ref}`. |
| `BOT_IDENTITY_IMAGE_MAX_BYTES` | `5242880` | Maksymalny rozmiar pojedynczego obrazu udostępnianego przez CTIP. |
| `BOT_IDENTITY_IMAGE_CACHE_SECONDS` | `3600` | Czas bezpiecznego cache obrazu modelu w przeglądarce. |
| `BOT_IDENTITY_SYNC_INTERVAL_SECONDS` | `300` | Cykl odczytu lokalnej bazy Firebird w trybie read-only. |
| `BOT_IDENTITY_WARN_AFTER_SECONDS`, `BOT_IDENTITY_BLOCK_AFTER_SECONDS` | `900`, `3600` | Progi ostrzeżenia i blokady ujawniania dla nieświeżego katalogu. |

Synchronizację bez pozostałych usług uruchamia `python -m app.bot_identity_worker`.
Worker odmawia startu przy `FB_ALLOW_WRITES=true`. W środowisku `ctip_test`
wymagane są `FB_MODE=local`, testowa nazwa bazy oraz lokalny host lub alias
kontenera `firebird`. Sterownik otwiera transakcję Firebird jako
`ISOLATION_LEVEL_READ_COMMITED_RO`.

Izolowane API dla CHAT_KP i kanału voice uruchamia
`uvicorn app.bot_identity_api_app:app`. Proces udostępnia tylko trasy
`/internal/v1`, kompatybilne trasy `/v1` oraz `/health`; nie uruchamia
schedulera synchronizacji ani paneli CTIP. Trasa `/v1/capabilities` odpowiada
kontraktem `1.0` wymaganym przez CHAT_KP.

Odpowiedź `POST /v1/customers/resolve` zawiera `company_name` wyłącznie dla
jednoznacznego wyniku `exact` lub `unique`. Dla `ambiguous` i `not_found` nazwa
oraz `customer_ref` pozostają puste; API nie zwraca danych kontaktowych ani
uwierzytelniających.

Po poprawnej weryfikacji SMS
`POST /v1/customers/{customer_ref}/devices/masked` zwraca wyłącznie aktywne
urządzenia potwierdzonego klienta: losowe `device_ref`, producenta i model z
projekcji `MASZYNA → MODEL`, pełny `serial`, zgodne `serial_last4`, bezpieczne
`image_url`, lokalizację i flagę aktywności. Kontrakt spraw obsługuje stare
`device_ref` oraz opcjonalne `device_refs` z deduplikacją i limitem 20.
Szczegółowe reguły ujawniania oraz mapowanie `MODEL.PLIK` opisuje
`docs/bot_identity_directory.md`.

W lokalnym środowisku integracyjnym CHAT_KP wskazuje
`http://ctip-bot-api:8082`. API działa bez portu hosta jako kontrolowany most
dwóch sieci Docker, a sam CHAT_KP nie otrzymuje dostępu do sieci Firebird ani
PostgreSQL CTIP. Szczegóły scenariusza znajdują się w
`docs/bot_identity_directory.md`.

### Zmienne środowiskowe Google Sheets (FLOW)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `GOOGLE_SHEETS_ENABLED` | `false` | Włącza synchronizację workflow z Google Sheets. |
| `GOOGLE_APPLICATION_CREDENTIALS` | *(puste)* | Ścieżka do pliku `service account JSON`; źródło prawdy dla integracji FLOW. |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | *(puste)* | Identyfikator skoroszytu FLOW. |
| `GOOGLE_SHEETS_WORKFLOW_DEVICES_SHEET` | `Urzadzenia_magazyn` | Nazwa zakładki urządzeń workflow. |
| `GOOGLE_SHEETS_CONFIG_LOCK` | `false` | Zmienna legacy, obecnie bez wpływu na runtime; konfiguracja Google Sheets jest read-only w panelu niezależnie od tej flagi. |
| `WORKFLOW_SHEET_STATUS_CACHE_SCHEDULER_ENABLED` | `true` | Włącza scheduler okresowego odświeżania lokalnego cache statusów arkusza dla modalu `/flow`. |
| `WORKFLOW_SHEET_STATUS_CACHE_REFRESH_INTERVAL_SECONDS` | `900` | Interwał odświeżania lokalnego cache statusów arkusza przez scheduler w tle. |
| `WORKFLOW_SHEET_STATUS_CACHE_STALE_AFTER_SECONDS` | `1800` | Próg oznaczania danych cache jako nieświeżych w UI `/flow`. |

### Zmienne środowiskowe modułu CTIP AI Asystent (test)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `OPENAI_API_CHAT_KP` | *(puste)* | Główny klucz OpenAI dla chatu Ksero-Partner (fallback runtime dla Responses API). |
| `OPENAI_API_KEY` | *(puste)* | Dodatkowy fallback klucza OpenAI; preferowane jest użycie `OPENAI_API_CHAT_KP` lub sekretu `assistant.openai_api_key` w `admin_setting`. |
| `PGDATABASE` | `ctip_test` | Asystent zapisuje historię i audyt wyłącznie do bazy testowej `ctip_test` podczas pracy lokalnej. |
| `FB_MODE`, `FB_HOST`, `FB_DATABASE` | `local`, `127.0.0.1`, `/tmp/test_ms.fdb` | Narzędzia `firebird_read` i `firebird_business_read` działają na lokalnym Firebird testowym, bez połączeń do hostów produkcyjnych. |
| `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_SHEETS_SPREADSHEET_ID` | *(puste)* | Narzędzie `sheets_read` używa konta serwisowego i testowego arkusza workflow; zalecane odseparowanie od skoroszytów produkcyjnych. |
| `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` | lokalne testowe | Narzędzie `ctip_schema_read` odczytuje metadane tabel/kolumn/relacji FK ze schematu PostgreSQL `ctip` (bez odczytu rekordów biznesowych). |

### Zmienne środowiskowe Firebird v-maintenance
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `FB_V_HOST` | `127.0.0.1` | Host lokalnej kopii bazy v-maintenance. |
| `FB_V_PORT` | `3050` | Port usługi Firebird v-maintenance. |
| `FB_V_DATABASE` | `/tmp/test_vmaintenance.fdb` | Ścieżka lokalnej bazy v-maintenance. |
| `FB_V_USER`, `FB_V_PASSWORD` | `SYSDBA`, `masterkey` | Dane logowania do bazy v-maintenance. |
| `FB_V_CHARSET` | `UTF8` | Kodowanie sesji Firebird v-maintenance. |
| `FB_V_ROLE` | *(puste)* | Rola Firebird v-maintenance (opcjonalnie). |

### Zmienne środowiskowe źródeł Naprawa KP/xxxx
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `KP_CSV_DIRECTORY` | `inbox/ewidencja` | Katalog wejściowy CSV dla oznaczeń `/R/`. |
| `KP_CSV_PATTERN` | `DPLAC*.csv` | Wzorzec wyszukiwania pliku CSV w katalogu wejściowym. |
| `KP_EMAIL_LOOKBACK_MONTHS` | `5` | Domyślne zawężenie źródła e-mail (`CMAIL`) do ostatnich miesięcy. |

Kopię lokalną (po podmontowaniu źródłowego pliku `.fdb`) można wykonać skryptem:
`python scripts/firebird_clone_local.py --force`.

Analiza rzeczywistego przeplywu Menadzera Serwisu (model -> magazyn -> PZ -> proforma -> FV oraz zlecenie serwisowe -> webpanel -> czesci `ZPOZYCJA` -> FV serwisowa -> zamkniecie) i zakres zmian po aktualizacji KSeF sa opisane w `docs/firebird/proces_sprzedazy_ms.md`. Do lokalnej diagnostyki klienta MS nalezy uzywac aliasu `BAZAMS_TEST`; aktualna `.env.test` moze wskazywac inny plik niz alias obslugiwany przez kontener Firebird.

Biezacy stan modułu FLOW, formularzy testowych i decyzji architektonicznych zapisano dodatkowo w `docs/projekt/flow_status_2026-03-16.md`.

Dla dashboardu `/contracts` oraz modalu workflow w `/flow` zrodlem wyboru urzadzen jest teraz Firebird `MAGAZYN` dla magazynu `28`, filtrowany do pozycji z dodatnim dostepnym stanem (`ILOSC - IL_REZ > 0`). Jeżeli rekord magazynowy nie ma wypelnionych pol `MARKA` / `MODEL` / `ID_MODEL`, backend wyciąga producenta, model, numer seryjny i numer wew z tekstu `NAZWA`, a dla wybranych wariantow Ricoh dodatkowo normalizuje zapis modelu do postaci zgodnej z tabela `MODEL`. Drugi tor oparty bezposrednio o tabele `SERIAL` nie jest jeszcze aktywnym katalogiem handlowca, ale workflow ma juz przygotowany model identyfikacji `source_type + source_row`, zeby oba adaptery mogly wspolistniec bez kolizji rekordow. Dawny import z arkusza Google pozostaje tylko jako tor kompatybilnosci dla starszych rekordow workflow i recznej synchronizacji urzadzenia.

Akcja `POST /admin/contracts/action`, endpoint workflow `POST /admin/contracts/forms/{id}/workflow/client` oraz automat `SUBMITTED` korzystaja z biezacej konfiguracji Firebird odczytywanej bezposrednio z `.env`. Zapis pozostaje zablokowany, dopoki aktywne środowisko nie ustawi `FB_ALLOW_WRITES=true`; panel administratora pokazuje tę flagę w trybie tylko do odczytu. W trybie `local` zapis moze wskazywac zarowno plik w repozytorium, jak i jawnie ustawiona absolutna sciezke testowa hosta, pod warunkiem ze plik istnieje.

Widok `/genform` korzysta teraz z danych `GET /admin/contracts/dashboard?forms_scope=all&include_devices=0`, aby pokazywać ten sam kontekst procesu co `/flow`: status jako checklista wykonanych etapów, kolumnę zbiorczą klienta (`nazwa`, `NIP`, `e-mail`, `telefon`, `adres`), stan Menadżera Serwisu oraz status GRENKE z datą ostatniej zmiany statusu. Akcje wiersza obejmują `Wyświetl`, `Dodaj urządzenie`, `Stwórz proformę` i `Dezaktywuj`; dezaktywacja usuwa powiązania workflow/rezerwacje arkusza, ale nie usuwa wcześniej utworzonego klienta Firebird. W modalu workflow `/genform` dostępny jest też zapis statusu biznesowego GRENKE przez endpoint `POST /admin/contracts/forms/{id}/workflow/status`, a modal proformy renderuje podsumowanie workflow i ładuje listę użytkowników faktury niezależnie od wcześniejszego otwierania modalu urządzeń.

Akcja synchronizacji urzadzenia w `/contracts` wykorzystuje arkusz `Urzadzenia` jako zrodlo numeru seryjnego, ewidencji i modelu. Przy odblokowanym zapisie Firebird potrafi dopisac brakujacy wpis `MASZYNA` oraz szkic pozycji `MAGAZYN` w lokalnej kopii Firebird, korzystajac z domyslnych identyfikatorow `FB_WAREHOUSE_CLIENT_ID` i `FB_WAREHOUSE_ID`. Modal workflow w `/flow` zapisuje wybor urzadzen w tabelach CTIP `form_workflow_case` i `form_workflow_device` (z cenami `netto/brutto`) i juz na etapie `Zapisz wybor urzadzen` aktualizuje rezerwacje w arkuszu Google wskazanym przez aktywna konfiguracje środowiskową (`GOOGLE_SHEETS_*` z `.env`); zapis ustawia notatke automatycznej rezerwacji, imie i nazwisko opiekuna bez e-maila, numer formularza CTIP, techniczne kolumny `CTIP_FORM_ID`, `CTIP_WORKFLOW_CASE_ID`, `STATUS HANDLOWY (LEGACY)` oraz jasnoczerwone tlo calego wiersza. Jezeli arkusz ma osobna kolumne `STATUS REZERWACJI`, to tam trafia wartosc `04. Rezerwacja GRENKE`; zwykla kolumna `STATUS` pozostaje bez zmian, zeby nie gubic informacji o etapie zerowki. Lista urzadzen w workflow jest dodatkowo wzbogacana o aktualny `STATUS` z arkusza Google i aktywne rezerwacje z innych formularzy CTIP: urzadzenie zapisane w innej aktywnej sprawie jest oznaczane jako zarezerwowane, pokazuje inicjaly osoby i numer formularza, a checkbox wyboru pozostaje zablokowany. Sam modal nie czyta juz Google Sheets przy kazdym otwarciu: backend korzysta z lokalnej tabeli `ctip.workflow_sheet_status_cache`, ktora jest odswiezana recznie endpointem `POST /admin/contracts/workflow/sheet-status-refresh` i automatycznie przez scheduler w tle. UI pokazuje czas ostatniej udanej synchronizacji, liczbe pozycji w cache i pozwala recznie odswiezyc statusy z arkusza. Proforma dopisuje potem numer dokumentu do tych samych wierszy, a odznaczenie urzadzenia zwalnia jego rezerwacje, czyści pomocnicze kolumny i przywraca domyslny kolor wiersza; dopasowanie wiersza arkusza nie ufa już bezwarunkowo historycznemu `sheet_row` i przy rozjechaniu numeracji fallbackuje po `MS_ID_MAGAZYN_TABLE`/`INDEKS`/`SERIAL`. API `/admin/contracts/forms/{id}/workflow/devices` zwraca też ostrzeżenie, jeżeli po odznaczeniu nie udało się zwolnić wszystkich wcześniejszych rezerwacji w arkuszu (`released_count < oczekiwane`). W module `genform` przycisk `Zapisz do PDF` nadaje teraz jawna nazwe pobieranego pliku (`<numer_proformy>.pdf`), co eliminuje zapisy typu `pdf.txt` w przegladarce, a samo pobieranie działa przez `fetch + blob` z komunikatem błędu backendu zamiast ślepej nawigacji przeglądarki. Sekcja `Konfiguracja bazy -> Google Sheets (FLOW)` pokazuje `spreadsheet_id`, sciezke do `service account JSON` i nazwe zakladki w trybie tylko do odczytu; pole `spreadsheet_id` akceptuje surowe ID albo pelny URL Google Sheets po stronie loadera i zapisuje wyłącznie czyste ID w konfiguracji środowiskowej. Endpoint `POST /admin/google-sheets/test` weryfikuje dostep do skoroszytu i wymaganych naglowkow (`PRODUCENT`, `MODEL`, `INDEKS`, `SERIAL`, `STATUS`, `MS_ID_MAGAZYN_TABLE`, `REZERWACJA GRENKE`, `FAKTURA PROFORMA GRENKE`), a `POST /admin/google-sheets/bootstrap-headers` potrafi przygotowac brakujace naglowki jeszcze przed pierwsza synchronizacja proformy; jezeli arkusz legacy nie ma prawdziwego wiersza naglowka, bootstrap wstawia go jako nowy pierwszy wiersz i przesuwa dane w dol bez utraty rekordow. Ten sam bootstrap ustawia tez format naglowka: zamrozenie pierwszego wiersza, filtr oraz czytelne kolory/szerokosci kolumn. Przy ręcznym zwolnieniu zasobów po `REJECTED_GRENKE` albo usunięciu formularza workflow czyści rezerwacje w tym samym arkuszu; samo ustawienie odmowy tylko wyznacza termin automatycznego zwolnienia.

### Zmienne środowiskowe modułu e-mail (panel administratora)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `EMAIL_HOST` | *(puste)* | Adres serwera SMTP. |
| `EMAIL_PORT` | `587` | Port serwera SMTP (TLS/STARTTLS). |
| `EMAIL_USERNAME` | *(puste)* | Login do serwera SMTP (opcjonalnie). |
| `EMAIL_PASSWORD` | *(puste)* | Hasło do serwera SMTP (opcjonalnie). |
| `EMAIL_SENDER_NAME` | *(puste)* | Nazwa nadawcy w wiadomościach e-mail. |
| `EMAIL_SENDER_ADDRESS` | *(puste)* | Adres nadawcy (From). |
| `EMAIL_USE_TLS` | `true` | Włącza STARTTLS. |
| `EMAIL_USE_SSL` | `false` | Połączenie przez SSL/TLS (port 465). |
| `BLOCK_CLIENT_COMMUNICATIONS` | `false` | Tymczasowo blokuje wysyłkę powiadomień dla klientów (SMS z linkiem i e-maile informacyjne). |

### Zmienne środowiskowe skrzynki automatyzacji umów (IMAP/SMTP)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `MAILBOX_EMAIL_ADDRESS` | *(puste)* | Adres skrzynki używanej przez automatyzację umów. |
| `MAILBOX_EMAIL_PASSWORD` | *(puste)* | Hasło do skrzynki automatyzacji. |
| `MAILBOX_IMAP_HOST` | *(puste)* | Host IMAP skrzynki (odbiór). |
| `MAILBOX_IMAP_PORT` | `993` | Port IMAP SSL. |
| `MAILBOX_SMTP_HOST` | *(puste)* | Host SMTP skrzynki (wysyłka). |
| `MAILBOX_SMTP_PORT` | `465` | Port SMTP SSL. |
| `MAILBOX_SMTP_USE_SSL` | `true` | Wymusza połączenie SMTP przez SSL. |
| `MAILBOX_SMTP_USE_STARTTLS` | `false` | Włącza STARTTLS dla SMTP (nie łączyć z SSL). |
| `CONTRACTS_MAILBOX_SCHEDULER_ENABLED` | `true` | Włącza automatyczny scheduler synchronizacji mailbox -> FLOW przy starcie backendu. |
| `CONTRACTS_MAILBOX_SYNC_INTERVAL_SECONDS` | `300` | Interwał (sekundy) cyklicznego uruchamiania synchronizacji mailboxa. |
| `CONTRACTS_MAILBOX_SYNC_LIMIT` | `60` | Limit liczby najnowszych wiadomości analizowanych w jednym przebiegu automatu. |
| `CONTRACTS_MAILBOX_SYNC_FOLDER` | `INBOX` | Folder IMAP używany przez automat mailboxa. |
| `CONTRACTS_MAILBOX_SYNC_TIMEOUT_SECONDS` | `300` | Timeout pojedynczego przebiegu automatu mailboxa. |
| `CONTRACTS_MAILBOX_SYNC_REPROCESS` | `false` | Gdy `true`, automat przetwarza ponownie wiadomości już zapisane w stanie lokalnym. |
| `CONTRACTS_MAILBOX_ARCHIVE_ROOT` | *(puste)* | Katalog zapisu odszyfrowanych umów PDF (np. `D:\\archiwum_dok` na Windows). |

### Zmienne środowiskowe panelu administratora (`app/api/routes/admin_*`)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `ADMIN_SECRET_KEY` | *(puste)* | Opcjonalny klucz (Fernet, base64) do szyfrowania wartości poufnych zapisywanych w `ctip.admin_setting`. |
| `ADMIN_SESSION_TTL_MINUTES` | `60` | Czas życia tokenu sesji administratora (w minutach). |
| `ADMIN_SESSION_REMEMBER_HOURS` | `72` | Czas życia sesji, gdy użytkownik wybierze opcję „Zapamiętaj mnie” (w godzinach). |
| `ADMIN_PANEL_URL` | `http://localhost:8000/admin` | Publiczny adres logowania używany w e-mailach i SMS z danymi kont. |
| `FORM_PUBLIC_BASE_URL` | `http://localhost:8000` | Publiczny adres bazowy używany do budowy linków `/formularz/{token}`; dla wdrożenia poza LAN ustaw `https://form.twoja-domena.pl`. W runtime moze byc nadpisany przez sekcje `Obsługa formularza` w panelu `/admin`. |
| `GRENKE_APP_BASE_URL` | `https://newonline.leasingoptymalny.pl` | Bazowy adres aplikacji GRENKE; backend buduje z niego URL typu `/kalkulacja/{key}` dla przycisku `Wniosek GRENKE` w `/genform`. |
| `GRENKE_API_BASE_URL` | `https://newonline.leasingoptymalny.pl/API` | Bazowy adres API GRENKE używany przez backend do prefillu (`setSession.php`, `calculate.php`, `saveCalculation.php`). |
| `GRENKE_TIMEOUT_SECONDS` | `12` | Timeout (sekundy) pojedynczego wywołania HTTP do API GRENKE. |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:8000,http://127.0.0.1:8000,http://testserver` | Lista originów CORS rozdzielona przecinkami; backend automatycznie dopina origin z `ADMIN_PANEL_URL` i `FORM_PUBLIC_BASE_URL`. |
| `AUTH_COOKIE_NAME` | `ctip_session` | Nazwa ciasteczka `HttpOnly` dla sesji paneli WWW. |
| `AUTH_COOKIE_SECURE` | `false` | Wymusza przesyłanie ciasteczka sesji wyłącznie po HTTPS. |
| `AUTH_COOKIE_SAMESITE` | `lax` | Polityka `SameSite` ciasteczka sesji (`lax`, `strict`, `none`). |
| `AUTH_COOKIE_DOMAIN` | *(puste)* | Opcjonalna domena ciasteczka sesji. |
| `AUTH_COOKIE_PATH` | `/` | Ścieżka ciasteczka sesji. |
| `LOGIN_FAILURE_LIMIT` | `5` | Maksymalna liczba nieudanych prób logowania dla pary adres IP i konto w aktywnym oknie. |
| `LOGIN_FAILURE_WINDOW_MINUTES` | `15` | Długość okna blokady logowania po przekroczeniu limitu. |
| `PANEL_ALLOWED_NETWORKS` | `127.0.0.0/8,::1/128,192.168.0.0/24` | Sieci dopuszczone do bezpośredniego dostępu do aplikacji. Testowy `.env.test.example` dodaje `172.16.0.0/12`, aby umożliwić komunikację wyłącznie między lokalnymi sieciami Docker; konfiguracja produkcyjna nie powinna rozszerzać listy bez analizy sieci. |

Logowanie do `/auth/login` i `/admin/auth/login` ustawia obecnie dwa transporty tej samej sesji:
- bezpieczniejsze ciasteczko `HttpOnly` dla przeglądarki,
- dotychczasowy token JSON zachowany dla zgodności z istniejącym frontendem i testami (`X-Admin-Session`).

W bazie jest przechowywany wyłącznie skrót SHA-256 tokenu. Nieudane logowania podlegają limitowi, a historyczne trasy `/calls`, `/contacts` i `/sms/*` nie są publikowane; ich zabezpieczone odpowiedniki działają pod `/operator/api/*`.

Warstwa HTTP dodaje teraz również podstawowe nagłówki bezpieczeństwa (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`) oraz `Cache-Control: no-store` dla paneli i endpointów logowania.

### Lista kontrolna przed uruchomieniem
1. Utwórz/aktywuj środowisko `.venv` i zainstaluj zależności: `python3 -m venv .venv`, następnie `source .venv/bin/activate` oraz `pip install -r requirements.txt`.
2. Dla pracy lokalnej skopiuj `.env.test.example` do `.env.test`, pozostaw `PGDATABASE=ctip_test`, `PBX_HOST=127.0.0.1`, `SMS_TEST_MODE=true` i wygeneruj `ADMIN_SECRET_KEY` (`python - <<<'import secrets, base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'`).
3. Przed migracją potwierdź `PGHOST=127.0.0.1` i `PGDATABASE=ctip_test`, wykonaj backup, sprawdź `alembic current`, `alembic heads` i dopiero wtedy uruchom `set -a && source .env.test && set +a && alembic upgrade head`.
   Jeżeli panel `/contracts` zwraca błąd `UndefinedColumnError` dla `ctip.form_request.archive_bucket`, oznacza to brak pełnych migracji i należy ponowić `alembic upgrade head` dla `ctip_test`.
4. Dodaj pierwszego administratora, np. w SQL: `INSERT INTO ctip.admin_user (email, role, password_hash, is_active) VALUES (...)`; skrót hasła wygeneruj funkcją `hash_password` z `app.services.security`.
5. Zweryfikuj instalację: `source .venv/bin/activate && pytest` oraz testowe logowanie do `/admin/auth/login` (ciasteczko `HttpOnly` lub nagłówek `X-Admin-Session`).

### Uruchamianie kolektora w WSL z pliku `.env.test`
W środowiskach Windows Subsystem for Linux lokalny start repozytorium powinien korzystać z `.env.test` (format `KEY=VALUE`). Przed startem `collector_full.py` oraz `sms_sender.py` należy wczytać zmienne, np.:
```bash
set -a
source .env.test
set +a
python collector_full.py
```
Równolegle można uruchomić moduł SMS, korzystając z tych samych zmiennych środowiskowych:
```bash
python sms_sender.py
```
Procedura wymaga wcześniejszego zainstalowania zależności opisanych w sekcji „Instalacja i uruchomienie na Linux”.

## Procedura inicjalizacji CTIP
1. Po zestawieniu gniazda TCP (lokalnie domyślnie `127.0.0.1:5525`, produkcyjnie `192.168.0.11:5524`) wyślij polecenie `aWHO`, aby sprawdzić odpowiedź centrali. Prawidłowa centrala Slican NCP melduje się komunikatem w formacie `aOK NCP-000 NO03914 v1.23.0140/15 2025.10.10 01:54'59`.
2. Po potwierdzeniu odpowiedzi wykonaj dokładnie jedno `aLOGA <PIN>` (np. `aLOGA 1234`). Komenda aktywuje monitorowanie wszystkich numerów. Projekt zakłada pojedynczą aktywną sesję – aby zakończyć nasłuch, należy zamknąć gniazdo TCP/IP; centrala nie udostępnia komendy wylogowującej.
3. Wszystkie komendy wysyłane do centrali muszą mieć prefiks `a`. Do szybkich testów lokalnych można wykorzystać `telnet 127.0.0.1 5525`, a do diagnostyki produkcji wyłącznie po jawnym poleceniu użytkownika `telnet 192.168.0.11 5524`.
4. `collector_full.py` automatyzuje powyższą sekwencję, loguje identyfikator centrali i przerywa pracę, gdy `aLOGA` zostanie odrzucone (np. z powodu aktywnej sesji innego kolektora).

## Przygotowanie bazy danych
Schemat `ctip` musi być dostarczony zewnętrznie (migracje Alembic lub dump z katalogu `docs/baza/`). Od wersji 0.2 kolektor nie wykonuje operacji DDL – podczas startu weryfikuje obecność wymaganych kolumn (`calls`, `call_events`, `sms_out`, `ivr_map`, `contact`, `contact_device`). W przypadku braków `collector_full.py` przerwie pracę i wypisze listę brakujących kolumn. Przed lokalnym uruchomieniem kolektora ustaw `.env.test` (na podstawie `.env.test.example`) i wykonaj `alembic upgrade head`; produkcyjny `.env` pozostaje tylko po stronie serwera. Wszystkie znaczniki czasu w tabelach `calls`, `call_events`, `contact`, `sms_out` i `sms_template` muszą mieć typ `timestamp with time zone`, ponieważ backend zapisuje daty w UTC i udostępnia je operatorowi – brak strefy czasowej kończy się błędem 500 podczas wysyłki SMS lub pobierania statystyk.

Przykładowe wstawienie rekordu:
```sql
INSERT INTO ctip.ivr_map(digit, ext, sms_text)
VALUES (1, '203', 'Klient oczekuje na rozmowę z działem serwisu.');
```

Jeżeli schemat został utworzony przez konto `postgres`, należy przekazać własność i prawa operacyjne użytkownikowi aplikacyjnemu (`appuser`), np.:
```sql
ALTER TABLE ctip.sms_out OWNER TO appuser;
ALTER TABLE ctip.calls OWNER TO appuser;
ALTER TABLE ctip.call_events OWNER TO appuser;
ALTER TABLE ctip.contact OWNER TO appuser;
ALTER TABLE ctip.contact_device OWNER TO appuser;
ALTER TABLE ctip.ivr_map OWNER TO appuser;
ALTER SEQUENCE ctip.sms_out_id_seq OWNER TO appuser;
ALTER SEQUENCE ctip.calls_id_seq OWNER TO appuser;
ALTER SEQUENCE ctip.call_events_id_seq OWNER TO appuser;
```

## Instalacja i uruchomienie na Linux
1. Utwórz wirtualne środowisko: `python -m venv venv && source venv/bin/activate`.
2. Zainstaluj zależności: `pip install psycopg[binary]`.
3. Ustaw zmienne środowiskowe (np. w pliku `systemd` lub skrypcie startowym).
4. Uruchom kolektor: `python collector_full.py`.
5. Uruchom moduł SMS (jeśli używany): `python sms_sender.py`.

Rekomenduje się uruchomienie obu procesów pod nadzorem `systemd` lub innego menedżera usług. W przypadku `systemd` kontroluj usterki poprzez `Restart=always` oraz logowanie do `journalctl`.

## Aktualizacja produkcji na Windows Server (PowerShell)
Środowisko produkcyjne dla tego projektu działa na Windows Server.

1. Aktualizacja kodu:
```powershell
cd C:\sciezka\do\ctip
git checkout main
git pull --ff-only origin main
```
2. Przygotowanie środowiska Python:
```powershell
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```
3. Backup baz przed wdrozeniem (zalecane):
```powershell
.\scripts\windows\backup_prod_databases.ps1 -InstallDir D:\CTIP -GbakPath "C:\Program Files\Firebird\Firebird_2_5\bin\gbak.exe"
```
Skrypt tworzy backup PostgreSQL (`pg_dump` + opcjonalnie `pg_dumpall --globals-only --no-role-passwords`) oraz backup Firebird (`gbak -b -g`) do katalogu `D:\CTIP\backups\prod_<timestamp>`. Dane logowania Firebird przekazuje przez `ISC_USER` i `ISC_PASSWORD`, bez umieszczania hasła w argumentach procesu.
Każde narzędzie zapisuje osobne logi STDOUT/STDERR w `D:\CTIP\backups\prod_<timestamp>\_logs`, a skrypt po wykonaniu waliduje istnienie i rozmiar wygenerowanych plików backupu.
Jeżeli `pg_dumpall --globals-only --no-role-passwords` nie powiedzie się, skrypt domyślnie zgłasza ostrzeżenie i kontynuuje; użyj `-FailOnPgGlobalsError`, aby traktować ten przypadek jako błąd krytyczny.
4. Co dalej po `pip install -r requirements.txt`:
```powershell
# 1) Ustaw/zweryfikuj .env produkcyjne (szczególnie Firebird)
# FB_MODE=network
# FB_HOST, FB_PORT, FB_DATABASE, FB_USER, FB_PASSWORD, FB_CHARSET, FB_ROLE

# 2) Migracje bazy PostgreSQL
alembic upgrade head

# 3) Restart usług aplikacji (produkcyjne nazwy usług)
Restart-Service "CTIP-Web"
Restart-Service "CollectorService"
Restart-Service "CTIP-SMS"
```
5. Weryfikacja po restarcie:
```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty StatusCode
```
6. W panelu administratora (`/admin`) przejdź do sekcji `Konfiguracja bazy`, zweryfikuj konfigurację Firebird odczytaną z `.env` i wykonaj `Testuj połączenie`.

Aktualne nazwy usług produkcyjnych (Windows Server):
- `CollectorService` – Collector Service (`collector_full.py`)
- `CTIP-SMS` – moduł wysyłki SMS
- `CTIP-Web` – backend/panel web

## Backend API (FastAPI)
Warstwa REST udostępniająca dane CTIP i kolejkę SMS została zrealizowana w katalogu `app/`. Do pracy wymaga zależności opisanych w `pyproject.toml` (`fastapi`, `uvicorn`, `sqlalchemy`, `psycopg`, `pydantic-settings`).

### Uruchomienie środowiska
1. Zainstaluj pakiet w trybie deweloperskim: `pip install -e .`
2. Zastosuj aktualną migrację bazy: `psql $DATABASE_URL -f docs/baza/migrations.sql`.
3. Uruchom serwer: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` (wariant `--host 0.0.0.0` udostępnia panel w sieci LAN; w celu zawężenia dostępu ustaw odpowiednie IP).

### Dostępne endpointy (wersja prototypowa)
- `GET /health` – status serwera.
- `GET /calls` – lista połączeń z filtrami (kierunek, status, wewnętrzny, zakres dat, wyszukiwanie tekstowe).
- `GET /calls/{call_id}` – szczegóły połączenia (zdarzenia CTIP, historia SMS).

## Panel administracyjny (HTMX + Alpine)
- Strona startowa znajduje się pod `/admin`; serwuje ją moduł `app/web/admin_ui.py`, korzystając z szablonów w `app/templates/admin/` oraz statycznych zasobów `app/static/admin/`.
- Layout i nawigacja są sterowane przez Alpine.js (`admin.js`), a sekcje ładowane dynamicznie przez HTMX (`/admin/partials/...`). Udostępnione moduły obejmują Dashboard, konfigurację PostgreSQL/Firebird/CTIP/SerwerSMS/E-mail/obsługi formularza, narzędzie „Naprawa KP/xxxx” oraz pełny widok „Użytkownicy”.
- Lokalne zasoby panelu administratora (`/static/admin/*`, `section_switcher.*`, favicon) są wersjonowane parametrem `?v=<app.version>`, aby po wdrożeniu nowego backendu przeglądarka nie trzymała starej kopii `admin.js` lub CSS.
- Strona główna (`/`) działa jako centralny punkt logowania: formularz używa API `/auth/login`, a po poprawnym logowaniu przekierowuje na `/choice`.
- Widok `/choice` pokazuje sekcje przypisane do konta (`admin`, `operator`, `generator`) oraz stałą opcję `Chat ksero-partner` prowadzącą do `/assistant`; ekran obsługuje też wylogowanie (`/auth/logout`).
- Każdy panel roboczy (`/admin`, `/operator`, `/genform`, `/contracts`, `/assistant`) zawiera szybki powrót do `/choice`.
- Logowanie odbywa się przez `/admin/auth/login` (formularz na stronie głównej). Token sesji (`X-Admin-Session`) zapisywany jest w `localStorage`, a kolejne żądania HTMX/fetch automatycznie go dołączają.
- W razie odpowiedzi 401/403 podczas ładowania sekcji panel samoczynnie czyści token, wylogowuje użytkownika i sygnalizuje wygaśnięcie sesji.
- Dashboard udostępnia aktywne akcje dla kafelków statusu: `Testuj połączenie` (baza danych), `Edytuj konfigurację` oraz `Diagnostyka` (CTIP i SerwerSMS). Diagnostyka pobiera dane z `/admin/status/<moduł>` i wyświetla je w panelu bocznym.
- Formularze konfiguracji: PostgreSQL + Firebird Menadżer Serwisu + Google Sheets (FLOW) + Firebird v-maintenance + źródło CSV (`/admin/partials/config/database`), CTIP (`/admin/partials/config/ctip`), SerwerSMS (`/admin/partials/config/sms`), E-mail (`/admin/partials/config/email`) oraz Obsługa formularza (`/admin/partials/config/form-handling`) zapewniają testy połączeń tam, gdzie ma to sens (`/admin/status/database`, `/admin/firebird/test`, `/admin/google-sheets/test`, `/admin/google-sheets/bootstrap-headers`, `/admin/firebird/test-vmaintenance`, `/admin/sms/test`, `/admin/email/test`). Sekcje połączeniowe korzystają z `.env` i są read-only w UI; wyjątki edytowalne to `form_handling`, operacyjna część backupu i `kp_repair.email_lookback_months`.
- W sekcji `Konfiguracja bazy` oba formularze Firebird są jawnie rozdzielone:
  - Menadżer Serwisu korzysta z konfiguracji środowiskowej `firebird` i testu `POST /admin/firebird/test`,
  - v-maintenance korzysta z konfiguracji środowiskowej `firebird_vmaintenance` i testu `POST /admin/firebird/test-vmaintenance`.
- Sekcja SerwerSMS zawiera monitor pracy `sms_sender`: widok logu (`/admin/partials/sms/logs`) prezentuje końcówkę pliku `docs/LOG/sms/sms_sender_<YYYY-MM-DD>.log`, a tabela historii (`/admin/partials/sms/history`) odświeża ostatnie wysyłki z `ctip.sms_out` i pozwala filtrować je po statusach (`NEW`, `RETRY`, `SENT`, `ERROR`, `SIMULATED`). Formularz wysyłki testowej normalizuje numer do formatu E.164 (obsluga prefiksu wyjscia na zewnetrzna linie `0`, prefiksu `00` oraz korekta `+0`), a poprawna próba (w trybie testowym lub produkcyjnym) natychmiast pojawia się w logu i historii.
- Sekcja CTIP udostępnia podgląd na żywo (`/admin/partials/ctip/live`) z filtrowaniem po wewnętrznych numerach oraz wbudowanym formularzem konfiguracji; kafelek na dashboardzie oferuje zarówno edycję parametrów, jak i szybkie przejście do widoku live. Aktualizacje są dostarczane kanałem WebSocket (`/admin/ctip/ws`), który pomija ramki keep-alive typu `T`.
- Sekcja Automatyzacje IVR (`/admin/partials/ctip/ivr-map`) pozwala zarządzać mapowaniami cyfr IVR na numery wewnętrzne, treścią automatycznych SMS i ich aktywnością. Każda operacja (utworzenie, aktualizacja, usunięcie) jest audytowana i natychmiast dostępna dla kolektora bez restartu.
- Sekcja SMS dla dzwoniacych (`/admin/partials/call-sms`) udostepnia konfiguracje scenariuszy przychodzacych/wychodzacych (odebrane, nieodebrane, ponowne), tryb ograniczen „Nigdy / Po X dniach / Zawsze”, liste numerow wykluczonych, scenariusz po godzinach pracy wyzwalany numerem wewnetrznym (np. 500) oraz masowa wysylke do unikalnych numerow z historii polaczen.
- Sekcja E-mail umożliwia konfigurację serwera SMTP (host, port, logowanie, nadawca), test połączenia oraz wysłanie wiadomości testowej na wskazany adres (`/admin/email/test`). Wynik jest prezentowany w UI i zapisywany w audycie.
- Sekcja Konfiguracja bazy zawiera parametry Menadżera Serwisu (`/admin/config/firebird`, test: `/admin/firebird/test`), konfigurację Google Sheets dla FLOW (`/admin/config/google-sheets`, test: `/admin/google-sheets/test`, przygotowanie nagłówków: `/admin/google-sheets/bootstrap-headers`), parametry Firebird v-maintenance (`/admin/config/firebird-vmaintenance`, test: `/admin/firebird/test-vmaintenance`) oraz konfigurację źródła CSV dla oznaczeń `/R/` (`/admin/config/kp-repair-source`, test: `/admin/kp-repair/csv-source/test`). Pola połączeniowe są odczytywane z `.env`; zapis z panelu pozostaje dostępny tylko dla filtra `email_lookback_months`.
- W trybie `lokalna` panel automatycznie testuje połączenie pod `127.0.0.1` i wykorzystuje ścieżkę `FB_LOCAL_COPY_PATH`; pola hosta i ścieżki bazy sieciowej są wyszarzone.
- Sekcja `Naprawa KP/xxxx` (`/admin/partials/kp-repair`) udostępnia raport ilości `/V /E /R`, czyszczenie markerów (`/admin/kp-repair/clear`) i retagowanie wg źródeł (`/admin/kp-repair/rebuild`); raporty i rollbacki są zapisywane do `inbox/ewidencja`.
- Operacje `kp-repair` wykonują zapytania Firebird w wątku roboczym (`asyncio.to_thread`), aby nie blokować event-loop FastAPI i nie zamrażać panelu podczas generowania raportu.
- UI `Naprawa KP/xxxx` pokazuje pasek postępu (orientacyjny) i licznik czasu operacji; klient ma limit czasu żądania 10 minut, a backend limit wykonania 900 s (HTTP 504 po przekroczeniu).
- Sekcja Kopie zapasowe (`/admin/partials/backups`) udostępnia konfigurację harmonogramu (`06:00`, `20:00`), retencji czasowej (lokalnie 21 dni, Office 365 14 dni) i zakresu archiwizacji. Każde żądanie backupu PostgreSQL uruchamia `pg_dump` w formacie niestandardowym, waliduje wynik przez `pg_restore --list` i zapisuje go w archiwum jako `postgresql/ctip.dump`. Widok historii pokazuje status i potwierdzenie (suma kontrolna `.sha256`).
- Konfiguracja backupu jest rozdzielona: parametry integracyjne i sekrety (`BACKUP_DEFAULT_LOCAL_DIR`, Office 365, SQL Optima) pochodzą z `.env`, a panel zapisuje tylko harmonogram, retencję i pozostałe ustawienia operacyjne przez `/admin/backup/config`. Pelne wykonanie (`dry_run=false`) jest aktywne automatycznie na hostcie zgodnym z `BACKUP_PRODUCTION_HOST`, a w innych srodowiskach mozna je jawnie wlaczyc lub wylaczyc przez `BACKUP_EXECUTION_ENABLED` (`true`/`false`).
- Harmonogram zapisany w panelu (`schedule_morning`, `schedule_evening`) jest realizowany automatycznie przez scheduler backendu; dla kazdego slotu wykonywane jest maksymalnie jedno zadanie na dobe.
- Scheduler backupu mozna globalnie wlaczyc/wylaczyc przez `BACKUP_SCHEDULER_ENABLED` (domyslnie `true`).
- Wysyłka do SharePoint zapisuje archiwum CTIP w `BackupKP/CTIP`, logiczną kopię Firebird `.fbk` w `BackupKP/Menadzer_Serwisu/prod` i trzy natywne kopie Optimy `.bak` w `BackupKP/Optima`; duże pliki są przesyłane fragmentowo przez sesję Microsoft Graph. Firebird jest weryfikowany próbą odtworzenia `gbak`, a Optima przez `RESTORE VERIFYONLY WITH CHECKSUM` oraz kontrolny restore z `DBCC CHECKDB PHYSICAL_ONLY`. Status `PARTIAL` jawnie sygnalizuje błąd chmury lub retencji, zachowując lokalne artefakty.
- Narzędzia backupu można wskazać przez `PG_DUMP_PATH`, `PG_RESTORE_PATH`, `FIREBIRD_GBAK_PATH` i `OPTIMA_SQLCMD_PATH`; limity wykonania ustalają odpowiednie zmienne `BACKUP_*_TIMEOUT_SECONDS`. Szczegóły prób odtworzenia opisują `docs/instal/bezpieczenstwo_backup_tls.md` oraz `docs/instal/backup_firebird_optima_retencja.md`.
- Dla Windows dostępny jest skrypt tworzący lokalną strukturę katalogów backupu: `scripts/windows/create_backup_structure.ps1` (domyślny root: `D:\Backup_CTIP_MS_optima`).
- Dla SharePoint dostępny jest skrypt konfigurujący czytelny widok biblioteki backupów: `scripts/windows/setup_sharepoint_backup_view.ps1` (grupowanie po folderach i sortowanie malejąco po dacie modyfikacji).
- Dla SharePoint dostępny jest też skrypt tworzący osobną stronę dashboardu backupów (`SitePages/BackupKP-Dashboard.aspx`) z tabelą linków do widoków: `scripts/windows/create_sharepoint_backup_dashboard.ps1` (układ strony `Article`; ponowne uruchomienie dla istniejącej strony wymaga przełącznika `-OverwritePage`; skrypt automatycznie wykrywa wariant struktury folderów `BackupKP/...` lub bez tego prefiksu i generuje linki z parametrami `id=<folder>` oraz `viewid=<GUID>`, aby otwierać widok od razu w docelowym katalogu).
- Oba skrypty SharePoint automatycznie wykrywają bibliotekę dokumentów (priorytet: parametr `LibraryTitle`, potem `Backup_KP`, `Documents`, `Shared Documents`, `Dokumenty`).
- Oba skrypty SharePoint wspierają dwa tryby logowania: `device login` (użytkownik) oraz `app-only` (`ClientSecret`). W trybie app-only skrypt próbuje najpierw wariantu `-ClientSecret` bez `-Tenant`, następnie wariantu z `-Tenant` (kompatybilność z różnymi wersjami PnP), a przy błędzie lub braku dostępu do witryny (`Unauthorized` po logowaniu) automatycznie przechodzi na fallback OAuth (`-AccessToken`) i zwraca rozszerzoną diagnostykę autoryzacji.
- Domyślny `ClientId` w skryptach (`31359c7f-bd7e-475c-86db-fdb8c937548e`) jest przeznaczony do logowania delegowanego (`DeviceLogin`). Dla trybu `app-only` należy podać własny `ClientId` i `ClientSecret` z rejestracji aplikacji Entra ID.
- W nagłówku sekcji backupów znajduje się szybki link do witryny SharePoint (`https://kseropartner.sharepoint.com/sites/Backups`).
- Sekcja Książka adresowa (`/admin/partials/contacts`) udostępnia CRUD kontaktów z wyszukiwarką po numerze, nazwisku, e-mailu i identyfikatorze Firebird; formularze pozwalają przypisać numer wewnętrzny, notatki operacyjne oraz pole `firebird_id` wykorzystywane do mapowania z bazą Firebird.
- Sekcja Użytkownicy zawiera blok `Powiadomienia CRM` z niezależnymi ustawieniami `Handel: SMS/e-mail` oraz `Pozostałe (Serwis, Umowy i liczniki, Inne): SMS/e-mail`. Nowa sprawa otrzymuje link bezpośredni `/crm?case=<ref>`; kanał bez numeru telefonu lub adresu e-mail jest pomijany i odnotowywany na osi sprawy.
- Generator formularzy działa jako osobny flow pod adresem `/genform` (poza panelem `/admin`) i jest dostępny po zalogowaniu kontem `operator` albo `admin`. Moduł korzysta z API `/admin/forms`, generuje jednorazowe linki `/formularz/{token}`, pozwala ustawić datę ważności formularza (domyślnie 7 dni), zapisuje w bazie wyłącznie hash tokenu i przechowuje dane klienta w postaci zaszyfrowanej (Fernet, `ADMIN_SECRET_KEY`).
- Sekcja `/admin -> Obsługa formularza` nie zastępuje ekranu `/genform`; służy wyłącznie do ustawienia adresu publicznego formularza oraz szablonów komunikatów:
  - SMS do klienta po wygenerowaniu linku,
  - e-mail do klienta z linkiem,
  - e-mail potwierdzający po zapisaniu formularza,
  - SMS do wszystkich aktywnych handlowców oznaczonych w panelu po zapisaniu formularza.
- Domyślne treści po instalacji są przygotowane pod komunikację z klientem Ksero Partner i nie zawierają fallbacku do lokalnego adresu; po zapisaniu własnej konfiguracji administrator może je nadpisać bez ingerencji w kod.
- Szablony obsługują podstawienie zmiennych takich jak `form_url`, `expires_at`, `customer_name`, `company_name` i `sender_name`; walidacja odbywa się przy zapisie konfiguracji, a generator pobiera wartości z `ctip.admin_setting` (namespace `form_handling.*`).
- Sekcja pokazuje również podgląd renderu każdej wiadomości na przykładowych danych, dzięki czemu można od razu wychwycić błędny link lub nieczytelny komunikat bez generowania realnego formularza.
- API listy formularzy (`GET /admin/forms`) toleruje historyczne rekordy z niestandardową domeną e-mail (np. `*.local`) i nie przerywa pracy całego widoku przez pojedynczy wpis legacy.
- Publiczny formularz `/formularz/{token}` działa etapowo: krok 1 (dane firmy z rozbiciem adresu siedziby i korespondencyjnego, polami `Nr telefonu firmowy`, `E-mail firmowy`, obowiązkowym polem `E-mail do e-faktur` oraz opcją „taki sam jak adres siedziby” i „Kopiuj e-mail”), krok 2 (jeden lub wielu reprezentantów: `E-mail reprezentanta`, `Telefon reprezentanta`, PESEL, automatyczne uzupełnianie daty urodzenia, wybór rodzaju dokumentu z listy, daty dokumentu z wpisem ręcznym `dd-mm-rrrr` lub kalendarzem; pola dat dokumentu akceptują wpis ciągły `ddmmrrrr`, automatycznie wstawiają separatory i po podaniu daty wydania proponują datę ważności `+10 lat` z możliwością ręcznej korekty), krok 3 (podsumowanie i końcowe potwierdzenie). Dane trafiają do systemu dopiero po kliknięciu `Potwierdź i wyślij`.
- Parser `POST /formularz/{token}` traktuje niezaznaczone checkboxy przeglądarki (`correspondence_same_as_registered`, `consent`) jako wartości boolowskie `false`, zamiast pustych stringów; dzięki temu brak pola w payloadzie nie kończy już żądania `422` przez walidację typu boolean.
- Błędy walidacji publicznego formularza są zwracane po polsku, z nazwą pola; dla reprezentantów komunikat wskazuje numer osoby i pole (np. `Reprezentant 1: pole „Imię” musi mieć co najmniej 2 znaki.`), zamiast surowych komunikatów Pydantic po angielsku.
- Po zatwierdzeniu formularza system wysyła e-mail potwierdzający do klienta, dodaje do kolejki SMS powiadomienie dla wszystkich aktywnych użytkowników oznaczonych jako `Handlowiec`, a następnie automatycznie sprawdza Menadżer Serwisu po NIP: jeśli klient istnieje, zapisuje powiązanie do workflow; jeśli nie istnieje i zapis do Firebird jest włączony, tworzy kartę klienta i zapisuje wynik w `ctip.form_request.ms_status`.
- Przycisk `Kopiuj link` w `/genform` korzysta z Clipboard API, a gdy środowisko blokuje kopiowanie (np. brak `https`), automatycznie przełącza się na fallback `execCommand("copy")`.
- Ekran `/genform` udostępnia akcje `Wyświetl`/`Dodaj urządzenie`/`Stwórz proformę`/`Dezaktywuj` dla każdego wniosku oraz okno szczegółów: dla statusu `SUBMITTED` prezentowane są odszyfrowane dane klienta w układzie zgodnym z końcowym podsumowaniem formularza, z przyciskiem `Kopiuj` przy każdym polu oraz akcjami `Dane zostały wpisane`, `Wniosek GRENKE`, `Drukuj` i `PDF`; przycisk `Dane zostały wpisane` uruchamia jednorazową wysyłkę e-mail do klienta z informacją o dalszych krokach (Grenke + Autenti + płatność weryfikacyjna `0,01 zł`) i po pierwszej wysyłce blokuje się na stałe dla danego formularza, a `Wniosek GRENKE` działa tylko po etapie `PROFORMA_CREATED` i otwiera nowe okno przeglądarki z izolowaną sesją oraz prefillem API (`full` albo `partial`) przygotowanym przez backend. W modalu szczegółów znajdują się bloki `Krok 2 GRENKE – dane do wklejenia` (NIP, telefon/e-maile z formularza, stałe: liczba pracowników `10`, inny numer konta `00000000000000000000000000`; operator przechodzi do kroku 2 fizycznym kliknięciem `Wypełnij wniosek online` w GRENKE) oraz `Krok 3 GRENKE – osoby uprawnione do podpisania umowy` (mapowanie reprezentantów z CTIP do pól GRENKE, z datami w formacie `YYYY-MM-DD` i oznaczeniem pól wymagających ręcznego uzupełnienia, np. stan cywilny). Modal `Dodaj urządzenie` pokazuje tabelę pozycji Firebird z filtrami, rezerwacją arkusza Google i ręcznym wpisem cen `netto/brutto`, a modal `Proforma` pokazuje osobno dane firmy z formularza oraz aktualnego nabywcy dokumentu (`bank` albo `klient z formularza`), pozwala zapisać PDF bez otwierania podglądu A4 i usuwać proformę w pełnym zakresie: kasuje dokument z Firebird, czyści numer w arkuszu Google i zwalnia numer proformy, ale pozostawia aktywną rezerwację urządzeń. Nad tabelą formularzy widoczna jest też notka o ostatniej synchronizacji e-mail GRENKE (`mailbox_sync` z dashboardu), dzięki czemu operator od razu widzi aktualność automatu. Dla pozostałych statusów widoczna jest czytelna informacja operacyjna (np. „formularz został wysłany, ale nie został jeszcze wypełniony”).
- Tabela generatora zawiera kolumny `Utworzone przez` oraz `Status MS`, dzięki czemu od razu widać operatora/administratora, który wygenerował formularz, oraz wynik automatycznej synchronizacji klienta z Menadżerem Serwisu.
- W wariancie produkcyjnym poza LAN rekomendowane jest wystawienie samych formularzy przez `app.public_forms_app:app`; `/genform` i cały panel pozostają wtedy na adresie wewnętrznym, a klient otrzymuje wyłącznie link `https://form.twoja-domena.pl/formularz/<token>`.
- Zweryfikowany wariant produkcyjny dla `form.ksero-partner.com.pl` bazuje na usłudze `CTIP-FormsPublic` na `127.0.0.1:8100`, witrynie IIS z bindingiem `https *:443:form.ksero-partner.com.pl`, lokalnych regułach `web.config` oraz braku globalnej reguły `rewrite/globalRules` dla tego hosta w `applicationHost.config`.
- Ważność publicznego certyfikatu kontroluje `scripts/windows/check_public_tls.ps1`; skrypt wykonuje pełny handshake i zapisuje ostrzeżenia do dziennika zdarzeń Windows `Application`.
- Moduł publicznego formularza (`app/web/form_ui.py`) ładuje szablony po ścieżce absolutnej względem repozytorium (`app/templates`), dzięki czemu usługa `CTIP-FormsPublic` nie zależy od bieżącego katalogu roboczego procesu podczas startu pod NSSM/IIS.
- Backend SQLAlchemy korzysta ze sterownika `postgresql+psycopg`; na Windows aplikacje ASGI ustawiają zgodną pętlę `WindowsSelectorEventLoopPolicy` przed importem tras bazodanowych. Decyzja wynika z produkcyjnego przypadku, w którym `asyncpg` kończył `/formularz/*` błędem `ConnectionDoesNotExistError`, mimo poprawnego połączenia `psql`/`psycopg`.
- Dashboard `/contracts` (Obsługa umów) pozostaje technicznym widokiem integracji: pobiera formularze `SUBMITTED`, weryfikuje klienta po NIP w lokalnej kopii Firebird (`KLIENT`) oraz pokazuje dostępne pozycje magazynowe Firebird dla magazynu `28`, z podziałem na pozycje bez rezerwacji i częściowo zarezerwowane.
- Widok `/assistant` udostępnia historię rozmów, streaming odpowiedzi SSE, panel „Źródła danych” i sekcję „Wnioski o zmiany”; backend blokuje zwykłe próby modyfikacji danych i kieruje je do workflow akceptacji. Pytania o porównanie urządzeń w Firebird MS z arkuszem Google `Zerowki_prod/Urzadzenia_magazyn` są obsługiwane deterministycznym narzędziem `workflow_devices_audit`: asystent pokazuje raport rozbieżności, a operator może kliknąć `Zapisz do urzadzenia_chat`, co czyści i wypełnia wyłącznie zakładkę roboczą `urzadzenia_chat`.
- W czacie dostępny jest wybór profilu „pracownika AI” dla nowej rozmowy (`ksero_partner_analyst`, `opiekun_klienta`, `diagnosta_bazy_ms`); wybrany profil jest zapisywany w wątku i wpływa na styl odpowiedzi.
- Chat wspiera zapytania biznesowe w języku naturalnym przez narzędzie `firebird_business_read` (intencje: `devices_by_company`, `monthly_average_print_by_model`, `company_monthly_print_summary`, `top_models_by_volume`, `device_monthly_print_by_serial`, `active_devices_on_contracts`, `active_devices_on_contracts_count`, `contract_settlement_period_explainer`), np. „Wyświetl urządzenia firmy Steico”, „Pokaż średni miesięczny wydruk modelu MPC3004”, „Pokaż top modele po wydrukach”, „Pokaż historię serialu RNP12345”, „Podaj mi ilość urządzeń aktywnych na umowach” albo „Jak działa rozliczanie dat umów (zakresy vs pojedyncza data)?”.
- Chat korzysta też z lokalnego indeksu wiedzy `firebird_knowledge_read` (`docs/firebird/knowledge/firebird_ms_knowledge.json`), dzięki czemu pytania o strukturę i kontekst bazy MS nie wymagają każdorazowej analizy źródeł i zużywają mniej tokenów.
- Chat potrafi wysłać raport jako załącznik e-mail przez `email_send_report` (format `csv|json|txt`) na podstawie ostatniego wyniku narzędzia danych; wysyłka używa konfiguracji `admin/config/email` (ta sama skrzynka systemowa co powiadomienia CTIP).
- Moduł posiada mechanizm uczenia użytkownika: po udanych odpowiedziach biznesowych aktualizuje profil `assistant_user_profile.preferences` (statystyki trafnych intencji, aliasy firm/modeli i przykłady promptów), a runtime wykorzystuje tę pamięć do szybszego mapowania naturalnych pytań na gotowe intencje.
- Widok `/flow` rozwija ten sam backend o zapis stanu sprawy w tabelach `ctip.form_workflow_case`, `ctip.form_workflow_device` i `ctip.workflow_sheet_status_cache`: operator może otworzyć workflow formularza, potwierdzić podstawowe tworzenie klienta na potrzeby proformy, zapisać klienta Menadżera Serwisu, przypisać do formularza jedno lub wiele urządzeń po stronie CTIP na bazie pozycji `MAGAZYN` z Firebird, recznie wprowadzic cene `netto` i `brutto` dla kazdego urzadzenia, ustawić status biznesowy sprawy (`WAITING_SIGNATURE`, `APPROVED_ORDER`, `REJECTED_GRENKE`, `RENTAL_WITHOUT_GRENKE`, `CLOSED_NOT_REALIZED`) w osobnym modalu `/genform`, zapisać ustalenia dowozu (data/okno/kontakt/notatka), a nastepnie wystawic realna proforme w lokalnej Firebird i otworzyc jej podglad A4. Wewnetrznie wybor urzadzen jest juz identyfikowany para `source_type + source_row`, co przygotowuje drugi adapter oparty o `SERIAL` bez psucia dzialajacego toru `firebird_magazyn_28`. Lista urzadzen w modalu korzysta ze statusow zapisanych w lokalnym cache arkusza i udostepnia przycisk `Odswiez statusy z arkusza`, zamiast czytac Google Sheets przy kazdym otwarciu. Lista formularzy w `/flow` ma tez akcje `Usun` z potwierdzeniem (`window.confirm`), korzystajaca z `DELETE /admin/forms/{id}`. Sekcja `Obsluga urzadzen` pokazuje ten sam magazynowy stan Firebird, ale bez edycji samej pozycji handlowej.
- Sekcja `Harmonogram dowozow` w `/flow` pokazuje plan dostaw w zakresie dat i umozliwia akcje operacyjne: przejscie do edycji wpisu w modalu workflow, przeniesienie wpisu o +/-1 dzien albo na wskazana date (`Przenies...`) oraz usuniecie wpisu z potwierdzeniem.
- Informacje logistyczne dowozu sa przechowywane po stronie CTIP (`form_workflow_case`) i nie sa drukowane w dokumencie proformy.
- W modalu workflow proformy jest opcja `Proforma na bank`; przy wlaczonej opcji dokument jest wystawiany na klienta bankowego `GRENKELEASING Sp. z o.o.` (domyslnie `ID_KLIENT=855`, NIP `782-22-75-815`), a po odznaczeniu na klienta z formularza. Przy kliknieciu `Utworz proforme` UI zawsze pokazuje okno potwierdzenia z aktualnym odbiorca i wybranym `Uzytkownikiem MS`, zeby ograniczyc pomylki przy testach i pracy operatora. Bezposrednio pod tym przelacznikiem widoczna jest lista wybranych urzadzen z cenami: przed wystawieniem proformy ceny `netto` i `brutto` mozna jeszcze korygowac z tego miejsca, a po zapisaniu dokumentu lista przechodzi w tryb tylko do odczytu. Wpisywanie cen nie przebudowuje juz calej tabeli po kazdym znaku, wiec kursor pozostaje w aktywnym polu.
- Widok `/device` jest osobnym panelem operacyjnym obslugi urzadzen: pokazuje ostatnie przyjecia `PZ` na magazyn `28`, wykrywa rozjazdy pomiedzy `ZAKPOZYCJA`, `MAGAZYN`, `SERIAL` i `MASZYNA`, raportuje jakosc danych w tabeli `MODEL` (duplikaty po normalizacji, braki `RODZAJ`, `KOLOR`, `PLIK`) oraz wyznacza status procesu i nastepny krok operatora; mapowanie `SERIAL`/`MASZYNA` odbywa sie per pozycja `ZAKPOZYCJA` (preferencja `ID_SERIAL`), co eliminuje powielanie tego samego egzemplarza w ramach jednej PZ.
- Modul zapisowy `/device` utrzymuje dedykowana kartoteke `AUTO/XXXX` dla magazynu `28` (nazwa pozycji: `MARKA + MODEL`, `RODZAJ='2. Towar inny'`, wymuszenie numerow seryjnych) i pozwala tworzyc przyjecie `PZ` single/batch z wpisaniem `S/N` oraz numeru `KP` (`EWIDENCJA`) do procesu `SERIAL`, a nastepnie automatycznie zaklada rekord `MASZYNA` i laczy go przez `SERIAL.ID_MASZYNA`.
- Osobne menu `Wykup BNP` w `/device` nie zmienia istniejacego formularza przyjecia PZ. Operator wyszukuje pojedyncze urzadzenie po `MASZYNA.SERIAL/SERIAL2`, widzi klienta i kartoteki ze wszystkich magazynow, a pole nazwy pozycji otrzymuje edytowalna podpowiedz `Producent Model S/N:serial`. Brakujaca pozycje `WKP/<numer>/BNP` moze utworzyc na magazynie `27` ze stanem `0`. Finalizacja wymaga numeru i daty dokumentu BNP, nazwy oraz ceny netto; aktualizuje `MASZYNA.EWIDENCJA` z `KP/...` na `WKP/...`, tworzy jednoelementowe `PZ` na magazyn `27` i wymaga wyniku stanu `1`, bez tworzenia rekordu `SERIAL`. Dodatni stan, niejednoznaczny serial, kolizja indeksu albo istniejacy dokument blokują zapis. Szczegółowy przebieg opisuje `docs/projekt/wykup_bnp.md`.
- Formularz `/device` autouzupelnia numeracje ewidencyjna na podstawie systemowej sekwencji (`SERIAL`/`MASZYNA`), obsluguje reczna zmiane kolejnosci i pyta czy kolejne numery maja kontynuowac od recznej wartosci czy pozostac przy sekwencji systemowej; poprawione zostaly tez przypadki usuniecia calej listy i ponownego dodawania pozycji, a dla prefiksu `KP/` numeracja pomija outliery testowe (np. skrajne numery 6-8 cyfrowe) i trzyma dominujacy format.
- W formularzu batch `/device` dostawca i model sa wybierane z jednego pola autocomplete z filtrem dynamicznym (`NIP`/`nazwa`/`ID` dla dostawcy, `ID/marka/model` dla modelu); po wyborze dostawcy formularz automatycznie uzupelnia sekcje danych dostawcy i pokazuje lokalny status wyboru, a akcja `Dodaj nowego dostawce` najpierw sprawdza duplikat po NIP/nazwie i dopiero przy braku rekordu tworzy nowy wpis. Pole ewidencji w tabeli pozycji jest rozdzielone na baze numeracji (`KP/xxxx/`, ciagnieta systemowo) oraz reczny dopisek operatora, bez osobnego przycisku przeliczania. Po kliknieciu `Utworz przyjecie PZ` UI pokazuje lokalny status wysylki/bledu oraz wyrazne podsumowanie zapisu (numer PZ, liczba pozycji, podglad utworzonych wierszy), a odswiezanie danych wykonuje sie w tle.
- Sekcja synchronizacji AUTO umozliwia teraz tryb pelnej synchronizacji wszystkich modeli oraz synchronizacje pojedynczego modelu wybieranego z listy `marka + model`; formularz nowego modelu domyslnie podstawia grupe `Druk` (jezeli jest na liscie) i ma domyslnie wlaczone tworzenie/uzupelnienie kartoteki `AUTO`.
- Dotychczasowa kartoteka magazynowa pozostaje bez ingerencji; automatyzacja tworzy i aktualizuje tylko pozycje z prefiksem `AUTO/`.
- Przy tworzeniu proformy z `/flow` backend preferuje recznie zapisane ceny `price_gross` albo `price_net` z workflow CTIP; jesli nie podano wyceny recznej, jako fallback wykorzystuje ceny pozycji `MAGAZYN` w Firebird (`CENA_BRUTTO` / `CENA_NETTO`) zapisane w wybranej pozycji magazynowej. UI blokuje wystawienie proformy, jezeli operator ma niezapisane zmiany w wyborze urzadzen albo dla wybranego urzadzenia nie ma dodatniej ceny netto/brutto.
- Strona `/flow/proforma-wizualizacja` prezentuje referencyjny układ dokumentu handlowego na podstawie wzorca `inbox/FPROFORMA.pdf`; to wzorzec widoku do późniejszego spięcia z danymi Firebird i generacją PDF.
- Strona `/flow/proforma-wizualizacja1` jest drugim wariantem, celowo bliższym oryginalnemu wydrukowi FastReport z Menadżera Serwisu: ma blok nabywcy u gory, uklad metadanych, sekcje `Sprzedawca/Nabywca`, podpisy, uwagi i blok ostrzeżenia w stopce.
- Obie strony wizualizacji mają przycisk `Zapisz PDF A4`, który uruchamia przeglądarkowy wydruk z arkuszem stylów ustawionym pod format A4 w orientacji pionowej.
- Strona `/flow/proforma/{id}` renderuje juz rzeczywista proforme odczytana z aktywnej konfiguracji Firebird (runtime z panelu administratora, ten sam co dla workflow/proformy); domyslny wariant finalny to `?variant=final` (alias zgodnosciowy: `?variant=v1`) i jest zblizony do wzorca `inbox/FPROFORMA.pdf`.
- Endpointy `/flow/proforma/{id}` oraz `/flow/proforma/{id}/pdf` korzystaja z zaleznosci `get_db_session` importowanej z `app.api.deps`; bledna podmiana importu na `app.db.session` powoduje awarie startu `CTIP-Web` (usluga Windows moze pozostac `Running`, ale `/health` na porcie `8000` nie odpowiada).
- Backend zapisuje tez fizyczny plik PDF proformy do `inbox/faktura/generated/proforma_<ID>.pdf`; endpoint `/flow/proforma/{id}/pdf` zwraca pobranie jako strumien bajtów PDF (z nagłówkiem `Content-Disposition`) i nie zależy od `FileResponse`, a w workflow CTIP kolumna `proforma_pdf_path` trzyma sciezke do zapisanego pliku. Generator PDF buduje uklad A4 wzorowany na `inbox/FPROFORMA.pdf` (w tym rodzina fontow Verdana z fallbackami systemowymi), a sekcje naglowka/tabeli/podsumowania sa pozycjonowane pod produkcyjny wzorzec MS (`Faktura nr <numer_proformy>.pdf`), zamiast prostego dumpu tekstowego; nazwa pobieranego pliku jest aliasem numeru dokumentu (np. `20/proforma/2026` -> `20_proforma_2026.pdf`). Wiersz pozycji proformy w wariancie `v1` jest renderowany jednolinijkowo (bez dodatkowego `nr.wew`), z szerokosciowym przycinaniem tekstu i mniejsza typografia sekcji podsumowania/uwag/stopki, aby uniknac nachodzenia tresci; kwoty w wierszu `Razem` sa celowo wyrownane typograficznie do wiersza `wg stawki 23 %`, etykieta `Data zakończenia dostaw/usług` jest w jednej linii, blok adresowy `Sprzedawca/Nabywca` ma zaciesnione odstepy pionowe, a ciagla linia sekcji podsumowania pozostaje na poziomie `v4` przy jednoczesnym przesunieciu calego tekstu pod nia o `2 mm` w dol.
- Sekcja Użytkownicy umożliwia przypisanie dostępu do sekcji (`admin`, `operator`, `generator`, `delivery`) niezależnie od roli konta; strona główna i API respektują te uprawnienia przy prezentacji i autoryzacji modułów. Rola `serwisant` domyślnie otrzymuje dostęp do sekcji `delivery`. Dodatkowo konto może być oznaczone znacznikiem biznesowym `Handlowiec`, niezależnym od roli i sekcji, powiązane z użytkownikiem Menadżera Serwisu przez listę `Użytkownik MS`, a także posiadać konfigurację IMAP (admin-only) przypisaną per użytkownik do odczytu nagłówków wiadomości przez moduł chatu.
- Treści SMS zawierające link jednorazowy lub potwierdzenie wypełnienia formularza są maskowane w historii panelu (`Treść ukryta`), aby nie ujawniać danych wrażliwych.
- Operatorzy logują się tym samym panelem co administratorzy i mają dostęp do Dashboardu, widoku CTIP, Książki adresowej (w trybie edycji bez możliwości usuwania kontaktów) oraz Generatora formularzy. Pozostałe sekcje pozostają zarezerwowane dla roli `admin`.
- W CTIP Live dostępny jest szybki edytor kontaktu: po wskazaniu zdarzenia można jednym formularzem zaktualizować dane numeru (imię, nazwisko, firma, e-mail, `firebird_id`, notatki), a wynik jest natychmiast synchronizowany z główną książką adresową.
- Sekcja Użytkowników wymaga podania telefonu komórkowego; udostępnia listę kont administratorów/operatorów, formularz tworzenia nowych użytkowników, edycję w modalach, reset hasła, zmianę statusu aktywności oraz usuwanie kont (blokada usunięcia własnego lub ostatniego administratora). Po utworzeniu konta oraz po resecie hasła automatycznie wysyłany jest e-mail i SMS z danymi logowania, a odpowiedź API zwraca pola `sms_queued` i `sms_recipient`, aby panel potwierdzał kolejkowanie wiadomości. Frontend buduje ten komunikat w komponencie sekcji Użytkownicy zarówno dla tworzenia konta, jak i resetu hasła, więc oba scenariusze korzystają z tej samej ścieżki UI. Konto można dodatkowo oznaczyć jako `Handlowiec`, a formularze po statusie `SUBMITTED` wysyłają wtedy powiadomienia SMS do całej aktywnej grupy handlowców z poprawnym numerem telefonu. Dodatkowe pole `Użytkownik MS` ładuje bieżącą listę operatorów Menadżera Serwisu z aktywnej konfiguracji Firebird i zapisuje trwałe mapowanie do konta CTIP. Formularz użytkownika zawiera też konfigurację IMAP (host/port/login/folder/SSL oraz hasło trzymane jako sekret), którą może zmieniać wyłącznie administrator i która jest wykorzystywana przez `imap_read` w czacie. Do panelu mogą logować się wyłącznie konta z rolą `admin`.
- Aby uruchomić panel lokalnie:
  1. `source .venv/bin/activate`
  2. `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
  3. Otwórz przeglądarkę na `http://localhost:8000/admin`
- Domyslna polityka startu calego systemu:
  1. domyslnie uruchamiaj wyłącznie srodowisko testowe (`.env.test`, baza `ctip_test`, lokalny Firebird, mock CTIP);
  2. zasoby `192.168.0.8` i `192.168.0.11` traktuj jako produkcyjne i nie używaj ich bez jawnego polecenia;
  3. start produkcyjny z `.env` jest celowo blokowany bez jawnego potwierdzenia;
  4. do testow preferuj `./ctiptest` albo `./run_test_stack_tmux.sh`.
- Aby uruchomić caly stos testowy jednym poleceniem:
  1. `./ctiptest`
  2. alternatywnie: `./run_test_stack_tmux.sh`
  3. dla wariantu z lokalnym Firebird: `ENV_FILE=.env.test ./run_server_with_firebird.sh`
- Aby uruchomic caly stos produkcyjny jednym poleceniem:
  1. `ALLOW_PRODUCTION_START=true ./run_stack_tmux.sh`
  2. albo `ALLOW_PRODUCTION_START=true ./run_server_with_firebird.sh`
  3. opcjonalnie wymuszenie startu kontenera Firebird: `ALLOW_PRODUCTION_START=true START_FIREBIRD=always ./run_server_with_firebird.sh`
  4. wdrozenie produkcyjne powinno wynikac z commita GitHub oraz jawnej konfiguracji `.env` po stronie serwera.
- Implementacja kolejnych sekcji (konsola SQL, raporty) jest prowadzona zgodnie z dokumentem `docs/projekt/panel_admin_ui.md`.
- `GET /contacts/{number}` oraz `GET /contacts?search=` – dane i wyszukiwarka kartoteki kontaktów.
- `GET /admin/config/firebird`, `PUT /admin/config/firebird` – odczyt konfiguracji połączenia Firebird oraz blokada zapisu z panelu (`PUT` zwraca `423 Locked`; źródłem prawdy jest `.env`).
- `POST /admin/firebird/test` – test logowania do bazy Firebird z audytem (`config_firebird_test`, wymaga roli `admin`).
- `GET /admin/config/firebird-vmaintenance`, `PUT /admin/config/firebird-vmaintenance` – odczyt konfiguracji połączenia Firebird v-maintenance oraz blokada zapisu z panelu (`423 Locked`).
- `POST /admin/firebird/test-vmaintenance` – test logowania do bazy Firebird v-maintenance.
- `GET /admin/config/kp-repair-source`, `PUT /admin/config/kp-repair-source` – odczyt katalogu/wzorca CSV z `.env` oraz zapis wyłącznie filtra czasu (`email_lookback_months`) dla źródła e-mail.
- `POST /admin/kp-repair/csv-source/test` – test katalogu CSV z wykrywaniem najnowszego pliku wejściowego.
- `GET /admin/kp-repair/summary`, `POST /admin/kp-repair/clear`, `POST /admin/kp-repair/rebuild` – raport, czyszczenie i retagowanie `MASZYNA.EWIDENCJA` dla markerów `/V /E /R` (raporty i rollbacki trafiają do `inbox/ewidencja`).
- `GET /admin/contacts`, `POST /admin/contacts`, `PUT /admin/contacts/{contact_id}`, `DELETE /admin/contacts/{contact_id}` – zarządzanie wpisami książki adresowej (wymaga nagłówka `X-Admin-Session` i roli `admin`); obsługa pola `firebird_id` umożliwia powiązanie z rekordami bazy Firebird.
- `GET /admin/contacts/by-number/{number}` – wyszukaj kontakt po numerze MSISDN (wymagane `X-Admin-Session`; dostęp dla roli `admin` i `operator`).
- `POST /auth/login`, `GET /auth/me`, `POST /auth/logout` – centralne logowanie strony głównej i wybór sekcji na podstawie przypisanych uprawnień.
- `GET /auth/profile`, `PUT /auth/profile` – podgląd i edycja własnych danych użytkownika (imię, nazwisko, e-mail, numer wewnętrzny, telefon) z poziomu `/choice`.
- `POST /auth/profile/change-password` – zmiana własnego hasła z poziomu `/choice` (wymagania: min. 9 znaków, duża litera, cyfra, znak specjalny).
- `GET /assistant/workers` – lista dostępnych profili pracowników AI do wyboru przy zakładaniu rozmowy.
- `POST /assistant/chats`, `GET /assistant/chats`, `GET /assistant/chats/{id}` – tworzenie i przegląd własnych wątków asystenta (z polem `worker_key` profilu pracownika AI).
- `POST /assistant/chats/{id}/messages` – wysłanie promptu do asystenta (JSON lub SSE `text/event-stream` przy `stream=true`); narzędzia: `firebird_read`, `firebird_business_read`, `firebird_knowledge_read`, `workflow_devices_audit`, `sheets_read`, `imap_read`, `ctip_schema_read` (odczyt) oraz `email_send_report` (wysyłka załączników raportowych przez systemowe SMTP CTIP).
- `POST /assistant/change-requests/{id}/execute-workflow-devices-chat-sheet` – wykonuje zaakceptowaną albo własną akcję operatora po audycie urządzeń i zapisuje przygotowany staging do zakładki `urzadzenia_chat`; endpoint nie zapisuje do `Urzadzenia_magazyn`.
- `POST /assistant/change-requests`, `GET /assistant/change-requests` – ręczne tworzenie i lista wniosków o zmiany (admin widzi wszystkie, użytkownik tylko własne).
- `POST /assistant/change-requests/{id}/approve`, `POST /assistant/change-requests/{id}/reject` – decyzja administratora dla workflow zmian.
- `GET /assistant/insights/weekly` – tygodniowy raport usprawnień asystenta (tylko admin).
- `GET /assistant/users/{user_id}/learning-profile`, `PUT /assistant/users/{user_id}/learning-profile` – podgląd i ręczna korekta pamięci uczenia asystenta per użytkownik (tylko admin).
- `GET /admin/forms`, `POST /admin/forms`, `GET /admin/forms/{id}`, `DELETE /admin/forms/{id}`, `POST /admin/forms/{id}/notify-data-entered` – lista/generowanie/podgląd/usuwanie jednorazowych formularzy oraz jednorazowa wysyłka e-mail „Dane zostały wpisane” (wymagane uprawnienie sekcji `generator`).
- `GET /genform` – osobny ekran handlowca do generowania i podglądu formularzy.
- `GET /flow` – główny widok roboczy FLOW dla obsługi umów i urządzeń po zalogowaniu.
- `GET /device` – wydzielony widok roboczy tylko dla obsługi urządzeń po zalogowaniu.
- `GET /admin/device/dashboard` – dane dashboardu `/device`: ostatnie przyjecia `PZ` urzadzen, ocena spojnosci `MAGAZYN` / `SERIAL` / `MASZYNA`, audyt tabeli `MODEL`, reguly procesu oraz rekomendowane akcje dla kolejnego kroku obslugi egzemplarza.
- `POST /admin/device/catalog/sync` – tworzy/uzupelnia kartoteke `AUTO/XXXX` na magazynie `28` dla wskazanych `ID_MODEL` (albo hurtowo dla wszystkich modeli z wypelniona marka/model); endpoint wspiera tryb `only_missing=true|false`.
- `GET /admin/device/models`, `POST /admin/device/models` – wyszukiwarka modeli do formularza `/device` i szybkie tworzenie nowego `MODEL` (z opcjonalnym utworzeniem kartoteki `AUTO`).
- `GET /admin/device/model-form-options` – listy pomocnicze dla formularza modelu (`marka` z konfiguracji PG, `grupa`/`rodzaj` z Firebird `MODEL`).
- `GET /admin/device/suppliers`, `POST /admin/device/suppliers` – wyszukiwarka dostawcow (`KLIENT`) po nazwie/NIP/ID (filtrowanie rekordow dostawcow po `TYP='Dostawca'` lub `RODZAJ=4`, z obsluga dlugich nazw i polskich znakow, bez bledow `string truncation` dla zapytan liczbowych) oraz szybkie dodanie podstawowego dostawcy z poziomu `/device` (z oznaczeniem rekordu jako dostawca).
- `GET /admin/device/intake/defaults` – zwraca domyslna numeracje ewidencyjna (`prefix`, `next_number`, `suggested`) do autouzupelniania formularza batch; dla `KP/` mechanizm ignoruje historyczne outliery numeracji.
- `POST /admin/device/intake` – tworzy pojedyncze przyjecie `PZ` (`ZAKUPY + ZAKPOZYCJA`) dla wskazanego modelu i rekordu `AUTO`, wpisuje `S/N` oraz `EWIDENCJA`, tworzy `SERIAL`, tworzy `MASZYNA` i laczy rekordy.
- `POST /admin/device/intake/batch` – tworzy jeden dokument `PZ` z wieloma pozycjami (`ILOSC=1` na kazda pozycje), wykrywa duplikaty `SERIAL`/`EWIDENCJA`, uzupelnia brakujace ewidencje numeracja systemowa, obsluguje opcjonalna cene zakupu netto i dla kazdego egzemplarza tworzy `SERIAL + MASZYNA`.
- `GET /admin/device/bnp-buyout/lookup` – wyszukuje urządzenie po numerze seryjnym, zwraca klienta, bieżące `KP`, kartoteki magazynowe, propozycję `WKP` oraz blokady procesu wykupu BNP.
- `POST /admin/device/bnp-buyout/catalog` – idempotentnie tworzy brakującą kartotekę `WKP/<numer>/BNP` na magazynie `27` ze stanem `0`.
- `POST /admin/device/bnp-buyout/complete` – aktualizuje `MASZYNA.EWIDENCJA`, nazwę i indeks kartoteki oraz tworzy PZ wykupu BNP z ilością `1`, ceną netto i VAT 23%; dostawcę rozpoznaje po NIP BNP.
- `GET /flow/proforma-wizualizacja` – wzorcowa wizualizacja proformy oparta o dokument referencyjny `inbox/FPROFORMA.pdf`, z przyciskiem `Zapisz PDF A4`.
- `GET /flow/proforma-wizualizacja1` – wierniejsza wizualizacja proformy z układem zbliżonym do oryginalnego wydruku Menadżera Serwisu, również z przyciskiem `Zapisz PDF A4`.
- `GET /flow/proforma/{proforma_firebird_id}?variant=(base|final|v1)` – podglad rzeczywistej proformy zapisanej w lokalnej Firebird, z przyciskiem `Zapisz PDF A4` (`final` jest wariantem domyslnym i docelowym, `v1` pozostaje aliasem zgodnosciowym).
- `GET /flow/proforma/{proforma_firebird_id}/pdf` – backendowy plik PDF proformy zapisany w `inbox/faktura/generated/`.
- `GET /contracts`, `GET /admin/contracts/dashboard` – dashboard „Obsługa umów” i dane integracyjne (formularze SUBMITTED, Firebird, pozycje `MAGAZYN` dla magazynu `28`), wymagane uprawnienie sekcji `generator`; endpoint wspiera `include_devices=true|false` (domyslnie `true`) do kontrolowania kosztownego odczytu pozycji magazynowych oraz `archive_scope=active|accepted|rejected|unfilled|ksero_partner|closed_other` dla menu archiwum GenForm.
- `GET /admin/contracts/forms/{id}/workflow` – szczegóły sprawy workflow formularza `SUBMITTED` (podgląd klienta, stan CTIP, lista urządzeń do wyboru).
- `POST /admin/contracts/workflow/sheet-status-refresh` – recznie odswieza lokalny cache statusow urządzeń z arkusza Google, wykorzystywany przez modal wyboru urządzeń w `/flow`.
- `POST /admin/contracts/forms/{id}/workflow/client` – tworzy albo potwierdza klienta w Menadżerze Serwisu i zapisuje powiązanie po stronie CTIP.
- `POST /admin/contracts/forms/{id}/workflow/devices` – zapisuje wybór urządzeń do sprawy formularza razem z recznie wpisaną ceną `netto` i `brutto` dla kazdego wybranego urzadzenia, a nastepnie od razu aktualizuje rezerwacje w arkuszu GRENKE (`UWAGI`, `REZERWACJA GRENKE`, `FORMULARZ CTIP`, `CTIP_FORM_ID`, `CTIP_WORKFLOW_CASE_ID`, `STATUS HANDLOWY (LEGACY)` oraz kolor wiersza; pole statusu jest ruszane tylko wtedy, gdy arkusz ma osobna kolumne `STATUS REZERWACJI`); gdy sprawa ma status `APPROVED_ORDER`, endpoint uruchamia dodatkowo automat wiązania urządzeń z klientem MS i zapisuje wynik w statusie „Menadżer Serwisu”; endpoint blokuje też zapis urządzenia, które jest już przypięte do innego aktywnego formularza workflow, i zwraca wtedy `409` z numerem formularza blokującego.
- `POST /admin/contracts/forms/{id}/workflow/status` – zapisuje ręczny status GRENKE po stronie CTIP (`WAITING_SIGNATURE`, `APPROVED_ORDER`, `REJECTED_GRENKE`, `RENTAL_WITHOUT_GRENKE`, `CLOSED_NOT_REALIZED`); odmowa nie usuwa historii, tylko ustawia 7-dniowy termin zwolnienia zasobów, decyzje końcowe ustawiają 14-dniowy termin archiwizacji, a `CLOSED_NOT_REALIZED` dodatkowo zwalnia rezerwacje/aktywną proformę i trafia po archiwizacji do menu „Odrzucone inne”.
- `POST /admin/contracts/forms/{id}/workflow/grenke-launch` – przygotowuje i zwraca URL nowego okna formularza GRENKE z prefillem (`prefill_state=full|partial`); endpoint wymaga statusu `SUBMITTED` oraz etapu `PROFORMA_CREATED`, zapisuje przebieg do audytu i zwraca ostrzeżenia z integracji API (`setSession.php` / `calculate.php` / `saveCalculation.php`).
- `POST /admin/contracts/forms/{id}/workflow/release-resources` – ręcznie zwalnia rezerwacje po odmowie GRENKE, usuwa aktywną proformę i zostawia historię formularza oraz urządzeń.
- `POST /admin/contracts/forms/{id}/archive` i `POST /admin/contracts/forms/{id}/archive/extend` – przenoszą formularz do archiwum albo przedłużają termin automatycznego przeniesienia o 7 dni.
- `POST /admin/contracts/forms/{id}/workflow/proforma` – tworzy realna proforme w lokalnej Firebird na podstawie klienta Menadzera Serwisu i urzadzen wybranych w workflow CTIP; zapisuje numer dokumentu, sciezke backendowego PDF w `proforma_pdf_path` i URL podgladu/pobrania dokumentu.
  - endpoint przyjmuje opcjonalne body JSON `{ "for_bank": true|false, "sheet_assignee_id": <id_uzytkownika_ms>|null }`; domyslnie `for_bank=true`, a `sheet_assignee_id` jest wybierane z mapowania konta CTIP -> MS lub fallbacku na login operatora.
  - po utworzeniu proformy endpoint synchronizuje arkusz wskazany przez aktywna konfiguracje środowiskową `GOOGLE_SHEETS_*`.
- `POST /admin/contracts/forms/{id}/workflow/sheet-sync` – recznie ponawia synchronizacje arkusza GRENKE dla sprawy z utworzona proforma (z opcjonalnym `sheet_assignee_id`).
- `POST /admin/contracts/forms/{id}/workflow/sheet-release` – recznie zwalnia rezerwacje arkusza GRENKE dla urzadzen przypisanych do sprawy.
- `POST /admin/contracts/forms/{id}/workflow/delivery` – zapisuje dane dowozu dla sprawy workflow (`delivery_date`, `delivery_time_window`, `delivery_contact_name`, `delivery_contact_phone`, `delivery_notes`).
- `DELETE /admin/contracts/forms/{id}/workflow/delivery` – usuwa dane dowozu przypisane do formularza.
- `GET /admin/contracts/delivery/schedule?day_from=YYYY-MM-DD&day_to=YYYY-MM-DD` – zwraca harmonogram dowozow w zadanym zakresie dat.
- `POST /admin/contracts/delivery/{workflow_case_id}/move` – przenosi wpis harmonogramu na inny dzien.
- `DELETE /admin/contracts/delivery/{workflow_case_id}` – usuwa wpis harmonogramu dla wskazanej sprawy workflow.
- `GET /delivery` – widok modułu „Obsługa dostaw” dostępny przez `/choice` dla sekcji `delivery`.
- `GET /admin/delivery/clients/search?q=...&nip=...` – wyszukuje klienta w Menadżerze Serwisu dla nowej dostawy albo odbioru.
- `GET /admin/delivery/clients/{client_id}/devices` – pobiera aktywne urządzenia klienta MS do planowania odbioru.
- `GET /admin/delivery/devices/available` – pobiera dostępne urządzenia magazynowe MS z informacją o rezerwacjach CTIP.
- `GET /admin/delivery/cases` – lista spraw dostaw i odbiorów (`source=grenke|manual`, `case_type=delivery|pickup`, `status_filter=...`, `include_done=true|false`).
- `GET /admin/delivery/cases/{case_id}` – szczegóły sprawy, urządzenia, zadania, pliki i pliki mailboxa GRENKE.
- `POST /admin/delivery/cases` – ręczne utworzenie sprawy dostawy spoza GRENKE; opcjonalnie używa `firebird_client_id` albo `create_firebird_client=true`.
- `POST /admin/delivery/pickups` – tworzy sprawę odbioru urządzeń od klienta MS bez zmiany powiązań Firebird na etapie planowania.
- `PATCH /admin/delivery/cases/{case_id}` – aktualizacja nazwy, terminu, okna czasowego, kontaktu, notatek i statusu sprawy.
- `POST /admin/delivery/cases/{case_id}/devices` – zapisuje urządzenia sprawy; dla dostaw aktualizuje rezerwacje w arkuszu Google Sheets, jeżeli synchronizacja jest aktywna.
- `GET /admin/delivery/cases/{case_id}/tasks`, `POST /admin/delivery/cases/{case_id}/tasks`, `PATCH /admin/delivery/cases/{case_id}/tasks/{task_id}` – obsługa zadań operacyjnych sprawy.
- `GET /admin/delivery/cases/{case_id}/files`, `POST /admin/delivery/cases/{case_id}/files`, `GET /admin/delivery/files/{file_id}/download` – obsługa plików sprawy.
- `GET /admin/delivery/document-templates` i `POST /admin/delivery/cases/{case_id}/documents/generate` – lista szablonów i generowanie dokumentów DOCX.
- `GET /admin/delivery/grenke-contracts` – lista wpisów kalendarza końców umów GRENKE z filtrami `status_filter`, `date_from`, `date_to` i `q`; odpowiedź zawiera m.in. `grenke_contract_start_date`, daty końca i harmonogram przypomnień.
- `POST /admin/delivery/grenke-contracts/{item_id}/confirm` – potwierdza datę końca umowy i aktywuje przypomnienia.
- `POST /admin/delivery/grenke-contracts/{item_id}/cancel` – anuluje wpis kalendarza końca umowy.
- `POST /admin/delivery/grenke-contracts/reminders/run` – ręczne uruchomienie przypomnień końców umów.
- `GET /formularz/{token}`, `POST /formularz/{token}` – publiczny formularz klienta oparty o jednorazowy token.
- `GET /admin/backup/history` – lista plików kopii zapasowych z katalogu `backups/` (wymaga roli `admin`).
- `GET /admin/backup/config`, `PUT /admin/backup/config` – odczyt konfiguracji modułu kopii zapasowych oraz zapis tylko parametrów operacyjnych (harmonogram, zakres, retencja, tryb składowania); pola integracyjne i sekrety pozostają źródłowane z `.env`.
- `POST /admin/backup/office365/test` – test połączenia OAuth/Graph do SharePoint (z automatycznym ustaleniem `Drive ID` na podstawie `Site ID`, jeśli `Drive ID` nie jest podany).
- `POST /admin/backup/retention/run` – podgląd (`dry_run=true`) albo potwierdzone wykonanie czasowej retencji zarządzanych zestawów lokalnych i Office 365; pełny wynik trafia do audytu.
- `POST /admin/backup/run`, `POST /admin/backup/restore` – inicjacja kopii/przywracania; wykonanie poza produkcją jest blokowane (`403`), dry-run pozostaje dostępny.
- `GET /sms/templates` – lista szablonów (globalnych i użytkownika).
- `POST /sms/templates` – dodawanie szablonów (globalny tylko dla administratora).
- `POST /sms/send` – zapis SMS do kolejki `ctip.sms_out` (treść lub szablon).
- `GET /admin/sms/logs` oraz `GET /admin/sms/history` – JSON wykorzystywany przez monitor SerwerSMS w panelu administratora (wymagany nagłówek `X-Admin-Session`).
- `GET /sms/history` – historia wysyłek z filtrem po numerze/statusie/połączeniu.
- `GET /sms/account` – podstawowe statystyki (liczba wysłanych, oczekujących, błędnych).

## Panel operatora
- Strona `/operator` udostępnia interfejs bazujący na prototypie (`prototype/index.html`) i komunikuje się z backendem REST (`/operator/api/**`). Widok zawiera panel listy połączeń, szczegóły CTIP, dane kontaktu oraz moduł szybkich SMS.
- Lista połączeń normalizuje numery CLIP/CLIR do 9 cyfr (usunięcie prefiksów `0`, `+48`, spacji) i pomija połączenia wyłącznie wewnętrzne, aby operator widział wyłącznie ruch przychodzący/wychodzący z/do abonentów zewnętrznych.
- Dostępne funkcje: filtrowanie listy połączeń (połączenia wewnętrzne są pomijane), podgląd osi czasu CTIP, prezentacja powiązanego kontaktu wraz z numerem Firebird, historia SMS i kolejka szybkiej wysyłki.
- Szybka wysyłka SMS oferuje przyciski aktywnych szablonów (globalnych i operatora), dwa predefiniowane komunikaty znane z prototypu („Aplikacja”, „Liczniki”) oraz tryb własnej wiadomości. Przed wysyłką można wymusić potwierdzenie, a dowolny tekst zapisać od razu jako nowy szablon operatora.
- Moduł szybkich SMS normalizuje numer docelowy do formatu E.164 (obsluga prefiksu wyjscia na zewnetrzna linie `0`, prefiksu `00` oraz korekta `+0`) przed zapisaniem w kolejce `sms_out`.
- Panel nagłówka prezentuje liczbę wysłanych SMS w bieżącym dniu i miesiącu (`GET /operator/api/stats`).
- W prawym dolnym rogu panelu operatora widnieje wersja i data aktualizacji interfejsu (obecnie: 0.2.13 - Aktualizacja 2026-04-09).
- Operator może dodać lub edytować kontakt bezpośrednio z widoku połączenia (`POST/PUT /operator/api/contacts`), a dane logowania wysłane w wiadomościach SMS są ukrywane w historii dla bezpieczeństwa.
- Strona `/operator/settings` udostępnia formularze: edycję profilu operatora (imię, nazwisko, e-mail, numer wewnętrzny, telefon), zmianę hasła oraz zarządzanie własnymi szablonami SMS (dodawanie, edycja, usuwanie). Szablony globalne są widoczne w trybie tylko do odczytu.
- Opcja „Zapamiętaj mnie” przechowuje token sesji w `localStorage` i wydłuża ważność sesji (`ADMIN_SESSION_REMEMBER_HOURS`), natomiast standardowe logowanie używa `sessionStorage`.
- Operatorzy i administratorzy mogą logować się centralnie przez `/auth/login` (strona `/`) lub bezpośrednio przez dedykowane formularze (`/admin/auth/login`, `/operator/auth/login`). Dostępne moduły zależą od przypisanych sekcji konta.
- Historia SMS w szczegółach połączenia bazuje na powiązaniu `call_id` oraz znormalizowanych wariantach numeru (+48, bez prefiksów), więc wpisy SerwerSMS są widoczne nawet wtedy, gdy rekord połączenia zawiera numer bez prefiksu międzynarodowego.
- Dokument referencyjny: `docs/projekt/panel_operator_ui.md`.

### API operatora
- `GET /operator/api/me` – dane zalogowanego operatora.
- `GET /operator/api/profile` – odczyt danych profilu operatora (wraz z rolą).
- `PUT /operator/api/profile` – aktualizacja danych kontaktowych operatora.
- `POST /operator/api/profile/change-password` – zmiana hasła (wymaga podania obecnego hasła; polityka: min. 9 znaków, duża litera, cyfra, znak specjalny).
- `GET /operator/api/calls` – lista połączeń (`limit`, `search`, `direction`).
- `GET /operator/api/calls/{call_id}` – szczegóły połączenia (oś czasu CTIP, kontakt, historia SMS).
- `GET /operator/api/contacts/by-number/{number}` – dane kontaktu na podstawie numeru MSISDN.
- `GET /operator/api/sms/history?number=` – historia wiadomości dla wskazanego numeru.
- `POST /operator/api/sms/send` – dodanie wiadomości do kolejki `sms_out` (wymaga roli `operator` lub `admin`).
- `GET /operator/api/sms/templates` – lista szablonów (globalnych i operatora) wraz z informacją o możliwości edycji.
- `POST /operator/api/sms/templates` – dodanie szablonu operatora.
- `PUT /operator/api/sms/templates/{id}` – edycja własnego szablonu operatora.
- `DELETE /operator/api/sms/templates/{id}` – usunięcie szablonu operatora.
- `GET /operator/api/stats` – bieżące statystyki wysłanych SMS (dzień/miesiąc).
- `POST /operator/api/contacts` oraz `PUT /operator/api/contacts/{id}` – zarządzanie książką adresową bezpośrednio z panelu operatora.

Wszystkie trasy panelu operatora wymagają nagłówka `X-Admin-Session` z ważnym tokenem sesji (rola `operator` lub `admin`); brak nagłówka skutkuje kodem `401 UNAUTHORIZED`.

## Automatyczna wysyłka SMS z IVR
- Tabela `ctip.ivr_map` przechowuje mapowania cyfr IVR (`digit`) na wewnętrzne numery docelowe (`ext`) wraz z tekstem wiadomości i flagą `enabled`. Dodatkowe ograniczenie `uq_ivr_map_ext` gwarantuje, że dany numer wewnętrzny ma tylko jedną aktywną regułę.
- Panel administracyjny (`/admin/partials/ctip/ivr-map`) udostępnia pełny CRUD mapowań oraz natychmiast aktualizuje treść wysyłanej wiadomości. Domyślna migracja (`15989372b89d`) tworzy wpis dla cyfry `9` kierującej na wewnętrzny `500` i przypisuje komunikat „Instrukcja instalacji aplikacji Ksero Partner znajdziesz na stronie https://www.ksero-partner.com.pl/appkp/.” – wpis można dowolnie edytować lub wyłączyć.
- `collector_full.py` odczytuje mapowania w momencie obsługi ramki `RING`; po wykryciu dopasowania dodaje pojedynczą wiadomość do kolejki `ctip.sms_out` (źródło `ivr`, powód `{"reason": "ivr_map"}`) i zabezpiecza się przed duplikatami (`ON CONFLICT (call_id) WHERE source='ivr' DO NOTHING`), dzięki czemu każde połączenie otrzymuje maksymalnie jeden SMS.
- Metadane `ext` i `digit` w `sms_out.meta` są zapisywane z jawnym rzutowaniem (`::text`, `::int`), aby uniknąć błędu „nie można określić typu danych parametru” po stronie PostgreSQL.
- Strumień CTIP nie zawiera informacji o wciśniętych cyfrach IVR – centrala wysyła jedynie pierwszy `RING` na skonfigurowany numer wewnętrzny. Kolektor wnioskuje cyfrę na podstawie trafionego numeru wewnętrznego (`ctip.ivr_map`) i loguje to jako `IVR_MAP_HIT digit=<...>`.
- Historia CTIP (`call_events`) rejestruje zarówno trafienia (`IVR_MAP_HIT`), jak i brak dopasowania (`IVR_MAP_MISS`) wraz z numerem wewnętrznym, co ułatwia diagnostykę konfiguracji IVR.
- Dashboard panelu administracyjnego prezentuje kafelek „Automatyczne SMS (IVR)” zawierający licznik błędów/kolejki oraz skrót do historii wysyłek i diagnostyki `/admin/status/ivr`.

## Automatyczne SMS dla dzwoniących
- Konfiguracja jest przechowywana w `ctip.admin_setting` (prefiks `call_sms.*`) i ładowana przez kolektor w momencie zdarzenia `REL`.
- Domyslne tresci SMS zawieraja link do aplikacji `https://www.ksero-partner.com.pl/app/` i moga byc edytowane w panelu.
- Scenariusz po godzinach pracy uruchamia sie po wykryciu wskazanego numeru wewnetrznego (np. 500) i ma priorytet nad pozostalymi scenariuszami.
- Scenariusze obejmują połączenia przychodzące i wychodzące: odebrane, nieodebrane oraz ponowne (oddzielne treści, opcjonalne przełączniki).
- Powtórne połączenie jest rozpoznawane po wcześniejszym wpisie `sms_out` o źródle `call_sms`; jeśli scenariusz „ponowny” jest aktywny, jego treść zastępuje bazowy wariant, aby jedno połączenie nie generowało wielu SMS.
- Mechanizm ograniczeń częstotliwości działa w trybach „Nigdy / Po X dniach / Zawsze” i bazuje na czasie ostatniego wpisu `sms_out` z `source='call_sms'`.
- Wysyłka jest ograniczona do polskich numerów komórkowych (+48) i ignoruje numery stacjonarne, premium oraz zagraniczne (lista prefiksów komórkowych znajduje się w `app/services/call_sms_rules.py`).
- Lista numerów z blokadą (opt-out) jest edytowana w panelu, a masowa wysyłka dodaje pojedynczy SMS do każdego unikalnego numeru z historii połączeń, z zachowaniem filtrów i ograniczeń.

## Środowisko testowe WSL (mock CTIP + osobna baza)
- Pełny runbook wraz z zabezpieczeniami przed podłączeniem do produkcji: `docs/instal/test_env_wsl.md` (mock CTIP, `.env.test`, `run_test_stack_tmux.sh`).
- To jest domyslna i zalecana sciezka uruchamiania calego systemu lokalnie. Start produkcyjny przez `run_stack_tmux.sh` oraz `run_server_with_firebird.sh` wymaga od teraz jawnej flagi `ALLOW_PRODUCTION_START=true`.
- Skrót procedury:
  - przygotowanie zależności: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`;
  - kopiowanie `.env.test.example` → `.env.test`, pozostawienie `PGDATABASE=ctip_test`, `PBX_HOST=127.0.0.1`, `PBX_PORT=5525`, `SMS_TEST_MODE=true`, lokalnych hostów baz `127.0.0.1` oraz pustych pól `SMS_API_URL`, `SMS_API_TOKEN`, `SMS_API_USERNAME`, `SMS_API_PASSWORD`, jeśli test ma być całkowicie odcięty od operatora SMS;
  - dla dostepu z innych hostow LAN ustaw `TEST_PUBLIC_HOST=<IP_lub_DNS_hosta>` albo pozostaw autodetekcje; `ctiptest` i `run_test_stack_tmux.sh` nadpisuja testowe `ADMIN_PANEL_URL` oraz `FORM_PUBLIC_BASE_URL` publicznym adresem hosta;
  - załadowanie zmiennych i migracje: `set -a && source .env.test && set +a && alembic upgrade head`;
  - start mocka CTIP: `python scripts/mock/mock_ctip_server.py --port 5525 --loop --log-level INFO`;
  - uruchomienie stosu w tmux: `./run_test_stack_tmux.sh` (okna `collector`, `uvicorn` na porcie 8000, `sms-sender` z `SMS_TEST_MODE`; dodatkowo preflight Firebird na `FB_HOST:FB_PORT` z auto-startem kontenera `ctip-firebird-local` gdy host jest lokalny i port nie odpowiada);
  - podgląd/zatrzymanie: `tmux attach -t ctip-stack-test`, zakończenie `kill-session -t ctip-stack-test` i `Ctrl+C` w oknie mocka.
- Analiza ryzyk równoległej pracy produkcji i testów: `docs/projekt/dual_site_analysis.md`.
- Start całości jednym poleceniem (mock + Firebird + kolektor + uvicorn + sms_sender): `./ctiptest` – tworzy sesję tmux `ctip-stack-test` z czterema oknami i blokuje uruchomienie, jeśli `.env.test` wskazuje na produkcyjną centralę, hosty baz `192.168.0.8`, bazę inną niż `ctip_test` albo `SMS_TEST_MODE` ≠ `true`. Przed startem sprawdza też dostępność Firebird pod `FB_HOST:FB_PORT`; gdy host jest lokalny (`127.0.0.1`/`localhost`) i port nie odpowiada, skrypt automatycznie uruchamia kontener `ctip-firebird-local` i czeka na nasłuch. Domyslnie uruchamia `uvicorn` bez `--reload`, aby nie wpadać w bledy watchera na katalogach roboczych; reload mozna wlaczyc jawnie przez `TEST_UVICORN_RELOAD=true ./ctiptest`. Skrypt publikuje testowy adres WWW pod wykrytym adresem LAN hosta albo pod wartoscia `TEST_PUBLIC_HOST`, dzieki czemu linki i panel nie wskazuja na `localhost` przy dostepie z innych maszyn. Przy pustych `SMS_API_*` wysylka przechodzi w lokalna symulacje `SIMULATED` bez ruchu do operatora.
- Aby zasilić `ctip_test` realnymi formularzami workflow przed testami mailbox/GRENKE, można uruchomić:

```bash
source .venv/bin/activate
set -a
source .env.test
source .env
set +a
python scripts/sync_prod_forms_to_test.py --limit 200 --status SUBMITTED
```

- Skrypt importuje `ctip.form_request`, `ctip.form_workflow_case` i `ctip.form_workflow_device`, zachowuje identyfikatory rekordów, po imporcie ustawia sekwencje po stronie testowej i nigdy nie zapisuje nic do źródłowej bazy produkcyjnej. Jeśli `created_by` lub `updated_by` wskazują użytkownika, którego nie ma w `ctip_test`, pola są zerowane do `NULL`, aby nie złamać kluczy obcych.

## Instalacja jako usługa Windows
1. Przygotuj `D:\CTIP` (git clone), Python 3.11 x64, plik `.env`.
2. Uruchom PowerShell jako Administrator i skrypt `scripts/windows/install_service.ps1 -InstallDir "D:\CTIP" -PythonVersion "3.11"` – tworzy `.venv`, instaluje zależności, zapisuje `collector_service_config.json` z polem `env_file=D:\CTIP\.env` oraz `script=D:\CTIP\scripts\windows\run_collector_env_bootstrap.py`, rejestruje i startuje usługę `CollectorService` (kolektor CTIP) z logami w `logs/collector`.
3. Zainstaluj NSSM (https://nssm.cc/download), a następnie uruchom `scripts/windows/install_web_sms_nssm.ps1 -InstallDir "D:\CTIP" -ServicePrefix "CTIP" -UvicornPort 8000 -NssmPath "C:\Program Files\nssm\nssm.exe"`. Skrypt tworzy i włącza dwie usługi: `CTIP-Web` (bootstrap `scripts/windows/run_ctip_web.py`, `WindowsSelectorEventLoopPolicy`, `0.0.0.0:8000`, `workers=1`) oraz `CTIP-SMS` (`sms_sender.py`) z logami w `logs/web` i `logs/sms`, uruchamiane automatycznie po restarcie.
4. Jeżeli formularze mają być wystawione poza LAN, doinstaluj trzecią usługę publiczną: `scripts/windows/install_web_sms_nssm.ps1 -InstallDir "D:\CTIP" -ServicePrefix "CTIP" -UvicornPort 8000 -InstallPublicForms -PublicFormsHost "127.0.0.1" -PublicFormsPort 8100 -NssmPath "C:\Program Files\nssm\nssm.exe"`. Skrypt dodaje usługę `CTIP-FormsPublic` (uvicorn `app.public_forms_app:app`) z logami w `logs/forms_public`.
5. Sprawdzenie stanu: `Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic`; logi odpowiednio w `logs/collector`, `logs/web`, `logs/sms`, `logs/forms_public`. Panel jest dostępny pod `http://<host>:8000/admin`, natomiast publiczny link formularza powinien po reverse proxy kierować do `https://form.twoja-domena.pl/formularz/<token>`.
6. Instalator kolektora generuje lokalnie `collector_service_config.json`; jest to artefakt roboczy hosta Windows i pozostaje poza Git. Repo utrzymuje tylko skrypty bootstrapu (`collector_service.py`, `scripts/windows/run_collector_env_bootstrap.py`, `scripts/windows/run_ctip_web.py`).

Uwaga: komunikaty w skryptach PowerShell są zapisane w ASCII (bez polskich znaków), dzięki czemu Windows PowerShell 5.1 z domyślnym kodowaniem nie zgłasza błędów parsowania. Skrypty instalacyjne znajdują się w repozytorium w `scripts/windows` (także w pakiecie `docs/instal/ctip_windows_service_package.zip`) i domyślnie wymuszają `py -3.11`; na hostach z domyślnym Pythonem 3.13 uruchamiaj `install_service.ps1` z parametrem `-PythonVersion "3.11"`.
Jeżeli host nadal korzysta ze starszego wrappera `collector_service.py`, plik `collector_service_config.json` może wskazywać `script=D:\CTIP\scripts\windows\run_collector_env_bootstrap.py`; bootstrap jawnie wczytuje `D:\CTIP\.env` i dopiero uruchamia `collector_full.py`, co eliminuje zależność od globalnych zmiennych środowiskowych Windows.

Aktualizacje kodu na Windows wykonuj przez `scripts/windows/update_ctip.ps1` (zatrzymuje uslugi, `git fetch/pull`, aktualizacja zaleznosci, `pre-commit run --all-files`, testy `python -m unittest discover -s tests`, a nastepnie restart uslug). Dla srodowisk z NSSM uzyj `-ServiceNames "CollectorService","CTIP-Web","CTIP-SMS"` albo `-ServiceNames "CollectorService","CTIP-Web","CTIP-SMS","CTIP-FormsPublic"`, jeżeli aktywna jest tez subdomena formularzy.
Szybka aktualizacja bez testow i instalacji zaleznosci: `scripts/windows/update_ctip_easy.ps1` (wykonuje `git fetch/pull --ff-only` i restartuje tylko uruchomione uslugi, a gdy brak nowych commitow - nie restartuje nic). Opcjonalnie wymusisz restart parametrem `-ForceRestart`.
W przypadku bledu `500` na publicznym `/formularz/{token}` uzyj `scripts/windows/fix_forms_public_500.ps1`. Skrypt ma tryb diagnostyczny (bez zmian) oraz tryb naprawczy `-Apply` (git pull, `pip install -e .`, weryfikacja/korekta `AppDirectory`, wymuszenie kompletu `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD/PGSSLMODE` w `AppEnvironmentExtra` uslugi `CTIP-FormsPublic`, restart uslugi i testy endpointow `/health` + `/formularz/*`). Domyslnie dla uslugi formularzy wymusza `PGHOST=127.0.0.1`, bo PostgreSQL dziala na tym samym Windows Server.
Gotowy skrypt wdrozenia sesji AI Asystenta na Windows Server (branch `codex/fix-public-form-checkbox-422`, kontrola Alembic i healthcheck) znajduje sie w `scripts/windows/deploy_prod_ctip_assistant_2026-04-30.ps1`; uruchomienie: `.\scripts\windows\deploy_prod_ctip_assistant_2026-04-30.ps1 -Apply`. Skrypt nie blokuje wdrozenia, gdy indeks wiedzy Firebird nie istnieje przed `git pull` i po aktualizacji automatycznie probuje go wygenerowac przez `scripts/build_firebird_knowledge_index.py`. Dodatkowo przed healthcheck wymusza start wskazanych uslug, a `scripts/windows/update_ctip.ps1` obsluguje komunikaty informacyjne `git`/`python` na `stderr` bez falszywego przerwania aktualizacji (ustawienie `PSNativeCommandUseErrorActionPreference=$false` i walidacja po `LASTEXITCODE`). Parametr `-TargetCommit` jest opcjonalny (domyslnie pusty) i mozna go podac tylko wtedy, gdy chcesz wymusic konkretny hash.

Szczegółowy przewodnik dla Windows Server 2022 (instalacja w `D:\CTIP`, skrypty PowerShell oraz pakiet `ctip_windows_service_package.zip`) znajduje się w `docs/instal/windows_server_2022.md`. Runbook DNS/NAT/reverse proxy dla publicznych formularzy: `docs/instal/public_forms_production.md`. Stan wdrożenia produkcyjnego i opis kolejnego etapu interakcji formularza: `docs/projekt/public_forms_status_2026-04-09.md`. Dziennik domkniecia etapu formularzy, automatu MS i panelu administratora: `docs/projekt/dziennik_2026-04-09.md`.
Szybki runbook awaryjny (checklisty i komendy 1:1 dla `CTIP-Web`/`CTIP-FormsPublic`) znajduje sie w `docs/instal/ctip_windows_recovery_runbook.md`.
Docelowy model galezi i promocji zmian miedzy testem a produkcja opisuje `docs/instal/model_galezi_i_wdrozen_2026-05-25.md`.
Krotki runbook kolejnej poprawki produkcyjnej po tym porzadkowaniu znajduje sie w `docs/instal/runbook_nastepnej_poprawki_prod_2026-05-25.md`.

## Integracja wysyłki SMS
`sms_sender.py` uruchamia pętlę pobierającą z `ctip.sms_out` wiadomości w statusie `NEW` i przekazuje je do `HttpSmsProvider` (token lub login/hasło operatora SerwerSMS). Każda próba jest logowana przez `log_utils.append_log` do pliku `docs/LOG/sms/sms_sender_<YYYY-MM-DD>.log`, a wynik aktualizuje rekord (`SENT` z `provider_status` i `provider_msg_id`, albo `ERROR` z `error_msg`). Moduł automatycznie odnawia połączenie z PostgreSQL po błędach transportowych, aby krótkie restarty bazy nie zatrzymywały kolejki. Podgląd logu i najnowszej historii wysyłek jest dostępny bezpośrednio w panelu administratora (sekcja SerwerSMS). Dodatkowo `HttpSmsProvider` automatycznie generuje identyfikatory `unique_id` w formacie `CTIP-000000`, dzięki czemu operator nie zgłasza już błędu „Niepoprawne znaki w unique_id”. Dla tresci dluzszych niz 160 znakow GSM lub zawierajacych znaki spoza ASCII provider wymusza typ `full` oraz parametr `utf=true`, aby mozliwe bylo wyslanie SMS wieloczescowych (wymaga aktywnego kanalu FULL u operatora). Szczegółowy manual HTTPS API v2 znajduje się w `docs/centralka/serwersms_https_api_v2_manual.md`, a przykładową bibliotekę kliencką udostępnia projekt SerwerSMS: ``https://github.com/SerwerSMSpl/serwersms-python-api``.

## Diagnostyka i monitoring
- Logi kolektora zawierają prefiks `LOG_PREFIX` i są wypisywane na STDOUT/STDERR lub do pliku (w Windows wg konfiguracji usługi).
- Po uruchomieniu należy zweryfikować w logach linię z identyfikatorem centrali (`aWHO`/`aOK`) oraz komunikat potwierdzający `aLOGA`; ich brak oznacza przerwany handshake.
- `ctip_sniff.py` pozwala szybko zweryfikować, czy centrala zwraca zdarzenia – zapisuje surowe linie do `ctip_sniff.log`.
- `conect_sli.py` można wykorzystać do ręcznego monitorowania strumienia CTIP (telnet w Pythonie) z poziomu WSL lub Linux; każdy odebrany wiersz trafia do pliku `docs/LOG/Centralka/log_con_sli_<YYYY-MM-DD>.log` wraz ze znacznikiem czasu.
- `sms_sender.py` tworzy dzienny log `docs/LOG/sms/sms_sender_<YYYY-MM-DD>.log`; ten sam plik prezentowany jest na żywo w panelu (SerwerSMS → Log sms_sender).
- Tabela `sms_out` powinna być monitorowana pod kątem wpisów w statusie `ERROR`.
- Szybka diagnostyka uslugi SMS na Windows: `scripts/windows/collect_sms_sender_diag.ps1` zapisuje raport do katalogu, z ktorego zostal uruchomiony (np. `\\<host>\CTIP\temp`).
- Dobowy restart i testy uslug (Windows): `scripts/windows/restart_daily.ps1` restartuje `CollectorService`, `CTIP-Web`, `CTIP-SMS`, a nastepnie testuje TCP do centrali i polaczenie z PostgreSQL (psql). Logi trafiaja do `logs/maintenance/daily_restart_<YYYY-MM-DD>.log`. Skrypt korzysta z `.env` i opcjonalnie wysyla alerty SMS/e-mail, jesli ustawisz `ALERT_SMS_DEST` i `ALERT_EMAIL_TO` wraz z `EMAIL_*`. Dla testu bazy i alertow SMS wymagany jest `psql` w PATH albo wskazany w `PSQL_BIN`.
- Harmonogram 00:00 (Task Scheduler):
  ```bat
  schtasks /Create /TN "CTIP-Daily-Restart" /TR "\"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\" -NoProfile -ExecutionPolicy Bypass -File D:\\CTIP\\scripts\\windows\\restart_daily.ps1" /SC DAILY /ST 00:00 /RU SYSTEM /RL HIGHEST /F
  ```
- Dla weryfikacji poprawności bazy warto okresowo wykonywać zapytania kontrolne, np. liczba połączeń na godzinę, czasy odpowiedzi itp.
- Analiza logów komunikacji CTIP powinna obejmować korelację zdarzeń z centralą i raportowanie rozłączeń, błędów `NAK` oraz przerw w strumieniu TCP do zespołu utrzymaniowego.

## Zasoby w katalogu `docs/`
- `docs/centralka` – instrukcje centrali Slican (m.in. „CTIP” oraz „instrukcja programowania NCP v1.21”) ułatwiające konfigurację warstwy telekomunikacyjnej i protokołu CTIP.
- `docs/baza` – aktualny schemat `schema_ctip.sql`; plik `ctip_plain` pozostawiono jako nieaktualny zrzut archiwalny (do wglądu historycznego, nie do odtwarzania).
- `docs/firebird` – materiały integracyjne dla Menadżera Serwisu (konfiguracja połączenia, mapa `bazams` -> `ctip.contact` w `docs/firebird/bazams_mapowanie_ctip.md` oraz miejsce na robocze artefakty).
- `docs/firebird/proces_sprzedazy_ms.md` – opis potwierdzonego procesu handlowego Menadzera Serwisu, znaczenia triggerow, zmian po aktualizacji KSeF oraz zapytan diagnostycznych.
- `docs/instal/public_forms_production.md` – runbook wystawienia `form.ksero-partner.com.pl` (DNS w home.pl, NAT/router, reverse proxy, osobna usługa `CTIP-FormsPublic`).
- `docs/LOG/Centralka` – dzienne logi kolektora i monitora CTIP (np. `log_collector_<YYYY-MM-DD>.log`, `log_con_sli_<YYYY-MM-DD>.log`); każdy wpis zawiera datę i godzinę.
- `docs/LOG/BAZAPostGre` – dzienne logi operacji na bazie PostgreSQL (np. `log_192.168.0.8_postgre_<YYYY-MM-DD>.log`).
- `docs/projekt` – przestrzeń na notatki projektowe, szkice i checklisty wdrożeniowe; `centrum_obslugi_prototyp_2026-07-25.md` opisuje warstwę widoków CRM, `laboratorium_formularzy_chat_crm_2026-07-27.md` opisuje trwały LAB i kontrakty kanałów, `formularze_www_crm_2026-07-30.md` dokumentuje wymianę formularzy PageKP, a `wysylki_dpd.md` opisuje wysyłki i katalog zgodności części.
- `docs/raport` – statyczny raport CPC (HTML + CSV) udostępniany bez logowania pod `http://127.0.0.1:8000/raport`; serwer FastAPI montuje katalog bez prawa zapisu, dzięki czemu pełni rolę tylko-do-odczytu.
- 📁 Archiwum sesji Codex: `docs/archiwum/sesja_codex_2025-10-11.md`
- `baza_CTIP` (katalog główny repozytorium) – dokument opisujący strukturę schematu `ctip`, procedurę połączenia oraz typowe operacje administracyjne.
- `prototype/index.html` – statyczny prototyp interfejsu użytkownika prezentujący widok listy połączeń CTIP, panel szczegółów, szybkie akcje SMS oraz historię wiadomości (dane przykładowe, brak połączenia z API).

## Testowanie i rozwój
Repozytorium zawiera testy jednostkowe handshake CTIP (`tests/test_handshake.py`), klienta monitorującego (`tests/test_conect_sli.py`), kolektora CTIP (`tests/test_collector_context.py`), warstwy API (`tests/test_api_auth.py`, `tests/test_sms_schema.py`), procesu wysyłek i katalogu zgodności (`tests/test_shipping.py`) oraz zestaw weryfikacji schematu bazy (`tests/test_db_schema.py`). `tests/test_admin_backend.py` obejmuje scenariusze panelu administracyjnego, w tym logi i historię SerwerSMS (`/admin/sms/logs`, `/admin/sms/history`). Uruchom je poleceniem `python -m unittest`. Każda modyfikacja kodu powinna być od razu odzwierciedlona w dokumentacji i w sekwencjach testowych.
  - Zadania planowane.
## Zadania planowane
Szczegółowy rejestr zadań znajduje się w pliku `docs/projekt/zadania_planowane.md`.
Aktualny plan procesu obslugi umowy znajduje sie w `docs/projekt/prace_na_teraz_2026-03-13.md`.
Plan naprawczy modeli produkcyjnych znajduje sie w `docs/projekt/plan_naprawczy_modeli_produkcyjnych_2026-03-18.md`.
Audyt master tabeli MODEL na produkcji znajduje sie w `docs/projekt/audyt_master_model_produkcja_2026-03-18.md`.
Robocze pliki CSV do decyzji i przepiec modeli sa zapisywane w katalogu `inbox/audyt_model`.
Pipeline zdjec Ricoh (`pipeline_zdjec_imgdev.md`, `process_ricoh_images.py`, katalogi `imgsrc/`, `imgtmp/`, `imgdev/`, `logo/`) jest utrzymywany w `inbox/audyt_model`. Docelowy format PNG dla packshotow to `1200x1667`, biale tlo, logo w lewym gornym rogu oraz nazwa pliku z prefiksem `ran_`. Dodatkowy etap ponownego pozyskiwania kandydatow po odrzuceniu korzysta z katalogow `imgsrc_retry/`, `imgtmp_retry/`, `imgdev_retry/` oraz pliku `retry_better_candidates.csv`.
W katalogu `inbox/audyt_model/imgdev` znajduje sie tez pomocniczy `index.html` z prosta galeria wszystkich finalnych obrazow `ran_*.png`; plik mozna wrzucic na hosting do tego samego katalogu co obrazy i otwierac przez HTTP/HTTPS.
Synchronizacja `MODEL.PLIK` na lokalnej kopii Firebird jest zautomatyzowana skryptem `scripts/firebird_sync_model_plik.py`; w trybie `FB_MODE=local` skrypt laczy sie po `127.0.0.1`, obsluguje fallback do aliasu `BAZAMS_TEST` dla kopii WSL i dopiero po `--apply` zapisuje zmiany w lokalnej bazie.
Naprawe tabeli `MODEL` wzgledem zatwierdzonego snapshotu referencyjnego wykonuje skrypt `scripts/firebird_repair_model_master.py`; skrypt potrafi przepiac `ID_MODEL` w `MASZYNA`, `MAGAZYN`, `CENNIK` i `MZ`, usunac nadmiarowe modele oraz zwalidowac wynik 1:1 wzgledem snapshotu. Dla zdalnej bazy produkcyjnej backup trzeba wykonac osobno, skrypt uruchomic z `--skip-backup`, a snapshot referencyjny czytac przez lokalny host (`--reference-host 127.0.0.1`).
Jesli w `MODEL` pojawia sie wysokie techniczne ID (np. `3000xxxx`), do bezpiecznej renumeracji na ciag dalszy po `631` sluzy skrypt `scripts/firebird_resequence_model_ids.py`, ktory wykonuje dry-run, raportuje plan i po `--apply` aktualizuje powiazania `ID_MODEL` we wszystkich tabelach z ta kolumna.
