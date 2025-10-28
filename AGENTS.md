# CTIP – zasady pracy dla Codex

## Język i styl
1. Wszystkie komentarze, README i dokumentacja muszą być tworzone wyłącznie po polsku.
2. Obowiązuje styl techniczny, zgodny z dokumentacją administratora systemu.

## Uprawnienia i środowisko
3. Codex ma prawo edytować pliki w tym repozytorium bez konieczności uzyskania dodatkowej zgody.
4. Codex pracuje **wyłącznie w obrębie tego repozytorium** (nie wykonuje zmian poza nim).
5. Zawsze aktywuj i używaj środowiska `.venv`; jeżeli nie istnieje, utwórz je (`python3 -m venv .venv`).
6. Przed uruchomieniem poleceń `python`/`pip` automatycznie aktywuj `.venv`.
7. Automatycznie wczytuj zmienne z `.env` (PBX/DB itp.). **Nigdy** nie zapisuj sekretów do repo; trzymaj je w `.env`.
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
16. Centrala Slican używana przez projekt identyfikuje się jako `CP-000 NO03914 v1.23.0140/15` (`PBX_HOST=192.168.0.11`); przed startem potwierdź adres źródłowy hosta kolektora (WSL, np. `172.x.x.x`) i dostęp do sieci lokalnej.
17. Sekwencja inicjalizacji CTIP: `aWHO` → `aLOGA <PIN>` musi być wykonywana dokładnie raz na połączenie TCP.
18. Domyślny PIN CTIP to `1234`; zmiany wymagają synchronizacji z centralą i aktualizacji `.env`.
19. Do testów połączenia można używać `nc`/`telnet` w trybie RAW, np. `timeout 15s nc -v $PBX_HOST $PBX_PORT`.
20. Codex może analizować logi połączeń z centralą i raportować błędy komunikacji CTIP (nie przerywaj pracy przy sporadycznych błędach – loguj i kontynuuj).

## Uruchamianie i operacje
21. Codex może uruchamiać skrypty Python (`collector_full.py`, `sms_sender.py`) w WSL, używając zmiennych z `.env`.
22. Dla długiego nasłuchu preferuj uruchomienie w tle z logowaniem, np.:
    - `nohup .venv/bin/python collector_full.py > logs/collector.log 2>&1 &`
    - podgląd: `tail -f logs/collector.log`
    - zatrzymanie: `pkill -f collector_full.py`
23. Przed uruchomieniem testów/lintów odpal `pre-commit run --all-files`. W CI (GitHub Actions) uruchamiaj `ruff check` i `black --check`.

## Bezpieczeństwo i porządek
24. Nie commituj pliku `.env` ani sekretów. `.gitignore` musi zawierać: `.env`, `.venv/`, `__pycache__/`, logi itp.
25. Nie wykonuj operacji destrukcyjnych bez wyraźnego polecenia użytkownika (usuwanie plików, migracje bazy itp.).
