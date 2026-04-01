# Zadania planowane (stan na 2026-03-13)

## Priorytet biezacy
1. Ustalenie, czy cena z arkusza `Urzadzenia` dla proform ma byc traktowana jako
   brutto czy netto, i dopiecie tego jako stalej zasady lub ustawienia workflow.
2. Zastapienie tymczasowego URL podgladu A4 realnym plikiem PDF zapisywanym do
   sprawy workflow po utworzeniu proformy.
3. Ustalenie jednego zrodla prawdy dla urzadzen przeznaczonych do procesu
   bankowego i dalszego wdrozenia.
4. Dla automatycznego zakladania klienta dopisac na etapie formularza pola
   `nazwa skrocona` oraz `e-mail do faktur`, zasilane danymi klienta i
   zapisywane do Firebird dopiero po zatwierdzeniu docelowego workflow.

## Stabilizacja techniczna
6. Dodać konfigurację `pytest`, aby gołe `pytest` uruchamiało wyłącznie testy z
   katalogu `tests/` i nie zbierało plików pomocniczych z `docs/`.
7. Uporządkować warningi frameworka (`TemplateResponse`, `python_multipart`) i
   przygotować czystszy raport testów.
8. Dopiac aktualizacje dokumentacji operacyjnej po kazdej zmianie workflow.

## Dalsze etapy
9. Przygotowac model procesu bankowego po akceptacji dokumentow.
10. Zaprojektowac etap planowania dowozu, protokolu zdawczo-odbiorczego i
   powiadomienia klienta.
11. Zaprojektowac archiwizacje kompletu dokumentow lokalnie i w chmurze.
12. Zaprojektowac zapis umowy i oplat do Menadzera Serwisu po powrocie
    dokumentow.
13. Zaprojektowac wystawienie faktury VAT do banku.
