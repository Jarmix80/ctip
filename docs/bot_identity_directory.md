# Centralny katalog tożsamości botów

CTIP utrzymuje wspólny katalog relacji osoba–firma–telefon–urządzenie dla
kanałów `voice` i `chat`. Firebird jest wyłącznie źródłem read-only. Źródłem
reguł kont mobilnych jest repozytorium
`Jarmix80/bazams`, gałąź `agent/mobile-customer-accounts`, commit
`9e2d36073f943bb9b2926edbf00e55458ddc2cf9`.

Synchronizacja odczytuje wszystkich aktywnych klientów `KLIENT`, aktywne
kontakty `KONTAKT`, telefony firm oraz urządzenia `MASZYNA`. Nie kopiuje
adresów e-mail. Kontakty łączy z klientem przez `ID_KLIENT` i nigdy nie pobiera
wartości `LOCK_USER`. Pole służy wyłącznie jako warunek istnienia danych
uwierzytelniających. Konto mobilne jest dostępne, gdy ma
niepusty `NAZWA_S`, niepusty `LOCK_USER` oraz `AKTYWNY` różne od `NIE`;
`AKTYWNY = NULL` jest dopuszczalne.

Domyślny cykl synchronizacji wynosi 5 minut. Po 15 minutach katalog zgłasza
ostrzeżenie, a po 60 minutach blokuje rozpoznawanie i ujawnianie urządzeń.
Spadek liczby kont mobilnych o ponad 20% wymaga ręcznego przeglądu i nie dezaktywuje
dotychczasowego katalogu.

Połączenie używa izolacji `ISOLATION_LEVEL_READ_COMMITED_RO` sterownika
Firebird. Synchronizacja odmawia startu przy `FB_ALLOW_WRITES=true`. Lokalny
worker `python -m app.bot_identity_worker` uruchamia wyłącznie katalog
tożsamości; dla `PGDATABASE=ctip_test` dodatkowo wymaga `FB_MODE=local`,
testowej nazwy bazy i lokalnego hosta albo aliasu kontenera `firebird`.

Kanały voice i CHAT_KP powinny korzystać z izolowanej aplikacji
`app.bot_identity_api_app:app`. Udostępnia ona wyłącznie wersjonowane trasy
`/internal/v1`, kompatybilne trasy `/v1` Centrum Obsługi i techniczny
`/health`. Nie rejestruje panelu administratora, formularzy ani operatorskiego
API CRM. Proces API nie uruchamia schedulera Firebird; katalog zasila osobny
worker. Odpowiedź `/v1/capabilities` zachowuje kontrakt `1.0` wymagany przez
CHAT_KP, w tym identyfikator usługi `ctip` i jawne flagi obsługiwanych funkcji.
W środowisku izolowanym opcjonalna zmienna `BOT_IDENTITY_TEST_SMS_CODE`
ustawia sześciocyfrowy kod używany wyłącznie do testu end-to-end. Kod nie jest
zwracany przez API, a wyzwania zachowują idempotencję na podstawie nagłówka
`Idempotency-Key`.

Laboratoryjne API ma ustawione `BOT_IDENTITY_TEST_SMS_CODE=123456` razem z
`CRM_LAB_MODE=true`, `PGDATABASE=ctip_test` i `SMS_TEST_MODE=true`. Ustawienie
stałego kodu poza tym zestawem zabezpieczeń jest odrzucane. Zmienna nie może
występować w produkcyjnym `.env`; produkcyjny mechanizm generowania i wysyłki
SMS pozostaje niezależny od funkcji LAB.

## Projekcja urządzeń i zdjęć

