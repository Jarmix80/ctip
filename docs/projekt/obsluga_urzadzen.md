# Obsługa urządzeń – model procesu

## Cel

Moduł `/device` prowadzi nowy fizyczny egzemplarz jedną kontrolowaną ścieżką:

1. operator tworzy dokument PZ;
2. każdy egzemplarz otrzymuje osobny rekord `MAGAZYN`;
3. system tworzy rekord `MASZYNA` przypisany początkowo do klienta magazynowego `656`;
4. egzemplarz może zostać zarezerwowany ręcznie albo wybrany w aktywnej sprawie FLOW;
5. po statusie `APPROVED_ORDER` automat FLOW przypisuje `MASZYNA` do klienta docelowego;
6. proforma i harmonogram dowozu korzystają z tego samego trwałego egzemplarza.

## Reguły przyjęcia

- Dostawca, model i serial są obowiązkowe.
- Wybór modelu z listy ma format `ID | marka model`; przycisk `Dodaj pozycję` zachowuje ten wybór, pokazuje lokalny komunikat i przenosi fokus do pola serialu pierwszego dodanego egzemplarza.
- Wybór dostawcy z listy ma format `ID | nazwa | NIP`; pełna etykieta nie może zerować wybranego rekordu.
- Przed utworzeniem PZ frontend pokazuje zbiorczą listę braków, oznacza niepoprawne pola na czerwono i ustawia fokus na pierwszym problemie. Brak dokumentu zewnętrznego wymaga jawnego wyjątku nawet wtedy, gdy ceny są dodatnie.
- Konto CTIP musi mieć sekcję `device` oraz aktywne powiązanie z użytkownikiem Menadżera Serwisu.
- Pole wystawiającego PZ pochodzi wyłącznie z tego powiązania; API nie przyjmuje go od klienta.
- Brak dokumentu zewnętrznego albo cena zakupu `0` wymaga zaznaczenia wyjątku i uzasadnienia mającego co najmniej 10 znaków.
- Data przyjęcia jest obowiązkowym polem wspólnym dokumentu. Formularz podpowiada bieżący dzień, a wybrana wartość steruje `ZAKUPY.DATA_PRZY_WYDA`, datą pozycji PZ, datą przyjęcia kartoteki `MAGAZYN` oraz rokiem numeracji PZ.
- Data wystawienia trafia do `ZAKUPY.DATA_WYST`. Forma płatności korzysta z wartości występujących w Menadżerze Serwisu: `Przelew`, `Gotówka` albo `Pobranie`. Termin płatności trafia do `ZAKUPY.DATA_PLAT`, domyślnie wynosi 14 dni od daty wystawienia i pozostaje edytowalny.
- Klucz UUID operacji jest idempotentny. Po błędzie połączenia dokładnie to samo żądanie zachowuje UUID, natomiast każda zmiana danych formularza automatycznie rozpoczyna nową operację z nowym UUID.
- Wycofane urządzenia pozostają w historycznym rejestrze CTIP, ale nie blokują ponownego przyjęcia tego samego serialu albo numeru KP. Unikalność jest wymuszana wyłącznie pomiędzy aktywnymi wpisami, a wyszukiwanie operacyjne zawsze preferuje wpis aktywny.
- Moduł nie tworzy nowych rekordów `SERIAL`. Serial i numer KP są zapisywane w pozycji PZ, osobnej kartotece `MAGAZYN`, rekordzie `MASZYNA` i rejestrze CTIP.
- Przed zapisem PZ generatory tabel używanych bezpośrednio i przez triggery są porównywane z maksymalnymi identyfikatorami oraz podnoszone wyłącznie w przypadku wykrycia opóźnienia.
- Historyczna ścieżka `arkusz → MAGAZYN/MASZYNA` bez PZ jest zablokowana.

## Formularz przyjęcia PZ

Interfejs `/device` korzysta na komputerach ze stałego lewego menu. Strona główna,
przyjęcie PZ, magazyn, audyt, historia PZ i synchronizacje są osobnymi ekranami, dlatego
zawartość nie jest układana kolejno na jednej stronie. Na urządzeniach mobilnych menu
zmienia się w poziomy, przewijany pasek nad właściwym ekranem.

