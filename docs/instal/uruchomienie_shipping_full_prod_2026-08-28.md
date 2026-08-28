# Pełne uruchomienie produkcyjne modułu Shipping

## Cel

Procedura ustawia funkcjonalny wariant V2 jako główny widok `/shipping`, zachowuje poprzedni interfejs pod `/shipping/legacy` i przygotowuje produkcję do kontrolowanego pilota RW, WZ oraz FV z WZ. Uruchomienie jest rozdzielone na fazę gotowości i późniejszy pilot. Przed jawnym komunikatem o gotowości nie wolno tworzyć zleceń pilotażowych.

Zmiana kodu nie modyfikuje schematu PostgreSQL. Oczekiwana rewizja Alembic to `a7c4e2f9b1d3`. Podczas wdrożenia restartowana jest wyłącznie usługa `CTIP-Web`.

## Stan wejściowy

- produkcyjny commit bazowy: `c206a876aeeaa5b0ed802fb24feb03b323cfca0b`,
- rewizja Alembic: `a7c4e2f9b1d3`,
- `SHIPPING_ENABLED=true`,
- `SHIPPING_CATALOG_MUTATIONS_ENABLED=true`,
- `SHIPPING_FULFILLMENT_ENABLED=false`,
- `DPD_ENABLED=false`,
- `DPD_MODE=production`,
- `FB_ALLOW_WRITES=true`,
- `SHIPPING_TEST_FIREBIRD_WRITES=false`,
- `SMS_TEST_MODE=false`,
- kompletne dane dostępowe i dane nadawcy DPD,
- działające `CTIP-Web`, `CTIP-FormsPublic`, `CollectorService` oraz `CTIP-SMS`,
- aktywne konto Marcina z sekcją `shipping`.

W Menadżerze Serwisu istnieją przygotowane dane techniczne:

- klient `Test Umowa`,
- fikcyjna umowa `test01`,
- urządzenie o numerze `7222` przypisane do umowy.

Zlecenia pilotażowe nie mogą istnieć przed zakończeniem fazy gotowości.

## Wydanie kodu

W czystym worktree należy uruchomić:

```bash
source .venv/bin/activate
pre-commit run --all-files
pytest tests/test_shipping.py tests/test_shipping_release.py tests/test_settings.py tests/test_api_auth.py
git status --short
git push -u origin feature/shipping-full-rollout-2026-08-28
```

Skrypt produkcyjny:

```text
scripts/windows/deploy_shipping_full_prod_2026-08-28.ps1
```

Na serwerze Windows należy pobrać go z zatwierdzonego commita, wykonać najpierw dry-run, a następnie uruchomić z `-Apply`. Skrypt:

1. sprawdza bieżący commit, Alembic, konfigurację i czystość repozytorium;
2. wymaga wyłączonej realizacji i DPD;
3. wykonuje pełny backup PostgreSQL oraz Firebird;
4. uruchamia kandydata na `127.0.0.1:8002`;
5. sprawdza `/health`, `/shipping`, `/shipping/legacy` oraz alias `/shipping/v2`;
6. zatrzymuje wyłącznie `CTIP-Web`;
7. przełącza produkcję na dokładny commit release;
8. uruchamia `CTIP-Web` i powtarza kontrole;
9. przy błędzie cutover przywraca commit `c206a876aeeaa5b0ed802fb24feb03b323cfca0b`.

## Faza gotowości

Po wdrożeniu nadal obowiązują:

```dotenv
SHIPPING_FULFILLMENT_ENABLED=false
DPD_ENABLED=false
```

Należy wykonać następujące kontrole:

1. `/shipping` renderuje funkcjonalny wariant V2.
2. `/shipping/legacy` renderuje poprzedni widok.
3. `/shipping/v2` przekierowuje do `/shipping`.
4. Kolejka pokazuje wyłącznie otwarte zlecenia dowozu materiałów z dozwolonym technikiem.
5. Konfiguracja raportuje gotowość Firebird do zapisu i kompletność DPD bez ujawniania sekretów.
6. Tabele Shipping nie zawierają stanów `failed` ani `reconcile_required`.
7. Działają `/operator`, `/genform`, `/flow`, `/device` i publiczne formularze.
8. Działają wszystkie cztery usługi oraz automaty SMS, e-mail i backupów.
9. Produkcyjny skan katalogu został wykonany bez importowania lokalnych danych testowych.
10. Oczywiste mapowania dla modeli z bieżącej kolejki zostały zatwierdzone; niepewne relacje pozostają sugestiami.

