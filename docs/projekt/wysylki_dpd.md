# Automatyzacja wysyłek i katalog zgodności części

## Cel modułu

Moduł `/shipping` automatyzuje realizację zleceń Menadżera Serwisu typu `TYP_US=8` (`dowóz materiałów`) i utrzymuje lokalny katalog zgodności części z modelami urządzeń. Operator nie przepisuje ręcznie danych z MS do panelu przewoźnika. CTIP prowadzi kontrolowany proces: weryfikacja odbiorcy, wybór części, utworzenie przesyłki, wydruk etykiety A4, przekazanie kurierowi i zamknięcie dnia.

Moduł `/delivery` obsługujący dowozy urządzeń i workflow GRENKE pozostaje odrębnym procesem.

## Przepływ operacyjny

1. CTIP pobiera z lokalnego lub produkcyjnego Firebirda wyłącznie niezakończone zlecenia `TYP_US=8` w stanie `O` albo `ZR`, dla których `ZLECENIE.TECHNIK` i `ZLECENIE.TECHNIK2` są puste albo zawierają dokładną wartość techniczną `Wysyłka Wysyłka`. Każdy inny technik oznacza, że materiał dostarcza pracownik terenowy, dlatego takie zlecenie nie trafia do kolejki wysyłkowej.
   Kolejka prezentuje polskie nazwy etapów, a tło całego wiersza odpowiada bieżącemu stanowi sprawy. Osobny znacznik rozróżnia zlecenie utworzone przez aplikację mobilną od zlecenia wpisanego ręcznie w MS.
   Dla klientów kolejki CTIP wykonuje zbiorczy odczyt przeterminowanych dokumentów `FAKTURA.RODZAJ_DOK='KPSK'`, dla których `ID_ODBIORCA=0`, `DATA_PLAT` jest wcześniejsza niż bieżąca data i `DO_ZAPLATY>0`. Wiersz takiego zlecenia otrzymuje czerwone oznaczenie nadrzędne wobec koloru etapu oraz podsumowanie liczby i kwoty zaległych FV.
   Przeglądarka odświeża kolejkę i lekki stan otwartego zlecenia co 30 sekund, po odzyskaniu fokusu okna oraz po ponownym pokazaniu karty. Zmiana na stan zakończony, przypisanie technika albo rozbieżność numeru przesyłki wyświetla czerwone ostrzeżenie i wyłącza operacje zapisu bez przeładowywania formularza.
   Domyślne sortowanie operacyjne układa kolejkę od góry: sprawy „Do weryfikacji”, „Gotowe do etykiety”, „Etykieta wygenerowana”, a na samym dole wszystkie zlecenia klientów z nieopłaconymi FV. Operator może przełączyć sortowanie na najstarsze, najnowsze albo nazwę klienta A–Z.
2. Operator otwiera zlecenie, widzi pełną treść `ZLECENIE.PROBLEM` i osobno porównuje bieżącą `MASZYNA.STOI` ze snapshotem `ZLECENIE.STOI`.
   Jeżeli klient ma zaległości, szczegóły pokazują numer każdej FV, datę wystawienia i termin, kwotę brutto, kwotę zapłaconą, pozostałą kwotę oraz liczbę dni po terminie. Informacja ma charakter ostrzegawczy i nie blokuje przygotowania przesyłki.
