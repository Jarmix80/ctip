# Panel administratora CTIP – szkic interfejsu

## Założenia wizualne
- Spójność z `prototype/index.html`: ta sama paleta kolorów (`--primary`, `--accent`, `--bg`), typografia i kształt komponentów (karty z zaokrąglonymi narożnikami, tabele z przełączanymi kolumnami).
- Layout desktop-first z responsywnością do szerokości 1280 px; przełączenie na układ jednokolumnowy poniżej 1100 px.
- Nawigacja boczna (drawer) po lewej stronie, pełna wysokość, z ikonami i etykietami sekcji.
- Nagłówek górny z informacją o zalogowanym administratorze, statusem środowiska (ikony zielony/żółty/czerwony) i przyciskiem „Wyloguj”.
- Komponenty interaktywne bazujące na lekkim frameworku JS (można wykorzystać `htmx` + `Alpine.js` lub prosty bundle Vite), aby ograniczyć zależność od dużych bibliotek.
- Ekran logowania dostępny przed głównym layoutem; po udanym logowaniu token `X-Admin-Session` zapisywany jest w `localStorage`, a HTMX dołącza go do kolejnych żądań.
- Domyślny klucz szyfrujący (`ADMIN_SECRET_KEY`) można wygenerować poleceniem `python - <<'PY' ...` – aktualne środowisko: `WIhihceDcH4lgOWSbs9Qxa4rTS0LojBVxOEeQHD_u8g=` (docelowo trzymać w `.env`).

## Nawigacja

| Sekcja | Opis | Główne komponenty |
|--------|------|-------------------|
| Dashboard | Skróty operacyjne i statusy | Karty (`Status bazy`, `CTIP`, `SerwerSMS`, `Ostatnia kopia`, `Aktywne sesje`) |
| Konfiguracja bazy | Edycja parametrów PostgreSQL, test połączenia | Formularz, przycisk „Testuj”, wyniki health-check |
| Baza Firebird | Parametry połączenia Menadżera Serwisu i test logowania | Formularz z wyborem trybu `sieciowa`/`lokalna`, test `/admin/firebird/test`, ścieżki bazy sieciowej i lokalnej |
| Kopie zapasowe | MVP: podgląd plików w `backups/` (bez uruchamiania) | Tabela z listą plików, przyciski akcji zablokowane |
| CTIP Live | Monitor protokołu, restart klienta, status handshake | Panel z WebSocket (lista zdarzeń), karty statusu, przyciski akcji |
| Automatyzacje IVR | Mapowanie cyfr IVR na SMS i numery wewnętrzne | Lista reguł, formularz CRUD, audyt operacji, walidacja unikalności |
| SerwerSMS | Parametry operatora, tryb demo, wysyłka testowa | Formularz, przełącznik demo, moduł wysyłki testowej, saldo |
| Obsługa formularza | Publiczny adres formularza oraz treści interakcji po stronie klienta i operatora | Formularz konfiguracji, pola szablonów, lista dostepnych placeholderow, podglad renderu |
| Książka adresowa | Kartoteka kontaktów, powiązanie z bazą Firebird | Lista kontaktów, formularz dodawania, edycja w modalach, wyszukiwarka |
| Konsola SQL & Raporty | Sandbox `SELECT`, zapisane zapytania, wykresy SMS | Edytor tekstowy, wyniki tabelaryczne, eksport CSV |
| Użytkownicy | Zarządzanie kontami, przypisanie numerów, statystyki | Tabela CRUD, modale, wykres słupkowy (SMS na użytkownika) |

### Przełączanie sekcji
- Po kliknięciu w pozycję menu bocznego zawartość głównego panelu (`<main>`) jest zastępowana odpowiadającym widokiem.
- Aktywna pozycja menu jest wyróżniona `border-left` oraz tłem `rgba(31, 63, 105, 0.12)`.
- Na urządzeniach mobilnych menu jest zwijane do hamburgera; panel wysuwa się w trybie overlay.

## Widoki szczegółowe

