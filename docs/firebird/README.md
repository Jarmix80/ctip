# Integracja Firebird (Menadżer Serwisu)

## Cel
Ten katalog przechowuje materiały referencyjne i robocze dotyczące integracji CTIP z bazą Firebird programu Menadżer Serwisu.

## Główne źródła informacji
1. Repozytorium zewnętrzne: `https://github.com/Jarmix80/bazams`.
   - lokalny klon roboczy: `docs/firebird/external/bazams/` (nie commitujemy do głównego repozytorium CTIP),
   - mapa referencyjna pod CTIP: `docs/firebird/bazams_mapowanie_ctip.md`.
   - opis rzeczywistego procesu handlowego MS oraz zmian KSeF: `docs/firebird/proces_sprzedazy_ms.md`.
2. Konfiguracja i test połączenia w CTIP:
   - API: `app/api/routes/admin_config.py` (`/admin/config/firebird`) i `app/api/routes/admin_firebird.py` (`/admin/firebird/test`),
   - UI: `app/templates/admin/partials/config_firebird.html`, `app/static/admin/admin.js`, `app/web/admin_ui.py`.
3. Mapowanie kontaktów CTIP ↔ Firebird:
   - pole `ctip.contact.firebird_id` w `docs/baza/schema_ctip.sql`,
   - API kontaktów: `app/api/routes/admin_contacts.py`, `app/api/routes/operator_portal.py`.

## Co umieszczać w tym katalogu
1. Wybrane pliki z `bazams` wymagane do integracji (opis struktury tabel, zapytania, słowniki kodów).
2. Notatki mapowania pól Firebird -> `ctip.contact` / `ctip.contact_device`.
3. Utrwalone analizy workflow Menadżera Serwisu (klient, magazyn, PZ, proforma, FV) wraz z triggerami i zapytaniami diagnostycznymi.
4. Eksporty pomocnicze do testów lokalnych (bez danych wrażliwych).

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

## Synchronizacja MODEL.PLIK z obrazami /imgdev
Do aktualizacji pola `MODEL.PLIK` na lokalnej kopii roboczej sluzy skrypt:

```bash
source .venv/bin/activate
set -a && source .env && set +a
python scripts/firebird_sync_model_plik.py
python scripts/firebird_sync_model_plik.py --apply
```

Zasady:
1. Domyslnie wykonywany jest tylko `dry-run` i raport CSV/MD trafia do `inbox/audyt_model`.
2. W trybie `FB_MODE=local` skrypt wymusza host `127.0.0.1`, zgodnie z lokalnym kontenerem Firebird.
3. Skrypt laczy sie przez `firebirdsql` do lokalnej kopii wskazanej przez `FB_LOCAL_COPY_PATH`, a gdy serwer Firebird nie widzi sciezki WSL, automatycznie probuje aliasu `BAZAMS_TEST` lub wartosci podanej przez `--database-alias`.
4. Przy `--apply` skrypt wykonuje kopie bezpieczenstwa pliku `.fdb`, jezeli wskazana baza istnieje lokalnie jako plik.
5. Aktualizowane sa tylko rekordy `MODEL` z rodziny Ricoh, dla ktorych istnieje finalny plik `ran_*.png` w `inbox/audyt_model/imgdev`.

## Naprawa approved master MODEL
Do odtworzenia approved master tabeli `MODEL` ze snapshotu referencyjnego sluzy skrypt:

```bash
source .venv/bin/activate
set -a && source .env && set +a
python scripts/firebird_repair_model_master.py
python scripts/firebird_repair_model_master.py --apply
```

Zasady:
1. Snapshot referencyjny jest traktowany jako zrodlo prawdy dla `MODEL`.
2. Skrypt przepina `ID_MODEL` w `MASZYNA`, `MAGAZYN`, `CENNIK` i `MZ`, jezeli nadmiarowy model ma jednoznaczny odpowiednik w approved masterze.
3. Nadmiarowe rekordy `MODEL` sa usuwane dopiero po przepieciu referencji.
4. Dla lokalnej bazy skrypt sam robi backup pliku `.fdb`; dla zdalnej bazy produkcyjnej trzeba wykonac backup osobno i uruchomic skrypt z `--skip-backup`.
5. Dla scenariusza produkcyjnego host docelowy i host snapshotu moga byc rozne (`--host 192.168.0.8`, `--reference-host 127.0.0.1`).
6. Po `--apply` skrypt porownuje wynik 1:1 ze snapshotem referencyjnym i raportuje ewentualne osierocone `ID_MODEL`.

## Renumeracja wysokich ID_MODEL
Do uporzadkowania numeracji `MODEL.ID_MODEL` (np. przeniesienie rekordow `3000xxxx` na ciag dalszy po `631`) sluzy skrypt:

```bash
source .venv/bin/activate
set -a && source .env && set +a
python scripts/firebird_resequence_model_ids.py --max-stable-id 631
python scripts/firebird_resequence_model_ids.py --max-stable-id 631 --apply
```

Zasady:
1. Domyslnie skrypt wykonuje tylko dry-run i zapisuje raport JSON/MD do `inbox/audyt_model`.
2. Przy `--apply` renumeruje tylko rekordy `MODEL` z `ID_MODEL > max_stable_id`.
3. Powiazania `ID_MODEL` sa aktualizowane we wszystkich tabelach z kolumna `ID_MODEL` (m.in. `MASZYNA`, `MAGAZYN`, `CENNIK`, `MZ`).
4. Dla lokalnej bazy plikowej skrypt wykonuje backup `.fdb` (chyba ze podano `--skip-backup`).
5. Po wykonaniu nalezy zweryfikowac raport i dopiero potem powtorzyc operacje na produkcji.
