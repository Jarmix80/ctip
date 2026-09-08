# Korekta urządzenia w formularzu 70

## Cel

Procedura zamienia w produkcyjnym formularzu `70` uszkodzone urządzenie
`Ricoh IM C300`, numer seryjny `3930PA00796`, na sprawny egzemplarz
`Ricoh IM C300`, numer seryjny `3931P651369`, indeks `KP/5278`.

Operacja zachowuje bez zmian:

- starą proformę Firebird `52/proforma/2026`, ID `64578`, oraz jej PDF;
- urządzenie `Ricoh IM 430`, numer seryjny `3359PA02610`;
- status sprawy `APPROVED_ORDER`, źródło `mailbox` i historię GRENKE;
- numer wniosku GRENKE `173-25799`.

Nowa proforma ma dwie pozycje i łączną wartość `13400,00` netto,
`3082,00` VAT oraz `16482,00` brutto. Cena nowego urządzenia wynosi
`7150,00` netto i `8794,50` brutto.

## Warunki Wstępne

1. Produkcja działa na hoście `192.168.0.8`.
2. Kod został wdrożony mechanizmem `scripts/deploy_windows_prod.py` z dokładnego commita.
3. Na serwerze istnieje `D:\CTIP\.env` z profilem `production` i odblokowanym zapisem Firebird.
4. Pełny backup PostgreSQL i Firebird zakończył się powodzeniem. Katalog backupu musi zawierać co najmniej jeden plik `.dump` i jeden plik `.fbk`.
5. Operator zna katalog backupu zwrócony przez wdrożenie albo `scripts\windows\backup_prod_databases.ps1`.
6. Usługi `CTIP-Web` i `CTIP-FormsPublic` są zatrzymane na czas dry-runu zatwierdzającego i zapisu.

Nie wykonywać ręcznego `git pull`, zmiany gałęzi ani migracji. Korekta nie wymaga zmiany schematu bazy.

## Dry-Run

W PowerShell uruchomionym jako administrator:

```powershell
Set-Location D:\CTIP
$env:CTIP_ENV_FILE = "D:\CTIP\.env"
Stop-Service CTIP-Web,CTIP-FormsPublic -Force
& .\.venv\Scripts\python.exe scripts\replace_form70_device_prod.py
```

Raport musi potwierdzić:

- formularz `70`, sprawę `40` i proformę `52/proforma/2026`;
- bieżące urządzenia `3930PA00796` i `3359PA02610`;
- planowane urządzenie `3931P651369`;
- właścicieli maszyn `7674 -> 1462`, `7712 -> 1462`, `7848 -> 656`;
- trzy zgodne wiersze arkusza;
- brak zajętego planowanego numeru nowej proformy.

Z raportu należy skopiować `state_token` i `required_confirmation`.

## Wykonanie

```powershell
& .\.venv\Scripts\python.exe scripts\replace_form70_device_prod.py `
  --apply `
  --state-token "<STATE_TOKEN_Z_DRY_RUN>" `
  --confirmation "ZAMIEN FORMULARZ 70 3930PA00796 NA 3931P651369" `
  --backup-dir "<KATALOG_PELNEGO_BACKUPU>"
```

Skrypt zapisuje dziennik etapów w
`runtime\form70_device_replacement\form70-device-18353-to-18839-20260908.json`.
Po przerwaniu można uruchomić dokładnie to samo polecenie ponownie. Zapis rozpoznaje
utworzoną proformę i wykonane etapy, dlatego nie tworzy drugiego dokumentu.

## Weryfikacja

Wynik końcowy musi potwierdzić:

1. PostgreSQL wskazuje urządzenia `3931P651369` i `3359PA02610`.
2. Sprawa `40` wskazuje nową proformę, ale jej status, źródło i historia GRENKE są identyczne jak przed korektą.
3. Firebird nadal zawiera proformę `52/proforma/2026` oraz nową proformę z dwiema prawidłowymi pozycjami.
4. `MASZYNA 7674` należy do klienta magazynowego `656`, a `MASZYNA 7848` do klienta `1462`.
5. Stary wiersz arkusza nie zawiera formularza ani proformy i ma blokadę `NIESPRAWNE - SERWIS`.
6. Nowy i pozostawiony wiersz arkusza wskazują formularz `70`, sprawę `40` i nową proformę.
7. Stary PDF ma niezmienioną sumę kontrolną, a nowy PDF istnieje i nie jest pusty.

Po pozytywnej weryfikacji:

```powershell
Start-Service CTIP-Web,CTIP-FormsPublic
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8100/health -UseBasicParsing
```

Następnie należy otworzyć `/genform`, formularz `70`, sprawdzić oba urządzenia oraz pobrać nową proformę.

## Rollback

Rollback wykonuje się wyłącznie przy zatrzymanych usługach. Wymaga identyfikatora `run_id`
z raportu apply i przywraca zapisane pola PostgreSQL, Firebird oraz trzy wiersze arkusza.
Usuwana jest wyłącznie nowa proforma utworzona przez korektę; stara proforma pozostaje nietknięta.

```powershell
& .\.venv\Scripts\python.exe scripts\replace_form70_device_prod.py `
  --rollback "<RUN_ID>" `
  --confirmation "WYCOFAJ ZAMIANE FORMULARZA 70 <RUN_ID>"
```

Po rollbacku należy powtórzyć dry-run oraz weryfikację obu endpointów zdrowia przed uruchomieniem obsługi operatorów.
