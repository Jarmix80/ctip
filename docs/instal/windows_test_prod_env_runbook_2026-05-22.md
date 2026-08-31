# Runbook wyrownania konfiguracji Windows TEST/PROD do modelu `.env` (2026-05-22)

> **Dokument archiwalny.** Opisuje dawną zmianę modelu konfiguracji. Kolejne wdrożenia wykonuje wyłącznie `scripts/deploy_windows_prod.py` według `docs/instal/windows_release_deployment.md`.

## Cel
Dokument opisuje bezpieczne wdrozenie na instalacjach Windows CTIP po zmianie modelu konfiguracji:
- dane polaczeniowe i sekrety sa utrzymywane w `.env`,
- panel administratora pokazuje sekcje polaczeniowe tylko do odczytu,
- `CollectorService` uruchamia `collector_full.py` z pliku wskazanego przez `collector_service_config.json -> env_file`,
- konfiguracja backupu jest rozdzielona na czesc integracyjna z `.env` i czesc operacyjna z `ctip.admin_setting`.

Zakres runbooka:
- instalacja testowa Windows (`TEST-WIN`), jezeli istnieje,
- instalacja produkcyjna Windows (`PROD-WIN`),
- backup, preflight, wdrozenie, weryfikacja i rollback.

Runbook nie obejmuje:
- czyszczenia rekordow `admin_setting` dla starych namespace'ow,
- migracji Alembic,
- zmian danych biznesowych w PostgreSQL i Firebird,
- live testow SMS/e-mail bez osobnej zgody.

## Definicje srodowisk
- `TEST-WIN`: osobna instalacja Windows CTIP, odseparowana od produkcji.
- `PROD-WIN`: aktywna instalacja produkcyjna Windows pod `D:\CTIP`.
- `LOCAL-WSL`: lokalne srodowisko developerskie z `.env.test`, `ctip_test`, mock CTIP, lokalnym Firebird i `SMS_TEST_MODE=true`.

Jesli nie istnieje osobna instalacja `TEST-WIN`, jako bramke kodowa nalezy wykonac co najmniej:
- `pre-commit run --all-files`,
- testy lokalne na `LOCAL-WSL`,
- smoke lokalny backendu i UI,
- dopiero potem przejsc do etapu `PROD-WIN`.

## Zasady bezpieczenstwa
1. W tej fali nie wykonuj `alembic upgrade`, `alembic downgrade`, recznych `DELETE/UPDATE` w bazach ani czyszczenia `admin_setting`.
2. Nie wykonuj `git reset --hard`, `git checkout --`, recznego usuwania logow, katalogow lub backupow.
3. Nie wysylaj realnych SMS-ow ani e-maili bez osobnej zgody.
4. Nie usuwaj globalnych zmiennych maszynowych `PG*`, `PBX*` ani wpisow NSSM bez osobnej zgody.
5. Wszystkie operacje ponizej oznaczone jako `STOP` wymagaja osobnego potwierdzenia uzytkownika przed wykonaniem.

## Bramki potwierdzen
### G0. Start procedury
`STOP`

Przed rozpoczeciem:
- potwierdz docelowy commit,
- potwierdz, czy wykonujemy tez `TEST-WIN`, czy tylko `PROD-WIN`,
- potwierdz, czy po wdrozeniu wolno wykonac testy live SMS/e-mail.

### G1. Backup ratunkowy
`STOP`

Wymagane przed:
- uruchomieniem `backup_prod_databases.ps1`,
- kopiowaniem `.env`,
- eksportem konfiguracji uslug i logow do `D:\backup_temp`.

### G2. Modyfikacja konfiguracji
`STOP`

Wymagane przed:
- zmiana `.env`,
- zmiana `collector_service_config.json`,
- zmianami NSSM / rejestru uslug / `AppEnvironmentExtra`.

### G3. Restart uslug testowych
`STOP`

Wymagane przed restartem jakiejkolwiek uslugi na `TEST-WIN`.

### G4. Restart uslug produkcyjnych
`STOP`

Wymagane przed restartem jakiejkolwiek uslugi na `PROD-WIN`.

### G5. Operacje na danych
`STOP`

Wymagane przed:
- migracjami Alembic,
- reczna korekta danych,
- odtworzeniem backupu PostgreSQL lub Firebird.

### G6. Testy live
`STOP`

Wymagane przed:
- `POST /admin/sms/test` na realny numer,
- `POST /admin/email/test` na realny adres,
- uruchomieniem procesow, ktore tworza lub zmieniaja dane biznesowe poza testem odczytowym.

## Artefakty, ktore musza powstac przed zmiana
Katalog roboczy backupu:

