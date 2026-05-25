# Runbook kolejnej poprawki produkcyjnej CTIP po porządkowaniu repo

## Cel
Ten dokument opisuje najkrótszą i powtarzalną ścieżkę wdrożenia kolejnej poprawki produkcyjnej po wyprostowaniu gałęzi:
- rozwój lokalny: `integration/prod-realign-2026-05-25`,
- wspólna baza: `main`,
- wdrożenia produkcji: `prod/realign-2026-05-25`.

Dokument zakłada:
- brak ręcznych edycji plików na serwerze poza kontrolowanym wdrożeniem,
- każda zmiana produkcyjna ma odpowiadający commit w Git,
- serwer produkcyjny pracuje na gałęzi `prod/realign-2026-05-25`.

## Zasady
1. Nie wdrażaj na produkcję bezpośrednio z `integration/*`.
2. Nie wdrażaj z brudnego worktree.
3. Każdą poprawkę produkcyjną najpierw weryfikuj lokalnie na `.env.test`.
4. Na produkcję promuj tylko wybrany zakres commitów odcięty na gałęzi `prod/*`.
5. Przed restartem usług, zmianą `.env`, migracjami i testami live wymagane jest osobne potwierdzenie operatora.

## Aktualne gałęzie robocze
- `integration/prod-realign-2026-05-25`: bieżący rozwój i testy lokalne,
- `main`: wspólna baza rozwoju,
- `prod/realign-2026-05-25`: linia produkcyjna,
- `release/grenke-only`: archiwum historyczne, bez dalszego rozwoju.

## Krok 1. Przygotowanie poprawki lokalnie
Pracuj na:

```bash
git switch integration/prod-realign-2026-05-25
```

Wykonaj zmianę w kodzie, zaktualizuj testy i dokumentację.

Minimalna walidacja lokalna:

```bash
source .venv/bin/activate
pre-commit run --all-files
pytest -q <testy_zmienionego_obszaru>
```

Jeśli poprawka dotyczy Windows/bootstrapu/deploy:
- sprawdź też dokumenty w `docs/instal/`,
- upewnij się, że README wskazuje aktualny sposób wdrożenia.

## Krok 2. Odetnij czysty zakres produkcyjny
Załóż nową gałąź produkcyjną od bieżącego `prod/realign-2026-05-25`:

```bash
git switch prod/realign-2026-05-25
git pull --ff-only
git switch -c prod/hotfix-YYYY-MM-DD-opis
```

Przenieś tylko te commity, które mają wejść na produkcję:

```bash
git cherry-pick <sha1> [<sha2> ...]
```

Jeżeli poprawka jest większa i nie składa się z gotowych commitów:
- najpierw rozbij ją logicznie na commity na `integration/*`,
- dopiero potem cherry-pickuj wyłącznie potrzebny zakres.

## Krok 3. Zweryfikuj gałąź produkcyjną lokalnie
Na nowej gałęzi `prod/hotfix-*` uruchom:

```bash
source .venv/bin/activate
pre-commit run --all-files
pytest -q <testy_zmienionego_obszaru>
```

Jeżeli poprawka dotyczy konfiguracji lub ścieżki startu usług:
- sprawdź pliki `scripts/windows/*.ps1`,
- sprawdź bootstrapy `scripts/windows/run_*.py`,
- upewnij się, że nowe artefakty hosta pozostają poza Git.

Jeżeli poprawka dotyczy UI/admin:
- zweryfikuj, że README i `docs/` opisują nowy stan.

## Krok 4. Opublikuj zakres do wdrożenia
Wypchnij gałąź i oznacz punkt wdrożeniowy tagiem:

```bash
git push -u origin prod/hotfix-YYYY-MM-DD-opis
git tag -a prod-YYYY-MM-DD-opis -m "Opis wdrozenia"
git push origin prod-YYYY-MM-DD-opis
```

Rekomendacja:
- każda produkcyjna aktualizacja dostaje osobny tag,
- tag wskazuje dokładny commit wdrożeniowy.

