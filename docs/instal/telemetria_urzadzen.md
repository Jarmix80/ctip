# Pozyskiwanie danych urządzeń

## Polityka dzienna — rozszerzenie z 11 września 2026 r.

Stan wdrożenia: kod produkcyjny `1e822f67ce49fa601ae427609ff052d8fba0acc6`,
migracja `e8c7d6a5b410`, backup `D:\CTIP\backups\prod_20260911_165801`.
Zachowano poprawkę wykupu BNP obecną wcześniej na produkcji.
Pilot zakończył się bez błędu. Pełny import działał w zadaniu
`CTIP-Telemetry-Backfill` od 17:07 do 18:34 czasu serwera i zakończył się kodem 0.
Zadanie jednorazowe zachowano jako ślad administracyjny, bez kolejnego terminu.

### Wynik odbioru produkcyjnego

| Źródło | Potwierdzony wynik |
| --- | --- |
| Główny katalog Remote | 222 CSV w `Archiwum`, bez CSV w katalogu roboczym |
| Toner / All Supplies / Reporting | Odpowiednio 23 / 23 / 30 CSV w archiwach, katalogi robocze puste |
| Poczta | 15325 wiadomości: 7264 w `przetworzone`, 8061 w `odrzucone`; INBOX i kolejka przeniesień puste |
| MS CPC | 25461 okresów 2023-09–2026-09 dla 1001 z 1019 aktualnie aktywnych maszyn |
| V-Maintenance | 25954 logiczne migawki dzienne dla 87 aktywnych urządzeń; zakres od 2024-09-11 |
| PrintRadar | 630 logicznych migawek dla 59 aktywnych urządzeń; dostępne dane od 2026-05-28 |

Migawki oraz nowo zaakceptowane wiadomości nie zawierają niedopasowanych urządzeń.
Oryginalne 298 CSV zachowały zgodne skróty; istniejących wcześniej archiwów
nie zmieniono. Sprawdzono także 7584 skompresowane oryginały w bazie:
rozmiary i SHA-256 zgodne, bez uszkodzeń. Brak błędów importu, zaległych
archiwizacji, niepotwierdzonych MOVE oraz powtórzonych wersji rekordów.
Historyczne rekordy wcześniejszego pilota pozostają zachowane; dlatego liczba
seriali w surowych rekordach może przewyższać liczbę aktywnie dopasowanych urządzeń.

Pełne ponowienie zakończono o 18:45: wszystkie CSV rozpoznane bez ponownego
importu danych, MS i V bez nowych rekordów, poczta bez ponownego przenoszenia.
PrintRadar dodał tylko wersję kartoteki ze zmienionym źródłowym `last_seen_at`;
liczba punktów dziennych pozostała równa 26584. Zmiana obserwacji obecności
urządzenia nie jest duplikatem ani nową migawką.

`CTIP-Telemetry` jest aktywne na koncie SYSTEM: codziennie 23:55 oraz po starcie
systemu, limit 12 godzin, sześć ponowień co 15 minut, bez równoległych instancji.
Próbny przebieg harmonogramu zakończono o 18:48 kodem 0; uzgodnienie objęło
ostatnie siedem dni i zatwierdziło należny slot `2026-09-10`.
Ponowny start workera zwrócił `already_completed`, bez przyrostu rekordów
i importów. Kolejny należny przebieg: 11 września o 23:55 czasu polskiego.

Końcowy stan zawiera 200213 wersji rekordów. Cztery usługi CTIP działają,
oba endpointy health zwracają 200, a chroniony podgląd bez sesji zwraca 401.
Sprawdzono odczyt metadanych i renderowanie panelu administratora.
Źródłowe bazy nadal są odczytywane kontami tylko do odczytu; Shipping,
dokumenty magazynowe oraz wspólny profil SYSTEM pozostały bez zmian.

Weryfikacja kodu: 158 testów ukierunkowanych na obu gałęziach; pełny przebieg
977 poprawnych i cztery wyłączone, znane wcześniej testy GenForm/raportów.
Kontrola pre-commit oraz rzeczywiste korekty A/B/A/B i rollback w PostgreSQL
`ctip_test` poprawne. Obrazu działającego stosu testowego nie przebudowywano.

### Ograniczenia danych

Ostrzeżenia jakości nie oznaczają błędu importu. Zachowano m.in. nieustaloną
strefę czasu treści poczty, brakujące daty, 55 wskazań spadku licznika,
84 wartości poza zakresem oraz 11 konfliktów wartości. Nie nadpisywano danych
źródłowych ani nie zgadywano dat zdarzeń. Oryginalne EML i surowe pola pozostają
dostępne do dalszej interpretacji. Parametr `TELEMETRY_MAIL_TIMEZONE` nadal
wymaga potwierdzonej strefy, a nie przyjęcia jej na podstawie lokalizacji klienta.

