# Model gałęzi i wdrożeń CTIP po porządkowaniu produkcji

## Cel
Po zmianach z 25 maja 2026 repozytorium ma wspierać dwa równoległe tory:
- stabilizację i odtwarzalność produkcji,
- dalszy rozwój funkcji na lokalnym środowisku testowym.

Kluczowa zasada: produkcja nie może już żyć w postaci ręcznie nadpisanych plików poza historią Git.

## Stan wyjściowy
- produkcja Windows Server została ręcznie uporządkowana i zapisana commitem `92e46d242a7c0aa25e040227f084348df9c8f35e` na gałęzi `release/grenke-only`,
- lokalna gałąź `prod/realign-2026-05-25` zawiera równoważny technicznie zakres porządkujący bootstrap Windows i model `env-only`,
- lokalna gałąź `integration/prod-realign-2026-05-25` zawiera dalszy rozwój (w tym dostawy) i jest właściwą bazą pracy testowej.

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
- nie powinna być dalej używana jako docelowa baza nowych wdrożeń,
- pozostaje tylko jako punkt odniesienia do wcześniejszych wdrożeń.

### `main`
- docelowa gałąź długoterminowa,
- przed ponownym użyciem jako jedynej linii wdrożeniowej wymaga osobnego uporządkowania względem `origin/main`, lokalnego `main` i obecnych gałęzi `prod/*` oraz `integration/*`.

## Zalecany przepływ pracy
1. Nowe funkcje rozwijaj na `integration/prod-realign-2026-05-25`.
2. Krytyczne hotfixy produkcyjne przygotowuj od `prod/realign-2026-05-25`.
3. Każdy hotfix produkcyjny po wdrożeniu przenoś z powrotem na gałąź integracyjną.
4. Każde wdrożenie produkcji wykonuj z oznaczonego commita lub taga, nigdy z brudnego worktree.
5. Po wdrożeniu zapisuj na serwerze:
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
4. Wypchnij gałąź produkcyjną do `origin`.
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
- ustalić jeden oficjalny branch publikowany do `origin` dla produkcji,
- uporządkować relację `main` <-> `origin/main` <-> `integration/prod-realign-2026-05-25`,
- wprowadzić tagowanie wdrożeń produkcyjnych.
