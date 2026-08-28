# Rejestr kontrolny pilota Shipping

## Zakres

Rejestr zapisuje stan produkcyjnego Menadżera Serwisu przed zaakceptowaniem zleceń w Shipping. Dane odczytano 28 sierpnia 2026 roku. Na tym etapie nie istniały rekordy `shipping_case` ani `shipping_shipment`, a realizacja Firebird i DPD pozostawały wyłączone.

## Dane klienta umownego

- klient `Test Umowa`: `KLIENT.ID_KLIENT_TABLE=2681`, `KLIENT.ID_KLIENT=2954`;
- umowa `test01`: `UMOWACPC.ID_UMOWACPC_TABLE=2080`, `UMOWACPC.ID_UMOWACPC=2079`;
- urządzenie: `MASZYNA.ID_MASZYNA_TABLE=5301`, `MASZYNA.ID_MASZYNA=7222`, `MODEL.ID_MODEL=458`, model `Ricoh IM 350`.

## Zlecenia pilota

| Numer | `ID_ZLECENIE_TABLE` | Rodzaj | Klient | Urządzenie | Model | Oczekiwany dokument |
|---|---:|---|---:|---:|---|---|
| `18493/2026` | `83493` | `Umowa` | `2954` | `7222` | `458` – Ricoh IM 350 | RW, opcja FV wyłączona |
| `18494/2026` | `83494` | `Płatne` | `739` | `7221` | `539` – Ricoh IM C3000 | WZ, opcję FV należy odznaczyć |
| `18495/2026` | `83495` | `Płatne` | `739` | `7229` | `380` – Ricoh MP C2011 | FV z powiązanym WZ, opcja FV włączona |

Wszystkie trzy zlecenia miały `TYP_US=8`, status `O`, pustego technika, brak dokumentów końcowych i brak numeru przesyłki. Zlecenia `18494/2026` i `18495/2026` należą do tego samego klienta i mogą zostać objęte próbą jednej wspólnej paczki po potwierdzeniu identycznego adresu.

## Przypisane części i stan początkowy

| Zlecenie | `MAGAZYN.ID_MAGAZYN_TABLE` | Indeks | Część | `ILOSC` | `IL_REZ` | Dostępne | Cena zakupu netto | Cena sprzedaży netto |
|---|---:|---|---|---:|---:|---:|---:|---:|
| `18493/2026` | `13250` | `419082O` | Toner Ricoh IM350 14k org | `9` | `0` | `9` | `100,00` | `249,00` |
| `18494/2026` | `15533` | `imc3000blk zam chemical` | Toner Ricoh IMC3000/3500 blk | `9` | `0` | `9` | `115,00` | `250,00` |
| `18495/2026` | `10280` | `2021400211764` | Toner Ricoh MPC 2003/2011/2503/2004/2504 magenta chemical | `10` | `0` | `10` | `120,00` | `490,00` |

Każda pozycja ma być dodana w ilości `1`. Po pilocie i ręcznym usunięciu dokumentów stany `ILOSC`, `IL_REZ` i stan dostępny muszą wrócić dokładnie do wartości z tabeli.

## Kontrola techniczna

W zleceniach `ZLECENIE.ID_FIRMA=1`, natomiast odpowiadające rekordy `KLIENT` i `MASZYNA` mają `ID_FIRMA=0`. Produkcyjna baza nie zawiera powtórzonych wartości `KLIENT.ID_KLIENT`, `MASZYNA.ID_MASZYNA` ani par `MASZYNA.(ID_KLIENT, ID_MASZYNA)`. Hotfix Shipping łączy te rekordy po globalnych identyfikatorach klienta i urządzenia, bez błędnego wymagania zgodności `ID_FIRMA`.

## Sprzątanie

Po pilocie Marcin ręcznie anuluje etykiety DPD i usuwa FV, oba WZ, RW oraz trzy zlecenia. Następnie usuwa przygotowane dane `Test Umowa`, `test01` i urządzenie `7222`. Codex wykonuje końcową kontrolę odczytową dokumentów, powiązań oraz dokładnego przywrócenia trzech stanów magazynowych.
