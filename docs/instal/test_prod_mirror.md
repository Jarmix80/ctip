# Izolowane środowisko testowe zgodne z produkcją

## Cel

Środowisko służy do testowania kodu i danych zbliżonych do produkcji bez dostępu do produkcyjnych usług. Kod uruchamiany lokalnie pochodzi z osobnego worktree opartego na wersji produkcyjnej. Dane PostgreSQL i Firebird mogą być okresowo odświeżane z autoryzowanych kopii, ale runtime korzysta wyłącznie z lokalnych zasobów.

Zabronione jest uruchamianie stosu testowego z `.env` albo wskazywanie w `.env.test` hostów `192.168.0.8` i `192.168.0.11`.

## Architektura

Stos `compose.test.yml` uruchamia:

| Moduł | Rola | Dostęp z hosta |
|---|---|---|
| `postgres` | lokalna baza `ctip_test`, PostgreSQL 17 | `127.0.0.1:5432` przez bramę |
| `firebird` | robocza kopia Menadżera Serwisu, Firebird 2.5 | `127.0.0.1:3050` lub `192.168.0.9:3050` przez bramę |
| `mailpit` | lokalne przechwytywanie e-mail | `127.0.0.1:8025` przez bramę |
| `mock-ctip` | symulator centrali CTIP | tylko sieć wewnętrzna |
| `web` | pełny panel CTIP | `0.0.0.0:8000` przez bramę |
| `forms-public` | aplikacja formularzy publicznych | `127.0.0.1:8100` przez bramę |
| `collector` | kolektor podłączony wyłącznie do mocka | tylko sieć wewnętrzna |
| `sms-sender` | obsługa kolejki w trybie symulacji | tylko sieć wewnętrzna |
| `test-gateway` | statyczna brama HAProxy do wskazanych portów | sieć wewnętrzna i brzegowa |
| `google-egress` | brama TLS ograniczona do API Google używanego przez arkusz testowy | sieć wewnętrzna i brzegowa |
| `addresy-egress` | brama TLS ograniczona do `api.adresy.app` dla ręcznego geokodera | sieć wewnętrzna i brzegowa |

Kontenery aplikacyjne są podłączone wyłącznie do sieci `ctip_test_internal` (`172.28.252.0/24`) oznaczonej jako `internal`. Nie mają trasy domyślnej, dlatego nie mogą połączyć się z Internetem, produkcyjnym PostgreSQL, Firebird ani centralą Slican. Brama `test-gateway` nie uruchamia kodu CTIP i przekazuje tylko jawnie skonfigurowane porty. Brama `google-egress` przyjmuje wyłącznie ruch TLS z nazwą SNI `oauth2.googleapis.com`, `sheets.googleapis.com` albo `www.googleapis.com`, a brama `addresy-egress` wyłącznie ruch TLS do `api.adresy.app`; pozostałe cele są odrzucane.

Testowy Firebird wczytuje aliasy z `ops/firebird/aliases.test.conf`. Menadżer Serwisu uruchomiony na tym samym hoście należy kierować na pełną ścieżkę `127.0.0.1:BAZAMS_TEST`, a z innego komputera w sieci firmowej na `192.168.0.9:BAZAMS_TEST`. Jeżeli formularz posiada osobne pola, port wynosi `3050`, a baza `BAZAMS_TEST`. Alias obsługuje również warianty `BAZAMS_TEST\BazaMS.fdb` i `BAZAMS_TEST/BazaMS.fdb`, które klient MS może zbudować automatycznie. Brama HAProxy odrzuca połączenia spoza hosta lokalnego oraz podsieci `192.168.0.0/24`.

## Blokady komunikacji

Konfiguracja `.env.test` wymusza:

- `CTIP_RUNTIME_PROFILE=test`;
- `OUTBOUND_DELIVERY_MODE=capture`;
- `SMS_TEST_MODE=true` oraz puste dane operatora SMS;
- SMTP wyłącznie do hosta `mailpit` bez TLS i uwierzytelnienia;
- Firebird w trybie `network`, ale wyłącznie do kontenera o nazwie `firebird`;
- wyłączone harmonogramy skrzynki, Google Sheets, kopii, Office 365 i powiadomień o dowozach;
- puste dane OpenAI, Office 365, Optimy i produkcyjnych skrzynek;
- domeny GRENKE z końcówką `.invalid`;
- lokalny mock CTIP zamiast `192.168.0.11`.

Każda próba e-mail lub SMS jest zapisywana z pełną treścią i adresatami do dziennego pliku `logs/outbound_test/outbound_test_YYYY-MM-DD.log`. Pliki mają tryb `0600`, katalog `0700`, a retencja wynosi 14 dni. E-mail jest dodatkowo widoczny w Mailpit. SMS otrzymuje status `SIMULATED` i nie uruchamia klienta HTTP.

## Przygotowanie

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.test.example .env.test
```

W ignorowanym pliku `.env.test` należy ustawić:

- losowy klucz Fernet `ADMIN_SECRET_KEY`;
- losowe hasło `TEST_ADMIN_PASSWORD` o długości co najmniej 12 znaków;
- `LOCAL_UID` i `LOCAL_GID` zgodne z wynikiem `id -u` oraz `id -g`.

Nie wolno kopiować produkcyjnych danych dostępowych do `.env.test`.

## Obsługa stosu

```bash
./ctiptest start
./ctiptest status
./ctiptest logs
./ctiptest stop
```

Panel jest dostępny pod `http://127.0.0.1:8000/` oraz pod adresem LAN hosta na porcie `8000`. Konto testowe ma adres `admin-test@example.com`; hasło znajduje się wyłącznie w ignorowanym `.env.test`.