```powershell
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupRoot = "D:\backup_temp\ctip_env_unification_$Stamp"
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
New-Item -ItemType Directory -Force -Path "$BackupRoot\01_manifest","$BackupRoot\02_databases","$BackupRoot\03_env","$BackupRoot\04_services","$BackupRoot\05_logs","$BackupRoot\06_repo","$BackupRoot\07_restore_check" | Out-Null
```

Do zebrania:
- kopia `D:\CTIP\.env`,
- `git branch --show-current`, `git rev-parse HEAD`, `git status -sb`,
- lista plikow niesledzonych,
- `Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic`,
- `sc qc CollectorService`, `sc qc CTIP-Web`, `sc qc CTIP-SMS`, `sc qc CTIP-FormsPublic`,
- eksport parametrow NSSM (`Application`, `AppDirectory`, `AppParameters`, `AppStdout`, `AppStderr`, `AppEnvironmentExtra`),
- eksport kluczy rejestru uslug do `.reg`,
- `.\.venv\Scripts\python.exe -m pip freeze`,
- `alembic current` i `alembic heads` z aktywnym `.env`,
- `Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing`,
- ostatnie logi z `logs\collector`, `logs\web`, `logs\sms`, `logs\forms_public`.

## Krok 1. Backup ratunkowy
Uruchamiaj dopiero po bramce `G1`.

### 1.1. Backup baz
```powershell
cd D:\CTIP
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\windows\backup_prod_databases.ps1 `
  -InstallDir D:\CTIP `
  -BackupRoot D:\backup_temp `
  -GbakPath "C:\Program Files\Firebird\Firebird_2_5\bin\gbak.exe" `
  -FailOnPgGlobalsError
```

Uwagi:
- dla `TEST-WIN`, jesli baza to test i skrypt blokuje `PGDATABASE=ctip_test`, dodaj jawnie `-AllowTestDatabase`,
- backup skryptowy tworzy dump PostgreSQL i backup Firebird; nie zastępuje kopii `.env` i eksportu uslug.

### 1.2. Kopia konfiguracji i stanu uslug
```powershell
Copy-Item D:\CTIP\.env "$BackupRoot\03_env\.env" -Force
git -C D:\CTIP branch --show-current | Set-Content "$BackupRoot\01_manifest\git_branch.txt"
git -C D:\CTIP rev-parse HEAD | Set-Content "$BackupRoot\01_manifest\git_head.txt"
git -C D:\CTIP status -sb | Set-Content "$BackupRoot\01_manifest\git_status.txt"
git -C D:\CTIP ls-files --others --exclude-standard | Set-Content "$BackupRoot\01_manifest\git_untracked.txt"

Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic | Format-Table -AutoSize | Out-String | Set-Content "$BackupRoot\04_services\services_status.txt"
sc.exe qc CollectorService | Set-Content "$BackupRoot\04_services\collector_sc_qc.txt"
sc.exe qc CTIP-Web | Set-Content "$BackupRoot\04_services\web_sc_qc.txt"
sc.exe qc CTIP-SMS | Set-Content "$BackupRoot\04_services\sms_sc_qc.txt"
sc.exe qc CTIP-FormsPublic | Set-Content "$BackupRoot\04_services\forms_sc_qc.txt"

reg export "HKLM\SYSTEM\CurrentControlSet\Services\CollectorService" "$BackupRoot\04_services\collector_service.reg" /y
reg export "HKLM\SYSTEM\CurrentControlSet\Services\CTIP-Web" "$BackupRoot\04_services\ctip_web.reg" /y
reg export "HKLM\SYSTEM\CurrentControlSet\Services\CTIP-SMS" "$BackupRoot\04_services\ctip_sms.reg" /y
reg export "HKLM\SYSTEM\CurrentControlSet\Services\CTIP-FormsPublic" "$BackupRoot\04_services\ctip_forms_public.reg" /y
```

### 1.3. Snapshot logow
```powershell
Copy-Item D:\CTIP\logs\collector "$BackupRoot\05_logs\collector" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item D:\CTIP\logs\web "$BackupRoot\05_logs\web" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item D:\CTIP\logs\sms "$BackupRoot\05_logs\sms" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item D:\CTIP\logs\forms_public "$BackupRoot\05_logs\forms_public" -Recurse -Force -ErrorAction SilentlyContinue
```

### 1.4. Minimalna walidacja backupu
```powershell
Get-ChildItem D:\backup_temp -Recurse | Get-FileHash | Export-Csv "$BackupRoot\01_manifest\hashes.csv" -NoTypeInformation
```