3. Panel pokazuje jawne karty źródeł adresu. Pierwszeństwo ma wcześniej zweryfikowany adres tego samego urządzenia i dokładnie tej samej wersji lokalizacji, następnie jednoznacznie rozpoznany pełny adres lokalizacji MS, a później kompletne pola oddziału, zlecenia lub klienta. Źródło bez ulicy, kodu i miejscowości nie tworzy pustego kafelka. Niepełna lokalizacja jest widoczna z ostrzeżeniem i przyciskiem „Użyj danych”, ale nie jest wybierana automatycznie. Ręczne użycie scala wyłącznie niepuste wartości źródła z formularzem; brak telefonu, e-maila albo innego pola nie usuwa informacji już wpisanej lub pobranej z kontaktu. Jeżeli żadne źródło nie jest kompletne, formularz zachowuje ulicę, kod i miasto z jednego najlepszego źródła strukturalnego MS, oznacza wariant jako ręczny i wskazuje operatorowi dokładnie brakujące pola. Telefon jest obowiązkowy, e-mail opcjonalny. Obsługiwane są wyłącznie adresy krajowe `PL`.
4. Lista „Kontakt z Menadżera Serwisu” zawiera aktywne rekordy `KONTAKT` przypisane do firmy i klienta. Wybór osoby uzupełnia nazwę, telefon oraz e-mail, ale pola pozostają edytowalne. Zlecenie z jawnym znacznikiem `Utworzył z aplikacji:` w `ZLECENIE.OPERATOR` jest dopasowywane do aktywnego konta mobilnego po loginie, e-mailu, telefonie i nazwie; jednoznaczny kontakt staje się domyślnym odbiorcą. Jeżeli `ZLECENIE.PROBLEM` zawiera wiarygodny dziewięciocyfrowy numer z separatorami, prefiksem kraju albo opisem „telefon” lub „kontakt”, numer z treści ma pierwszeństwo przed automatycznym numerem z konta mobilnego i zlecenia. Formularz oznacza takie uzupełnienie komunikatem „Telefon pobrano z treści zlecenia”, ale nadal pozwala operatorowi ręcznie zmienić numer lub wybrać inny kontakt. Parser odrzuca zwarte indeksy części i numery zleceń. Wartość `KONTAKT.LOCK_USER` służy wyłącznie jako warunek identyfikacji aktywnego konta i nie jest odczytywana ani zwracana przez API.
5. CTIP pokazuje fizyczne pozycje z `MAGAZYN.ID_MAGAZYN=1` i rodzajów `1. Część zamienna` oraz `2. Towar inny`, odejmuje `IL_REZ` oraz miękkie rezerwacje aktywnych spraw CTIP.
6. Domyślnie widoczne są wyłącznie potwierdzone mapowania dla `MASZYNA.ID_MODEL`. Operator jawnie przełącza zakres między „Części dla modelu” i „Cały magazyn”; wpisanie tekstu nie zmienia zakresu wyszukiwania.
7. Operator wybiera pozycje i może jawnie potwierdzić zgodność kartoteki z modelem urządzenia. Dla zlecenia umownego bez FV cena pozycji jest tylko do odczytu i pochodzi z `MAGAZYN.CENA_Z1`. Dla zlecenia poza umową oraz każdego zlecenia z FV panel podpowiada edytowalną cenę sprzedaży z `MAGAZYN.CENA_NETTO`; dopiero przy jej braku używa `MAGAZYN.CENA_Z1` i wyraźnie oznacza awaryjne źródło ceny. Pozycja z rzeczywistym stanem zerowym pozostaje zablokowana do czasu zaznaczenia ostrzeżenia „Zezwól na część ze stanem zerowym”. Zgoda dotyczy wyłącznie wybranej pozycji, jest zapisywana w sprawie i pozwala MS czasowo utworzyć ujemny stan przy `ZPOZYCJA`, RW albo WZ, gdy oczekiwana dostawa nie została jeszcze przyjęta. Zgoda nie omija konfliktu miękkich rezerwacji dla kartoteki z dodatnim stanem ani nie pozwala wydać ilości większej od dostępnej pozycji dodatniej.
8. Akceptacja danych tworzy sprawę w stanie `ready`. Kolejna akceptacja tej samej sprawy atomowo zastępuje wcześniejsze miękkie rezerwacje, również gdy operator ponownie wybierze tę samą kartotekę. Operator może przy akceptacji zaznaczyć „Wystaw FV”. Dla zlecenia poza umową opcja jest zaznaczana automatycznie przy pierwszym otwarciu, ale operator może ją odznaczyć. Zapisany wybór jest wiążący i wybiera późniejszy wariant `FV + WZ`, `RW` albo `WZ bez FV`. Dopiero po akceptacji dostępne jest generowanie etykiety.
9. Po każdej akceptacji kolejka porównuje gotowe sprawy bez etykiety. Jeżeli co najmniej dwa zlecenia mają tę samą znormalizowaną nazwę firmy, ulicę, kod pocztowy i miejscowość, wiersze oraz szczegóły pokazują ostrzeżenie z numerami zleceń. Przy pojedynczym zleceniu blok ostrzeżenia pozostaje całkowicie ukryty. Przycisk „Zaznacz te zlecenia” wybiera całą wykrytą grupę, a akcja „Jedna paczka / jedna etykieta” tworzy jeden fizyczny list przewozowy ze wspólnym numerem przesyłki.
10. Operator może wygenerować etykietę w szczegółach jednej sprawy, zaznaczyć wiele spraw `ready` i utworzyć dla nich osobne etykiety, utworzyć jedną wspólną paczkę dla zgodnego adresu albo użyć akcji „Generuj wszystkie gotowe”. Operacja zbiorcza osobnych etykiet przetwarza maksymalnie 100 spraw kolejno i zwraca wynik dla każdej z nich, dzięki czemu błąd pojedynczego zlecenia nie ukrywa poprawnie utworzonych etykiet. Jedna wspólna paczka obejmuje od 2 do 20 zleceń, sprawdza aktualny stan każdego z nich i sumuje wagę do limitu 31,5 kg.
11. Zaznaczone sprawy z etykietą mają dwa niezależne wydruki. „Lista części” tworzy poziome zestawienie kompletacyjne na zwykły papier A4 z numerem zlecenia, klientem, numerem przesyłki, indeksem, nazwą towaru i ilością. „Etykiety DPD” pobierają dla wybranych numerów `waybill` natywny dokument A4 z `generateSpedLabels`, przeznaczony na arkusz samoprzylepny 2×2. Dla wspólnej paczki zestawienie zachowuje osobne zlecenia i części, ale numer przesyłki występuje w partii etykiet tylko raz. Tryb `mock` generuje od lewego górnego pola realistyczny odpowiednik arkusza z odbiorcą, nadawcą, wagą, referencją, trasą testową, kodem kreskowym numeru `MOCK` oraz maksymalnie trzema wierszami zawartości paczki. Każdy wiersz podaje ilość, jednostkę, indeks i nazwę części; przy większej liczbie pozycji trzeci wiersz wskazuje liczbę pominiętych elementów i odsyła do pełnej kompletacji. Niedekodowalny znacznik 2D, czerwony pasek oraz ukośny znak wodny jednoznacznie blokują użycie takiego wydruku jako dokumentu przewozowego.
12. Nadanie jest idempotentne. Odpowiedź DPD i etykieta PDF są zapisywane przed próbą aktualizacji Firebirda.
13. Zapis Firebird wykonywany przy utworzeniu etykiety blokuje rekord zlecenia i ponownie sprawdza `STAN`, `TECHNIK`, `TECHNIK2` oraz istniejący numer przesyłki. Dopiero poprawny stan dodaje brakujące `ZPOZYCJA` z zaakceptowaną ceną, ustawia `ZLECENIE.PRZESYLKA` i stan `ZR`. Zlecenie zakończone nie może zostać ponownie otwarte przez nadpisanie `STAN='ZR'`. Operacja nie zapisuje jeszcze daty nadania ani wpisu „Wysłana paczka”, ponieważ kurier nie potwierdził odbioru.
14. Po fizycznym odbiorze paczek operator może użyć zbiorczej akcji „Kurier odebrał paczki” albo przycisku „Kurier odebrał tę paczkę — zakończ zlecenie” w szczegółach pojedynczej sprawy. Jeżeli wybrane zlecenie należy do wspólnej paczki, przycisk pokazuje wszystkie jej numery i finalizuje każde powiązane zlecenie; każde zachowuje własne pozycje oraz osobny RW, WZ lub FV, mimo wspólnego numeru przesyłki. Przed potwierdzeniem backend ponownie odczytuje stan MS, a funkcja finalizująca jeszcze raz kontroluje go pod blokadą transakcyjną. Stan inny niż `ZR`, przypisany technik albo różny numer przesyłki zwraca konflikt `409`, ustawia sprawę `reconcile_required` i nie tworzy dokumentów ani rozchodu dla zlecenia z konfliktem. Obie akcje korzystają z tej samej funkcji finalizującej. CTIP wpisuje `ZLECENIE.DATA_PRZES`, dopisuje do `ZLECENIE.WYKONANIE` tekst `Wysłana paczka DD.MM.RRRR NUMER_PRZESYŁKI`, tworzy dokumenty w jednej transakcji i zamyka zlecenie stanem `Z`. Zlecenie umowne bez FV tworzy magazynowy nagłówek `ZAKUPY.RODZAJ_DOK='RW'` oraz pozycje `ZAKPOZYCJA.RODZAJ_DOK='RW'`, a następnie zapisuje identyfikator dokumentu w `ZLECENIE.ID_RW`. Numeracja korzysta z kolejnego `ZAKUPY.DOKUMENT` w danym roku i ma format `RW / N / RRRR`. Natywny trigger Firebirda ustawia `ZAKPOZYCJA.POBRANO` i zmniejsza `MAGAZYN.ILOSC`; CTIP nie zapisuje RW jako sprzedażowego `FAKTURA.RODZAJ_DOK='ROK'`. Zlecenie z FV najpierw tworzy `ZAKUPY.RODZAJ_DOK='WZ'`, następnie fakturę `FAKTURA.RODZAJ_DOK='KPSK'` powiązaną z WZ i zapisuje numer FV w zleceniu. Zlecenie poza umową bez FV tworzy wyłącznie WZ i zapisuje jego numer w zleceniu. Nagłówki RW, WZ i FV używają zaakceptowanego adresu wysyłki z formularza, a pozycje zachowują ceny zatwierdzone w `/shipping`.
15. Po potwierdzeniu odbioru CTIP wysyła SMS oraz, jeżeli podano adres, e-mail z numerem przesyłki.