Przed startem `scripts/test_env_preflight.py` sprawdza konfigurację. Ten sam skrypt jest wykonywany wewnątrz panelu, formularzy, kolektora i sendera SMS z opcją `--check-network`. Brak izolacji sieciowej albo wykrycie ustawień produkcyjnych przerywa start modułu.

Polecenie `./ctiptest start` wymusza ponowne utworzenie kontenerów, aby każda zmiana w `.env.test` została zastosowana również przez usługi działające wcześniej.

## Odświeżenie PostgreSQL

Odświeżenie wymaga jawnej zgody na odczyt produkcji. Eksport należy wykonać na serwerze produkcyjnym przez `pg_dump` w formacie niestandardowym, pobrać do ignorowanego katalogu `inbox/test_seed/` i zweryfikować sumę SHA-256. Hasła nie mogą występować w argumentach procesu ani logach.

Po odtworzeniu dumpu w pustej bazie `ctip_test` trzeba wykonać neutralizację:

```bash
source .venv/bin/activate
set -a
source .env.test
set +a
export CTIP_ENV_FILE="$PWD/.env.test"
export PGHOST=127.0.0.1
python scripts/reset_test_security.py --source-env /bezpieczna/sciezka/do/produkcyjnego.env
```

Skrypt działa wyłącznie dla profilu `test` i bazy `ctip_test`. W jednej transakcji:

- przekłada zaszyfrowane payloady formularzy z produkcyjnego `ADMIN_SECRET_KEY` na testowy klucz;
- usuwa wszystkie sesje administratorów;
- usuwa sekrety oraz produkcyjne nadpisania integracji z `admin_setting`;
- unieważnia aktywne tokeny formularzy;
- neutralizuje oczekujące SMS;
- wyłącza produkcyjnych użytkowników i ustawia im losowe hasła;
- tworzy lub aktualizuje konto `admin-test@example.com`.

Wyjątkiem od całkowitego braku komunikacji zewnętrznej jest worker outboxu urządzeń w usłudze `web`. Może on zapisywać wyłącznie do skoroszytu o ID zgodnym z `GOOGLE_SHEETS_TEST_SPREADSHEET_ID`, tytule `Zerowki_test` i zakładce `Urzadzenia_magazyn`. Plik konta usługi należy umieścić poza Git jako `runtime/secrets/google-service-account.json`; jest montowany tylko do usługi `web`. Preflight blokuje start przy innym ID, tytule, zakładce albo braku pliku poświadczeń.

Plik wskazany przez `--source-env` jest używany tylko przez jednorazowy proces neutralizacji. Nie jest montowany do kontenerów ani kopiowany do worktree.

## Odświeżenie Firebird

Źródłowy plik, przykładowo `inbox/baza_07/BAZAMS.FDB`, należy traktować jako tylko do odczytu. Procedura odświeżenia:

1. Zweryfikuj sumę SHA-256 źródła.
2. Skopiuj plik do `runtime/firebird/BAZAMS_TEST.FDB`.
3. Uruchom Firebird 2.5 i wykonaj logiczny backup `gbak -backup_database -garbage_collect`.
4. Usuń wyłącznie kopię roboczą i odtwórz ją przez `gbak -create_database`.
5. Przy zatrzymanym Firebird utwórz `runtime/firebird/BAZAMS_TEST_BASE.FDB`.
6. Ponownie sprawdź sumę źródła, aby potwierdzić brak jego modyfikacji.

Reset zmian wykonanych podczas testów:

```bash
./ctiptest reset-firebird
```

Polecenie zatrzymuje panel i Firebird, odtwarza plik roboczy ze snapshotu bazowego i ponownie uruchamia oba moduły.

## Test odbiorczy

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8100/health
curl --fail http://127.0.0.1:8025/api/v1/messages
PGPASSWORD=ctip_test psql -h 127.0.0.1 -p 5432 -U ctip_test -d ctip_test -c 'select 1'
docker compose --env-file .env.test -f compose.test.yml exec -T web \
  python scripts/test_env_preflight.py --check-network
```

Odbiór obejmuje także próbę e-mail z parametrami zewnętrznego SMTP. Wiadomość musi pojawić się wyłącznie w Mailpit. Próba SMS musi zwrócić `SIMULATED` i pojawić się w dziennym audycie.

## Archiwum i rollback

Przed pełnym odświeżeniem należy zachować:

- dump dotychczasowej bazy `ctip_test`;
- dotychczasowy plik Firebird;
- `git bundle`, patch zmian roboczych i listę plików niewersjonowanych;
- manifest z sumami SHA-256.

Rollback do wskazanego archiwum uruchamia:

```bash
./ctiptest rollback /sciezka/do/katalogu_archiwum
```

Skrypt weryfikuje sumy archiwum, zatrzymuje nowy stos, uruchamia zachowany kontener PostgreSQL i odtwarza poprzedni lokalny plik Firebird. Nie usuwa wolumenów nowego stosu.
