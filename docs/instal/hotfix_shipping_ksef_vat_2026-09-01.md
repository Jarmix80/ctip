# Hotfix stawki VAT Shipping dla KSeF

## Cel

Procedura usuwa błąd tekstowego zapisu stawki VAT przez Shipping. Wartość
`23.0 %` była interpretowana przez generator XML Menadżera Serwisu jako
`P_12=230`, podczas gdy schemat KSeF FA(3) wymaga wartości słownikowej `23`.

Incydent z 1 września 2026 r. dotyczy:

- FV `5348/KPSK/2026`, `FAKTURA.ID_FAKTURA_TABLE=64590`;
- WZ `WZ / 187 / 2026`, `ZAKUPY.ID_ZAKUPY_TABLE=38830`;
- czterech pozycji `FPOZYCJA` i czterech pozycji `ZAKPOZYCJA`;
- odrzuconej próby KSeF ze statusem `450` i bez numeru KSeF.

Do czasu zakończenia procedury nie wolno finalizować w Shipping kolejnych
zleceń wymagających FV. Obsługa RW oraz WZ bez FV może działać normalnie.

## Zabezpieczenia narzędzia

Skrypt `scripts/repair_shipping_invoice_vat_rate.py`:

1. domyślnie wykonuje wyłącznie `dry-run`;
2. wymaga jawnych ID FV i WZ oraz oczekiwanej liczby pozycji;
3. sprawdza status `450` i brak nadanego numeru KSeF;
4. sprawdza `IDVAT`, kwoty VAT oraz stawki kartotek magazynowych;
5. odmawia zapisu przy danych mieszanych albo przyjętej fakturze;
6. aktualizuje wyłącznie `STAWKA_VAT` wskazanej FV i WZ;
7. nie zmienia tabel `KSEF_*`, kwot, stanów magazynowych ani zlecenia;
8. wykonuje zapis w jednej transakcji i ponownie odczytuje wszystkie pozycje
   przed `commit`;
9. zapisuje raport bez danych klienta w ignorowanym katalogu
   `runtime/repairs/`.

## Dry-run produkcyjny

Polecenie należy uruchomić na serwerze produkcyjnym w procesie z konfiguracją
NSSM usługi `CTIP-Web`. Nie wolno przekazywać hasła Firebird jako argumentu.

```powershell
D:\CTIP\.venv\Scripts\python.exe scripts\repair_shipping_invoice_vat_rate.py `
  --invoice-number 5348/KPSK/2026 `
  --expected-invoice-id 64590 `
  --expected-wz-id 38830 `
  --expected-lines 4
```

Oczekiwany status raportu to `ready`, stawka źródłowa `23.0 %`, cztery
pozycje FV, cztery pozycje WZ oraz brak numeru KSeF.

## Backup i korekta

Przed `--apply` trzeba wykonać pełny backup Firebird przez
`app.services.firebird_backup.create_firebird_backup` w procesie z konfiguracją
NSSM usługi `CTIP-Web`. Dzięki temu źródło bazy i narzędzie `gbak` są identyczne
jak w działającym runtime i nie pochodzą z potencjalnie nieaktualnego `.env`.
Backup musi przejść próbne odtworzenie i wygenerować manifest oraz SHA-256.

Dla tego incydentu utworzono i zweryfikowano:

- `D:\Backup_CTIP_MS_optima\hotfix_shipping_ksef_vat\ctip_firebird_prod_20260901_145636.fbk`;
- SHA-256 `e84cefbb44253da99c4828109d41b79920211209dbd2729067ed839fd12810c4`;
- rozmiar `383803392` bajtów;
- wynik próbnego odtworzenia `verified=true`.

Po osobnym potwierdzeniu administratora korekta używa polecenia:

```powershell
D:\CTIP\.venv\Scripts\python.exe scripts\repair_shipping_invoice_vat_rate.py `
  --invoice-number 5348/KPSK/2026 `
  --expected-invoice-id 64590 `
  --expected-wz-id 38830 `
  --expected-lines 4 `
  --apply `
  --confirmation "NAPRAW VAT 5348/KPSK/2026"
```

Po operacji raport musi mieć status `corrected`, a ponowny `dry-run` status
`already_corrected`. Odrzuconego rekordu `KSEF_FAKTURA` nie wolno usuwać.
Ponowną wysyłkę faktury do KSeF wykonuje operator w Menadżerze Serwisu.

## Trwała poprawka

Formatter Shipping usuwa końcowe zera i zapisuje wartości `23`, `23.0` oraz
`23.000` jako `23 %` we wszystkich pozycjach FV, WZ i RW. Przed utworzeniem
FV system dopuszcza wyłącznie jednoznaczne liczbowe stawki FA(3): `23`, `22`,
`8`, `7`, `5`, `4` i `3`. Stawki zerowe, specjalne oraz ułamkowe wymagają
ręcznego dokumentu do czasu wdrożenia ich pełnej klasyfikacji KSeF.

Trwały hotfix należy wdrożyć najpierw na serwer testowy. Produkcję można
przełączyć dopiero po potwierdzeniu, że FV `5348/KPSK/2026` została przyjęta
przez KSeF i otrzymała numer KSeF. Wdrożenie produkcyjne restartuje wyłącznie
usługę `CTIP-Web`.

## Rollback

Niezgodność wykryta przed `commit` powoduje automatyczny rollback transakcji.
Rollback kodu polega na przywróceniu poprzedniego commita i restarcie wyłącznie
`CTIP-Web`. Nie należy przywracać wartości `23.0 %` po skutecznej korekcie,
ponieważ jest ona semantycznie błędna dla generatora KSeF.
