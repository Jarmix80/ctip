# Wdrożenie Produkcyjne GENFORM/FLOW (2026-04-29)

## Cel
Wdrożenie zmian GENFORM/FLOW z przyciskiem `Dane zostały wpisane`, konfiguracją produkcyjnej skrzynki GRENKE oraz walidacją arkusza `Zerowki_prod`, z pełnym planem backupu i rollbacku.

## Stan wejściowy (sprawdzone 2026-04-29)
1. `alembic current` dla produkcyjnego `.env` wskazuje rewizję `b3f41e2c1a9d`.
2. `alembic heads` wskazuje `d7a2c9f8e041` (różnica migracji do wykonania).
3. Test arkusza `Zerowki_prod` kończy się `PermissionError` (konto serwisowe nie ma dostępu do skoroszytu lub zakładki).
4. Test skrzynki `MAILBOX_*` działa, ale aktualny adres w środowisku to konto testowe (`umowy-test@...`) i trzeba go przełączyć na produkcyjne `umowy@ksero-partner.com.pl`.

## Krok 1. Backup przed wdrożeniem (obowiązkowo)
1. Zatrzymaj usługi aplikacji (web, collector, sms sender), aby backup był spójny.
2. Zapisz punkt odniesienia Git na serwerze:
```bash
git fetch --all --tags
git rev-parse HEAD
git tag -a pre_genform_flow_2026-04-29 -m "Stan przed wdrozeniem GENFORM/FLOW 2026-04-29"
```
3. Wykonaj dump PostgreSQL:
```bash
mkdir -p backups/prod_2026-04-29
pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
  --format=custom --file "backups/prod_2026-04-29/ctip_prod_pre_genform_flow.dump"
pg_dumpall -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" --globals-only \
  > "backups/prod_2026-04-29/pg_globals_pre_genform_flow.sql"
```
4. Wykonaj backup Firebird (produkcyjny MS):
```bash
gbak -b -user "$FB_USER" -password "$FB_PASSWORD" \
  "$FB_DATABASE" "backups/prod_2026-04-29/firebird_prod_pre_genform_flow.fbk"
```
5. (Opcjonalnie, dodatkowo) uruchom backup panelowy CTIP:
```bash
curl -X POST "http://127.0.0.1:8000/admin/backup/run" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Session: <TOKEN_ADMINA>" \
  -d '{"label":"pre_genform_flow_2026-04-29","compress":true,"dry_run":false}'
```

## Krok 2. Aktualizacja kodu aplikacji
1. Pobierz i przełącz kod na branch/revizję wdrożeniową:
```bash
git fetch origin
git checkout codex/fix-public-form-checkbox-422
git pull --ff-only
```
2. Aktywuj środowisko i doinstaluj zależności:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Krok 3. Migracje Alembic (wymagane przed startem)
1. Uruchom migracje do najnowszej rewizji:
```bash
source .venv/bin/activate
set -a; source .env; set +a
alembic upgrade head
alembic current
```
2. Oczekiwany wynik końcowy: `d7a2c9f8e041`.

## Krok 4. Konfiguracja skrzynki GRENKE (produkcyjny adres)
1. W pliku `.env` ustaw produkcyjny adres:
```dotenv
MAILBOX_EMAIL_ADDRESS=umowy@ksero-partner.com.pl
MAILBOX_EMAIL_PASSWORD=<haslo_skrzynki>
MAILBOX_IMAP_HOST=<host_imap>
MAILBOX_IMAP_PORT=993
MAILBOX_SMTP_HOST=<host_smtp>
MAILBOX_SMTP_PORT=465
MAILBOX_SMTP_USE_SSL=true
MAILBOX_SMTP_USE_STARTTLS=false
```
2. Przetestuj połączenie IMAP/SMTP:
```bash
source .venv/bin/activate
set -a; source .env; set +a
python scripts/mailbox_connection_check.py --timeout 12
```
3. Przetestuj automat mailboxa na sucho:
```bash
python scripts/contracts_mailbox_sync.py --limit 10 --dry-run
```

## Krok 5. Konfiguracja SMTP dla wiadomości z GENFORM
1. W panelu: `Admin -> Konfiguracja -> E-mail` ustaw:
- `Adres nadawcy (From)`: `umowy@ksero-partner.com.pl`
- poprawny host/port/login/hasło SMTP.
2. Wykonaj `Wyślij test` z panelu lub:
```bash
curl -X POST "http://127.0.0.1:8000/admin/email/test" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Session: <TOKEN_ADMINA>" \
  -d '{
    "test_recipient":"<adres_testowy>",
    "test_subject":"Test SMTP GENFORM",
    "test_body":"Test po wdrozeniu produkcyjnym."
  }'
```

## Krok 6. Google Sheets FLOW: `Zerowki_prod`
1. W panelu: `Admin -> Konfiguracja bazy -> Google Sheets (FLOW)` ustaw:
- `Spreadsheet ID`: produkcyjny ID skoroszytu,
- `Zakładka workflow`: `Zerowki_prod`,
- `Credentials path`: ścieżka do JSON service account.
2. Udostępnij skoroszyt kontu service account (mail z pliku JSON, rola co najmniej edytor).
3. Uruchom test konfiguracji:
```bash
curl -X POST "http://127.0.0.1:8000/admin/google-sheets/test" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Session: <TOKEN_ADMINA>" \
  -d '{"workflow_devices_worksheet":"Zerowki_prod"}'
```
4. Jeżeli test pokaże brak nagłówków, uruchom bootstrap:
```bash
curl -X POST "http://127.0.0.1:8000/admin/google-sheets/bootstrap-headers" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Session: <TOKEN_ADMINA>" \
  -d '{"workflow_devices_worksheet":"Zerowki_prod"}'
```
5. Wykonaj ponownie test `/admin/google-sheets/test` i potwierdź `success=true`.

## Krok 7. Start usług po wdrożeniu
1. Uruchom aplikację (w zależności od trybu hostingu: `run_stack_tmux.sh`, usługa systemowa lub kontener).
2. Zweryfikuj healthcheck:
```bash
curl -sS http://127.0.0.1:8000/health
```

## Krok 8. Smoke test funkcjonalny GENFORM/FLOW
1. Zaloguj się na `/genform`.
2. Otwórz formularz `SUBMITTED`, kliknij `Dane zostały wpisane`.
3. Potwierdź:
- e-mail dochodzi do klienta,
- drugi klik nie wysyła ponownie (idempotencja),
- w szczegółach formularza widoczny jest status wysłania.
4. W `/genform` sprawdź notkę `Synchronizacja e-mail GRENKE` oraz ręcznie uruchom synchronizację mailboxa z panelu FLOW.

## Rollback (powrót do stanu sprzed wdrożenia)
1. Zatrzymaj usługi aplikacji.
2. Przywróć kod:
```bash
git checkout pre_genform_flow_2026-04-29
```
3. Odtwórz PostgreSQL:
```bash
dropdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE"
createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE"
pg_restore -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
  "backups/prod_2026-04-29/ctip_prod_pre_genform_flow.dump"
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
  -f "backups/prod_2026-04-29/pg_globals_pre_genform_flow.sql"
```
4. Odtwórz Firebird:
```bash
gbak -c -replace_database -user "$FB_USER" -password "$FB_PASSWORD" \
  "backups/prod_2026-04-29/firebird_prod_pre_genform_flow.fbk" "$FB_DATABASE"
```
5. Uruchom usługi i sprawdź `GET /health`.
