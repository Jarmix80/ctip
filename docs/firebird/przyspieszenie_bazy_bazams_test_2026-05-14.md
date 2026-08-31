# Przyspieszenie bazy BAZAMS (test) - 2026-05-14

## Status dokumentu
Ten materiał dotyczy wyłącznie optymalizacji bazy Menadżer Serwisu (Firebird) na środowisku testowym i jest poza zakresem funkcjonalnym projektu CTIP.

## Środowisko i zakres testu
- Data testów: `2026-05-14`.
- Host testowy Firebird: `192.168.0.9` (lokalna instancja testowa).
- Alias bazy: `BAZAMS_TEST`.
- Źródło odtworzenia: `inbox/baza_test/BAZAMS_20260514_130630.fbk`.
- Odtworzony plik roboczy: `inbox/firebird/menadzer_serwisu.fdb` (pod aliasem `BAZAMS_TEST`).
- Zakres: ciężkie zapytania na fakturach, KSeF, pozycjach faktur, licznikach CPC i historii CMAIL.

## Metodyka benchmarku
- Fala 1 (faktury + KSeF): pomiar `before` i `after` po dodaniu pierwszego pakietu indeksów.
- Fala 2 (dodatkowa): pomiar `before` i `after` po dodaniu drugiego pakietu indeksów.
- Pomiary wykonywane na tej samej bazie testowej po odtworzeniu backupu.
- Fala 1: `runs_per_query=1`, `warmup_runs=1`.
- Fala 2: `runs_per_query=2`, `warmup_runs=1`.

## Zapytania testowe - fala 1

### 1) `invoice_list_with_ksef_rows5000`
Parametry: `['1', '2025-05-14', '2026-05-14', '%']`

```sql
SELECT DISTINCT
    F.ID_FAKTURA_TABLE,
    F.ID_KLIENT,
    F.ID_ODBIORCA,
    F.NUMER,
    F.NAZWA,
    F.DATA_WYST,
    F.SUMA_BRUTTO,
    F.DO_ZAPLATY,
    F.RODZAJ_DOK,
    K.STATUSCODE,
    K.KSEFNUMBER
FROM FAKTURA F
LEFT JOIN KSEF_FAKTURA K ON K.ID_FAKTURA = F.ID_FAKTURA_TABLE
WHERE F.ID_FIRMA = ?
  AND F.DATA_WYST >= ?
  AND F.DATA_WYST <= ?
  AND F.RODZAJ_DOK LIKE ?
ORDER BY F.NAZWA ASC
ROWS 5000;
```

### 2) `invoice_ksef_status_join_rows5000`
Parametry: `['1', '2025-05-14', '2026-05-14']`

```sql
SELECT
    F.ID_FAKTURA_TABLE,
    F.DATA_WYST,
    F.NUMER,
    K.ID_KF,
    K.STATUSCODE,
    K.KSEFNUMBER,
    S.ID_KS,
    S.STATUSCODE AS SESSION_STATUS
FROM FAKTURA F
LEFT JOIN KSEF_FAKTURA K ON K.ID_FAKTURA = F.ID_FAKTURA_TABLE
LEFT JOIN KSEF_SESSION S ON S.REFERENCENUMBER = K.SESSNUMBER
WHERE F.ID_FIRMA = ?
  AND F.DATA_WYST >= ?
  AND F.DATA_WYST <= ?
  AND COALESCE(K.DEMOMODE, 0) = 0
ORDER BY F.DATA_WYST DESC, F.ID_FAKTURA_TABLE DESC
ROWS 5000;
```

### 3) `fpozycja_by_idfirma_numer`
Parametry: `['1', '']`

```sql
SELECT *
FROM FPOZYCJA
WHERE ID_FIRMA = ?
  AND NUMER = ?;
```

### 4) `fpozycja_sum_by_ftemp_idvat`
Parametry: `['ROZLICZENIA_NEW.JoannaG.28.10.2025.15:26:27.206212261', '1']`

