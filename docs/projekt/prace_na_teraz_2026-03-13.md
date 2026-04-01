# Prace na teraz – proces obslugi umowy (stan na 2026-03-13)

## Cel etapu
Uporzadkowac i wdrozyc srodkowy odcinek procesu handlowo-wdrozeniowego pomiedzy
formularzem klienta a przygotowaniem dokumentow dla banku i logistyki dowozu.

## Stan obecny
1. Generowanie formularza przez handlowca jest gotowe.
2. Wypelnienie formularza przez klienta i zapis do systemu jest gotowe.
3. Modul `Obsluga umow` pokazuje formularze `SUBMITTED`, dane klienta oraz probe
   dopasowania klienta i urzadzen do danych Firebird.
4. Akcje w module umow sa nadal w trybie bezpiecznym i nie wykonują jeszcze
   rzeczywistych zapisow do Firebird ani workflow dokumentowego.

## Proces docelowy
1. Handlowiec generuje formularz dla klienta.
2. Klient wypelnia formularz, a dane trafiaja do CTIP.
3. Handlowiec przygotowuje dokumenty bankowe na podstawie formularza, podpina
   fakture proforma i konkretne urzadzenie magazynowe z wiarygodnym numerem seryjnym.
4. Bank akceptuje dokumenty i odsylka stanowi sygnal do uruchomienia procesu dowozu.
5. Termin dowozu trafia do ewidencji i sluzy do przygotowania protokolu
   zdawczo-odbiorczego oraz informacji dla klienta.
6. Nastepuje dowoz urzadzenia.
7. Komplet dokumentow wraca do firmy, jest archiwizowany lokalnie i w chmurze,
   a dane umowy trafiaja do Menadzera Serwisu.
8. Wystawiana jest faktura VAT do banku.

## Zakres prac na teraz
### Etap A – weryfikacja proformy w Menadzerze Serwisu
1. Ustalic, czy Menadzer Serwisu generuje fakture proforma automatycznie, czy
   wymaga recznego uzupelnienia danych handlowych i magazynowych.
2. Zidentyfikowac tabele, pola i relacje Firebird odpowiedzialne za:
   - klienta,
   - urzadzenie,
   - dokument proforma,
   - powiazanie dokumentu z umowa lub zleceniem.
3. Zweryfikowac, czy proforma moze byc wygenerowana na podstawie samego klienta
   i urzadzenia, czy potrzebne sa dodatkowe encje posrednie.

### Etap B – zrodlo urzadzen z gwarancja oryginalnosci
1. Ustalic system referencyjny dla urzadzen:
   - magazyn/ERP,
   - arkusz `Urzadzenia`,
   - Menadzer Serwisu,
   - osobny rejestr dostaw.
2. Okreslic, ktore zrodlo jest nadrzedne dla numeru seryjnego i statusu
   dostepnosci urzadzenia.
3. Zdefiniowac minimalny zestaw pol wymaganych do bezpiecznego przypisania
   urzadzenia:
   - numer seryjny,
   - ewidencja,
   - model,
   - pochodzenie partii / dokument magazynowy,
   - status dostepnosci.
4. Ograniczyc UI i workflow tak, aby handlowiec nie mogl wybrac urzadzenia
   spoza zrodla referencyjnego.

## Proponowany plan wdrozenia
1. Rozpoznanie Firebird:
   - przejrzec aktualne zapytania i tabele wykorzystywane przez modul umow,
   - dopisac dokumentacje mapowania encji klient/urzadzenie/proforma.
2. Model danych procesu:
   - opisac, jaki rekord jest tworzony na kazdym kroku,
   - wskazac moment przejscia z `SUBMITTED` do etapu bankowego.
3. Integracja UI:
   - rozszerzyc `Obsluga umow` o sekcje wyboru urzadzenia referencyjnego,
   - dodac statusy procesu bankowego i kompletacji dokumentow.
4. Integracja zapisu:
   - dopiero po rozpoznaniu schematu uruchomic realne akcje
     `utworz_klienta`, `podlacz_klienta` i rejestracje urzadzenia/proformy.
5. Testy:
   - testy jednostkowe dla nowych akcji,
   - test scenariusza `formularz -> klient -> urzadzenie -> status bankowy`.

## Decyzje robocze
- Na tym etapie nie wdrazamy jeszcze procesu dowozu.
- Nie wdrazamy jeszcze archiwizacji dokumentow ani faktury VAT do banku.
- Najpierw zamykamy obszar klient + proforma + urzadzenie z wiarygodnym numerem seryjnym.

## Najblizsze zadania implementacyjne
1. Sprawdzic schemat Firebird pod katem proform i powiazan magazynowych.
2. Opisac i uzgodnic jedno zrodlo prawdy dla urzadzen.
3. Rozszerzyc backend `Obsluga umow` o dane potrzebne do wyboru urzadzenia.
4. Dopiero potem uruchomic realny zapis do Firebird.