## Ochrona zmiennej lokalizacji urządzenia

1. Bieżącym źródłem lokalizacji jest `MASZYNA.STOI`; `ZLECENIE.STOI` służy jako fallback, gdy kartoteka urządzenia nie zawiera lokalizacji, oraz jako widoczny snapshot z czasu zlecenia.
2. CTIP normalizuje tekst lokalizacji i wylicza SHA-256 z identyfikatorów firmy, klienta, urządzenia oraz bieżącej lokalizacji. Zmiana wielkości liter, separatorów lub nadmiarowych spacji nie wymusza ponownej akceptacji.
3. Akceptacja adresu zapisuje `location_source`, `location_text_snapshot` i `location_fingerprint` w `shipping_address` oraz `shipping_case`.
4. Zapisany adres jest automatycznie dostępny tylko dla tego samego urządzenia i zgodnego fingerprintu. Rekord historyczny bez fingerprintu pozostaje widoczny jako niezweryfikowany i nie może zostać wybrany.
5. Odczyt szczegółów zlecenia cofa sprawę bez przesyłki do `review_pending`, jeżeli bieżąca lokalizacja różni się od zaakceptowanej. Stary adres jest widoczny, ale zablokowany.
6. Żądanie akceptacji zawiera fingerprint odczytany przez przeglądarkę. Jeżeli MS zmienił się między otwarciem ekranu a kliknięciem, API zwraca konflikt `409` i wymusza odświeżenie danych.
7. Bezpośrednio przed utworzeniem etykiety albo zapisaniem ręcznego numeru DPD backend ponownie odczytuje lokalizację z MS. Niezgodność blokuje nadanie. Po utworzeniu przesyłki zapisany snapshot pozostaje wiążący i nie zmienia danych istniejącej etykiety.
8. Parser lokalizacji akceptuje wyłącznie jeden polski kod `NN-NNN`, jednoznaczną miejscowość oraz ulicę z numerem budynku. Opisy typu „kontrola jakości”, „biura – góra” lub tekst bez pełnego adresu pozostają informacją dla operatora.

