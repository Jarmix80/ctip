# Proces sprzedazy urzadzenia w Menadzerze Serwisu (Firebird)

## Cel dokumentu
Dokument utrwala faktycznie zaobserwowany przebieg pracy Menadzera Serwisu na lokalnej bazie testowej Firebird i ma skrocic przyszle diagnozy przy analizie workflow:

- `klient`,
- `urzadzenie serwisowe`,
- `pozycja magazynowa`,
- `PZ`,
- `proforma`,
- `FV`.

Opis opiera sie na testach wykonanych 2026-03-13 na lokalnym aliasie Firebird `BAZAMS_TEST`.

## Srodowisko referencyjne
1. Lokalny serwer Firebird dziala w kontenerze `ctip-firebird-local` na porcie `3050`.
2. Alias `BAZAMS_TEST` wskazuje na plik:
   - `/home/marcin/projects/ctip/inbox/firebird/menadzer_serwisu.fdb`
3. Alias obsluguje rowniez warianty doklejane przez klienta MS:
   - `BAZAMS_TEST`
   - `BAZAMS_TEST\\BazaMS.fdb`
   - `BAZAMS_TEST/BazaMS.fdb`
4. Uwaga operacyjna:
   - aktualna `.env.test` wskazuje `FB_LOCAL_COPY_PATH=inbox/firebird/test_ms_local.fdb`,
   - to nie jest ten sam plik, ktory obsluguje alias `BAZAMS_TEST`,
   - przy analizie procesu handlowego nalezy laczyc sie jawnie do aliasu `BAZAMS_TEST`, inaczej latwo porownywac rozne bazy.

## Najwazniejszy wniosek
Proces handlowy nie opiera sie na tabeli `MASZYNA`.

Rzeczywisty lancuch danych wyglada tak:

`MODEL -> MAGAZYN -> ZAKUPY/ZAKPOZYCJA -> SERIAL -> FAKTURA/FPOZYCJA`

Tabela `MASZYNA` reprezentuje urzadzenie serwisowe / wdrozone, ale sama w sobie nie wystarcza do sprzedazy i nie jest glownym zrodlem danych dla proformy ani koncowej faktury.

## Znaczenie najwazniejszych tabel

### `MODEL`
- baza wzorcow urzadzen,
- przechowuje marke, model, grupe, gwarancje, przeglady i dodatkowe parametry handlowe/CPC,
- moze byc zapisywany jako twarde `ID_MODEL` w innych tabelach.

### `MASZYNA`
- urzadzenie serwisowe / ewidencyjne,
- przechowuje m.in. `SERIAL`, `EWIDENCJA`, `ID_KLIENT`, `ID_UMOWACPC`,
- nie jest glownym bytem sprzedazowym,
- w badanym flow nie byla wykorzystywana przez proforme ani finalna FV.

### `MAGAZYN`
- pozycja towarowa / magazynowa,
- to ten byt jest wykorzystywany przez `FPOZYCJA`,
- moze przechowywac `ID_MODEL`, `MARKA`, `MODEL`, ilosc i ceny,
- przy pustym `INDEKS` trigger potrafi wygenerowac indeks automatycznie.

### `SERIAL`
- osobna ewidencja konkretnych egzemplarzy,
- zapisuje `ID_MAGPOZ`, `ID_PZ`, `ID_FAKTURA`, `ID_MASZYNA`, `SERIAL`, `EWIDENCJA`,
- jest uzupelniana tylko wtedy, gdy przeplyw handlowy wymusi obsluge numeru seryjnego.

### `ZAKUPY` i `ZAKPOZYCJA`
- dokument i pozycje PZ/PW/WZ/RW/MM,
- aktualizuja stan `MAGAZYN.ILOSC`,
- dla `PZ` potrafia rowniez dopiac rekord `SERIAL`.

### `FAKTURA` i `FPOZYCJA`
- dokument handlowy i jego pozycje,
- proforma zapisuje sie do tych samych tabel co finalna FV,
- roznica wynika glownie z `FAKTURA.RODZAJ_DOK` i `FPOZYCJA.RODZAJ_DOK`.

## Faktycznie potwierdzony przebieg z 2026-03-13

### 1. Utworzenie klienta
Po zapisaniu klienta pojawil sie rekord:

