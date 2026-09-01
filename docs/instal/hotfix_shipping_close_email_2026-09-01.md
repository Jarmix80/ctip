# Hotfix zamknięcia Shipping i nadawcy e-mail

## Cel

Hotfix usuwa fałszywy błąd HTTP 500 występujący po poprawnym zapisaniu dokumentów
RW/WZ/FV i zamknięciu zleceń. Źródłem błędu było wygaszenie wszystkich obiektów
sesji SQLAlchemy przed ponownym odczytem adresu IP sesji administratora. Zmiana
zabezpiecza również częściowe zamknięcie dnia przed ponownym wykonaniem skutków
zewnętrznych.

Druga część hotfixu ustanawia jedną tożsamość poczty wychodzącej:

```dotenv
EMAIL_USERNAME=system@ksero-partner.com.pl
EMAIL_SENDER_ADDRESS=system@ksero-partner.com.pl
EMAIL_REPLY_TO_ADDRESS=marcin@ksero-partner.com.pl
MAILBOX_EMAIL_ADDRESS=umowy@ksero-partner.com.pl
```

Adres `umowy@ksero-partner.com.pl` służy wyłącznie do odbioru IMAP. Nie może być
używany w nagłówku `From`, kopercie SMTP ani alercie administracyjnym.

## Stan wejściowy

- produkcyjny commit przed hotfixem: `f8d9b5256081709b2d2cf3c8dbc08a992c795bd2`;
- rewizja Alembic przed i po wdrożeniu: `f2b7c9d4e6a1`;
- geokoder Adresy.app i zapis pól przesyłki MS są już aktywne;
- wdrożenie nie zmienia schematu PostgreSQL ani Firebirda;
- osiem wiadomości z incydentu pozostaje ze statusem błędu i nie podlega ponowieniu.

## Kontrola przed wdrożeniem

1. Potwierdź czysty `HEAD` produkcji i działanie usług `CollectorService`,
   `CTIP-Web`, `CTIP-SMS` oraz `CTIP-FormsPublic`.
2. Sprawdź `/health`, `/shipping`, `/shipping/legacy`, `/operator`, `/genform`,
   `/flow`, `/device` i lokalny healthcheck FormsPublic.
3. Wykonaj `pre-commit run --all-files`, pełny `pytest`, `ruff check` oraz
   `black --check` w środowisku `.env.test`.
4. Wykonaj kopię `D:\CTIP\.env` w zabezpieczonym katalogu kopii konfiguracji.
5. Ustaw wartości poczty wskazane w sekcji „Cel”. Jeżeli NSSM zawiera
   `EMAIL_SENDER_ADDRESS` lub `EMAIL_REPLY_TO_ADDRESS`, usuń te nadpisania, aby
   jedynym źródłem był plik `.env`.

## Wdrożenie kodu

Użyj wyłącznie kanonicznego orkiestratora. `<release_sha>` musi być pełnym SHA
hotfixu i potomkiem stanu wejściowego.

```bash
source .venv/bin/activate
python scripts/deploy_windows_prod.py \
  --release <release_sha> \
  --expected-current f8d9b5256081709b2d2cf3c8dbc08a992c795bd2 \
  --alembic-before f2b7c9d4e6a1 \
  --alembic-after f2b7c9d4e6a1 \
  --allowed-path app \
  --allowed-path scripts \
  --allowed-path tests \
  --allowed-path docs \
  --allowed-path README.md \
  --allowed-path .env.example \
  --service CTIP-Web \
  --service CTIP-FormsPublic \
  --endpoint 'CTIP|http://127.0.0.1:8000/health|200' \
  --endpoint 'Shipping|http://127.0.0.1:8000/shipping|200' \
  --endpoint 'FormsPublic|http://127.0.0.1:8100/health|200' \
  --dry-run
```

Po zaakceptowaniu raportu powtórz identyczne polecenie z `--apply` zamiast
`--dry-run`.

## Backfill audytu

Po pozytywnym healthchecku wykonaj odczytową kontrolę dwóch brakujących wpisów:

```powershell
D:\CTIP\.venv\Scripts\python.exe `
  scripts\backfill_shipping_close_audit_2026_09_01.py
```

Oczekiwany wynik pierwszego uruchomienia to `candidate_count=2` oraz
`would_create_count=2`. Zapis wymaga jawnej frazy:

```powershell
D:\CTIP\.venv\Scripts\python.exe `
  scripts\backfill_shipping_close_audit_2026_09_01.py `
  --apply `
  --confirmation "UZUPELNIJ AUDYT SHIPPING 2026-09-01"
```

Ponowny dry-run musi zwrócić `existing_count=2` i `would_create_count=0`.
Backfill zapisuje bieżący czas jako `created_at`, a pierwotny czas operacji,
identyfikator źródłowego zdarzenia i oznaczenie `backfill=true` w payloadzie.

## Kontrola po wdrożeniu

1. Potwierdź czysty produkcyjny `HEAD`, Alembic `f2b7c9d4e6a1` i brak błędów od
   bieżącego startu `CTIP-Web`.
2. Wyślij jedną wiadomość testową wyłącznie do
   `marcin@ksero-partner.com.pl` i sprawdź `From`, kopertę SMTP oraz `Reply-To`.
3. Potwierdź, że historyczne zamknięcie dnia `id=2` nadal ma status `partial`,
   liczniki `9/9/8` i nie uruchomiło ponownie powiadomień.
4. Przy najbliższym rzeczywistym zamknięciu sprawdź odpowiedź HTTP 200,
   `audit_status=recorded`, dokumenty MS oraz pojedynczy wpis audytowy.

## Rollback

Rollback kodu przywraca commit
`f8d9b5256081709b2d2cf3c8dbc08a992c795bd2` i restartuje wyłącznie
`CTIP-Web` oraz `CTIP-FormsPublic`. Poprawionych adresów poczty nie należy cofać.
Wpisów backfillu nie usuwa się, ponieważ dokumentują rzeczywiście wykonane
operacje biznesowe.