Profil testowy udostępnia pod adresem `/device/style-prototypes` cztery nieaktywne warianty
tła obszaru roboczego: błękitny, grafitowy, miętowy i piaskowy. Galeria nie ładuje skryptu
modułu, nie korzysta z API i służy wyłącznie do zatwierdzenia wyglądu przed wdrożeniem.
Analogiczna trasa `/device/sidebar-prototypes` prezentuje na wybranym błękitnym tle trzy
warianty lewego menu: granatowy, jasnobłękitny i dwutonowy.
Trasa `/device/header-prototypes` zawiera trzy nieaktywne warianty górnego panelu dla
motywu niebieskiego: błękitne szkło, pełny granat i gradient dwutonowy.

Docelowym motywem domyślnym jest błękit operacyjny z granatowym menu. Przełącznik
`Kolorystyka` w prawym górnym rogu udostępnia również kompletny motyw grafitowy i miętowy.
Każdy motyw zmienia razem tło robocze, lewe menu, aktywną pozycję, nagłówki oraz
naprzemienne kolory wierszy. Górny panel korzysta z półprzezroczystego wariantu „szkło”
dopasowanego do każdej palety. Źródłem prawdy wyboru jest pole `admin_user.device_theme`,
więc preferencja pozostaje związana z kontem również po zalogowaniu na innym komputerze.
`localStorage` pod kluczem `ctip-device-theme` służy wyłącznie jako cache ograniczający
mignięcie domyślnego motywu przed zakończeniem odczytu `/auth/me`.
Górny panel pokazuje osobną wersję modułu w formacie `/device vX.Y.Z`. Ta sama wersja jest
częścią adresu zasobów CSS i JavaScript, co ułatwia potwierdzenie aktualności widoku po
wdrożeniu i wymusza pobranie nowych plików przez przeglądarkę.

W profilu testowym pod adresem `/device/intake/prototypes` dostępne są trzy makiety
planowanej przebudowy formularza PZ:

1. kreator z trzema krokami;
2. jeden uporządkowany formularz;
3. widok dzielony do pracy seryjnej.

Makiety korzystają wyłącznie ze statycznych danych prezentacyjnych. Nie zawierają skryptów
aplikacyjnych, nie wywołują API i nie wykonują zapisów do PostgreSQL, Firebird ani Google
Sheets. Trasa zwraca `404 Not Found` w profilu produkcyjnym.

Docelowy `/device/intake` korzysta z wariantu drugiego: jednego uporządkowanego formularza
z numerowanymi sekcjami dokumentu, urządzeń i listy egzemplarzy. Z wariantu trzeciego
zachowano wyróżnioną akcję `+ Dodaj urządzenie` oraz wyszukiwanie po modelu, serialu i
numerze KP w bieżącej liście. Przyklejone podsumowanie pokazuje liczbę urządzeń, kompletność
danych oraz wartości netto, VAT i brutto. Podsumowanie jest obliczane lokalnie i nie zmienia
zasad walidacji ani formatu żądania do istniejącego endpointu przyjęcia PZ.

Każdy egzemplarz może opcjonalnie otrzymać liczniki B/W, kolor i skan. Wartości są
nieujemnymi liczbami całkowitymi. Zapis aktualizuje odpowiednio `MASZYNA.LICZNIK`,
`LICZNIKA3`, `LICZNIK_TOTAL` i `SKAN_MONO`, tworzy historyczny odczyt CTIP oraz przekazuje
dane do kolejki synchronizacji arkusza. Brak liczników nie blokuje utworzenia PZ.

W szczegółach pozycji magazynowej operator może dodać datowany odczyt liczników. Odczyt
starszy od najnowszego jest zapisywany wyłącznie w historii CTIP i nie zmienia Firebird ani
arkusza. Obniżenie bieżącego licznika wymaga jawnego zaznaczenia wyjątku i uzasadnienia
mającego co najmniej 10 znaków. Każda aktualizacja tworzy zdarzenie audytowe, a publikacja
do arkusza korzysta z osobnej operacji outbox `update_counters`.

Tworzenie kartoteki nowego modelu nie znajduje się w głównym formularzu PZ. Formularz
`Dodaj nowy model urządzenia` jest dostępny w obszarze audytu pod tabelą magazynu. Po jego
zapisaniu model jest dostępny na liście wyboru przy kolejnym przyjęciu.

## Dane historyczne

Określenie „tylko audytowane” oznacza, że istniejące rekordy są wyświetlane i mogą zostać
powiązane technicznie dopiero przy jawnej zmianie uwagi lub rezerwacji. System nie wykonuje
zbiorczych napraw, nie dopisuje wstecz dokumentów PZ i nie migruje automatycznie rekordów
`SERIAL`, `MAGAZYN` ani `MASZYNA`.

