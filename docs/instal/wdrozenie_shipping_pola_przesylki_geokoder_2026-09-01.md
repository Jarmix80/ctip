# Wdrożenie pól przesyłki MS i geokodera Shipping

## Zakres

Zmiana jest dzielona na dwa niezależne wydania:

1. zapis metadanych etykiety i kamieni milowych DPD do zlecenia Menadżera Serwisu;
2. ręczny geokoder pocztowej części adresu przez Adresy.app.

Rozdzielenie ogranicza zakres rollbacku. Wydanie drugie nie jest wymagane do działania etykiet, dokumentów RW/WZ/FV ani synchronizacji InfoServices.

## Zasady bezpieczeństwa

- Najpierw migrowana i uruchamiana jest wyłącznie baza `ctip_test` oraz kopia Firebirda `BAZAMS_TEST`.
- Produkcyjny Firebird nie może być użyty do pierwszego testu pól.
- `KOSZTP1` i `KOSZTP2` pozostają bez zmian.
- Historyczne przesyłki bez `firebird_label_metadata_synced_at` nie są automatycznie uzupełniane.
- Anulowane albo zmienione zdarzenie DPD zapisane wcześniej jako data powoduje konflikt, nie automatyczne cofnięcie pola MS.
- Klucz Adresy.app pozostaje w pliku środowiskowym i nie może trafić do obrazu, repozytorium, przeglądarki ani logów.
- Do Adresy.app trafiają wyłącznie ulica, kod pocztowy i miejscowość.
- Wspólna paczka zachowuje osobny rekord przesyłki i osobny zapis pól dla każdego zlecenia MS.

## Wydanie 1: pola przesyłki MS

### Konfiguracja testowa

Po migracji `d6e8f0a2b4c7` ustaw w bezpiecznej konfiguracji testowej:

```dotenv
DPD_INFO_ENABLED=true
SHIPPING_DPD_FIREBIRD_MILESTONES_ENABLED=true
FB_ALLOW_WRITES=true
SHIPPING_TEST_FIREBIRD_WRITES=true
SMS_TEST_MODE=true
BLOCK_CLIENT_COMMUNICATIONS=true
```

Pozostałe blokady środowiska testowego nadal muszą potwierdzać bazę PostgreSQL `ctip_test`, Firebirda z `TEST` w nazwie i tryb DPD `mock` albo `demo`. Jeżeli test ma korzystać z rzeczywistych zdarzeń InfoServices, numer listu musi być kontrolowany i nie może należeć do klienta produkcyjnego.

### Test odbiorczy

1. Przed próbą wykonaj backup PostgreSQL `ctip_test` i pliku `BAZAMS_TEST`.
2. Wygeneruj nową etykietę dla świeżego zlecenia; nie używaj zlecenia `18416/2026`, którego wartości `test1`–`test4` są wyłącznie mapą pól.
3. Potwierdź w MS: `PRZESYLKA`, wpis z datą i sposobem utworzenia numeru w `WYKONANIE`, pełny adres bez telefonu i e-maila w `ADRES_PRZES` oraz brak zmian `KOSZTP1` i `KOSZTP2`.
4. Dla wspólnej paczki potwierdź ten sam numer listu i poprawny adres na każdym powiązanym zleceniu.
5. Wprowadź zdarzenie „Gotowa do nadania” i potwierdź brak `DATA_PRZES`.
6. Wprowadź potwierdzone zdarzenie „Nadanie” lub „Przyjęta do Oddziału”; potwierdź `DATA_PRZES` i pojedynczy wpis „Wysłana paczka” w `WYKONANIE`.
7. Powtórz synchronizację i potwierdź brak duplikatu tekstu oraz brak kolejnej zmiany daty.
8. Wprowadź niedoręczenie albo przekierowanie i potwierdź aktualizację `PRZESYLKA_WE` bez `DATA_PRZES_WE`.
9. Wprowadź doręczenie i potwierdź `DATA_PRZES_WE` oraz końcowy opis w `PRZESYLKA_WE`.
10. Zasymuluj anulowanie użytego zdarzenia i potwierdź `firebird_milestone_error`, brak cofnięcia daty oraz widoczny konflikt do ręcznego uzgodnienia.
11. Potwierdź brak nowych SMS-ów, e-maili, dokumentów magazynowych i zmian stanów wynikających wyłącznie z synchronizacji statusów.

### Bramka produkcyjna

Produkcję można przygotować dopiero po poprawnym przejściu całego testu. Najpierw wdrażany jest kod i migracja z flagą:

```dotenv
SHIPPING_DPD_FIREBIRD_MILESTONES_ENABLED=false
```

Po kontroli healthchecków i pozostałych modułów należy przeprowadzić jeden rzeczywisty pilot DPD na nowym zleceniu. Dopiero po potwierdzeniu numeru, adresu, odbioru i doręczenia można włączyć flagę i zrestartować wyłącznie usługę `CTIP-Web`. Pierwsze trzy przesyłki wymagają ręcznej kontroli pól MS po każdym przebiegu InfoServices.

Rollback funkcjonalny polega na ustawieniu `SHIPPING_DPD_FIREBIRD_MILESTONES_ENABLED=false` i restarcie wyłącznie `CTIP-Web`. Addytywnych kolumn PostgreSQL nie trzeba usuwać.

