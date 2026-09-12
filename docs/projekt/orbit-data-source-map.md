# KP ORBIT — odczyt MS

## Kontrakt integracyjny

`load_orbit_snapshot(machine_ids=None, *, only_machine_ids=None)` wykonuje wyłącznie odczyt Firebird przez
`app.services.telemetry.sources.firebird_connection`. Wywołuje go zadanie synchronizacji,
a nie każde żądanie HTTP. Rodzic przekazuje w `machine_ids` wszystkie wcześniej znane
logiczne `MASZYNA.ID_MASZYNA` z PostgreSQL. Parametr rozszerza aktywną flotę, nie filtruje jej.
Wynik ma postać `{devices: [...], facts: [...], complete: true}`. Błąd odczytu przerywa
wywołanie; nie wolno uznać częściowego wyniku za kompletną synchronizację.
Jawny `only_machine_ids` ogranicza flotę i znane identyfikatory do pilota; przekazane
ID są sprawdzane również po zawieszeniu umowy. Pusta lista daje pusty wynik bez
połączenia. Dokument wspólny może wymagać odczytu powiązań innych maszyn, ale wynik
pilota zawiera wyłącznie wskazane urządzenia i ich fakty.

Urządzenie: `id="ms:<ID_MASZYNA>"`, `ms_machine_id`, `serial`, `model`, `model_id`,
`customer`, `customer_id`, `contract_id`, `status=active|suspended|scrapped|review`, `data`.
Brak aktywnej umowy oznacza `suspended`. Nieodnalezienie znanej maszyny oznacza `review`,
nie usunięcie urządzenia. Klient i magazyn są propozycjami źródła w
`data.current_customer` i `data.currentwarehouse`. Jawne identyfikatory złomowania
pochodzą z `Settings.shipping_orbit_scrap_customer_ids` oraz
`Settings.shipping_orbit_scrap_warehouse_ids`; puste listy nie złomują żadnych urządzeń.
Jeżeli kartoteka wskazuje dodatnie `ID_UMOWACPC`, ale rekordu umowy nie ma, stan
urządzenia wynosi `review` z problemem `missing_current_contract`. Brak rekordu nie
stanowi dowodu zawieszenia lub zakończenia umowy.

Fakt: `external_key`, `device_id`, `kind`, `observed_at`, `time_precision`, `title`,
`status`, `data`. `observed_at` to ISO daty/czasu albo `null`, nigdy czas importu.
Precyzja: `date`, `second`, `month`, `unknown`. CPC ma `observed_at=null`,
`time_precision=month` oraz jawny `data.period={year,month,start,end}`.

Wspólne `data`: `schema_version=1`, `source="ms"`, `status=confirmed|missing|conflict`,
`title`, `issues=[kod]`, `contract_id`, `customer_id`, `is_contract`, `raw`.
Powiązania z dokumentami są płaskie: `order_id`, `order_year`, `order_table_id`,
`document_id`, `document_line_id`, `invoice_id`, `invoice_line_id`.
Kwoty i ilości mają typ tekstu dziesiętnego albo `null`; identyfikatory są liczbami.
Umowa CPC ma `contract_number=UMOWACPC.UMOWA`, `starts_at`, `ends_at`.
Faktura ma `allocation_status`, `net_value`, `assigned_net_value` i `lines`.
`net_value` jest dostępne wyłącznie przy potwierdzonym przypisaniu; `assigned_net_value`
może pokazywać udokumentowany fragment, lecz nie służy do sumowania przychodów, jeśli
status faktu jest inny niż `confirmed`. Wszystkie kwoty linii należą do części finansowej.

