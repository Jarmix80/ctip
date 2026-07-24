# Audyt wydajności BAZAMS produkcja - 2026-06-30

## Status dokumentu
Ten materiał dotyczy wyłącznie bazy Menadżer Serwisu (`BAZAMS.FDB`) na produkcji. Audyt wykonano w trybie tylko do odczytu: bez DDL, bez DML, bez zmian konfiguracji Firebird i bez zatrzymywania usług.

## Zakres i zasady bezpieczeństwa
- Host produkcyjny: `192.168.0.8`.
- Adres źródłowy hosta audytującego: `192.168.0.9`.
- Port Firebird: `3050`.
- Baza: `D:\BAZA_MS_KP\BAZAMS.FDB`.
- Silnik: Firebird `2.5.9`, ODS `11.2`.
- Sterownik audytu: `firebirdsql` z transakcją `ISOLATION_LEVEL_READ_COMMITED_RO`.
- Artefakty surowe:
  - `inbox/firebird_audit/audyt_bazams_prod_20260630_20260630_095730.json`,
  - `inbox/firebird_audit/propozycje_indeksow_bazams_prod_20260630_20260630_095730.sql`.

## Stan techniczny bazy
- Plik bazy: `580599808` bajtów, około `554 MiB`.
- Usługi `FirebirdGuardianDefaultInstance` i `FirebirdServerDefaultInstance` działały w trakcie audytu.
- Parametry z `gstat -h`:
  - `Page size`: `4096`,
  - `Generation`: `13422813`,
  - `Oldest transaction`: `16470336`,
  - `Oldest active`: `16480284`,
  - `Oldest snapshot`: `16480284`,
  - `Next transaction`: `16640740`,
  - `Sweep interval`: `20000`,
  - `Attributes`: `force write`,
  - `Creation date`: `Feb 1, 2020 15:59:34`.
- Różnica `Next transaction - Oldest active` wynosi około `160456`, a `Next transaction - Oldest transaction` około `170404`. To wskazuje na realny problem długich transakcji blokujących sprzątanie wersji rekordów.
- Najstarsza aktywna transakcja w `MON$TRANSACTIONS` pochodziła z `2026-06-18 23:20:10` i była przypisana do `C:\MSConnector\MSConnector.exe` na `SERWER1`.
- Kolejne długie transakcje pochodziły m.in. z `MSerwis_synchro.exe`, `Counters.exe` oraz klientów `mserwis6.exe`.
- `firebird.conf` nie ma jawnie ustawionych parametrów z grupy cache/temp/sort w sprawdzonych pozycjach, więc instancja działa na wartościach domyślnych Firebird.
- Serwer: Dell PowerEdge R630, Windows Server 2022 Standard, `32` logiczne CPU, około `64 GiB` RAM. Dysk `D:` miał około `1.42 TB` wolnego miejsca.

## Rozmiary kluczowych tabel
| Tabela | Liczba rekordów | Czas pomiaru |
|---|---:|---:|
| `FPOZYCJA` | 245568 | 0.410 s |
| `CPC` | 170534 | 0.199 s |
| `CMAIL` | 108598 | 0.177 s |
| `FAKTURA` | 57643 | 0.153 s |
| `ZAKPOZYCJA` | 105556 | pomiar uzupełniający |
| `ZAKUPY` | 37667 | pomiar uzupełniający |
| `MAIL` | 32877 | 0.079 s |
| `MAGAZYN` | 7351 | 0.045 s |
| `MASZYNA` | 5785 | 0.042 s |
| `KLIENT` | 2597 | 0.032 s |
| `KSEF_SESSION` | 2108 | 0.032 s |
| `KSEF_FAKTURA` | 1997 | 0.035 s |
| `NOTES` | 814 | 0.030 s |
| `MODEL` | 174 | 0.030 s |
| `MZ` | 0 | 0.029 s |

Tabela `PZ` nie występuje w tej bazie pod taką nazwą; próba `COUNT(*)` zwróciła błąd `Table unknown`.

## Wyniki benchmarków
### Zapytania przekraczające limit
| Zapytanie | Limit | Plan Firebird | Wniosek |
|---|---:|---|---|
| `invoice_list_with_ksef_rows5000` | 20 s | `SORT (SORT (JOIN ...))` | Indeksy są używane, ale `DISTINCT` + `ORDER BY F.NAZWA` wymuszają ciężkie sortowanie. |
| `invoice_ksef_status_join_rows5000` | 20 s | `JOIN (SORT (JOIN ...), S INDEX ...)` | Sortowanie po połączeniu `FAKTURA` + `KSEF_FAKTURA`; filtr `COALESCE(K.DEMOMODE, 0)` nie pomaga optymalizatorowi. |
| `faktura_order_nazwa_rows5000` | 16 s | `ORDER FAKTURA_NAZWA INDEX (IDX_FV_FIR_DATA_RODZ)` | Optymalizator łączy indeks porządkujący nazwę z filtrem daty, ale nadal nie mieści się w limicie. |
| `ksef_faktura_session_join_rows5000` | 12 s | `SORT (JOIN (K NATURAL, S INDEX ...))` | `KSEF_FAKTURA` jest skanowana naturalnie i sortowana po `ID_FAKTURA DESC`. |