### 1. Dashboard
- Dwukolumnowa siatka kart (CSS Grid).
- Dane z `/admin/status/summary` – każda karta zawiera status (tekst), szczegóły oraz CTA (np. test połączenia bazy, przejście do konfiguracji).
- Karty infrastrukturalne (Baza, CTIP, SerwerSMS) udostępniają w panelu przycisk „Diagnostyka”, który otwiera modal z danymi z `/admin/status/database|ctip|sms`; podgląd prezentuje zarówno opisową kartę, jak i surową odpowiedź JSON do szybkiej analizy.
- Karta `Baza danych`: efekt `SELECT 1`, prezentacja hosta i użytkownika, akcja „Testuj połączenie”.
- Karta `Centrala CTIP`: informacja o host/porcie i stanie konfiguracji PIN, akcja „Edytuj konfigurację”.
- Karta `SerwerSMS`: nadawca, tryb demo/produkcyjny, akcja „Ustaw parametry”.
- Karta `Kopie`: podgląd ostatniej kopii i CTA do sekcji (tryb podglądu).

### 2. Konfiguracja bazy
- Formularz HTMX/Alpine z polami `Host`, `Port`, `Baza`, `Użytkownik`, `Hasło`, `SSL` – wczytywany z `/admin/partials/config_database`.
- Hasło nie jest wyświetlane – wpisanie nowej wartości nadpisuje zapisane hasło, pozostawienie pustego pola pozostawia je bez zmian.
- Sekcja statusowa z wynikiem ostatniego testu (`/admin/status/database`), przycisk „Testuj połączenie” wywołuje endpoint i prezentuje rezultat.
- Po poprawnym zapisie (`PUT /admin/config/database`) formularz aktualizuje wartości oraz emituje toast „Zapisano konfigurację bazy”.
- Panel docelowo rozszerzymy o instrukcje migracji (link do `docs/baza/schema_ctip.sql`).

### 3. Kopie zapasowe
- MVP: widok `/admin/partials/backups` prezentuje listę plików z katalogu `backups/` (nazwa, rozmiar, data, status).
- Przyciski `Utwórz kopię` i `Przywróć` są zablokowane; API obsługuje wyłącznie dry-run.
- Docelowo: filtr dat i statusów, modale potwierdzające, upload pliku `.dump` oraz panel logów przebiegu.
- API: `POST /admin/backup/run`, `POST /admin/backup/restore`, `GET /admin/backup/history` (run/restore tylko dry-run).

### 3a. Baza Firebird (Menadżer Serwisu)
- Formularz HTMX/Alpine z polami `Aktywna baza` (`sieciowa` / `lokalna`), `Host`, `Port`, `Ścieżka bazy sieciowej`, `Użytkownik`, `Hasło`, `Kodowanie`, `Rola`, `Ścieżka bazy lokalnej`.
- Odczyt i zapis konfiguracji: `GET/PUT /admin/config/firebird`.
- Test logowania: `POST /admin/firebird/test` (wynik prezentowany w sekcji statusowej formularza i zapisywany w audycie); endpoint testuje ścieżkę lokalną lub sieciową zgodnie z wybranym trybem, a dla trybu lokalnego wymusza host `127.0.0.1`.
- Konfiguracja jest przechowywana w `admin_setting` pod namespace `firebird` z szyfrowaniem hasła analogicznie do SMTP/SerwerSMS.
- Formularz posiada jawne atrybuty `data-save-endpoint` i `data-test-endpoint`, aby UI nie moglo pomylic tej sekcji z baza `v-maintenance`.

### 4. CTIP Live
- Layout 70/30: lewa kolumna – strumień zdarzeń, prawa – karty statusu.
- Strumień (lista rosnąco od góry) z możliwością filtrowania po numerze/typie ramki.
- Karty statusu: `Połączenie`, `Handshakes`, `Ostatni błąd`, `Ostatnia wiadomość IVR`.
- Przyciski: `Restart klienta`, `Wymuś aWHO`, `Wymuś aLOGA`.
- WebSocket `GET /admin/events/cti` (do zaimplementowania) – front buforuje ostatnie 200 wpisów.

