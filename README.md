![CI](https://github.com/Jarmix80/ctip/actions/workflows/ci.yml/badge.svg)

# CTIP – kolektor zdarzeń CTI i dystrybucja alertów SMS

CTIP agreguje zdarzenia telefoniczne emitowane przez centralę Slican, zapisuje je w bazie PostgreSQL oraz inicjuje wysyłkę powiadomień SMS na podstawie mapowania IVR. Projekt przeznaczony jest do wdrożeń on-premise, w których administrator musi zapewnić niezawodny odbiór strumienia CTIP i dalsze przetwarzanie danych.

## Najważniejsze komponenty
- `collector_full.py` – produkcyjny kolektor CTIP: łączy się z centralą, koreluje zdarzenia, persystuje rekordy w schemacie `ctip` oraz rejestruje zadania SMS.
- `collector_service.py` – wrapper w formie usługi Windows utrzymujący działanie `collector_full.py` i restartujący proces po awarii; automatycznie dopina ścieżki `pywin32` oraz dodaje katalog `pywin32_system32` do ścieżki DLL (start jako `pythonservice.exe`), wymagane wcześniejsze `pywin32_postinstall`.
- `sms_sender.py` – pętla pollingująca kolejkę `sms_out`; implementacja `send_sms` wymaga podpięcia właściwego operatora.
- `ctip_sniff.py` – narzędzie diagnostyczne zapisujące surowy strumień CTIP do pliku w celu analizy protokołu.
- `conect_sli.py` – lekki monitor CTIP uruchamiany w trybie interaktywnym, wykonujący `aWHO`/`aLOGA` i wypisujący zdarzenia na STDOUT.
- `collector_fullwork.py` oraz katalog `docs/` – materiały warsztatowe i referencyjne, niezalecane do użycia w produkcji.
- `app/api/routes/admin_*` – moduł API panelu administratora (logowanie, konfiguracja PostgreSQL/Firebird/CTIP/SerwerSMS/SMTP, audyt zmian oraz health-checki `/admin/status/summary`, `/admin/status/database`, `/admin/status/ctip`, `/admin/status/sms`).
- `app/web/admin_ui.py` + `app/templates/admin/` – interfejs administracyjny w technologii HTMX + Alpine (adres `/admin`).
- `app/api/routes/admin_contacts.py` + `app/services/admin_contacts.py` – warstwa API i logika książki adresowej z obsługą pola `firebird_id`.
- `app/api/routes/admin_forms.py` + `app/services/form_generator.py` – generator jednorazowych formularzy klienta (token haszowany, zapis danych zaszyfrowanych).
- `app/web/genform_ui.py` + `app/templates/genform/` – osobny flow handlowca pod adresem `/genform` (logowanie, generowanie linku, lista statusów).
- `app/web/contracts_ui.py` + `app/templates/contracts/` + `app/api/routes/admin_contracts.py` – dashboard „Obsługa umów” pod adresem `/contracts` (formularze SUBMITTED, weryfikacja klienta w Firebird, lista urządzeń z arkusza Google i status dopasowania).
- `app/web/form_ui.py` + `app/templates/public/` – publiczny, jednorazowy formularz klienta pod adresem `/formularz/{token}`.
- `app/api/routes/portal_auth.py` + `app/static/root/root.js` – centralne logowanie na stronie głównej (`/`) oraz wybór sekcji na osobnym widoku `/choice`.
- `app/api/routes/admin_firebird.py` + `app/services/firebird_client.py` – konfiguracja i test połączenia z bazą Firebird programu Menadżer Serwisu.
- `inbox/` – katalog wymiany plików z Windows (dropzone), przeznaczony na pliki robocze poza wersjonowaniem Git.
- `scripts/inbox_samba.sh` – uruchamianie udziału SMB dla `inbox/` (mapowany dysk w Windows).
- `scripts/firebird_clone_local.py` – utworzenie lokalnej kopii roboczej pliku `.fdb` na podstawie `FB_DATABASE` i `FB_LOCAL_COPY_PATH`.
- `integrations/google_sheets/update_calendar_and_devices.py` – aktualizacja arkuszy Google (`Kalendarz_wiersze`, `Urzadzenia`) z formatowaniem i slotami zdarzeń dziennych.

## Wymagania systemowe
- Python 3.11 lub nowszy (z bibliotekami `psycopg` oraz – opcjonalnie dla Windows – `pywin32`; `uvloop` instalowane tylko na Linux dzięki warunkowi w `requirements.txt`).
- Dostępny serwer PostgreSQL >= 13 z utworzonym schematem `ctip`.
- Łącze sieciowe TCP z centralą Slican (port CTIP domyślnie 5524).
- System Linux lub Windows (dla usługi Windows wymagane uprawnienia administratora).

## Konfiguracja środowiskowa
Ustawienia lokalnych narzędzi automatyzacji (np. dostęp SMB dla Codex) są zapisywane w `.codex/smb_settings.json`, a sekrety w `.env`; oba pliki pozostają poza wersjonowaniem.

### Uruchomienie Codex
Skrypt `scripts/run_codex.sh` uruchamia Codex w kontekście repozytorium i automatyzuje kroki wymagane przez `AGENTS.md`:
- ustawia katalog roboczy na root projektu i weryfikuje obecność `AGENTS.md`,
- wczytuje `.env` z eksportem zmiennych,
- tworzy (jeśli brak) i aktywuje `.venv`,
- ustawia `CODEX_HOME` na `.codex` i uruchamia `codex` lub `openai codex`.

Przykład: `./scripts/run_codex.sh` (opcjonalnie z parametrami, np. `./scripts/run_codex.sh --help`).

### Katalog wymiany plikow (mapowany dysk Windows)
Do wygodnego wrzucania plikow bez komend po stronie Windows przygotowany jest katalog `inbox/` oraz udział SMB.

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
set -a && source .env && set +a
python scripts/inbox_contract_watcher.py --inbox-dir inbox
```

Tryb ciagly (nasluch nowych/zmienionych PDF):
```bash
source .venv/bin/activate
set -a && source .env && set +a
python scripts/inbox_contract_watcher.py --inbox-dir inbox --watch
```

Uwagi operacyjne:
- parser wykorzystuje `pypdf` i najpierw probuje odczytu warstwy tekstowej PDF (bez OCR),
- jezeli dokument jest pustym wzorem (bez wypelnionych pol), wynik moze nie zawierac `nip` i/lub `contract_number`,
- lista wszystkich wykrytych kandydatow jest zapisywana w polach `nips_found` i `contract_number_candidates`.

### Zmienne środowiskowe kolektora (`collector_full.py`)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `PBX_HOST` | `192.168.0.11` | Adres centrali Slican CTIP (CP-000 NO03914 v1.23.0140/15). |
| `PBX_PORT` | `5524` | Port TCP protokołu CTIP. |
| `PBX_PIN` | `1234` | PIN do komendy `LOGA`. |
| `PGHOST` / `PGPORT` | `192.168.0.8` / `5433` | Adres/port PostgreSQL. |
| `PGDATABASE`, `PGUSER`, `PGPASSWORD` | `ctip`, `appuser`, `change_me` | Dane uwierzytelniające. |
| `PGSSLMODE` | `disable` | Tryb TLS (ustaw na `require`, jeśli serwer wymusza TLS). |
| `SOCK_CONNECT_TIMEOUT`, `SOCK_RECV_TIMEOUT` | `5`, `5` | Limity czasowe gniazda w sekundach. |
| `RECONNECT_DELAY_SEC` | `3` | Odstęp między próbami ponownego połączenia. |
| `LOGIN_ACK_TIMEOUT` | `8` | Limit czasu (s) oczekiwania na `aOK LOGA` po wysłaniu polecenia. |
| `PAYLOAD_ENCODING` | `latin-1` | Kodowanie zapisu surowego payloadu. |
| `LOG_PREFIX` | `[CTIP]` | Prefiks logów widocznych na STDOUT. |

Uwaga operacyjna: centrala Slican (`PBX_HOST = 192.168.0.11`) pracuje w tej samej podsieci warstwy dostępowej co host kolektora. Należy zapewnić trasowanie i reguły zapory pozwalające na dwukierunkową komunikację w sieci lokalnej 192.168.0.0/24. Po przełączeniu WSL w tryb mostkowany (zob. `docs/projekt/update_wsl.md`) host kolektora ma adres `192.168.0.133/24` (`hostname -I`). Zaktualizuj zasady zapory Windows/`pg_hba.conf`, aby dopuścić ten adres do serwera PostgreSQL (`PGHOST:PGPORT`, domyślnie `192.168.0.8:5433`); brak reguły skutkuje timeoutem przy logowaniu administratora.

### Zmienne środowiskowe modułu SMS (`sms_sender.py`)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` | jak wyżej | Dostęp do PostgreSQL. |
| `POLL_SEC` | `3` | Okres odpytywania kolejki `sms_out`. |
| `SMS_DEFAULT_SENDER` | `KseroPartner` | Domyślna nazwa nadawcy przekazywana do API. |
| `SMS_TYPE` | `eco+` | Kanał/typ wiadomości (zgodnie z konfiguracją operatora). |
| `SMS_API_URL` | `https://api2.serwersms.pl` | Bazowy adres HTTPS API. |
| `SMS_API_TOKEN` | *(puste)* | Token dostępowy (opcjonalnie, gdy operator go udostępnia). |
| `SMS_API_USERNAME`, `SMS_API_PASSWORD` | *(puste)* | Login i hasło do HTTPS API (jeśli nie używamy tokenu). |
| `SMS_TEST_MODE` | `true` | Umożliwia wysyłkę w trybie testowym bez naliczania kosztów. |

### Zmienne środowiskowe modułu Firebird (Menadżer Serwisu)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `FB_HOST` | `192.168.0.8` | Host serwera Firebird dla Menadżera Serwisu. |
| `FB_PORT` | `3050` | Port usługi Firebird. |
| `FB_MODE` | `network` | Aktywny tryb pracy Firebird: `network` (baza sieciowa) lub `local` (baza lokalna). |
| `FB_DATABASE` | `D:/BAZA_MS_KP/BAZAMS.FDB` | Ścieżka bazy Firebird (po stronie hosta Firebird). |
| `FB_USER`, `FB_PASSWORD` | `SYSDBA`, `masterkey` | Dane logowania Firebird. |
| `FB_CHARSET` | `WIN1250` | Kodowanie sesji Firebird. |
| `FB_ROLE` | *(puste)* | Rola Firebird (opcjonalnie). |
| `FB_LOCAL_COPY_PATH` | `inbox/firebird/menadzer_serwisu.fdb` | Docelowa ścieżka lokalnej kopii roboczej bazy. |

Kopię lokalną (po podmontowaniu źródłowego pliku `.fdb`) można wykonać skryptem:
`python scripts/firebird_clone_local.py --force`.

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

### Zmienne środowiskowe panelu administratora (`app/api/routes/admin_*`)
| Nazwa | Domyślna wartość | Opis |
|-------|------------------|------|
| `ADMIN_SECRET_KEY` | *(puste)* | Opcjonalny klucz (Fernet, base64) do szyfrowania wartości poufnych zapisywanych w `ctip.admin_setting`. |
| `ADMIN_SESSION_TTL_MINUTES` | `60` | Czas życia tokenu sesji administratora (w minutach). |
| `ADMIN_SESSION_REMEMBER_HOURS` | `72` | Czas życia sesji, gdy użytkownik wybierze opcję „Zapamiętaj mnie” (w godzinach). |
| `ADMIN_PANEL_URL` | `http://localhost:8000/admin` | Publiczny adres logowania używany w e-mailach i SMS z danymi kont. |
| `FORM_PUBLIC_BASE_URL` | `http://localhost:8000` | Publiczny adres bazowy używany do budowy linków `/formularz/{token}`. |

### Lista kontrolna przed uruchomieniem
1. Utwórz/aktywuj środowisko `.venv` i zainstaluj zależności: `python3 -m venv .venv`, następnie `source .venv/bin/activate` oraz `pip install -r requirements.txt`.
2. Uzupełnij plik `.env` wszystkimi parametrami (PostgreSQL, Firebird, CTIP, SerwerSMS) oraz wygeneruj `ADMIN_SECRET_KEY` (`python - <<<'import secrets, base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'`).
3. Wykonaj migracje: `alembic upgrade head` (dodaje również tabele panelu administracyjnego i nowe sekwencje).
4. Dodaj pierwszego administratora, np. w SQL: `INSERT INTO ctip.admin_user (email, role, password_hash, is_active) VALUES (...)`; skrót hasła wygeneruj funkcją `hash_password` z `app.services.security`.
5. Zweryfikuj instalację: `source .venv/bin/activate && python -m unittest` oraz testowe logowanie do `/admin/auth/login` (nagłówek `X-Admin-Session`).

### Uruchamianie kolektora w WSL z pliku `.env`
W środowiskach Windows Subsystem for Linux zaleca się przechowywanie konfiguracji w pliku `.env` (format `KEY=VALUE`). Przed startem `collector_full.py` oraz `sms_sender.py` należy wczytać zmienne, np.:
```bash
set -a
source .env
set +a
python collector_full.py
```
Równolegle można uruchomić moduł SMS, korzystając z tych samych zmiennych środowiskowych:
```bash
python sms_sender.py
```
Procedura wymaga wcześniejszego zainstalowania zależności opisanych w sekcji „Instalacja i uruchomienie na Linux”.

## Procedura inicjalizacji CTIP
1. Po zestawieniu gniazda TCP (domyślnie `192.168.0.11:5524`) wyślij polecenie `aWHO`, aby sprawdzić odpowiedź centrali. Prawidłowa centrala Slican NCP melduje się komunikatem w formacie `aOK NCP-000 NO03914 v1.23.0140/15 2025.10.10 01:54'59`.
2. Po potwierdzeniu odpowiedzi wykonaj dokładnie jedno `aLOGA <PIN>` (np. `aLOGA 1234`). Komenda aktywuje monitorowanie wszystkich numerów. Projekt zakłada pojedynczą aktywną sesję – aby zakończyć nasłuch, należy zamknąć gniazdo TCP/IP; centrala nie udostępnia komendy wylogowującej.
3. Wszystkie komendy wysyłane do centrali muszą mieć prefiks `a`. Do szybkich testów można wykorzystać `telnet 192.168.0.11 5524` lub tryb RAW w ulubionym kliencie TCP.
4. `collector_full.py` automatyzuje powyższą sekwencję, loguje identyfikator centrali i przerywa pracę, gdy `aLOGA` zostanie odrzucone (np. z powodu aktywnej sesji innego kolektora).

## Przygotowanie bazy danych
Schemat `ctip` musi być dostarczony zewnętrznie (migracje Alembic lub dump z katalogu `docs/baza/`). Od wersji 0.2 kolektor nie wykonuje operacji DDL – podczas startu weryfikuje obecność wymaganych kolumn (`calls`, `call_events`, `sms_out`, `ivr_map`, `contact`, `contact_device`). W przypadku braków `collector_full.py` przerwie pracę i wypisze listę brakujących kolumn. Przed uruchomieniem kolektora ustaw `.env` (np. na podstawie `.env.example`), wykonaj `alembic upgrade head`, a w sytuacjach awaryjnych możesz jednorazowo zaimportować zrzut SQL (np. `psql $DATABASE_URL -f docs/baza/schema_ctip_11.10.2025.sql`). Po migracji uzupełnij mapę IVR. Wszystkie znaczniki czasu w tabelach `calls`, `call_events`, `contact`, `sms_out` i `sms_template` muszą mieć typ `timestamp with time zone`, ponieważ backend zapisuje daty w UTC i udostępnia je operatorowi – brak strefy czasowej kończy się błędem 500 podczas wysyłki SMS lub pobierania statystyk.

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
3. Co dalej po `pip install -r requirements.txt`:
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
4. Weryfikacja po restarcie:
```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty StatusCode
```
5. W panelu administratora (`/admin`) przejdź do sekcji `Baza Firebird`, zapisz konfigurację i wykonaj `Testuj połączenie`.

Aktualne nazwy usług produkcyjnych (Windows Server):
- `CollectorService` – Collector Service (`collector_full.py`)
- `CTIP-SMS` – moduł wysyłki SMS
- `CTIP-Web` – backend/panel web

## Backend API (FastAPI)
Warstwa REST udostępniająca dane CTIP i kolejkę SMS została zrealizowana w katalogu `app/`. Do pracy wymaga zależności opisanych w `pyproject.toml` (`fastapi`, `uvicorn`, `sqlalchemy`, `asyncpg`, `pydantic-settings`).

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
- Layout i nawigacja są sterowane przez Alpine.js (`admin.js`), a sekcje ładowane dynamicznie przez HTMX (`/admin/partials/...`). Udostępnione moduły obejmują Dashboard, konfigurację PostgreSQL/Firebird/CTIP/SerwerSMS/E-mail oraz pełny widok „Użytkownicy”.
- Strona główna (`/`) działa jako centralny punkt logowania: formularz używa API `/auth/login`, a po poprawnym logowaniu przekierowuje na `/choice`.
- Widok `/choice` pokazuje wyłącznie sekcje przypisane do konta (`admin`, `operator`, `generator`) i obsługuje wylogowanie (`/auth/logout`).
- Każdy panel roboczy (`/admin`, `/operator`, `/genform`, `/contracts`) zawiera listę rozwijaną „Sekcja”, która pozwala szybko przełączyć się do innego modułu lub wrócić do `/choice`.
- Logowanie odbywa się przez `/admin/auth/login` (formularz na stronie głównej). Token sesji (`X-Admin-Session`) zapisywany jest w `localStorage`, a kolejne żądania HTMX/fetch automatycznie go dołączają.
- W razie odpowiedzi 401/403 podczas ładowania sekcji panel samoczynnie czyści token, wylogowuje użytkownika i sygnalizuje wygaśnięcie sesji.
- Dashboard udostępnia aktywne akcje dla kafelków statusu: `Testuj połączenie` (baza danych), `Edytuj konfigurację` oraz `Diagnostyka` (CTIP i SerwerSMS). Diagnostyka pobiera dane z `/admin/status/<moduł>` i wyświetla je w panelu bocznym.
- Formularze konfiguracji: PostgreSQL (`/admin/partials/config/database`), Firebird (`/admin/partials/config/firebird`), CTIP (`/admin/partials/config/ctip`) oraz SerwerSMS (`/admin/partials/config/sms`) zapisują dane przez `/admin/config/...` i zapewniają testy połączeń (`/admin/status/database`, `/admin/firebird/test`, `/admin/sms/test`).
- Sekcja SerwerSMS zawiera monitor pracy `sms_sender`: widok logu (`/admin/partials/sms/logs`) prezentuje końcówkę pliku `docs/LOG/sms/sms_sender_<YYYY-MM-DD>.log`, a tabela historii (`/admin/partials/sms/history`) odświeża ostatnie wysyłki z `ctip.sms_out` i pozwala filtrować je po statusach (`NEW`, `RETRY`, `SENT`, `ERROR`, `SIMULATED`). Formularz wysyłki testowej normalizuje numer do formatu E.164 (obsluga prefiksu wyjscia na zewnetrzna linie `0`, prefiksu `00` oraz korekta `+0`), a poprawna próba (w trybie testowym lub produkcyjnym) natychmiast pojawia się w logu i historii.
- Sekcja CTIP udostępnia podgląd na żywo (`/admin/partials/ctip/live`) z filtrowaniem po wewnętrznych numerach oraz wbudowanym formularzem konfiguracji; kafelek na dashboardzie oferuje zarówno edycję parametrów, jak i szybkie przejście do widoku live. Aktualizacje są dostarczane kanałem WebSocket (`/admin/ctip/ws`), który pomija ramki keep-alive typu `T`.
- Sekcja Automatyzacje IVR (`/admin/partials/ctip/ivr-map`) pozwala zarządzać mapowaniami cyfr IVR na numery wewnętrzne, treścią automatycznych SMS i ich aktywnością. Każda operacja (utworzenie, aktualizacja, usunięcie) jest audytowana i natychmiast dostępna dla kolektora bez restartu.
- Sekcja SMS dla dzwoniacych (`/admin/partials/call-sms`) udostepnia konfiguracje scenariuszy przychodzacych/wychodzacych (odebrane, nieodebrane, ponowne), tryb ograniczen „Nigdy / Po X dniach / Zawsze”, liste numerow wykluczonych, scenariusz po godzinach pracy wyzwalany numerem wewnetrznym (np. 500) oraz masowa wysylke do unikalnych numerow z historii polaczen.
- Sekcja E-mail umożliwia konfigurację serwera SMTP (host, port, logowanie, nadawca), test połączenia oraz wysłanie wiadomości testowej na wskazany adres (`/admin/email/test`). Wynik jest prezentowany w UI i zapisywany w audycie.
- Sekcja Baza Firebird (Menadżer Serwisu) umożliwia zapis połączenia (`/admin/config/firebird`) i test logowania (`/admin/firebird/test`) z użyciem aktualnych danych środowiskowych lub nadpisania z formularza; panel pozwala przełączać aktywną bazę `sieciowa`/`lokalna`.
- W trybie `lokalna` panel automatycznie testuje połączenie pod `127.0.0.1` i wykorzystuje ścieżkę `FB_LOCAL_COPY_PATH`; pola hosta i ścieżki bazy sieciowej są wyszarzone.
- Sekcja Kopie zapasowe (`/admin/partials/backups`) udostępnia konfigurację harmonogramu (`06:00`, `20:00`), retencji, zakresu archiwizacji (CTIP/Firebird/Optima), wyboru miejsca zapisu (lokalne/sieciowe), dane Office 365/SharePoint (Tenant ID, Client ID, Site ID, Drive ID) oraz osobne foldery docelowe: `BackupKP/CTIP`, `BackupKP/Menadzer_Serwisu/prod`, `BackupKP/Menadzer_Serwisu/test`, `BackupKP/Optima`. Widok historii pokazuje status i potwierdzenie (suma kontrolna `.sha256`).
- Konfiguracja backupu jest zapisywana przez API (`/admin/backup/config`), natomiast uruchamianie i przywracanie pełne jest zablokowane poza środowiskiem produkcyjnym (`BACKUP_EXECUTION_ENABLED=false`).
- Dla Windows dostępny jest skrypt tworzący lokalną strukturę katalogów backupu: `scripts/windows/create_backup_structure.ps1` (domyślny root: `D:\Backup_CTIP_MS_optima`).
- Dla SharePoint dostępny jest skrypt konfigurujący czytelny widok biblioteki backupów: `scripts/windows/setup_sharepoint_backup_view.ps1` (grupowanie po folderach i sortowanie malejąco po dacie modyfikacji).
- Dla SharePoint dostępny jest też skrypt tworzący osobną stronę dashboardu backupów (`SitePages/BackupKP-Dashboard.aspx`) z tabelą linków do widoków: `scripts/windows/create_sharepoint_backup_dashboard.ps1` (układ strony `Article`; ponowne uruchomienie dla istniejącej strony wymaga przełącznika `-OverwritePage`).
- Oba skrypty SharePoint automatycznie wykrywają bibliotekę dokumentów (priorytet: parametr `LibraryTitle`, potem `Backup_KP`, `Documents`, `Shared Documents`, `Dokumenty`).
- Oba skrypty SharePoint wspierają dwa tryby logowania: `device login` (użytkownik) oraz `app-only` (`ClientSecret`). W trybie app-only skrypt najpierw próbuje `Connect-PnPOnline -ClientSecret`, a przy błędzie automatycznie przechodzi na fallback OAuth (`-AccessToken`) i zwraca rozszerzoną diagnostykę autoryzacji.
- W nagłówku sekcji backupów znajduje się szybki link do witryny SharePoint (`https://kseropartner.sharepoint.com/sites/Backups`).
- Sekcja Książka adresowa (`/admin/partials/contacts`) udostępnia CRUD kontaktów z wyszukiwarką po numerze, nazwisku, e-mailu i identyfikatorze Firebird; formularze pozwalają przypisać numer wewnętrzny, notatki operacyjne oraz pole `firebird_id` wykorzystywane do mapowania z bazą Firebird.
- Generator formularzy działa jako osobny flow pod adresem `/genform` (poza panelem `/admin`) i jest dostępny po zalogowaniu kontem `operator` albo `admin`. Moduł korzysta z API `/admin/forms`, generuje jednorazowe linki `/formularz/{token}`, pozwala ustawić datę ważności formularza (domyślnie 7 dni), zapisuje w bazie wyłącznie hash tokenu i przechowuje dane klienta w postaci zaszyfrowanej (Fernet, `ADMIN_SECRET_KEY`).
- Publiczny formularz `/formularz/{token}` działa etapowo: krok 1 (dane firmy z rozbiciem adresu siedziby i korespondencyjnego, obowiązkowym polem `E-mail do e-faktur` oraz opcją „taki sam jak adres siedziby” i „Kopiuj e-mail”), krok 2 (jeden lub wielu reprezentantów: PESEL, automatyczne uzupełnianie daty urodzenia, wybór rodzaju dokumentu z listy, daty dokumentu z wpisem ręcznym `dd-mm-rrrr` lub kalendarzem), krok 3 (podsumowanie i końcowe potwierdzenie). Dane trafiają do systemu dopiero po kliknięciu `Potwierdź i wyślij`.
- Po zatwierdzeniu formularza system wysyła e-mail potwierdzający do klienta oraz dodaje do kolejki SMS powiadomienie dla użytkownika, który wygenerował link.
- Przycisk `Kopiuj link` w `/genform` korzysta z Clipboard API, a gdy środowisko blokuje kopiowanie (np. brak `https`), automatycznie przełącza się na fallback `execCommand("copy")`.
- Ekran `/genform` udostępnia akcje `Wyświetl`/`Usuń` dla każdego wniosku oraz okno szczegółów: dla statusu `SUBMITTED` prezentowane są odszyfrowane dane klienta, a dla pozostałych statusów czytelna informacja operacyjna (np. „formularz został wysłany, ale nie został jeszcze wypełniony”).
- Tabela generatora zawiera kolumnę `Utworzone przez`, dzięki czemu od razu widać operatora/administratora, który wygenerował formularz.
- Dashboard `/contracts` (Obsługa umów) pobiera formularze `SUBMITTED`, weryfikuje klienta po NIP w lokalnej kopii Firebird (`KLIENT`) oraz porównuje urządzenia z arkusza `Urzadzenia` względem tabeli `MASZYNA` (serial/ewidencja). Wynik pokazuje status „podłącz klienta” lub „utwórz klienta” oraz potwierdzenie urządzeń.
- Sekcja Użytkownicy umożliwia przypisanie dostępu do sekcji (`admin`, `operator`, `generator`) niezależnie od roli konta; strona główna i API respektują te uprawnienia przy prezentacji i autoryzacji modułów.
- Treści SMS zawierające link jednorazowy lub potwierdzenie wypełnienia formularza są maskowane w historii panelu (`Treść ukryta`), aby nie ujawniać danych wrażliwych.
- Operatorzy logują się tym samym panelem co administratorzy i mają dostęp do Dashboardu, widoku CTIP, Książki adresowej (w trybie edycji bez możliwości usuwania kontaktów) oraz Generatora formularzy. Pozostałe sekcje pozostają zarezerwowane dla roli `admin`.
- W CTIP Live dostępny jest szybki edytor kontaktu: po wskazaniu zdarzenia można jednym formularzem zaktualizować dane numeru (imię, nazwisko, firma, e-mail, `firebird_id`, notatki), a wynik jest natychmiast synchronizowany z główną książką adresową.
- Sekcja Użytkowników wymaga podania telefonu komórkowego; udostępnia listę kont administratorów/operatorów, formularz tworzenia nowych użytkowników, edycję w modalach, reset hasła, zmianę statusu aktywności oraz usuwanie kont (blokada usunięcia własnego lub ostatniego administratora). Po utworzeniu konta oraz po resecie hasła automatycznie wysyłany jest e-mail i SMS z danymi logowania. Do panelu mogą logować się wyłącznie konta z rolą `admin`.
- Aby uruchomić panel lokalnie:
  1. `source .venv/bin/activate`
  2. `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
  3. Otwórz przeglądarkę na `http://localhost:8000/admin`
- Implementacja kolejnych sekcji (konsola SQL, raporty) jest prowadzona zgodnie z dokumentem `docs/projekt/panel_admin_ui.md`.
- `GET /contacts/{number}` oraz `GET /contacts?search=` – dane i wyszukiwarka kartoteki kontaktów.
- `GET /admin/config/firebird`, `PUT /admin/config/firebird` – odczyt i zapis konfiguracji połączenia Firebird (wymaga roli `admin`).
- `POST /admin/firebird/test` – test logowania do bazy Firebird z audytem (`config_firebird_test`, wymaga roli `admin`).
- `GET /admin/contacts`, `POST /admin/contacts`, `PUT /admin/contacts/{contact_id}`, `DELETE /admin/contacts/{contact_id}` – zarządzanie wpisami książki adresowej (wymaga nagłówka `X-Admin-Session` i roli `admin`); obsługa pola `firebird_id` umożliwia powiązanie z rekordami bazy Firebird.
- `GET /admin/contacts/by-number/{number}` – wyszukaj kontakt po numerze MSISDN (wymagane `X-Admin-Session`; dostęp dla roli `admin` i `operator`).
- `POST /auth/login`, `GET /auth/me`, `POST /auth/logout` – centralne logowanie strony głównej i wybór sekcji na podstawie przypisanych uprawnień.
- `GET /auth/profile`, `PUT /auth/profile` – podgląd i edycja własnych danych użytkownika (imię, nazwisko, e-mail, numer wewnętrzny, telefon) z poziomu `/choice`.
- `POST /auth/profile/change-password` – zmiana własnego hasła z poziomu `/choice` (wymagania: min. 9 znaków, duża litera, cyfra, znak specjalny).
- `GET /admin/forms`, `POST /admin/forms`, `GET /admin/forms/{id}`, `DELETE /admin/forms/{id}` – lista/generowanie/podgląd/usuwanie jednorazowych formularzy (wymagane uprawnienie sekcji `generator`).
- `GET /genform` – osobny ekran handlowca do generowania i podglądu formularzy.
- `GET /contracts`, `GET /admin/contracts/dashboard` – dashboard „Obsługa umów” i dane integracyjne (formularze SUBMITTED, Firebird, arkusz Google `Urzadzenia`), wymagane uprawnienie sekcji `generator`.
- `GET /formularz/{token}`, `POST /formularz/{token}` – publiczny formularz klienta oparty o jednorazowy token.
- `GET /admin/backup/history` – lista plików kopii zapasowych z katalogu `backups/` (wymaga roli `admin`).
- `GET /admin/backup/config`, `PUT /admin/backup/config` – odczyt i zapis konfiguracji modułu kopii zapasowych (harmonogram, zakres, lokalizacja, Office 365, konfiguracja SQL Optimy i wybór baz do archiwizacji).
- `POST /admin/backup/office365/test` – test połączenia OAuth/Graph do SharePoint (z automatycznym ustaleniem `Drive ID` na podstawie `Site ID`, jeśli `Drive ID` nie jest podany).
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
- W prawym dolnym rogu panelu operatora widnieje wersja i data aktualizacji interfejsu (obecnie: 0.2.2 - Aktualizacja 2026-01-16).
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
- Skrót procedury:
  - przygotowanie zależności: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`;
  - kopiowanie `.env.test.example` → `.env.test`, wypełnienie `PG*`, pozostawienie `PBX_HOST=127.0.0.1`, `PBX_PORT=5525`, `SMS_TEST_MODE=true`, `ADMIN_PANEL_URL=http://localhost:18000/admin`;
  - załadowanie zmiennych i migracje: `set -a && source .env.test && set +a && alembic upgrade head`;
  - start mocka CTIP: `python scripts/mock/mock_ctip_server.py --port 5525 --loop --log-level INFO`;
  - uruchomienie stosu w tmux: `./run_test_stack_tmux.sh` (okna `collector`, `uvicorn` na porcie 18000, `sms-sender` z `SMS_TEST_MODE`);
  - podgląd/zatrzymanie: `tmux attach -t ctip-stack-test`, zakończenie `kill-session -t ctip-stack-test` i `Ctrl+C` w oknie mocka.
- Analiza ryzyk równoległej pracy produkcji i testów: `docs/projekt/dual_site_analysis.md`.
- Start całości jednym poleceniem (mock + kolektor + uvicorn + sms_sender): `./ctiptest` – tworzy sesję tmux `ctip-stack-test` z czterema oknami i blokuje uruchomienie, jeśli `.env.test` wskazuje na produkcyjną centralę lub `SMS_TEST_MODE` ≠ `true`.

## Instalacja jako usługa Windows
1. Przygotuj `D:\CTIP` (git clone), Python 3.11 x64, plik `.env`.
2. Uruchom PowerShell jako Administrator i skrypt `scripts/windows/install_service.ps1 -InstallDir "D:\CTIP" -PythonVersion "3.11"` – tworzy `.venv`, instaluje zależności, rejestruje i startuje usługę `CollectorService` (kolektor CTIP) z logami w `logs/collector`.
3. Zainstaluj NSSM (https://nssm.cc/download), a następnie uruchom `scripts/windows/install_web_sms_nssm.ps1 -InstallDir "D:\CTIP" -ServicePrefix "CTIP" -UvicornPort 8000 -NssmPath "C:\Program Files\nssm\nssm.exe"`. Skrypt tworzy i włącza dwie usługi: `CTIP-Web` (uvicorn `app.main:app`) oraz `CTIP-SMS` (`sms_sender.py`) z logami w `logs/web` i `logs/sms`, uruchamiane automatycznie po restarcie.
4. Sprawdzenie stanu: `Get-Service CollectorService,CTIP-Web,CTIP-SMS`; logi odpowiednio w `logs/collector`, `logs/web`, `logs/sms`. Panel jest dostępny pod `http://<host>:8000/admin`, endpoint `/health` pod tym samym portem.

Uwaga: komunikaty w skryptach PowerShell są zapisane w ASCII (bez polskich znaków), dzięki czemu Windows PowerShell 5.1 z domyślnym kodowaniem nie zgłasza błędów parsowania. Skrypty instalacyjne znajdują się w repozytorium w `scripts/windows` (także w pakiecie `docs/instal/ctip_windows_service_package.zip`) i domyślnie wymuszają `py -3.11`; na hostach z domyślnym Pythonem 3.13 uruchamiaj `install_service.ps1` z parametrem `-PythonVersion "3.11"`.

Aktualizacje kodu na Windows wykonuj przez `scripts/windows/update_ctip.ps1` (zatrzymuje uslugi, `git fetch/pull`, aktualizacja zaleznosci, `pre-commit run --all-files`, testy `python -m unittest discover -s tests`, a nastepnie restart uslug). Dla srodowisk z NSSM uzyj `-ServiceNames "CollectorService","CTIP-Web","CTIP-SMS"`.
Szybka aktualizacja bez testow i instalacji zaleznosci: `scripts/windows/update_ctip_easy.ps1` (wykonuje `git fetch/pull --ff-only` i restartuje tylko uruchomione uslugi, a gdy brak nowych commitow - nie restartuje nic). Opcjonalnie wymusisz restart parametrem `-ForceRestart`.

Szczegółowy przewodnik dla Windows Server 2022 (instalacja w `D:\CTIP`, skrypty PowerShell oraz pakiet `ctip_windows_service_package.zip`) znajduje się w `docs/instal/windows_server_2022.md`.

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
- `docs/LOG/Centralka` – dzienne logi kolektora i monitora CTIP (np. `log_collector_<YYYY-MM-DD>.log`, `log_con_sli_<YYYY-MM-DD>.log`); każdy wpis zawiera datę i godzinę.
- `docs/LOG/BAZAPostGre` – dzienne logi operacji na bazie PostgreSQL (np. `log_192.168.0.8_postgre_<YYYY-MM-DD>.log`).
- `docs/projekt` – przestrzeń na notatki projektowe, szkice i checklisty wdrożeniowe; kluczowe pliki: `panel_admin_architektura.md` (architektura backendu panelu), `panel_admin_ui.md` (plan interfejsu administratora) oraz `dziennik_2026-02-26.md` (podsumowanie wdrożeń wykonanych 26 lutego 2026).
- `docs/raport` – statyczny raport CPC (HTML + CSV) udostępniany bez logowania pod `http://127.0.0.1:8000/raport`; serwer FastAPI montuje katalog bez prawa zapisu, dzięki czemu pełni rolę tylko-do-odczytu.
- 📁 Archiwum sesji Codex: `docs/archiwum/sesja_codex_2025-10-11.md`
- `baza_CTIP` (katalog główny repozytorium) – dokument opisujący strukturę schematu `ctip`, procedurę połączenia oraz typowe operacje administracyjne.
- `prototype/index.html` – statyczny prototyp interfejsu użytkownika prezentujący widok listy połączeń CTIP, panel szczegółów, szybkie akcje SMS oraz historię wiadomości (dane przykładowe, brak połączenia z API).

## Testowanie i rozwój
Repozytorium zawiera testy jednostkowe handshake CTIP (`tests/test_handshake.py`), klienta monitorującego (`tests/test_conect_sli.py`), kolektora CTIP (`tests/test_collector_context.py`), warstwy API (`tests/test_api_auth.py`, `tests/test_sms_schema.py`) oraz świeży zestaw weryfikacji schematu bazy (`tests/test_db_schema.py`). `tests/test_admin_backend.py` obejmuje scenariusze panelu administracyjnego, w tym logi i historię SerwerSMS (`/admin/sms/logs`, `/admin/sms/history`). Uruchom je poleceniem `python -m unittest`. W przypadku rozszerzania logiki parsowania zdarzeń oraz wysyłki SMS rekomendowane jest dopisywanie kolejnych testów (zarówno dla parsowania strumienia, jak i integracji z API SMS). Każda modyfikacja kodu powinna być od razu odzwierciedlona w dokumentacji i w sekwencjach testowych.
  - Zadania planowane.
## Zadania planowane
Szczegółowy rejestr zadań znajduje się w pliku `docs/projekt/zadania_planowane.md`.