## Katalog zgodności

1. Akcja „Skanuj nazwy i historię” wykonuje wyłącznie zapytania `SELECT` do Firebirda. Odczytuje fizyczne kartoteki magazynu `1`, słownik `MODEL` oraz historyczne użycia `ZPOZYCJA` powiązane przez `ZLECENIE` i `MASZYNA`.
2. Skan porównuje pełne oznaczenia modeli, numery katalogowe, pola tonerów ze słownika `MODEL` i historię użycia. Dopasowanie samej marki nie tworzy sugestii. Granice cyfr blokują błędne utożsamienie modeli, np. `301` z `3010`.
3. Całe urządzenia rozpoznane po indeksie `KP/`, `WKP/`, `AUTO/` albo oznaczeniu `S/N:`/`nr.wew` pozostają dostępne w wyszukiwaniu magazynu, lecz nie tworzą automatycznych sugestii części.
4. Rodziny marek Ricoh/Nashuatec/Gestetner/Lanier/Infotec, Konica Minolta/Develop/ineo/bizhub oraz Kyocera/UTAX/Taskalfa/Ecosys są używane wyłącznie do rozstrzygania pełnych oznaczeń modeli.
5. Pewność `high` wymaga co najmniej dwóch niezależnych sygnałów. Pewność `medium` oznacza pełne oznaczenie modelu, zgodny kod katalogowy albo powtarzalną historię. Pojedyncze historyczne użycie ma pewność `low`.
6. Każdy wynik skanu ma stan `suggested`. Operator lub administrator musi go potwierdzić albo odrzucić. Kolejne skany nie nadpisują decyzji `confirmed` i `rejected`; nieobecna wcześniej sugestia otrzymuje stan `stale`.
7. Opcjonalna akcja Web Search działa tylko ręcznie, dla maksymalnie 20 zaznaczonych kartotek i z limitem dobowym. Do OpenAI trafiają wyłącznie publiczne dane produktu: ID kartoteki, indeks, numery katalogowe, nazwa i podpowiedź marki/modelu. Dane klientów i zleceń nie są wysyłane.
8. Wynik WWW jest zapisywany wyłącznie jako sugestia, gdy model da się jednoznacznie rozwiązać do słownika `MODEL` i odpowiedź zawiera poprawne cytowanie URL. Cytowania są dostępne jako klikalne odnośniki w panelu.
9. CTIP nie zapisuje mapowań do `MAGAZYN.ID_MODEL`. Źródłem prawdy dla relacji wiele-do-wielu pozostaje lokalna tabela PostgreSQL `ctip.shipping_consumable_compatibility`.
10. Katalog prezentuje jeden wiersz dla fizycznej części i listę wszystkich pasujących modeli. Każda relacja model–część zachowuje niezależny status, dowody i historię decyzji.
11. Formularz ręcznego mapowania pozwala wybrać wiele modeli i zapisuje wszystkie relacje w jednej transakcji. Potwierdzenie lub odrzucenie jednego modelu nie wpływa na pozostałe modele części.
12. Przyciski „Zaznacz wszystkie” i „Odznacz wszystkie” działają na relacjach widocznych na bieżącej stronie i z aktywnymi filtrami.
13. Pola wyszukiwania kolejki, magazynu, modeli i katalogu traktują wpisane wyrazy niezależnie od kolejności. Frazy `toner mpc 3003` oraz `toner ylw 3503` wymagają obecności każdego wyrazu w przeszukiwanych danych.

## Odporność na błędy

