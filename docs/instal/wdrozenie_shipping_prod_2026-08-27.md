# Wdrożenie produkcyjne modułu Shipping — 2026-08-27

## Cel i zakres

Procedura wdraża wyłącznie moduł `/shipping`, katalog zgodności części, integrację DPD, dokumenty RW/WZ/FV i Archiwum. Release bazuje na produkcyjnym commicie `e17ad41c2651039d8b00464ddc6dc86a5549b240` i zawiera jedną migrację `f9a0b1c2d3e4` po rewizji `d8f1a2b3c4e5`.

Wdrożenie jest etapowe. Pierwsza faza udostępnia Marcinowi kolejkę oraz katalog, ale blokuje akceptację zleceń, DPD i zapisy do Menadżera Serwisu. Dopiero po kontroli danych wykonywany jest jeden jawnie wybrany pilot produkcyjny.

Skrypt wdrożeniowy restartuje wyłącznie usługę `CTIP-Web`. Usługi `CollectorService`, `CTIP-SMS` i `CTIP-FormsPublic` muszą działać przed, podczas i po operacji.

## Zasady bezpieczeństwa

1. Wdrożenie wykonuje administrator Windows na serwerze `192.168.0.8` w katalogu `D:\CTIP`.
2. Operację można przeprowadzić w dzień, ale przed cutover należy poprosić użytkowników o zakończenie bieżących operacji webowych.
3. Nie zatrzymuj `CollectorService`, `CTIP-SMS` ani `CTIP-FormsPublic`.
4. Nie wykonuj `alembic downgrade` po utworzeniu danych Shipping.
5. Nie kopiuj do produkcji lokalnych mapowań, sugestii, przesyłek ani Archiwum z `ctip_test`.
6. Nie twórz i nie aktywuj kont użytkowników automatycznie.
7. Nie wybieraj samodzielnie zlecenia pilotażowego. Marcin wskazuje rzeczywiste zlecenie tuż przed nadaniem.
8. Nie potwierdzaj akcji „Kurier odebrał paczki” przed fizycznym odbiorem paczki.

## Warunki rozpoczęcia

Wszystkie warunki muszą być spełnione jednocześnie:

- release jest opublikowany na gałęzi `release/shipping-prod-current-2026-08-27` w GitHub;
- lokalne i CI testy release zakończyły się powodzeniem;
- `git rev-parse HEAD` na serwerze wskazuje dokładnie `e17ad41c2651039d8b00464ddc6dc86a5549b240`;
- `git status --porcelain --untracked-files=no` jest pusty;
- `alembic current` wskazuje `d8f1a2b3c4e5`;
- nie istnieją produkcyjne tabele `ctip.shipping_*`;
- wszystkie cztery usługi Windows mają stan `Running`;
- `http://127.0.0.1:8000/health` i `http://127.0.0.1:8100/health` zwracają HTTP `200`;
- jest dostępne co najmniej tyle miejsca, aby wykonać pełny backup PostgreSQL i Firebird;
- użytkownicy wiedzą o krótkim restarcie wyłącznie panelu webowego.

Jeżeli dowolny warunek nie jest spełniony, należy przerwać wdrożenie i wyjaśnić rozbieżność. Nie używaj parametrów omijających kontrolę commita, brudnego repozytorium ani rewizji Alembic.

## Przygotowanie release

Na stacji roboczej, po zatwierdzeniu testów:

```bash
git -C .codex/shipping-prod-release-worktree status --short
git -C .codex/shipping-prod-release-worktree log -1 --oneline
git -C .codex/shipping-prod-current-worktree push -u origin release/shipping-prod-current-2026-08-27
git -C .codex/shipping-prod-release-worktree rev-parse HEAD
```

Ostatnie polecenie zwraca pełny, czterdziestoznakowy `ReleaseCommit`. Tę wartość należy przekazać skryptowi na produkcji. Nie używaj skróconego SHA.

## Konfiguracja `.env` — faza pierwsza

Przed dry-run uzupełnij produkcyjny `D:\CTIP\.env`. Nie zapisuj wartości dostępowych w Git ani w dokumentacji.

```dotenv
SHIPPING_ENABLED=true
SHIPPING_CATALOG_MUTATIONS_ENABLED=true
SHIPPING_FULFILLMENT_ENABLED=false
SHIPPING_WAREHOUSE_ID=1
SHIPPING_TEST_FIREBIRD_WRITES=false
SHIPPING_COMPATIBILITY_WEB_ENABLED=false

DPD_ENABLED=false
DPD_MODE=production
DPD_API_URL=
DPD_LOGIN=<wartosc_poufna>
DPD_PASSWORD=<wartosc_poufna>
DPD_MASTER_FID=<wartosc_poufna>
DPD_PAYER_FID=<wartosc_poufna>
DPD_SENDER_COMPANY=<nadawca>
DPD_SENDER_CONTACT=<kontakt>
DPD_SENDER_STREET=<ulica_i_numer>
DPD_SENDER_POSTAL_CODE=<kod>
DPD_SENDER_CITY=<miasto>
DPD_SENDER_PHONE=<telefon>
DPD_SENDER_EMAIL=<email>
```

