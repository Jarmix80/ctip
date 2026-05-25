# Model gałęzi i wdrożeń CTIP po porządkowaniu produkcji

## Cel
Po zmianach z 25 maja 2026 repozytorium ma wspierać dwa równoległe tory:
- stabilizację i odtwarzalność produkcji,
- dalszy rozwój funkcji na lokalnym środowisku testowym.

Kluczowa zasada: produkcja nie może już żyć w postaci ręcznie nadpisanych plików poza historią Git.

## Stan wyjściowy
- produkcja Windows Server została ręcznie uporządkowana i zapisana commitem `92e46d242a7c0aa25e040227f084348df9c8f35e`; repo na serwerze pozostaje na lokalnej gałęzi historycznej, ale zna już zdalne refy `origin/prod/realign-2026-05-25` i tag `prod-2026-05-25-realign`,
- gałąź `prod/realign-2026-05-25` zawiera równoważny technicznie zakres porządkujący bootstrap Windows i model `env-only`,
- gałąź `integration/prod-realign-2026-05-25` zawiera dalszy rozwój (w tym dostawy) i jest właściwą bazą pracy testowej,
- gałąź `main-realign-2026-05-25` porządkuje linię `main` względem `origin/main` bez duplikowania commita GRENKE.

## Rola gałęzi
### `prod/realign-2026-05-25`
- techniczna baza odtwarzająca obecny model produkcyjny,
- zawiera tylko zakres potrzebny do stabilnego uruchamiania Windows i spójnego modelu konfiguracji,
- nie powinna przyjmować eksperymentalnych zmian funkcjonalnych.

### `integration/prod-realign-2026-05-25`
- główna gałąź lokalnego rozwoju i testów,
- może zawierać zmiany wyprzedzające produkcję,
- musi okresowo wchłaniać poprawki techniczne z linii produkcyjnej (bootstrap, deploy, runbooki, hotfixy krytyczne).

### `release/grenke-only`
- gałąź historyczna,
- jest zsynchronizowana z `origin/release/grenke-only`, ale nie powinna być dalej używana jako docelowa baza nowych wdrożeń,
- pozostaje tylko jako punkt odniesienia do wcześniejszych wdrożeń.

### `main`
- lokalny `main` jest już przestawiony na czystą historię i śledzi `origin/main-realign-2026-05-25`,
- sama zdalna gałąź `origin/main` nie została jeszcze przepisana i pozostaje stanem historycznym sprzed porządkowania,
- docelowo dopiero po osobnej decyzji można zastąpić `origin/main` linią `main-realign-2026-05-25`.

### `main-realign-2026-05-25`
- techniczna gałąź porządkująca historię `main`,
- bazuje na `origin/main` i zawiera tylko unikalne commity brakujące po stronie zdalnej,
- ma ten sam stan plików co wcześniejszy lokalny `main`, ale bez rozjazdu `ahead/behind`.

## Zalecany przepływ pracy
1. Nowe funkcje rozwijaj na `integration/prod-realign-2026-05-25`.
2. Krytyczne hotfixy produkcyjne przygotowuj od `prod/realign-2026-05-25`.
3. Każdy hotfix produkcyjny po wdrożeniu przenoś z powrotem na gałąź integracyjną.
4. Dla prac bazowych nad długoterminowym kierunkiem rozwoju używaj `main` lub bezpośrednio `main-realign-2026-05-25`, nie starego `origin/main`.
5. Każde wdrożenie produkcji wykonuj z oznaczonego commita lub taga, nigdy z brudnego worktree.
6. Po wdrożeniu zapisuj na serwerze:
   - commit SHA,
   - stan `.env`,
   - wynik `/health`,
   - listę zrestartowanych usług,
   - lokalizację backupu.

## Minimalna procedura promocji zmian
1. Na gałęzi integracyjnej przygotuj commit lub serię commitów do wdrożenia.
2. Odetnij z nich czysty zakres produkcyjny na gałęzi `prod/*`.
3. Zweryfikuj:
   - `pre-commit run --all-files`,
   - testy jednostkowe dla zmienionego obszaru,
   - smoke na `.env.test`.
4. Wypchnij odpowiednią gałąź do `origin`:
   - `prod/*` dla wdrożeń,
   - `integration/*` dla dalszego rozwoju,
   - `main-realign-*` dla porządkowania bazowej linii.
5. Na Windows Server wykonaj backup i wdrożenie z konkretnego commita.
6. Po wdrożeniu zapisz ewentualne różnice środowiskowe poza Git (`.env`, usługi NSSM, `collector_service_config.json`).

## Czego unikać
- bezpośrednich poprawek produkcyjnych bez odpowiadającego commita lokalnego,
- wdrożeń z niezacommitowanego worktree,
- trzymania sekretów i konfiguracji połączeń w Git,
- mieszania zmian funkcjonalnych z operacyjnymi w jednym dużym wdrożeniu,
- dalszego używania `release/grenke-only` jako aktywnej linii rozwoju.

## Następny etap
Docelowo należy jeszcze:
- zdecydować, czy `origin/main` ma zostać zastąpiony linią `main-realign-2026-05-25`,
- utrzymać zasadę: produkcja z `prod/*`, test i rozwój z `integration/*`,
- kontynuować tagowanie wdrożeń produkcyjnych.