Zapisano osobno dostępne zgłoszenia V oraz 2004 różne obserwacje komunikatów
PrintRadar. Obserwacja stanu nie dowodzi chwili nowej awarii, a zgłoszenie
zapotrzebowania na toner nie potwierdza wysyłki. Importer dokumentów wydań,
wykresy oraz blokady zamówień tonerów nie należą do tego wdrożenia.

### Reguły eksploatacyjne

Aktualne reguły zastępują wcześniejsze pozostawianie DPLAC i niezmienianie skrzynki.
MS zachowuje 36 pełnych miesięcy i bieżący okres wyłącznie aktywnych umów.
Remote CSV obejmuje wszystkie dostępne daty i urządzenia. Poczta nie ma granicy
wieku, lecz dane zapisujemy tylko dla jednoznacznie dopasowanych aktywnych umów.
V-Maintenance oraz PrintRadar obejmują dwa lata kalendarzowe aktywnych urządzeń.

Wszystkie źródła są uruchamiane codziennie o 23:55 Europe/Warsaw. Zadanie po
restarcie nadrabia ostatni należny przebieg, a trwały znacznik nie pozwala wykonać
go drugi raz po sukcesie. Błędy nie zatwierdzają zakończenia całego harmonogramu.
Zadanie ma sześć ponowień co 15 minut oraz limit wykonania 12 godzin.

### CSV i oryginały

Cztery wejścia to katalog główny `remote ricoh` i podkatalogi
`tonery/All Supplies`, `tonery/Reporting`, `tonery/Toner`.
Skanowanie głównego katalogu nie obejmuje ponownie podkatalogów tonerowych.
Każde wejście obejmuje również swoje archiwum; plik już archiwalny nie jest
przenoszony. Brakujący główny katalog otrzymuje nazwę `Archiwum`, a katalogi
raportów zachowują istniejące `archiwum`.

Po zapisie bazy przenoszone są także DPLAC. Ten sam plik pod tą samą nazwą
i z identycznym SHA-256 wykorzystuje istniejące archiwum; inne dane o tej samej
nazwie dostają przyrostek. Zmiana zawartości, uszkodzona struktura lub trwający
zapis pozostawia plik roboczy do kolejnego przebiegu. Oryginał każdego CSV jest
zachowany w bazie jako gzip, także dla wcześniejszych znaczników DPLAC.

Obsługiwane są również polskie zestawienia liczników, UTF-8/CP1250, przecinek
i średnik. Pola przyrostu pozostają osobne od liczników narastających.
Nieznany poprawny format jest przechowywany jako `unclassified_csv`
z ostrzeżeniem `csv_schema_unknown`, bez zgadywania tożsamości i pomiarów.

### Poczta

Źródłem jest INBOX. Foldery `przetworzone` i `odrzucone` są tworzone dopiero
w jawnym trybie zapisu, nie są kolejnymi źródłami wejściowymi. Poprawnie
przetworzona wiadomość z dopasowanymi danymi trafia do `przetworzone`; brak
jednoznacznej aktywnej umowy do `odrzucone`. Przy wielu urządzeniach zapisujemy
tylko dopasowane części. Odrzucenie zachowuje skrót, identyfikator i powód,
ale nie tworzy telemetrii niedopasowanego urządzenia.

HTML jest odczytywany wyłącznie jako tekst, XML z ochroną przed encjami,
załączniki CSV istniejącymi parserami. Zachowane EML umożliwiają późniejsze
rozszerzenie interpretacji. Nieudane parsowanie i błędy techniczne pozostawiają
wiadomość w INBOX do ponowienia. Awaria odczytu aktywnych umów nigdy nie oznacza
pustego katalogu i masowego odrzucenia.

Tabela `telemetry_mail_delivery` przechowuje decyzję i kolejkę MOVE w tej samej
transakcji co dane. Przenoszenie używa UID MOVE, nie zbiorczego EXPUNGE.
Po utracie odpowiedzi sprawdzana jest identyczna treść w folderze docelowym.
Inna generacja UID lub niezgodny skrót blokują operację. Foldery wynikowe
nie są automatycznie ponownie klasyfikowane przy późniejszej zmianie umowy.

### Migawki i zdarzenia