Dopiero po przejściu wszystkich punktów należy przekazać Marcinowi komunikat:

> Shipping jest gotowy — możesz teraz utworzyć trzy zlecenia testowe.

## Dane kontrolne przed pilotem

Po utworzeniu zleceń, ale przed ich akceptacją w Shipping, należy odczytowo zapisać:

- `KLIENT.ID_KLIENT_TABLE` i `KLIENT.ID_KLIENT` klienta `Test Umowa`,
- identyfikator umowy `test01` i nazwę tabeli `UMOWA` albo `UMOWACPC`,
- `MASZYNA.ID_MASZYNA_TABLE` i `MASZYNA.ID_MASZYNA` urządzenia `7222`,
- `ZLECENIE.ID_ZLECENIE_TABLE`, numer i rok każdego z trzech zleceń,
- identyfikatory trzech pozycji `MAGAZYN`,
- `MAGAZYN.ILOSC`, `MAGAZYN.IL_REZ` i stan dostępny każdej pozycji.

Każde zlecenie ma używać innej części, ilości `1`, dodatniego stanu i potwierdzonego mapowania. Wszystkie trzy zlecenia używają kontrolowanego adresu oraz telefonu i e-maila Marcina.

## Pilot produkcyjny

Przygotowane zlecenia obejmują:

1. zlecenie umowne bez FV, kończące się RW;
2. zlecenie poza umową z odznaczoną FV, kończące się WZ;
3. zlecenie poza umową z zaznaczoną FV, kończące się FV oraz powiązanym WZ.

Przed pilotem należy ustawić:

```dotenv
SHIPPING_FULFILLMENT_ENABLED=true
DPD_ENABLED=true
```

Następnie restartuje się wyłącznie `CTIP-Web` i potwierdza działanie pozostałych usług.

Kolejność pilota:

1. zaakceptować dane i części trzech zleceń;
2. dla zlecenia RW utworzyć osobną etykietę;
3. dla zleceń WZ i FV z WZ utworzyć jedną wspólną paczkę;
4. potwierdzić dwa unikalne numery DPD i trzy rekordy przesyłek CTIP;
5. wydrukować arkusz dwóch etykiet i listę kompletacyjną trzech zleceń;
6. sprawdzić po utworzeniu etykiet stan `ZR`, `ZPOZYCJA`, numery przesyłek i brak przedwczesnych dokumentów końcowych;
7. zamknąć zlecenie RW przyciskiem pojedynczego zlecenia;
8. zamknąć wspólną paczkę przez akcję masową;
9. sprawdzić jeden RW, dwa WZ, jedną FV, numer FV w `ZAKUPY.DOK_ZEW` powiązanego WZ, `DATA_PRZES`, `WYKONANIE`, stan `Z` i ceny;
10. potwierdzić jednokrotne zmniejszenie każdego stanu magazynowego;
11. potwierdzić trzy wpisy Archiwum oraz statusy wysłanych SMS-ów i e-maili;
12. wymagać zera spraw `failed` i `reconcile_required`.

## Obowiązkowe sprzątanie

Pilota nie uznaje się za zakończony przed pełnym sprzątnięciem danych testowych. Operacje usuwania wykonuje ręcznie Marcin w Menadżerze Serwisu i panelu DPD. Codex wykonuje wyłącznie późniejszą kontrolę odczytową.

Kolejność ręczna:

1. anulować oba numery przesyłek w panelu DPD;
2. usunąć testową FV;
3. usunąć WZ powiązany z FV;
4. usunąć osobny WZ;
5. usunąć RW;
6. po każdym dokumencie sprawdzić zmianę stanu odpowiedniej części;
7. usunąć trzy testowe zlecenia i ich pozycje;
8. odpiąć urządzenie `7222` od umowy, jeśli wymaga tego interfejs MS;
9. usunąć umowę `test01`;
10. pozostawić urządzenie `7222` i klienta `Test Umowa` jako kontrolowane dane do przyszłych testów.

