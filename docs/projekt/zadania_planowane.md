# Zadania planowane (stan na 2026-04-22)

## Priorytet biezacy
1. Rozbudowac publiczny formularz `/formularz/{token}` o kolejny pakiet interakcji:
   - walidacje inline w krokach,
   - czytelne prowadzenie po etapach,
   - szybszy powrot z podsumowania do edycji,
   - dopracowanie obslugi listy reprezentantow i komunikatow bledow.
   - utrzymac zgodnosc z nowa sekcja `/admin -> Obsluga formularza`, aby tresci komunikatow i link publiczny pozostawaly konfigurowalne bez zmian w kodzie.
2. Ustalenie, czy cena z arkusza `Urzadzenia` dla proform ma byc traktowana jako
   brutto czy netto, i dopiecie tego jako stalej zasady lub ustawienia workflow.
3. Ustalenie jednego zrodla prawdy dla urzadzen przeznaczonych do procesu
   bankowego i dalszego wdrozenia.
4. Dla automatycznego zakladania klienta dopisac na etapie formularza pola
   `nazwa skrocona` oraz `e-mail do faktur`, zasilane danymi klienta i
   zapisywane do Firebird dopiero po zatwierdzeniu docelowego workflow.
5. Dodac pelna paginacje proformy PDF (A4), tak aby przy wielu pozycjach
   dokument poprawnie lamal sie na kolejne strony bez nakladania tabel,
   podsumowan i numeru rachunku.

## Stabilizacja techniczna
6. Dodać konfigurację `pytest`, aby gołe `pytest` uruchamiało wyłącznie testy z
   katalogu `tests/` i nie zbierało plików pomocniczych z `docs/`.
7. Uporządkować warningi frameworka (`TemplateResponse`, `python_multipart`) i
   przygotować czystszy raport testów.
8. Dopiac aktualizacje dokumentacji operacyjnej po kazdej zmianie workflow.
9. Bezpieczenstwo i hardening API/formularzy (do wdrozenia pozniej):
   - rotacja sekretow i porzadek wokol plikow srodowiskowych,
   - zastapienie mechanizmu `X-User-Id` realna autoryzacja sesji/tokenu,
   - naprawa sciezki transakcyjnej formularza przy scenariuszu `ValueError`,
   - dodanie CSRF dla publicznego formularza,
   - dodanie rate limitingu na endpointach publicznych,
   - zaostrzenie polityki CORS (`allow_headers` ograniczone do wymaganych naglowkow).
10. Wydajnosc i utrzymanie (do wdrozenia pozniej):
   - przeglad i ewentualna eliminacja ryzyk N+1 przy listowaniu kontaktow,
   - dodanie indeksow dla `admin_audit_log.user_id` i `form_workflow_device.workflow_case_id`,
   - podzial `contracts_dashboard.py` na mniejsze, testowalne moduly.

## Dalsze etapy
11. Przygotowac model procesu bankowego po akceptacji dokumentow.
12. Zaprojektowac etap planowania dowozu, protokolu zdawczo-odbiorczego i
   powiadomienia klienta.
13. Zaprojektowac archiwizacje kompletu dokumentow lokalnie i w chmurze.
14. Zaprojektowac zapis umowy i oplat do Menadzera Serwisu po powrocie
    dokumentow.
15. Zaprojektowac wystawienie faktury VAT do banku.

## Zrealizowane (ostatnie etapy FLOW)
1. Zastapiono tymczasowy URL podgladu A4 realnym plikiem PDF proformy zapisywanym do sprawy workflow.
2. Dopracowano generator proformy do zgodnosci z docelowym ukladem i formatem A4.
3. Uporzadkowano domyslne nazewnictwo zapisywanego pliku proformy na bazie aliasu dokumentu (np. `20_proforma_2026.pdf`).
4. Domknieto etap arkusza GRENKE i cache statusow urzadzen po stronie FLOW (odswiezanie, metadane synchronizacji, endpoint administracyjny).
5. Dodano auto-start testowego Firebirda do startu uslug potrzebnych dla aplikacji w srodowisku testowym.
