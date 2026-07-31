# Wykup urządzenia BNP w module `/device`

## Cel procesu

Menu `Wykup BNP` obsługuje przyjęcie urządzenia po zakończeniu wynajmu finansowanego przez BNP. Proces jest niezależny od istniejącego formularza `Przyjęcie PZ` i nie zmienia jego działania.

Docelowy zapis obejmuje:

1. identyfikację urządzenia po numerze seryjnym,
2. kontrolę klienta i kartotek magazynowych,
3. przygotowanie pozycji na magazynie `27` (`Urządzenia Wynajem`) ze stanem `0`,
4. utworzenie dokumentu PZ z ilością `1`,
5. zmianę `MASZYNA.EWIDENCJA` z `KP/...` na `WKP/...`.

## Reguły identyfikacji

- Numer seryjny jest dopasowywany po znormalizowanej wartości do `MASZYNA.SERIAL` oraz `MASZYNA.SERIAL2`.
- Brak rekordu lub więcej niż jeden pasujący rekord blokuje zapis.
- Źródłowe `MASZYNA.EWIDENCJA` musi mieć format `KP/<numer>/...`.
- Docelowa ewidencja zachowuje numer i wszystkie dotychczasowe dopiski, zmieniając wyłącznie prefiks na `WKP/`.
- Cyfrowy numer KP jest zablokowany przed zmianą.
- Domyślny indeks kartoteki ma format `WKP/<numer>/BNP`; operator może korygować dopiski po numerze.

## Kartoteka magazynowa

System pokazuje wszystkie kartoteki o tym samym numerze `WKP` niezależnie od magazynu. Do finalizacji służy wyłącznie jedna kartoteka na magazynie `27`.

Jeżeli kartoteki brakuje, akcja `Stwórz pozycję ze stanem 0` zapisuje rekord `MAGAZYN` z następującymi zasadami:

- `ID_MAGAZYN=27`,
- `ILOSC=0`,
- `INDEKS=WKP/<numer>/BNP` albo skorygowany dopisek operatora,
- `NAZWA` z dokumentu BNP,
- `SERIAL=NIE`,
- `JM=szt.`,
- VAT `23%`,
- marka, model i `ID_MODEL` kopiowane z `MASZYNA`, jeżeli są dostępne.

Istniejąca kartoteka ze stanem różnym od `0` blokuje ponowne przyjęcie.

## Finalizacja PZ

Operator podaje:

- numer dokumentu BNP,
- datę dokumentu,
- nazwę pozycji,
- cenę netto.

Pole nazwy otrzymuje domyślną, edytowalną podpowiedź `Producent Model S/N:serial` z danych `MASZYNA`. Stawka VAT wynosi `23%`, a cena brutto jest wyliczana w interfejsie. Dostawca BNP jest rozpoznawany po NIP `1132061128`; identyfikator `KLIENT.ID_KLIENT` nie jest zapisany na stałe w kodzie.

Finalizacja działa w jednej transakcji Firebird:

1. ponownie sprawdza maszynę, źródłowe KP, kartotekę i stan `0`,
2. blokuje kolizję indeksu, ponowne użycie numeru dokumentu oraz wcześniejsze PZ tego numeru KP,
3. aktualizuje `MAGAZYN.INDEKS` i `MAGAZYN.NAZWA`,
4. tworzy `ZAKUPY` oraz jedną `ZAKPOZYCJA` na magazynie `27`,
5. ustawia `MASZYNA.EWIDENCJA` na docelowe `WKP/...`,
6. potwierdza, że stan kartoteki po PZ wynosi dokładnie `1`.

Brak oczekiwanego stanu lub dowolny błąd powoduje wycofanie całej transakcji. Proces nie tworzy rekordu w tabeli `SERIAL`, zgodnie z historycznymi wykupami BNP.

## API i audyt

- `GET /admin/device/bnp-buyout/lookup?serial=...` – podgląd urządzenia i kartotek.
- `POST /admin/device/bnp-buyout/catalog` – utworzenie kartoteki ze stanem `0`.
- `POST /admin/device/bnp-buyout/complete` – finalizacja PZ i zmiana KP na WKP.

Zapisy wymagają roli `admin` albo `operator`, uprawnienia sekcji `generator` oraz aktywnej flagi `FB_ALLOW_WRITES=true`. Operacje są rejestrowane w audycie jako:

- `device_bnp_catalog_create`,
- `device_bnp_buyout_complete`.

## Środowisko testowe

Lokalne uruchomienie korzysta z `.env.test`, bazy PostgreSQL `ctip_test`, lokalnego Firebirda oraz `SMS_TEST_MODE=true`. Zasoby `192.168.0.8` i `192.168.0.11` nie uczestniczą w testach modułu.