Po usunięciu FV należy potwierdzić, że `MAGAZYN.ILOSC` pozycji nie ma wartości `NULL`. Generator Shipping zapisuje `FPOZYCJA.PARAGON=0`, ponieważ trigger `DEL_FPOZYCJA` odejmuje to pole podczas odtwarzania stanu magazynowego.

Kontrola odczytowa po sprzątaniu musi potwierdzić:

- brak obu aktywnych przesyłek po stronie DPD;
- brak zapisanych dokumentów RW, WZ i FV po ich identyfikatorach;
- brak trzech zleceń i powiązanych `ZPOZYCJA`;
- brak umowy `test01` w rozpoznanej tabeli;
- obecność urządzenia `7222` i klienta `Test Umowa`, ale bez umowy `test01`, zleceń pilota i dokumentów pilota;
- brak osieroconych odwołań do umowy, zleceń i dokumentów;
- dokładny powrót `MAGAZYN.ILOSC`, `MAGAZYN.IL_REZ` i stanu dostępnego każdej części do wartości początkowych.

Rekordy Archiwum Shipping pozostają jako trwały audyt pilota i nie są usuwane.

## Zadania po pilocie

Po zakończeniu pilota i potwierdzeniu pełnego odtworzenia stanów magazynowych należy wykonać osobne zadania interfejsowe:

1. Rozszerzyć przycisk odświeżania kolejki o pełną synchronizację z Menadżerem Serwisu: ponowny odczyt całej kolejki, dodanie nowych zleceń oraz bezpieczne usunięcie z roboczego widoku CTIP spraw, których nie ma już w źródle. Rekordów Archiwum i trwałego audytu nie wolno usuwać.
2. Poprawić wysokość i przewijanie lewej kolejki zleceń, aby ostatni wiersz był zawsze widoczny w całości niezależnie od rozdzielczości, skali systemowej i wysokości okna przeglądarki.
3. Wysyłać jedno powiadomienie SMS na wspólną paczkę zamiast osobnego SMS-a dla każdego zlecenia tego samego odbiorcy.
4. Uzgodnić `EMAIL_SENDER_ADDRESS` z kontem uwierzytelnianym przez SMTP; serwer odrzucił nadawcę nieprzypisanego do aktywnego użytkownika.
5. Wdrożyć poprawki generatora FV z WZ: numer FV w `ZAKUPY.DOK_ZEW` oraz `FPOZYCJA.PARAGON=0`, wymagane do prawidłowego odtworzenia stanu przy ręcznym usunięciu dokumentów.
6. Opisać w interfejsie cykl niewykorzystanej etykiety DPD Services API; CTIP nie generuje protokołu ani podjazdu kuriera, a samo API nie udostępnia usunięcia wygenerowanego numeru listu.

Identyfikatory i stany początkowe bieżącego pilota zapisano w `docs/instal/pilot_shipping_2026-08-28.md`.

## Udostępnienie operatorom

Dopiero po poprawnym pilocie i kontroli sprzątania należy nadać sekcję `shipping` aktywnym kontom Joanny Gostyńskiej i Agnieszki Gołembiewskiej. Nie wolno zmieniać ich ról ani innych sekcji.

Pierwszego dnia należy sprawdzać każde zamknięcie. Przez kolejne trzy dni kontroluje się:

- błędy Shipping i `reconcile_required`,
- dokumenty Firebird i stany magazynowe,
- DPD, SMS i e-mail,
- backupy,
- `CTIP-Web`, `CTIP-FormsPublic`, `CollectorService` oraz `CTIP-SMS`.

## Blokada awaryjna

Przy pierwszym błędzie należy ustawić:

```dotenv
SHIPPING_FULFILLMENT_ENABLED=false
DPD_ENABLED=false
```

Następnie restartuje się wyłącznie `CTIP-Web`. Nie wolno automatycznie ponawiać operacji posiadającej numer DPD albo dokument Firebird. Kod można cofnąć do `c206a876aeeaa5b0ed802fb24feb03b323cfca0b`; tabel Shipping i Archiwum nie należy usuwać.
