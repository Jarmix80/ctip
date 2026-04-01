# Plan naprawczy modeli produkcyjnych 2026-03-18

## Zakres

- Źródło: produkcyjna Firebird Menadżera Serwisu, odczyt sieciowy.
- Zakres marek: wyłącznie producenci urządzeń drukujących i kserokopiarek.
- Cel: przygotowanie hurtowego mapowania `MASZYNA.ID_MODEL`, identyfikacja braków w `MODEL` oraz osobnych wyjątków w `MAGAZYN`.

## Najważniejsze liczby

- `MAGAZYN.ID_MODEL IS NULL`: 6984
- `MASZYNA.ID_MODEL IS NULL`: 5677
- `MASZYNA` bez `ID_MODEL`, ale z czytelnym `MARKA+MODEL`: 5621
- Relewantne sygnatury marek drukujących w `MASZYNA`: 422
- Top 20 sygnatur obejmuje: 2684 rekordów z 5361

## Co mogę wykonać sam

- Hurtowe ustawienie `MASZYNA.ID_MODEL` dla wszystkich wierszy, które dostaną od Ciebie zatwierdzony `decyzja_id_model` w CSV.
- Uzupełnienie brakujących pól w `MODEL` dla rekordów jednoznacznych, jeśli podasz brakujące wartości.
- Uzupełnienie dwóch wyjątków w `MAGAZYN` po ręcznej identyfikacji modelu.

## Czego potrzebuję od Ciebie

- Wybór kanonicznego `ID_MODEL` dla wierszy ze statusem `decyzja_duplikat`.
- Brakujące wartości `GRUPA`, `RODZAJ`, `KOLOR`, `PLIK` dla wierszy ze statusem `jednoznaczny_niepelny` albo `brak_modelu`.
- Potwierdzenie, czy dwa rekordy seryjne w `MAGAZYN` z `KP/4416` mapujemy do `IM 2500` i jakiego `ID_MODEL` użyć.

## Statusy w CSV

- `gotowy_jednoznaczny`: 56
- `jednoznaczny_niepelny`: 295
- `decyzja_duplikat`: 71
- `brak_modelu`: 0

## Top 20 sygnatur do pierwszej fali naprawy

| Priorytet | Rodzina | Model | Liczba rekordów | Kandydaci | Status |
|---|---|---|---:|---|---|
| WYSOKI | RICOH | MPC 2500 | 256 | 383(3/4) | jednoznaczny_niepelny |
| WYSOKI | RICOH | MP 301 | 254 | 472(4/4) | gotowy_jednoznaczny |
| WYSOKI | RICOH | MPC 307 | 222 | 545(4/4), 571(4/4), 392(3/4), 602(3/4) | decyzja_duplikat |
| WYSOKI | RICOH | MPC 3003 | 196 | 628(4/4), 30002488(4/4), 389(3/4), 560(3/4) | decyzja_duplikat |
| WYSOKI | RICOH | MPC 2003 | 180 | 378(4/4), 622(4/4), 30002487(4/4) | decyzja_duplikat |
| WYSOKI | RICOH | MP 161 | 145 | 338(3/4) | jednoznaczny_niepelny |
| WYSOKI | RICOH | IMC 3000 | 133 | 539(4/4), 30002504(4/4), 30002508(4/4) | decyzja_duplikat |
| WYSOKI | RICOH | MP 171 | 121 | 339(3/4), 610(3/4) | decyzja_duplikat |
| WYSOKI | RICOH | MPC 306 | 120 | 489(4/4), 572(4/4) | decyzja_duplikat |
| WYSOKI | RICOH | MPC 3004 | 119 | 390(4/4), 543(4/4), 600(4/4) | decyzja_duplikat |
| WYSOKI | RICOH | MPC 3001 | 117 | 388(3/4) | jednoznaczny_niepelny |
| WYSOKI | RICOH | MPC 2011 | 114 | 380(3/4), 624(3/4) | decyzja_duplikat |
| WYSOKI | RICOH | MP 2000 | 113 | 340(3/4) | jednoznaczny_niepelny |
| WYSOKI | RICOH | DSM 415 | 108 | 318(4/4), 139(3/4) | decyzja_duplikat |
| WYSOKI | RICOH | MP 201 | 103 | 342(3/4), 612(3/4) | decyzja_duplikat |
| SREDNI | RICOH | IM 350 | 94 | 458(3/4), 576(3/4) | decyzja_duplikat |
| SREDNI | RICOH | IMC 300 | 79 | 30002496(3/4), 30002503(3/4) | decyzja_duplikat |
| SREDNI | RICOH | DSM 622 | 74 | 325(4/4), 334(2/4), 559(2/4) | decyzja_duplikat |
| SREDNI | RICOH | MP 2510 | 69 | 349(3/4) | jednoznaczny_niepelny |
| SREDNI | RICOH | MP 305+ | 67 | 374(4/4), 592(4/4), 473(3/4) | decyzja_duplikat |

## Pliki robocze

- `docs/projekt/plan_naprawczy_modeli_produkcyjnych_2026-03-18_mapowanie_maszyna.csv`: pełna tabela mapowania `MASZYNA` do uzupełnienia w Excelu.
- `docs/projekt/plan_naprawczy_modeli_produkcyjnych_2026-03-18_modele_master.csv`: podzbiór wierszy wymagających decyzji modelowych lub uzupełnienia pól.
- `docs/projekt/plan_naprawczy_modeli_produkcyjnych_2026-03-18_magazyn_manual.csv`: dwa wyjątki magazynowe do ręcznej identyfikacji.

## Uwaga operacyjna

- W produkcji problem praktyczny siedzi głównie w `MASZYNA`, nie w `MAGAZYN`.
- `MAGAZYN` ma tylko 2 seryjne rekordy bez `ID_MODEL`; oba nie mają uzupełnionych `MARKA/MODEL`.
- Większość pracy można wykonać hurtowo po zatwierdzeniu mapowania sygnatur `MARKA+MODEL -> ID_MODEL`.