### Zapytania działające poprawnie
| Zapytanie | Czas | Uwagi |
|---|---:|---|
| `cmail_like_original_rows2000` | 0.468 s | Wzorzec `LIKE/UPPER` jest zauważalnie wolniejszy. |
| `cmail_starting_with_rows2000` | 0.055 s | `STARTING WITH` jest około `8.5x` szybsze dla tej próbki. |
| `invoice_monthly_summary_with_ksef` | 0.155 s | Obecne indeksy wystarczają. |
| `faktura_due_order_nazwa_rows5000` | 0.099 s | Akceptowalne. |
| `magazyn_available_warehouse28_rows5000` | 0.063 s | Akceptowalne dla aktualnej liczności. |
| `maszyna_by_client` | 0.050 s | Akceptowalne. |
| `fpozycja_by_idfirma_numer` | 0.036 s | Akceptowalne. |
| `cpc_by_machine_order_desc_rows240` | 0.034 s | Akceptowalne. |
| `invoice_unpaid_by_sample_client` | 0.032 s | Akceptowalne. |
| `klient_by_nip` | 0.031 s | Akceptowalne. |
| `fpozycja_sum_by_ftemp_idvat` | 0.031 s | Akceptowalne. |

## Indeksy
Wszystkie indeksy rekomendowane po testach z `2026-05-14` są już obecne na produkcji:

- `IDX_FP_IDF_NUM`,
- `IDX_FP_FTEMP_IDVAT`,
- `IDX_FV_FIR_DATA_RODZ`,
- `IDX_FV_FIR_KLI_ODB_PLAT`,
- `IDX_CPC_MASZ_ROK_MIES`,
- `IDX_KSEF_FAKT_ID_DEMO`,
- `IDX_KSEF_FAKT_SESS`,
- `IDX_KSEF_SESS_REF`,
- `IDX_MAIL_FIR_AKCJA`,
- `IDX_NOTES_PRZYP_DATA`,
- `IDX_CPC_MASZ_ROK_MIES_D`,
- `IDX_FV_FIR_DATA_PLAT`,
- `IDX_FV_FIR_NAZWA`,
- `IDX_CMAIL_SERIAL_UPPER`,
- `IDX_CMAIL_MODEL_NAME`.

Plik SQL z propozycjami nie zawiera obowiązkowych indeksów z poprzedniego etapu, bo nic z tej listy nie brakuje. Zawiera natomiast zakomentowane kandydaty do testów na kopii bazy.

## Obserwacje z aktywnych zapytań MS
W `MON$STATEMENTS` widoczne były zapytania aplikacji `MSerwis6.exe`, `MSConnector.exe`, `Counters.exe` i `MSerwis_synchro.exe`. Najważniejsze wzorce:

- `NOTES` po `PRZYPOMINACZ` + `DATA_P` ma już indeks `IDX_NOTES_PRZYP_DATA`.
- `MAIL` po `ID_FIRMA` + `AKCJA` ma już indeks `IDX_MAIL_FIR_AKCJA`.
- `ZAKUPY` wykonuje zapytania po `ID_FIRMA`, zakresie `DATA_WYST`, `RODZAJ_DOK` i sortowaniu `DATA_WYST`, `NUMER`; nie ma indeksu z takim prefiksem.
- `ZAKPOZYCJA` wykonuje zapytania po `ID_FIRMA`, `UWAGI`, `TEMP`, `IDVAT` oraz join po `ID_MAGAZYN`; nie ma indeksu z takim prefiksem.
- `MAGAZYN` używa wyszukiwania `UPPER(...) LIKE UPPER(?)` po kilku kolumnach z warunkami `ID_FIRMA` i `ID_MAGAZYN`; taki wzorzec zwykle nie korzysta dobrze ze zwykłych indeksów, szczególnie przy wyszukiwaniu zawierającym tekst w środku.
- `SAMOCHOD` i `CYKL` nie mają indeksów dla obserwowanych filtrów, ale aktualnie mają odpowiednio `0` i `2` rekordy, więc nie są priorytetem.

## Rekomendacje przyspieszenia
### Priorytet 1: długie transakcje
1. W oknie serwisowym zidentyfikować, dlaczego `MSConnector.exe` utrzymuje transakcję od `2026-06-18`.
2. Zamknąć lub zrestartować procesy utrzymujące najstarsze transakcje dopiero po potwierdzeniu, że nie wykonują krytycznej operacji.
3. Po usunięciu długich transakcji ponownie wykonać `gstat -h` i sprawdzić spadek różnic `Next - OAT` oraz `Next - OIT`.
4. Dopiero po tym rozważyć `sweep` lub backup/restore. Sweep bez zamknięcia starych transakcji nie usunie podstawowej przyczyny.

