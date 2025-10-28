# Panel administratora CTIP – architektura modułu

## Założenia ogólne
- Panel ma rozszerzyć istniejący prototyp UI (`prototype/index.html`) o sekcje administracyjne działające w tym samym stylu.
- Autoryzacja będzie oparta o lokalną bazę użytkowników z rolami (na potrzeby panelu wystarczy rozróżnienie `admin` / `operator`). Brak uwierzytelniania dwuskładnikowego.
- Konfiguracja środowiskowa (PostgreSQL, CTIP, SerwerSMS) musi być dostępna do podglądu i modyfikacji z poziomu panelu. Zmiany wchodzą w życie po walidacji i zapisie w magazynie ustawień.
- Wszystkie operacje administracyjne wymagają audytu (kto, kiedy, co zmienił) oraz logowania w formacie kompatybilnym z `docs/LOG`.
- Backend FastAPI pozostaje źródłem prawdy: panel korzysta z nowych tras pod `/admin/**`. WebSockety zapewnią podgląd CTI w czasie rzeczywistym.
- Rola `operator` ma dostęp do ograniczonych widoków (Dashboard, CTIP Live, Książka adresowa) i może edytować kontakty, natomiast operacje konfiguracyjne są zarezerwowane dla `admin`.

## Struktura backendu
1. **Modele i migracje**
   - `ctip.admin_user` – użytkownicy panelu (imię, nazwisko, e-mail, numer wewnętrzny, rola, skrót hasła Argon2).
   - `ctip.admin_session` – sesje/jednorazowe tokeny (identyfikator, użytkownik, ważność, adres IP klienta).
   - `ctip.admin_audit_log` – dziennik działań (znacznik czasu, użytkownik, typ akcji, szczegóły JSON).
   - `ctip.admin_settings` – klucze konfiguracyjne (obsługiwane przestrzenie nazw: `database.*`, `ctip.*`, `sms.*`). Wartości poufne przechowujemy zaszyfrowane (`fernet` z kluczem odczytywanym z `.env`).
   - `ctip.admin_backup` – metadane kopii zapasowych (plik, rozmiar, status, suma kontrolna).
   - `ctip.contact` – kartoteka kontaktów (numery telefonów, dane kontrahenta) rozszerzona o kolumnę `firebird_id` umożliwiającą powiązanie z bazą Firebird.

2. **Warstwa usług**
   - `app/services/settings_store.py` – odczyt/zapis konfiguracji z cachem w pamięci i automatyczną walidacją przez pydantic.
   - `app/services/admin_auth.py` – logowanie, wygaszenie sesji, kontrola uprawnień.
   - `app/services/backup_runner.py` – wrapper na `pg_dump`/`pg_restore`, generowanie plików w `backups/`, raport statusu.
   - `app/services/ctip_monitor.py` – pośrednik do komunikacji z istniejącym klientem CTIP (status loginu, restart, strumień zdarzeń).
   - `app/services/admin_contacts.py` – logika książki adresowej (walidacja i CRUD kontaktów, obsługa `firebird_id`).

3. **Trasy FastAPI (prefiks `/admin`)**
   - `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`.
   - `GET/PUT /config/database`, `/config/ctip`, `/config/sms` – zwracają aktualne ustawienia, przy zapisie wykonują test połączenia i zapisują do `admin_settings`.
   - `GET/PUT /config/email` – konfiguracja serwera SMTP wykorzystywanego przez panel.
   - `GET /status/database`, `/status/ctip`, `/status/sms` – szybkie health-checki.
   - `POST /email/test` – test połączenia z serwerem SMTP (bez wysyłki wiadomości).
   - `POST /database/query` – kontrolowane zapytania SQL (tylko `SELECT`, limit 200 wierszy).
   - `POST /backup/run`, `POST /backup/restore`, `GET /backup/history` – sterowanie kopiami.
   - `GET /events/cti` – endpoint WebSocket do transmisji zdarzeń CTI na żywo.
   - `POST /sms/test` – wysłanie wiadomości testowej z aktualną konfiguracją.
   - `GET/POST/PUT /users` – zarządzanie użytkownikami panelu oraz przypisanie numerów wewnętrznych.
   - `GET/POST/PUT/DELETE /contacts` – zarządzanie kartoteką kontaktów, obsługa wyszukiwania i pola `firebird_id` (tylko rola `admin`).
   - `GET /reports/sms-summary` – statystyki wysyłek per użytkownik, dzień, status.

