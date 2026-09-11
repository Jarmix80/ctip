# Pozyskiwanie danych urządzeń

## Zakres

Moduł pozyskuje historię do tabel `ctip.telemetry_*`. Nie blokuje zamówień, nie
wylicza dostępnych zapasów, nie wystawia dokumentów i nie zapisuje do MS,
V-Maintenance ani PrintRadar. Nie uruchamia harmonogramów aplikacji WWW.
Migracja addytywna: `a6d9e1f3b520`, poprzednik `f2b7c9d4e6a1`.

## Źródła i oryginały

| Źródło | Dane | Oryginał |
| --- | --- | --- |
| Reporting | Pełne kolumny, liczniki, poziomy, wymiany, pokrycie | Skompresowany CSV w bazie, plik przenoszony do `archiwum` |
| Toner / All Supplies | Zdarzenia materiałowe i towarzyszące pomiary | Jak Reporting |
| DPLAC | Historia liczników | Plik pozostaje bez zmian, baza przechowuje wszystkie pola, ścieżkę i SHA-256 |
| DPLAC Not obtained | Brak pomiaru w okresie oraz ostatni znany pomiar | Jak DPLAC; stary licznik nie jest nowym pomiarem |
| IMAP Remote | Wiadomości, bezpiecznie odczytany XML, liczniki, poziomy oraz tekst historii i firmware | Skompresowane EML w bazie, skrzynka bez zmian |
| V-Maintenance | MASZYNY, MASZYNY_STATS, WEZWANIE, MAGAZYNY, DODAJ, CPC | Wersjonowane dokumenty JSON, identyfikatory tabel i wierszy |
| PrintRadar | Urządzenia, liczniki, migawki serwisowe, materiały, jakość źródła | Przetworzone dane JSON, identyfikatory próbek; pełne tablice SNMP/HTML pozostają w PrintRadar |
| MS | Powiązanie SERIAL z ID_MASZYNA i ID_KLIENT | Wersjonowane powiązania, bez kopiowania dokumentów magazynowych |

Źródła nie są traktowane jako dowody faktycznej wysyłki tonera. Pola `ZAPAS_*`
oraz zapotrzebowanie ze zgłoszeń V-Maintenance pozostają danymi źródłowymi.
Historie wewnątrz wiadomości są zachowane w oryginale i sekcjach tekstowych/XML;
nie stają się automatycznie bieżącymi incydentami.

## Konfiguracja

Worker korzysta z wybranego `CTIP_ENV_FILE`: lokalnie `.env.test`, jawnie na
produkcji `.env`. Dodatkowe ustawienia czyta odpowiednio z `.env.test.telemetry`
albo `.env.telemetry`. Pliki te są wykluczone z Git. Domyślnie moduł i wszystkie
połączenia źródłowe pozostają wyłączone.

| Ustawienie | Znaczenie |
| --- | --- |
| TELEMETRY_ENABLED | Główne włączenie workera |
| TELEMETRY_REPORT_ROOT | Katalog zawierający podkatalogi Toner, All Supplies, Reporting |
| TELEMETRY_DPLAC_ROOT | Katalog skanowany bezpośrednio wzorcem DPLAC*.csv; nigdy nie jest archiwizowany |
| TELEMETRY_CSV_TIMEZONE | Domyślnie Europe/Warsaw |
| TELEMETRY_MAIL_TIMEZONE | Domyślnie puste: nie zgadujemy strefy czasu treści wiadomości |
| TELEMETRY_MAIL_ENABLED | Włączenie poczty; używa REMOTE_EMAIL_ADDRESS, REMOTE_EMAIL_PASSWORD, REMOTE_IMAP_HOST, REMOTE_IMAP_PORT |
| TELEMETRY_IMAP_FOLDER | Domyślnie INBOX |
| TELEMETRY_VM_ENABLED / TELEMETRY_MS_ENABLED | Włączenie odczytu Firebird |
| TELEMETRY_VM_HOST / TELEMETRY_VM_DATABASE / TELEMETRY_VM_CHARSET | Osobny adres, baza i kodowanie V-Maintenance |
| TELEMETRY_VM_USER / TELEMETRY_VM_PASSWORD | Dedykowane konto odczytowe V-Maintenance |
| TELEMETRY_MS_USER / TELEMETRY_MS_PASSWORD | Dedykowane konto odczytowe MS, bez zmiany konta używanego przez Shipping |
| TELEMETRY_PRINTRADAR_DSN | Połączenie do PrintRadar kontem z SELECT do czterech dopuszczonych tabel |
| TELEMETRY_PAGE_SIZE / TELEMETRY_MAX_PAGES | Domyślnie 500 rekordów i 4 porcje na tabelę w przebiegu przyrostowym |
| TELEMETRY_MAX_FILE_BYTES | Domyślnie 32 MiB |
| TELEMETRY_STABILITY_SECONDS | Co najmniej 60 sekund pomiędzy kontrolami stabilności CSV |

