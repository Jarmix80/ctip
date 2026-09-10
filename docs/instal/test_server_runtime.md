# Kanoniczny stos testowy na serwerze `192.168.0.9`

## Cel

Na serwerze testowym działa jeden kanoniczny projekt Compose o nazwie `ctip-test`. Główny panel CTIP jest publikowany wyłącznie na porcie `8000`; historyczny port `8002` nie jest używany. Środowisko korzysta tylko z bazy PostgreSQL `ctip_test`, lokalnej kopii Firebird i trybu przechwytywania komunikacji z klientami.

Kod usług aplikacyjnych pochodzi z jednego niezmiennego obrazu oznaczonego pełnym SHA commita. Kontenery nie montują katalogu `app` z aktywnego worktree, dlatego zmiana gałęzi w repozytorium nie zmienia działającego systemu.

Kanoniczny projekt używa podsieci `172.28.252.0/24` dla usług wewnętrznych i
`172.28.253.0/24` dla bramy. Historyczne podsieci `172.28.250.0/24` oraz
`172.28.251.0/24` pozostają zarezerwowane dla zachowanego stosu rollbacku, więc
oba zestawy sieci nie kolidują nawet wtedy, gdy stare kontenery są zatrzymane.
PostgreSQL ma w sieci wewnętrznej unikalny alias `ctip-test-postgres`. Nie wolno
zastępować go ogólną nazwą `postgres`, ponieważ usługa Bot Identity należy także
do sieci CHAT_KP, w której występuje inna baza o takim aliasie.

## Wyrównanie z produkcją — 10 września 2026

Gałąź `test/shipping-retry-local-validation-2026-09-03` zawiera scalenie wydania
`release/genform-search-all-2026-09-03` z poprawkami produkcyjnymi do commita
`1bc17c064e0f6a1fb9581d9ecf3b53bb56d3619f`. Obejmuje to serializację czasu
Firebird do JSON, pełne zamknięcia Shipping, uzgadnianie ręcznych zmian MS,
pełną treść etykiety z blokadą powyżej 81 znaków, model urządzenia oraz
poprawki GenForm/FLOW. Moduły Delivery, CRM, LAB i Bot Identity pozostają dostępne.

Obraz testowy ma własny SHA ze względu na rozszerzenia testowe; zgodność nie
oznacza identycznego obrazu ani przeniesienia produkcyjnej konfiguracji.
Schemat pozostaje na rewizji `f2b7c9d4e6a1`, bez migracji i resetowania danych.
`SHIPPING_MS_RECONCILE_ENABLED=false` jest wymuszane przez Compose i kontrolowane
przez preflight, aby automat nie zmieniał historycznych spraw testowych.
Scenariusze uzgadniania sprawdzają testy z odseparowanymi danymi i atrapami integracji.
Wszystkie usługi otrzymują `PBX_HOST=mock-ctip`, niezależnie od starszych wpisów `.env.test`.

Polecenia wydania należy wykonywać z katalogu
`/home/marcin/projects/ctip/.codex/shipping-retry-local-validation-test-worktree`.
Główny katalog repozytorium pozostaje archiwalny. Jego stary `ctiptest` nie jest
skryptem zarządzającym aktywnym stosem. Właściwy skrypt używa
`scripts/docker_compose.sh`, który potrafi uruchomić istniejącą wtyczkę Compose
bezpośrednio, gdy polecenie `docker compose` nie jest zarejestrowane.

Przed przełączeniem tego wydania należy dodatkowo wykonać logiczną kopię
Firebird przez `/usr/local/firebird/bin/gbak` w kontenerze `ctip-test-firebird-1`,
zapisując wynik poza Git i sprawdzając odtworzenie do osobnego pliku.
Zwykła kopia działającego pliku FDB wykonywana przez starszy skrypt wydania
nie zastępuje takiej weryfikacji. PostgreSQL wymaga kopii `pg_dump -Fc`.
Stan rollbacku oraz raport odbioru są przechowywane w ignorowanym
`runtime/deployments/`; nie wolno usuwać poprzedniego obrazu ani wolumenów.
Dzienne logi `docs/LOG` zachowują format `*_YYYY-MM-DD.log` i znaczniki czasu wpisów.

Walidacja przygotowanego scalenia: 236 testów Shipping, GenForm/FLOW, interfejsu
i izolacji zakończonych powodzeniem; pełny zestaw: 823 poprawne, 14 pominiętych
i 4 niepowodzenia. Dwa testy automatyzacji FLOW nie tworzą tabeli `delivery_case`
w swojej bazie SQLite, a dwa testy `/raport` oczekują braku plików mimo danych
tworzonych przez własny pomocniczy katalog testowy. Te same cztery problemy
potwierdzono na wcześniejszej bazie kodu `09b74f2`; nie zmieniano tych testów
ani niezwiązanej z wydaniem logiki. Rzeczywista baza `ctip_test` zawiera
`delivery_case` i `grenke_contract_end`. Kontrole pre-commit, Ruff, Black
oraz składni JavaScript zakończyły się powodzeniem.