- `KLIENT.ID_KLIENT=2895`
- nazwa: `ZANOX SPOLKA Z OGRANICZONA ODPOWIEDZIALNOSCIA`

Wniosek:
- utworzenie klienta dotyka tylko `KLIENT`,
- klient jest dalej wykorzystywany przez `FAKTURA`.

### 2. Utworzenie urzadzenia serwisowego
Po zapisaniu urzadzenia pojawil sie rekord:

- `MASZYNA.ID_MASZYNA=7629`
- `SERIAL=testsn`
- `EWIDENCJA=KP/100test`
- `ID_KLIENT=656`

Wnioski:
- samo `MASZYNA` nie wystarczylo do wystawienia proformy,
- urzadzenie serwisowe zostalo przypisane do klienta magazynowego, a nie do nowo utworzonego klienta handlowego,
- w tym konkretnym zapisie `MASZYNA.ID_MODEL` bylo puste.

### 3. Utworzenie pozycji magazynowej z modelu
Przy utworzeniu pozycji `IMCTEST` z przypisanym modelem `RICOH IMC 8000` powstal rekord:

- `MAGAZYN.ID_MAGAZYN_TABLE=18163`
- `INDEKS=18163` (wygenerowany automatycznie)
- `NAZWA=IMCTEST`
- `ID_MAGAZYN=28`
- `ID_MODEL=30002516`
- `MARKA=RICOH`
- `MODEL=IMC 8000`
- `SERIAL=TAK`

Wnioski:
- wybierany model nie tworzy nowego rekordu w `MODEL`,
- model zasila pozycje magazynowa i zapisuje sie jako twarde `ID_MODEL`,
- `NAZWA` pozycji handlowej moze byc niezalezna od nazwy wzorca `MODEL`.

### 4. PZ z numerem seryjnym
Przy `PZ / 73 / 2026` powstaly:

- `ZAKUPY.ID_ZAKUPY_TABLE=35961`
- `ZAKPOZYCJA.ID_ZAKPOZYCJA_TABLE=102632`
- aktualizacja `MAGAZYN.ID_MAGAZYN_TABLE=18163`
- `SERIAL.ID_SERIAL=12`

Zapis seriala:

- `SERIAL=test12345`
- `EWIDENCJA=Kp/test1`
- `ID_PZ=35961`
- `ID_MAGPOZ=18163`
- `ID_MAGAZYN=28`

Wnioski:
- `PZ` jest rzeczywistym miejscem zasilenia stanu magazynu,
- numer seryjny trafia do `SERIAL` tylko wtedy, gdy pozycja magazynowa wymaga numeru seryjnego i numer zostanie podany,
- sam zapis `MAGAZYN` bez `PZ` nie daje historii zakupu i nie tworzy `SERIAL`.

### 5. Proforma
Przy wystawieniu `2/proforma/2026` powstaly:

- `FAKTURA.ID_FAKTURA_TABLE=60397`
- `FPOZYCJA.ID_FPOZYCJA_TABLE=271599`

Najwazniejsze pola pozycji:

- `ID_MAGPOZ=18163`
- `ID_SERIAL=12`
- `NAZWA=IMCTEST`

Wnioski:
- proforma korzysta z `MAGAZYN`, a nie z `MASZYNA`,
- proforma potrafi przeniesc referencje do konkretnego `SERIAL`,
- proforma nie zmniejsza stanu magazynowego.

### 6. Finalna FV na podstawie proformy
Po wystawieniu `1259/KPSK/2026` powstaly:

- `FAKTURA.ID_FAKTURA_TABLE=60398`
- `FPOZYCJA.ID_FPOZYCJA_TABLE=271601`

Najwazniejsze pola pozycji:

- `ID_MAGPOZ=18163`
- `ID_SERIAL=NULL`

Efekt magazynowy:

- `MAGAZYN.ID_MAGAZYN_TABLE=18163`
- `ILOSC` spadlo z `1.0000` do `0.0000`
- `DATA_S=2026-03-13`

Wnioski:
- finalna FV domyka sprzedaz przez `MAGAZYN/FPOZYCJA`,
- w aktualnym procesie biznesowym numer seryjny nie jest wykorzystywany jako glowny nosnik finalnej sprzedazy,
- serial zostaje zapisany technicznie, ale nie prowadzi procesu do konca.

