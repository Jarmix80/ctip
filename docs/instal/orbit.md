# KP ORBIT — instrukcja administratora

## Rozszerzenie produkcyjne 2026-09-13

Wydanie dodaje prognozy miesięczne CPC, reguły doradcze, ocenę pojedynczych
i łączonych wysyłek oraz wiele dowodów jednego zdarzenia. Poniższy odbiór
z 12 września dokumentuje wyłącznie wcześniejszy pilotaż. Nie zastępuje
odbioru pełnej floty ani monitorowania po publikacji.

Przed rozszerzeniem `ctip_test` wykonano kopię `test-before-extension-20260913.dump`
w ignorowanym `inbox/orbit-audit`; SHA-256:
`561e199920263bcd0626072a0c83e191cb10c98c9d938781ea4b867043c8656e`.
Docelowa rewizja schematu to `d9a4f6b8c031`. Obie migracje są addytywne,
a ich automatyczny downgrade jest zablokowany.

## Odbiór testowy 2026-09-12

- Kod ORBIT przygotowano w obu worktree, zachowując testowe Delivery, CRM, LAB
  i Bot Identity. Nie wykonano commita, push ani wdrożenia produkcyjnego.
- Baza `ctip_test` ma rewizję `c8f3e5a7b920`; przed migracją wykonano
  zweryfikowaną kopię logiczną 75 757 237 bajtów w ignorowanym `inbox/orbit-audit`.
- Pilotaż obejmuje cztery urządzenia MS: 105, 4050, 4696, 5914 i 602 bieżące
  fakty. Ponowienie importu zwróciło `updated=0`. Nie uruchomiono jeszcze
  pełnego importu całej floty ani cyklicznego zadania ORBIT.
- Odzyskano starsze powiązania RW/WZ. Sześć wartości wydań jest potwierdzonych,
  ale waluta i historia zwrotów wymagają uzgodnienia; nie udajemy pełnego kosztu.
- Pełna regresja kodu wydania: 1158 zaliczonych, 15 pominiętych. W obu worktree
  odbiór obszaru zmiany: 253 zaliczone testy; pre-commit poprawny.
- Autoryzowane API na rzeczywistych danych: HTTP 200, próbka odczytów 10–23 ms;
  bez sesji HTTP 401. Chromium przeszedł osiem zakładek w obu wyglądach,
  także przy szerokości 390 px: brak błędów JS i poziomego przepełnienia.
- Podgląd: `http://192.168.0.9:18170/shipping?view=orbit`, proces bez
  harmonogramów WWW (`--lifespan off`), tylko lokalne bazy testowe.
  Główny testowy na porcie 8000 pozostał bez restartu.
- Podgląd jest procesem tymczasowym, nie nową usługą systemową. Jego log
  ze znacznikami czasu znajduje się w ignorowanym `inbox/orbit-audit/preview.log`
  worktree wydania; regularny worker zachowuje dzienną rotację opisaną niżej.

### Dostęp do podglądu z LAN

Diagnoza z 12 września 2026: proces na porcie 18170 działa i odpowiada HTTP 200
na żądania z tego samego hosta, ale UFW odrzuca połączenia z sieci lokalnej.
Potwierdzenie w dzienniku zapory: `[UFW BLOCK]`, `DST=192.168.0.9`,
`DPT=18170`. Test adresu LAN wykonany na samym serwerze nie sprawdza tej ścieżki.

Aktualizacja: użytkownik dopuścił port i potwierdził działanie podglądu z LAN.
Poniższe polecenia pozostają instrukcją historyczną, nie bieżącą blokadą wdrożenia.

Administrator serwera testowego może dopuścić wyłącznie ruch TCP z LAN:

```bash
sudo ufw allow from 192.168.0.0/24 to 192.168.0.9 port 18170 proto tcp comment 'CTIP ORBIT test'
sudo ufw status numbered
```