Opcjonalnie, jesli na serwerze jest `pg_restore.exe`, dodaj:
```powershell
pg_restore.exe -l "<sciezka_do_najnowszego_dumpa.dump>" | Set-Content "$BackupRoot\07_restore_check\pg_restore_list.txt"
```

## Krok 2. Preflight odczytowy
Ten krok jest bezpieczny i nie wymaga restartu.

### 2.1. Sprawdzenie stanu aplikacji
```powershell
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic
```

### 2.2. Sprawdzenie zrodla konfiguracji
Oczekiwany stan po wdrozeniu:
- `GET /admin/config/database` zwraca `source=env`, `editable=false`,
- `GET /admin/config/firebird` zwraca `source=env`, `editable=false`,
- `GET /admin/config/google-sheets` zwraca `source=env`, `editable=false`,
- `GET /admin/config/ctip` zwraca `source=env`, `editable=false`,
- `GET /admin/config/sms` zwraca `source=env`, `editable=false`,
- `GET /admin/config/email` zwraca `source=env`, `editable=false`,
- `GET /admin/backup/config` zwraca `integration_source=env`, `integration_editable=false`.

### 2.3. Sprawdzenie kolektora
Zweryfikuj:
- obecność `collector_service_config.json`,
- wpis `env_file` wskazujacy na `D:\CTIP\.env`,
- w logu startowym kolektora poprawny `PBX_HOST:PBX_PORT`.

## Krok 3. Wdrozenie `TEST-WIN`
Wykonuj tylko jesli istnieje osobna instalacja testowa Windows. W przeciwnym razie przejdz do `Krok 4`.

### 3.1. Aktualizacja kodu
Uruchamiaj dopiero po bramkach `G2` i `G3`.

Rekomendowany wariant:
```powershell
cd D:\CTIP
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\windows\update_ctip.ps1 `
  -InstallDir D:\CTIP `
  -GitRemote origin `
  -GitBranch main `
  -ServiceNames "CollectorService","CTIP-Web","CTIP-SMS","CTIP-FormsPublic"
```

Jesli test ma pozostac na innym branchu technicznym, zapisz ten fakt w manifeście i nie wykonuj `checkout` bez osobnej decyzji.

### 3.2. Weryfikacja po aktualizacji
1. `pre-commit run --all-files` musi przejsc.
2. `python -m unittest discover -s tests` uruchamiane przez `update_ctip.ps1` musi przejsc albo miec jawnie udokumentowany wyjatek.
3. `GET /health` musi zwrocic `200`.
4. Panel `/admin` musi byc dostepny.
5. Sekcje polaczeniowe maja byc read-only.
6. `CollectorService` ma startowac z `env_file` i prawidlowym `PBX`.

### 3.3. Co sprawdzic recznie
- `collector_stderr.log`: brak prob laczenia do `127.0.0.1:5525`, jezeli test ma inny `PBX_HOST`,
- `web_stderr.log`: brak bledow konfiguracji Pydantic,
- `sms_stdout.log` / `sms_stderr.log`: brak bledow odczytu `.env`,
- `/admin/backup/config`: zapis tylko parametrow operacyjnych.

## Krok 4. Wdrozenie `PROD-WIN`
Wykonuj dopiero po pozytywnym wyniku `TEST-WIN` albo `LOCAL-WSL`.

### 4.1. Modyfikacja `.env`
Uruchamiaj dopiero po bramce `G2`.

Zasada:
- nie kopiuj sekretow z bazy do repo,
- nie kasuj jeszcze historycznych wpisow `admin_setting`,
- przed zapisem przygotuj diff starego i nowego `.env`.

Minimalne pola krytyczne do sprawdzenia:
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE`,
- `PBX_HOST`, `PBX_PORT`, `PBX_PIN`,
- `FB_MODE`, `FB_HOST`, `FB_PORT`, `FB_DATABASE`, `FB_USER`, `FB_PASSWORD`, `FB_ALLOW_WRITES`,
- `EMAIL_*`,
- `SMS_*`,
- `GOOGLE_SHEETS_ENABLED`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SHEETS_WORKFLOW_DEVICES_SHEET`,
- `BACKUP_DEFAULT_LOCAL_DIR`, `BACKUP_PRODUCTION_HOST`, `OFFICE365_*`, `OPTIMA_*`.

### 4.2. Aktualizacja kodu
Uruchamiaj dopiero po bramce `G4`.

```powershell
cd D:\CTIP
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\windows\update_ctip.ps1 `
  -InstallDir D:\CTIP `
  -GitRemote origin `
  -GitBranch main `
  -ServiceNames "CollectorService","CTIP-Web","CTIP-SMS","CTIP-FormsPublic"
```

