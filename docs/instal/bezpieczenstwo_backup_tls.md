# Zabezpieczenie API, backup PostgreSQL i nadzór TLS

## Zakres

Dokument opisuje zabezpieczenia wdrażane dla produkcyjnego CTIP:

- ograniczenie bezpośredniego dostępu do aplikacji na porcie `8000` do hosta lokalnego i sieci LAN;
- usunięcie z głównego routera niezabezpieczonych tras historycznych `/calls`, `/contacts` i `/sms/*`;
- skróty SHA-256 tokenów sesji w bazie oraz limit nieudanych prób logowania;
- obowiązkowy logiczny backup PostgreSQL przez `pg_dump` wraz z walidacją `pg_restore --list`;
- pojedynczy upload kompletnego archiwum do `BackupKP/CTIP` w Office 365;
- kontrolę ważności publicznego certyfikatu TLS.

## Konfiguracja bezpieczeństwa

W produkcyjnym `.env` należy utrzymywać co najmniej:

```dotenv
PANEL_ALLOWED_NETWORKS=127.0.0.0/8,::1/128,192.168.0.0/24
LOGIN_FAILURE_LIMIT=5
LOGIN_FAILURE_WINDOW_MINUTES=15
AUTH_COOKIE_SECURE=true
```

Po wdrożeniu skrótów tokenów wszystkie sesje utworzone przez starszą wersję aplikacji przestają być ważne. Użytkownicy muszą zalogować się ponownie. Token przekazywany klientowi pozostaje losowy, ale w tabeli `ctip.admin_session` jest przechowywany wyłącznie jego skrót SHA-256.

Trasy operatora pozostają dostępne pod `/operator/api/*` i wymagają aktywnej sesji oraz dostępu do sekcji `operator`. Historyczny nagłówek `X-User-Id` nie jest źródłem tożsamości.

## Backup PostgreSQL

W produkcyjnym `.env` należy wskazać narzędzia PostgreSQL, jeżeli nie są dostępne w `PATH`:

```dotenv
PG_DUMP_PATH=C:\Program Files\PostgreSQL\17\bin\pg_dump.exe
PG_RESTORE_PATH=C:\Program Files\PostgreSQL\17\bin\pg_restore.exe
BACKUP_PG_DUMP_TIMEOUT_SECONDS=900
OFFICE365_FOLDER_CTIP=BackupKP/CTIP
```

Hasło PostgreSQL nie jest dodawane do argumentów procesu. Aplikacja przekazuje je do procesu potomnego przez `PGPASSWORD`. Backup bazy ma format niestandardowy PostgreSQL i trafia do archiwum jako `postgresql/ctip.dump`. Brak `pg_dump`, błąd procesu, pusty plik lub nieudane `pg_restore --list` przerywa tworzenie kopii i daje status błędu.

Kompletne archiwum oraz plik `.sha256` są wysyłane tylko raz do `BackupKP/CTIP`. Duże pliki korzystają z fragmentowych sesji Microsoft Graph. Po udanym uploadzie stosowana jest retencja chmurowa; retencja lokalna jest stosowana po utworzeniu poprawnego archiwum.

Status `SUCCESS` oznacza brak pominiętych, żądanych składników i brak błędów retencji lub chmury. Status `PARTIAL` oznacza, że lokalne archiwum istnieje, ale pominięto składnik albo nie zakończono operacji dodatkowej. Niezaimplementowany dump SQL Server Optimy jest jawnie raportowany jako pominięty składnik.

## Próba odtworzenia

Próbę odtworzenia należy wykonywać wyłącznie do odseparowanej bazy testowej. Przykład po wyjęciu `postgresql/ctip.dump` z archiwum:

```powershell
createdb.exe --host 127.0.0.1 --port 5433 --username postgres ctip_restore_test
pg_restore.exe --host 127.0.0.1 --port 5433 --username postgres --dbname ctip_restore_test --no-owner --no-privileges .\ctip.dump
psql.exe --host 127.0.0.1 --port 5433 --username postgres --dbname ctip_restore_test -c "SELECT count(*) FROM ctip.admin_user;"
```

Po weryfikacji bazę testową można usunąć wyłącznie w ramach zatwierdzonej operacji administracyjnej.

## Nadzór TLS

Skrypt `scripts/windows/check_public_tls.ps1` wykonuje pełny handshake TLS dla `form.ksero-partner.com.pl`, sprawdza nazwę hosta, łańcuch zaufania i termin ważności certyfikatu. Zwraca kod `1` przy ostrzeżeniu i `2` przy stanie krytycznym lub błędzie połączenia. Ostrzeżenia trafiają do dziennika `Application` ze źródłem `CTIP-TLS`.

Przykładowa rejestracja codziennego zadania na serwerze Windows:

```powershell
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument '-NoProfile -ExecutionPolicy Bypass -File "D:\CTIP\scripts\windows\check_public_tls.ps1"'
$trigger = New-ScheduledTaskTrigger -Daily -At 07:00
Register-ScheduledTask -TaskName "CTIP-TLS-Monitor" -Action $action -Trigger $trigger -RunLevel Highest -User "SYSTEM"
```

Kontrola ręczna:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\CTIP\scripts\windows\check_public_tls.ps1
```