Pola kosztu kontraktowego: `purchase_price`, `issued_quantity`, `returned_quantity`,
`purchase_value`, `document_purchase_price`, `zero_cost_confirmed`, `currency`,
`purchase_cost={status,net,unit_net,quantity,basis}`. Brak lub konflikt nie staje się
zerem. `returned_quantity=null` oznacza brak potwierdzonej interpretacji zwrotu.
Waluta RW/WZ pozostaje `null`, ponieważ te tabele nie zawierają jej kodu.
`purchase_value` jest kosztem potwierdzonej pozycji, nie pełnym kosztem zlecenia/umowy.
Zgodne zera cen zakupu w obu tabelach nie dowodzą bezpłatnego nabycia: przy braku
osobnego potwierdzenia źródło zgłasza `unconfirmed_zero_purchase_cost` i pozostawia
`zero_cost_confirmed=false`. Jawnie płatny typ zlecenia nie jest kosztem umowy tylko
dlatego, że urządzenie ma obecnie aktywną umowę.
Obecny kalkulator rodzica wymaga także potwierdzonej waluty PLN i ilości zwrotów:
wartości `null` blokują końcowy koszt nawet przy uzgodnionej wartości samego wydania.
Nie wolno zastępować tych braków domyślnym PLN lub zerem bez osobnej decyzji opartej
na potwierdzonym źródle. Oryginalne nazwy cen w `raw` pozostają `CENA_Z/WARTOSC_Z`.

`raw={TABELA:[rekord,...]}` zachowuje oryginalną strukturę kolumn, daty ISO i dokładne
liczby dziesiętne. BLOB-y, tablice Firebird i pola haseł nie są pobierane. `raw` jest
wyłącznie materiałem wewnętrznym administratora: rodzic zapisuje go w `Reading.payload`
źródła `ms_orbit`, a publiczne `orbit_event.data` buduje z jawnej listy dozwolonych pól.
Finanse należy wydzielić według uprawnień; nie wolno publikować całego `data` ani `raw`.
Nagłówek surowej faktury występuje tylko w `raw`, a jego suma nie jest przychodem maszyny.

## CPC i deduplikacja

Fakt CPC ma `external_key="CPC:<ID_CPC_TABLE>"` i `data.raw.CPC=[pełny_rekord]`.
Rodzic może przekazać ten rekord do istniejącego `cpc_reading`, zachowując namespace
`ms_cpc` i aktualny mechanizm wersjonowania. Snapshot nie rozszerza istniejącego
importera CPC ani nie zapisuje jego rekordów. Projekcja dostaje okres, liczniki i
opłaty; nie należy dodawać opłat CPC drugi raz do przychodów z faktur.

## Zweryfikowane źródła i klucze

Zweryfikowano lokalne kopie `integrations/bazams/docs/structure` oraz
`docs/firebird/external/bazams/docs/structure` w głównym repozytorium. W worktree
ich odpowiednikiem jest również `docs/firebird/knowledge/firebird_ms_knowledge.json`.
Odczyty kontrolne wykonano 2026-09-12 wyłącznie w lokalnym Firebird przez
`ctip-test-web-1`, w transakcji tylko do odczytu.