Następnie należy otworzyć podgląd z innego komputera w LAN i potwierdzić
żądanie w logu aplikacji. Reguły nie wykonano w ramach diagnozy: wymaga
uprawnień administratora i zmienia konfigurację hosta poza repozytorium.
Nie wyłączać całej zapory. Osobny port oznacza osobną pamięć sesji przeglądarki;
przy pierwszym wejściu może być wymagane logowanie.

Po zakończeniu tymczasowego podglądu administrator powinien usunąć wyłącznie
dodaną regułę, identyfikując ją przez `sudo ufw status numbered`.

## Przeznaczenie i granice

ORBIT udostępnia osiem zakładek w obu wyglądach Shipping. Odczyty HTTP korzystają
wyłącznie z PostgreSQL, bez Firebirda, IMAP i pobierania obrazów z adresów urządzeń.
Brak potwierdzonego zdjęcia oznacza symbol zastępczy. Narracja jest deterministyczna,
bez zewnętrznego AI. Zmiana zakresu raportu nie rozpoczyna nowego cyklu tonera.

Reguły zamawiania są wyłącznie doradcze. Ostrzeżenia nie blokują zatwierdzania
ani łączenia paczek; brak oceny po dwóch sekundach daje jawny komunikat o braku
danych i pozwala kontynuować. ORBIT nie zapisuje dokumentów, stanów magazynowych
ani wiadomości. Nie wysyła automatycznych SMS ani e-maili.
Istniejący worker telemetrii nadal odpowiada za swoje archiwizacje CSV i IMAP.

Domyślna lista obejmuje aktywne umowy; zakresy `suspended`, `scrapped`, `review`
i `all` pozwalają przeszukiwać zachowaną historię. Zakończenie umowy nie usuwa
urządzenia. Dalsza umowa kontynuuje tę samą fizyczną historię. Niejednoznaczne
numery i sprzeczne przypisania trafiają do zakresu weryfikacji.

## Konfiguracja i prawa

Ustawienia należy umieścić w prywatnym `.env.test`, a dopiero po zatwierdzeniu
produkcji w `.env`. Nie kopiować identyfikatorów złomu między środowiskami.

| Ustawienie | Domyślnie | Znaczenie |
| --- | --- | --- |
| `SHIPPING_ORBIT_ENABLED` | `false` | Dostęp HTTP i uruchomienie workera. |
| `SHIPPING_ORBIT_SCRAP_CUSTOMER_IDS` | `[]` | Zweryfikowane ID klientów złomu, lista JSON. |
| `SHIPPING_ORBIT_SCRAP_WAREHOUSE_IDS` | `[]` | Zweryfikowane ID fizycznych magazynów złomu. |
| `SHIPPING_ORBIT_STALE_DAYS` | `3` | Ostrzeżenie o wieku ostatnich dostępnych danych. |

Dla miesięcznych CPC próg wynosi co najmniej 35 dni, aby zwykłego cyklu rozliczeń
nie przedstawiać jako awarii codziennego monitoringu. Błąd importu MS nie blokuje
projekcji danych już dostępnych lokalnie; raport zachowuje jawny stan częściowy.

Administrator ma dostęp do kwot. Operator wymaga sekcji Shipping i osobnego prawa
`can_view_orbit_finance`, nadawanego w formularzu użytkownika. Domyślnie jest ono
wyłączone; dotychczasowe prawa nie są rozszerzane. Ochrona obejmuje szczegóły,
historię, dowody i narrację. API nie udostępnia surowych dokumentów ani EML.

## Koszty i jakość

Koszt materiałów pod umową to historyczna cena zakupu `ZPOZYCJA.CENA_Z`
pomnożona przez potwierdzoną ilość wydaną, po uzgodnieniu wartości zakupu pozycji
i RW/WZ. `WARTOSC_Z` jest dowodem uzgodnienia, nie drugim składnikiem sumy.
Nie stosować sprzedażowych `CENA/WARTOSC`, aktualnej ceny katalogowej, stawek
sprzedaży robocizny/dojazdu ani całej faktury wielourządzeniowej jako kosztu.

