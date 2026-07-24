# Moduł `/device` – stan zakończenia etapu 2026-07-24

## Status

Etap funkcjonalnej przebudowy modułu `/device` jest zakończony i przechodzi w tryb
stabilizacji podczas normalnej pracy użytkowników. Na dzień `2026-07-24` nie stwierdzono
błędu blokującego przyjęcia PZ, obsługę magazynu, audyt ani synchronizację arkusza.

Punktem odniesienia dla kodu jest produkcyjny commit
`b82bd810b1cca7e95fcb6241c5f4c10df875f8f4`, wdrożony na Windows Server
`192.168.0.8`. Główna przebudowa znajduje się w commicie
`2a0c4dc055f6e4d4ca870e196dda4fc69fc79a1f`, a commit `b82bd81` poprawia zapis
ceny urządzenia do Google Sheets jako liczby zamiast wartości interpretowanej jako data.

Wersja interfejsu modułu to `/device v0.3.1`, a wersja aplikacji CTIP wdrożona podczas
zamknięcia etapu to `0.2.14`.

## Zakres działający

### Nawigacja i interfejs

- Moduł ma osobne ekrany: start, przyjęcie PZ, magazyn, audyt, historia PZ i synchronizacje.
- Na komputerze działa stałe lewe menu, a na mniejszych ekranach poziomy pasek nawigacji.
- Dostępne są motywy niebieski, grafitowy i miętowy, zapisywane w profilu użytkownika.
- Górny nagłówek pokazuje wersję `/device`, a wersjonowane adresy CSS i JavaScript
  ograniczają problemy z pamięcią podręczną po wdrożeniu.
- Szeroka tabela magazynu przewija się wewnątrz karty. Szczegóły otwiera dwuklik wiersza
  albo klawisz `Enter` lub spacja.

### Przyjęcie urządzeń

- Przyjęcie PZ wymaga dostawcy, modelu i numeru seryjnego.
- Brak dokumentu zewnętrznego albo cena `0` wymagają jawnego wyjątku i uzasadnienia
  mającego co najmniej 10 znaków.
- Frontend oznacza błędne pola i pokazuje zbiorczą informację o brakujących danych.
- Wystawiający pochodzi z mapowania zalogowanego użytkownika CTIP do użytkownika
  Menadżera Serwisu.
- Każdy egzemplarz otrzymuje osobny rekord `MAGAZYN`, `MASZYNA` i wpis rejestru CTIP.
- Operacja jest idempotentna dzięki UUID. Po niejednoznacznym błędzie transportowym można
  bezpiecznie ponowić to samo żądanie.
- Liczniki B/W, kolor i skan są opcjonalne. Jeżeli je podano, trafiają do Firebird,
  historii CTIP i kolejki aktualizacji arkusza.
- Nowy model i nowego dostawcę można dodać z interfejsu, zgodnie z uprawnieniami.

### Magazyn

- Widok łączy dane arkusza `Urzadzenia_magazyn`, magazynu Firebird `28`, kartotek
  `MASZYNA` i rejestru CTIP.
- Kolumna `Arkusz/Magazyn/Urządzenie/CTIP` wskazuje `OK` albo `BD` dla każdego źródła.
- Domyślny widok operacyjny pokazuje aktywny arkusz oraz pozycje magazynu `28` z dostępnym
  stanem co najmniej `1`.
- Tabela pokazuje liczniki, cenę zakupu netto i najnowszą uwagę.
- Szczegóły umożliwiają zapis nowego odczytu liczników, uwagi i rezerwacji ręcznej.
- Obniżenie licznika wymaga jawnego wyjątku i uzasadnienia; starszy odczyt pozostaje tylko
  w historii CTIP.

### Audyt

- Audyt działa w tle, jest trwały po restarcie i jednocześnie dopuszcza jeden przebieg.
- Odczytuje świeży arkusz, magazyn `28`, kartoteki `MASZYNA` oraz rejestr CTIP.
- Nie wykonuje automatycznych napraw, migracji ani zapisów do Firebird lub Google Sheets.
- Zachowywanych jest 20 ostatnich przebiegów.
- Wynik ma priorytet `DUPLIKAT` → `ROZBIEŻNOŚĆ` → `BRAKI` → `OK`.
- Dane historyczne bez wpisu CTIP są oznaczane jako `Tylko audyt`.

### Historia i wycofanie PZ

- Historia pokazuje utworzone operacje PZ i pozwala przejść do ich szczegółów.
- Wycofanie jest dostępne administratorowi oraz użytkownikom z prawem
  `can_withdraw_device_pz`.
- Przed wykonaniem system pokazuje wpływ na PZ, `MAGAZYN`, `MASZYNA`, arkusz i późniejsze
  powiązania.
- Użytkownik podaje uzasadnienie i przepisuje pełny numer PZ.
- Zwykły operator może wycofać tylko dokument niezmieniony i nieużywany.
- Administrator może wymusić odłączenie powiązań wyłącznie przy kompletnym zapisie
  początkowym CTIP.
- Historia CTIP pozostaje zachowana ze statusem `withdrawn`.

### Google Sheets

- Zatwierdzenie PZ w Firebird nie zależy od dostępności Google.
- Zapis arkusza trafia do trwałego outboxu i jest ponawiany maksymalnie 10 razy.
- Nieudana wcześniejsza operacja blokuje późniejsze operacje tego samego egzemplarza,
  zachowując ich kolejność.
- Przyjęcie PZ wpisuje w kolumnie `UWAGI` czerwony tekst
  `dodana automatem PZ z CTIP`.