`daily_snapshot` ma stały klucz źródło/serial/dzień oraz wersjonowany zestaw
ostatnich składników: liczniki, serwis i materiały. Każdy składnik zachowuje
swój czas, dokładność i identyfikator źródłowy. Późniejszy odczyt lub korekta
tworzy wersję tej samej migawki, nie kolejny logiczny punkt wykresu.
Brak pomiaru nie daje sztucznego zera ani świeżej daty.

`telemetry_daily_head` wskazuje bieżącą wersję w tej samej transakcji co kursor.
Powrót do już istniejącej wersji aktualizuje wskaźnik bez duplikowania historii;
wykres nie powinien wybierać wersji wyłącznie według czasu jej pierwszego importu.

Normalna aktualizacja pomiaru w ciągu dnia nie jest konfliktem wartości.
Różne wartości składnika z tym samym czasem zachowują alternatywy i ostrzeżenie;
wybór reprezentacji technicznej nie rozstrzyga arbitralnie prawidłowego wyniku.

Każdy przebieg ponownie sprawdza co najmniej ostatnie siedem dni od poprzedniego
ukończenia, a niedokończone porcje mają własny kursor. Zmiana katalogu aktywnych
umów wymusza ponowne sprawdzenie dwuletniej historii. Zdarzenia i wpisy historii
są zachowane niezależnie; powtarzający się stan bez daty wystąpienia jest
`event_observation`, nie nową awarią. Wzrost poziomu tonera nie dowodzi wymiany.
V korzysta z kartotek, historii liczników i zgłoszeń; globalne, niedopasowane
operacje magazynowe nie są przypisywane arbitralnie do urządzenia.

### Sterowanie i odbiór

- `TELEMETRY_ARCHIVE_ROOT_CSV=true` jawnie włącza wszystkie CSV i archiwizację głównego katalogu.
- `TELEMETRY_MAIL_MOVE_ENABLED=true` jawnie włącza foldery i przenoszenie wiadomości.
- `TELEMETRY_VM_HISTORY_YEARS=2`, `TELEMETRY_PRINTRADAR_HISTORY_YEARS=2`, `TELEMETRY_RECONCILE_DAYS=7` rozdzielają zakresy od MS.
- `--source NAZWA` wybiera źródło; opcję można powtarzać.
- `--limit N` ogranicza pliki, wiadomości albo strony źródła bazodanowego w pilocie.
- `--drain` opróżnia zaległe porcje bez rozszerzania zakresu dat, `--backfill` ponownie sprawdza pełną dozwoloną historię.
- `--scheduled` wykonuje tylko zaległy pełny przebieg o 23:55; nie łączy się go z limitem i wyborem źródła.
- `--dry-run` nie zapisuje danych, kursorów, folderów, przeniesień ani dziennika plikowego.

Rozszerzenie wymaga migracji `a6d9e1f3b520 -> c4f2a9b8d610 -> e8c7d6a5b410`. Przed włączeniem
zadań należy wykonać kontrolny odczyt, ograniczony pilot, pełną historię i
ponowienie bez przyrostu logicznych danych. Panel administracyjny pokazuje
oddzielnie migawki, zaległości i decyzje pocztowe. Dziennik
`docs/LOG/telemetry_YYYY-MM-DD.log` przełącza plik także przy pracy przez północ.

Poniższy pierwotny odbiór jest zapisem historycznym sprzed aktywacji tych reguł;
nie potwierdza jeszcze wdrożenia rozszerzenia.

## Zakres

Moduł pozyskuje historię do tabel `ctip.telemetry_*`. Nie blokuje zamówień, nie
wylicza dostępnych zapasów, nie wystawia dokumentów i nie zapisuje do MS,
V-Maintenance ani PrintRadar. Nie uruchamia harmonogramów aplikacji WWW.
Migracja addytywna: `a6d9e1f3b520`, poprzednik `f2b7c9d4e6a1`.

## Źródła i oryginały

| Źródło | Dane | Oryginał |
| --- | --- | --- |
| Reporting | Pełne kolumny, liczniki, poziomy, wymiany, pokrycie | Skompresowany CSV w bazie, plik przenoszony do `archiwum` |
| Toner / All Supplies | Zdarzenia materiałowe i towarzyszące pomiary | Jak Reporting |
| DPLAC | Historia liczników | Plik pozostaje bez zmian, baza przechowuje wszystkie pola, ścieżkę i SHA-256 |
| DPLAC Not obtained | Brak pomiaru w okresie oraz ostatni znany pomiar | Jak DPLAC; stary licznik nie jest nowym pomiarem |
| IMAP Remote | Wiadomości, bezpiecznie odczytany XML, liczniki, poziomy oraz tekst historii i firmware | Skompresowane EML w bazie, skrzynka bez zmian |
| V-Maintenance | MASZYNY, MASZYNY_STATS, WEZWANIE, MAGAZYNY, DODAJ, CPC | Wersjonowane dokumenty JSON, identyfikatory tabel i wierszy |
| PrintRadar | Urządzenia, liczniki, migawki serwisowe, materiały, jakość źródła | Przetworzone dane JSON, identyfikatory próbek; pełne tablice SNMP/HTML pozostają w PrintRadar |
| MS | Powiązanie SERIAL z ID_MASZYNA i ID_KLIENT | Wersjonowane powiązania, bez kopiowania dokumentów magazynowych |
| MS CPC | Miesięczne liczniki początku i końca, okres, historyczne ID urządzenia/klienta/umowy/FV, pełny wiersz CPC | Wersjonowany JSON w tych samych tabelach; bez odczytu i kopiowania pozycji FV |