### Kontrolowany pilot na zleceniu archiwalnym

Archiwalne zlecenie może służyć do sprawdzenia rzeczywistych zdarzeń
InfoServices oraz zapisu `DATA_PRZES`, `DATA_PRZES_WE`, `WYKONANIE` i
`PRZESYLKA_WE`. Nie zastępuje ono świeżego pilota generowania etykiety, pola
`PRZESYLKA` ani `ADRES_PRZES`.

Podczas całej operacji pozostaw:

```dotenv
SHIPPING_DPD_FIREBIRD_MILESTONES_ENABLED=false
```

Najpierw odśwież wyłącznie wskazany list, a następnie wykonaj dry-run:

```bash
python scripts/dpd_infoservices_backfill.py --waybill <numer_listu> --apply
python scripts/shipping_dpd_milestone_pilot.py \
  --order <numer_zlecenia/rok> \
  --waybill <numer_listu>
```

Raport dry-run zawiera token stanu i dokładną frazę potwierdzającą. Zapis wolno
uruchomić dopiero po wykonaniu backupu PostgreSQL i Firebirda oraz kontroli pól
MS:

```bash
python scripts/shipping_dpd_milestone_pilot.py \
  --order <numer_zlecenia/rok> \
  --waybill <numer_listu> \
  --apply \
  --state-token <token_z_dry_run> \
  --confirmation "URUCHOM PILOT DPD <numer_zlecenia/rok> <numer_listu>"
```

Skrypt odrzuca zlecenie spoza Archiwum, przesyłkę inną niż produkcyjna DPD,
rekord objęty już automatyczną synchronizacją, brak pełnej sekwencji odbioru i
doręczenia, niezgodność numeru listu oraz każdą zmianę stanu po dry-run. Operacja
dotyczy dokładnie jednego powiązania `zlecenie + list`, również gdy ten sam list
obsługuje wspólną paczkę.

Raport zapisu zwraca `pilot_run_id`. Rollback wymaga tego identyfikatora oraz
osobnej frazy potwierdzającej i przywraca tylko pola zmienione przez wskazany
przebieg. Jeżeli którekolwiek z tych pól zostało później zmienione ręcznie,
rollback jest blokowany:

```bash
python scripts/shipping_dpd_milestone_pilot.py \
  --order <numer_zlecenia/rok> \
  --waybill <numer_listu> \
  --rollback <pilot_run_id> \
  --confirmation "WYCOFAJ PILOT DPD <pilot_run_id>"
```

Raporty są zapisywane w ignorowanym przez Git katalogu
`runtime/shipping_pilots/`. Dziennik `shipping_event` zachowuje zarówno zapis,
jak i ewentualny rollback; wpisów audytowych nie należy usuwać.

## Oddzielny audyt testów administracyjnych

Po zakończeniu pilota Shipping należy przeprowadzić osobny audyt ośmiu testów
`tests/test_admin_backend.py`, które nie dotyczą obsługi przesyłek. Audyt ma
rozróżnić błędy konfiguracji środowiska testowego od regresji kodu, uzupełnić
brakujące tabele SQLite używane przez testy oraz ponownie uruchomić pełny zestaw
bez łączenia ewentualnych poprawek z wydaniem Shipping.

## Wydanie 2: Adresy.app

### Konfiguracja testowa

```dotenv
SHIPPING_GEOCODER_ENABLED=true
ADDRESY_APP_API_URL=https://api.adresy.app/api/v1
ADDRESY_APP_API_KEY=
ADDRESY_APP_TIMEOUT_SECONDS=5
ADDRESY_APP_MIN_SCORE=0.85
```

Środowisko testowe może skorzystać z limitu bez klucza. Produkcja wymaga osobnego klucza serwerowego.

### Test odbiorczy

1. Otwórz jasny i ciemny widok Shipping i potwierdź obecność przycisku „Sprawdź adres”.
2. Sprawdź poprawny pełny adres, adres bez kodu, adres bez miasta, literówkę oraz brak wyniku.
3. Potwierdź zachowanie numeru lokalu w zapisie `budynek/lokal`.
4. Potwierdź automatyczne uzupełnienie wyłącznie pustych pól.
5. Dla różnej niepustej ulicy, kodu albo miasta potwierdź wymagane okno zgody przed zastąpieniem.
6. Potwierdź, że firma, kontakt, telefon i e-mail nie zmieniają się po użyciu kandydata.
7. Sprawdź odpowiedzi błędu po timeoutcie i limicie HTTP `429`.
8. Skontroluj dziennik audytu: zawiera tylko wynik i liczbę kandydatów, bez treści adresu oraz klucza API.

Produkcję uruchamia się po osobnym backupie i kontroli pozostałych modułów przez ustawienie `SHIPPING_GEOCODER_ENABLED=true` oraz klucza. Rollback wymaga wyłącznie wyłączenia flagi i restartu `CTIP-Web`.

## Kontrole wspólne

Po każdym wydaniu sprawdź `/health`, `/shipping`, `/shipping/legacy`, `/operator`, `/genform`, `/flow`, `/device`, publiczne formularze, stabilność usług SMS i e-mail oraz świeżość backupów. Nie wykonuj szerokiego restartu ani migracji Firebirda.