## Test techniczny bez UI (CTIP-AUTO-20260313-1260)
Na tej samej bazie wykonano tez kontrolowany test bezposrednich insertow SQL:

- klient: `KLIENT.ID_KLIENT=2896`
- urzadzenie serwisowe: `MASZYNA.ID_MASZYNA=7630`
- pozycja magazynowa: `MAGAZYN.ID_MAGAZYN_TABLE=18164`
- proforma: `FAKTURA.ID_FAKTURA_TABLE=60399`, numer `3/proforma/2026`
- FV: `FAKTURA.ID_FAKTURA_TABLE=60400`, numer `1260/KPSK/2026`

Najwazniejsza obserwacja techniczna:
- sam `INSERT` do `FPOZYCJA` dla finalnej FV nie zmniejszyl stanu magazynowego,
- `MAGAZYN.ID_MAGAZYN_TABLE=18164` nadal mial `ILOSC=1.0000`,
- `FPOZYCJA.ID_FPOZYCJA_TABLE=271603` miala `POBRANO=NULL`.

Dopiero nastepny `UPDATE` tej samej pozycji ustawil:
- `FPOZYCJA.POBRANO=1.0000`
- `MAGAZYN.ILOSC=0.0000`
- `MAGAZYN.DATA_S=2026-03-13`

Wniosek praktyczny:
- Menadzer Serwisu w normalnym workflow UI nie konczy sie na surowym insercie pozycji,
- przy automatyzacji bez UI trzeba odtworzyc co najmniej dwa kroki:
  1. zapis pozycji,
  2. aktualizacje pozycji uruchamiajaca logike zejscia magazynowego.

## Zakres zmian po aktualizacji KSeF
Z porownania biezacej bazy z historyczna dokumentacja struktury wynika, ze aktualizacja dotknela glownie obszaru KSeF i danych naglowkowych dokumentow.

### Nowe tabele
- `FAKINFOKSEF`
- `KSEF_CERTS`
- `KSEF_FAKTURA`
- `KSEF_SESSION`

### Nowe pola w istniejacych tabelach
- `FAKTURA.KOD_KRAJU`
- `ZAKUPY.KOD_KRAJU`
- `ZAKUPY.KSEFID`
- `ZAKUPY.KSEFTIME`
- `KOSZTORYS.KOD_KRAJU`
- `KOSZTORYS.KSEFID`
- `KOSZTORYS.KSEFTIME`
- `KOSZTY.KOD_KRAJU`
- `KOSZTY.KSEFID`
- `KOSZTY.KSEFTIME`
- `KOSZTY.KSEFXML`
- `FIRMA.KOD_KRAJU`
- `FIRMA.KSEF_LIC`
- `KLIENT.KOD_KRAJU`
- `ZLECENIE.KOD_KRAJU`

### Wniosek dla procesu sprzedazowego
Zmiany KSeF nie przebudowaly rdzenia pozycji sprzedazowych:

- `FPOZYCJA` nie ma pol KSeF,
- `ZAKPOZYCJA` nie ma pol KSeF,
- glowne rozszerzenia weszly do naglowkow dokumentow, danych kontrahenta i osobnych tabel integracyjnych KSeF.

Wniosek praktyczny:
- workflow `MODEL -> MAGAZYN -> ZAKUPY/ZAKPOZYCJA -> SERIAL -> FAKTURA/FPOZYCJA` pozostaje aktualny,
- KSeF trzeba traktowac jako dodatkowa warstwe wysylki i ewidencji dokumentu, a nie jako zmiane podstawowej logiki pozycji magazynowych i fakturowych.

## Co robia triggery i dlaczego to jest wazne

### `KLIENT / INS_KLIENT_RECORD`
- generuje `ID_KLIENT_TABLE`,
- generuje `ID_KLIENT`,
- ustawia `ID_FIRMA=1`, jesli pole jest `NULL`.

Skutek:
- testowe inserty klienta moga zostawic `ID_KLIENT_TABLE` i `ID_KLIENT` jako `NULL`.

### `MASZYNA / INS_MASZYNA_RECORD`
- generuje `ID_MASZYNA_TABLE`,
- generuje `ID_MASZYNA`.

Skutek:
- urzadzenie serwisowe mozna wstawic bez recznego liczenia identyfikatorow.

