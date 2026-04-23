# Publiczne formularze - stan na 2026-04-09

## Cel notatki
Ten plik zamyka etap produkcyjnego wystawienia `form.ksero-partner.com.pl` i zapisuje decyzje techniczne potrzebne przed kolejnym etapem rozbudowy formularza.

## Zakres zamknięty w wersji 0.2.3
- publiczna subdomena `form.ksero-partner.com.pl` działa na osobnej aplikacji `app.public_forms_app:app`,
- klient widzi wyłącznie:
  - `/`
  - `/health`
  - `/formularz/{token}`
- pełny panel CTIP pozostaje poza publiczną subdomeną,
- `http://form.ksero-partner.com.pl/...` przekierowuje do `https://form.ksero-partner.com.pl/...`,
- `https://form.ksero-partner.com.pl/admin` zwraca `404`,
- wersja aplikacji i panelu operatora została oznaczona jako `0.2.3`.

## Konfiguracja końcowa produkcji
1. DNS:
   - `form.ksero-partner.com.pl -> publiczny adres routera`
2. Router:
   - publikacja wyłącznie `80/tcp` i `443/tcp` do hosta `192.168.0.8`
3. Serwer Windows:
   - `CTIP-Web` na `:8000`
   - `CTIP-FormsPublic` na `127.0.0.1:8100`
4. IIS:
   - witryna `MyPHPApp`
   - binding `http *:80:`
   - binding `https *:443:form.ksero-partner.com.pl` z `sslFlags=1`
5. Lokalny `web.config` witryny:
   - `forms_http_to_https` - redirect `HTTP -> HTTPS`
   - `forms_public_proxy` - rewrite do `127.0.0.1:8100`
6. Globalny `applicationHost.config`:
   - `proxy enabled="true"`
   - brak globalnej reguły `forms_public_proxy` w `rewrite/globalRules`

## Faktycznie napotkane problemy i rozwiązania
### 1. Brak modułu `app.public_forms_app`
Przyczyna:
- kod `public forms` nie był jeszcze wgrany na serwer, ponieważ pliki początkowo nie były częścią wdrożonego `HEAD`.

Rozwiązanie:
- domknięcie funkcji w repo,
- commit i wdrożenie na serwer,
- ponowny start usługi `CTIP-FormsPublic`.

### 2. Konflikt `443` z `RRAS/SSTP`
Objaw:
- `W3SVC 1007`
- witryna IIS przechodziła w `Stopped`
- brak listenera na `443`

Przyczyna:
- rezerwacje `http://+:443/sra_*` i `https://+:443/sra_*` należące do `SstpSvc`

Rozwiązanie:
- zatrzymanie `RemoteAccess` i `SstpSvc`,
- usunięcie konfliktowych rezerwacji `HTTP.SYS`,
- ponowny start witryny IIS.

### 3. Brak redirectu `HTTP -> HTTPS`
Objaw:
- `http://form.ksero-partner.com.pl/health` zwracał `200`, a nie `301`

Przyczyna:
- w `applicationHost.config` istniała globalna reguła `rewrite/globalRules/forms_public_proxy`, która przechwytywała cały ruch dla hosta formularzy przed regułami witryny.

Rozwiązanie:
- usunięcie globalnej reguły `forms_public_proxy`,
- pozostawienie proxy wyłącznie w lokalnym `web.config` witryny.

## Stan końcowy do odbioru
- `http://form.ksero-partner.com.pl/health` -> `301`
- `https://form.ksero-partner.com.pl/health` -> `200` dla `GET`
- `https://form.ksero-partner.com.pl/` -> strona informacyjna
- `https://form.ksero-partner.com.pl/admin` -> `404`
- `https://form.ksero-partner.com.pl/formularz/<token>` -> publiczny formularz klienta

## Ograniczenia bieżącej wersji
- formularz publiczny działa poprawnie jako flow krokowy, ale obecny etap zamyka przede wszystkim warstwę publikacji i bezpieczeństwa,
- nie rozwijano jeszcze nowych zachowań UX po stronie klienta poza dotychczasowym zakresem etapów formularza.

## Rozszerzenie w wersji 0.2.5
- generator formularzy pozostaje pod adresem `/genform`,
- panel `/admin` otrzymał osobna sekcje `Obsluga formularza`,
- sekcja zapisuje publiczny adres bazowy formularza oraz szablony wiadomosci:
  - SMS do klienta po wygenerowaniu linku,
  - e-mail z linkiem do formularza,
  - e-mail potwierdzajacy zapis formularza,
  - SMS do operatora po zapisaniu formularza,
