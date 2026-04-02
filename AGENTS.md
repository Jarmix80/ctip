# CTIP – zasady pracy dla Codex

## Język i styl
1. Wszystkie komentarze, README i dokumentacja muszą być tworzone wyłącznie po polsku.
2. Obowiązuje styl techniczny, zgodny z dokumentacją administratora systemu.

## Uprawnienia i środowisko
3. Codex ma prawo edytować pliki w tym repozytorium bez konieczności uzyskania dodatkowej zgody.
4. Codex pracuje **wyłącznie w obrębie tego repozytorium** (nie wykonuje zmian poza nim).
5. Zawsze aktywuj i używaj środowiska `.venv`; jeżeli nie istnieje, utwórz je (`python3 -m venv .venv`).
6. Przed uruchomieniem poleceń `python`/`pip` automatycznie aktywuj `.venv`.
7. Automatycznie wczytuj zmienne z właściwego pliku środowiskowego (`.env.test` dla pracy lokalnej i startów testowych, `.env` wyłącznie dla jawnego scenariusza produkcyjnego). **Nigdy** nie zapisuj sekretów do repo; trzymaj je w plikach środowiskowych poza Git.
8. Dopuszczalne polecenia: `python`, `pip`, `pre-commit`, `ruff`, `black`, `pytest`, `git`, `nc`, `telnet`, `psql`, polecenia powłoki niezbędne do pracy nad projektem.
9. Pamięć sesji: zapisuj/odtwarzaj kontekst w `.codex/session.json` (jeśli dostępne); po starcie wczytaj kontekst z plików projektu.

## Dokumentacja i utrzymanie
10. Po każdej zmianie w projekcie należy zadbać o aktualność pliku `README.md`.
11. Dokumentacja w `README.md` musi uwzględniać aktualną zawartość katalogu `docs/`.
12. Struktura bazy danych jest przechowywana w `docs/baza/schema_ctip.sql` i musi być brana pod uwagę przy aktualizacjach dokumentacji.
13. Jeżeli zmieni się `schema_ctip.sql`, wygeneruj krótkie podsumowanie zmian w `docs/baza/changes.md`.
14. Logi w `docs/LOG` są rotowane dziennie (`*_YYYY-MM-DD.log`) i każdy wpis zawiera znacznik czasu – nowa dokumentacja musi odzwierciedlać ten format.
15. W przypadku modyfikacji kodu należy jednocześnie aktualizować docstringi oraz testy jednostkowe.

## Połączenia z centralą CTIP (LAN)
16. Produkcyjna centrala Slican używana przez projekt identyfikuje się jako `CP-000 NO03914 v1.23.0140/15` (`PBX_HOST=192.168.0.11`); przed jawnym startem produkcyjnym potwierdź adres źródłowy hosta kolektora (WSL, np. `172.x.x.x`) i dostęp do sieci lokalnej.
17. Sekwencja inicjalizacji CTIP: `aWHO` → `aLOGA <PIN>` musi być wykonywana dokładnie raz na połączenie TCP.
18. Domyślny PIN CTIP to `1234`; zmiany wymagają synchronizacji z centralą i aktualizacji `.env`.
19. Do testów połączenia można używać `nc`/`telnet` w trybie RAW, np. `timeout 15s nc -v $PBX_HOST $PBX_PORT`.
20. Codex może analizować logi połączeń z centralą i raportować błędy komunikacji CTIP (nie przerywaj pracy przy sporadycznych błędach – loguj i kontynuuj).

## Uruchamianie i operacje
21. Definicja „testowego” w tym repo: wszystkie lokalne bazy i usługi uruchamiane z WSL/Linux (`ctip_test`, lokalny Firebird, mock CTIP, `SMS_TEST_MODE=true`). Zasoby zdalne `192.168.0.8` (PostgreSQL/Firebird) oraz `192.168.0.11` (PBX) traktuj jako produkcyjne.
22. Domyślnie każde polecenie typu „uruchom cały system” realizuj wyłącznie w środowisku testowym (`.env.test`, `./ctiptest`, `./run_test_stack_tmux.sh`).
23. Start produkcyjny (`.env`, `./run_stack_tmux.sh`, `./run_server_with_firebird.sh`) wolno wykonywać tylko po wyraźnym komunikacie użytkownika, że chodzi o produkcję; bez takiego komunikatu nie wolno łączyć się z `192.168.0.8` ani `192.168.0.11`.
24. Lokalna praca ma zawsze trafiać do jednej bazy PostgreSQL `ctip_test`; nie mieszaj lokalnego `ctip` z `ctip_test`.
25. Bramka SMS w środowisku lokalnym musi pozostać w trybie `SMS_TEST_MODE=true`, aby nie generować zbędnych wiadomości.
26. Aktualizacje produkcyjne odbywają się przez commit z GitHub i wdrożenie po stronie serwera; lokalne zmiany w repo mają pozostać zgodne z produkcją, ale domyślne uruchamianie musi być testowe.
27. Codex może uruchamiać skrypty Python (`collector_full.py`, `sms_sender.py`) w WSL, używając zmiennych z właściwego pliku środowiskowego.
28. Dla długiego nasłuchu preferuj uruchomienie w tle z logowaniem, np.:
    - `nohup .venv/bin/python collector_full.py > logs/collector.log 2>&1 &`
    - podgląd: `tail -f logs/collector.log`
    - zatrzymanie: `pkill -f collector_full.py`
29. Przed uruchomieniem testów/lintów odpal `pre-commit run --all-files`. W CI (GitHub Actions) uruchamiaj `ruff check` i `black --check`.

## Bezpieczeństwo i porządek
30. Nie commituj pliku `.env`, `.env.test` ani sekretów. `.gitignore` musi zawierać: `.env`, `.env.*`, `.venv/`, `__pycache__/`, logi itp.
31. Nie wykonuj operacji destrukcyjnych bez wyraźnego polecenia użytkownika (usuwanie plików, migracje bazy itp.).
