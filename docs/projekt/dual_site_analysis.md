# Analiza ryzyk przy równoległej pracy Windows (produkcja) i WSL (test)

## Zakres systemu
- **Produkcja (Windows Server 2022)** – `CollectorService` + `collector_full.py`, `sms_sender.py` (opcjonalnie jako usługa lub zadanie) i panel FastAPI (jeśli hostowany). Baza PostgreSQL działa lokalnie (`192.168.0.8:5433`).
- **Testy (WSL/Ubuntu)** – pełny zestaw usług uruchamiany incydentalnie, zasilany mockiem CTIP i osobną bazą `ctip_test`.

## Ryzyka i scenariusze awarii
| Nr | Obszar | Potencjalny problem | Symptomy | Zalecana reakcja |
|----|--------|---------------------|----------|------------------|
| 1 | CTIP TCP | Dwa kolektory próbują równocześnie połączyć się z tą samą centralą Slican. | Produkcyjny kolektor otrzymuje `ANAK LOGA`, WSL rejestruje `CTIPLoginError`, w logach centrali widoczne „zajęte połączenie”. | Nigdy nie ustawiaj `PBX_HOST=192.168.0.11` w `.env.test`; jeśli diagnostyka wymaga realnej centrali, zatrzymaj `CollectorService` przed testem i uruchom go ponownie zaraz po nim. |
| 2 | Baza danych | Testowy kolektor zapisuje do produkcyjnej bazy `ctip` albo odwrotnie. | Duplikaty rekordów, konflikty `call_id`, błędne raporty w panelu, w logach `psycopg.UniqueViolation`. | Używaj oddzielnych baz (`ctip` vs `ctip_test`) i użytkowników; w `.env.test` wymuś `PGDATABASE=ctip_test`. |
| 3 | SMS | Testowy `sms_sender.py` wysyła prawdziwe SMS-y. | Niechciane wiadomości do klientów, koszty operatora, alerty SerwerSMS. | `SMS_TEST_MODE=true`, alternatywnie podstaw `SMS_API_URL=https://httpbin.org/post`. Skrypt `run_test_stack_tmux.sh` nie uruchomi się, jeśli wartość jest inna. |
| 4 | E-mail | Testowy panel wysyła powiadomienia do realnych adresów. | Próbne konta/powiadomienia trafiają do użytkowników. | `EMAIL_HOST=localhost`, `EMAIL_USE_TLS=false`, używaj lokalnego serwera debugowego (np. `python -m smtpd`). |
| 5 | Konfiguracja | Pomylenie `.env` i `.env.test`, brak synchronizacji sekretów. | Producent i test dzielą te same klucze, co utrudnia audyt. | Plik produkcyjny przechowuj tylko na Windowsie; w repo trzymaj `.env.test` (bez sekretów). |
| 6 | Migracje | `alembic upgrade` odpalony na złym środowisku (np. testowy schemat na produkcji). | `SchemaValidationError` produkcyjnego kolektora, brak danych w panelu. | Przed migracją eksportuj `PGDATABASE`/`PGHOST`, loguj w notatniku, wykonuj `select current_database()` w psql. |
| 7 | Porty aplikacji | Testowy uvicorn używa portu 8000 i koliduje z `run_stack_tmux.sh`. | „Address already in use” na WSL. | `run_test_stack_tmux.sh` korzysta z portu 18000; unikaj równoległego uruchamiania dwóch skryptów na jednym porcie. |
| 8 | Logi | Logi z WSL trafiają do `docs/LOG` i są mylone z produkcyjnymi. | Analiza incydentu obejmuje błędne wpisy. | Dla testów ustaw `LOG_PREFIX=[CTIP-TEST]`, rotuj logi WSL poza repo (np. `~/logs/ctip`). |
| 9 | Obciążenie | Jednoczesne działanie testowego i produkcyjnego stacka obciąża bazę lub sieć. | Wzrost czasu odpowiedzi, `psycopg` wyrzuca `timeout`. | W testach używaj lokalnej bazy; jeśli musisz łączyć się z produkcyjną bazą, ogranicz liczbę równoległych wątków i testuj poza godzinami szczytu. |
| 10 | Drift wersji | Kod i zależności w `.venv` Windowsa i WSL rozjeżdżają się. | Błędy „works on my machine”, inne wyniki testów. | Regularnie aktualizuj `.venv` po `git pull` (`pip install -r requirements.txt`), prowadź changelog w `docs/instal/windows_server_2022.md`. |

## Procedury ochronne
1. **Oddzielne pliki środowiskowe** – `.env` (produkcyjny, nie w repo) oraz `.env.test` (lokalny, jawnie testowy).
2. **Mock CTIP** – `scripts/mock/mock_ctip_server.py` zapewnia dane do testów i eliminuje konflikt TCP.
3. **Runbook WSL** – `run_test_stack_tmux.sh` wymusza `SMS_TEST_MODE=true` i blokuje połączenie do adresu produkcyjnego.
4. **Nadzór usług** – na Windows monitoruj `CollectorService` (`Get-Service`, Event Viewer), w WSL monitoruj sesję tmux i logi w `logs/collector`.
5. **Zarządzanie zmianą** – każdą modyfikację schematu/testów dokumentuj w `docs/instal/test_env_wsl.md` oraz README (sekcja instalacji/testów).

## Reakcja na incydent
1. Jeśli produkcyjny kolektor zgłasza `LOGA odrzucone`, natychmiast sprawdź, czy w WSL nie działa proces `collector_full.py` z `PBX_HOST=192.168.0.11`. Zatrzymaj testy i uruchom ponownie usługę Windows (`Start-Service CollectorService`).
2. W przypadku duplikacji rekordów w bazie sprawdź `ctip.call_events` i identyfikator `source` (powinny być różne dla testu, np. ustaw `LOG_PREFIX=[CTIP-TEST]`).
3. Jeśli SMS-y wyszły z testów, sprawdź logi `docs/LOG/sms/` na WSL; ustaw `SMS_TEST_MODE=true` i ponownie uruchom `sms_sender.py` w trybie testowym.

## Rekomendacje dodatkowe
- Automatycznie synchronizuj `.env.test` ze wzorcem `.env.test.example`, aby nowi członkowie zespołu nie kopiowali ustawień produkcyjnych.
- Gdy to możliwe, uruchamiaj testy integracyjne na dumpie bazy (odtworzonym lokalnie) zamiast na produkcyjnej instancji.
- Logi WSL przechowuj poza repo (np. `~/logs/ctip_test`) i czyść je po każdej sesji.