### Odbiór wdrożenia

Przełączenie zakończono 10 września 2026 r. około godziny 14:15 CEST. Działa obraz
`ctip/test-runtime:902d0556f5580ad58833b6ef0088977e797faa69`, wypchnięty na istniejącą
gałąź testową. Późniejszy commit dokumentacyjny zapisuje odbiór, ale nie zmienia
wdrożonego obrazu. Przy kolejnych poleceniach operatorskich należy jawnie ustawić:

```bash
export CTIP_TEST_IMAGE=ctip/test-runtime:902d0556f5580ad58833b6ef0088977e797faa69
./ctiptest server-status
```

- Wszystkie 15 usług trwałych działa bez restartów; 9 usług aplikacyjnych używa
  wspólnego obrazu. Jednorazowy `log-init` zakończył się kodem `0`.
- CTIP, formularze publiczne, CRM i LAB odpowiadają na kontrolę zdrowia kodem `200`.
  Strony Shipping i GenForm są dostępne; chronione API bez sesji zwraca `401`.
  Udostępniane pliki JavaScript odpowiadają zawartości wydania.
- Odczytano 9 pozycji testowej kolejki Shipping oraz szczegóły 3 zleceń. Serializacja
  danych i wartości czasu do JSON działa poprawnie. Kolektor zapisuje świeże
  zdarzenia z atrapy centrali.
- Bot Identity potwierdził kontrakt `ctip-v1` i świeżą synchronizację Firebird
  tylko do odczytu. Logi aplikacji nie zawierały świeżych błędów startu.
- Po starcie ponownie potwierdzono testową bazę, blokady komunikacji i zapisów,
  wyłączone uzgadnianie MS oraz brak trasy domyślnej kontenera `web`.

Logiczne kopie baz znajdują się w `backups/test-alignment-20260910_135724/`.
Sumy SHA-256 są poprawne; kopię Firebird odtworzono do osobnego pliku i odczytano
81294 zlecenia, 174 modele oraz 7548 kartotek magazynowych. Dodatkowa kopia
skryptu przełączenia: `backups/test-cutover/20260910_141316/`.
Stan rollbacku: `runtime/deployments/test-cutover-20260910_141329/`;
zawiera poprzedni obraz `ce9328e3213067b5670a2bd102a6e218f8bacc4e` i jego nakładkę
Compose z sumą kontrolną. Raport odbioru bez danych klientów:
`runtime/deployments/raport-odbioru-2026-09-10.json`.

Nie wykonywano migracji, resetowania danych, rzeczywistych nadań przesyłek ani
wystawiania dokumentów RW/WZ/FV. Produkcja i MSConnector pozostały bez zmian.

## Dane trwałe

- PostgreSQL używa zachowanego wolumenu `ctip-prod-mirror_ctip_mirror_postgres_data`.
- Mailpit używa zachowanego wolumenu `ctip-prod-mirror_ctip_mirror_mailpit_data`.
- Firebird znajduje się w ignorowanym katalogu `.runtime-firebird/BAZAMS_TEST.FDB`.
- Konfiguracja i testowa baza zabezpieczeń Firebird znajdują się w stabilnym wolumenie `ctip-test-firebird-config`; został on skopiowany ze sprawdzonego starego stosu bez usuwania źródła.
- Logi aplikacyjne korzystają z wolumenu inicjalizowanego przez jednorazową usługę `log-init`, dzięki czemu procesy bez uprawnień administratora mogą tworzyć dzienne katalogi i pliki.
- Sekrety testowe znajdują się w ignorowanym katalogu `.runtime-secrets`; pliki muszą mieć tryb `0600`, a katalog `0700`.
- Stare kontenery, wolumeny i kopie baz nie są automatycznie usuwane.

W `.env.test` należy ustawić bezwzględne ścieżki `CTIP_TEST_FIREBIRD_DIR` i `CTIP_TEST_SECRET_DIR`, jeśli polecenia są wykonywane z dodatkowego worktree.

