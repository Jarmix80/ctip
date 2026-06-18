# Stan Sesji Codex

Ten plik sluzy do szybkiego wznowienia pracy po przerwanej sesji.
Sekcja ponizej jest utrzymywana recznie, a historia snapshotow jest dopisywana przez skrypt `scripts/update_session_state.sh`.

## Biezacy Kontekst
- Biezaca galaz: `integration/prod-realign-2026-05-25`
- Biezacy commit: `41b9703c165c0ad83d5c4b61b5c8f08e4e73d2b3`
- Biezace zadanie: Zachowanie planu dalszych prac dla modulu `/device`.
- Co zostalo zmienione: Dopisano notatke "Plan na pozniej: /device" oraz zaktualizowano `.codex/session.json`.
- Co pozostalo do zrobienia: Wrocic do punktow z planu `/device`, gdy modul bedzie przygotowywany do pelnego uzycia produkcyjnego.
- Ostatni znany status testow: `pytest tests/test_device_intake.py tests/test_device_dashboard.py -q` zakonczyl sie wynikiem `10 passed`.
- Dokladny nastepny krok: Przy wznowieniu tematu `/device` zaczac od testu end-to-end na kontrolowanej kopii Firebird.

## Plan Na Pozniej: `/device`

### Stan
- `/device` jest zaimplementowany jako v1 panelu operacyjnego obslugi urzadzen.
- Panel ma dashboard PZ, audyt `MAGAZYN` / `SERIAL` / `MASZYNA` / `MODEL`, synchronizacje kartotek `AUTO/XXXX`, tworzenie dostawcow/modeli oraz przyjecie PZ batch.
- Zapis PZ batch tworzy `ZAKUPY`, `ZAKPOZYCJA`, `SERIAL`, `MASZYNA` i laczy `SERIAL.ID_MASZYNA`.
- Dostep do zapisow Firebird jest chroniony flaga `FB_ALLOW_WRITES=true`.
- Do czasu wykonania testow operacyjnych modul nie powinien byc traktowany jako w pelni zatwierdzony proces produkcyjny.

### Do Zrobienia Przed Produkcyjnym Uzyciem
- Wykonac test end-to-end na kontrolowanej kopii Firebird: PZ -> `ZAKPOZYCJA` -> `SERIAL` -> `MASZYNA`.
- Dopisac test integracyjny pelnego zapisu batch.
- Ujednolicic stale magazynu `28` / `656` z konfiguracja `FB_WAREHOUSE_ID` i `FB_WAREHOUSE_CLIENT_ID`.
- Przejrzec ryzyko pola `force`, ktore omija blokade duplikatu serial/ewidencja.
- Potwierdzic z Menadzerem Serwisu, ze tworzone pola `MASZYNA` sa kompletne operacyjnie.
- Dodac `/device` do middleware `Cache-Control: no-store`.
- Wykonac reczny test UI z realnym scenariuszem dostawcy, modelu i 2-3 pozycji.

## Historia Snapshotow


### Snapshot 2026-04-23 22:27:58 UTC
- Data/czas: `2026-04-23 22:27:58 UTC`
- Galaz: `codex/fix-public-form-checkbox-422`
- Notatka: Inicjalny snapshot po wdrozeniu mechanizmu wznowienia sesji

#### git status --short
```text
 M .codex/session.json
 M README.md
 M app/api/routes/admin_contracts.py
 M app/static/root/genform.js
 M scripts/sync_prod_forms_to_test.py
?? CLAUDE.md
?? docs/session_state.md
?? scripts/update_session_state.sh
```

#### Ostatnie 20 commitow
```text
9df3395 Usuwaj proforme Firebird przy dezaktywacji formularza
ffa7997 Domknij usuwanie proformy i popraw import formularzy
5fc74d2 Dodaj import formularzy z produkcji do ctip_test
84b080d Rozbuduj workflow genform i popraw walidacje formularza
47b79f6 Docs: aktualizacja listy zadan planowanych i zrealizowanych FLOW
d55ef30 FLOW: auto-start Firebird w testowym starcie uslug
2bc7529 FLOW: dopracowanie PDF proformy i spojnosc podgladu
1354523 FLOW: domkniecie etapu arkusza GRENKE i cache statusow
3c5d0bc fix public form checkbox parsing
7616bd8 Dodanie mapowania użytkownika MS w panelu
5a44e2f 0.2.16: odblokuj lokalny zapis firebird poza repo
d9de545 0.2.15: dodaj blokade zapisu firebird w panelu
a445ebb 0.2.14: napraw runtime firebird i edycje uzytkownikow
c9804fa 0.2.13: zautomatyzuj MS i handlowcow w formularzach
eca8a8d 0.2.12: dopracuj publiczny formularz i wydruk genform
a23ea4a 0.2.7: popraw daty dokumentu w publicznym formularzu
d179118 0.2.7: napraw SMS i edycje uzytkownika w panelu admina
db6326b 0.2.6: doprecyzuj obsluge formularza i sekcje firebird
c75296a Dodaj konfigurację i podgląd obsługi formularza
5571492 feat: dodaj publiczna aplikacje formularzy
```

### Snapshot 2026-06-18 16:13:16 CEST
- Data/czas: `2026-06-18 16:13:16 CEST`
- Galaz: `integration/prod-realign-2026-05-25`
- Notatka: Zapis planu /device do pozniejszego wdrozenia przed commitem sesji

#### git status --short
```text
 M .codex/session.json
 M docs/session_state.md
```

#### Ostatnie 20 commitow
```text
41b9703 Popraw migracje statusu zamkniecia bez realizacji
588a170 Dodaj status zamkniecia bez realizacji
20ccc6f Napraw statusy GRENKE i dodaj wynajem bez GRENKE
aec0b21 Napraw reset hasla w panelu administratora
64477f6 Dodaj runbook kolejnej poprawki produkcyjnej
0e1c91d Zaktualizuj model galezi po wyrownaniu main
71e7296 Domknij model galezi po porzadkowaniu repo
3b5e16e Ujednolic bootstrap Windows z linia produkcyjna
d925638 Przygotuj repo do dalszego rozwoju i wdrozen
12b316f Napraw automat mailboxa dla zgody GRENKE
196f671 feat(assistant): audyt urzadzen do zakladki chat
57c990b feat(genform): uruchamianie wniosku GRENKE z prefillem krok 2/3
1595ec4 Mailbox: automat retencji audit logow tylko po wlaczeniu w prod
8bb7b40 Mailbox sync: warningi bez exit 1 i krótsze logi
4564046 Napraw interpolacje Label z dwukropkiem w PowerShell
bebee2b Uodpornij backup script na blad Start-Process ArgumentList
e8c8189 Napraw kolizje z automatyczna zmienna Host w backup script
db6f1fb Napraw skladnie tablic kandydatow w skrypcie backupu Windows
715478a Dodaj skrypt backupu PostgreSQL i Firebird dla Windows
b83025a Popraw workflow wiazania urzadzen dla APPROVED_ORDER
```