| Obszar | Rzeczywista relacja | Ograniczenie |
| --- | --- | --- |
| Tożsamość | `MASZYNA.ID_MASZYNA`, PK `ID_MASZYNA_TABLE` | Nie są zamienne. |
| Umowa CPC | `MASZYNA/CPC.ID_UMOWACPC → UMOWACPC.ID_UMOWACPC_TABLE` | Bieżąca maszyna nie stanowi pełnej historii przypisań. |
| Historia CPC | `CPC.ID_MASZYNA`, PK `ID_CPC_TABLE` | Odczyt wszystkich okresów, bez limitu trzech lat. |
| Inne umowy | `UMOWA.ID_MASZYNA`, PK `ID_UMOWA` | Oddzielny rodzaj umowy, bez utożsamiania z ID umowy CPC. |
| Zlecenie i części | `ZPOZYCJA.(ID_ZLECENIE,ROK) → ZLECENIE.(ID_ZLECENIE,ROK)` | Lokalna kopia zawiera duplikat pary; nie można wybierać pierwszego zlecenia. |
| RW/WZ | `ZAKUPY.(ID_ZLECENIE,ROK_ZLECENIA)` oraz `ZLECENIE.ID_RW/ID_WZ` | Niespójność wskazań wymaga konfliktu. |
| Pozycje RW/WZ | `ZAKPOZYCJA.ID_ZAKUPY → ZAKUPY.ID_ZAKUPY_TABLE` albo starszy `(NUMER,ID_FIRMA)` | Klucz numerowy potwierdza `DEL_ZAKUPY`; wymagane jednoznaczne przypisanie i zgodność typu/dat. `ID_MAGAZYN` pozycji jest ID kartoteki. |
| Koszt zakupu | `ZPOZYCJA.ID_MAGPOZ ↔ ZAKPOZYCJA.ID_MAGAZYN` w tym samym zleceniu | Uzgadniane `CENA_Z`, `WARTOSC_Z`, ilości i realizacja; nie `CENA/WARTOSC`. |
| Faktury | `CPC.ID_FAKTURA → FAKTURA.ID_FAKTURA_TABLE`, `FPOZYCJA.ID_FAKTURA` | Faktura może obejmować wiele urządzeń. |
| Linie faktur | `FPOZYCJA.ID_MASZYNA`, para zlecenia | Opis tekstowy nie jest kluczem relacji. |
| Korekty | `FAKTURA.NR_KORY ↔ FAKTURA.NUMER` z kontrolą firmy | Relacja numerowa nie rozstrzyga semantyki kwot. |
| Magazyn | `SERIAL.ID_MAGPOZ → MAGAZYN.ID_MAGAZYN_TABLE`; fizyczny `MAGAZYN.ID_MAGAZYN` | Dane seryjne bywają niepełne, więc propozycja może być nieznana. |

W lokalnej kopii 173758 wierszy CPC obejmowało 69 odwołań do nieistniejącej umowy.
Wszystkie 41672 pozycje RW i 1883 WZ miały `POBRANO=ILOSC`, przy czym trzy RW miały
ilość zerową. Samo planowane `ZPOZYCJA.ILOSC` nie dowodzi wydania.
130 korekt wskazywało oryginał przez `NR_KORY`; żaden z 123 dodatnich `KORID` nie
łączył się z PK faktury, a żaden z 426 wierszy korekt nie miał skutecznego połączenia
`ID_OLD → ID_FPOZYCJA_TABLE`. Nie wolno przyjąć tych pól jako potwierdzonych kluczy
ani odejmować kwot korekty bez znajomości ich semantyki.

## Ograniczenia eksploatacyjne

`complete=true` potwierdza wyczerpanie stron odczytu, nie pełną jakość historii ani
rozliczenie wszystkich kosztów. Połączenie wspólne z telemetrią używa transakcji
READ COMMITTED RO; nie gwarantuje jednego obrazu wszystkich tabel przy równoległej
edycji MS. Nie ma osobnej tabeli historii przypisań CPC; źródłem historii są zachowane
okresy, bieżąca kartoteka i umowy. Rekordy bez daty należy zachować z oznaczeniem braku.
Statusy konfliktów i braków mają blokować udawanie pełnej marży/kosztu w projekcji.

Odczyt używa porcji kursora po 500 rekordów, partii po 500 pojedynczych ID
i po 100 par kluczy. Duże tabele są
czytane po zebranych identyfikatorach i parach numer/rok. Wstępne zapytanie faktur
z wieloma skorelowanymi `EXISTS` przekraczało 20-sekundowy limit połączenia; zastąpiono
je odczytami partiami bez zmiany limitu ani indeksów źródła.

Indeksy urządzenie → umowy oraz umowa → urządzenia powstają raz na migawkę.
Przypisanie faktury nie skanuje ponownie CPC całej floty. Indeksy należą wyłącznie
do danego importu i nie pozostają w globalnym cache między przebiegami.

Pilot odczytowy 2026-09-12 dla testowego ID `105`: 1,30 s, `complete=true`,
1 aktywne urządzenie, 24 fakty (16 CPC, 1 umowa, 7 zleceń), wszystkie `confirmed`.
Brak RW/WZ oznacza brak potwierdzonych wartości wydania. Najwolniejszy odczyt tabeli
FPOZYCJA trwał 0,312 s. Testowe aktywne ID z RW i dodatnią historyczną ceną zakupu:
`4050`, `4696`, `5914`. Nie są to gwarancje pełnej jakości finansowej tych urządzeń.

