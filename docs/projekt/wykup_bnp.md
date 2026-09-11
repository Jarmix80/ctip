# Wykup urządzenia BNP w module `/device`

## Cel procesu

Ekran `Wykup BNP` obsługuje przyjęcie urządzenia po zakończeniu wynajmu finansowanego przez BNP. Proces jest niezależny od istniejącego formularza `Przyjęcie PZ` i nie zmienia jego działania.

Docelowy zapis obejmuje:

1. identyfikację urządzenia po numerze seryjnym,
2. kontrolę klienta i kartotek magazynowych,
3. przygotowanie pozycji na magazynie `27` ze stanem `0`,
4. utworzenie dokumentu PZ z ilością `1`,
5. ustawienie docelowego `MASZYNA.EWIDENCJA` w formacie `WKP/...` według numeru KP lub serialu.

## Reguły identyfikacji

- Numer seryjny jest dopasowywany po znormalizowanej wartości do `MASZYNA.SERIAL` oraz `MASZYNA.SERIAL2`.
- Brak rekordu lub więcej niż jeden pasujący rekord blokuje zapis.
- Jeżeli źródłowe `MASZYNA.EWIDENCJA` ma poprawny format `KP/<numer>/...`, docelowa ewidencja zachowuje numer i dotychczasowe dopiski, zmieniając wyłącznie prefiks na `WKP/`. Domyślny indeks kartoteki pozostaje `WKP/<numer>/BNP`.
- Pusta lub niestandardowa ewidencja uruchamia wariant oparty na serialu dla każdego rodzaju urządzenia. Podgląd wyświetla ostrzeżenie `MASZYNA.EWIDENCJA nie ma formatu KP/<numer>/...`, wskazuje zastosowany serial i proponowane oznaczenie. Sama niespójność nie blokuje wykupu; ostrzeżenie pozostaje widoczne po przygotowaniu kartoteki.
- W wariancie serialowym oba pola otrzymują domyślnie `WKP/<serial>`, bez przenoszenia starej ewidencji ani jej dopisków. Kanoniczny serial pochodzi z `MASZYNA.SERIAL`, a przy pustym polu z `SERIAL2`. Jest normalizowany jak podczas wyszukiwania: wielkie litery, wyłącznie `A–Z` i `0–9`. Wyszukanie po alternatywnym numerze nie zmienia tożsamości wykupu.
- Operator może niezależnie edytować dopiski po `/` w docelowej ewidencji i indeksie, np. `/BNP` lub `/Serwis`. Bazowy numer KP albo serial jest zablokowany przed zmianą zarówno w formularzu, jak i podczas zapisu.
- Identyfikatory przekraczające 100 znaków są odrzucane; system nie skraca serialu ani dopisków do długości pola.
- Każda źródłowa ewidencja zaczynająca się od `WKP/` blokuje nowy wykup, również w wariancie serialowym. Ponowienie tego samego zakończonego żądania finalizacji zwraca istniejący PZ.

Przykłady wariantu serialowego:

| Serial | Dotychczasowa ewidencja | Domyślna ewidencja docelowa | Domyślny indeks |
| --- | --- | --- | --- |
| `B630205697` | `B630205697/BNP` | `WKP/B630205697` | `WKP/B630205697` |
| `C630099408` | `C630099408` | `WKP/C630099408` | `WKP/C630099408` |

## Kartoteka magazynowa

System pokazuje wszystkie kartoteki o tym samym bazowym identyfikatorze `WKP` niezależnie od magazynu, również z dopiskami po `/`. Fragment dłuższego identyfikatora nie jest dopasowaniem. Do finalizacji służy wyłącznie jedna kartoteka na magazynie `27`.

Jeżeli kartoteki brakuje, akcja `Stwórz pozycję ze stanem 0` zapisuje rekord `MAGAZYN` z następującymi zasadami:

- `ID_MAGAZYN=27`,
- `ILOSC=0`,
- `INDEKS=WKP/<numer KP>/BNP` lub `WKP/<serial>`, z opcjonalnym dopiskiem operatora,
- `NAZWA` z dokumentu BNP,
- `SERIAL=NIE`,
- `JM=szt.`,
- VAT `23%`,
- marka, model i `ID_MODEL` kopiowane z `MASZYNA`, jeżeli są dostępne.

