# SerwerSMS.pl – HTTPS API v2: Kompletny manual (Markdown)

> Ten manual został przygotowany na podstawie oficjalnej dokumentacji SerwerSMS.pl dla **HTTPS API v2** i powiązanych podstron (Wysyłka SMS/MMS/RCS, nazwy nadawcy, kontakty i grupy, wiadomości przychodzące, kody błędów, tester RCS itd.). Zawiera syntetyczne podsumowanie endpointów, parametrów, przykładów i dobrych praktyk integracyjnych.

---

## Spis treści
1. [Wprowadzenie / Zasady komunikacji](#wprowadzenie--zasady-komunikacji)
2. [Autoryzacja i komunikaty ogólne](#autoryzacja-i-komunikaty-ogólne)
3. [Wysyłanie SMS (jednolita treść)](#wysyłanie-sms-jednolita-treść)
4. [Wysyłanie SMS spersonalizowanych](#wysyłanie-sms-spersonalizowanych)
5. [Wysyłanie MMS (upload plików + wysyłka)](#wysyłanie-mms-upload-plików--wysyłka)
6. [Wiadomości VOICE (uwagi)](#wiadomości-voice-uwagi)
7. [Wiadomości RCS (Rich Communication Services)](#wiadomości-rcs-rich-communication-services)
8. [Lookup / HLR / NAT (uwagi)](#lookup--hlr--nat-uwagi)
9. [Raporty doręczeń (DLR)](#raporty-doręczeń-dlr)
10. [Usuwanie zaplanowanych wysyłek](#usuwanie-zaplanowanych-wysyłek)
11. [Pobieranie wiadomości przychodzących](#pobieranie-wiadomości-przychodzących)
12. [Stan konta (uwagi)](#stan-konta-uwagi)
13. [Nazwy nadawców (Sender Name)](#nazwy-nadawców-sender-name)
14. [Kontakty i grupy](#kontakty-i-grupy)
15. [Subkonta (uwagi)](#subkonta-uwagi)
16. [Czarna lista (uwagi)](#czarna-lista-uwagi)
17. [Szablony (uwagi)](#szablony-uwagi)
18. [Inne funkcje (Tester RCS, Konsola API)](#inne-funkcje-tester-rcs-konsola-api)
19. [Kody błędów – przegląd](#kody-błędów--przegląd)
20. [Dobre praktyki integracyjne](#dobre-praktyki-integracyjne)
21. [Przykładowe biblioteki i integracje](#przykładowe-biblioteki-i-integracje)
22. [Checklist integracyjny](#checklist-integracyjny)

---

## 1) Wprowadzenie / Zasady komunikacji

- Bazowy adres API: `https://api2.serwersms.pl/` (alternatywnie: `https://s1api2.serwersms.pl/`).
- Protokół: **HTTPS** (szyfrowanie wymagane).
- Metoda: **POST** (GET bywa wspierany, ale POST jest zalecany, zwłaszcza przy dużych payloadach).
- Format odpowiedzi: **JSON** (XML poprzez sufiks `.xml` w ścieżce).
- Numery telefonów: **format międzynarodowy z „+”**, np. `+48500111222`.
- Limity zgłoszeń:
  - Do **100 000 numerów** dla tej samej treści (masowa wysyłka SMS).
  - Do **10 000 rekordów** w jednym zgłoszeniu dla spersonalizowanych wiadomości (tablica `messages`).
- Konto testowe: `username=demo`, `password=demo` + `test=1` w zapytaniu – bez realnej wysyłki.
- Dostępne gotowe biblioteki klienckie (PHP, Node.js, C#, Java, Python, Ruby, Bash, Delphi…).

---

## 2) Autoryzacja i komunikaty ogólne

- W każdym żądaniu przekazuj **`username`** i **`password`**.
- Brak akcji (`action`) lub błędne logowanie → odpowiedź z obiektem `error` (np. `code: 1000/1001`).
- Przykładowe błędy ogólne:
  - `1000 InvalidAction` – nie podano akcji/endpointu,
  - `1001 InvalidUser` – błędny login lub hasło,
  - `1002 InvalidRole` – brak uprawnień do API,
  - `1003 InvalidIP` – niezaufany adres IP (jeśli włączone ograniczenia IP).

> Wiele endpointów wspiera `details=true`, aby zwracać szczegółowe wyniki per numer/pozycję w tablicy `items` (z `error_code` i `error_message`).

---

## 3) Wysyłanie SMS (jednolita treść)

**Endpoint:** `messages/send_sms`

**Parametry kluczowe:**
- `username` *(string, wymagane)* – login do konta,
- `password` *(string, wymagane)* – hasło do konta,
- `phone` *(string lub array, wymagane)* – numer(y) odbiorców,
- `text` *(string, wymagane)* – treść wiadomości,
- `sender` *(string)* – nazwa/numeryczny nadawca (wymaga autoryzacji nazwy; FULL SMS),
- `utf` *(bool)* – włącz, jeśli używasz znaków PL; wymaga FULL SMS,
- `flash` *(bool)* – SMS Flash,
- `test` *(bool)* – tryb testowy (bez realnej wysyłki),
- `date` *(datetime)* – planowana data wysyłki,
- `unique_id` *(string lub array)* – własne identyfikatory wiadomości,
- `group_id`, `contact_id` *(id lub array)* – wysyłka do wybranych kontaktów/grup,
- `details` *(bool)* – szczegóły per numer w odpowiedzi,
- `dlr_url` *(string)* – URL do callbacków raportów doręczeń (nadpisuje ustawienia w panelu).

**Przykład (JSON/POST):**
```json
{
  "username": "twoj_login",
  "password": "twoje_haslo",
  "phone": ["+48500600700", "+48500600500"],
  "text": "Wiadomość testowa",
  "sender": "NADAWCA",
  "utf": true,
  "details": true,
  "date": "2025-10-12 12:00:00",
  "unique_id": ["id1", "id2"]
}
```

**Odpowiedź (skrót):**
```json
{
  "success": true,
  "queued": 2,
  "unsent": 0,
  "items": [
    { "id": "abc123", "phone": "+48500600700", "status": "queued", "parts": 1 },
    { "id": "def456", "phone": "+48500600500", "status": "queued", "parts": 1 }
  ]
}
```

**Uwagi:**
- System deduplikuje numery (powtórzone numery zostaną wysłane raz).
- Dla masowych list stosuj batchowanie (50–200 numerów) i ustaw dłuższy timeout (≥ 60 s).

---

## 4) Wysyłanie SMS spersonalizowanych

**Endpoint:** `messages/send_personalized`

**Idea:** Zamiast `phone` + `text`, przekazujesz tablicę `messages: [{phone, text}, …]`.

**Parametry:**
- `username`, `password` *(wymagane)*,
- `messages` *(array, wymagane)* – lista obiektów `{ "phone": "+48...", "text": "..." }`,
- `sender`, `utf`, `flash`, `test`, `date` *(opcjonalne)*,
- `unique_id` *(string lub array)* – Twoje ID (dla każdego wpisu),
- `details` *(bool)* – zwracanie szczegółów.

**Limit:** do **10 000** rekordów w jednym żądaniu.

---

## 5) Wysyłanie MMS (upload plików + wysyłka)

### 5.1 Upload pliku
**Endpoint:** `files/add`

**Parametry:**
- `username`, `password` *(wymagane)*,
- `type` *(string, wymagane)* – `"mms"` lub `"voice"`,
- **jeden z:** `url` *(string)* albo `file` *(multipart)*.

**Odpowiedź (skrót):**
```json
{ "success": true, "id": "b67d70c22d" }
```

### 5.2 Lista plików
**Endpoint:** `files/index`
Zwraca listę Twoich plików (`id`, `name`, `size`, `type`, `date`).

### 5.3 Wysyłka MMS
**Endpoint:** `messages/send_mms`

**Parametry:**
- `username`, `password` *(wymagane)*,
- `title` *(string)* – tytuł MMS,
- `file_id` *(string lub array)* – ID pliku(ów) z uploadu,
- `phone` *(string lub array)* – numer(y) odbiorców,
- `text` *(string)* – opcjonalny opis,
- `date`, `test`, `details` *(opcjonalne)*.

**Odpowiedź:** analogiczna do SMS (success/queued/unsent/items).

---

## 6) Wiadomości VOICE (uwagi)

W dokumentacji HTTPS API v2 VOICE bywa wzmiankowane przy wysyłkach spersonalizowanych (flaga `voice`), ale pełny opis endpointu nie zawsze jest publiczny. Zalecany kontakt z supportem lub sprawdzenie w API Console, czy VOICE jest dostępne i w jakim wariancie.

---

## 7) Wiadomości RCS (Rich Communication Services)

**Endpoint:** `messages/send_rcs`

**Parametry kluczowe:**
- `username`, `password` *(wymagane)*,
- `rcs_id` *(string, wymagane)* – identyfikator bota RCS skonfigurowanego w panelu,
- `phone` *(string lub array, wymagane)*,
- `message` *(object/array, wymagane)* – struktura treści (tekst, media, rich cards, suggestions),
- `details`, `date`, `unique_id`, `group_id` *(opcjonalne)*.

**Zasady i limity (najważniejsze):**
- `text` do 3072 znaków,
- `title` do 100 znaków, `description` do 1000,
- karuzela (carousel) – max 4 karty,
- dla multimediów wymagane `url`, `size`, `type`,
- liczba „suggestions” ograniczona (zwykle ≤ 11).

---

## 8) Lookup / HLR / NAT (uwagi)

Sekcje widoczne w menu dokumentacji, lecz szczegóły mogą wymagać dostępu poprzez panel/konsolę API lub kontakt z supportem. Typowo: wejście przyjmuje `phone`, zwraca informacje o numerze (sieć, aktywność, portowanie itd.).

---

## 9) Raporty doręczeń (DLR)

- Dla SMS możesz podać `dlr_url` w żądaniu wysyłki lub skonfigurować go w panelu.
- Serwer będzie wywoływał Twój endpoint (GET/POST) z parametrami w stylu: `#SMSID#`, `#STAN#`, `#DATA#`, `#PRZYCZYNA#`.
- Jeśli ustawisz `dlr_url` w samym requestcie, **nadpisze** to ustawienia panelowe.

---

## 10) Usuwanie zaplanowanych wysyłek

Dostępny jest endpoint do anulowania zadań zaplanowanych (`date` > teraz). Nazwa/parametry mogą różnić się w zależności od pakietu – sprawdź w API Console lub poproś support o aktualną specyfikację (np. `messages/delete_scheduled`).

---

## 11) Pobieranie wiadomości przychodzących

**Endpoint (wg dokumentacji):** `messages/recived` *(czasem spotykana pisownia „received” / „incoming”)*

**Parametry:**
- `username`, `password` *(wymagane)*,
- `phone` *(string lub array, wymagane)* – Twój numer docelowy (NDI itp.),
- `date_from`, `date_to` *(datetime)* – zakres,
- `type` *(string)* – `eco | nd | ndi | pre | mms`,
- `ndi` *(string)* – filtr po konkretnym NDI,
- `read` *(bool)* – jeśli `true`, oznacza jako odczytane,
- `show_contact` *(bool)* – dołącz dane kontaktu (jeśli jest w bazie).

**Odpowiedź (przykład skrócony):**
```json
{
  "items": [
    {
      "id": 2477793,
      "type": "eco",
      "phone": "+48500600700",
      "recived": "2025-10-11 11:18:03",
      "message_id": "eba39e4fe1",
      "blacklist": false,
      "text": "Przyjadę o 16"
    },
    {
      "id": 2204974,
      "type": "mms",
      "phone": "+48500600700",
      "recived": "2025-10-11 14:09:15",
      "to_number": "+48664078059",
      "blacklist": false,
      "title": "Temat MMS",
      "attachments": [
        { "id": 2518, "name": "plik.txt", "content_type": "text/plain", "data": "..." }
      ]
    }
  ]
}
```

---

## 12) Stan konta (uwagi)

W dokumentacji znajduje się rozdział o sprawdzaniu salda/limitów, jednak nazwy i formaty endpointów bywają dostępne dopiero w panelu lub API Console (np. `account/status`).

---

## 13) Nazwy nadawców (Sender Name)

**Endpointy:** `senders/add`, `senders/index`

### 13.1 Dodanie nazwy
**Parametry:** `username`, `password`, `name` (alfanum., do 11 znaków).
**Odpowiedź:** `{ "success": true }` (potem oczekiwanie na autoryzację przez operatora).

### 13.2 Lista nazw
**Parametry:** `username`, `password`, opcjonalnie: `predefined`, `type` (`ndi|mms`), `sort`, `order`.
**Odpowiedź – przykładowe pola:** `name`, `agreement` (`required|not_required`), `status` (`pending_authorization|authorized|rejected|deactivated`), `note`, `networks` (dla MMS).

> Uwaga: Nazwa **musi zostać zatwierdzona** zanim będzie można jej używać w `sender` (FULL SMS).

---

## 14) Kontakty i grupy

**Grupy – endpointy:** `groups/index`, `groups/add`, `groups/edit`
**Kontakty – endpointy:** `contacts/index`, `contacts/add`, `contacts/edit`, `contacts/delete`

### 14.1 Grupy
- **Lista (`groups/index`)**: filtrowanie `search`, sortowanie `sort`/`order`. Zwraca m.in. `id`, `name`, `count`.
- **Dodanie (`groups/add`)**: `name` (do 100 znaków). Odpowiedź: `{ "success": true, "id": ... }`.
- **Edycja (`groups/edit`)**: jak wyżej + `id` (szczegóły zależne od wersji).

### 14.2 Kontakty
- **Lista (`contacts/index`)**: filtry `search`, `group_id` (albo `"none"`), sort `first_name|phone|...`, `order`.
- **Dodanie (`contacts/add`)**: `phone` (wymagane), `group_id` (opcjonalnie pojedyncze lub array), oraz opcjonalne pola (`email`, `first_name`, `last_name`, `company`, `tax_id`, `address`, `city`, `description`).
- **Edycja/Usuwanie**: analogicznie, z parametrem `id` lub `ids`.

---

## 15) Subkonta (uwagi)

Sekcja jest dostępna w dokumentacji, natomiast pełna specyfikacja endpointów bywa udostępniana w panelu bądź przez support (np. `subaccounts/index`, `subaccounts/add`, uprawnienia, limity).

---

## 16) Czarna lista (uwagi)

Typowy zestaw operacji: `blacklist/index`, `blacklist/add`, `blacklist/delete`. W publicznych fragmentach dokumentacji bywa skrótowo. Sprawdź API Console/panel, by poznać dokładne parametry (zwykle `phone`, `note`).

---

## 17) Szablony (uwagi)

Zarządzanie szablonami wiadomości (dodawanie, lista, użycie) może być dostępne w panelu lub poprzez endpointy (`templates/index`, `templates/add`, `templates/edit`). Zalecany przegląd w API Console.

---

## 18) Inne funkcje (Tester RCS, Konsola API)

### 18.1 Tester RCS
**Endpoint:** `messages/tester_rcs`
**Parametry:** `username`, `password`, `rcs_id`, `phone`
**Działanie:** wysyła zaproszenie do testów RBM dla danego numeru; użytkownik musi zaakceptować, aby odbierać RCS od bota.

### 18.2 Konsola API
Interaktywna konsola (API Console) umożliwia testy endpointów z Twojego konta (uwierzytelnionego). Ułatwia weryfikację parametrów i odpowiedzi.

---

## 19) Kody błędów – przegląd

| Kod   | Typ                    | Znaczenie (skrót)                                  |
|:-----:|------------------------|----------------------------------------------------|
| 1000  | InvalidAction          | Brak/niepoprawna akcja                             |
| 1001  | InvalidUser            | Błędny login/hasło                                 |
| 1002  | InvalidRole            | Brak uprawnień do API                              |
| 1003  | InvalidIP              | Niezaufany adres IP                                |
| 2000+ | Validation*            | Braki w wymaganych parametrach / walidacja         |
| 3000+ | SendError              | Błędy wysyłki (limity, uprawnienia, numer, długość)|
| 4000+ | FileError              | Błędy uploadu/plików                               |
| 4100+ | PhoneError             | Numer/HLR/NAT                                      |
| 4200+ | PremiumError           | Premium SMS                                        |
| 4400+ | SenderError            | Nazwy nadawców                                     |
| 4500+ | SubaccountError        | Subkonta                                           |
| 4600+ | BlacklistError         | Czarna lista                                       |

> W odpowiedziach „per numer” (gdy `details=true`) sprawdzaj `error_code` i `error_message` przy rekordach z `status: "unsent"`.

---

## 20) Dobre praktyki integracyjne

1. **Batchowanie**: wysyłaj w paczkach 50–200 numerów dla stabilności i krótszego czasu odpowiedzi.
2. **Timeout**: ustaw ≥ 60 s po stronie klienta HTTP dla dużych zgłoszeń.
3. **`details=true`**: w trakcie testów pozwala przejrzyście wykrywać błędy per numer.
4. **Deduplikacja numerów**: po Twojej stronie też warto filtrować duplikaty (API i tak deduplikuje).
5. **`unique_id`**: generuj i przechowuj w swoim systemie mapowanie (3–50 znaków, unikalne).
6. **`dlr_url`**: preferuj push DLR zamiast pollingu; uwzględnij retry i logowanie żądań.
7. **Nazwy nadawców**: zgłaszaj wcześniej, czekaj na autoryzację; nie używaj zastrzeżonych słów/mark.
8. **UTF/FULL SMS**: przy znakach PL wymagaj odpowiedniego kanału/zgód.
9. **Walidacja numerów**: normalize E.164, opcjonalnie HLR/Lookup przed wysyłką.
10. **Obsługa błędów**: nie rollbackuj całej paczki, tylko loguj/naprawiaj „unsent” per pozycja.
11. **Planowane wysyłki**: trzymaj referencje do zadań, by móc je anulować.
12. **Monitoring**: logi request/response, metryki (liczba wysłanych, DLR, błędy, retry).

---

## 21) Przykładowe biblioteki i integracje

SerwerSMS udostępnia klienty/biblioteki w popularnych językach (PHP, Node.js, C#, Java, Python, Ruby, Bash, Delphi). Warto z nich korzystać, by skrócić czas wdrożenia i uniknąć błędów protokołu/serializacji.

---

## 22) Checklist integracyjny

- [ ] HTTPS, metoda POST, JSON (lub `.xml`, jeśli potrzebujesz).
- [ ] `username`/`password` poprawne, IP uprawnione (jeśli restrykcje włączone).
- [ ] Format numerów E.164 (`+48...`), deduplikacja numerów.
- [ ] Testy na `demo/demo` z `test=1` oraz `details=true`.
- [ ] `sender` zatwierdzony (dla FULL, UTF, personalizacji).
- [ ] Długie listy → batch (50–200), timeout ≥ 60 s.
- [ ] `unique_id` z Twojego systemu + mapowanie do rekordów.
- [ ] DLR: `dlr_url` skonfigurowany + obsługa callbacków i retry.
- [ ] Zaplanowane wysyłki: masz endpoint do anulowania i referencje ID.
- [ ] MMS/RCS: najpierw upload plików / konfiguracja BOT-a (`rcs_id`).
- [ ] Logowanie i monitoring (alerty na błędy 4xx/5xx i kody `error.code`).

---

### Nota końcowa
Niektóre endpointy (np. VOICE, subkonta, czarna lista, szablony, stan konta, delete scheduled) mogą mieć szczegółową specyfikację dostępną **w panelu klienta lub API Console**. W praktyce integracyjnej zacznij od: wysyłek SMS (`send_sms`, `send_personalized`), uploadu i MMS (`files/add`, `messages/send_mms`), RCS (`messages/send_rcs`), nazw nadawców (`senders/*`), kontaktów i grup (`contacts/*`, `groups/*`), a następnie dołączaj kolejne moduły według potrzeb.