Źródła nie są traktowane jako dowody faktycznej wysyłki tonera. Pola `ZAPAS_*`
oraz zapotrzebowanie ze zgłoszeń V-Maintenance pozostają danymi źródłowymi.
Historie wewnątrz wiadomości są zachowane w oryginale i sekcjach tekstowych/XML;
nie stają się automatycznie bieżącymi incydentami.

## Konfiguracja

Worker korzysta z wybranego `CTIP_ENV_FILE`: lokalnie `.env.test`, jawnie na
produkcji `.env`. Dodatkowe ustawienia czyta odpowiednio z `.env.test.telemetry`
albo `.env.telemetry`. Pliki te są wykluczone z Git. Domyślnie moduł i wszystkie
połączenia źródłowe pozostają wyłączone.

| Ustawienie | Znaczenie |
| --- | --- |
| TELEMETRY_ENABLED | Główne włączenie workera |
| TELEMETRY_REPORT_ROOT | Katalog zawierający podkatalogi Toner, All Supplies, Reporting |
| TELEMETRY_DPLAC_ROOT | Katalog skanowany bezpośrednio wzorcem DPLAC*.csv; nigdy nie jest archiwizowany |
| TELEMETRY_CSV_TIMEZONE | Domyślnie Europe/Warsaw |
| TELEMETRY_MAIL_TIMEZONE | Domyślnie puste: nie zgadujemy strefy czasu treści wiadomości |
| TELEMETRY_MAIL_ENABLED | Włączenie poczty; używa REMOTE_EMAIL_ADDRESS, REMOTE_EMAIL_PASSWORD, REMOTE_IMAP_HOST, REMOTE_IMAP_PORT |
| TELEMETRY_IMAP_FOLDER | Domyślnie INBOX |
| TELEMETRY_VM_ENABLED / TELEMETRY_MS_ENABLED | Włączenie odczytu Firebird |
| TELEMETRY_MS_CPC_ENABLED | Niezależne, domyślnie wyłączone pozyskiwanie miesięcznych liczników MS |
| TELEMETRY_HISTORY_YEARS | Domyślnie 3 lata; MS CPC pobiera 36 pełnych miesięcy oraz bieżący okres, tylko urządzenia aktualnie na aktywnych umowach |
| TELEMETRY_VM_HOST / TELEMETRY_VM_DATABASE / TELEMETRY_VM_CHARSET | Osobny adres, baza i kodowanie V-Maintenance |
| TELEMETRY_VM_USER / TELEMETRY_VM_PASSWORD | Dedykowane konto odczytowe V-Maintenance |
| TELEMETRY_MS_USER / TELEMETRY_MS_PASSWORD | Dedykowane konto odczytowe MS, bez zmiany konta używanego przez Shipping |
| TELEMETRY_PRINTRADAR_DSN | Połączenie do PrintRadar kontem z SELECT do czterech dopuszczonych tabel |
| TELEMETRY_PAGE_SIZE / TELEMETRY_MAX_PAGES | Domyślnie 500 rekordów i 4 porcje na tabelę w przebiegu przyrostowym |
| TELEMETRY_MAX_FILE_BYTES | Domyślnie 32 MiB |
| TELEMETRY_STABILITY_SECONDS | Co najmniej 60 sekund pomiędzy kontrolami stabilności CSV |

Testowy worker dopuszcza wyłącznie `ctip_test`, tryb testowy SMS i adresy lokalne
lub sieć izolowanego stosu `172.28.252.0/24`. Nie korzysta z produkcyjnego pliku
`.env.telemetry`. Zmiana podsieci stosu wymaga aktualizacji kontroli izolacji.
Połączenia Firebird jawnie używają transakcji READ ONLY, PostgreSQL PrintRadar
ustawia `default_transaction_read_only=on` i ogranicza czas zapytania.