- Unikalny `idempotency_key` blokuje podwójne nadanie po ponownym kliknięciu lub odświeżeniu strony.
- Interfejs generuje poprawny UUID również wtedy, gdy przeglądarka otwiera panel przez zwykły adres HTTP w sieci LAN i nie udostępnia `crypto.randomUUID()`; błąd przygotowania żądania jest prezentowany operatorowi.
- Dokumenty CTIP rejestrują font TrueType obsługujący polskie znaki. Kolejność wyszukiwania obejmuje DejaVu Sans na Linuxie, Arial na Windows i font Vera dostarczany z ReportLab.
- Ponowna akceptacja przed nadaniem usuwa stare pozycje w osobnym flushu transakcji, zanim zapisze nowy zestaw, dzięki czemu ta sama kartoteka nie narusza ograniczenia `uq_shipping_item_case_warehouse`.
- Etykieta jest zawsze pobierana z zapisanego rekordu; ponowny wydruk nie tworzy nowej przesyłki.
- DPD otrzymuje stabilne techniczne referencje paczki i parceli o długości do 50 znaków. Numery zleceń trafiają do biznesowych pól `ref1`, `ref2`, `ref3`, a ponowienie po timeoutcie lub odpowiedzi o zduplikowanej referencji próbuje odzyskać istniejącą etykietę zamiast tworzyć drugą paczkę.
- Kod pocztowy pozostaje w formularzu i dokumentach CTIP w formacie `00-000`, natomiast adapter przekazuje go do DPD REST jako pięć cyfr `00000`, zgodnie z walidacją środowiska Demo i produkcyjnego.
- Hasło, Basic Auth i nagłówek `X-DPD-FID` nie są zapisywane w bazie ani logach. `provider_response` przechowuje statusy, `sessionId`, `waybill`, `documentId` i `traceId`, ale nie przechowuje pola Base64 `documentData`.
- Jeżeli DPD utworzy przesyłkę, ale Firebird odrzuci zapis, sprawa otrzymuje stan `reconcile_required`. Operator widzi numer listu i błąd, a system nie ponawia automatycznie nadania.
- Bieżący stan zlecenia jest kontrolowany przy akceptacji, generowaniu etykiety, potwierdzeniu odbioru oraz wewnątrz transakcji Firebirda. Zewnętrzne zakończenie zlecenia albo przypisanie technika blokuje kolejne dokumenty i zapisuje zdarzenie `external_order_state_conflict`.
- Stan zerowy wymaga jawnej zgody operatora zapisanej przy konkretnej pozycji. Samo zaznaczenie globalnego potwierdzenia bez wyboru części nie zmienia danych, a jego wyłączenie usuwa z paczki pozycje wymagające ujemnego stanu.
- Zamknięcie dnia jest unikalne dla daty. Przed utworzeniem dokumentu CTIP sprawdza istniejące `ID_RW`, `ID_WZ`, `ID_FAKTURA` oraz dokumenty powiązane z numerem zlecenia, aby ponowienie nie utworzyło duplikatu.
- Tryb testowy generuje realistyczny PDF z numerem `MOCK`, niedekodowalnym znacznikiem 2D oraz powtarzanym napisem „ETYKIETA TESTOWA — NIE NADAWAĆ”. Samo generowanie dokumentu nie modyfikuje Firebirda.
- Wyjątek `SHIPPING_TEST_FIREBIRD_WRITES=true` pozwala etykiecie `mock` albo DPD Demo zapisać `ZPOZYCJA` i późniejsze dokumenty RW, WZ albo FV tylko wtedy, gdy równocześnie aktywne są: PostgreSQL `ctip_test`, sieciowy Firebird na lokalnym hoście, baza z `TEST` w nazwie, `FB_ALLOW_WRITES=true`, `DPD_MODE=mock|demo` i `SMS_TEST_MODE=true`. Decyzja jest utrwalana w żądaniu konkretnej przesyłki, dlatego włączenie flagi nie obejmuje starszych etykiet testowych.
- Klient Menadżera Serwisu łączy się do kopii przez `192.168.0.9:3050` i alias `BAZAMS_TEST` albo `BAZAMS_TEST\\BazaMS.fdb`. Backend uruchomiony bezpośrednio na hoście używa stałego portu `127.0.0.1:3051`, udostępnionego wyłącznie lokalnie bezpośrednio przez kontener Firebird. Rozdzielenie portów omija bramę HAProxy niezgodną ze sterownikiem `firebirdsql` i zabezpiecza aplikację przed zmianą wewnętrznego adresu kontenera po restarcie.
- Tryb produkcyjny nadania jest blokowany, gdy `FB_ALLOW_WRITES=false`.

## Dane PostgreSQL

- `shipping_address` — zweryfikowane adresy lokalizacji.
- `shipping_consumable_compatibility` — sugestie, decyzje, poziom pewności i dowody dla mapowań model–część.
- `shipping_case` — stan obsługi zlecenia MS, snapshot zaakceptowanych danych oraz jawny wybór `invoice_required` dla rozliczenia przez FV.
- `shipping_item` — wybrane pozycje, cena zaakceptowana, ceny źródłowe sprzedaży i zakupu, źródło ceny, miękkie rezerwacje oraz zgoda `allow_negative_stock` na kontrolowany wyjątek dla stanu zerowego.
- `shipping_shipment` — identyfikatory DPD, numer listu, etykieta, metadane wspólnej paczki, statusy uzgodnienia oraz identyfikatory i numery wynikowych dokumentów RW, WZ i FV. Pola `closed_by`, `archive_snapshot` i `archive_search_text` przechowują operatora zamknięcia, niezmienny stan końcowy oraz znormalizowaną treść wyszukiwarki. Wspólna paczka ma jeden numer DPD, ale osobny rekord dla każdego zlecenia, aby zachować niezależne dokumenty MS.
- `shipping_day_close` — idempotentne zamknięcie dnia.
- `shipping_event` — niemodyfikowalny dziennik etapów i błędów.