### 4.3. Kontrola `CollectorService`
Po aktualizacji sprawdz:
```powershell
Get-Content -Tail 120 D:\CTIP\logs\collector\collector_stderr.log
Get-Content D:\CTIP\collector_service_config.json
```

Oczekiwany stan:
- `env_file` wskazuje `D:\CTIP\.env`,
- log startu nie pokazuje fallbacku do `127.0.0.1:5525`, jezeli produkcyjny `PBX_HOST` jest inny,
- brak petli restartow wynikajacych z braku `PBX_*`.

### 4.4. Kontrola panelu
Po restarcie:
- `GET /health` -> `200`,
- `/admin/config/*` pokazuje `source=env`, `editable=false`,
- `/admin/backup/config` pokazuje `integration_source=env`, `integration_editable=false`,
- testy `POST /admin/firebird/test`, `POST /admin/google-sheets/test`, `POST /admin/sms/test` i `POST /admin/email/test` wykonuj tylko zgodnie z bramka `G6`.

## Krok 5. Weryfikacja po wdrozeniu
### 5.1. Weryfikacja bezpieczna
```powershell
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic
Get-Content -Tail 80 D:\CTIP\logs\web\web_stderr.log
Get-Content -Tail 80 D:\CTIP\logs\sms\sms_stderr.log
Get-Content -Tail 80 D:\CTIP\logs\collector\collector_stderr.log
```

Sprawdz recznie:
- panel laduje sie poprawnie,
- sekcje `PostgreSQL`, `Firebird`, `Google Sheets`, `CTIP`, `SerwerSMS`, `E-mail` maja komunikat o zarzadzaniu w `.env`,
- `kp_repair` nadal pozwala zapisac `email_lookback_months`,
- backup pozwala zapisac harmonogram i retencje, ale blokuje pola integracyjne.

### 5.2. Weryfikacja procesowa
- czy `CollectorService` zapisuje nowe zdarzenia CTIP,
- czy `sms_sender.py` nie traci polaczenia z PostgreSQL,
- czy scheduler backupu dziala z obecnym `.env`,
- czy `/admin/status/*` nie pokazuje nowych bledow po restarcie.

### 5.3. Testy live
Uruchamiaj dopiero po bramce `G6`.

Minimalny zestaw:
- `1` testowy e-mail na adres techniczny,
- `1` testowy SMS na numer techniczny,
- potwierdzenie dostarczenia,
- zapis wyniku w raporcie wdrozeniowym.

## Krok 6. Rollback
### 6.1. Rollback konfiguracji i kodu
Jesli problem dotyczy tylko konfiguracji lub startu uslug:
1. zatrzymaj uslugi po osobnym potwierdzeniu (`G4`),
2. przywroc `D:\CTIP\.env` z backupu,
3. przywroc `collector_service_config.json` z backupu, jesli byl zmieniany recznie,
4. przywroc poprzedni commit lub tag Git,
5. uruchom uslugi,
6. sprawdz `GET /health` i logi.

### 6.2. Rollback danych
`STOP`

Wykonuj tylko po bramce `G5` i tylko jesli awaria wynika z uszkodzenia danych, a nie z konfiguracji.

Kolejnosc:
1. zatrzymaj uslugi,
2. odtworz PostgreSQL z dumpa,
3. odtworz Firebird z `.fbk`,
4. uruchom uslugi,
5. wykonaj check `/health` i logow.

W tej konkretnej fali rollback danych nie powinien byc potrzebny, bo zmiana dotyczy przede wszystkim kodu, `.env` i sposobu odczytu konfiguracji.

## Krok 7. Czego nie robic w tej fali
- nie usuwaj starych namespace'ow `database.*`, `firebird.*`, `firebird_vmaintenance.*`, `ctip.*`, `sms.*`, `email.*`, `google_sheets.*` z `admin_setting`,
- nie usuwaj globalnych `PG*` / `PBX*` z systemu bez oddzielnego audytu,
- nie wykonuj migracji Alembic tylko po to, aby "zsynchronizowac" srodowiska,
- nie traktuj testow live jako czesci automatycznego smoke testu,
- nie mieszaj prac konfiguracyjnych z rozwojem funkcjonalnym w jednym wdrozeniu.

## Raport po wdrozeniu
Po kazdym wdrozeniu zapisz w jednym pliku:
- data i godzina,
- host,
- branch i commit,
- wynik backupu,
- wynik `/health`,
- stan uslug,
- wynik kontroli `CollectorService`,
- wynik testow read-only `/admin/config/*`,
- wynik testow live (jesli byly zatwierdzone),
- decyzja: `OK`, `OK z odchyleniem`, `ROLLBACK`.