Brak ceny, techniczne zero, brak realizacji, konflikt relacji lub niejednoznaczna
korekta nie oznaczają kosztu zerowego. Wydanie i jego reprezentacja w Shipping
nie są liczone dwa razy. Obecny adapter nie zakłada semantyki niejednoznacznych
korekt MS: zachowuje je jako materiał do weryfikacji zamiast pozornie dokładnego
odjęcia. Czysty kalkulator obsługuje potwierdzoną ilość zwróconą. Sumy oznaczają
znane kwoty, nie pełny bilans. Pole marży pozostaje puste.

Lista `documented_issues` pokazuje osobno uzgodnione wartości pojedynczych wydań
przed weryfikacją zwrotów. Nie dopisuje waluty PLN, jeżeli źródło jej nie
potwierdza; nie wlicza takich kwot do ostatecznego kosztu po zwrotach. Starsze
linie RW/WZ mogą być jednoznacznie powiązane przez numer dokumentu i firmę,
zgodnie z potwierdzonym mechanizmem MS. Sprzeczne klucze nadal blokują koszt.

Miesięczne CPC zachowuje okres i klienta historycznego, bez sztucznych pomiarów
dziennych. Wartości narastające różnych źródeł nie są sumowane. Spadki i sprzeczne
odczyty przerywają wiarygodny przyrost; jawny reset rozpoczyna nową epokę.
Pomiary poziomu tonera mają pierwszeństwo przed szacunkami. Wysyłka może rozpocząć
szacunek wyłącznie przy wiarygodnym liczniku bazowym i zgodnej wydajności; nie
interpolujemy nieobecnego odczytu. Druga wysyłka może stanowić zapas i nie resetuje
cyklu. Alert materiałowy nie jest potwierdzeniem wymiany.

## Model danych i aktualizacja

`shipping_orbit_evidence` wiąże jedno zdarzenie z wieloma istniejącymi rekordami
telemetrii, bez kopiowania oryginałów. Wysyłki MS i Shipping scala wyłącznie
jednoznaczna zgodność urządzenia, zlecenia z rokiem, kierunku i numeru przesyłki.
Ilości pochodzą z unikalnych pozycji Shipping, a nie z sumowania nagłówków MS.
Koszt nadal pochodzi wyłącznie z uzgodnionego rozchodu MS.
Usunięcie faktu z pełnej migawki wycofuje bieżący wskaźnik, zachowując oryginał.
Pełny odczyt Firebird używa porcjowanego kursora, bez ponawiania kosztownego
sortowania i skanowania tabel pozycji przy każdej stronie.

`shipping_orbit_policy` przechowuje nadpisania reguł. Kolejność dziedziczenia:
globalne → klient → urządzenie; w każdym zakresie ustawienie koloru nadpisuje
ustawienie ogólne. Domyślnie: zapas **0**, niski poziom **20%**, wyprzedzenie
dostawy **7 dni**, ważność CPC **45 dni**, ważność pomiaru **3 dni**.
Puste pole przywraca dziedziczenie, a zero zapasu pozostaje jawną wartością.
Edycja wymaga administratora; każda zmiana zapisuje poprzednie i nowe wartości
w `admin_audit_log` jako `orbit_policy_update` w tej samej transakcji.

Prognoza tempa wymaga 3–6 kolejnych zakończonych miesięcy tej samej umowy.
Jest średnią ważoną liczbą dni; nie sumuje liczników narastających ani nie
dodaje ponownie A3. Konflikt, luka i reset przerywają wiarygodną podstawę.
Zużycie po okresie i kolejne 30 dni są szacunkiem, nie bieżącym pomiarem.
CPC nie zastępuje poziomu tonera. Zapas z historii dostaw jest potencjalny,
a brak potwierdzenia doręczenia pozostaje nierozstrzygniętym transportem.
Dzienne migawki zachowują osobne czasy komponentów; późny licznik nie odmładza
porannego poziomu tonera ani starego zdarzenia serwisowego.