### 4a. Automatyzacje IVR
- Widok `/admin/partials/ctip/ivr-map` udostępnia tabelę reguł (`Digit`, `Numer wewnętrzny`, `Treść SMS`, `Status`, `Ostatnia modyfikacja`) oraz panel formularza do dodawania/edycji mapowania.
- Walidacje: numer wewnętrzny (`ext`) musi być unikalny (constraint `uq_ivr_map_ext`), `digit` z zakresu 0–9, treść wiadomości maks. 500 znaków. UI sygnalizuje konflikty (np. duplikat numeru) w postaci komunikatu przy polach.
- Zapisy trafiają do endpointów `/admin/ctip/ivr-map` (POST), `/admin/ctip/ivr-map/{ext}` (PUT/DELETE); backend loguje operacje w `admin_audit_log` razem z identyfikatorem użytkownika i adresem IP.
- Formularz wspiera szybkie włączanie/wyłączanie reguł (`enabled`), podpowiada znormalizowany numer oraz prezentuje hint o skutkach (jedna wiadomość SMS na połączenie, źródło `ivr`).
- Domyślna migracja `15989372b89d` tworzy wpis dla cyfry `9` kierujący na wewnętrzny `500` z komunikatem „Instrukcja instalacji aplikacji Ksero Partner znajdziesz na stronie https://www.ksero-partner.com.pl/appkp/.” – po usunięciu lub wyłączeniu reguły kolektor przestaje automatycznie wysyłać SMS dla tego numeru.
- Strumień CTIP nie zwraca ramki z cyfrą IVR – wykrycie mapowania następuje na podstawie pierwszego `RING` skierowanego na dany numer wewnętrzny. Panel prezentuje cyfrę z definicji mapowania, a logi kolektora dopisują `IVR_MAP_HIT`/`IVR_MAP_MISS` dla celów diagnostycznych.
- Dashboard udostępnia osobny kafelek „Automatyczne SMS (IVR)” (źródło `/admin/status/ivr`) z licznikami błędów i oczekujących wysyłek oraz skrótem do konfiguracji/historii IVR.
- Widok wykorzystuje lightweight bundle (`app/static/admin/ctipIvrMap.js`) i HTMX do odświeżania listy po każdej operacji, aby nie przeładowywać całego panelu.

### 5. SerwerSMS
- Formularz (HTMX/Alpine) z polami `API URL`, `Nadawca`, `Typ SMS`, `Login`, `Hasło`, `Token`, `Tryb demo`.
- Zapis wykorzystuje endpoint `/admin/config/sms`; po sukcesie panel pokazuje toast i resetuje pola hasła/tokenu.
- Wysyłka testowa (`/admin/sms/test`) – formularz inline, prezentuje wynik sukcesu/błędu na ekranie, a numer docelowy musi być podany w formacie E.164 (walidacja po stronie backendu i UI).
- Historia (`/admin/partials/sms/history`) umożliwia filtrowanie po statusach (`NEW`, `RETRY`, `SENT`, `ERROR`, `SIMULATED`) i cykliczne odświeżanie.
- Integracja z `/admin/status/summary` – karta pokazuje liczbę błędów wysyłki (`sms_out.status = ERROR`) oraz ostatni komunikat błędu.

### 5a. CTIP Live
- Widok `/admin/partials/ctip/live` łączy monitor zdarzeń (WebSocket + filtr po wewnętrznym) z wbudowanym formularzem konfiguracji.
- Endpoint `/admin/ctip/events` udostępnia JSON na potrzeby testów/manualnych odczytów, natomiast `GET /admin/ctip/ws` przesyła aktualizacje w czasie rzeczywistym (limit 5–200 wpisów; filtr usuwa ramki keep-alive typu `T`).
- Kafelek „Centrala CTIP” na dashboardzie posiada dwie akcje: edycję konfiguracji oraz szybkie przejście do podglądu live.
- Operatorzy i administratorzy mają w tej sekcji panel „Szybka edycja kontaktu”: wybrane zdarzenie otwiera formularz pozwalający zapisać dane numeru (imię, nazwisko, firma, e-mail, `firebird_id`, notatki). Zapis odświeża główną książkę adresową i loguje operację w audycie.

### 5b. E-mail SMTP
- Formularz `/admin/partials/config/email` pozwala ustawić host, port, login, hasło oraz dane nadawcy wiadomości.
- Przyciski „Testuj połączenie” korzystają z `/admin/email/test`, który próbuje nawiązać połączenie z podanym serwerem (uwierzytelnianie + STARTTLS/SSL).
- Dashboard pokazuje kartę „E-mail SMTP” z informacją o stanie konfiguracji i skrótami do sekcji oraz testu.