### Priorytet 2: zapytania faktur i KSeF
1. Przetestować na kopii bazy indeksy z pliku `inbox/firebird_audit/propozycje_indeksow_bazams_prod_20260630_20260630_095730.sql`.
2. Najpierw testować pojedynczo, nie hurtowo:
   - indeks zstępujący dla listy faktur po `DATA_WYST DESC, ID_FAKTURA_TABLE DESC`,
   - indeks rozszerzający sortowanie po `NAZWA` o `DATA_WYST`,
   - indeks zstępujący dla `KSEF_FAKTURA.ID_FAKTURA`.
3. Jeżeli aplikacja pozwala modyfikować zapytania, lepszy efekt może dać ograniczenie listy faktur przed joinem z KSeF, np. pobranie `ROWS 5000` z `FAKTURA` w podzapytaniu i dopiero potem `LEFT JOIN` do `KSEF_FAKTURA`.
4. Usunąć `DISTINCT` tylko wtedy, gdy potwierdzimy, że join do `KSEF_FAKTURA` nie zwielokrotnia faktur w danym widoku.
5. Warunek `COALESCE(K.DEMOMODE, 0) = 0` utrudnia użycie indeksów. Jeśli semantyka na to pozwala, testować wariant jawny: `(K.DEMOMODE = 0 OR K.DEMOMODE IS NULL)`.

### Priorytet 3: aktywne zapytania zakupowe
1. Na kopii bazy przetestować jeden wariant indeksu dla `ZAKUPY`, a nie dwa naraz:
   - `ID_FIRMA, DATA_WYST, NUMER` dla filtrów daty i sortowania,
   - albo `ID_FIRMA, RODZAJ_DOK, DATA_WYST, NUMER`, jeśli `RODZAJ_DOK` jest często filtrowany konkretną wartością, a nie wzorcem `%`.
2. Dla `ZAKPOZYCJA` przetestować indeks `ID_FIRMA, UWAGI, TEMP, IDVAT`, bo obserwowane zapytania agregujące używają dokładnie tych filtrów.
3. Dla joinów `ZAKPOZYCJA -> MAGAZYN` przetestować indeks `ZAKPOZYCJA(ID_MAGAZYN)`.

### Priorytet 4: wyszukiwanie tekstowe
1. W aplikacyjnych zapytaniach preferować `STARTING WITH` zamiast `LIKE '%tekst%'`, gdy użytkownik wpisuje początek numeru seryjnego, indeksu albo modelu.
2. Unikać `UPPER(kolumna) LIKE UPPER(?)` dla dużych list. Jeśli wielkość liter musi być ignorowana, testować indeksy wyrażeniowe `COMPUTED BY (UPPER(...))` na kopii bazy.
3. Dla `CMAIL` potwierdzono różnicę: wzorzec z `STARTING WITH` był około `8.5x` szybszy niż pierwotny wariant `LIKE/UPPER`.

### Priorytet 5: parametry Firebird i utrzymanie
1. Ustawić osobny punkt odniesienia po usunięciu długich transakcji: `gstat -h`, liczba aktywnych transakcji, lista najstarszych transakcji, czas czterech benchmarków przekraczających limit.
2. Dopiero po baseline sprawdzić tuning cache/temp w `firebird.conf`; obecnie nie znaleziono jawnych ustawień cache, sort i temp.
3. Rozważyć backup/restore z większym page size tylko po teście na kopii i tylko jeśli zysk jest mierzalny. Zmiana page size wymaga odtworzenia bazy, nie jest zmianą online.
4. Dodać cykliczny monitoring tylko do odczytu: `gstat -h` raz dziennie, `MON$TRANSACTIONS` i `MON$ATTACHMENTS` co kilka godzin, alert przy transakcji starszej niż 24 godziny.

## Proponowana kolejność wdrożenia
1. Wykonać `gbak` produkcji i odtworzyć kopię testową.
2. Na kopii odtworzyć cztery zapytania przekraczające limit i zapisać czasy bazowe.
3. Zamknąć długie transakcje na produkcji w kontrolowanym oknie i powtórzyć sam odczyt `gstat -h`.
4. Na kopii testować kandydatów indeksów pojedynczo z pomiarem `before/after`.
5. Dopiero po potwierdzonym zysku wdrażać wybrane indeksy na produkcji w oknie serwisowym.
6. Po każdym `CREATE INDEX` wykonać `SET STATISTICS INDEX` dla nowego indeksu i powtórzyć benchmark.

## Wniosek
Produkcja ma już wdrożone indeksy z poprzedniego testu, więc dalsze przyspieszenie nie polega na prostym „dodać brakujące indeksy”. Największym ryzykiem operacyjnym są długie transakcje blokujące sprzątanie wersji rekordów. Największy potencjał techniczny jest w korekcie zapytań faktur/KSeF oraz w przetestowaniu kilku nowych indeksów pod konkretne plany i aktywne zapytania aplikacji MS.