V-Maintenance wymaga osobnego połączenia `UTF8`: baza ma domyślne `WIN1250`, lecz
część kolumn `NONE` zawiera tekst UTF-8. Dziedziczenie kodowania MS powodowało błąd
dekodowania i wtórny błąd protokołu podczas zamykania połączenia. Nie zastępujemy
nieznanych znaków ani nie zmieniamy bazy źródłowej; kodowanie MS pozostaje bez zmian.
Niektóre pola V zawierają także znak NUL, którego PostgreSQL JSONB nie przyjmuje.
Taki tekst jest zachowany jako obiekt `{"__telemetry_encoding__": "utf-8/base64",
"value": "..."}` z bezstratnie zakodowaną wartością, bez obcinania treści. Rekord
otrzymuje ostrzeżenie `source_text_encoded` z nazwą pola.

## Uruchamianie

```bash
source .venv/bin/activate
export CTIP_ENV_FILE="$PWD/.env.test"
python -m app.telemetry_worker --once --dry-run
python -m app.telemetry_worker --once
python -m app.telemetry_worker --once --backfill
```

Tryb `--dry-run` nie używa bazy docelowej, nie zapisuje logów do plików, kursorów
ani archiwów. Bez `--backfill` kontroluje ograniczoną liczbę porcji baz i poczty.
`--backfill` wykonuje pełny przegląd dostępnej historii, korzystając z trwałego
kursora niedokończonego przeglądu baz. Poczta jest ponownie przeglądana od początku;
oryginały oraz dane są deduplikowane. Zmiana wersji parsera pozwala utworzyć nowe
interpretacje zachowanych danych podczas ponownego przeglądu.

Jedna blokada sesyjna PostgreSQL obejmuje cały przebieg. Kursor każdej porcji jest
zatwierdzany z jej danymi. Codzienny przegląd starszych rekordów wykrywa korekty
nieposiadające daty modyfikacji. Małe, zmienne kartoteki są sprawdzane w każdym
przebiegu. Niepowodzenia pobrania pojedynczych wiadomości mają osobną kolejkę
ponowień; błędne XML pozostają w zachowanej wiadomości z ostrzeżeniem.

## Integralność i archiwizacja

Unikalne SHA-256 rozpoznają oryginały, a klucz źródłowy, skrót dokumentu i wersja
parsera rozpoznają wersje rekordów. Osobne powiązania zachowują każde wystąpienie
w plikach i wiadomościach. Wspólny klucz semantyczny grupuje potwierdzone zdarzenia
Toner/All Supplies, pary XML/tekst i wspólne pomiary DPLAC/Reporting. Nie oznacza
arbitralnego wyboru wartości przy konflikcie. Jednakowy licznik w innej dacie
pozostaje odrębnym pomiarem.

Plik CSV musi być stabilny przed odczytem i niezmieniony po odczycie. W razie
błędnej struktury cały plik pozostaje na miejscu; inne pliki są nadal obsługiwane.
Archiwizacja następuje po zatwierdzeniu bazy. Stan `pending` jest ponawiany także
po restarcie. Przeniesienie używa dowiązania twardego w tym samym woluminie oraz
usunięcia nazwy źródłowej dopiero po kontroli zawartości. Kolizja nazwy otrzymuje
unikalny przyrostek. Nie nadpisujemy obcych archiwów. Brak obsługi dowiązań lub
brak praw pozostawia plik i zgłasza błąd. DPLAC są dodatkowo chronione przed
wywołaniem funkcji archiwizacji.

Oryginały, rekordy i ostrzeżenia nie mają automatycznej retencji w pierwszym etapie.
Wymagany jest monitoring rozmiaru bazy i objęcie nowych tabel dotychczasowym
backupem PostgreSQL. Odnośniki do diagnostyki PrintRadar zależą od retencji źródła.

## Jakość i podgląd

Podgląd administratora: `/admin/telemetry`; API tylko do odczytu:
`GET /admin/telemetry/status`. Obejmuje źródła, świeżość pomiarów, liczbę wersji,
grupy semantyczne, kolejkę archiwizacji i ostatnie 50 importów oraz ostrzeżeń.
Oryginalne wiadomości i poświadczenia nie są zwracane przez to API.

### Miesięczne liczniki MS

Źródło `ms_cpc` zapisuje rekordy `kind=billing_period`, klucz `CPC:ID_CPC_TABLE`.
Nie wymaga nowych tabel ani kolejnej migracji: istniejące JSONB, historia wersji,
pochodzenie, powiązania i ostrzeżenia obejmują również te dane.

