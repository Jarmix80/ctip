# FLOW - stan prac na 2026-03-16

## Cel notatki
Ten plik utrwala stan modułu `/flow`, aby po restarcie sesji można było szybko wrócić do kontekstu bez ponownego rozpoznawania kodu i procesu.

## Co jest gotowe
- Widok `/flow` działa jako roboczy ekran procesu z dwiema sekcjami:
  - `Obsluga umow`
  - `Obsluga urzadzen`
- Przycisk `Dane` przy formularzu otwiera czytelny modal z pełnym payloadem formularza i kopiowaniem pojedynczych pól.
- Dla formularzy `SUBMITTED` działa osobny modal workflow:
  - podgląd skróconych danych klienta,
  - zapis klienta do Menadżera Serwisu albo potwierdzenie klienta już istniejącego,
  - zapis wyboru urządzeń wyłącznie po stronie CTIP,
  - reczne ceny `netto` i `brutto` dla kazdego wybranego urzadzenia,
  - realne wystawienie proformy w lokalnej Firebird,
  - opcje wystawienia proformy `na bank` (domyslnie wlaczona) albo na klienta z formularza,
  - link do podgladu A4 wygenerowanej proformy,
  - widoczny etap sprawy workflow,
  - reczny status biznesowy sprawy (`Robocza`, `Oczekuje na akceptacje`, `Zaakceptowano`, `Zerowka`, `Odrzucono`),
  - sekcja `Dane dla handlowca` z klientem, reprezentantami, urzadzeniami i numerem proformy.

## Nowe tabele CTIP
- `ctip.form_workflow_case`
  - jedna sprawa workflow na jeden formularz,
  - trzyma etap, status biznesowy, `firebird_client_id`, tryb klienta, dane proformy.
- `ctip.form_workflow_device`
  - trzyma urządzenia wybrane do sprawy po stronie CTIP,
  - obecnie źródło: arkusz Google `Urzadzenia`,
  - przechowuje tez reczna wycene `price_net` i `price_gross`.

## Migracja
- Alembic:
  - `c8d3c6f8f3a1_add_form_workflow_tables.py`
  - `9f6a8b7c2d41_add_flow_pricing_and_business_status.py`
- Migracja została już wykonana na środowisku testowym `ctip_test`.

## Endpointy workflow
- `GET /admin/contracts/forms/{id}/workflow`
  - szczegóły sprawy formularza `SUBMITTED`
- `POST /admin/contracts/forms/{id}/workflow/client`
  - tworzy albo potwierdza klienta w Menadżerze Serwisu i zapisuje powiązanie po stronie CTIP
- `POST /admin/contracts/forms/{id}/workflow/devices`
  - zapisuje wybrane urządzenia po stronie CTIP razem z reczna cena `netto/brutto`
- `POST /admin/contracts/forms/{id}/workflow/status`
  - zapisuje reczny status biznesowy sprawy
- `POST /admin/contracts/forms/{id}/workflow/proforma`
  - tworzy realna proforme w lokalnej Firebird,
  - przyjmuje opcjonalne body `{ "for_bank": true|false }` (domyslnie `true`),
  - przy `for_bank=true` dokument jest wystawiany na klienta bankowego `GRENKELEASING Sp. z o.o.` (priorytetowo `ID_KLIENT=855`),
  - generuje fizyczny plik PDF w `inbox/faktura/generated/proforma_<ID>.pdf`,
  - zapisuje numer dokumentu i sciezke PDF w sprawie CTIP,
  - udostepnia plik przez `GET /flow/proforma/{id}/pdf`

## Stan formularzy testowych
- `ID 4`
  - nowy klient, brak w Menadżerze Serwisu
  - scenariusz bazowy do dalszej pracy
- `ID 5`
  - klient zgodny z Menadżerem Serwisu
- `ID 6`
  - zgodny NIP, ale rozbieżna nazwa/adres
- `ID 7`
  - zgodny NIP, ale rozbieżne dane względem istniejącego klienta
- `ID 9`
  - nowy klient, brak w Menadżerze Serwisu
  - `1` reprezentant
