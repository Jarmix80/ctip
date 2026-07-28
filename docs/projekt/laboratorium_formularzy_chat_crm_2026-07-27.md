# Laboratorium formularzy, CHAT_KP i Centrum Obsługi

## Stan implementacji CTIP

CTIP udostępnia trwałe Centrum Obsługi w wersji LAB `0.6.0`. Sprawy są
zapisywane w `ctip.crm_case`, a historia operacji w `ctip.crm_case_event`.
Interfejs `/crm` korzysta z API `/api/crm/v1`, pozwala wskazać aktywnego
użytkownika CTIP jako deklarowanego operatora oraz nie uruchamia SMS, poczty ani
zapisu do Firebird.

Izolowana aplikacja `app.crm_prototype_app:app` może działać w sieci LAN bez
logowania. API pozostaje zablokowane, jeżeli środowisko nie spełnia jednocześnie
warunków:

- `CRM_ENABLED=true`;
- `CRM_LAB_MODE=true`;
- `CRM_PUBLIC_PROTOTYPE_MODE=true`;
- `PGDATABASE=ctip_test`;
- `FB_HOST=127.0.0.1`;
- `SMS_TEST_MODE=true`.

Reset LAB wymaga wskazania operatora i uzasadnienia. Usuwa wyłącznie rekordy
`is_lab=true`, a podsumowanie zachowuje w dzienniku administratora. Retencja
treści wynosi 360 dni, natomiast zakończone sprawy są archiwizowane po 30 dniach.

## Brama WordPress i kanały LAB

Osobna aplikacja `app.lab_portal_app:app` działa na porcie `8790` i udostępnia:

- `/chat` – widget lokalnego CHAT_KP przez proxy ograniczone do `/widget/v1/*`,
  `/api/v1/*` i `/privacy-notice`;
- `/forms` – siedem wariantów formularzy: kontakt, produkt/wynajem, Serwis + IT,
  liczniki, księgowość, umowy oraz aplikacja Ksero Partner;
- `/scenarios` – generator fikcyjnych spraw dla wszystkich sześciu kolejek;
- `/crm` – istniejący prototyp pracy operatorów;
- `/api/crm/v1/intake` – idempotentne przyjęcie formularza lub scenariusza jako
  sprawy `is_lab=true`, bez deklarowania pracownika w imieniu klienta.

Brama nie przekazuje ciasteczek WordPressa ani nagłówka `Origin` do CHAT_KP.
Przekazywane są wyłącznie `Accept`, `Authorization` i `Content-Type`. Adres
upstreamu jest stałą konfiguracją `CRM_LAB_CHAT_BASE_URL`, więc brama nie jest
otwartym proxy.

Współdzielony sekret jest utrzymywany poza Git:

```text
pagekp: KP_CTIP_LAB_IFRAME_SECRET
CTIP:   CRM_LAB_IFRAME_SECRET
```

Wtyczka WordPress znajduje się w osobnym repozytorium:
`public/wp-content/plugins/kp-ctip-lab/`. Tworzy stronę
`centrum-obslugi-lab`, oznacza ją `noindex` i wymaga tajnego linku. Zmiana nie
modyfikuje motywu ani dotychczasowych formularzy Bitrix.

## Kontrakty kanałów

Zaufane usługi voice i chat używają różnych tokenów. CTIP udostępnia:

- `GET /v1/capabilities`;
- `POST /v1/customers/resolve`;
- `POST /v1/sms/challenges`;
- `POST /v1/sms/challenges/{challenge_ref}/verify`;
- `POST /v1/customers/{customer_ref}/devices/masked`;
- `POST /v1/cases`;
- `GET /v1/cases/{case_ref}`.

Utworzenie sprawy jest idempotentne po haszowanym `Idempotency-Key` oraz po
parze źródło–identyfikator zewnętrzny. Testowy kod SMS jest zwracany w odpowiedzi
wyłącznie w bezpiecznym LAB i nie trafia do logów ani bramki SMS.

## Granice bezpieczeństwa

Worker katalogu czyta lokalny Firebird read-only co 5 minut. Kopiuje aktywne
firmy, kontakty, telefony i urządzenia, ale nie kopiuje e-maili ani wartości
`KONTAKT.LOCK_USER`. Telefony, NIP i numery seryjne są szyfrowane, a dopasowanie
telefonu oraz NIP odbywa się przez odseparowane indeksy HMAC.

Konto mobilne wymaga potwierdzenia firmy. Zwykły kontakt i telefon firmy wymagają
dodatkowo poprawnego NIP. Grant urządzeń jest jednorazowy. Powiązanie
`operator_approved` po SMS ujawnia tylko ostatnie cztery znaki numeru seryjnego.

## Migracje lokalnego CTIP

Lokalna baza `ctip_test` znajdowała się na rewizji urządzeń `c4d8e2f6a1b3`,
natomiast gałąź umów/dostaw kończyła się rewizją `4c9a2e7d6f10`. Rewizja
`d6f1a8c3e740` scala obie historie bez dodatkowego DDL. Następnie wykonywane są
rewizje katalogu tożsamości `71c4e8a2d9f0` i CRM `e2b7c4d9a610`.

Przed lokalną migracją wykonano i zweryfikowano backup:

```text
inbox/backups/crm_lab/ctip_test_before_crm_20260727_164748.dump
```

Po migracji `alembic current` i `alembic heads` wskazują jeden head
`e2b7c4d9a610`. Operacja dotyczyła wyłącznie lokalnej bazy `ctip_test`.

## Etapy poza repozytorium CTIP

Repozytorium CHAT_KP wymaga osobnego backupu lokalnej bazy, potwierdzenia hosta
i nazwy bazy oraz wykonania pełnego grafu Alembic obejmującego rewizje
`c418ba62a9d1` i `d947f12c3a80`. Migracji nie wolno uruchamiać pojedynczym SQL
ani na bazie produkcyjnej. Przed `alembic upgrade head` trzeba potwierdzić jeden
head i poprawne `down_revision` obu rewizji.

W bieżącym etapie CTIP nie zmieniono żadnego pliku repozytorium CHAT_KP.