- `LICZNIK_MONO_START/END`, `LICZNIK_KOLOR_START/END`, `LICZNIK_MONOA3_START/END`,
  `LICZNIK_KOLORA3_START/END` oraz `LICZNIK_SKAN_START/END` są osobnymi pomiarami
  `billing.start.*` i `billing.end.*`; A3 nie jest ponownie dodawane do sumy.
- `ROK` i `MIESIAC` wyznaczają `payload.__ctip_billing__.start/end`. Brak daty
  faktycznego odczytu oznacza `observed_at=NULL`, `time_precision=month`.
  Okres jest pokazany osobno w panelu i nie poprawia świeżości telemetrii.
- `ID_FAKTURA` oznacza wyłącznie powiązanie z fakturą. Nie dowodzi daty odczytu
  ani fizycznej dostawy tonera. Pozycje bez FV pozostają z `billing_uninvoiced`.
- Pierwotne `ID_KLIENT` i `ID_MASZYNA` tworzą powiązanie `source_confirmed`;
  późniejszy obecny właściciel numeru seryjnego nie nadpisuje historii.
- Spadek między początkiem i końcem daje `billing_counter_decrease`; pomiędzy
  zafakturowanymi okresami tej samej maszyny i umowy — `billing_period_decrease`.
  Zapytanie ogranicza serię numerem seryjnym i korzysta z istniejącego indeksu
  źródło/serial/czas, zamiast ponownie przeglądać całą historię wszystkich maszyn.
  Błędny okres pozostaje ostrzeżeniem parsera, nigdy nie jest normalizowany z
  miesiąca 0 na styczeń. Zapytanie zakresowe nie pobiera okresów spoza okna.

Zakres aktywności wynika z `MASZYNA.ID_UMOWACPC -> UMOWACPC.ID_UMOWACPC_TABLE`
i `UMOWACPC.AKTYWNA='TAK'`. Maszyny wycofane z aktywnych umów nie są pobierane.
Historia obecnie aktywnej maszyny może zawierać poprzednich klientów; ich
identyfikatory są zachowane i przyszła analiza zamówień musi je rozdzielać.

Klucz maszyny w CPC wskazuje **logiczne `MASZYNA.ID_MASZYNA`**, nie techniczne
`ID_MASZYNA_TABLE`. Potwierdzono to na produkcji przez zgodność numerów seryjnych
z opisami pozycji faktur: 27 zgodnych przypadków, 3 bez rozstrzygającego opisu,
0 potwierdzeń alternatywnego połączenia technicznego. Analogicznie nie należy
zastępować technicznego ID umowy jej numerem logicznym.

Audyt 11 września 2026 r.: CPC zawiera 174763 wiersze dla 3885 historycznych
urządzeń. Dla 1001 z 1019 urządzeń aktualnie aktywnych umów istnieje licznik mono
końca okresu lipiec–wrzesień 2026 r. (98,2%). To pokrycie okresowe, nie gwarancja
świeżego odczytu. W całej historii znaleziono 1435 błędnych okresów, 87 spadków
wewnątrz okresu i 2455 grup wielokrotnych wpisów maszyna/umowa/miesiąc. Nie
usuwamy tych danych z MS; wersje i grupy semantyczne nie wybierają arbitralnie wyniku.

W zakresie wrzesień 2023–wrzesień 2026 dla maszyn obecnie na aktywnych umowach
zapytanie zwraca 25461 okresów dla 1001 urządzeń. Nie pobiera całej historii
174763 okresów ani urządzeń wycofanych z aktywnych umów.

### Dane dla przyszłej osi czasu urządzenia

Wykresy i blokowanie zamówień nie należą do tego etapu. Oryginały i ich pełne
pola muszą jednak pozwolić na późniejsze odtworzenie następujących zdarzeń:

| Zdarzenie | Źródło i interpretacja |
| --- | --- |
| Narastający licznik i przyrost kopii | Reporting/DPLAC, V-Maintenance, PrintRadar; CPC jako osobny okres rozliczeniowy, nie odczyt dzienny |
| Wymiana tonera, kolor, licznik wymiany | Toner/All Supplies, zdarzenia Remote i historie materiałów; wzrost poziomu jest poszlaką, nie dowodem wysyłki |
| Zamówienie materiału | Zgłoszenie i zlecenie; nie jest równoznaczne z wydaniem ani wymianą |
| Wydanie magazynowe | MS `ZAKUPY` RW/WZ i `ZAKPOZYCJA`; ilość faktycznie pobrana jest liczbą w `POBRANO`, nie flagą TAK/NIE |
| Nadanie, odbiór kuriera, doręczenie | MS `ZLECENIE.DATA_PRZES/PRZESYLKA`, CTIP `shipping_shipment.handed_over_at`, zdarzenia `shipping_tracking_event.event_time` wraz z anulowaniem |
| Awaria, zacięcie, naprawa | Historie XML/tekst Remote, przetworzone migawki PrintRadar, V `WEZWANIE`, MS `ZLECENIE.PROBLEM/USZKODZENIE` i daty serwisu |