`shipping_orbit_device` zachowuje tożsamość, stan floty i gotowy raport.
`shipping_orbit_event` przechowuje bieżące, znormalizowane fakty i identyfikator
dowodu `telemetry_record`, bez kolejnej kopii oryginału. `shipping_orbit_run`
udostępnia stan importu i projekcji. Historia źródłowa pozostaje niezmienna.
`telemetry_record_head` wskazuje ostatnią zaakceptowaną obserwację bieżącego
stanu; powrót A→B→A nie tworzy kolejnej kopii. Sam replay CSV/EML nie potwierdza
kolejności korekt. Dla dziennych migawek pierwszeństwo ma `telemetry_daily_head`.
Starsze wielowersyjne dane bez wskaźnika pozostają konfliktem, nie arbitralnym
wyborem do kalkulacji. Projekcja i telemetria współdzielą blokadę workera.

API pod `/admin/shipping/orbit`: `capabilities`, lista urządzeń, `/{device_id}`,
`/{device_id}/timeline` i `/{device_id}/evidence/{event_id}`. Identyfikator ma postać
`ms:<ID_MASZYNA>`. Filtry: `query`, `scope`, `contract_id`, `date_from`, `date_to`,
`bucket=day|week|month`, `page`, `page_size` (maksymalnie 100). Agregacja osi czasu
zlicza zdarzenia, nie sumuje liczników narastających. Dane CPC mają osobne okresy.

`GET /policies?device_id=ms:123` zwraca reguły efektywne i nadpisania.
`PUT /policies` przyjmuje `scope`, `scope_id`, `color`, `values`; tylko ta
operacja zmienia konfigurację. `POST /shipment-assessment` ocenia szkice,
a `POST /shipment-assessment/saved` przyjmuje `order_table_ids` zapisanych
zleceń. Obie oceny są odczytowe i zawsze zwracają `blocking=false`.

## Testowy i odbiór

Polecenia wykonywać z właściwego worktree, po aktywacji głównego `.venv`:

```bash
source /home/marcin/projects/ctip/.venv/bin/activate
export CTIP_ENV_FILE=/home/marcin/projects/ctip/.env.test
export PGHOST="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ctip-test-postgres-1)"
export FB_HOST="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ctip-test-firebird-1)"
export SHIPPING_ORBIT_ENABLED=true
python -m app.orbit_worker --once --import-ms --dry-run --machine-id 123
```

ID `123` jest przykładem: należy podać zweryfikowane ID urządzenia pilotażowego.
`--dry-run` nie zapisuje PostgreSQL, nie wymaga migracji tabel docelowych i nie
archiwizuje plików. Bez `--machine-id` kontrola obejmuje całą aktywną flotę.
Po kopii bezpieczeństwa i migracji `alembic upgrade head`:

```bash
python -m app.orbit_worker --once --import-ms --machine-id 123
python -m app.orbit_worker --once --import-ms
python -m app.orbit_worker --loop --project-only
```

Pełny odczyt MS obejmuje dostępną historię od pierwszej umowy aktywnej lub wcześniej
zarejestrowanej floty. Pierwszy przebieg może być długi; zdarzenia bez daty zachowują
brak czasu. Zapis następuje dopiero po zakończonym odczycie. Ponowne uruchomienie
nie dubluje danych. Pilotaż nie oznacza wykonania pełnego przebiegu dziennego.
Pętla `--project-only` odświeża projekcję i Shipping co 300 sekund, bez Firebirda.
Zaplanowany przebieg `run_telemetry.py --scheduled` uruchamia najpierw telemetrię,
a po niej pojedynczy przebieg ORBIT z pełnym MS. Przedział nocny zaczyna się
o 23:55 Europe/Warsaw. Przy włączonym ORBIT telemetria pomija własny import CPC;
historię pełną i miesięczne liczniki obsługuje jeden importer ORBIT. Wyłączenie
flagi przywraca wcześniejszy przebieg CPC bez trwałej zmiany jego konfiguracji.
Zajęta blokada pojedynczego przebiegu zwraca kod 3, aby nie udawać wykonanego
cyklu nocnego. Harmonogram musi umożliwiać ponowienie po zwolnieniu blokady.