```sql
SELECT
    SUM(WARTOSC_NETTO) AS SUMA_N,
    SUM(WARTOSC_BRUTTO) AS SUMA_B,
    SUM(WARTOSC_NETTO_WAL) AS SUMA_NW,
    SUM(WARTOSC_BRUTTO_WAL) AS SUMA_BW,
    SUM(WARTOSC_Z) AS SUMA_ZAK
FROM FPOZYCJA
WHERE FTEMP = ?
  AND IDVAT = ?;
```

### 5) `invoice_monthly_summary_with_ksef`
Parametry: `['1', '2025-05-14', '2026-05-14']`

```sql
SELECT
    EXTRACT(YEAR FROM F.DATA_WYST) AS ROK,
    EXTRACT(MONTH FROM F.DATA_WYST) AS MIESIAC,
    COUNT(*) AS ILOSC_FV,
    SUM(F.SUMA_BRUTTO) AS SUMA_BRUTTO,
    SUM(F.DO_ZAPLATY) AS SUMA_DO_ZAPLATY,
    SUM(CASE WHEN K.ID_KF IS NULL THEN 0 ELSE 1 END) AS ILOSC_Z_KSEF
FROM FAKTURA F
LEFT JOIN KSEF_FAKTURA K ON K.ID_FAKTURA = F.ID_FAKTURA_TABLE
WHERE F.ID_FIRMA = ?
  AND F.DATA_WYST >= ?
  AND F.DATA_WYST <= ?
  AND F.RODZAJ_DOK <> 'proforma'
GROUP BY EXTRACT(YEAR FROM F.DATA_WYST), EXTRACT(MONTH FROM F.DATA_WYST)
ORDER BY ROK, MIESIAC;
```

### 6) `invoice_unpaid_by_top_client`
Parametry: `['1', '1937', '2026-05-14']`

```sql
SELECT
    NUMER,
    DATA_WYST,
    DATA_PLAT,
    SUMA_BRUTTO,
    DO_ZAPLATY
FROM FAKTURA
WHERE ID_FIRMA = ?
  AND ID_KLIENT = ?
  AND ID_ODBIORCA = 0
  AND DATA_PLAT <= ?
  AND DO_ZAPLATY > 0
ORDER BY DATA_WYST ASC;
```

## Indeksy dodane - fala 1

```sql
CREATE INDEX IDX_FP_IDF_NUM ON FPOZYCJA (ID_FIRMA, NUMER);
CREATE INDEX IDX_FP_FTEMP_IDVAT ON FPOZYCJA (FTEMP, IDVAT);
CREATE INDEX IDX_FV_FIR_DATA_RODZ ON FAKTURA (ID_FIRMA, DATA_WYST, RODZAJ_DOK);
CREATE INDEX IDX_FV_FIR_KLI_ODB_PLAT ON FAKTURA (ID_FIRMA, ID_KLIENT, ID_ODBIORCA, DATA_PLAT);
CREATE INDEX IDX_CPC_MASZ_ROK_MIES ON CPC (ID_MASZYNA, ROK, MIESIAC);
CREATE INDEX IDX_KSEF_FAKT_ID_DEMO ON KSEF_FAKTURA (ID_FAKTURA, DEMOMODE);
CREATE INDEX IDX_KSEF_FAKT_SESS ON KSEF_FAKTURA (SESSNUMBER);
CREATE INDEX IDX_KSEF_SESS_REF ON KSEF_SESSION (REFERENCENUMBER);
CREATE INDEX IDX_MAIL_FIR_AKCJA ON MAIL (ID_FIRMA, AKCJA);
CREATE INDEX IDX_NOTES_PRZYP_DATA ON NOTES (PRZYPOMINACZ, DATA_P);
```

Po utworzeniu indeksów wykonano odświeżenie statystyk:

