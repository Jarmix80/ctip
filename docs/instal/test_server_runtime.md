# Kanoniczny stos testowy na serwerze `192.168.0.9`

## Cel

Na serwerze testowym działa jeden kanoniczny projekt Compose o nazwie `ctip-test`. Główny panel CTIP jest publikowany wyłącznie na porcie `8000`; historyczny port `8002` nie jest używany. Środowisko korzysta tylko z bazy PostgreSQL `ctip_test`, lokalnej kopii Firebird i trybu przechwytywania komunikacji z klientami.

Kod usług aplikacyjnych pochodzi z jednego niezmiennego obrazu oznaczonego pełnym SHA commita. Kontenery nie montują katalogu `app` z aktywnego worktree, dlatego zmiana gałęzi w repozytorium nie zmienia działającego systemu.

Kanoniczny projekt używa podsieci `172.28.252.0/24` dla usług wewnętrznych i
`172.28.253.0/24` dla bramy. Historyczne podsieci `172.28.250.0/24` oraz
`172.28.251.0/24` pozostają zarezerwowane dla zachowanego stosu rollbacku, więc
oba zestawy sieci nie kolidują nawet wtedy, gdy stare kontenery są zatrzymane.

## Dane trwałe

- PostgreSQL używa zachowanego wolumenu `ctip-prod-mirror_ctip_mirror_postgres_data`.
- Mailpit używa zachowanego wolumenu `ctip-prod-mirror_ctip_mirror_mailpit_data`.
- Firebird znajduje się w ignorowanym katalogu `.runtime-firebird/BAZAMS_TEST.FDB`.
- Sekrety testowe znajdują się w ignorowanym katalogu `.runtime-secrets`; pliki muszą mieć tryb `0600`, a katalog `0700`.
- Stare kontenery, wolumeny i kopie baz nie są automatycznie usuwane.

W `.env.test` należy ustawić bezwzględne ścieżki `CTIP_TEST_FIREBIRD_DIR` i `CTIP_TEST_SECRET_DIR`, jeśli polecenia są wykonywane z dodatkowego worktree.

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

`server-build` buduje obraz `ctip/test-runtime:<pełny_sha>`. `server-check` sprawdza etykietę obrazu, izolację, montowania, porty i zgodność migracji. `server-migrate` najpierw wykonuje backup PostgreSQL i Firebird, a następnie aktualizuje wyłącznie `ctip_test`. `server-cutover` ponownie wykonuje backup, zatrzymuje stare testowe kontenery bez ich usuwania i uruchamia projekt `ctip-test`.

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

Przy pierwszym przełączeniu polecenie uruchamia zachowane kontenery poprzednich stosów. Przy kolejnym wydaniu odtwarza poprzedni niezmienny obraz projektu `ctip-test`. Katalog stanu i ścieżka backupu znajdują się w `runtime/deployments/`.

Rollback kodu nie cofa automatycznie migracji. Migracje testowe muszą być wstecznie zgodne; przy zmianie destrukcyjnej bazę należy odtworzyć ręcznie z backupu po osobnej decyzji administratora.

## Kontrola odbiorcza

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://192.168.0.9:8000/shipping
./ctiptest server-status
./ctiptest server-logs
```

Odbiór obejmuje również stabilność usług `collector`, `sms-sender`, `forms-public`, Bot Identity, CRM i LAB, potwierdzenie `SMS_TEST_MODE=true`, `BLOCK_CLIENT_COMMUNICATIONS=true`, `FB_ALLOW_WRITES=false` oraz brak publikacji portu `8002`.
