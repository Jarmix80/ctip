## Hotfix produkcyjny: mailbox `APPROVED_ORDER` uruchamia automat wiązania urządzeń

### Cel
Naprawa luki, w której wiadomość GRENKE typu `Zgoda na realizację zamówienia...` ustawia status `APPROVED_ORDER`, ale nie uruchamia automatu wiązania urządzeń z klientem Menadżera Serwisu.

### Zakres zmian
- plik: `scripts/contracts_mailbox_sync.py`
- brak migracji Alembic,
- brak zmian `.env`,
- brak zmian w usługach Windows poza opcjonalnym restartem `CTIP-Web`.

### Warunki wstępne
- istnieje aktualny backup produkcji,
- wdrożenie wykonuje się w katalogu `D:\CTIP`,
- operator ma możliwość uruchomienia panelu `/genform` albo `/flow`,
- każda operacja zmieniająca dane produkcyjne jest potwierdzona przed wykonaniem.

### Pliki do wdrożenia
- `scripts/contracts_mailbox_sync.py`

### Procedura wdrożenia
1. Zatrzymaj się na bramce potwierdzenia przed zmianą pliku produkcyjnego.
2. Wykonaj kopię pliku:
   - `Copy-Item D:\CTIP\scripts\contracts_mailbox_sync.py D:\backup_temp\contracts_mailbox_sync.py.bak_<timestamp>`
3. Skopiuj nową wersję pliku do:
   - `D:\CTIP\scripts\contracts_mailbox_sync.py`
4. Wariant minimalny:
   - nie restartuj usług,
   - kolejny ręczny trigger mailboxa albo kolejny przebieg schedulera użyje już nowej wersji skryptu.
5. Wariant deterministyczny:
   - zrestartuj `CTIP-Web`,
   - użyj tego wariantu, jeżeli okno serwisowe jest dostępne i chcesz mieć jednoznaczny punkt przełączenia.

### Weryfikacja po wdrożeniu
1. Uruchom ręczny trigger mailboxa w trybie operacyjnym albo poczekaj na kolejny przebieg schedulera.
2. Sprawdź audyt `contracts_mailbox_sync`.
3. Dla nowej zgody GRENKE potwierdź, że payload audytu zawiera:
   - `binding_items`,
   - `binding_alert` (`null` przy poprawnym wiązaniu albo szczegóły alertu przy błędzie).
4. W modalu workflow sprawdź, że status „Menadżer Serwisu” nie pokazuje już komunikatu:
   - `Brak uruchomionego automatu wiązania urządzeń.`

### Jednorazowa naprawa formularza `39`
To jest operacja zmieniająca dane i wymaga osobnego potwierdzenia wykonania.

Najbezpieczniejszy wariant operatorski:
1. Otwórz formularz `39` w `/genform` albo `/flow`.
2. Otwórz modal workflow.
3. Pozostaw status `Zgoda na realizację zamówienia`.
4. Kliknij `Zapisz status`.

Skutek:
- endpoint ręcznej zmiany statusu uruchomi automat wiązania urządzeń,
- snapshoty urządzeń dostaną `ms_binding_status`, `ms_binding_message`, `ms_binding_updated_at`,
- audit zapisze zdarzenie `contracts_flow_status_save`.

### Weryfikacja formularza `39` po naprawie
1. W UI status „Menadżer Serwisu” powinien przejść z komunikatu braku automatu na wynik wiązania.
2. W audycie formularza powinien pojawić się wpis `contracts_flow_status_save`.
3. W rekordach urządzeń workflow sprawdź:
   - `ms_binding_status`,
   - `ms_binding_message`,
   - `ms_id_maszyna`,
   - `ms_id_klient`.

### Rollback
Jeżeli hotfix trzeba wycofać:
1. Przywróć kopię:
   - `Copy-Item D:\backup_temp\contracts_mailbox_sync.py.bak_<timestamp> D:\CTIP\scripts\contracts_mailbox_sync.py -Force`
2. Jeżeli był wykonany restart `CTIP-Web`, zrestartuj usługę ponownie po rollbacku.
3. Formularz `39` nie cofnie automatycznie jednorazowej naprawy statusu; rollback dotyczy tylko kodu skryptu.
