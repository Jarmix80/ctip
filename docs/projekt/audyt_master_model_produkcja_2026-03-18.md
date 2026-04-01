# Audyt master tabeli MODEL na produkcji 2026-03-18

## Zakres

- Źródło: produkcyjna Firebird Menadżera Serwisu, tylko odczyt.
- Analiza dotyczy wyłącznie tabeli `MODEL` jako mastera do zakładania urządzenia magazynowego i urządzenia klienckiego.
- Uwzględnione rodziny producentów: Ricoh, Canon, Konica Minolta, Kyocera, HP, Epson, Brother, Xerox, Samsung, Triumph-Adler, OCE.
- Priorytet wyliczam po realnej zależności urządzeń w `MASZYNA` oraz po brakach `ID_MODEL` w tych urządzeniach.

## Najważniejsze liczby

- Sygnatury master `MODEL` w analizowanym zakresie: 422
- `gotowy_master`: 55
- `uzupelnic_master`: 296
- `wybrac_kanoniczny`: 71

## Co mogę wykonać sam

- Po Twoim wskazaniu kanonicznego `ID_MODEL` mogę przygotować i wykonać hurtowe przypięcie urządzeń do mastera.
- Po podaniu brakujących wartości mogę uzupełnić pola `GRUPA`, `RODZAJ`, `KOLOR`, `PLIK` w wybranych rekordach `MODEL`.
- Mogę też przygotować drugi etap: przepięcie `MASZYNA.ID_MODEL` oraz wyjątków w `MAGAZYN`.

## Czego potrzebuję od Ciebie

- W wierszach `wybrac_kanoniczny`: wybór jednego `ID_MODEL` w kolumnie `decyzja_kanoniczny_id_model`.
- W wierszach `uzupelnic_master`: wpisanie brakujących wartości w kolumnach `uzupelnij_grupa`, `uzupelnij_rodzaj`, `uzupelnij_kolor`, `uzupelnij_plik`.
- Jeśli `PLIK` ma wskazywać konkretny obraz, potrzebuję od Ciebie finalnej ścieżki lub nazwy pliku, jeśli nie da się jej odtworzyć z istniejących rekordów.

## Podsumowanie rodzin

| Rodzina | Wszystkie sygnatury | Gotowe | Do uzupełnienia | Duplikaty do decyzji |
|---|---:|---:|---:|---:|
| RICOH | 216 | 42 | 116 | 58 |
| CANON | 55 | 4 | 46 | 5 |
| KONICA MINOLTA | 50 | 2 | 42 | 6 |
| HP | 33 | 0 | 33 | 0 |
| KYOCERA | 29 | 5 | 23 | 1 |
| EPSON | 20 | 1 | 18 | 1 |
| TRIUMPH-ADLER | 6 | 1 | 5 | 0 |
| BROTHER | 4 | 0 | 4 | 0 |
| OCE | 4 | 0 | 4 | 0 |
| SAMSUNG | 3 | 0 | 3 | 0 |
| XEROX | 2 | 0 | 2 | 0 |

## Najważniejsze modele do pierwszej decyzji

| Priorytet | Rodzina | Model | Urządzenia bez ID_MODEL | Kandydaci | Status |
|---|---|---|---:|---|---|
| WYSOKI | RICOH | MPC 2500 | 256 | 383(3/4) | uzupelnic_master |
| WYSOKI | RICOH | MP 301 | 254 | 472(4/4) | gotowy_master |
| WYSOKI | RICOH | MPC 307 | 222 | 545(4/4),  571(4/4),  392(3/4),  602(3/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MPC 3003 | 196 | 628(4/4),  30002488(4/4),  389(3/4),  560(3/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MPC 2003 | 180 | 378(4/4),  622(4/4),  30002487(4/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MP 161 | 145 | 338(3/4) | uzupelnic_master |
| WYSOKI | RICOH | IMC 3000 | 133 | 539(4/4),  30002504(4/4),  30002508(4/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MP 171 | 121 | 339(3/4),  610(3/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MPC 306 | 120 | 489(4/4),  572(4/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MPC 3004 | 119 | 390(4/4),  543(4/4),  600(4/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MPC 3001 | 117 | 388(3/4) | uzupelnic_master |
| WYSOKI | RICOH | MPC 2011 | 114 | 380(3/4),  624(3/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MP 2000 | 113 | 340(3/4) | uzupelnic_master |
| WYSOKI | RICOH | DSM 415 | 108 | 318(4/4),  139(3/4) | wybrac_kanoniczny |
| WYSOKI | RICOH | MP 201 | 103 | 342(3/4),  612(3/4) | wybrac_kanoniczny |
| SREDNI | RICOH | IM 350 | 94 | 458(3/4),  576(3/4) | wybrac_kanoniczny |
| SREDNI | RICOH | IMC 300 | 79 | 30002496(3/4),  30002503(3/4) | wybrac_kanoniczny |
| SREDNI | RICOH | DSM 622 | 74 | 325(4/4),  334(2/4),  559(2/4) | wybrac_kanoniczny |
| SREDNI | RICOH | MP 2510 | 69 | 349(3/4) | uzupelnic_master |
| SREDNI | RICOH | MP 305+ | 67 | 374(4/4),  592(4/4),  473(3/4) | wybrac_kanoniczny |
| SREDNI | RICOH | MP 2550 | 63 | 350(3/4) | uzupelnic_master |
| SREDNI | RICOH | DSM 618D | 58 | 322(4/4) | gotowy_master |
| SREDNI | RICOH | MPC 2800 | 51 | 386(3/4),  627(3/4) | wybrac_kanoniczny |
| SREDNI | RICOH | MPC 4503 | 50 | 631(4/4),  400(3/4),  604(3/4) | wybrac_kanoniczny |
| SREDNI | RICOH | IMC 4500 | 47 | 538(4/4),  30002491(4/4) | wybrac_kanoniczny |

## Plik roboczy

- `docs/projekt/audyt_master_model_produkcja_2026-03-18.csv`: tabela do uzupełnienia w Excelu; po Twoim wypełnieniu mogę wykonać właściwe poprawki.

## Uwaga

- To jest etap 1: uporządkowanie mastera `MODEL`.
- Dopiero etap 2 to hurtowe przypinanie `MASZYNA.ID_MODEL` i wyjątków `MAGAZYN` do ustalonego mastera.
