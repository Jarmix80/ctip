# Bazams -> CTIP: mapa referencyjna

## Źródło referencyjne
- Repozytorium: `git@github.com:Jarmix80/bazams.git`
- Klon lokalny (tylko roboczy): `docs/firebird/external/bazams/`
- Gałąź/commit użyty do analizy: `main` / `a44f467c6c4b77566838a9c9fa99191559fb60ca`

## Gdzie szukać informacji w `bazams`
1. Konfiguracja połączenia Firebird i zasady pracy:
   - `README.md`
   - `docs/production_db.md`
2. Struktura tabel Firebird:
   - `docs/structure/*.md`
   - generator: `src/generate_structure_docs.py`
3. Procesy importu liczników / relacje biznesowe:
   - `src/import_email_counters.py`
   - `src/import_csv_counters.py`
   - `scripts/sync_cmail_counters.py`
   - `docs/faktury_umowy.md`

## Tabele istotne dla CTIP
1. `KLIENT`:
   - klucz biznesowy: `ID_KLIENT`
   - pola użyteczne: `NAZWA`, `NIP`, `TELEFON`, `E_MAIL`, `UWAGI`
2. `KONTAKT`:
   - klucz: `ID_KONTAKT_TABLE`
   - powiązanie: `ID_KLIENT`
   - pola użyteczne: `NAZWA`, `TEL_K`, `TEL_D`, `MAIL1`, `MAIL2`, `UWAGI`
3. `MASZYNA`:
   - klucz biznesowy: `ID_MASZYNA`
   - pola użyteczne: `SERIAL`, `SERIAL2`, `EWIDENCJA`, `ID_KLIENT`, `ID_UMOWACPC`
4. `ZLECENIE`:
   - klucz biznesowy: `ID_ZLECENIE`
   - pola użyteczne: `ID_KLIENT`, `ID_MASZYNA`, `TELEFON`, `E_MAIL`, `OPERATOR`, `DATA`
5. `UMOWA`, `UMOWACPC`, `CPC`, `FAKTURA`, `FPOZYCJA`:
   - używane do analityki umów/faktur, bezpośrednio niepodpinane do CTIP na tym etapie.

## Mapowanie pól do `ctip.contact`
1. `ctip.contact.firebird_id`:
   - rekomendacja: przechowuj `ID_KLIENT` (źródło: `KLIENT.ID_KLIENT`),
   - alternatywnie dla wpisów serwisowych per urządzenie: `ID_MASZYNA`.
2. `ctip.contact.company`:
   - `KLIENT.NAZWA`
3. `ctip.contact.nip`:
   - `KLIENT.NIP`
4. `ctip.contact.email`:
   - preferencja: `KONTAKT.MAIL1`, fallback: `KLIENT.E_MAIL`
5. `ctip.contact.number`:
   - preferencja: `KONTAKT.TEL_K`, fallback: `KLIENT.TELEFON`
6. `ctip.contact.notes`:
   - `KLIENT.UWAGI` lub `KONTAKT.UWAGI`

## Zapytania robocze (read-only)
### Szukanie klienta po numerze telefonu
```sql
SELECT
    k.ID_KLIENT,
    k.NAZWA,
    k.NIP,
    k.TELEFON,
    k.E_MAIL
FROM KLIENT k
WHERE k.TELEFON CONTAINING :needle;
```

### Szukanie kontaktów osoby po numerze
```sql
SELECT
    c.ID_KLIENT,
    c.NAZWA,
    c.TEL_K,
    c.TEL_D,
    c.MAIL1
FROM KONTAKT c
WHERE c.TEL_K CONTAINING :needle
   OR c.TEL_D CONTAINING :needle;
```

### Ostatnie zlecenia utworzone z aplikacji
```sql
SELECT ID_ZLECENIE, ROK, DATA, OPERATOR
FROM ZLECENIE
WHERE OPERATOR CONTAINING 'z aplikacji'
ORDER BY DATA DESC, ID_ZLECENIE DESC;
```

## Uwagi integracyjne
1. W dokumentacji `bazams` wiele relacji FK nie jest zdefiniowanych w DB jako constrainty; łączenia robimy po kolumnach `ID_*`.
2. Na środowisku produkcyjnym obowiązuje tryb odczytu (zgodnie z `docs/production_db.md`).
3. Dla testów zapisu i eksperymentów używać wyłącznie lokalnej kopii roboczej Firebird.