Testowy worker dopuszcza wyłącznie `ctip_test`, tryb testowy SMS i adresy lokalne
lub sieć izolowanego stosu `172.28.252.0/24`. Nie korzysta z produkcyjnego pliku
`.env.telemetry`. Zmiana podsieci stosu wymaga aktualizacji kontroli izolacji.
Połączenia Firebird jawnie używają transakcji READ ONLY, PostgreSQL PrintRadar
ustawia `default_transaction_read_only=on` i ogranicza czas zapytania.

V-Maintenance wymaga osobnego połączenia `UTF8`: baza ma domyślne `WIN1250`, lecz
część kolumn `NONE` zawiera tekst UTF-8. Dziedziczenie kodowania MS powodowało błąd
dekodowania i wtórny błąd protokołu podczas zamykania połączenia. Nie zastępujemy
nieznanych znaków ani nie zmieniamy bazy źródłowej; kodowanie MS pozostaje bez zmian.
Niektóre pola V zawierają także znak NUL, którego PostgreSQL JSONB nie przyjmuje.
Taki tekst jest zachowany jako obiekt `{"__telemetry_encoding__": "utf-8/base64",
"value": "..."}` z bezstratnie zakodowaną wartością, bez obcinania treści. Rekord
otrzymuje ostrzeżenie `source_text_encoded` z nazwą pola.

## Uruchamianie

```bash
source .venv/bin/activate
export CTIP_ENV_FILE="$PWD/.env.test"
python -m app.telemetry_worker --once --dry-run
python -m app.telemetry_worker --once
python -m app.telemetry_worker --once --backfill
```

Tryb `--dry-run` nie używa bazy docelowej, nie zapisuje logów do plików, kursorów
ani archiwów. Bez `--backfill` kontroluje ograniczoną liczbę porcji baz i poczty.
`--backfill` wykonuje pełny przegląd dostępnej historii, korzystając z trwałego
kursora niedokończonego przeglądu baz. Poczta jest ponownie przeglądana od początku;
oryginały oraz dane są deduplikowane. Zmiana wersji parsera pozwala utworzyć nowe
interpretacje zachowanych danych podczas ponownego przeglądu.

Jedna blokada sesyjna PostgreSQL obejmuje cały przebieg. Kursor każdej porcji jest
zatwierdzany z jej danymi. Codzienny przegląd starszych rekordów wykrywa korekty
nieposiadające daty modyfikacji. Małe, zmienne kartoteki są sprawdzane w każdym
przebiegu. Niepowodzenia pobrania pojedynczych wiadomości mają osobną kolejkę
ponowień; błędne XML pozostają w zachowanej wiadomości z ostrzeżeniem.

## Integralność i archiwizacja

Unikalne SHA-256 rozpoznają oryginały, a klucz źródłowy, skrót dokumentu i wersja
parsera rozpoznają wersje rekordów. Osobne powiązania zachowują każde wystąpienie
w plikach i wiadomościach. Wspólny klucz semantyczny grupuje potwierdzone zdarzenia
Toner/All Supplies, pary XML/tekst i wspólne pomiary DPLAC/Reporting. Nie oznacza
arbitralnego wyboru wartości przy konflikcie. Jednakowy licznik w innej dacie
pozostaje odrębnym pomiarem.

Plik CSV musi być stabilny przed odczytem i niezmieniony po odczycie. W razie
błędnej struktury cały plik pozostaje na miejscu; inne pliki są nadal obsługiwane.
Archiwizacja następuje po zatwierdzeniu bazy. Stan `pending` jest ponawiany także
po restarcie. Przeniesienie używa dowiązania twardego w tym samym woluminie oraz
usunięcia nazwy źródłowej dopiero po kontroli zawartości. Kolizja nazwy otrzymuje
unikalny przyrostek. Nie nadpisujemy obcych archiwów. Brak obsługi dowiązań lub
brak praw pozostawia plik i zgłasza błąd. DPLAC są dodatkowo chronione przed
wywołaniem funkcji archiwizacji.

