# Wdrożenie wydania CTIP na Windows Server

## Zasada nadrzędna

Jedynym obsługiwanym mechanizmem wdrożenia produkcyjnego jest lokalne polecenie `scripts/deploy_windows_prod.py`. Skrypt odczytuje wyłącznie klucz `ssh_serv_link` z `.env`, zachowuje cytowanie argumentów SSH i nie ujawnia wartości w logach. Historyczne skrypty aktualizacyjne i wdrożeniowe są zablokowane.

Duży kod PowerShell jest pobierany na serwer z konkretnego commita jako binarne archiwum `git archive`, rozpakowywany do pliku tymczasowego, dekodowany rygorystycznie jako UTF-8, normalizowany do `LF`, weryfikowany przez SHA-256 i uruchamiany przez `powershell -File`. Dzięki temu polskie znaki nie przechodzą przez dekodowanie konsoli Windows, a ustawienie `core.autocrlf` nie wpływa na kontrolę integralności. `EncodedCommand` służy wyłącznie do krótkich poleceń kontrolnych.

## Przygotowanie planu

Administrator ustala:

- pełny SHA bieżącej wersji produkcyjnej;
- pełny SHA wydania będącego potomkiem wersji produkcyjnej;
- oczekiwaną rewizję Alembic przed i po wdrożeniu;
- dozwolone ścieżki zmian;
- dokładną listę usług do restartu;
- lokalne endpointy kontrolne.

Przykład kontroli bez zmian:

```bash
source .venv/bin/activate
python scripts/deploy_windows_prod.py \
  --release <pełny_sha_release> \
  --expected-current <pełny_sha_produkcji> \
  --alembic-before <rewizja_przed> \
  --alembic-after <rewizja_po> \
  --allowed-path app \
  --allowed-path alembic \
  --allowed-path scripts \
  --service CTIP-Web \
  --endpoint 'CTIP|http://127.0.0.1:8000/health|200' \
  --endpoint 'FormsPublic|http://127.0.0.1:8100/health|200' \
  --dry-run
```

Tryb `--dry-run` nie zatrzymuje usług, nie migruje bazy i nie przełącza commita. Dopiero osobne, jawne polecenie z `--apply` wykonuje wdrożenie.

## Przebieg `--apply`

1. Potwierdza trasę i adres źródłowy połączenia SSH.
2. Sprawdza dokładny zdalny HEAD, czystość Git, usługi i endpointy.
3. Importuje `.env`, a następnie nadpisuje go aktywnym środowiskiem NSSM `CTIP-Web` bez wypisywania wartości.
4. Weryfikuje potomność release i listę zmienionych ścieżek.
5. Wykonuje pełny backup PostgreSQL i Firebird.
6. Tworzy osobny worktree kandydata i uruchamia `compileall` oraz kontrolę Alembic.
7. Migruje bazę, zatrzymuje wyłącznie wskazane usługi i przełącza produkcję na dokładny commit.
8. Uruchamia usługi, sprawdza endpointy i analizuje log tylko od bieżącego startu procesu.
9. Usuwa worktree kandydata i zapisuje raport bez sekretów.

Przy błędzie skrypt przywraca poprzedni commit i ponownie uruchamia wskazane usługi. Backupów ani baz nie usuwa.

## Ograniczenia operacyjne

- Nie używać `source .env`, `git pull`, aktywnej nazwy gałęzi ani założenia, że produkcja nie pracuje w detached HEAD.
- Nie przesyłać dużych skryptów przez linię poleceń PowerShell.
- Nie traktować samego `stderr` Alembic jako awarii; decyduje kod wyjścia.
- Endpoint FormsPublic sprawdzać lokalnie przez `127.0.0.1:8100`.
- Nie restartować usług niewymienionych w planie wydania.
- Nie wykonywać `--apply` bez zaakceptowanego dry-run, aktualnych backupów i okna serwisowego.