Plik `.env.test` musi także zawierać odrębne testowe wartości
`BOT_IDENTITY_SECRET_KEY`, `BOT_IDENTITY_CHAT_TOKEN` i
`BOT_IDENTITY_VOICE_TOKEN`. Kod `BOT_IDENTITY_TEST_SMS_CODE=123456` jest
dozwolony wyłącznie przy `CRM_LAB_MODE=true`. Moduł Shipping działa testowo z
`DPD_MODE=mock`, `DPD_INFO_ENABLED=false`,
`SHIPPING_DPD_FIREBIRD_MILESTONES_ENABLED=false`, `SHIPPING_GEOCODER_ENABLED=true`,
`SHIPPING_COMPATIBILITY_WEB_ENABLED=false` oraz
`SHIPPING_TEST_FIREBIRD_WRITES=false`. Taki zestaw udostępnia pełny interfejs
Shipping, nie nadaje rzeczywistej przesyłki i nie zapisuje do Firebirda. Ręczny
geokoder może łączyć się wyłącznie z `api.adresy.app` przez dedykowaną bramę TLS
`addresy-egress`; kontener `web` nadal nie ma trasy domyślnej.

## Procedura wydania testowego

```bash
source /home/marcin/projects/ctip/.venv/bin/activate
export CTIP_VENV_DIR=/home/marcin/projects/ctip/.venv
export ENV_FILE=/home/marcin/projects/ctip/.env.test

./ctiptest server-build
./ctiptest server-check
./ctiptest server-migrate
./ctiptest server-cutover
./ctiptest server-status
```

`server-build` buduje obraz `ctip/test-runtime:<pełny_sha>`. `server-check` sprawdza etykietę obrazu, izolację, montowania, porty, bezpieczne flagi Shipping i kompletność konfiguracji Bot Identity. `server-migrate` najpierw wykonuje backup PostgreSQL i Firebird, a następnie aktualizuje wyłącznie `ctip_test`. `server-cutover` ponownie wykonuje backup, zatrzymuje stare testowe kontenery bez ich usuwania i uruchamia projekt `ctip-test`. Odbiór wymaga nie tylko odpowiedzi HTTP, ale też stabilnego przez co najmniej 10 sekund stanu wszystkich procesów trwałych, w tym Firebird, kolektora oraz sendera SMS. Dodatkowo sprawdza uwierzytelniony kontrakt `ctip-v1`, świeżo zakończoną synchronizację Bot Identity i brak nowych błędów w logach bieżącego startu.

Jeżeli historyczna baza ma znacznik `e4a8c1d9f2b7`, ale fizycznie zawiera już
również tabele i kolumny gałęzi dostaw, Bot Identity oraz CRM, zwykła migracja
zatrzyma się na próbie ponownego utworzenia istniejących obiektów. W takim
jednorazowym przypadku zamiast `server-migrate` należy wykonać:

```bash
./ctiptest server-reconcile
```

Polecenie tworzy i sprawdza sumy backupu PostgreSQL oraz Firebird, porównuje
wszystkie tabele i kolumny z 59 modelami ORM, sprawdza krytyczne ograniczenia i
dane CRM/Bot Identity, a następnie zmienia wyłącznie wpis `alembic_version`.
Nie wykonuje DDL ani migracji danych biznesowych. Każda niezgodność przerywa
operację bez zapisu; po uzgodnieniu `server-check` musi zakończyć się sukcesem.

Preflight jest uruchamiany w wyczyszczonym środowisku procesu. Zmienne sesji administratora, narzędzi CI lub Codex nie mogą nadpisać `.env.test` i przypadkowo aktywować produkcyjnych integracji.

## Rollback

```bash
./ctiptest server-rollback
```

Przy pierwszym przełączeniu polecenie uruchamia zachowane kontenery poprzednich stosów. Przy kolejnym wydaniu odtwarza poprzedni niezmienny obraz projektu `ctip-test` razem z plikiem `compose.test.server.yml` pobranym z commita tego obrazu. Kopia konfiguracji otrzymuje sumę SHA-256, a brak commita lub poprawnej sumy zatrzymuje cutover przed wyłączeniem działającego stosu. Po rollbacku automat wymaga odpowiedzi `/health` i stabilności wszystkich procesów trwałych. Katalog stanu i ścieżka backupu znajdują się w `runtime/deployments/`.

Rollback kodu nie cofa automatycznie migracji. Migracje testowe muszą być wstecznie zgodne; przy zmianie destrukcyjnej bazę należy odtworzyć ręcznie z backupu po osobnej decyzji administratora.

## Kontrola odbiorcza

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://192.168.0.9:8000/shipping
./ctiptest server-status
./ctiptest server-logs
```

Odbiór obejmuje również stabilność usług `collector`, `sms-sender`, `forms-public`, Bot Identity, CRM i LAB, potwierdzenie `SMS_TEST_MODE=true`, `BLOCK_CLIENT_COMMUNICATIONS=true`, `FB_ALLOW_WRITES=false`, trybu DPD `mock`, świeżej synchronizacji katalogu oraz brak publikacji portu `8002`. Kontrola nie wypisuje wartości tokenów ani klucza szyfrującego.