Potwierdzono odczyt historii RW/WZ z produkcyjnego MS. W trzyletnim zakresie
połączonym z aktualnie aktywnymi maszynami występuje 6375 powiązań pozycji RW
i 7 WZ. Warunek nazwy zawierającej `TONER` wskazuje 4845 powiązań kandydatów;
to **nie** jest jeszcze liczba jednoznacznych wysyłek ani zatwierdzony słownik
tonerów. Część nazw może dotyczyć innych materiałów, a dokument może obsługiwać
wiele zleceń. Tylko 72 z tych powiązań zawierają `DATA_PRZES`; brak tej daty nie
może zostać zastąpiony datą rozchodu jako rzekomo potwierdzoną wysyłką.

Przy kolejnym importerze rozchodów należy zachować `ID_ZAKPOZYCJA_TABLE`,
`ID_ZAKUPY_TABLE`, `RODZAJ_DOK`, `DATA_WYST`, `DATA_PRZY_WYDA`, `ID_MAGAZYN`,
`INDEKS`, `NAZWA`, `ILOSC`, `POBRANO` oraz źródłowe zlecenie/klienta/maszynę.
Dokument łączymy przez globalne `ZLECENIE.ID_RW/ID_WZ`; pozycje zlecenia
`ZPOZYCJA` przez parę logiczny numer/rok. Nie mnożymy całej ilości wspólnego
dokumentu przez liczbę maszyn i nie liczymy ponownie tej samej dostawy jako FV.
Niejednoznaczny podział pozostaje do weryfikacji. Kolor i typ materiału wymagają
zgodności kartoteki z kodami `MODEL.TONER/TONER_C/TONER_M/TONER_Y`, a nie tylko
wyszukania słowa w nazwie. Ten importer rozchodów jest przygotowany koncepcyjnie
na bazie potwierdzonych pól; nie jest jeszcze włączony jako źródło workera.

### Ostrzeżenia jakości

- `counter_decrease`: spadek porównywalnego licznika narastającego, także wykryty po dołożeniu starszej historii.
- `conflicting_value`: różne wartości w tej samej grupie semantycznej.
- `invalid_range`: licznik ujemny lub procent poza zakresem 0–100.
- `missing_serial`: brak numeru seryjnego; rekord nadal zachowany.
- `time_*`: brak, błąd, nieustalona strefa lub niejednoznaczny czas.
- `source_error`: błąd pomiaru zgłoszony przez PrintRadar.
- `mail_parse`: nieudane odczytanie wiadomości; oryginał zachowany.

Nie porównujemy liczników wyłącznie po modelu, nie wymuszamy równości całkowitego
licznika z sumą kolorów i nie traktujemy resetu licznika aktualnego tonera jako
resetu urządzenia. Wartości tekstowe materiałów nie są zamieniane na zero.
Pomiary o dokładności tylko do dnia nie pozwalają rozstrzygać kolejności wewnątrz
tej doby. Dopasowanie do MS wymaga jednoznacznego SERIAL; potwierdzone historyczne
powiązania nie są przepisywane przy zmianie klienta.

Logi: `docs/LOG/telemetry_YYYY-MM-DD.log`, każdy wpis ze znacznikiem czasu.
Raportowane są kody błędów, nie pełne wyjątki sterowników zawierające poświadczenia.

## Wdrożenie Windows i rollback

### Stan odbioru z 11 września 2026 r.

- Produkcja: kod `8302a6e09ebbf96c47431d8a91b954827f4c6b05`, migracja
  `a6d9e1f3b520`; backup przed ostatnią aktualizacją:
  `D:\CTIP\backups\prod_20260911_052420`.
- Zapisano 25461 różnych rekordów CPC dla 1001 z 1019 urządzeń aktualnie na
  aktywnych umowach, okresy wrzesień 2023–wrzesień 2026. Żadne urządzenie nie
  wykracza poza ten zakres aktywności. Nie nadano fikcyjnej daty odczytu.
- Pozostałe źródła mają 1886 rekordów kontrolowanego pilota; łączny stan
  wynosi 27347 rekordów. Dziewięć rejestrów źródeł nie zgłasza ostatniego błędu.
