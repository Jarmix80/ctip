# MSConnector — notatka diagnostyczna dla producenta

## Stan zgłoszenia

Notatkę przygotowano 10 września 2026 roku do ręcznego przekazania producentowi.
Nie została automatycznie wysłana. Nie zmieniano konfiguracji, plików programu,
rejestru ani konta usługi. Nie wykonywano restartu konektora ani serwera.
Użytkownik nie wyraził zgody na zmianę wspólnego profilu SYSTEM.

## Objawy i środowisko

- Program: Menadżer Serwisu Connector dla aplikacji mobilnych, Serwisoft.
- Wersja pliku: `2025.1.15.1`.
- Usługa: `MS_Connector`, uruchomiona automatycznie, konto `LocalSystem`.
- Proces działa i nasłuchuje na skonfigurowanym porcie komunikacyjnym.
- Log startu potwierdza odczyt konfiguracji bazy danych bez błędu.
- Dziennik Windows Application: źródło `MSConnector.exe`, zdarzenie `0`.
- Ponad 1200 błędów w ostatniej dobie podczas pomiaru, zwykle co minutę.
- Przykładowy błąd z 10 września: `'10.09.2026' is not a valid integer value`.
- Starsze wpisy zawierają analogicznie daty `07.09.2026`, `08.09.2026`
  i `09.09.2026`; problem występował przed restartem po aktualizacji systemu.

## Ustalenia dotyczące daty

Profil SYSTEM ma ustawienia `pl-PL`, `sShortDate=dd.MM.yyyy`, `sDate=.`
oraz `iDate=1`. Aktualna instrukcja producenta na stronie 5 zaleca format
`yyyy-MM-dd` i opisuje zależność prawidłowego wyświetlania danych mobilnych
od systemowego formatu daty:
[Instrukcja MS Mobile](https://www.menadzerserwisu.pl/download/android/Instrukcja_obs%C5%82ugi_dla_MS_Mobile.pdf).

Niezgodność formatu jest hipotezą zgodną z treścią wyjątku i zaleceniem producenta.
Nie została potwierdzona próbą zmiany ustawień ani odtworzeniem błędu w kodzie
źródłowym konektora. Sam stan usługi `Running` nie potwierdza wykonania wszystkich
operacji synchronizacji; nie ustalono, które operacje są przerywane przez wyjątek.

## Pytania do producenta

1. Która operacja wersji `2025.1.15.1` konwertuje datę systemową na liczbę całkowitą
   i czy opisany problem jest znany dla formatu `dd.MM.yyyy`?
2. Czy istnieje wspierany parametr formatu daty ograniczony do MSConnector,
   bez zmiany wspólnego profilu SYSTEM i wpływu na inne usługi Windows?
3. Czy dostępna jest poprawiona wersja konektora zgodna z aktualną bazą MS
   i aplikacjami mobilnymi, bez migracji bazy?
4. Jak odczytowo zweryfikować poprawność operacji, która obecnie zgłasza błąd?

## Warunki dalszej naprawy

Preferowane jest rozwiązanie ograniczone do konektora, potwierdzone przez producenta.
Przed zmianą wymagane są kopia konfiguracji i programu, procedura rollbacku oraz
uzgodniony krótki restart wyłącznie tej usługi. Odbiór powinien obejmować brak
nowych błędów przez co najmniej 15 minut aktywnej pracy i odczyt danych w aplikacji
mobilnej. Do tego czasu problem pozostaje otwarty, nie jest oznaczony jako naprawiony.