Tabele `shipping_address` i `shipping_case` przechowują pola `location_source`, `location_text_snapshot` oraz `location_fingerprint`. Migracja `a4b5c6d7e8f9` dodaje te pola i indeks wyszukiwania adresu po urządzeniu oraz fingerprintcie.

Migracja `b5c6d7e8f901` dodaje do `shipping_case` pole `invoice_required`. Wartość jest zapisywana przy akceptacji danych, widoczna w kolejce i uczestniczy w wyborze wariantu RW, WZ albo FV + WZ.

Migracja `c6d7e8f901a2` dodaje do `shipping_item` pole `allow_negative_stock`. Wartość domyślna `false` zachowuje dotychczasową blokadę, natomiast `true` jest zapisywane tylko po ręcznym potwierdzeniu operatora dla kartoteki z rzeczywistym stanem zerowym.

Migracja `d7e8f901a2b3` dodaje snapshot `catalog_price_net`, źródło `price_source` oraz identyfikatory i numery dokumentów Firebird w `shipping_shipment`. `price_net` pozostaje ceną zaakceptowaną przez operatora i używaną w pozycjach zlecenia oraz dokumentach końcowych.

Migracja `e8f901a2b3c4` dodaje trwałe Archiwum wysyłek. Dla istniejących zamkniętych spraw uzupełnia operatora na podstawie zdarzenia `courier_handover`, buduje snapshot zlecenia, odbiorcy, urządzenia, części, cen, DPD i dokumentów oraz tworzy indeksy daty, operatora i `pg_trgm`. Nowe snapshoty powstają w tej samej transakcji co dokumenty RW, WZ albo FV + WZ, dlatego odczyt Archiwum nie wymaga połączenia z Firebirdem.

## API

- `GET /admin/shipping/config` — stan konfiguracji bez sekretów.
- `GET /admin/shipping/dpd/status` — tryb, adres środowiska i gotowość konfiguracji DPD bez sekretów.
- `POST /admin/shipping/dpd/demo-diagnostic` — kontrolowana przesyłka Ksero-Partner → Ksero-Partner w DPD Demo, bez zapisu do MS.
- `GET /admin/shipping/compatibility` — filtrowany katalog sugestii i decyzji.
- `GET /admin/shipping/compatibility/items` — filtrowany katalog pogrupowany według fizycznej części.
- `POST /admin/shipping/compatibility/scan` — lokalny skan nazw, kodów i historii.
- `POST /admin/shipping/compatibility/review` — zbiorcze potwierdzenie lub odrzucenie.
- `POST /admin/shipping/compatibility/manual` — ręczne potwierdzenie model–kartoteka.
- `POST /admin/shipping/compatibility/manual-batch` — atomowe potwierdzenie wielu modeli dla jednej kartoteki.
- `POST /admin/shipping/compatibility/web` — ręczne wzbogacenie wybranych kartotek przez Web Search.
- `GET /admin/shipping/stock` — wyszukiwanie fizycznych kartotek magazynu głównego.
- `GET /admin/shipping/models` — wyszukiwanie kanonicznych modeli Firebird.
- `GET /admin/shipping/archive` — stronicowane Archiwum z wyszukiwaniem wielowyrazowym i przybliżonym oraz filtrami daty, operatora, typu dokumentu, źródła, trybu przesyłki i wspólnego pakowania.
- `GET /admin/shipping/archive/{id}` — pełny, tylko do odczytu snapshot zakończonego zlecenia wraz z operatorami, dokumentami, częściami, cenami, historią zdarzeń i odnośnikiem do etykiety DPD.
- `GET /admin/shipping/queue` — kolejka zleceń z Firebirda z polskim etapem prezentowanym przez UI, źródłem `mobile|manual`, decyzją FV, podsumowaniem zaległych płatności i informacją o możliwym wspólnym pakowaniu.
- `GET /admin/shipping/orders/{id}` — pełna treść zlecenia, kontekst lokalizacji, jawni kandydaci adresu, stan, zgodności i lista przeterminowanych nieopłaconych FV klienta.
- `GET /admin/shipping/orders/{id}/state` — lekki, bieżący stan MS używany przez okresowe odświeżanie i blokady interfejsu.
- `POST /admin/shipping/orders/{id}/review` — akceptacja danych i rezerwacji z kontrolą bieżącego fingerprintu lokalizacji.
- `POST /admin/shipping/shipments` — utworzenie przesyłki i etykiety.
- `POST /admin/shipping/shipments/consolidated` — utworzenie jednej przesyłki i etykiety dla 2–20 gotowych zleceń tego samego klienta na identyczny adres.
- `POST /admin/shipping/shipments/bulk` — sekwencyjne utworzenie etykiet dla wybranych albo wszystkich gotowych spraw.
- `POST /admin/shipping/shipments/manual-tracking` — rejestracja wyjątku wykonanego w panelu DPD.
- `POST /admin/shipping/orders/{id}/close` — potwierdzenie odbioru i finalizacja pojedynczego zlecenia albo wszystkich zleceń jego wspólnej paczki tym samym procesem RW, WZ albo FV + WZ co zamknięcie dnia.
- `GET /admin/shipping/shipments/{id}/label` — ponowny wydruk zapisanej etykiety A4.
- `GET /admin/shipping/shipments/packing-list` — osobne zestawienie zleceń i części na zwykły papier A4.
- `GET /admin/shipping/shipments/labels-sheet` — natywny arkusz A4 DPD dla wybranych numerów listów albo arkusz 2×2 w trybie `mock`.
- `GET /admin/shipping/shipments/print-bundle` — jeden PDF z tabelą kompletacyjną i wybranymi etykietami.
- `GET /admin/shipping/day-close` — podgląd paczek do przekazania.
- `POST /admin/shipping/day-close` — potwierdzenie odbioru i finalizacja dokumentów.