```sql
SET STATISTICS INDEX IDX_FP_IDF_NUM;
SET STATISTICS INDEX IDX_FP_FTEMP_IDVAT;
SET STATISTICS INDEX IDX_FV_FIR_DATA_RODZ;
SET STATISTICS INDEX IDX_FV_FIR_KLI_ODB_PLAT;
SET STATISTICS INDEX IDX_CPC_MASZ_ROK_MIES;
SET STATISTICS INDEX IDX_KSEF_FAKT_ID_DEMO;
SET STATISTICS INDEX IDX_KSEF_FAKT_SESS;
SET STATISTICS INDEX IDX_KSEF_SESS_REF;
SET STATISTICS INDEX IDX_MAIL_FIR_AKCJA;
SET STATISTICS INDEX IDX_NOTES_PRZYP_DATA;
```

## Wyniki benchmarku - fala 1
Poniższe czasy pochodzą z pomiaru `before` vs `after` z dnia `2026-05-14`.

| Zapytanie | Przed | Po | Zysk |
|---|---:|---:|---:|
| `invoice_list_with_ksef_rows5000` | 6.4714 s | 0.6648 s | x9.73 |
| `invoice_ksef_status_join_rows5000` | 8.9153 s | 0.4146 s | x21.51 |
| `fpozycja_by_idfirma_numer` | 0.2741 s | 0.0917 s | x2.99 |
| `fpozycja_sum_by_ftemp_idvat` | 0.2143 s | 0.0026 s | x83.82 |
| `invoice_monthly_summary_with_ksef` | 5.7430 s | 0.0578 s | x99.34 |
| `invoice_unpaid_by_top_client` | 0.0825 s | 0.0046 s | x18.05 |

## Zapytania testowe - fala 2

### 1) `faktura_due_order_nazwa_rows5000`
Parametry: `['1', '2025-09-30', '2026-09-30']`

```sql
SELECT
    F.ID_FAKTURA_TABLE,
    F.DATA_PLAT,
    F.NAZWA,
    F.SUMA_BRUTTO,
    F.ZAPLACONO,
    F.DO_ZAPLATY,
    F.RODZAJ_DOK
FROM FAKTURA F
WHERE F.ID_FIRMA = ?
  AND F.DATA_PLAT BETWEEN ? AND ?
  AND F.SUMA_BRUTTO <> F.ZAPLACONO
  AND F.RODZAJ_DOK <> 'proforma'
ORDER BY F.NAZWA ASC
ROWS 5000;
```

### 2) `faktura_order_nazwa_rows5000`
Parametry: `['1', '2025-05-14', '2026-05-14']`

```sql
SELECT
    F.ID_FAKTURA_TABLE,
    F.NAZWA,
    F.DATA_WYST,
    F.SUMA_BRUTTO
FROM FAKTURA F
WHERE F.ID_FIRMA = ?
  AND F.DATA_WYST BETWEEN ? AND ?
ORDER BY F.NAZWA ASC
ROWS 5000;
```

### 3) `cpc_by_machine_order_desc_rows240`
Parametry: `['0']`

```sql
SELECT
    ID_CPC_TABLE,
    ID_MASZYNA,
    ROK,
    MIESIAC,
    LICZNIK_MONO_END,
    LICZNIK_KOLOR_END
FROM CPC
WHERE ID_MASZYNA = ?
ORDER BY ROK DESC, MIESIAC DESC
ROWS 240;
```

### 4) `cmail_like_original_rows2000`
Parametry: `['C718R8%', 'MPC  3%']`

```sql
SELECT
    ID_CMAIL,
    SERIAL,
    MODEL_NAME,
    COUNTER_DATE,
    TOTAL,
    TOTAL_MONO,
    TOTAL_COLOR
FROM CMAIL
WHERE UPPER(SERIAL) LIKE UPPER(?)
  AND MODEL_NAME LIKE ?
ORDER BY ID_CMAIL DESC
ROWS 2000;
```

### 5) `cmail_starting_with_rows2000`
Parametry: `['C718R8', 'MPC  3']`

```sql
SELECT
    ID_CMAIL,
    SERIAL,
    MODEL_NAME,
    COUNTER_DATE,
    TOTAL,
    TOTAL_MONO,
    TOTAL_COLOR
FROM CMAIL
WHERE UPPER(SERIAL) STARTING WITH UPPER(?)
  AND MODEL_NAME STARTING WITH ?
ORDER BY ID_CMAIL DESC
ROWS 2000;
```