Przed odbiorem sprawdzić ponowienie importu, A/B/A, zwroty, nieznaną cenę, różnicę
ceny zakupu i sprzedaży, fakturę wielourządzeniową, reset, lukę, zmianę umowy,
zawieszenie/powrót/złom, blokadę kwot oraz obydwa interfejsy na telefonie.
Brak ustawień złomu wymaga rozstrzygnięcia przed włączeniem monitoringu produkcji.

## Produkcja, monitoring i wycofanie

Przed produkcyjnym importem konto wskazane przez `TELEMETRY_MS_USER` musi mieć
wyłącznie SELECT do tabel: MASZYNA, MODEL, KLIENT, UMOWACPC, UMOWA, CPC, ZLECENIE,
ZPOZYCJA, ZAKUPY, ZAKPOZYCJA, FAKTURA, FPOZYCJA, SERIAL i MAGAZYN. Dotychczasowy
instalator telemetrii nie nadaje wszystkich dodatkowych praw ORBIT. Należy je
zweryfikować i uzupełnić w zatwierdzonym oknie wdrożenia, bez praw zapisu.

Po backupie `scripts/windows/provision_orbit_reader.py --apply` nadaje istniejącemu
kontu wyłącznie SELECT, bez zmiany hasła ani tworzenia użytkownika. Bez `--apply`
wykonuje samą kontrolę. Sprawdza rzeczywiste kolumny tabel; `SELECT 1 FROM tabela`
nie jest wystarczającym testem uprawnień Firebird. Weryfikacja źródła produkcyjnego
potwierdziła klienta złomu `674` i magazyn złomu `3`; identyfikatory testowe
wymagają osobnego sprawdzenia i nie są kopiowane automatycznie.

Produkcja wymaga osobnej zgody, świeżego sprawdzenia HEAD, commitów i push,
kopii PostgreSQL/Firebird oraz zatwierdzonego wdrożenia Windows. Nie wdrażać
całego testowego Delivery/CRM tylko dla ORBIT. Najpierw migracja addytywna,
następnie pilotaż, dopiero potem pełny odczyt i włączenie zakładki.

Po odbiorze `scripts/windows/install_orbit_task.ps1` rejestruje niezależne zadanie
`CTIP-ORBIT`; `scripts/windows/run_orbit.py` jawnie ładuje produkcyjne `.env`.
Instalator nie wykonuje migracji ani nie uruchamia wysyłek. Nie uruchamiać drugiej
pętli równolegle. Proces zapisuje wpisy ORBIT ze znacznikiem czasu w dziennie
rotowanych `docs/LOG/telemetry_YYYY-MM-DD.log`, także przez północ. Błędy zawierają
wyłącznie bezpieczny typ, bez parametrów połączeń. Status jest widoczny w raporcie.

Po publikacji wymagane jest **72 godziny obserwacji i dwa pełne cykle nocne**:
stan `shipping_orbit_run`, świeżość źródeł, przyrost oryginałów, powtórzenia,
wycofania, błędy logów i czas odpowiedzi Shipping. Odbiór jednorazowy nie oznacza
zakończenia tej obserwacji. Nie uruchamiać drugiego pobierania IMAP/CSV obok
istniejącego harmonogramu; archiwizacje pozostają odpowiedzialnością telemetrii.

Wycofanie: wyłączyć flagę i zatrzymać zadanie ORBIT, bez wycofywania lub usuwania
danych. Automatyczny downgrade migracji jest zablokowany. Archiwum pozostaje
dostępne dla kolejnego wdrożenia. Oryginały telemetrii i proces Shipping nie są
usuwane ani rekonfigurowane przez ORBIT.