## Prototypy interfejsu

Trasa `/shipping/prototypes` prezentuje siedem niefunkcjonalnych kierunków wizualnych przyszłego modułu: operacyjny, etapowy, dyspozytorski, dokumentowy, minimalistyczny oraz dwie hybrydy. Wariant 06 zachowuje trzykolumnowy układ operacyjny 01 i stosuje ciemną kolorystykę 03. Wariant 07 zachowuje dokumentową kartę realizacji, rejestr i oś audytu z wariantu 04, również w kolorystyce 03. Każdy wariant zachowuje lewą kolejkę zleceń oraz pozwala przełączyć statyczny podgląd sekcji „Realizacja wysyłek”, „Katalog zgodności” i „Archiwum”. Przykładowe archiwum pokazuje operatora, zawartość paczki, dokument RW albo decyzję FV, numer przesyłki i datę wysłania.

Strona prototypów służy wyłącznie do porównywania układów. Jej skrypt nie wywołuje API, nie odczytuje Firebirda ani PostgreSQL i nie zapisuje decyzji. Proces operatora jest dostępny równolegle w dotychczasowym widoku `/shipping` oraz w wybranym wizualnie widoku `/shipping/v2`.

### Funkcjonalny widok V2

Trasa `/shipping/v2` wdraża wybrany wariant 07 jako równoległy, funkcjonalny interfejs. Korzysta z tych samych endpointów, sesji administratora i głównego skryptu `shipping.js` co dotychczasowy `/shipping`, dlatego akceptacja danych, wybór części, etykiety, wydruk zbiorczy, zamknięcie pojedynczego zlecenia, zamknięcie dnia oraz katalog zgodności działają według tej samej logiki. Pod tabelą magazynową znajduje się dynamiczna lista wszystkich części dodanych do paczki z indeksem, nazwą, ilością i jednostką; aktualizuje się przy zaznaczeniu, odznaczeniu oraz zmianie ilości. Funkcjonalna zakładka „Archiwum” pokazuje wszystkie zamknięte sprawy od najnowszych, rejestr dokumentów i części, zestaw filtrów oraz boczną kartę szczegółów. Rekord jest tylko do odczytu; można z niego ponownie otworzyć zapisaną etykietę DPD, ale nie zmienić danych procesu. Osobny skrypt `shipping-v2.js` nie wykonuje żądań sieciowych; wyłącznie synchronizuje boczny postęp, lokalizację, operatora i podsumowanie paczki z elementami działającego formularza.

Motyw V2 stosuje granatowo-grafitową kolorystykę wariantu 03, miętowe akcenty oraz subtelną siatkę techniczną z dwóch gradientów liniowych na tle przestrzeni roboczej. Etykiety systemowe, dane zlecenia, tabela części, Archiwum i panel audytu używają dodatkowo powiększonej typografii dostosowanej do pracy na ekranie stanowiska magazynowego. Skrypt zachowuje klasę `shipping-v2-location-note` podczas każdej aktualizacji lokalizacji, dzięki czemu także komunikat o braku lokalizacji pozostaje na ciemnym tle. Ostrzeżenie po godzinie granicznej ma przygaszone morsko-granatowe tło spójne z V2, natomiast rzeczywiste błędy zachowują czerwone oznaczenie. Dotychczasowy `/shipping` nie jest usuwany i zawiera link do V2; V2 pozwala wrócić do starego wyglądu.

## Aktywne środowisko testowe 2026-08-25

1. Produkcyjny Firebird `192.168.0.8:3050` został użyty wyłącznie jako źródło logicznego backupu `gbak -b -g`.
2. Backup produkcji odtworzono do lokalnego Firebirda 2.5.9 na serwerze `192.168.0.9`. Aktywny alias `BAZAMS_TEST` zawiera 81 292 zlecenia, 7 548 pozycji magazynowych i maksymalny `ZLECENIE.ID_ZLECENIE_TABLE=83440`.
3. Klient MS łączy się przez host `192.168.0.9`, port `3050` i ścieżkę `BAZAMS_TEST\\`; serwer obsługuje aliasy `BAZAMS_TEST` oraz `BAZAMS_TEST\\BazaMS.fdb`.
4. Backend `/shipping/v2` działa na porcie `8002` z PostgreSQL `ctip_test`, Firebirdem `BAZAMS_TEST`, lokalną symulacją DPD odpowiadającą `DPD_MODE=mock`, `SMS_TEST_MODE=true`, `FB_ALLOW_WRITES=true` i `SHIPPING_TEST_FIREBIRD_WRITES=true`.
5. Poprzednią bazę zachowano jako `BAZAMS_TEST_before_prod_refresh_20260825_1800.FDB`, jej logiczny backup jako `backups/BAZAMS_TEST_before_prod_refresh_20260825_175752.fbk`, a pobrany backup produkcji jako `backups/BAZAMS_PROD_20260825_175923.fbk` w katalogu runtime lokalnego Firebirda.
6. Test połączenia potwierdził słownik `TYP_US=8`, kolejkę 18 zleceń z ostatnich 30 dni oraz możliwość wykonania polecenia zapisu zakończonego rollbackiem. Dokument RW powinien zostać utworzony dopiero podczas ręcznego testu wybranego zlecenia w interfejsie.