### 5c. Obsluga formularza
- Sekcja `/admin/partials/config/form-handling` sluzy do konfiguracji publicznego adresu formularza oraz wszystkich komunikatow zwiazanych z flow `/genform` i `/formularz/{token}`.
- Generator formularzy pozostaje osobnym ekranem pod `/genform`; panel administratora nie przejmuje jego funkcji operacyjnych, a jedynie zarzadza konfiguracja runtime.
- Formularz zapisuje:
  - `public_base_url` dla linkow `/formularz/{token}`,
  - tresc SMS do klienta po wygenerowaniu formularza,
  - temat i tresc e-maila z linkiem,
  - temat i tresc e-maila potwierdzajacego zapis formularza,
  - tresc SMS do operatora/opiekuna po zapisie.
- Sekcja renderuje podglad wszystkich szablonow na przykładowych danych (`customer_name`, `company_name`, `form_url`, `expires_at`, `sender_name`), aby administrator mogl zweryfikowac tresc bez tworzenia realnego formularza.
- Dostepne placeholdery sa walidowane przy zapisie i obejmuja m.in. `form_url`, `expires_at`, `customer_name`, `company_name`, `sender_name`.
- Konfiguracja trafia do `admin_setting` pod namespace `form_handling` i jest odczytywana przez `app.services.form_generator`.
- Domyslne tresci po wdrozeniu sa przygotowane pod komunikacje z klientem Ksero Partner; administrator moze je nadpisac w panelu bez zmian w repo.

### 6. Konsola SQL & Raporty
- Tabulator: `SQL sandbox` i `Raporty`.
- `SQL sandbox`: edytor (`textarea` lub integracja `CodeMirror`), informacje o limicie (`SELECT` max 200 wierszy), przycisk `Wykonaj`. Poniżej tabela wyników (z paginacją) oraz log wykonania.
- `Raporty`: kafelki z gotowymi zapytaniami (np. „SMS per użytkownik”, „Połączenia wg statusu”). Po kliknięciu ładowane są dane w tabeli i mini-wykres (Chart.js).
- API: `POST /admin/database/query` (ograniczone do `SELECT`), `GET /admin/reports/sms-summary` (do wykonania).

### 7. Użytkownicy
- Tabela z kolumnami: `E-mail`, `Imię i nazwisko`, `Numer wewnętrzny`, `Telefon`, `Rola`, `Sekcje`, `Status`, `Ostatnie logowanie`, `Aktywne sesje` oraz akcjami (`Edytuj`, `Reset hasła`, `Dezaktywuj`, `Usuń`).
- Akcja `Edytuj` otwiera modal z formularzem zmiany numeru telefonu, roli i przypisanych sekcji (`admin`, `operator`, `generator`).
- Formularz dodawania wymusza podanie telefonu komórkowego – po utworzeniu konta system wysyła e-mail i SMS z danymi logowania.
- Akcja `Reset hasła` generuje nowe hasło tymczasowe, unieważnia aktywne sesje użytkownika i automatycznie wysyła powiadomienie e-mail + SMS z nowymi danymi logowania.
- API dla tworzenia użytkownika i resetu hasła zwraca pola `sms_queued` oraz `sms_recipient`, dzięki czemu interfejs potwierdza, czy SMS z danymi logowania został dodany do kolejki.
- Modal szczegółów prezentuje dane profilu, listę sesji (z informacją o unieważnieniu) oraz umożliwia edycję.
- Usuwanie blokuje własne konto administratora oraz ostatnie aktywne konto w roli `admin`.
- Panel boczny: statystyka liczby wysłanych SMS per użytkownik (wykres słupkowy), przyciski eksportu CSV.
- API: `/admin/users`, `/admin/users/{id}`, `/admin/users/{id}/reset-password`, `/admin/users/{id}/status`, `/admin/users/{id}` (DELETE).
- Sekcja E-mail oferuje formularz wysyłki wiadomości testowej na dowolny adres (`/admin/email/test`).
- Logowanie do panelu wymaga roli `admin`; użytkownicy z rolą `operator` są odrzucani przy próbie logowania.

### 8. Książka adresowa
- Widok `/admin/partials/contacts` ładuje listę kontaktów z backendu i pozwala filtrować dane po numerze, nazwisku, firmie, e-mailu oraz polu `firebird_id`.
- Formularz dodawania obejmuje numer telefonu (wymagany), numer wewnętrzny, dane personalne, e-mail, firmę, NIP, źródło, notatki oraz identyfikator `firebird_id` umożliwiający powiązanie z rekordem bazy Firebird (np. ERP).
- Edycja odbywa się w modalnym oknie: szczegóły prezentują metadane (`created_at`, `updated_at`), administratorzy i operatorzy mogą modyfikować dane; przy czym tylko rola `admin` widzi akcję „Usuń”. Każda operacja trafia do `admin_audit_log`.
- Usunięcie wymaga potwierdzenia i jest dostępne wyłącznie dla administratorów; po wykonaniu lista odświeża się bez przeładowania strony.
- Interfejs wykorzystuje Alpine.js do lokalnej walidacji oraz funkcję fetch z tokenem `X-Admin-Session` zapisanym w `localStorage`.

