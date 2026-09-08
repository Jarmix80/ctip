# Uzgadnianie ręcznych zamknięć Shipping z MS – 2026-09-08

## Cel

Mechanizm zabezpiecza aktywne przesyłki Shipping przed pozostawieniem niespójnego stanu po ręcznej zmianie zlecenia w Menadżerze Serwisu. Kontroler nie zapisuje danych w Firebirdzie. Może automatycznie uzupełnić wyłącznie PostgreSQL CTIP, gdy ręczne zamknięcie i dokumenty są w pełni zgodne z zatwierdzonym snapshotem Shipping.

Kontrola obejmuje przesyłki w stanach `label_ready`, `handed_over` oraz konflikty utworzone przez ten mechanizm. Zamknięte wpisy Archiwum nie są ponownie kontrolowane.

## Warunki automatycznego uzgodnienia

Zamknięcie jest przejmowane tylko wtedy, gdy jednocześnie:

- numer i rok zlecenia oraz klient odpowiadają rekordowi CTIP,
- numer przesyłki w MS jest identyczny z numerem etykiety Shipping,
- `STAN='Z'`, `DATA_Z` i operator zamykający potwierdzają pełne zamknięcie,
- istnieje dokładnie właściwy zestaw dokumentów: RW, WZ albo FV z WZ,
- dokumenty są przypisane do tego samego klienta, zlecenia i magazynu,
- zestaw kartotek, ceny netto, VAT i ilości odpowiadają decyzji Shipping,
- `POBRANO` odpowiada pełnej ilości, a dla FV także `ILOSCWZ` odpowiada ilości faktury,
- nagłówek zlecenia wskazuje właściwy dokument i nie zawiera dodatkowych powiązań.

Po zgodnym odczycie CTIP kopiuje identyfikatory dokumentów, zapisuje zewnętrzną datę i operatora, przenosi przesyłkę do Archiwum oraz dodaje zdarzenie `external_closure_reconciled`. SMS i e-mail nie są wysyłane; otrzymują stan `skipped_external`.

Każda niezgodność ustawia `reconcile_required`, zapisuje czytelny komunikat i zdarzenie `external_manual_change_conflict`. CTIP nie poprawia wtedy MS. Po ręcznym przywróceniu poprawnego stanu `ZR` bez dokumentów kontroler odtwarza poprzedni etap i zapisuje `external_manual_change_restored`.

## Konfiguracja

```dotenv
SHIPPING_MS_RECONCILE_ENABLED=false
SHIPPING_MS_RECONCILE_INTERVAL_SECONDS=300
SHIPPING_MS_RECONCILE_BATCH_LIMIT=250
```

Flaga pozostaje wyłączona podczas wdrożenia kodu. Interwał ma dopuszczalny zakres od 60 do 3600 sekund, a limit partii od 1 do 1000 rekordów. Harmonogram wykonuje pierwszy cykl bezpośrednio po starcie backendu, a kolejne zgodnie z interwałem. Blokada procesu i blokada doradcza PostgreSQL zapobiegają równoległym przebiegom.

## Dry-run

Na serwerze produkcyjnym, po wczytaniu właściwego środowiska:

```powershell
$env:CTIP_ENV_FILE = "D:\CTIP\.env"
& D:\CTIP\.venv\Scripts\python.exe D:\CTIP\scripts\reconcile_shipping_ms.py
```

Raport JSON trafia do ignorowanego katalogu `runtime/repairs`. Przed włączeniem automatu należy sprawdzić każdą pozycję z działaniem `reconcile` albo `conflict`, w szczególności numery dokumentów i komunikaty walidacji.

## Włączenie produkcyjne

1. Wykonaj pełny backup PostgreSQL i Firebirda zgodnie z kanonicznym runbookiem wdrożeniowym.
2. Wdróż przypięty commit przy pozostawionej wartości `SHIPPING_MS_RECONCILE_ENABLED=false`.
3. Wykonaj dry-run i zachowaj raport.
4. Ustaw `SHIPPING_MS_RECONCILE_ENABLED=true`, pozostaw interwał `300` i uruchom ponownie `CTIP-Web`.
5. Sprawdź kafelek „Kontrola MS” oraz endpoint `GET /admin/shipping/reconciliation/status`.
6. Zweryfikuj, że zgodne ręczne zamknięcia trafiły do Archiwum, dokumenty zostały skopiowane, a powiadomienia mają stan `skipped_external`.
7. Dla pozycji `reconcile_required` uzgodnij dane ręcznie w MS; nie wymuszaj automatycznego zamknięcia.

Ręczny przycisk „Sprawdź teraz” wywołuje ten sam idempotentny proces. Alternatywny zapis przez skrypt wymaga aktywnej flagi i dokładnego potwierdzenia:

```powershell
& D:\CTIP\.venv\Scripts\python.exe D:\CTIP\scripts\reconcile_shipping_ms.py `
  --apply `
  --confirmation "UZGODNIJ SHIPPING Z MS"
```

## Rollback

Wyłączenie `SHIPPING_MS_RECONCILE_ENABLED` i restart `CTIP-Web` zatrzymują kolejne cykle. Mechanizm nie zmienia Firebirda, dlatego nie odtwarza się dokumentów ani magazynu MS. Rekordy już uzgodnione w CTIP zachowują niezmienny snapshot i zdarzenie źródłowe; ich wycofanie wymaga odtworzenia PostgreSQL z backupu wykonanego przed włączeniem automatu.