## Konfiguracja i uruchomienie

1. Wykonaj `alembic upgrade head` na bazie testowej `ctip_test`.
2. Nadaj operatorom sekcję `shipping`; administrator otrzymuje ją automatycznie.
3. W zwykłym `.env.test` ustaw `DPD_ENABLED=true`, `DPD_MODE=mock`, `FB_ALLOW_WRITES=false`, `SHIPPING_TEST_FIREBIRD_WRITES=false` i `SMS_TEST_MODE=true`. Do kontrolowanego testu pełnego zapisu na świeżej kopii `BAZAMS_TEST` można czasowo ustawić obie flagi zapisu na `true`, zachowując pozostałe zabezpieczenia środowiska testowego.
4. Uruchom pierwszy skan katalogu na lokalnym Firebirdzie, przejrzyj sugestie i ręcznie potwierdź małą próbę mapowań.
5. Sprawdź kolejkę, walidację adresu, wybór części, etykietę A4 i zamknięcie dnia na lokalnym Firebirdzie.
6. Wzbogacanie WWW pozostaw domyślnie wyłączone. Włącz je przez `SHIPPING_COMPATIBILITY_WEB_ENABLED=true` dopiero po ustawieniu klucza OpenAI i zaakceptowaniu limitów.
7. Od opiekuna DPD uzyskaj osobno dla Demo i produkcji: login, hasło Basic Auth, Master FID do nagłówka `X-DPD-FID`, payer FID/numkat do pola `payerFID` oraz uprawnienia do `generatePackagesNumbers` i `generateSpedLabels`.
8. Dla pierwszej diagnostyki ustaw `DPD_MODE=demo`, uzupełnij dane dostępowe i użyj przycisku „Test DPD Demo”. Operacja wysyła wyłącznie adres Ksero-Partner jako nadawcę i odbiorcę oraz nie zapisuje danych do MS.
9. W pełnym teście zlecenia tryb Demo nadal zastępuje odbiorcę po stronie DPD adresem Ksero-Partner. Rzeczywisty adres klienta jest walidowany lokalnie, ale nie trafia do współdzielonego środowiska testowego przewoźnika.
10. Wydrukuj partie 1, 2, 3, 4 i 5 etykiet, sprawdź rozpoczęcie od pierwszego pola arkusza oraz zeskanuj wszystkie kody. Lista części musi zostać wydrukowana jako osobne zadanie na zwykłym papierze.
11. Dopiero po pilotażu ustaw produkcyjnie `DPD_MODE=production`, `DPD_ENABLED=true` i `FB_ALLOW_WRITES=true`.

DPD Polska publikuje aktualny opis DPD Services REST i udostępnia klucz przez opiekuna handlowego: <https://www.dpd.com/pl/pl/oferta-dla-firm/rozwiazania-it-dpd-polska/implementacja-wtyczek-web-service-dpd-polska/>.

## Drukowanie

DPD zwraca gotowy dokument PDF A4. Przy wydruku partii CTIP ponownie wywołuje wyłącznie `generateSpedLabels` dla istniejących numerów `waybill`; nie tworzy nowych przesyłek i nie skaluje samodzielnie kodów kreskowych. Dokument etykiet należy wysłać na arkusz samoprzylepny A4 2×2, a zestawienie kompletacyjne na zwykły papier jako osobne zadanie. Lokalny `mock` zachowuje ten sam podział pól, pokazuje skróconą listę części i przypomina właściwą etykietę, ale jego kod zawiera tylko numer `MOCK`, pole 2D jest celowo niedekodowalne, a wydruk ma trwałe ostrzeżenie testowe. Dla DPD Demo i produkcji CTIP przekazuje skrócony wykaz indeksów oraz ilości w standardowym polu `content` o maksymalnej długości 54 znaków; PDF zwrócony przez przewoźnika pozostaje niezmieniony, dlatego pełnym źródłem kompletacji nadal jest osobna lista części. Przed produkcją obowiązuje próbny wydruk oficjalnego PDF z DPD Demo i skan wszystkich rzeczywistych kodów. Docelowo możliwe jest przejście na **Zebra ZD421d, 203 dpi, druk termiczny bezpośredni, wariant z Ethernetem**, ale wymaga osobnego testu formatu `LBL_PRINTER`; zmiana formatu nie może tworzyć drugiej przesyłki.