Pod tabelą magazynu znajduje się stała legenda wyjaśniająca statusy synchronizacji arkusza,
rodzaje rezerwacji, źródło stanu Firebird, status zerówki oraz techniczny identyfikator
`MAGAZYN ID`. Etykieta `Tylko audyt` wskazuje pozycję historyczną bez wpisu w rejestrze CTIP.

Tabela magazynu pokazuje również licznik, cenę zakupu netto i najnowszą uwagę. Dla urządzenia
B/W kolumna licznika zawiera jedną wartość, a dla urządzenia kolorowego format
`B/W/KOLOR`, na przykład `5678/9584`; druga wartość jest wyróżniona kolorem. Brak odczytu
jest oznaczany jako `bd`, odpowiednio `bd` albo `bd/bd`. Liczniki pochodzą z kolumn
`LICZNIK B/W` i `LICZNIK KOLOR` arkusza i są odczytywane z lokalnego cache PostgreSQL.
Powiązanie cache z pozycją Firebird korzysta najpierw z `MS_ID_MAGAZYN_TABLE`. Dla wpisów
historycznych bez tego identyfikatora widok wykonuje bezpieczny fallback w pamięci: po
jednoznacznym numerze seryjnym, a następnie po jednoznacznej ewidencji. Duplikaty nie są
automatycznie łączone i nie powodują dodatkowych odczytów Google Sheets.

Szeroka tabela magazynu przewija się poziomo wyłącznie wewnątrz swojej karty i nie poszerza
obszaru całego modułu. Wiersze mają naprzemienne odcienie właściwe dla wybranego motywu.
Dwukrotne kliknięcie dowolnego pustego miejsca wiersza albo użycie klawisza `Enter` lub
spacji otwiera szczegóły urządzenia. Tabela nie ma osobnej kolumny ani przycisku
`Szczegóły`, dzięki czemu część źródłowa mieści się w obszarze roboczym.

## Rezerwacje

- Aktywna rezerwacja FLOW blokuje ręczną zmianę rezerwacji egzemplarza.
- Rezerwacja ręczna wymaga klienta/celu, terminu i uzasadnienia mającego co najmniej 10 znaków.
- Domyślny termin wynosi 14 dni i jest konfigurowany przez `DEVICE_MANUAL_RESERVATION_DEFAULT_DAYS`.
- Po terminie worker zwalnia rezerwację i zapisuje zdarzenie `reservation_expired`.
- Arkusz zachowuje kolumnę `STATUS` dla procesu zerówki. Rezerwacje są zapisywane osobno w
  `STATUS REZERWACJI`, `REZERWACJA DO` i `REZERWACJA GRENKE`.

## Wycofanie dokumentu PZ

Historia PZ udostępnia osobną kolumnę `Usuń` tylko administratorowi oraz użytkownikom z
uprawnieniem `can_withdraw_device_pz`. Operacja zawsze rozpoczyna się od podglądu skutków:
pozycji PZ, kartotek `MAGAZYN`, kartotek `MASZYNA`, wierszy arkusza, różnic względem zapisu
początkowego i późniejszych powiązań.

Zwykły operator może wycofać wyłącznie niezmieniony i nieużywany dokument. Administrator
może wymusić odłączenie wykrytych powiązań, ale tylko gdy CTIP ma pełny zapis początkowy.
Przed wykonaniem użytkownik podaje uzasadnienie mające co najmniej 10 znaków i przepisuje
pełny numer PZ. Stan jest ponownie sprawdzany w tej samej transakcji Firebird. Przed
usunięciem `MASZYNA` wszystkie wykryte odwołania, w tym `ZLECENIE.ID_MASZYNA`, są odłączane,
co zapobiega uruchomieniu kasującego triggera dla zleceń. CTIP zachowuje operację oraz
urządzenia ze statusem `withdrawn`, a usunięcie wierszy arkusza wykonuje idempotentny outbox
`delete_device`.

## Google Sheets

- Zapis PZ nie zależy od dostępności Google. Po zatwierdzeniu Firebird operacja arkusza trafia
  do `ctip.device_sheet_outbox`.
- Worker ponawia zapis maksymalnie 10 razy z rosnącym opóźnieniem. Po wyczerpaniu prób zadanie
  jest dostępne w `/device/issues`.
- Operacje jednego egzemplarza są wykonywane w kolejności utworzenia. Nieudane wcześniejsze
  zadanie blokuje późniejsze zmiany tego egzemplarza do czasu skutecznego ponowienia.