### `MAGAZYN / NEW_MAGAZYN_RECORD`
- generuje `ID_MAGAZYN_TABLE`,
- przy pustym `INDEKS` wpisuje `INDEKS = ID_MAGAZYN_TABLE`,
- przelicza ceny tylko w wybranych scenariuszach.

Skutek:
- brak indeksu nie jest bledem, ale trzeba swiadomie zdecydowac, czy chcemy indeks automatyczny, czy wlasny.

### `SERIAL / ADD_SERIAL_RECORD`
- generuje `ID_SERIAL`.

### `ZAKUPY / ADD_ZAKUPY`
- generuje `ID_ZAKUPY_TABLE`.

### `ZAKPOZYCJA / ADD_ZAKPOZYCJA`
- generuje `ID_ZAKPOZYCJA_TABLE`,
- dla `PZ` aktualizuje stan `MAGAZYN.ILOSC`,
- dla `PZ` uzupelnia `SERIAL.ID_PZ` i `SERIAL.ID_DOSTAWCA`.

Skutek:
- stan magazynowy po przyjeciu powstaje na poziomie `ZAKPOZYCJA`, nie samego naglowka `ZAKUPY`.

### `FAKTURA / ADD_FAKTURA_RECORD`
- generuje `ID_FAKTURA_TABLE`,
- aktualizuje `KLIENT.OSTATNIO`.

### `FPOZYCJA / INS_ID_FPOZYCJA_TABLE` oraz `ADD_FPOZYCJA`
- generuje `ID_FPOZYCJA_TABLE`,
- dla `RODZAJ_DOK='proforma'` konczy dzialanie przed aktualizacja stanu magazynowego,
- dla dokumentow sprzedazowych aktualizuje `MAGAZYN.ILOSC`,
- jezeli `ID_SERIAL` jest ustawione, zapisuje `SERIAL.ID_FAKTURA` i `SERIAL.ID_ODBIORCA`.

Skutek:
- proforma nie schodzi ze stanu,
- finalna FV schodzi ze stanu dopiero po etapie, ktory uruchamia logike `ADD_FPOZYCJA`,
- z testu technicznego wynika, ze sam surowy `INSERT` pozycji FV nie wystarcza i potrzebny jest dodatkowy `UPDATE`,
- z perspektywy triggera serial moglby zostac powiazany z faktura, ale w aktualnym procesie uzytkowym koncowa pozycja FV nie zawsze przenosi `ID_SERIAL`.

### `ZLECENIE / INS_ZLECENIE_RECORD` oraz `EDIT_ZLECENIE_RECORD`
- `INS_ZLECENIE_RECORD` generuje `ID_ZLECENIE_TABLE` i `ID_ZLECENIE`,
- przy braku `ROK` ustawia go na podstawie `DATA`,
- aktualizuje `KLIENT.OSTATNIO`,
- jezeli `SYNWP=1`, dopisuje wpis `('Zlecenie', 'I')` do tabeli `SYNCHRO`,
- `EDIT_ZLECENIE_RECORD` podbija `EDITCNT`, ustawia `EDITDATE` i `EDITTIME`,
- podczas edycji przelicza sumy z `ZPOZYCJA` do pol `CZESCI`, `MATERIALY`, `ROBOCIZNA`, `DOJAZD`, `NETTO`, `VAT`, `BRUTTO`, `WARTOSC_Z`,
- jezeli `SYNWP=1`, dopisuje wpis `('Zlecenie', 'U')` do tabeli `SYNCHRO`.

Skutek:
- zlecenie serwisowe i synchronizacja webpanelu opieraja sie na jednym rekordzie `ZLECENIE`,
- webpanel nie jest wykrywany po osobnej tabeli statusowej, tylko po `SYNWP` oraz wpisach w `SYNCHRO`,
- identyfikator `ID_ZLECENIE` nie jest globalnie unikalny; do identyfikacji trzeba uzywac co najmniej `ROK + ID_ZLECENIE`, a najlepiej `ID_ZLECENIE_TABLE`.

## Test zlecenia serwisowego i webpanelu

### Utworzenie zlecenia
Test dla:
- `KLIENT.ID_KLIENT=2896`
- `MASZYNA.ID_MASZYNA=7630`

Wynik:
- zlecenie utworzone w Menadzerze lub bezposrednim insertem trafia do `ZLECENIE`,
- wariant z `SYNWP=1` dopisuje do `SYNCHRO` rekord:
  - `FOR_TABLE='Zlecenie'`
  - `ACTION='I'`
