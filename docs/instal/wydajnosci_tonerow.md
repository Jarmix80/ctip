# Wydajności tonerów — instrukcja administratora

## Przeznaczenie i źródła

Katalog CTIP przechowuje nominalną, deklarowaną wydajność wkładu, a nie gwarantowaną liczbę rzeczywiście wykonanych kopii. Rozróżnia `confirmed` (potwierdzone źródłem), `estimated` (szacunek) i `missing` (brak). Norma lub pokrycie strony pozostają puste, jeżeli źródło ich nie podaje. Status potwierdzenia opisuje jakość źródła, nie pomiar eksploatacyjny.

Kartoteka magazynowa może obejmować wiele wariantów. Oddzielne dowody zachowują oznaczenia wkładu, źródło, wydajność i warunki deklaracji. Zgodnie z przyjętą polityką wspólny szacunek jest niższą udokumentowaną wartością zgodnych wariantów tego samego koloru i rodziny. Nie należy kopiować wartości między niezgodnymi wkładami ani przedstawiać szacunków jako danych producenta.

Wartości i historia są zapisane w PostgreSQL, bez aktualizacji MS, Optimy oraz surowej telemetrii. Edycja nie zmienia dokumentów RW/WZ/FV ani ilości magazynowych i nie blokuje zamawiania tonerów.

## Zakres i odświeżanie

Odczyt MS wymusza transakcję tylko do odczytu. Aktywne urządzenie wymaga `MASZYNA.AKTYWNA='TAK'` oraz aktywnej umowy po połączeniu `MASZYNA.ID_UMOWACPC=UMOWACPC.ID_UMOWACPC_TABLE`. Nazwa modelu jest również sprawdzana dla urządzeń bez słownikowego ID; znaku plus nie usuwa się z nazw Develop. Powiązania podejrzane pozostają do sprawdzenia.

Odświeżanie aktualizuje nazwy, ilości i zakres umów, ale nie wartości wydajności. Zachowuje kartoteki poza zakresem i poprzedni kompletny odczyt w razie awarii. Czas ostatniej udanej synchronizacji znajduje się w `admin_setting.shipping.toner_yields_sync`. Początkowa analiza obejmowała 111 kartotek aktywnych umów, 75 ustalonych wydajności, 36 braków i jedną dodatkową kartotekę z konfliktem; liczby te nie są stałymi aplikacji.

## Import kontrolowany

Pakiet prywatny JSON ma `format: ctip-toner-yields-v1` i tablicę `items`. Każda pozycja zawiera `item_id` (techniczny identyfikator kartoteki MS), `warehouse_id`, nazwę, metadane, modele `{brand, model, id?}`, dowody `evidence` i opcjonalne `value` z polami `pages`, `status`, `source`, `basis`, `reason`. Numery dokumentów źródłowych są metadanymi dowodów, nie załącznikami publicznymi. Nie zapisuje się pakietu, faktur ani danych klientów w Git.

Po aktywacji `.venv`:

```bash
python scripts/import_toner_yields.py --env-file /sciezka/ctip/.env.test --input inbox/toner_yields.json
python scripts/import_toner_yields.py --env-file /sciezka/ctip/.env.test --input inbox/toner_yields.json --apply
```

Na Windows produkcja wymaga jawnego `--production --env-file D:\CTIP\.env`. Bez `--apply` cały import jest wycofywany. Przy błędzie żadna część pakietu nie zostaje zatwierdzona. Powtórzenie nie powiela dowodów i historii. Import uzupełnia wyłącznie brakujące, nieręczne wartości; istniejące decyzje pozostają nietknięte, a nowe źródła są zachowane do przeglądu.

## Bezpieczeństwo i utrzymanie

### Dashboard Shipping

Zakładka **Wydajności tonerów** jest dostępna w obu wyglądach Shipping; odnośnik bezpośredni: `/shipping?view=toners`. Wyszukiwarka obejmuje modele, nazwy, indeksy, SKU i EAN. Filtry obejmują aktywność umów, kolor, markę, dostawcę, rodzaj wkładu, jakość danych i dodatni stan magazynu. Domyślnie widoczne są aktywne umowy, również ze stanem zero. Kafelki pokazują kompletność dla bieżących filtrów; udział szacunków nie jest wliczany do potwierdzeń.

Kliknięcie nazwy otwiera kartę z edytorem, źródłami i historią. Zapis wymaga uzasadnienia. Konflikt równoczesnej edycji zachowuje wpisany tekst i wymaga wczytania nowszej wersji. Zmiana na „Brak danych” usuwa bieżącą liczbę, ale nie historię ani źródła. Przycisk „Odśwież dane z MS” wykonuje odczyt na żądanie; nie uruchamia nowego harmonogramu i nie zmienia ręcznych wydajności.

Prawo odczytu wymaga sekcji Shipping, również dla administratora. Edycja i odświeżanie wymagają roli administratora lub flagi **Edycja wydajności tonerów** w ustawieniach użytkownika. Flaga nie zastępuje dostępu do Shipping, nie tworzy nowej sekcji menu i jest domyślnie wyłączona. Starszy klient aktualizujący użytkownika bez tego pola zachowuje jego dotychczasową wartość.

API pod `/admin/shipping/toner-yields` obsługuje `GET` listy, `GET /{item_id}`, `GET /{item_id}/history`, `PATCH /{item_id}` i `POST /refresh`. Korekta zawiera `revision`, `pages`, `status`, `source`, `basis`, `reason`. Błędy: 403 brak uprawnień, 404 niewłaściwa kartoteka, 409 konflikt wersji, 422 niepoprawne dane, 503 niedostępny odczyt MS. Nie ma publicznego importu faktur ani automatycznego odpytywania WWW.

Zmiana wydajności wymaga aktualnej wersji, uzasadnienia, a dla wartości potwierdzonej źródła i dla szacunku podstawy oszacowania. Wartość i audyt są zatwierdzane razem. Równoległa korekta kończy się konfliktem zamiast utraty danych.

Migracja `b7e2d4f6a810` dodaje tabele i domyślnie wyłączoną flagę użytkownika `can_edit_toner_yields`. Wycofanie aplikacji pozostawia tabele i historię. Nie wykonuje się automatycznego destrukcyjnego downgrade. Standardowe logi aplikacji w `docs/LOG` są rotowane dziennie (`*_YYYY-MM-DD.log`); każdy wpis ma znacznik czasu. Logi importu nie ujawniają haseł połączeń.