### 8a. Logowanie centralne i sekcje
- Strona główna (`/`) zawiera wyłącznie formularz logowania (`/auth/login`).
- Po poprawnym logowaniu użytkownik trafia na `/choice`, gdzie widzi sekcje przypisane do konta.
- Lista sekcji jest przechowywana per użytkownik jako zestaw `admin`, `operator`, `generator` i zwracana przez `/auth/me`.
- Widok `/choice` udostępnia dodatkowy formularz edycji własnego profilu (`/auth/profile`) z polami: imię, nazwisko, e-mail, numer wewnętrzny i telefon komórkowy.
- Widok `/choice` udostępnia również formularz zmiany hasła (`/auth/profile/change-password`) z polityką: minimum 9 znaków, co najmniej jedna duża litera, jedna cyfra i jeden znak specjalny.
- Wylogowanie strony głównej realizuje endpoint `/auth/logout`.
- Sekcja Użytkownicy w panelu administratora zawiera checkboxy przypisania sekcji podczas tworzenia i edycji konta.

### 8b. Generator formularzy (osobny flow)
- Generator formularzy został wydzielony poza panel administratora i działa pod adresem `/genform`.
- API pozostaje w module administracyjnym (`/admin/forms`), ale ekran operacyjny jest niezależny od `/admin`.
- Konfiguracja tresci wiadomosci i domeny publicznej jest jednak utrzymywana w sekcji `/admin -> Obsluga formularza`, aby nie mieszac operacyjnego generowania linkow z ustawieniami systemowymi.
- Publiczny formularz działa pod trasą `/formularz/{token}` i ma tryb etapowy:
  - krok 1: dane firmy dzierżawiącej sprzęt (adres siedziby i korespondencyjny rozbite na osobne pola, pola `Nr telefonu firmowy` i `E-mail firmowy`, opcja „Taki sam jak adres siedziby”, obowiązkowe pole `E-mail do e-faktur` z opcją „Kopiuj e-mail”),
  - krok 2: dane reprezentanta z możliwością dodania kolejnych osób (`E-mail reprezentanta`, `Telefon reprezentanta`, PESEL + auto-uzupełnienie daty urodzenia, wybór rodzaju dokumentu z listy `Dowód osobisty`/`Paszport`, daty dokumentu z wpisem ręcznym `dd-mm-rrrr` lub przez kalendarz; pola dat dokumentu maskują wpis `ddmmrrrr`, a data ważności domyślnie wylicza się jako `+10 lat` od daty wydania z możliwością ręcznej zmiany),
  - krok 3: podsumowanie i końcowe potwierdzenie.
- Ekran `/genform` podczas tworzenia linku umożliwia ustawienie daty ważności formularza; domyślnie ustawiane jest 7 dni od daty wygenerowania.
- Po wysyłce (dopiero po końcowym `Potwierdź`) dane są zapisywane jako szyfrowany payload (Fernet, klucz `ADMIN_SECRET_KEY`) i status zmieniany na `SUBMITTED`.
- Po zapisie system wysyła e-mail potwierdzający do klienta oraz tworzy SMS do użytkownika, który wygenerował link formularza.
- Lista formularzy zawiera akcje `Wyświetl` i `Usuń`.
- `Wyświetl` otwiera modal szczegółów z czytelnym układem danych firmy i reprezentantów (zgodnym z końcowym podsumowaniem formularza klienta), bez pustej przestrzeni w widoku wydruku/PDF, z przyciskiem kopiowania przy każdym polu oraz akcjami `Drukuj` i `PDF` opartymi o systemowe okno drukowania; wariant wydruku ma wewnętrzne marginesy i zwarty układ, a kolejne strony są dobierane naturalnie przez silnik drukowania.
- Modal pokazuje pełne dane klienta po statusie `SUBMITTED`, a dla statusów `GENERATED`/`DISPATCHED`/`EXPIRED` zwraca komunikat operacyjny („formularz został wysłany, ale nie został jeszcze wypełniony” itp.).
- Lista generatora zawiera kolumnę `Utworzone przez`.