- samo utworzenie zlecenia nie tworzy jeszcze rekordow w:
  - `ZADANIE`
  - `NOTES`
  - `PLIKI`
  - `UMOWA`
  - `UMOWACPC`
  - `SERIAL`
- samo utworzenie zlecenia nie aktualizuje tez `MASZYNA.ID_ZLECENIE`.

### Uzupelnienie danych technika przed wykonaniem
Na etapie przypisania technika, daty zakonczenia, licznikow i zaznaczenia wysylki na webpanel zmienia sie nadal tylko `ZLECENIE`:
- `TECHNIK`
- `TERMIN`
- `GODZINA_Z`
- `LICZNIK`, `LICZNIKA3`, `LICZNIK_TOTAL`
- `DATA_START`, `CZAS_START`, `CZAS_STOP`, `CZAS_TOTAL`, `CZAS_NAPRAWY`
- `SYNWP`
- `EDITCNT`, `EDITDATE`, `EDITTIME`
- `OPERATOR`

Dodatkowo przy `SYNWP=1` trigger dopisuje do `SYNCHRO` rekord:
- `FOR_TABLE='Zlecenie'`
- `ACTION='U'`

Wnioski:
- sam etap planowania / przydzialu technika nie dotyka `MASZYNA`, `SERIAL`, `ZADANIE`, `PLIKI`, `NOTES`, `UMOWA` ani `UMOWACPC`,
- wpis w webpanelu jest sygnalizowany aktualizacja `ZLECENIE`, a nie osobnym obiektem procesu.

### Uzupelnienie wykonania po stronie technika
Test wykonany dla zlecenia `ID_ZLECENIE_TABLE=80460`, `ID_ZLECENIE=15460`, `ROK=2026`.

Po zapisaniu:
- `WYKONANIE='test wykonania'`
- `UWAGIS='test na przyszlosc'`
- `LICZNIK=6`
- `LICZNIKA3=9`
- `LICZNIK_TOTAL=15`
- `EDITCNT=2`
- `SYNWP=1`

Dodanie czesci serwisowej utworzylo rekord w `ZPOZYCJA`:
- `ID_ZPOZYCJA_TABLE=156752`
- `ID_KLIENT=2896`
- `ID_MASZYNA=7630`
- `ID_ZLECENIE=15460`
- `ROK=2026`
- `RODZAJ='2. Towar inny'`
- `INDEKS='TL-8G108PE'`
- `NAZWA='TP-LINK Switch TL-8G108PE'`
- `ILOSC=1`
- `ID_MAGPOZ=16089`
- `CENA=0`
- `WARTOSC=0`
- `CENA_Z=214.5900`
- `WARTOSC_Z=214.5900`

Wnioski:
- czesci przypisane do zlecenia sa ewidencjonowane w `ZPOZYCJA`,
- `EDIT_ZLECENIE_RECORD` przelicza z `ZPOZYCJA` pole `WARTOSC_Z` w naglowku zlecenia,
- w testowym wariancie po dodaniu czesci nie powstal rekord w `MZ`,
- nadal nie powstaly rekordy w `ZADANIE`, `NOTES`, `PLIKI`, `UMOWA`, `UMOWACPC` ani `SERIAL`.

### Rozroznienie pol formularza zlecenia
W praktyce ekran uzytkownika rozroznia co najmniej trzy warstwy:
- `status zlecenia` -> zapis do `ZLECENIE.STAN`,
- `rodzaj zlecenia` -> na testach odpowiadal wartosci `ZLECENIE.RODZAJ_US='Płatne'` oraz `ZLECENIE.TYP_US=1`,
- `rodzaj uslugi` -> etykieta widoczna operatorowi, ale nie zostala znaleziona jako osobne pole tekstowe w testowanym rekordzie `ZLECENIE`.

Wniosek praktyczny:
- nie wolno utozsamiac etykiet z formularza 1:1 z nazwami kolumn Firebird,
- dla automatyzacji trzeba najpierw ustalic slownik mapowania wartosci UI -> pola `ZLECENIE`.

### Etap `zrealizowane -> FV -> zamkniecie zlecenia`
W praktyce operacyjnej po wykonanym serwisie serwisant najpierw zmienia status zlecenia na `zrealizowane`, a dopiero potem wykonywane sa dalsze czynnosci:
- wystawienie FV na podstawie zlecenia,
- zamkniecie zlecenia.

