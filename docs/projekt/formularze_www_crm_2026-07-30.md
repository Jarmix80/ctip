# Formularze WWW i powiadomienia CRM

## Cel etapu

Etap zastępuje aktywne formularze Bitrix24 i Contact Form 7 na lokalnej kopii
PageKP formularzami przekazującymi sprawy bezpośrednio do CTIP CRM. Całość
działa obecnie wyłącznie w środowisku testowym:

- PostgreSQL: `ctip_test`;
- lokalna brama LAB;
- izolowany interfejs CRM: `http://localhost:8001/crm`;
- `CRM_LAB_MODE=true`;
- `FB_ALLOW_WRITES=false`;
- `SMS_TEST_MODE=true`;
- brak rzeczywistej wysyłki SMS i e-mail.

Nie wykonano wdrożenia produkcyjnego ani połączenia z produkcyjnym Firebird lub
centralą Slican.

## Kolejki i kategorie

CRM ma cztery kolejki operacyjne:

| Wejście formularza | Kolejka CRM | Kategoria |
|---|---|---|
| produkt, wynajem, konfigurator | `sales` | `product`, `sales` albo `configurator` |
| bezpłatna konsultacja | `sales` | `sales` |
| serwis urządzenia, pomoc IT | `service_it` | `service` |
| umowa | `contracts` | `contracts` |
| odczyty liczników | `contracts` | `meters` |
| rejestracja aplikacji Ksero Partner | `other` | `app_registration` |
| pozostałe sprawy | `other` | `other` |

Księgowość nie ma osobnej kolejki ani formularza. Historyczne sprawy tej kolejki
są zachowane jako kategoria `accounting` w kolejce `other`.

## Formularze PageKP

Wtyczka `kp-ctip-forms` udostępnia funkcję `kp_ctip_render_form()` i shortcode
`[kp_ctip_form]`. Formularze zostały osadzone w lokalnych szablonach:

- formularz kontaktowy;
- modal zapytania o produkt;
- oferta produktu w `single-ofertownik.php`;
- komunikat o braku wyników konfiguratora;
- globalny panel szybkiego kontaktu dostępny z zielonego przycisku;
- widoczne pole zgody z tekstem wykorzystującym pełną szerokość formularza;
- zgłoszenie serwisowe;
- zgłoszenie pomocy IT;
- formularze bezpłatnej konsultacji w treści bazy wiedzy.

Formularze produktowe przekazują identyfikator i nazwę produktu, zamiar kontaktu,
adres źródłowy oraz parametry kampanii `utm_*`, `gclid` i `fbclid`.

Od wersji wtyczki `0.2.0` telefon jest wymagany w HTML, JavaScript i walidacji
backendu WordPress. Formularz wskazuje dokładnie brakujące pole zamiast
ogólnego komunikatu. Modal produktu jest pojedynczym, responsywnym kafelkiem z
nazwą urządzenia, przewijaniem na małych ekranach i zamykaniem klawiszem Escape.

Globalny loader przycisku Bitrix24 jest usuwany z kodu opcji ACF bez usuwania
pozostałych integracji, w tym Google Tag Managera. Osadzenia `data-b24-form` w
treści wpisów są zastępowane formularzem `Bezpłatna konsultacja`, a nieużywane
zasoby Contact Form 7 są wyłączone.

## Miejsca testowe PageKP

- `http://192.168.0.9:8074/kontakt/`;
- `http://192.168.0.9:8074/oferta/ricoh-im-9000/`;
- `http://192.168.0.9:8074/konfigurator/`;
- `http://192.168.0.9:8074/serwis-drukarek/`;
- `http://192.168.0.9:8074/obsluga-it/`;
- strona główna, lista `oferta` i kategorie produktów;
- dowolna podstrona z zielonym panelem `Kontakt / Oddzwonimy`;
- artykuły bazy wiedzy z formularzem `Bezpłatna konsultacja`.

Audyt 153 adresów mapy witryny wykonany 2026-07-30 zakończył się bez błędów
pobierania. Nie wykryto aktywnego loadera ani formularza Bitrix/CF7. Własny
panel boczny jest renderowany na 150 stronach HTML, a 47 stron ma dodatkowy
formularz kontekstowy.

## Połączenie serwer-serwer

WordPress wysyła dane do `POST /v1/form-cases`. Endpoint wymaga:

- nagłówka `Authorization: Bearer <CRM_WWW_TOKEN>`;
- stabilnego nagłówka `Idempotency-Key`;
- kanału `form` albo `configurator`;
- ruchu z sieci dopuszczonej przez `PANEL_ALLOWED_NETWORKS`.

Token jest odrębny od tokenów CHAT_KP i voice. Pozostaje po stronie serwera
WordPress i nie trafia do kodu JavaScript. Wtyczka dodatkowo stosuje nonce
WordPressa, pole-pułapkę, limit żądań, walidację zgody oraz walidację danych
kontaktowych.

## Powiadomienia użytkowników

Każdy aktywny użytkownik ma cztery niezależne ustawienia:

- Handel — SMS;
- Handel — e-mail;
- Pozostałe: Serwis, Umowy i liczniki, Inne — SMS;
- Pozostałe: Serwis, Umowy i liczniki, Inne — e-mail.

Treść SMS i e-mail zawiera numer sprawy oraz link
`/crm?case=<numer_sprawy>`. W LAB zamiast wysyłki powstaje zdarzenie
`notification` na osi sprawy. Zdarzenie zapisuje odbiorców, wybrane kanały,
wynik symulacji i bezpośredni link.

## Wynik testu E2E

Test wykonany 2026-07-30 przeszedł przez pełny łańcuch:

1. formularz kontaktowy WordPress;
2. AJAX WordPress i walidację nonce;
3. serwerowy proxy wtyczki;
4. autoryzowany endpoint CTIP;
5. zapis sprawy w `ctip_test`;
6. routing do `service_it` z kategorią `service`;
7. zapis zdarzenia symulacji SMS/e-mail dla wskazanego użytkownika.

Liczba rekordów `ctip.sms_out` przed i po teście była identyczna. Potwierdza to,
że środowisko LAB nie zleciło rzeczywistej wiadomości SMS.

## Warunki przed produkcją

Przed wdrożeniem produkcyjnym trzeba osobno:

- ustawić docelowy adres HTTPS endpointu;
- wygenerować nowy sekret produkcyjny;
- ograniczyć `PANEL_ALLOWED_NETWORKS` do rzeczywistego źródła;
- wskazać użytkowników i kanały powiadomień;
- sprawdzić konfigurację SMTP oraz bramki SMS;
- wykonać test akceptacyjny każdego typu formularza;
- wdrożyć migrację przez standardowy proces GitHub i serwer.
