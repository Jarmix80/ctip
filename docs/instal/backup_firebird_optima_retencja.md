# Backup Firebird i SQL Optima oraz retencja czasowa

## Cel

Moduł kopii zapasowych CTIP tworzy zweryfikowane, niezależne artefakty dla:

- PostgreSQL CTIP i plików aplikacji,
- produkcyjnej bazy Firebird Menadżera Serwisu,
- trzech produkcyjnych baz Microsoft SQL Server systemu Optima.

Kopie lokalne są utrzymywane przez 21 dni, a kopie w Office 365 przez 14 dni. Mechanizm retencji usuwa wyłącznie rozpoznane zestawy plików. Pliki obce, katalogi i notatki operatora pozostają nietknięte.

## Harmonogram produkcyjny

| Godzina | Zakres lokalny | Office 365 |
|---|---|---|
| `06:00` | CTIP/PostgreSQL oraz Firebird produkcyjny | bez wysyłki |
| `20:00` | CTIP/PostgreSQL, Firebird produkcyjny oraz trzy bazy Optimy | wysyłka wszystkich kompletnych zestawów |
| ręcznie | pełny zakres łącznie z Optimą | wysyłka wszystkich kompletnych zestawów |

Kopia Firebird testowego Menadżera Serwisu pozostaje wyłączona, ponieważ wskazana wcześniej ścieżka prowadziła do tej samej fizycznej bazy co produkcja. Folder Office 365 `BackupKP/Menadzer_Serwisu/test` nie otrzymuje nowych plików. Podczas porządkowania zachowywany jest jego najnowszy kompletny historyczny zestaw.

Proces korzysta z jednej blokady wykonawczej. Zadanie ręczne i harmonogram nie mogą wykonywać ciężkich kopii równolegle.

## Artefakty

### CTIP i PostgreSQL

Główne archiwum pozostaje w katalogu roboczym aplikacji `backups/`, na produkcji `D:\CTIP\backups`:

- `backup_YYYYMMDD_HHMMSS_<etykieta>.tar.gz`,
- `backup_YYYYMMDD_HHMMSS_<etykieta>.tar.gz.sha256`.

Archiwum zawiera dump PostgreSQL zweryfikowany przez `pg_restore --list`, pliki aplikacji oraz małe manifesty zewnętrznych komponentów. Duże pliki `.fbk` i `.bak` nie są dublowane wewnątrz archiwum CTIP.

### Firebird Menadżera Serwisu

Katalog lokalny: `D:\Backup_CTIP_MS_optima\Menadzer_Serwisu\prod`.

- `ctip_firebird_prod_YYYYMMDD_HHMMSS.fbk`,
- `ctip_firebird_prod_YYYYMMDD_HHMMSS.fbk.sha256`,
- `ctip_firebird_prod_YYYYMMDD_HHMMSS_manifest.json`.

`gbak -b -g` tworzy logiczną kopię bez blokowania garbage collection. Następnie `gbak -c` odtwarza ją do pliku tymczasowego. Zestaw jest publikowany dopiero po poprawnym odtworzeniu i obliczeniu SHA-256. Hasło Firebird jest przekazywane wyłącznie przez zmienną procesu `ISC_PASSWORD`.

### SQL Optima

Katalog lokalny: `D:\Backup_CTIP_MS_optima\Optima`.

Każdy wieczorny przebieg tworzy osobne pliki:

- `ctip_optima_YYYYMMDD_HHMMSS_CDN_IT_Partner.bak`,
- `ctip_optima_YYYYMMDD_HHMMSS_CDN_Ksero_Partner1.bak`,
- `ctip_optima_YYYYMMDD_HHMMSS_CDN_KNF_Ksero_Partner.bak`,
- sumę `.sha256` dla każdego pliku `.bak`,
- wspólny `ctip_optima_YYYYMMDD_HHMMSS_manifest.json`.

Każda baza musi istnieć i mieć stan `ONLINE`. Backup używa `COPY_ONLY`, `INIT` i `CHECKSUM`, a następnie `RESTORE VERIFYONLY WITH CHECKSUM`. Opcja `COMPRESSION` nie jest używana ze względu na edycję SQL Server Express. Po utworzeniu całego zestawu najmniejsza baza `CDN_IT_Partner` jest odtwarzana pod losową nazwą `CTIP_VERIFY_*`, sprawdzana przez `DBCC CHECKDB ... WITH PHYSICAL_ONLY` i usuwana.

Przy logowaniu Windows `sqlcmd` używa `-E`. Dla trybu SQL hasło jest przekazywane przez `SQLCMDPASSWORD`, nigdy przez parametr `-P`.

## Katalogi Office 365

- CTIP: `BackupKP/CTIP`,
- Firebird produkcyjny: `BackupKP/Menadzer_Serwisu/prod`,
- historyczny Firebird testowy: `BackupKP/Menadzer_Serwisu/test`,
- Optima: `BackupKP/Optima`.