- Cena jest zapisywana jako liczba. Naprawę formatu zawiera commit `b82bd81`.
- Środowisko testowe może zapisywać wyłącznie do chronionego skoroszytu testowego.

## Uprawnienia i bezpieczeństwo

- Administrator ma dostęp do wszystkich funkcji modułu.
- Inny użytkownik wymaga sekcji `device`; operacje zapisowe wymagają również mapowania do
  użytkownika Menadżera Serwisu.
- Produkcyjny zapis Firebird jest dodatkowo chroniony konfiguracją środowiska.
- Lokalny stos testowy używa `ctip_test`, lokalnej kopii Firebird, symulacji SMS, Mailpit
  i ograniczonej bramy Google.
- Moduł nie tworzy historycznych PZ ani nie naprawia automatycznie starych rekordów.
- Operacje PZ, liczników, uwag, rezerwacji, synchronizacji i wycofania są audytowane.

## Sprawdzenia wykonane na produkcji

1. Utworzono dokument `PZ / 270 / 2026`, identyfikator PZ `38289`, z trzema pozycjami.
2. Potwierdzono utworzenie dokumentu, pozycji magazynowych, kartotek urządzeń i zadań
   synchronizacji arkusza.
3. Wykryto interpretowanie ceny w arkuszu jako daty; poprawiono typ zapisu w commicie
   `b82bd81`.
4. Uruchomiono kontrolowaną próbę wycofania PZ; CTIP nie zgłosił błędu wykonania.
5. Podczas zamknięcia etapu repozytorium produkcyjne było czyste i wskazywało `b82bd81`.
6. Migracja produkcyjna wskazywała pojedynczy head `c4d8e2f6a1b3`.
7. Usługi `CollectorService`, `CTIP-Web`, `CTIP-SMS` i `CTIP-FormsPublic` działały.
8. Healthcheck aplikacji na porcie `8000` i formularzy publicznych na porcie `8100`
   zwracał HTTP `200`.

## Walidacja gałęzi zamknięcia

- `pre-commit run --all-files`, `ruff check .` i `black --check .` zakończyły się
  powodzeniem.
- Izolowany zestaw testów modułu urządzeń zakończył się wynikiem `46 passed`.
- Import aplikacji z katalogu roboczego potwierdził wersję `CTIP API 0.2.14`.
- `alembic heads` zwrócił pojedynczy head `c4d8e2f6a1b3`.
- Szeroki zestaw bez testów panelu administratora i bez lokalnego skryptu z katalogu
  `inbox` zakończył się wynikiem `298 passed, 4 failed`. Błędy dotyczą wcześniejszych
  testów pulpitu umów, statycznego raportu oraz preflight Google Sheets.
- `tests/test_admin_ui.py` zakończył się wynikiem `39 passed, 2 failed`; oba błędy dotyczą
  wcześniejszych oczekiwań tekstowych szablonu konfiguracji Firebird.
- `tests/test_admin_backend.py` nie powoduje już błędu interpretera przy `py_compile`.
  Pełny plik zakończył się wynikiem `139 passed, 15 failed`; błędy wynikają głównie z
  blokady zapisów konfiguracji w `.env.test` oraz wcześniejszych niespójności testów.
- Żaden z powyższych błędów nie został wywołany przez dokumentacyjny commit zamykający
  etap `/device`. Nie poprawiano ich w tym zadaniu, aby nie rozszerzać zakresu zmian.

## Obserwacja po zamknięciu

Moduł pozostaje objęty testami podczas normalnej pracy. W szczególności należy obserwować:

- zachowanie numeracji kolejnego PZ i KP po wycofaniu dokumentu;
- wielokrotne próby outboxu przy czasowej niedostępności Google;
- urządzenia historyczne z niejednoznacznym serialem lub ewidencją;
- próby wycofania PZ po późniejszych zmianach urządzenia lub utworzeniu zlecenia;
- zgodność liczników kolorowych i skanu dla kolejnych modeli;
- wzrost czasu pełnego audytu wraz z przyrostem danych.

Te punkty nie blokują bieżącej pracy. Nowy błąd należy odtworzyć na środowisku testowym,
powiązać z konkretnym PZ albo urządzeniem i dopiero potem przygotować osobny hotfix.

## Powrót do projektu

1. Jako bazę przyjąć commit `b82bd81`.
2. Przeczytać `docs/projekt/obsluga_urzadzen.md` i niniejszy raport.
3. Sprawdzić wersję `/device v0.3.1` w nagłówku aplikacji.
4. Uruchomić `.venv`, wczytać `.env.test` i wykonać testy modułów `device_*`.
5. Zweryfikować pojedynczy head Alembic przed dodaniem kolejnej migracji.
6. Pracować na lokalnym `ctip_test` i lokalnym Firebird; nie używać produkcyjnych baz bez
   jawnego scenariusza produkcyjnego.
7. Każdą poprawkę wdrażać jako osobny commit z backupem, kontrolą migracji i healthcheckiem.

Najważniejsze punkty wejścia:

- `app/api/routes/admin_device.py` – API modułu;
- `app/services/device_intake.py` – przyjęcia PZ;
- `app/services/device_warehouse.py` – scalony magazyn;
- `app/services/device_audit.py` – pełny audyt;
- `app/services/device_withdrawal.py` – wycofanie PZ;
- `app/services/device_sheet_worker.py` i `app/services/workflow_sheet_sync.py` – outbox
  oraz Google Sheets;
- `app/templates/device/index.html`, `app/static/device/device.js` i
  `app/static/device/device.css` – interfejs.