- Cache arkusza przechowuje także `LICZNIK B/W` i `LICZNIK KOLOR`, aby tabela magazynu
  nie wykonywała odczytu Google przy każdym otwarciu.
- Rezerwacja nie zapisuje uzasadnienia do kolumny `UWAGI`; bieżącą uwagę zmienia wyłącznie
  jawna operacja uwagi, a pełne uzasadnienie pozostaje w historii CTIP.
- W środowisku testowym zapis jest dozwolony wyłącznie do skoroszytu określonego przez
  `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` i tytuł `GOOGLE_SHEETS_TEST_SPREADSHEET_TITLE`.
- Izolowany stos testowy kieruje ruch przez bramę TLS dopuszczającą wyłącznie API Google,
  a scheduler outboxu jest uruchamiany tylko w usłudze `web` z dedykowanym kontem testowym.
- Nowe urządzenie jest zapisywane do jawnego zakresu od kolumny `A`, zamiast przez
  automatyczne `append`, aby Google nie przesunął kolejnej pozycji do prawego bloku kolumn.
- Przyjęcie PZ wpisuje w kolumnie `UWAGI` komunikat `dodana automatem PZ z CTIP`
  czerwonym tekstem. Późniejsza ręczna zmiana uwagi przywraca czarny kolor tekstu.
- Zarządzane wiersze otrzymują `CTIP_ENV=TEST` albo `CTIP_ENV=PRODUCTION`.
- Wymagana strefa czasowa skoroszytu to `Europe/Warsaw`.

## Uprawnienia

Sekcja `device` jest nadawana jawnie użytkownikom nieadministracyjnym w panelu użytkowników.
Rola `admin` zawsze otrzymuje wszystkie sekcje, w tym `device`, również gdy w bazie pozostał
starszy niepełny zapis. Utworzenie modelu, dostawcy i PZ dodatkowo wymaga aktywnego mapowania
konta CTIP do użytkownika Menadżera Serwisu.

## Kontrola spójności magazynu

Audyt ma osobną trasę `/device/audit`. Formularz nowego modelu oraz pełny audyt są
przenoszone do tej sekcji podczas inicjalizacji widoku i nie obciążają zwykłego ekranu
magazynu. Sekcja `/device/issues` pełni rolę historii synchronizacji i problemów kolejki.
Pokazuje błędy wymagające ponowienia, 300 ostatnich zadań outbox wraz ze stanem i liczbą
prób oraz 300 ostatnich zdarzeń urządzeń, w tym odczyty liczników i wycofania PZ.

Tabela magazynu pokazuje szybką kolumnę `Arkusz/Magazyn/Urządzenie/CTIP`. Każde źródło
otrzymuje znacznik `OK` albo `BD`:

- `Arkusz` oznacza jednoznaczny wpis w aktywnej zakładce `Urzadzenia_magazyn`, odczytany
  z lokalnego cache bez połączenia z Google przy każdym otwarciu tabeli.
- `Magazyn` oznacza pozycję magazynu Firebird nr 28 z dodatnim stanem dostępnym.
- `Urządzenie` oznacza jedną jednoznaczną kartotekę `MASZYNA`.
- `CTIP` oznacza jeden jednoznaczny wpis `ctip.device_inventory_unit`.

Ręczny audyt w widoku `/device/warehouse` wykonuje świeży odczyt aktywnego arkusza,
dostępnego magazynu 28, wszystkich kartotek `MASZYNA` oraz całego rejestru CTIP. Operacja
działa w tle, jest trwała po restarcie aplikacji i nie wykonuje napraw, migracji ani zapisów
do Firebird lub Google Sheets. Jednocześnie może działać tylko jeden audyt, a system
zachowuje 20 ostatnich przebiegów. Wynik końcowy ma priorytet `DUPLIKAT` →
`ROZBIEŻNOŚĆ` → `BRAKI` → `OK`.

Domyślny filtr wyników to `Widok operacyjny`. Pokazuje wszystkie wpisy z aktywnego arkusza
`Urzadzenia_magazyn` oraz magazynu Firebird nr 28 ze stanem dostępnym co najmniej `1`.
Kartoteki `MASZYNA` i wpisy CTIP są w tym widoku widoczne tylko jako powiązania tych
rekordów. Filtr źródła pozwala niezależnie wyświetlić pełny zbiór: arkusza, magazynu 28,
kartotek `MASZYNA`, rejestru CTIP albo całej unii audytu.