## Komponenty wspólne
- **Karty**: kontenery z ikoną, wartością i opisem. Wersje `success`, `warning`, `danger`.
- **Tabele**: responsywne (scroll horizontal), z górnym paskiem filtrów i paginacją.
- **Modale**: warstwa półprzezroczysta, okno 600 px, przyciski `Anuluj`/`Zatwierdź`.
- **Toast**: komunikaty sukcesu/błędu w prawym górnym rogu, automatyczne wygaszanie.
- **Loader**: spinner w barwach `--primary`, wykorzystywany podczas requestów.

## Stany i przepływy
- Po zmianie konfiguracji (PUT) UI otrzymuje nową wartość i wyświetla toast „Zapisano”.
- W przypadku błędu backendu (4xx/5xx) formularz prezentuje komunikat nad polami (np. `alert`).
- Sekcja CTIP Live: WebSocket reconnect z eksponowaniem statusu (`Połączony`, `Rozłączony`, `Ponawianie...`).
- Konsola SQL: blokada przycisku `Wykonaj` gdy poprzednie zapytanie w toku.

## Powiązania z backendem

| Widok | Endpointy (obecne/planowane) |
|-------|------------------------------|
| Dashboard | `/admin/status/summary`, `/admin/status/database`, `/admin/auth/me` |
| Konfiguracja | `/admin/partials/config/database`, `/admin/config/database`, `/admin/partials/config/firebird`, `/admin/config/firebird`, `/admin/firebird/test`, `/admin/partials/config/ctip`, `/admin/config/ctip`, `/admin/partials/config/sms`, `/admin/config/sms`, `/admin/status/database` |
| Kopie | `/admin/backup/history`, `/admin/backup/run`, `/admin/backup/restore` |
| CTIP Live | `/admin/partials/ctip/live`, `/admin/partials/ctip/events`, `/admin/ctip/events`, `/admin/ctip/ws`, `/admin/config/ctip`, `POST /admin/ctip/restart` (plan) |
| SerwerSMS | `/admin/partials/config_sms`, `/admin/config/sms`, `/admin/sms/test`, `/sms/account`, `/sms/history?status=SENT&limit=20` |
| Obsługa formularza | `/admin/partials/config/form-handling`, `/admin/config/form-handling` |
| Książka adresowa | `/admin/partials/contacts`, `/admin/contacts`, `/admin/contacts/{id}`, `/admin/contacts/by-number/{number}` |
| Logowanie centralne | `/`, `/choice`, `/auth/login`, `/auth/me`, `/auth/profile`, `/auth/profile/change-password`, `/auth/logout` |
| Generator formularzy | `/genform`, `/admin/forms`, `/admin/forms/{id}`, `/formularz/{token}` |
| E-mail | `/admin/partials/config_email`, `/admin/config/email`, `/admin/email/test` |
| Konsola SQL | `POST /admin/database/query`, `/admin/reports/*` |
| Użytkownicy | `/admin/users`, `/admin/users/{id}`, `/admin/users/{id}/reset-password`, `/admin/reports/sms-summary` |

## Backlog UI
1. Prototyp komponentów (Storybook lub katalog HTML) dla kart, tabel i formularzy.
2. Implementacja menu bocznego z pamiętaniem ostatnio wybranej sekcji (localStorage).
3. Mechanizm obsługi tokenów `X-Admin-Session` w fetch API (odświeżanie/wylogowanie).
4. Integracja WebSocket dla CTIP z buforowaniem i filtrowaniem po stronie klienta.
5. Zabezpieczenie operacji destrukcyjnych (potwierdzenia, double-submit guard).
6. Testy E2E (Playwright) dla scenariuszy: logowanie, zmiana konfiguracji, uruchomienie kopii, wysyłka testowa SMS.

## Dokumentacja i dalsze kroki
- Po każdym wdrożonym widoku uaktualnić `README.md` (sekcja „Panel administratora”) oraz dopisać instrukcje użytkowe do `docs/projekt`.
- W archiwum sesji (`docs/archiwum/arch12.10.txt`) rejestrować wykonane kroki integracji.
- Synchronizować wymagania z backendem – przed implementacją UI upewnić się, że endpointy planowane w tabeli powyżej są dostępne lub mają harmonogram pracy.