4. **Bezpieczeństwo**
   - Sesje uwierzytelniające przechowywane w HTTP-only cookie (token podpisany kluczem z `.env`).
   - Limitowanie prób logowania, możliwość ręcznego zablokowania użytkownika.
   - Audyt każdej zmiany konfiguracji lub operacji na backupie.

## Warstwa frontendowa
- Dodajemy nową sekcję „Administracja” w ramach istniejącego layoutu (rozszerzone menu boczne).
- Widoki modułowe:
  1. Panel ogólny – skróty stanu (DB, CTIP, SMS, ostatnia kopia, liczba aktywnych sesji).
  2. Konfiguracja – formularze z walidacją po stronie klienta oraz podgląd historii zmian.
  3. Kopie zapasowe – lista kopii, przyciski uruchom/odtwórz, log przebiegu.
  4. CTI Live – strumień zdarzeń z możliwością filtrowania po numerze/nr wewnętrznym.
  5. Konfiguracja e-mail – formularz host/port/login, test połączenia, status na dashboardzie.
  6. Konsola SQL/Raporty – sandbox z zapisanymi zapytaniami, eksport CSV.
  7. Użytkownicy – zarządzanie kontami, reset hasła, raport liczby wysłanych SMS.
  8. Książka adresowa – rejestr numerów klientów z mapowaniem na Firebird ID, wyszukiwarka i modalne edytory kontaktów; operatorzy mogą tworzyć i aktualizować wpisy, natomiast usuwanie pozostaje wyłącznie dla administratorów.
  9. CTI Live posiada dodatkowo panel szybkiej edycji kontaktu (wyświetlany obok logu zdarzeń) pozwalający na korektę danych wskazanego numeru bez opuszczania strumienia.
- UI korzysta z komponentów abstrakcyjnych (karty, listy, formularze) zgodnych z kolorystyką i typografią prototypu.

## Integracje i zależności
- **PostgreSQL** – po zmianie konfiguracji tworzony jest nowy `AsyncEngine`; obie konfiguracje (stara i nowa) działają do czasu zakończenia aktywnych transakcji.
- **CTIP** – klient `collector_full.CTIPClient` otrzyma wstrzyknięcie konfiguracji i webhook do aktualizacji statusu dla panelu.
- **SerwerSMS** – ustawienia zsynchronizowane z `app/core/config.py`; zmiana trybu demo natychmiast aktualizuje `HttpSmsProvider`.
- **SMTP** – konfiguracja (host/port/SSL/STARTTLS) jest przechowywana w `admin_setting` i testowana na żądanie z poziomu panelu.
- **Firebird** – kolumna `ctip.contact.firebird_id` przechowuje identyfikatory rekordów z zewnętrznej bazy Firebird; panel umożliwia ich edycję i raportowanie, ale połączenie z Firebird pozostaje w gestii usług integracyjnych.
- **Scheduler** – planowane zadania (cykliczny backup, test połączenia) obsłużymy przez `asyncio` + `APScheduler` w kolejnym etapie.

## Etapy wdrożenia
1. **Fundamenty** – modele `admin_*`, migracje, serwis ustawień, autoryzacja, podstawowe trasy `/admin/config`.
2. **Diagnostyka** – health-checki, test połączenia DB/SMS, API konsoli SQL (tylko odczyt), podstawowy widok „Status”.
3. **Kopie zapasowe** – implementacja `backup_runner`, kolejka zadań, interfejs do tworzenia/odtwarzania.
4. **CTI Live** – WebSocket, publikacja zdarzeń, komponent UI.
5. **Raporty i użytkownicy** – moduł raportów, zarządzanie kontami, integracja z historią SMS.
6. **E-mail/automation** – integracja wysyłki e-mail, powiadomienia, harmonogramy (w toku).
7. **Dopracowanie frontu** – refaktoring layoutu, dokumentacja użytkowa, testy end-to-end.

Każdy etap kończymy aktualizacją dokumentacji (`README.md`, pliki w `docs/`), testami jednostkowymi oraz wpisem w archiwum sesji.