- sekcja pokazuje podglad renderu szablonow na przykladowych danych, aby administrator mogl sprawdzic link i tresc jeszcze przed zapisem lub wygenerowaniem prawdziwego formularza,
- generator przestal polegac na twardo wpisanych tresciach i fallbackach do lokalnego adresu, jezeli konfiguracja zostala zapisana w `admin_setting.form_handling.*`.

## Korekta w wersji 0.2.6
- domyslne tresci SMS i e-mail zostaly dopasowane do komunikacji z klientem Ksero Partner,
- sekcje Firebird w `/admin -> Konfiguracja bazy` dostaly jawne przypisanie endpointow:
  - Menadzer Serwisu -> `firebird`, `PUT /admin/config/firebird`, `POST /admin/firebird/test`,
  - v-maintenance -> `firebird_vmaintenance`, `PUT /admin/config/firebird-vmaintenance`, `POST /admin/firebird/test-vmaintenance`,
- zmiana zamyka ryzyko pomylenia konfiguracji bazy glownej z baza `v-maintenance` w warstwie UI.

## Rozszerzenie w wersjach 0.2.12-0.2.16
### 0.2.12
- ekran `/genform` dostal czytelny widok szczegolow formularza po statusie `SUBMITTED`, oparty o ten sam uklad danych co finalne podsumowanie klienta,
- widok szczegolow wspiera `Kopiuj`, `Drukuj` i `PDF`,
- formularz klienta i widok szczegolow zostaly rozszerzone o pola:
  - `E-mail reprezentanta`,
  - `Telefon reprezentanta`,
- wydruk/PDF zostal doprowadzony do zwartego ukladu A4 z zachowaniem marginesow.

### 0.2.13
- po zapisaniu formularza system wysyla SMS nie tylko do autora, ale do wszystkich aktywnych uzytkownikow oznaczonych jako `Handlowiec`,
- po statusie `SUBMITTED` backend automatycznie sprawdza Menadzer Serwisu po NIP:
  - przy znalezionym kliencie zapisuje powiazanie,
  - przy braku klienta moze utworzyc nowa kartoteke,
  - wynik zapisuje do `ctip.form_request.ms_status`.

### 0.2.14
- konfiguracja Firebird uzywana przez automat formularzy, workflow i akcje `/contracts` jest ladowana runtime z panelu administratora (`admin_setting -> firebird.*`),
- naprawiono modal edycji uzytkownika w panelu administratora, aby ponownie udostepnial pelna edycje danych.

### 0.2.15
- sekcja `Konfiguracja Firebird (Menadzer Serwisu)` otrzymala przelacznik `Odblokuj zapis do Firebird`,
- blokada zapisu jest zapisywana w `admin_setting -> firebird.allow_writes` i steruje automatem MS bez recznej ingerencji w zmienne srodowiskowe.

### 0.2.16
- w trybie `local` Firebird dopuszczono absolutne sciezki testowe hosta, np. `D:\\BAZA_MS_KP\\BAZAMS.FDB`,
- usunieto bledna walidacje wymagajaca, aby lokalna kopia `.FDB` znajdowala sie wewnatrz repozytorium; jedynym wymogiem pozostaje istnienie pliku i jawne odblokowanie zapisu w panelu.

### 0.2.17
- modal `Dodaj urzadzenie` w `/genform` zostal przebudowany do ukladu roboczego blizszego `/flow`: ma statusowe karty naglowka, filtry tabeli, rezerwacje arkusza Google oraz reczne pola cen `netto/brutto`,
- modal `Proforma` pokazuje osobno dane firmy z formularza i aktualnego nabywce dokumentu; przy zaznaczeniu `Proforma na bank` widoczne sa dane klienta bankowego GRENKE, a po odznaczeniu dane klienta z formularza,
- zapis PDF odbywa sie bez otwierania podgladu A4, a usuniecie proformy czyści tylko informacje o dokumencie i zostawia aktywna rezerwacje urzadzen.

## Następny etap: rozbudowa możliwości formularza i jego interakcji
Najbliższy krok produktowy powinien dotyczyć wyłącznie warstwy interakcji formularza `GET/POST /formularz/{token}`.

Proponowany zakres:
1. walidacja inline dla każdego kroku bez czekania na finalny submit,
2. lepsze prowadzenie użytkownika między krokami wraz z widocznym stanem wymaganych pól,
3. czytelniejsza edycja podsumowania z możliwością szybkiego powrotu do konkretnej sekcji,
4. dopracowanie interakcji listy reprezentantów:
   - dodawanie,
   - usuwanie,
   - walidacja dokumentów i dat,
5. dopracowanie komunikatów błędów i stanów po zapisaniu formularza,
6. ewentualne zachowanie stanu roboczego po stronie przeglądarki, jeżeli okaże się potrzebne biznesowo.

## Dokumenty powiązane
- `docs/instal/public_forms_production.md`
- `docs/instal/windows_server_2022.md`
- `README.md`