Pilot przed rozpoznaniem starszego klucza linii `4050`: 6,32 s, 1 aktywne urządzenie i `complete=true`; 235 faktów
(102 CPC, 2 umowy, 30 zleceń, 79 faktur, 22 pozycje lub zgłoszenia brakujących linii
RW/WZ). Jakość: 125 `confirmed`, 100 `missing`, 10 `conflict`; 0 potwierdzonych
wartości wydań. Stwierdzono 79 faktur bez jednoznacznego przypisania linii, 11
dokumentów bez linii, 9 przypadków brakujących dowodów ceny zakupu, 8 powielonych
okresów CPC, 1 niejednoznaczną część i 1 rozbieżność historycznej wartości zakupu.
Liczby problemów mogą się nakładać. Odczyt zachowuje te dane, ale nie tworzy z nich
fikcyjnych kosztów zerowych. Najwolniejszy odczyt FPOZYCJA trwał 1,824 s.

Polecenia lokalnego pilota korzystały z
`CTIP_ENV_FILE=/home/marcin/projects/ctip/.env.test`, `PGHOST=172.28.252.3`,
`FB_HOST=172.28.252.5`. Są to adresy kontenerów potwierdzone dla tego uruchomienia,
nie stałe adresy wpisane w implementację. Nieaktualne adresy w pliku środowiskowym
worktree nie zostały zmienione. Wykonano 35 izolowanych testów źródła i 11 testów
istniejącej interpretacji CPC; Ruff i Black poprawne. `pre-commit run --all-files`
wykonano z kontrolą Black/Ruff bez zapisu, aby nie zmieniać plików innych wykonawców.

## Końcowy kontrakt kosztu wydania

`data.issue_purchase_cost` jest osobnym wynikiem potwierdzenia pojedynczego wydania.
Poniższy przykład jest syntetyczny:

```json
{
  "status": "known",
  "amount": "240.0000",
  "quantity": "2.0000",
  "unit_price": "120.0000",
  "currency": null,
  "currency_status": "unverified",
  "currency_scope": "ms:company:1",
  "scope": "before_returns",
  "returns_status": "unverified",
  "basis": "ZPOZYCJA.CENA_Z/WARTOSC_Z + ZAKPOZYCJA.CENA_Z/WARTOSC_Z",
  "issues": []
}
```

Dopuszczalne statusy: `known`, `unknown`, `conflict`. Warunki `known`: potwierdzone
powiązanie dokumentu, urządzenia, klienta i umowy, jednoznaczna pozycja zlecenia,
dodatnia realizacja linii RW/WZ oraz zgodne historyczne `CENA_Z/WARTOSC_Z` obu tabel.
Brak ceny dokumentu nadal blokuje potwierdzenie; nie stosuje się ceny sprzedaży ani
obecnej ceny kartoteki magazynowej.

Pola dla kalkulatora: `data.role="contract"`,
`issued_quantity_basis="ZAKPOZYCJA.POBRANO"`, `returned_quantity=null`,
`returned_quantity_basis=null`, `returns_status="unverified"`.
`quantity_reconciliation` opisuje oddzielnie uzgodnienie zbiorczej ilości zlecenia.
Puste `ZPOZYCJA.POBRANO` nie unieważnia dodatniej realizacji potwierdzonej na linii
RW/WZ. Daje `quantity_reconciliation.status="unknown"` i problem
`missing_order_taken_quantity`, a nie brak historycznej ceny. Jeżeli zbiorcza ilość
jest dostępna i przeczy dokumentom, koszt pozostaje zablokowany jako konflikt.