### 6) `ksef_faktura_session_join_rows5000`
Parametry: `[]`

```sql
SELECT
    K.ID_KF,
    K.ID_FAKTURA,
    K.SESSNUMBER,
    K.STATUSCODE,
    S.ID_KS,
    S.STATUSCODE AS SESSION_STATUS
FROM KSEF_FAKTURA K
LEFT JOIN KSEF_SESSION S ON S.REFERENCENUMBER = K.SESSNUMBER
WHERE COALESCE(K.DEMOMODE, 0) = 0
ORDER BY K.ID_FAKTURA DESC
ROWS 5000;
```

## Indeksy dodane - fala 2

```sql
CREATE DESCENDING INDEX IDX_CPC_MASZ_ROK_MIES_D ON CPC (ID_MASZYNA, ROK, MIESIAC);
CREATE INDEX IDX_FV_FIR_DATA_PLAT ON FAKTURA (ID_FIRMA, DATA_PLAT);
CREATE INDEX IDX_FV_FIR_NAZWA ON FAKTURA (ID_FIRMA, NAZWA);
CREATE INDEX IDX_CMAIL_SERIAL_UPPER ON CMAIL COMPUTED BY ((UPPER(SERIAL)));
CREATE INDEX IDX_CMAIL_MODEL_NAME ON CMAIL (MODEL_NAME);
```

Po utworzeniu indeksów wykonano odświeżenie statystyk:

```sql
SET STATISTICS INDEX IDX_CPC_MASZ_ROK_MIES_D;
SET STATISTICS INDEX IDX_FV_FIR_DATA_PLAT;
SET STATISTICS INDEX IDX_FV_FIR_NAZWA;
SET STATISTICS INDEX IDX_CMAIL_SERIAL_UPPER;
SET STATISTICS INDEX IDX_CMAIL_MODEL_NAME;
```

## Wyniki benchmarku - fala 2

| Zapytanie | Przed | Po | Zysk | Zmiana czasu |
|---|---:|---:|---:|---:|
| `faktura_due_order_nazwa_rows5000` | 0.2483 s | 0.0374 s | x6.64 | -84.95% |
| `faktura_order_nazwa_rows5000` | 0.2292 s | 0.2300 s | x1.00 | +0.35% |
| `cpc_by_machine_order_desc_rows240` | 0.0332 s | 0.0277 s | x1.20 | -16.69% |
| `cmail_like_original_rows2000` | 0.2187 s | 0.1905 s | x1.15 | -12.87% |
| `cmail_starting_with_rows2000` | 0.2068 s | 0.0571 s | x3.62 | -72.41% |
| `ksef_faktura_session_join_rows5000` | 0.0731 s | 0.0699 s | x1.04 | -4.30% |

## Co zyskano
- Największy efekt dały indeksy pod listowanie i agregacje faktur/KSeF (do ~x99 w fali 1).
- Dodatkowo przyspieszone zostały scenariusze faktur „nieopłaconych” oraz prefiksowe wyszukiwanie `CMAIL` (`STARTING WITH`).
- Zapytanie sortowane po `FAKTURA.NAZWA` bez dodatkowej selektywności (`faktura_order_nazwa_rows5000`) nie poprawiło się praktycznie, co wskazuje na koszt dominujący po stronie sortowania i wolumenu danych.

## Artefakty źródłowe (surowe JSON)
- `inbox/baza_test/benchmark_faktury_ksef_before_indexes_20260514_133343.json`
- `inbox/baza_test/benchmark_faktury_ksef_after_indexes_20260514_134610.json`
- `inbox/baza_test/benchmark_second_wave_before_20260514_140908.json`
- `inbox/baza_test/benchmark_second_wave_after_20260514_140908.json`
- `inbox/baza_test/benchmark_second_wave_compare_20260514_140908.json`