## Krok 5. Backup produkcji
Ten krok wykonuj dopiero po potwierdzeniu operatora.

Minimalny zestaw:
1. backup baz przez `scripts/windows/backup_prod_databases.ps1`,
2. kopia `D:\CTIP\.env`,
3. zapis `git rev-parse HEAD`, `git status -sb`,
4. snapshot logów i konfiguracji usług,
5. zapis aktualnego `/health`.

Jeżeli wdrożenie obejmuje zmianę `.env`:
- przygotuj diff starego i nowego pliku,
- wykonaj osobny backup `.env` przed zapisem.

## Krok 6. Wdrożenie na Windows Server
Ten krok wykonuj dopiero po potwierdzeniu operatora.

Na serwerze:

```powershell
cd D:\CTIP
git fetch --prune origin --tags
git switch prod/realign-2026-05-25
git pull --ff-only
git switch --detach <tag_lub_commit_wdrozeniowy>
```

Jeżeli polityka wdrożeń wymaga pozostania na gałęzi zamiast detached HEAD:

```powershell
git switch prod/realign-2026-05-25
git reset --hard <nie_uzywac_bez_osobnej_zgody>
```

Powyższy wariant z `reset --hard` traktuj jako operację destrukcyjną. Domyślnie preferowany jest wariant:
- `git switch prod/realign-2026-05-25`,
- `git merge --ff-only <tag_lub_commit>`,
- albo wdrożenie bezpośrednio na branchu po `pull --ff-only`, jeśli commit jest już końcem gałęzi.

Po aktualizacji kodu:
- zrestartuj wyłącznie wymagane usługi,
- zwykle: `CollectorService`, `CTIP-Web`, `CTIP-SMS`,
- `CTIP-FormsPublic` tylko jeśli zmiana go dotyczy.

## Krok 7. Weryfikacja po wdrożeniu
Minimalna kontrola:

```powershell
git rev-parse HEAD
git status -sb
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic
```

Do sprawdzenia:
1. `git status` jest czysty,
2. `/health` zwraca `200`,
3. usługi wróciły do `Running`,
4. logi nie pokazują nowych błędów startowych,
5. jeśli zmiana dotyczyła konkretnego modułu, wykonaj jego smoke test.

Przykłady smoke testów:
- formularz/admin: logowanie i otwarcie odpowiedniego widoku,
- CTIP: świeży przyrost `call_events`,
- SMS: brak nowych błędów kolejki,
- mailbox: ręczne uruchomienie bezpiecznego przebiegu diagnostycznego, jeśli istnieje.

## Krok 8. Zapis wyniku wdrożenia
Po wdrożeniu zapisz w notatce operatorskiej:
- datę i godzinę,
- commit SHA,
- tag wdrożeniowy,
- listę zrestartowanych usług,
- lokalizację backupu,
- wynik `/health`,
- wynik smoke testu,
- ewentualne odchylenia środowiskowe (`.env`, NSSM, logi).

## Rollback
Jeżeli wdrożenie nie przejdzie:
1. wróć do poprzedniego tagu lub commita,
2. przywróć poprzedni `.env`, jeśli był zmieniany,
3. zrestartuj usługi,
4. sprawdź `/health`,
5. dopiero jeśli to nie wystarczy, uruchamiaj odtworzenie backupu baz.

Rollback baz traktuj jako osobną operację wysokiego ryzyka i wykonuj tylko po osobnym potwierdzeniu.

## Minimalny wzorzec codziennej pracy
1. Zmiana powstaje na `integration/prod-realign-2026-05-25`.
2. Po testach lokalnych odcinasz `prod/hotfix-*`.
3. Publikujesz branch i tag.
4. Wykonujesz backup produkcji.
5. Wdrażasz dokładnie ten commit/tag.
6. Weryfikujesz `health`, usługi i smoke test.

To jest docelowy, powtarzalny model dalszych wdrożeń CTIP.