Istniejąca kartoteka ze stanem `0` jest ponownie wykorzystywana, bez tworzenia duplikatu. Stan różny od `0` blokuje ponowne przyjęcie. Przygotowanie kartoteki nie zmienia ewidencji maszyny ani nie tworzy PZ.

## Finalizacja PZ

Operator podaje numer i datę dokumentu BNP, nazwę pozycji oraz cenę netto. Pole nazwy otrzymuje domyślną, edytowalną podpowiedź `Producent Model S/N:serial` z danych `MASZYNA`. Stawka VAT wynosi `23%`, a cena brutto jest wyliczana w interfejsie. Dostawca BNP jest rozpoznawany po NIP `1132061128`; identyfikator `KLIENT.ID_KLIENT` nie jest zapisany na stałe w kodzie.

Pola formularza mają zwiększoną wysokość i czcionkę. Akcja przygotowania kartoteki oraz finalizacja wykupu są prezentowane jako osobne, wyróżnione karty z opisem skutków zapisu. W profilu testowym trasa `/device/bnp-buyout/prototypes` udostępnia trzy nieaktywne makiety do porównania:

1. `Karty etapowe` – pełny proces na jednej stronie.
2. `Panel operacyjny` – wybrany układ docelowy z dokumentem po lewej i przyklejonym kafelkiem urządzenia po prawej.
3. `Kreator 3 kroków` – prowadzenie operatora etap po etapie.

Finalizacja działa w jednej transakcji Firebird:

1. blokuje zapis współdzieloną blokadą procesu PZ i synchronizuje wymagane generatory,
2. ponownie sprawdza maszynę, źródłową ewidencję (także pustą), bazowy numer KP lub serial, kartotekę i stan `0`,
3. blokuje kolizję indeksu, ponowne użycie numeru dokumentu oraz wcześniejsze PZ tego identyfikatora WKP,
4. aktualizuje `MAGAZYN.INDEKS` i `MAGAZYN.NAZWA`,
5. tworzy `ZAKUPY` oraz jedną `ZAKPOZYCJA` na magazynie `27`,
6. ustawia `MASZYNA.EWIDENCJA` na docelowe `WKP/...`,
7. potwierdza, że stan kartoteki po PZ wynosi dokładnie `1`.

Brak oczekiwanego stanu lub dowolny błąd powoduje wycofanie całej transakcji. Proces nie tworzy rekordu w tabeli `SERIAL`.

## API i audyt

- `GET /admin/device/bnp-buyout/lookup?serial=...` – podgląd urządzenia i kartotek; `identifier_mode` określa wariant `kp` lub `serial`, a `identifier_value` zawiera bazowy numer. Brak jednoznacznego urządzenia daje puste wartości tych pól. Lista `warnings` opisuje niespójności, a `blockers` zawiera przyczyny uniemożliwiające zapis.
- `POST /admin/device/bnp-buyout/catalog` – utworzenie kartoteki ze stanem `0`.
- `POST /admin/device/bnp-buyout/complete` – finalizacja PZ i ustawienie docelowej ewidencji WKP.

Oba żądania zapisu wymagają pola `expected_ewidencja`; dopuszczają pusty ciąg, ale nie `null` ani pominięcie pola. Serwis samodzielnie wyznacza regułę identyfikacji z danych maszyny i kontroluje aktualność ewidencji przed zmianą.

Odczyt wymaga sekcji `device`. Zapisy wymagają dodatkowo aktywnego powiązania konta CTIP z użytkownikiem Menadżera Serwisu i flagi `FB_ALLOW_WRITES=true`. Operacje są rejestrowane w audycie jako `device_bnp_catalog_create` oraz `device_bnp_buyout_complete`.

## Środowisko testowe

Lokalne uruchomienie korzysta z `.env.test`, bazy PostgreSQL `ctip_test`, lokalnego Firebirda oraz `SMS_TEST_MODE=true`. Zasoby `192.168.0.8` i `192.168.0.11` nie uczestniczą w testach modułu.

Testy serwisu obejmują oba warianty, dopiski, puste oznaczenie, `SERIAL2`, ponowienia i wycofanie transakcji. Testy API sprawdzają kontrakt pustej ewidencji i audyt. Testy formularza uruchamiają rzeczywiste funkcje JavaScript w lokalnym Node.js; przy jego braku są pomijane.