Rodzic pobiera znane wartości wydań z `issue_purchase_cost`, niezależnie od
kalkulatora końcowego bilansu po zwrotach. W interfejsie należy używać opisu
„Wartość wydania przed zwrotami; waluta niepotwierdzona”. `currency_scope` identyfikuje
ewidencję firmy MS, nie potwierdza waluty. Nie wolno oznaczać tych wartości jako PLN,
kosztu po zwrotach ani włączać do sumy pieniężnej wymagającej potwierdzonej waluty.
Dotychczasowe `purchase_cost.net` i `purchase_value` opisują tę samą wartość wydania;
`purchase_cost.scope="before_returns"` określa jej zakres. Konflikt lub brak
wiarygodnej ceny nadal zeruje dostępność kwoty (`amount=null`), nie samą kwotę.

W lokalnym schemacie sprawdzono wszystkie nazwy pól: nie znaleziono `CCENA_*` ani
pola jednoznacznie deklarującego walutę bazową. Pola walutowe znaleziono na fakturach,
ale nie dowodzą one waluty cen zakupu konkretnej linii RW/WZ. Symbol waluty pozostaje
niepotwierdzony. Trigger `ADD_ZAKPOZYCJA` wykonuje rozchód RW/WZ na magazynie
i ustawia `NEW.POBRANO=NEW.ILOSC`, co potwierdza źródło ilości wydanej. Trigger
`DEL_ZAKPOZYCJA` odwraca rozchód przy usunięciu linii; nie jest pełnym rejestrem
wszystkich zwrotów przypisywalnych do nadal istniejącej pozycji.

## Starsze powiązania i odbiór końcowy

Ponowna analiza RW dla `4050` potwierdziła 17 starszych linii z lat 2020–2023 z
pustym `ID_ZAKUPY`. Każda miała dokładnie jeden zgodny nagłówek po `(NUMER,ID_FIRMA)`,
zgodny rodzaj dokumentu i datę wydania. Pozostałe 11 linii miało poprawny techniczny
ID nagłówka. Rzeczywisty trigger `DEL_ZAKUPY` usuwa pozycje przez
`ZakPozycja.Numer=OLD.Numer AND ZakPozycja.ID_Firma=OLD.ID_Firma`. Nie przyjęto
zgadywanego związku `ID_ZAKUPY=DOKUMENT`. Sprzeczność dodatniego ID z kluczem
numerowym, nieistniejący wskazany nagłówek albo powtórzony numer w firmie daje
`conflict`. Alternatywne powiązania mają sufiks `:ZAKUPY:<id>` w kluczu faktu,
aby nie nadpisywać się wzajemnie; żadne z nich nie daje potwierdzonego kosztu.

Końcowy rzeczywisty pilot odczytowy trzech maszyn: `complete=true`, **14,67 s**,
582 fakty. Braków `missing_document_lines` nie stwierdzono. Dane zawierają
48 starszych powiązań przez numer i firmę. Wynik nie jest pełnym bilansem finansowym.

| Testowe ID | Fakty | Fakty materiałowe | Potwierdzone wartości wydań | Starsze powiązania linii |
| --- | --- | --- | --- | --- |
| 4050 | 241 | 28 | 4 | 17 |
| 4696 | 271 | 38 | 2 | 28 |
| 5914 | 70 | 5 | 0 | 3 |

Łącznie potwierdzono **6 wartości pojedynczych wydań**. Pozostałe koszty są
wykluczone z zakresu umowy albo zablokowane z powodu braków historycznych cen,
niejednoznacznych części lub innych konfliktów źródła. Nieznana waluta i zwroty
pozostają jawne. Odbiór końcowy: **53 testy poprawne** (42 izolowane testy źródła
oraz 11 testów interpretacji CPC), Ruff i Black poprawne. Fixture kursora obejmuje
`ZAKPOZYCJA.NUMER`, `ID_FIRMA` i `DATA_PRZY_WYDA`; nie występuje już przejściowy
błąd brakującej kolumny `NUMER`.

Analiza źródeł była wyłącznie odczytowa. Stan migracji, podglądu testowego
i odbioru całego modułu opisano w `docs/instal/orbit.md`. Wdrożenie produkcyjne
wymaga osobnej zgody oraz weryfikacji uprawnień kont źródłowych.