Pozostałe produkcyjne zabezpieczenia muszą wskazywać:

```dotenv
FB_HOST=192.168.0.8
FB_ALLOW_WRITES=true
SMS_TEST_MODE=false
```

`DPD_API_URL` pozostaje puste, aby adapter wybrał oficjalny adres produkcyjny. Skrypt sprawdza obecność danych DPD bez wypisywania ich wartości.

## Dry-run na serwerze

Commit bazowy nie zawiera jeszcze dedykowanego skryptu. Uruchom PowerShell jako Administrator, pobierz release i wyodrębnij wyłącznie podpisany treścią Git skrypt do nieśledzonego katalogu `inbox`:

```powershell
cd D:\CTIP
$ReleaseCommit = "<PELNY_SHA_RELEASE>"
git fetch origin release/shipping-prod-current-2026-08-27
if ((git rev-parse "FETCH_HEAD^{commit}").Trim() -ne $ReleaseCommit) { throw "Niezgodny commit release" }
git show "$ReleaseCommit`:scripts/windows/deploy_shipping_prod_2026-08-27.ps1" |
  Set-Content -Encoding utf8 .\inbox\deploy_shipping_prod_2026-08-27.ps1
.\inbox\deploy_shipping_prod_2026-08-27.ps1 -ReleaseCommit $ReleaseCommit
```

Dry-run wykonuje wyłącznie kontrole: uprawnienia, konfigurację, stan usług, health-checki, czystość repozytorium, bazowy commit, pochodzenie release i dozwolony zakres plików. Nie wykonuje backupu, migracji, checkoutu ani restartu.

## Wdrożenie fazy pierwszej

Po poprawnym dry-run:

```powershell
.\inbox\deploy_shipping_prod_2026-08-27.ps1 `
  -ReleaseCommit $ReleaseCommit `
  -Apply `
  -GbakPath "C:\Program Files\Firebird\Firebird_2_5\bin\gbak.exe"
```

Skrypt wykonuje kolejno:

1. pełny i zweryfikowany backup PostgreSQL oraz Firebird przez `backup_prod_databases.ps1`;
2. osobny worktree kandydata w `D:\CTIP_shipping_candidate`; śledzony pusty `docs\raport\.gitkeep` jest usuwany dopiero po kontroli zawartości, a katalog zastępuje junction do produkcyjnego raportu;
3. synchronizację zależności istniejącego `.venv`;
4. kompilację kodu oraz lekkie testy ustawień i grafu migracji;
5. kontrolę `alembic current=d8f1a2b3c4e5` i `alembic heads=f9a0b1c2d3e4`;
6. addytywną migrację do `f9a0b1c2d3e4`;
7. uruchomienie kandydata na `127.0.0.1:8002` z wyłączonymi schedulerami, katalogiem, realizacją i DPD;
8. health-check kandydata, działającej produkcji i publicznych formularzy;
9. zatrzymanie wyłącznie `CTIP-Web`, checkout dokładnego SHA release i ponowne uruchomienie `CTIP-Web`;
10. końcowe health-checki i kontrolę, że pozostałe usługi nadal działają.

Jeżeli cutover nie przejdzie health-checku, skrypt przywraca kod `e17ad41c2651039d8b00464ddc6dc86a5549b240` i ponownie uruchamia wyłącznie `CTIP-Web`. Addytywne, puste tabele Shipping pozostają w PostgreSQL.

## Kontrola po wdrożeniu

```powershell
Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/ -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/admin -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/shipping/v2 -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8100/health -UseBasicParsing
git rev-parse HEAD
.\.venv\Scripts\python.exe -m alembic current
```

W przeglądarce sprawdź dodatkowo istniejące moduły `/operator`, `/genform`, `/flow` i `/device`. Nie wykonuj w nich zmian testowych; wystarczy logowanie, otwarcie list i jeden odczyt szczegółów.

Oczekiwany stan po fazie pierwszej:

- kolejka i szczegóły Shipping są dostępne po nadaniu sekcji;
- skan oraz decyzje katalogu są dostępne;
- akceptacja zlecenia, etykiety, DPD, zamknięcie zlecenia i zamknięcie dnia zwracają `423` i są nieaktywne w interfejsie;
- katalog, Archiwum i sprawy Shipping są początkowo puste;
- pozostałe moduły pracują bez zmian.

## Nadanie dostępu Marcinowi

W `/admin` otwórz aktywne konto `marcin@ksero-partner.com.pl`, zaznacz w polu „Dostępne sekcje” wyłącznie dodatkową sekcję `Shipping` i zapisz. Nie zmieniaj roli ani pozostałych sekcji.

Po ponownym logowaniu Marcin sprawdza:

1. kolejkę otwartych zleceń `TYP_US=8`;
2. brak zleceń zakończonych i z niedozwolonym technikiem;
3. treść zleceń, kontakty, adresy, lokalizacje, zaległe FV i sortowanie;
4. magazyn główny `1` oraz ceny bez wykonywania operacji wysyłkowych;
5. pierwszy skan katalogu, który ma utworzyć wyłącznie sugestie produkcyjne od zera;
6. ręczne potwierdzenie jednej oczywistej relacji model–część potrzebnej do pilota.

