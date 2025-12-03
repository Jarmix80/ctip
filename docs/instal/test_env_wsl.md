# Środowisko testowe CTIP w WSL

Celem środowiska testowego jest równoległe uruchomienie wszystkich kluczowych komponentów (kolektor `collector_full.py`, panel FastAPI + uvicorn oraz `sms_sender.py`) bez ingerencji w produkcyjny kolektor działający jako usługa Windows. Zamiast łączyć się z prawdziwą centralą Slican, środowisko WSL wykorzystuje lokalny mock CTIP i oddzielną bazę PostgreSQL.

## Założenia
- Produkcja: Windows Server 2022 (`D:\CTIP`, usługa `CollectorService`).
- Testy: WSL2 (Ubuntu) z repozytorium w `~/projects/ctip`.
- Połączenia CTIP nie są współdzielone: mock udostępnia dane tylko do testów.
- Baza danych testowa `ctip_test` działa lokalnie (np. kontener Docker lub lokalny serwer PostgreSQL na WSL).

## Szybki runbook – pełne uruchomienie wersji testowej
1. Przygotuj zależności w WSL:
   ```bash
   cd ~/projects/ctip
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Skopiuj i uzupełnij `.env.test` (oddzielne od produkcyjnego `.env`):
   ```bash
   cp .env.test.example .env.test
   # ustaw PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD dla lokalnej bazy
   # pozostaw PBX_HOST=127.0.0.1 i PBX_PORT=5525 (mock CTIP)
   # wymagane: SMS_TEST_MODE=true, ADMIN_PANEL_URL=http://localhost:18000/admin
   ```
3. Załaduj zmienne środowiskowe do bieżącej powłoki:
   ```bash
   set -a && source .env.test && set +a
   ```
4. Zastosuj migracje na bazie testowej:
   ```bash
   alembic upgrade head
   ```
5. Uruchom mock CTIP na porcie 5525 (oddzielne okno/zakładka):
   ```bash
   source .venv/bin/activate
   set -a && source .env.test && set +a
   python scripts/mock/mock_ctip_server.py --port 5525 --loop --log-level INFO
   ```
6. W drugim oknie (z aktywnym `.venv` i `.env.test`) wystartuj stos testowy w tmux:
   ```bash
   ./run_test_stack_tmux.sh
   ```
   Skrypt przerwie start, jeśli `PBX_HOST=192.168.0.11` (produkcja) albo `SMS_TEST_MODE` nie jest `true`.
7. Podgląd i weryfikacja:
   ```bash
   tmux attach -t ctip-stack-test
   ```
   - okno `collector`: logi `[CTIP]` z mocka,
   - okno `uvicorn`: panel na `http://localhost:18000/admin`,
   - okno `sms-sender`: wpisy `SMS_TEST_MODE`.
8. Zatrzymanie środowiska:
   - w oknie mocka `Ctrl+C`,
   - w tmux `Ctrl+b : kill-session -t ctip-stack-test`.

### Uruchomienie jednym poleceniem
Po wykonaniu powyższych kroków przygotowawczych możesz startować pełne środowisko testowe (mock CTIP + kolektor + uvicorn + sms_sender) poleceniem:
```bash
./ctiptest
```
Skrypt tworzy sesję tmux `ctip-stack-test` z czterema oknami (`mock-ctip`, `collector`, `uvicorn`, `sms-sender`) i wymusza zabezpieczenia: `.env.test` musi mieć `PBX_HOST` różny od produkcyjnego `192.168.0.11` oraz `SMS_TEST_MODE=true`.

## Krok 1 – środowisko Python i zależności
```bash
cd ~/projects/ctip
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Krok 2 – konfiguracja `.env.test`
1. Skopiuj wzorzec: `cp .env.test.example .env.test`.
2. Ustaw dane dostępowe do lokalnej bazy (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`).
3. Zostaw `PBX_HOST=127.0.0.1` i `PBX_PORT=5525` – to port mocka CTIP.
4. Upewnij się, że `SMS_TEST_MODE=true`, a `SMS_API_URL` wskazuje na endpoint testowy (np. `https://httpbin.org/post`).

## Krok 3 – baza danych testowa
```bash
# przykład lokalny
sudo -u postgres psql -c "CREATE DATABASE ctip_test;"
sudo -u postgres psql -c "CREATE USER ctip_test WITH PASSWORD 'ctip_test';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ctip_test TO ctip_test;"
```
Następnie zastosuj migracje w kontekście `.env.test`:
```bash
source .venv/bin/activate
set -a && source .env.test && set +a
alembic upgrade head
```

## Krok 4 – uruchomienie mocka CTIP
Mock znajduje się w `scripts/mock/mock_ctip_server.py`.
```bash
source .venv/bin/activate
python scripts/mock/mock_ctip_server.py --port 5525 --loop --log-level INFO
```
Opcjonalnie możesz przygotować własny scenariusz zdarzeń (plik tekstowy, format `sekundy;RAMKA`) i podać go parametrem `--scenario-file`.

## Krok 5 – start stosu testowego
Skrypt `run_test_stack_tmux.sh` uruchamia trzy procesy w sesji tmux zasilanej konfiguracją `.env.test`.
```bash
./run_test_stack_tmux.sh
# dołączenie do sesji
 tmux attach -t ctip-stack-test
```
Skrypt zatrzyma się, jeśli `PBX_HOST` wskazuje na adres produkcyjnej centrali lub `SMS_TEST_MODE!=true`, co chroni przed przypadkowym podłączeniem do środowiska produkcyjnego.

## Krok 6 – weryfikacja
- Kolektor: `tmux select-window -t ctip-stack-test:collector` i obserwuj linie `[CTIP]` z mocka.
- Panel: przeglądarka → `http://localhost:18000/admin` (domyślny port w skrypcie testowym).
- SMS: log `docs/LOG/sms/sms_sender_<DATE>.log` powinien zawierać wpisy `SMS_TEST_MODE`.
- Baza: sprawdź `ctip.call_events` w `ctip_test`.

## Krok 7 – zatrzymanie
- Zamknij mock (`Ctrl+C`).
- W tmux: `Ctrl+b` → `:` → `kill-session -t ctip-stack-test`.

## Dobre praktyki
- Nigdy nie edytuj `.env` produkcyjnego; trzymaj `.env.test` wyłącznie dla testów.
- Jeśli musisz czasowo podłączyć WSL do prawdziwej centrali (np. diagnostyka), zatrzymaj usługę `CollectorService` na Windowsie, wykonaj test i natychmiast uruchom usługę ponownie.
- Testowe konto `ctip_test` w bazie ogranicz do lokalnego hosta (`pg_hba.conf`: `host ctip_test ctip_test 127.0.0.1/32 md5`).
