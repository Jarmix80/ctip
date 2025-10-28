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
| Kopie zapasowe | Podgląd historii `pg_dump`, uruchamianie/odtwarzanie | Tabela z filtrami, modale potwierdzające, log przebiegu |
| CTIP Live | Monitor protokołu, restart klienta, status handshake | Panel z WebSocket (lista zdarzeń), karty statusu, przyciski akcji |
| Automatyzacje IVR | Mapowanie cyfr IVR na SMS i numery wewnętrzne | Lista reguł, formularz CRUD, audyt operacji, walidacja unikalności |
| SerwerSMS | Parametry operatora, tryb demo, wysyłka testowa | Formularz, przełącznik demo, moduł wysyłki testowej, saldo |
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
- Karta `Kopie`: placeholder do czasu integracji modułu backupów (akcja otwiera sekcję kopii).

### 2. Konfiguracja bazy
- Formularz HTMX/Alpine z polami `Host`, `Port`, `Baza`, `Użytkownik`, `Hasło`, `SSL` – wczytywany z `/admin/partials/config_database`.
- Hasło nie jest wyświetlane – wpisanie nowej wartości nadpisuje zapisane hasło, pozostawienie pustego pola pozostawia je bez zmian.
- Sekcja statusowa z wynikiem ostatniego testu (`/admin/status/database`), przycisk „Testuj połączenie” wywołuje endpoint i prezentuje rezultat.
- Po poprawnym zapisie (`PUT /admin/config/database`) formularz aktualizuje wartości oraz emituje toast „Zapisano konfigurację bazy”.
- Panel docelowo rozszerzymy o instrukcje migracji (link do `docs/baza/schema_ctip.sql`).

### 3. Kopie zapasowe
- Główny widok: tabela (`Data`, `Rozmiar`, `Status`, `Operator`, `Plik`). Filtr dat i statusów.
- Przyciski nad tabelą: `Utwórz kopię`, `Wgraj i przywróć`, `Odśwież`.
- Modale:
  - `Utwórz kopię` – wybór katalogu docelowego (predefiniowane profile), przełącznik kompresji.
  - `Przywróć` – upload pliku `.dump`, ostrzeżenie o nadpisaniu danych.
- Panel logów (prawej kolumnie) z `tail` przebiegu zadania.
- API: `POST /admin/backup/run`, `POST /admin/backup/restore`, `GET /admin/backup/history`.

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

### 6. Konsola SQL & Raporty
- Tabulator: `SQL sandbox` i `Raporty`.
- `SQL sandbox`: edytor (`textarea` lub integracja `CodeMirror`), informacje o limicie (`SELECT` max 200 wierszy), przycisk `Wykonaj`. Poniżej tabela wyników (z paginacją) oraz log wykonania.
- `Raporty`: kafelki z gotowymi zapytaniami (np. „SMS per użytkownik”, „Połączenia wg statusu”). Po kliknięciu ładowane są dane w tabeli i mini-wykres (Chart.js).
- API: `POST /admin/database/query` (ograniczone do `SELECT`), `GET /admin/reports/sms-summary` (do wykonania).

### 7. Użytkownicy
- Tabela z kolumnami: `E-mail`, `Imię i nazwisko`, `Numer wewnętrzny`, `Telefon`, `Rola`, `Status`, `Ostatnie logowanie`, `Aktywne sesje` oraz akcjami (`Szczegóły`, `Reset hasła`, `Dezaktywuj`, `Usuń`).
- Formularz dodawania wymusza podanie telefonu komórkowego – po utworzeniu konta system wysyła e-mail i SMS z danymi logowania.
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
| Konfiguracja | `/admin/partials/config_database`, `/admin/config/database`, `/admin/partials/config_ctip`, `/admin/config/ctip`, `/admin/partials/config_sms`, `/admin/config/sms`, `/admin/status/database` |
| Kopie | `/admin/backup/history`, `/admin/backup/run`, `/admin/backup/restore` |
| CTIP Live | `/admin/partials/ctip/live`, `/admin/partials/ctip/events`, `/admin/ctip/events`, `/admin/ctip/ws`, `/admin/config/ctip`, `POST /admin/ctip/restart` (plan) |
| SerwerSMS | `/admin/partials/config_sms`, `/admin/config/sms`, `/admin/sms/test`, `/sms/account`, `/sms/history?status=SENT&limit=20` |
| Książka adresowa | `/admin/partials/contacts`, `/admin/contacts`, `/admin/contacts/{id}`, `/admin/contacts/by-number/{number}` |
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