- W CPC zachowano 7079 okresów bez powiązania z FV, 106 rekordów bez numeru
  seryjnego i jeden z niepełną tożsamością źródłową. Zapisano 35 ostrzeżeń
  spadku wewnątrz okresu, 37 między okresami i 11 konfliktów wartości.
  Są to liczby ostrzeżeń, a nie liczby urządzeń ani potwierdzonych awarii.
- Wykorzystanie indeksu serii potwierdzono przez `EXPLAIN` na produkcji.
  Pierwszy przebieg przerwano wyłącznie na jego własnym procesie po zatwierdzeniu
  10500 rekordów, aby wdrożyć optymalizację; wznowienie z kursora dopisało 14961.
  Dane i punkt wznowienia są zatwierdzane razem, bez usuwania historii.
  Ponowne pobranie całego zakresu zakończyło się bez błędu: 0 nowych rekordów
  i 25461 rozpoznanych powtórzeń, bez zwiększenia historii.
- Utworzono trzy podkatalogi `archiwum` i przeniesiono po jednym zatwierdzonym
  raporcie z każdego katalogu. Kolejka archiwizacji jest pusta; wszystkie
  218 oryginalnych DPLAC ma niezmienione SHA-256.
- Cztery usługi CTIP działają. Health i Shipping zwracają HTTP 200, a panel
  oraz API telemetrii bez sesji HTTP 401. Funkcję administratora i szablon
  sprawdzono osobno na produkcyjnym PostgreSQL w transakcji tylko do odczytu.
- Pre-commit oraz 73 testy ukierunkowane przechodzą na obu gałęziach.
  Pełny przebieg przed rozszerzeniem CPC: 888 poprawnych testów, cztery
  wcześniej potwierdzone problemy GenForm i raportów wyłączone z tego przebiegu.
  Repozytorium testowe i jego schemat są aktualne; obrazu działającego stosu
  testowego nie przebudowywano w ramach tego etapu.

**Nie zarejestrowano jeszcze zadań cyklicznych ani nie uruchomiono pełnego
pobrania pozostałych źródeł.** Do ustalenia pozostaje, czy trzyletni zakres i
aktywne umowy mają ograniczać wszystkie importy, czy wyłącznie analizę przy
zachowaniu szerszych danych surowych. Nie usuwać danych pilota bez osobnej decyzji.
Importer wydań tonerów i wykresy nie są częścią odebranego kodu.

### Procedura uruchomienia

1. Wykonać testy, commit i push. Przez `scripts/deploy_windows_prod.py` wdrożyć
   dokładny SHA z migracją `f2b7c9d4e6a1 -> a6d9e1f3b520`, po poprawnym dry-run i backupie.
2. Na serwerze uruchomić `scripts/setup_telemetry_windows.py` najpierw kontrolnie,
   następnie z `--apply`. Opcja `--mail-stdin` przyjmuje cztery ustawienia IMAP jako
   JSON przez stdin, bez umieszczania ich w argumentach i logach.
3. Skrypt tworzy konto `ctip_telemetry_ro`, nadaje wyłącznie SELECT do wymaganych
   tabel i zapisuje `.env.telemetry` z prywatnymi ACL dla SYSTEM, administratorów
   i konta wykonującego wdrożenie. Nie zmienia globalnego profilu SYSTEM ani
   poświadczeń istniejących aplikacji. Przy niepowodzeniu moduł pozostaje wyłączony.
   Opcja `--ms-cpc` dodatkowo nadaje SELECT do `CPC` i `UMOWACPC` w MS oraz
   włącza źródło miesięczne. Nie nadaje praw do zapisu ani do tabel faktur.
4. Wykonać kontrolny odczyt przez `scripts/windows/run_telemetry.py --dry-run --once`.
5. Zarejestrować `scripts/windows/install_telemetry_task.ps1`, a dla pierwszego
   pełnego pobrania dodatkowo wywołać go z `-Backfill`. Zadanie regularne działa
   co 15 minut oraz po restarcie; jednorazowe zadanie historii korzysta z tej samej blokady.
6. Zweryfikować daty i liczniki importów, trzy podkatalogi `archiwum`, brak zmian
   DPLAC, uprawnienia kont źródłowych i ponowne uruchomienie bez przyrostu duplikatów.

Rollback polega na wyłączeniu zadań `CTIP-Telemetry` i `CTIP-Telemetry-Backfill`
oraz powrocie do poprzedniego kodu zgodnie z runbookiem wdrożenia Windows.
Nie wykonywać automatycznego downgrade usuwającego dane: migracja celowo go blokuje.
Pozyskane dane i przeniesione raporty pozostają dostępne; DPLAC nie wymagają przywracania.