Oryginały, rekordy i ostrzeżenia nie mają automatycznej retencji w pierwszym etapie.
Wymagany jest monitoring rozmiaru bazy i objęcie nowych tabel dotychczasowym
backupem PostgreSQL. Odnośniki do diagnostyki PrintRadar zależą od retencji źródła.

## Jakość i podgląd

Podgląd administratora: `/admin/telemetry`; API tylko do odczytu:
`GET /admin/telemetry/status`. Obejmuje źródła, świeżość pomiarów, liczbę wersji,
grupy semantyczne, kolejkę archiwizacji i ostatnie 50 importów oraz ostrzeżeń.
Oryginalne wiadomości i poświadczenia nie są zwracane przez to API.

- `counter_decrease`: spadek porównywalnego licznika narastającego, także wykryty po dołożeniu starszej historii.
- `conflicting_value`: różne wartości w tej samej grupie semantycznej.
- `invalid_range`: licznik ujemny lub procent poza zakresem 0–100.
- `missing_serial`: brak numeru seryjnego; rekord nadal zachowany.
- `time_*`: brak, błąd, nieustalona strefa lub niejednoznaczny czas.
- `source_error`: błąd pomiaru zgłoszony przez PrintRadar.
- `mail_parse`: nieudane odczytanie wiadomości; oryginał zachowany.

Nie porównujemy liczników wyłącznie po modelu, nie wymuszamy równości całkowitego
licznika z sumą kolorów i nie traktujemy resetu licznika aktualnego tonera jako
resetu urządzenia. Wartości tekstowe materiałów nie są zamieniane na zero.
Pomiary o dokładności tylko do dnia nie pozwalają rozstrzygać kolejności wewnątrz
tej doby. Dopasowanie do MS wymaga jednoznacznego SERIAL; potwierdzone historyczne
powiązania nie są przepisywane przy zmianie klienta.

Logi: `docs/LOG/telemetry_YYYY-MM-DD.log`, każdy wpis ze znacznikiem czasu.
Raportowane są kody błędów, nie pełne wyjątki sterowników zawierające poświadczenia.

## Wdrożenie Windows i rollback

1. Wykonać testy, commit i push. Przez `scripts/deploy_windows_prod.py` wdrożyć
   dokładny SHA z migracją `f2b7c9d4e6a1 -> a6d9e1f3b520`, po poprawnym dry-run i backupie.
2. Na serwerze uruchomić `scripts/setup_telemetry_windows.py` najpierw kontrolnie,
   następnie z `--apply`. Opcja `--mail-stdin` przyjmuje cztery ustawienia IMAP jako
   JSON przez stdin, bez umieszczania ich w argumentach i logach.
3. Skrypt tworzy konto `ctip_telemetry_ro`, nadaje wyłącznie SELECT do wymaganych
   tabel i zapisuje `.env.telemetry` z prywatnymi ACL dla SYSTEM, administratorów
   i konta wykonującego wdrożenie. Nie zmienia globalnego profilu SYSTEM ani
   poświadczeń istniejących aplikacji. Przy niepowodzeniu moduł pozostaje wyłączony.
4. Wykonać kontrolny odczyt przez `scripts/windows/run_telemetry.py --dry-run --once`.
5. Zarejestrować `scripts/windows/install_telemetry_task.ps1`, a dla pierwszego
   pełnego pobrania dodatkowo wywołać go z `-Backfill`. Zadanie regularne działa
   co 15 minut oraz po restarcie; jednorazowe zadanie historii korzysta z tej samej blokady.
6. Zweryfikować daty i liczniki importów, trzy podkatalogi `archiwum`, brak zmian
   DPLAC, uprawnienia kont źródłowych i ponowne uruchomienie bez przyrostu duplikatów.

Rollback polega na wyłączeniu zadań `CTIP-Telemetry` i `CTIP-Telemetry-Backfill`
oraz powrocie do poprzedniego kodu zgodnie z runbookiem wdrożenia Windows.
Nie wykonywać automatycznego downgrade usuwającego dane: migracja celowo go blokuje.
Pozyskane dane i przeniesione raporty pozostają dostępne; DPLAC nie wymagają przywracania.