Nie importuj 1334 lokalnych potwierdzeń, 50 sugestii, 1228 nieaktualnych relacji ani danych testowych wysyłek.

## Aktywacja pilota

Pilot rozpoczyna się dopiero po zaakceptowaniu kontroli fazy pierwszej. W `D:\CTIP\.env` zmień wyłącznie:

```dotenv
SHIPPING_FULFILLMENT_ENABLED=true
DPD_ENABLED=true
```

Następnie:

```powershell
Restart-Service CTIP-Web
Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
```

Nie restartuj pozostałych usług. Przed nadaniem sprawdź w panelu gotowość DPD bez ujawniania loginu, hasła i FID.

## Scenariusz jednego pilota

Marcin wskazuje jedno rzeczywiste zlecenie spełniające wszystkie warunki:

- zlecenie umowne, które zakończy się dokumentem RW;
- stan `O` albo `ZR`, typ `dowóz materiałów`;
- brak technika albo dokładny technik `Wysyłka Wysyłka`;
- jedna oczywista część z dodatnim stanem magazynowym;
- kompletny i ręcznie zweryfikowany adres oraz kontakt;
- brak potrzeby wspólnego pakowania z innym zleceniem.

Kolejność pilota:

1. otwórz zlecenie i ponownie porównaj stan MS, lokalizację i treść;
2. wybierz potwierdzoną część, sprawdź ilość i cenę zakupu;
3. zaakceptuj dane;
4. utwórz jedną rzeczywistą przesyłkę DPD;
5. sprawdź numer listu, oficjalny PDF i wydruk bez potwierdzania odbioru;
6. w MS sprawdź stan `ZR`, `ZLECENIE.PRZESYLKA` i dokładnie jedną pozycję `ZPOZYCJA`;
7. dopiero po fizycznym odbiorze użyj „Kurier odebrał tę paczkę — zakończ zlecenie”;
8. sprawdź `DATA_PRZES`, wpis „Wysłana paczka”, stan `Z`, jeden dokument RW, jego numer i pozycje;
9. potwierdź jednokrotne zmniejszenie stanu magazynowego;
10. sprawdź rekord w Archiwum, operatora, część, numer RW i etykietę.

Po pilocie przez co najmniej 30 minut obserwuj log `CTIP-Web`, kolejkę błędów oraz działanie pozostałych modułów. Stan `reconcile_required`, duplikat dokumentu, niezgodny stan magazynu, błąd DPD lub błąd innego modułu zatrzymuje rozszerzanie dostępu.

## Rozszerzenie dostępu po pilocie

Po pełnym powodzeniu pilota nadaj sekcję `Shipping` aktywnym kontom:

- Agnieszka Gołembiewska;
- Joanna Gostynska.

Jeżeli konto nie istnieje albo jest nieaktywne, nie twórz go i nie aktywuj automatycznie. Zgłoś brak do osobnej decyzji administratora. Nie zmieniaj roli ani dotychczasowych sekcji tych osób.

## Wycofanie i blokada awaryjna

### Natychmiastowe zatrzymanie operacji Shipping

Przy błędzie procesu ustaw:

```dotenv
SHIPPING_FULFILLMENT_ENABLED=false
DPD_ENABLED=false
```

Następnie wykonaj wyłącznie `Restart-Service CTIP-Web`. Odczyt kolejki i danych pozostanie dostępny, ale nowe etykiety oraz dokumenty MS będą zablokowane.

### Wyłączenie całego modułu

Ustaw `SHIPPING_ENABLED=false` i zrestartuj wyłącznie `CTIP-Web`. Pozostałe moduły oraz usługi pozostają aktywne.

### Rollback kodu

```powershell
cd D:\CTIP
Stop-Service CTIP-Web -Force
git checkout --detach e17ad41c2651039d8b00464ddc6dc86a5549b240
Start-Service CTIP-Web
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic
```

Nie cofaj migracji `f9a0b1c2d3e4` po wykonaniu pilota. Stare wydanie nie odwołuje się do tabel Shipping, więc addytywny schemat może bezpiecznie pozostać. Jeżeli DPD nadał numer albo Firebird utworzył dokument, dane trzeba uzgodnić ręcznie; nie usuwaj ich i nie ponawiaj automatycznie operacji.

## Kryteria zakończenia wdrożenia

Wdrożenie uznaje się za zakończone dopiero wtedy, gdy:

- wszystkie cztery usługi działają;
- pozostałe moduły przeszły smoke-test;
- migracja wskazuje `f9a0b1c2d3e4`;
- Marcin ma jawnie nadaną sekcję Shipping;
- pilot utworzył dokładnie jedną etykietę, jedną pozycję zlecenia i jeden poprawny RW;
- stan magazynu zmienił się dokładnie raz;
- Archiwum zawiera kompletny snapshot;
- po obserwacji nie ma błędów wymagających uzgodnienia;
- dopiero wtedy przyznano dostęp dwóm wskazanym aktywnym kontom.
