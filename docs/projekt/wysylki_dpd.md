# Automatyzacja wysyłek części i tonerów

## Cel modułu

Moduł `/shipping` automatyzuje realizację zleceń Menadżera Serwisu typu `TYP_US=8` (`dowóz materiałów`). Operator nie przepisuje ręcznie danych z MS do panelu przewoźnika. CTIP prowadzi kontrolowany proces: weryfikacja odbiorcy, wybór tonera, utworzenie przesyłki, wydruk etykiety A4, przekazanie kurierowi i zamknięcie dnia.

Moduł `/delivery` obsługujący dowozy urządzeń i workflow GRENKE pozostaje odrębnym procesem.

## Przepływ operacyjny

1. CTIP pobiera z lokalnego lub produkcyjnego Firebirda zlecenia `TYP_US=8` w stanie `O` albo `ZR`.
2. Operator otwiera zlecenie i porównuje lokalizację `ZLECENIE.STOI`/`MASZYNA.STOI` z danymi strukturalnymi zlecenia i klienta.
3. Zapisany, wcześniej zweryfikowany adres ma pierwszeństwo. Telefon jest obowiązkowy, e-mail opcjonalny. Obsługiwane są wyłącznie adresy krajowe `PL`.
4. CTIP pokazuje tonery z `MAGAZYN.ID_MAGAZYN=1`, odejmuje `IL_REZ` oraz miękkie rezerwacje aktywnych spraw CTIP.
5. Operator wybiera pozycje i może zapamiętać zgodność kartoteki tonera z `MASZYNA.ID_MODEL`.
6. Akceptacja danych tworzy sprawę w stanie `ready`. Dopiero wtedy dostępne jest generowanie etykiety.
7. Nadanie jest idempotentne. Odpowiedź DPD i etykieta PDF są zapisywane przed próbą aktualizacji Firebirda.
8. Produkcyjny zapis Firebird dodaje brakujące `ZPOZYCJA`, ustawia `ZLECENIE.PRZESYLKA`, `DATA_PRZES` i stan `ZR`.
9. Po fizycznym odbiorze paczek operator używa akcji „zamknij dzień”. Dla `RODZAJ_US='Umowa'` CTIP tworzy dokument `ROK` z dokładnie zaakceptowanych kartotek i ilości przesyłki, zapisuje `ZLECENIE.ID_RW` i stan `Z`. Zlecenia płatne trafiają do ręcznego rozliczenia i nie tworzą automatycznie faktury.
10. Po potwierdzeniu odbioru CTIP wysyła SMS oraz, jeżeli podano adres, e-mail z numerem przesyłki.

## Odporność na błędy

- Unikalny `idempotency_key` blokuje podwójne nadanie po ponownym kliknięciu lub odświeżeniu strony.
- Etykieta jest zawsze pobierana z zapisanego rekordu; ponowny wydruk nie tworzy nowej przesyłki.
- Jeżeli DPD utworzy przesyłkę, ale Firebird odrzuci zapis, sprawa otrzymuje stan `reconcile_required`. Operator widzi numer listu i błąd, a system nie ponawia automatycznie nadania.
- Zamknięcie dnia jest unikalne dla daty. Dokument RW jest tworzony tylko wtedy, gdy zlecenie nie ma już `ID_RW`.
- Tryb testowy generuje PDF z dużym napisem „ETYKIETA TESTOWA” i nie modyfikuje Firebirda.
- Tryb produkcyjny nadania jest blokowany, gdy `FB_ALLOW_WRITES=false`.

## Dane PostgreSQL

- `shipping_address` — zweryfikowane adresy lokalizacji.
- `shipping_consumable_compatibility` — potwierdzone mapowania model–toner.
- `shipping_case` — stan obsługi zlecenia MS i snapshot zaakceptowanych danych.
- `shipping_item` — wybrane pozycje i miękkie rezerwacje.
- `shipping_shipment` — identyfikatory DPD, numer listu, etykieta i statusy uzgodnienia.
- `shipping_day_close` — idempotentne zamknięcie dnia.
- `shipping_event` — niemodyfikowalny dziennik etapów i błędów.

## API

- `GET /admin/shipping/config` — stan konfiguracji bez sekretów.
- `GET /admin/shipping/queue` — kolejka zleceń z Firebirda.
- `GET /admin/shipping/orders/{id}` — źródła adresu, zapisany adres, stan i zgodności.
- `POST /admin/shipping/orders/{id}/review` — akceptacja danych i rezerwacji.
- `POST /admin/shipping/shipments` — utworzenie przesyłki i etykiety.
- `POST /admin/shipping/shipments/manual-tracking` — rejestracja wyjątku wykonanego w panelu DPD.
- `GET /admin/shipping/shipments/{id}/label` — ponowny wydruk zapisanej etykiety A4.
- `GET /admin/shipping/day-close` — podgląd paczek do przekazania.
- `POST /admin/shipping/day-close` — potwierdzenie odbioru i finalizacja dokumentów.

## Konfiguracja i uruchomienie

1. Wykonaj `alembic upgrade head` na bazie testowej `ctip_test`.
2. Nadaj operatorom sekcję `shipping`; administrator otrzymuje ją automatycznie.
3. W `.env.test` ustaw `DPD_ENABLED=true`, `DPD_TEST_MODE=true`, `FB_ALLOW_WRITES=false` i `SMS_TEST_MODE=true`.
4. Sprawdź kolejkę, walidację adresu, wybór tonera, etykietę A4 i zamknięcie dnia na lokalnym Firebirdzie.
5. Od opiekuna DPD uzyskaj osobny klucz API DPD Services REST. Dane logowania do panelu DPD nie są automatycznie kluczem API.
6. Na podstawie dokumentacji przypisanej do konta potwierdź ścieżki tworzenia przesyłki i pobierania etykiety. W razie różnic zmień `DPD_CREATE_SHIPMENT_PATH`, `DPD_LABEL_PATH_TEMPLATE` albo wyłącznie adapter `app/services/dpd_shipping.py`.
7. Uzupełnij dane nadawcy i wykonaj test na środowisku DPD wskazanym przez opiekuna.
8. Przeprowadź pilotaż pięciu przesyłek A4, porównując dane w CTIP, DPD i MS.
9. Dopiero po pilotażu ustaw produkcyjnie `DPD_TEST_MODE=false`, `DPD_ENABLED=true` i `FB_ALLOW_WRITES=true`.

DPD Polska publikuje aktualny opis DPD Services REST i udostępnia klucz przez opiekuna handlowego: <https://www.dpd.com/pl/pl/oferta-dla-firm/rozwiazania-it-dpd-polska/implementacja-wtyczek-web-service-dpd-polska/>.

## Drukowanie

Wersja pierwsza zapisuje i otwiera etykietę PDF A4 w przeglądarce. Nie wymaga instalacji dodatkowej drukarki ani usługi drukującej. Docelowo zalecana jest **Zebra ZD421d, 203 dpi, druk termiczny bezpośredni, wariant z Ethernetem**, z etykietami 100×150 mm. Szerokość druku 104 mm, obsługa ZPL/PDF Direct i dostępny interfejs sieciowy ułatwiają współdzielenie urządzenia oraz późniejszy wydruk bezpośredni z CTIP. Przed zakupem należy potwierdzić konkretny kod konfiguracji obejmujący Ethernet i wykonać próbę rzeczywistej etykiety DPD. Zmiana formatu nie może zmieniać logiki nadania ani tworzyć drugiej przesyłki.