- `ID 10`
  - nowy klient, brak w Menadżerze Serwisu
  - `2` reprezentantow
- `ID 11`
  - nowy klient, brak w Menadżerze Serwisu
  - `3` reprezentantow

## Otwarta decyzja architektoniczna
Najważniejsza nierozstrzygnięta decyzja dotyczy źródła urządzeń dla workflow:
- wariant 1: nadal arkusz Google,
- wariant 2: przejście na Menadżer Serwisu magazyn `28`.

Od tej decyzji zależy:
- czy filtry `A4/A3` i `mono/kolor` mają sens już teraz,
- czy wybór urządzenia będzie oparty o dane arkusza, czy o byt magazynowy Firebird,
- jak później wystawić proformę bez ręcznego przepisywania danych.

## Rzeczy jeszcze niegotowe
- rozpoznawanie rozbieżności danych klienta przy zgodnym NIP,
- pola `nazwa skrocona` i `e-mail do faktur` jako stały element procesu zakładania klienta.

## Wizualizacja dokumentu
- Dodano osobny podgląd dokumentu:
  - `/flow/proforma-wizualizacja`
- Dodano też wariant bliższy oryginalnemu wydrukowi:
  - `/flow/proforma-wizualizacja1`
- Strona jest oparta o realny dokument z `inbox/faktura`:
  - numer `2/proforma/2026`
  - klient `ZANOX SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ`
  - pozycja `IMCTEST`
  - numer seryjny `test12345`

To nie sa backendowe szablony PDF. To wzorce wizualne do dalszego spięcia z danymi z Firebird i workflow CTIP:
- wariant bazowy: bardziej nowoczesny i czytelny,
- wariant `1`: bardziej zbliżony do oryginału z Menadżera Serwisu.
- oba warianty mają już przycisk `Zapisz PDF A4`, który korzysta z przeglądarkowego wydruku z arkuszem stylów ustawionym na stronę A4.
- Dodatkowo jest juz dynamiczny podglad realnej proformy:
  - `/flow/proforma/{id}?variant=base`
  - `/flow/proforma/{id}?variant=v1`
  - `/flow/proforma/{id}/pdf` (fizyczny plik PDF z backendu)

## Zalozenie cenowe biezacej implementacji
- Dla proformy tworzonej z `/flow` backend preferuje recznie zapisane ceny `price_gross` lub `price_net`.
- Jezeli operator nie wpisze ceny recznej, fallbackiem pozostaje cena z arkusza Google `Urzadzenia`, nadal interpretowana jako brutto.
- Backend rozbija wartosc na `netto` i `VAT` wedlug `VAT_STAWKA` pozycji `MAGAZYN`.
- Jezeli w arkuszu brakuje ceny, backend probuje uzyc `MAGAZYN.CENA_BRUTTO`.

## Najbliższy sensowny krok
1. Rozbudowac workflow o rozpoznawanie rozbieznosci danych klienta przy zgodnym NIP.
2. Dopiac kolejne etapy po proformie: `zaakceptowano -> zerowka -> dowoz -> archiwum`.
3. Uporzadkowac docelowy format PDF (uklad finalny i ew. wiele stron przy dluzszych pozycjach).

## Punkt wznowienia po resecie sesji
- Wejście robocze:
  - `http://192.168.0.9:8000/flow`
- Formularz referencyjny do dalszej pracy:
  - `ID 4` jako bazowy przypadek nowego klienta bez rekordu w Menadżerze Serwisu
  - `ID 9-11` jako nowe przypadki z 1, 2 i 3 reprezentantami, rowniez bez rekordu w Menadżerze Serwisu
- Wizualizacje proformy:
  - `http://192.168.0.9:8000/flow/proforma-wizualizacja`
  - `http://192.168.0.9:8000/flow/proforma-wizualizacja1`
- Pamięć sesji:
  - `.codex/session.json`
- Dokument procesu:
  - `docs/firebird/proces_sprzedazy_ms.md`
- Notatka robocza FLOW:
  - `docs/projekt/flow_status_2026-03-16.md`