Źródłem urządzenia jest `MASZYNA`. Kanoniczne dane modelu są dobierane kolejno
przez `MASZYNA.ID_MODEL = MODEL.ID_MODEL`, a przy braku tego powiązania przez
dokładną parę `MASZYNA.MARKA = MODEL.MARKA` oraz
`MASZYNA.MODEL = MODEL.MODEL`. Producent pochodzi z `MODEL.MARKA`, nazwa modelu
z `MODEL.MODEL`, a zdjęcie z `MODEL.PLIK`. Jeżeli nie znaleziono rekordu
`MODEL`, projekcja zachowuje producenta i model z `MASZYNA`, ale nie zgaduje
zdjęcia.

Audyt lokalnej kopii Firebird wykazał 5795 urządzeń klientowskich: 14
powiązanych przez `ID_MODEL`, 919 przez dokładną parę marka–model i 4862 bez
kanonicznego rekordu modelu. Dla 931 urządzeń znaleziono `MODEL.PLIK`.
Prawidłowe adresy z `https://ksero-partner.com.pl/imgdev/` zwracają HTTP 200
oraz `image/jpeg` albo `image/png`. Dwa wpisy zawierające niezakodowane spacje
i jeden sklejony podwójny URL są odrzucane przez projekcję.

Do katalogu trafiają wyłącznie adresy HTTPS z kontrolowanych hostów. HTTP jest
dopuszczalne tylko w `ctip_test` przy `CRM_LAB_MODE=true` i
`SMS_TEST_MODE=true`, dla jawnie skonfigurowanych hostów laboratoryjnych.
Odrzucane są ścieżki lokalne, UNC/SMB, `file://`, dane logowania w URL-u,
parametry zapytania, fragmenty, path traversal, hosty spoza listy oraz wartości
niebędące jednoznacznym adresem obrazu. Brak lub odrzucenie zdjęcia ustawia
`image_url=null` i nie blokuje urządzenia.

Opcjonalny `GET /v1/device-model-images/{image_ref}` obsługuje przyszłe obrazy
utrzymywane w kontrolowanym katalogu CTIP. Referencja ma 64 znaki
szesnastkowe, nazwa źródłowa i ścieżka nie są ujawniane, a endpoint dopuszcza
wyłącznie JPEG, PNG, GIF i WebP. Pliki są sprawdzane pod kątem symlinków,
wyjścia poza katalog, nagłówka obrazu i limitu rozmiaru. Referencja obrazu musi
zostać zmieniona po zmianie zawartości pliku.

`POST /v1/customers/resolve` zwraca pole `company_name` o długości do 300
znaków wyłącznie dla wyniku `exact` albo `unique`. Dla `ambiguous` oraz
`not_found` zarówno `company_name`, jak i `customer_ref` mają wartość `null`.
Odpowiedź nie zawiera NIP-u, telefonu, e-maila, adresu, kontaktów ani danych
uwierzytelniających. Przykład wyniku po dokładnym NIP-ie:

```json
{
  "status": "exact",
  "candidate_count": 1,
  "customer_ref": "2933",
  "company_name": "NAZWA FIRMY",
  "matched_by": "nip"
}
```

## Lokalna integracja CHAT_KP

Testowy CHAT_KP używa `CTIP_BASE_URL=http://ctip-bot-api:8082`. Kontener API
jest jedynym mostem między siecią CHAT_KP a wewnętrzną siecią CTIP i nie
wystawia portu na hoście. Kontenery `public` oraz `outbox` nie są dołączane do
sieci Firebird/PostgreSQL CTIP. Połączenie PostgreSQL API wskazuje jednoznaczną
nazwę `ctip-prod-mirror-postgres-1`, aby uniknąć kolizji z aliasem `postgres`
bazy CHAT_KP. Mock `chat_kp-ctip-mock-1` pozostaje zatrzymany.

Test end-to-end obejmuje rozpoznanie firmy, wyzwanie SMS bez rzeczywistej
wysyłki, odczyt aktywnych urządzeń z pełnym numerem seryjnym po poprawnym SMS,
utworzenie sprawy LAB i jej ponowny odczyt. NIP, liczniki, umowy, historia
serwisu i zapisane dane kontaktowe nie są zwracane w tym przepływie. Retencja
spraw lokalnego adaptera wynosi 360 dni.