Upload odbywa się zestawami. Błąd chmury nie usuwa poprawnych artefaktów lokalnych i ustawia status przebiegu `PARTIAL`. Retencja danego folderu Office 365 uruchamia się dopiero po poprawnym wieczornym uploadzie całego nowego zestawu do tego folderu.

## Zasady retencji

Retencja grupuje plik główny, sumę kontrolną i manifest na podstawie nazwy przebiegu. Wiek zestawu jest liczony według najnowszej daty modyfikacji jego elementu.

- lokalnie: dokładnie 21 dni,
- Office 365: dokładnie 14 dni,
- zawsze zachowaj najnowszy kompletny zestaw w danym katalogu,
- stary niekompletny, ale rozpoznany zestaw może zostać usunięty,
- nowy niekompletny zestaw jest tylko raportowany,
- nierozpoznane pliki i katalogi nie są usuwane.

Pola `retention_local_copies` i `retention_cloud_copies` pozostają chwilowo w API dla zgodności ze starszym panelem, ale nie sterują już usuwaniem. Aktywne są `retention_local_days=21` oraz `retention_cloud_days=14`.

## API i audyt

Podgląd bez usuwania:

```http
POST /admin/backup/retention/run
Content-Type: application/json

{"dry_run": true}
```

Wykonanie operacji destrukcyjnej wymaga roli administratora, aktywnego trybu produkcyjnego i dokładnej frazy:

```json
{"dry_run": false, "confirm": "USUŃ STARE KOPIE"}
```

Odpowiedź zawiera osobny wynik każdego katalogu: liczbę zarządzanych zestawów, kandydatów, rozmiar możliwy do odzyskania, zachowany najnowszy zestaw, pliki nierozpoznane, błędy oraz faktycznie usunięte pliki. Pełny wynik trafia do audytu jako `backup_retention_dry_run` albo `backup_retention_apply`.

## Konfiguracja produkcyjna `.env`

Wymagane ustawienia narzędzi i baz:

```dotenv
BACKUP_DEFAULT_LOCAL_DIR=D:\Backup_CTIP_MS_optima
FIREBIRD_GBAK_PATH=C:\Program Files\Firebird\Firebird_2_5\bin\gbak.exe
BACKUP_FIREBIRD_TIMEOUT_SECONDS=7200
OPTIMA_SQLCMD_PATH=C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\SQLCMD.EXE
OPTIMA_SQL_SERVER_INSTANCE=SERWER1\OPTIMA
OPTIMA_SQL_AUTH_MODE=windows
OPTIMA_DB_IT_PARTNER=CDN_IT_Partner
OPTIMA_DB_KSERO_PARTNER=CDN_Ksero_Partner1
OPTIMA_DB_CONFIG=CDN_KNF_Ksero_Partner
BACKUP_OPTIMA_TIMEOUT_SECONDS=7200
```

Konta usługi aplikacji i SQL Server muszą mieć dostęp do katalogów `Menadzer_Serwisu\prod`, `Optima`, `.staging` i `.verify`. Na serwerze produkcyjnym usługi działają jako `SYSTEM`, dlatego katalogi powinny być ograniczone do `SYSTEM` i lokalnych administratorów.

## Pierwsze wdrożenie

1. Wykonać backup PostgreSQL przed wdrożeniem i zachować aktualny stan zadań Harmonogramu zadań.
2. Wdrożyć commit z GitHub i uzupełnić wyłącznie brakujące zmienne `.env`.
3. Utworzyć katalogi komponentów oraz nadać uprawnienia `SYSTEM` i `Administrators`.
4. Uruchomić pełny backup ręczny i potwierdzić: trzy `.bak`, jeden `.fbk`, wszystkie SHA-256, manifesty, kontrolny restore oraz upload Office 365.
5. Wykonać `POST /admin/backup/retention/run` najpierw z `dry_run=true`; zapisać wynik JSON jako raport wdrożeniowy.
6. Po akceptacji wykonać retencję z frazą potwierdzającą i zapisać drugi wynik JSON.
7. Wyłączyć stare zadanie godzinowe `Backup BAZAMS`, pozostawiając ostatni plik `D:\automate\BAZAMS_kopia.FDB` do czasu potwierdzenia kolejnego cyklu 06:00 i 20:00.
8. Wyłączyć niesprawne zadanie `Optima KP`. Systemowy Windows Backup VSS pozostawić aktywny jako niezależną warstwę bezpieczeństwa.

Nie usuwać starych kopii `D:\Backup_Optima` przed poprawnym nowym backupem, kontrolnym odtworzeniem i zapisaniem raportu dry-run.

## Kontrola i rollback

Po przebiegach 06:00 i 20:00 sprawdzić audyt panelu, daty plików lokalnych oraz kompletność folderów Office 365. Status `PARTIAL` wymaga zachowania artefaktów lokalnych i analizy pola `upload_error`.

Rollback aplikacji polega na wdrożeniu poprzedniego commita i restarcie usługi `CTIP-Web`. Następnie można ponownie włączyć zadanie `Backup BAZAMS`. Zadania `Optima KP` nie należy włączać bez poprawienia instancji, nazwy pliku i nieobsługiwanej opcji kompresji.
