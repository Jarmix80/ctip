# Integracja Firebird (Menadżer Serwisu)

## Cel
Ten katalog przechowuje materiały referencyjne i robocze dotyczące integracji CTIP z bazą Firebird programu Menadżer Serwisu.

## Główne źródła informacji
1. Repozytorium zewnętrzne: `https://github.com/Jarmix80/bazams`.
   - lokalny klon roboczy: `docs/firebird/external/bazams/` (nie commitujemy do głównego repozytorium CTIP),
   - mapa referencyjna pod CTIP: `docs/firebird/bazams_mapowanie_ctip.md`.
2. Konfiguracja i test połączenia w CTIP:
   - API: `app/api/routes/admin_config.py` (`/admin/config/firebird`) i `app/api/routes/admin_firebird.py` (`/admin/firebird/test`),
   - UI: `app/templates/admin/partials/config_firebird.html`, `app/static/admin/admin.js`, `app/web/admin_ui.py`.
3. Mapowanie kontaktów CTIP ↔ Firebird:
   - pole `ctip.contact.firebird_id` w `docs/baza/schema_ctip.sql`,
   - API kontaktów: `app/api/routes/admin_contacts.py`, `app/api/routes/operator_portal.py`.

## Co umieszczać w tym katalogu
1. Wybrane pliki z `bazams` wymagane do integracji (opis struktury tabel, zapytania, słowniki kodów).
2. Notatki mapowania pól Firebird -> `ctip.contact` / `ctip.contact_device`.
3. Eksporty pomocnicze do testów lokalnych (bez danych wrażliwych).

## Lokalne kopie bazy roboczej
1. Docelowa ścieżka lokalnej kopii jest konfigurowana przez `FB_LOCAL_COPY_PATH`.
2. Parametry połączenia konfiguruje administrator w panelu:
   - sekcja `Baza Firebird`,
   - wybór aktywnego trybu bazy: `Baza sieciowa` (`FB_MODE=network`) albo `Baza lokalna` (`FB_MODE=local`),
   - akcje: `Zapisz konfigurację` i `Testuj połączenie`.
3. Po poprawnym teście połączenia zalecany tryb pracy:
   - produkcja: połączenie z bazą produkcyjną Firebird,
   - development: praca na lokalnej kopii roboczej wskazanej przez `FB_LOCAL_COPY_PATH`.

## Status środowiska (Linux)
1. Dostęp TCP do `FB_HOST:FB_PORT` jest wymagany (domyślnie `192.168.0.8:3050`).
2. Endpoint testowy CTIP najpierw próbuje połączenia przez `fdb`, a w razie braku `fbclient` automatycznie przechodzi na `firebirdsql`.
3. Jeśli `FB_DATABASE` wskazuje ścieżkę windowsową (np. `C:/...` lub `D:/...`) na hostcie zdalnym, bezpośrednie kopiowanie pliku `.fdb` w Linuxie nie zadziała bez dodatkowego montowania/udostępnienia zasobu.

## Szybkie utworzenie kopii lokalnej
Po zamontowaniu źródłowego pliku `.fdb` w systemie lokalnym można wykonać kopię poleceniem:

```bash
source .venv/bin/activate
set -a && source .env && set +a
python scripts/firebird_clone_local.py --force
```

Skrypt mapuje ścieżki Windows (`D:/...`) na ścieżki montowania (`/mnt/d/...`) tylko gdy taki mount istnieje.