Po przełączeniu dwa historyczne rekordy `case-*`, utworzone wcześniej przez
mock, zachowano w PostgreSQL CHAT_KP ze statusem `delivery_failed`. Dzięki temu
worker nie odpytuje bez końca identyfikatorów, których rzeczywiste CTIP nigdy
nie utworzyło.

Telefon, NIP i numer seryjny są szyfrowane. Dokładne wyszukiwanie telefonu i NIP
korzysta z osobnych deterministycznych indeksów HMAC. Konto mobilne otrzymuje
poziom `trusted`. Zwykły kontakt i telefon firmy otrzymują poziom
`self_declared`: po dopasowaniu telefonu wymagają weryfikacji NIP, a następnie
jawnego potwierdzenia aktualności firmy.

Duplikaty numerów są niejednoznaczne. Administrator wybiera w panelu dokładną
osobę i domyślną firmę; decyzja jest wersjonowana i audytowana. Punkt odniesienia
raportu źródłowego to 509 niewyłączonych kont oraz 14 numerów powtarzających się
przy 29 kontach.

Kanał wywołuje kolejno:

1. `POST /internal/v1/identities/resolve-phone`;
2. dla źródła innego niż konto mobilne wywołuje `verify-nip`;
3. przedstawia rozpoznaną osobę i firmę bez danych urządzeń;
4. po odpowiedzi użytkownika wywołuje `confirm-current`;
5. dopiero z jednorazowym `disclosure_grant` pobiera urządzenia.

Zaufane i aktualnie potwierdzone powiązanie dopuszcza pełny numer seryjny.
W wewnętrznym przepływie voice/chat powiązanie `operator_approved` z grantem
`masked` dopuszcza wyłącznie ostatnie cztery znaki. Liczniki, umowy, historia
serwisu i zapisane dane kontaktowe nie są częścią API.

`POST /v1/customers/{customer_ref}/devices/masked` zachowuje historyczną nazwę
trasy dla zgodności, ale po poprawnym SMS zwraca pola `device_ref`, `producer`,
`model`, `serial`, `serial_last4`, `image_url`, `location` i `active`.
`device_ref` jest losowym UUID niezależnym od Firebird `ID_MASZYNA`.
`serial_last4` jest wyliczane bezpośrednio z odszyfrowanego `serial`.
Urządzenia nieaktywne, należące do innego klienta albo pozbawione pełnego
numeru seryjnego nie są ujawniane.

`POST /internal/v1/customers/{customer_ref}/devices` zwraca ten sam zestaw pól.
Pełny `serial` jest dostępny wyłącznie dla grantu `full_serial`; zdjęcie i
pozostałe pola są zwracane dopiero po sprawdzeniu klienta, kanału, rozmowy,
ważności oraz jednorazowości `disclosure_grant`.

`POST /v1/cases` zachowuje pojedyncze `device_ref` i przyjmuje opcjonalne
`device_refs`. Duplikaty są usuwane z zachowaniem kolejności, limit wynosi 20,
a każde urządzenie musi być aktywne i należeć do klienta potwierdzonego tym
samym wyzwaniem SMS. Jeżeli przekazano oba pola, `device_refs` po deduplikacji
musi zawierać wyłącznie wartość `device_ref`; inna kombinacja jest odrzucana.

API zgodności dla CHAT_KP udostępnia dodatkowo `GET /v1/capabilities`,
`POST /v1/customers/resolve`, testowe wyzwania `/v1/sms/challenges`,
urządzenia po SMS oraz idempotentne `POST /v1/cases`. Stały kod laboratoryjny
działa tylko przy `CRM_LAB_MODE=true`, `PGDATABASE=ctip_test` i
`SMS_TEST_MODE=true`; żadna wiadomość nie jest wysyłana.
