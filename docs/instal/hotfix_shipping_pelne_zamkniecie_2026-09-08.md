# Hotfix pełnego zamknięcia zleceń Shipping – 2026-09-08

## Cel

Hotfix usuwa rozbieżność, w której CTIP oznaczał przesyłkę jako zamkniętą i tworzył dokument RW, WZ albo FV, ale w `ZLECENIE` zapisywał wyłącznie `STAN='Z'`. Menadżer Serwisu prezentował wtedy zlecenie jako „zrealizowane”, ponieważ brakowało `DATA_Z` i dopisku operatora zamykającego.

## Diagnoza produkcyjna

Kontrola tylko do odczytu wykonana 8 września 2026 r. wykazała:

- `47` przesyłek ze stanem `closed` w PostgreSQL CTIP,
- `43` odpowiadające rekordy nadal istniejące w produkcyjnym Firebirdzie,
- wszystkie `43` rekordy miały `STAN='Z'`, dokument i numer przesyłki, ale puste `DATA_Z`,
- cztery starsze rekordy Firebirda o identyfikatorach `83493`, `83494`, `83495` i `83553` nie istnieją i nie są odtwarzane przez hotfix,
- ostatnie zlecenie `18691/2026` miało dokument `RW / 2213 / 2026` oraz numer `1050613059113U`, ale również puste `DATA_Z`.

## Zasada działania

Nowa finalizacja zapisuje i weryfikuje w jednej transakcji:

- właściwy identyfikator oraz numer dokumentu,
- `PRZESYLKA`,
- `STAN='Z'`,
- `DATA_Z` równą dacie zamknięcia,
- dopisek `Zamknął :<operator>` w `OPERATOR`.

Pola `EDITCNT`, `EDITDATE`, `EDITTIME` oraz wpisy `SYNCHRO` pozostają obsługiwane przez natywny trigger Firebirda. CTIP nie ustawia ich ręcznie.

Bieżący odczyt Shipping opisuje `STAN='Z'` jako „zamknięte” wyłącznie wtedy, gdy rekord ma również `DATA_Z` i znacznik `Zamknął :` w `OPERATOR`. Niepełny rekord `Z` pozostaje opisany jako „zrealizowane”, dzięki czemu operator widzi różnicę między realizacją a pełnym zamknięciem.

## Naprawa historyczna

Skrypt `scripts/repair_shipping_ms_closure.py` pobiera datę i operatora z niezmiennego archiwum przesyłki CTIP. Przed zapisem porównuje numer zlecenia, rok, dokument i numer przesyłki z Firebirdem. Rekord już poprawny jest pomijany, brakujący raportowany, a każda rozbieżność dokumentu lub tożsamości przerywa całą transakcję.

Dry-run na serwerze produkcyjnym:

```powershell
$env:CTIP_ENV_FILE = "D:\CTIP\.env"
& D:\CTIP\.venv\Scripts\python.exe D:\CTIP\scripts\repair_shipping_ms_closure.py
```

Zapis po sprawdzeniu raportu i wykonaniu pełnego backupu PostgreSQL oraz Firebird:

```powershell
$env:CTIP_ENV_FILE = "D:\CTIP\.env"
& D:\CTIP\.venv\Scripts\python.exe D:\CTIP\scripts\repair_shipping_ms_closure.py `
  --apply `
  --confirmation "NAPRAW ZAMKNIECIA SHIPPING"
```

Raport JSON trafia do ignorowanego katalogu `runtime/repairs`. Po zapisie każdy naprawiony rekord musi mieć datę zgodną z `shipping_shipment.closed_at` oraz pojedynczy dopisek operatora. Dokumenty, pozycje magazynowe i numery przesyłek nie są zmieniane.

## Przerwane zamknięcie dnia

Jeżeli żądanie zamknięcia dnia nie dotarło do API, przesyłki pozostają w `label_ready`, a w tabeli `shipping_day_close` nie powstaje rekord. Przed ponowieniem należy porównać log dostępu `CTIP-Web`, stan każdej przesyłki, dokumenty zlecenia i ilości w `MAGAZYN`.

Zlecenie ręcznie zamknięte w MS przed wygenerowaniem dokumentów nie może zostać przepuszczone przez standardowe API. Skrypt `scripts/repair_shipping_day_close.py` udostępnia osobny tryb naprawczy tylko dla jawnie wskazanych rekordów, które mają zgodny numer DPD i nie mają żadnego `RW`, `WZ` ani `FV`. Najpierw należy wykonać dry-run:

```powershell
$env:CTIP_ENV_FILE = "D:\CTIP\.env"
& D:\CTIP\.venv\Scripts\python.exe D:\CTIP\scripts\repair_shipping_day_close.py `
  --business-date 2026-09-08 `
  --user-id 16 `
  --recover-preclosed-order-table-id 83627
```

Po pełnym backupie PostgreSQL i Firebirda zapis wymaga dodatkowego potwierdzenia:

```powershell
$env:CTIP_ENV_FILE = "D:\CTIP\.env"
& D:\CTIP\.venv\Scripts\python.exe D:\CTIP\scripts\repair_shipping_day_close.py `
  --business-date 2026-09-08 `
  --user-id 16 `
  --recover-preclosed-order-table-id 83627 `
  --apply `
  --confirm "NAPRAW ZAMKNIECIE DNIA SHIPPING"
```

Po operacji trzeba ponownie zweryfikować wszystkie zlecenia dnia: `STAN`, `DATA_Z`, operatora, zgodność `ID_RW`/`ID_WZ`/`ID_FAKTURA`, nagłówki i pozycje dokumentów, wartości `POBRANO` oraz spadek `MAGAZYN.ILOSC` zgodny z ilościami zapisanymi w paczkach.

## Rollback

Naprawa historyczna zmienia tylko `ZLECENIE.DATA_Z` i `ZLECENIE.OPERATOR`, ale uruchamia standardowy trigger aktualizacji zlecenia. W razie konieczności wycofania należy użyć pełnego backupu Firebirda wykonanego bezpośrednio przed wdrożeniem. Nie należy wykonywać zbiorczego ręcznego `UPDATE` bez porównania raportu dry-run.
