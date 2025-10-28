# Panel operatora CTIP

## Zakres funkcjonalny
- Lista ostatnich połączeń (`/operator/api/calls`) normalizuje CLIP/CLIR do 9 cyfr (usuwa prefiksy `0`, `+48`, spacje) i domyślnie filtruje ruch wewnętrzny (np. 101↔105), aby operator skupiał się na połączeniach zewnętrznych.
- Pasek nagłówka prezentuje statystyki wysłanych SMS: bieżący dzień i miesiąc (`/operator/api/stats`), aktualizowane co 30 s lub przy przeładowaniu widoku.
- Szczegóły połączenia (`/operator/api/calls/{id}`): pełna oś czasu CTIP, powiązany kontakt z książki adresowej oraz historia SMS; z tego widoku operator może dodać lub zaktualizować wpis kontaktu (w tym `firebird_id`, notatki) bez opuszczania ekranu.
- Szybka wysyłka wiadomości (`POST /operator/api/sms/send`) z automatycznym uzupełnieniem numeru na podstawie wybranego połączenia.
- Wyszukiwarka kontaktów (`/operator/api/contacts/by-number/{number}`) prezentująca dane, pola Firebird i notatki operatora.
- Historia SMS dla numeru (`/operator/api/sms/history?number=`) z możliwością filtrowania limitu wpisów; treści zawierające dane logowania są anonimizowane (`***`) dla bezpieczeństwa.
- Ustawienia operatora: edycja profilu (imię, nazwisko, e-mail, numer wewnętrzny, telefon), zmiana hasła oraz własne szablony SMS (CRUD).
- Obsługa opcji „Zapamiętaj mnie” – wydłużona ważność tokenu (`ADMIN_SESSION_REMEMBER_HOURS`) i przechowywanie w `localStorage`.

## Ścieżki UI
- `/operator` – główny panel operatora (layout oparty o prototyp `prototype/index.html`).
- `/operator/settings` – moduł ustawień operatora (profil, hasło, szablony SMS).

## API panelu operatora
| Endpoint | Metoda | Opis |
|----------|--------|------|
| `/operator/api/me` | GET | Podstawowe informacje o zalogowanym operatorze. |
| `/operator/api/profile` | GET | Szczegóły profilu (imię, nazwisko, e-mail, numer wewnętrzny, telefon, rola). |
| `/operator/api/profile` | PUT | Aktualizacja danych profilu. |
| `/operator/api/profile/change-password` | POST | Zmiana hasła (wymaga podania obecnego hasła). |
| `/operator/api/calls` | GET | Lista połączeń (`limit`, `search`, `direction`). |
| `/operator/api/calls/{id}` | GET | Szczegóły połączenia (wydarzenia CTIP, kontakt, historia SMS). |
| `/operator/api/contacts/by-number/{number}` | GET | Dane kontaktu na podstawie numeru MSISDN. |
| `/operator/api/sms/history` | GET | Historia SMS dla numeru (`number`, `limit`). |
| `/operator/api/sms/send` | POST | Dodanie wiadomości do kolejki `sms_out`. |
| `/operator/api/sms/templates` | GET | Lista szablonów (globalne + operatora) z flagą `editable`. |
| `/operator/api/sms/templates` | POST | Dodanie szablonu operatora. |
| `/operator/api/sms/templates/{id}` | PUT | Edycja szablonu operatora. |
| `/operator/api/sms/templates/{id}` | DELETE | Usunięcie szablonu operatora. |

Każde żądanie wymaga nagłówka `X-Admin-Session` i roli `operator` lub `admin`.