Test wykonany dla:
- `ZLECENIE.ID_ZLECENIE_TABLE=80460`
- `ID_ZLECENIE=15460`
- FV `1261/KPSK/2026`

Po wykonaniu tych krokow:
- powstal rekord `FAKTURA`:
  - `ID_FAKTURA_TABLE=60401`
  - `NUMER='1261/KPSK/2026'`
  - `RODZAJ_DOK='KPSK'`
  - `ID_KLIENT=2896`
  - `ID_MASZYNA=7630`
  - `ID_ZLECENIE=15460`
  - `UWAGI='Zlecenie nr: 15460/2026; Ricoh IMC 3010 ; '`
- powstala pozycja `FPOZYCJA`:
  - `ID_FPOZYCJA_TABLE=271605`
  - `ID_FAKTURA=60401`
  - `ID_ZLECENIE=15460`
  - `ROK_ZLECENIA=2026`
  - `ID_MASZYNA=7630`
  - `ID_MAGPOZ=16089`
  - `INDEKS='TL-8G108PE'`
  - `NAZWA='TP-LINK Switch TL-8G108PE'`
  - `POBRANO=1`
  - `CENA_NETTO=0`
  - `WARTOSC_NETTO=0`
  - `CENA_Z=214.5900`
  - `WARTOSC_Z=214.5900`
- naglowek `ZLECENIE` zostal zaktualizowany:
  - `ID_FAKTURA=60401`
  - `FAKTURA='1261/KPSK/2026'`
  - `STAN='Z'`
  - `DATA_Z='2026-03-16'`
  - `OPERATOR` dostal dopisek `Zamknął :Marcin`
  - `EDITCNT` wzrosl do `6`
- w `SYNCHRO` liczba wpisow `('Zlecenie', 'U')` dla `ID_ROW=80460` wzrosla z `2` do `6`

Nie powstaly nadal:
- `MZ`
- `NOTES`
- `PLIKI`
- `SERIAL`

Wazny efekt uboczny:
- magazynowa pozycja czesci `MAGAZYN.ID_MAGAZYN_TABLE=16089` zeszla z `ILOSC=0` do `ILOSC=-1`,
- ustawilo sie tez `DATA_S='2026-03-16'`.

Wniosek praktyczny:
- wystawienie FV ze zlecenia serwisowego schodzi ze stanu magazynowego przez `FPOZYCJA -> MAGAZYN`,
- jezeli stan poczatkowy czesci wynosi `0`, system moze zejsc na stan ujemny,
- samo zamkniecie zlecenia nie tworzy nowego bytu workflow; finalny stan procesu nadal zapisuje sie glownie w `ZLECENIE`, `FAKTURA`, `FPOZYCJA` i `SYNCHRO`.

## Reguly diagnostyczne na przyszlosc
1. Nie zakladac, ze `MASZYNA` i `MAGAZYN` oznaczaja to samo.
2. Nie zakladac, ze samo utworzenie `MASZYNA` pozwoli wystawic proforme.
3. Jesli problem dotyczy sprzedazy, sprawdzac najpierw:
   - `MAGAZYN`
   - `ZAKUPY`
   - `ZAKPOZYCJA`
   - `FAKTURA`
   - `FPOZYCJA`
4. Jesli problem dotyczy gwarancji oryginalnosci po numerze seryjnym, sprawdzac:
   - czy pozycja magazynowa ma `SERIAL='TAK'`,
   - czy `PZ` zapisalo rekord w `SERIAL`,
   - czy proforma zapisala `FPOZYCJA.ID_SERIAL`,
   - czy finalna FV zachowala `ID_SERIAL`.
5. Jesli problem dotyczy modelu, sprawdzac zawsze:
   - `MAGAZYN.ID_MODEL`,
   - `MAGAZYN.MARKA`,
   - `MAGAZYN.MODEL`,
   - `MODEL.ID_MODEL`.
6. Jesli problem dotyczy zlecenia serwisowego lub webpanelu, sprawdzac najpierw:
   - `ZLECENIE.SYNWP`,
   - `SYNCHRO` dla `FOR_TABLE='Zlecenie'`,
   - `ZPOZYCJA` dla czesci przypisanych do zlecenia,
   - dopiero potem `MZ`, `PLIKI`, `NOTES`, `MASZYNA`.

## Zapytania kontrolne

### Ostatnie dokumenty handlowe
```sql
SELECT FIRST 20 ID_FAKTURA_TABLE, NUMER, RODZAJ_DOK, ID_KLIENT, ID_MAGAZYN, SUMA_BRUTTO
FROM FAKTURA
ORDER BY ID_FAKTURA_TABLE DESC;
```

### Ostatnie pozycje faktur z powiazaniem do magazynu i seriala
```sql
SELECT FIRST 20
    ID_FPOZYCJA_TABLE,
    ID_FAKTURA,
    NUMER,
    RODZAJ_DOK,
    ID_MAGAZYN,
    ID_MAGPOZ,
    ID_SERIAL,
    INDEKS,
    NAZWA,
    ILOSC,
    POBRANO
FROM FPOZYCJA
ORDER BY ID_FPOZYCJA_TABLE DESC;
```

### Ostatnie przyjecia magazynowe
```sql
SELECT FIRST 20
    z.ID_ZAKUPY_TABLE,
    z.NUMER,
    z.RODZAJ_DOK,
    zp.ID_ZAKPOZYCJA_TABLE,
    zp.ID_MAGAZYN,
    zp.ID_SERIAL,
    zp.SERIAL,
    zp.EWIDENCJA
FROM ZAKUPY z
JOIN ZAKPOZYCJA zp
  ON zp.ID_ZAKUPY = z.ID_ZAKUPY_TABLE
ORDER BY z.ID_ZAKUPY_TABLE DESC, zp.ID_ZAKPOZYCJA_TABLE DESC;
```

### Ostatnie numery seryjne
```sql
SELECT FIRST 20
    ID_SERIAL,
    ID_MAGPOZ,
    ID_MAGAZYN,
    ID_PZ,
    ID_FAKTURA,
    ID_MASZYNA,
    SERIAL,
    EWIDENCJA,
    DATA_ZAKU,
    DATA_SPRZ
FROM SERIAL
ORDER BY ID_SERIAL DESC;
```

### Ostatnie zlecenia serwisowe
```sql
SELECT FIRST 20
    ID_ZLECENIE_TABLE,
    ID_ZLECENIE,
    ROK,
    ID_KLIENT,
    ID_MASZYNA,
    STAN,
    RODZAJ_US,
    TYP_US,
    TECHNIK,
    SYNWP,
    PROBLEM,
    WYKONANIE
FROM ZLECENIE
ORDER BY ID_ZLECENIE_TABLE DESC;
```

### Kolejka synchronizacji webpanelu
```sql
SELECT FIRST 20
    ID_SYNC,
    ID_ROW,
    FOR_TABLE,
    ACTION,
    SYNCHRO
FROM SYNCHRO
WHERE FOR_TABLE = 'Zlecenie'
ORDER BY ID_SYNC DESC;
```

### Czesci przypisane do zlecenia
```sql
SELECT FIRST 20
    ID_ZPOZYCJA_TABLE,
    ID_ZLECENIE,
    ROK,
    ID_MASZYNA,
    ID_MAGPOZ,
    RODZAJ,
    INDEKS,
    NAZWA,
    ILOSC,
    CENA,
    CENA_Z,
    WARTOSC_Z
FROM ZPOZYCJA
ORDER BY ID_ZPOZYCJA_TABLE DESC;
```

## Implikacje dla CTIP
1. Modul `Obsluga umow` nie powinien probowac zbudowac calego workflow sprzedazowego wyłącznie na `MASZYNA`.
2. Dla procesu handlowego trzeba rozdzielic:
   - urzadzenie serwisowe (`MASZYNA`),
   - pozycje magazynowa (`MAGAZYN`),
   - konkretny egzemplarz (`SERIAL`),
   - dokument handlowy (`FAKTURA/FPOZYCJA`).
3. Jesli celem jest zgodnosc z obecnym sposobem pracy uzytkownikow, glownym bytem sprzedazowym pozostaje `MAGAZYN`.
4. Jesli celem jest wzmocnienie kontroli po numerze seryjnym, trzeba to traktowac jako rozszerzenie procesu biznesowego, a nie jako funkcje, ktora juz dzis spina finalna FV.
