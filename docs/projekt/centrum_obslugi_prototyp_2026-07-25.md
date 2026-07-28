# Centrum Obsługi — prototyp interfejsu

## Cel etapu

Widok pod adresem `/crm` przedstawia centrum przyjmowania i prowadzenia spraw klientów Ksero Partner. Łączy w jednym interfejsie kontakty pochodzące z formularzy WWW, chatu WWW, bota głosowego, telefonu i poczty elektronicznej.

Od wersji LAB `0.6.0` sprawy i ich oś zdarzeń zapisują się w PostgreSQL `ctip_test`. Moduł nadal nie zapisuje Firebird, nie wysyła wiadomości i nie tworzy rzeczywistych zleceń w Menadżerze Serwisu.

## Dostęp i bezpieczeństwo

- Widok korzysta z istniejącej sesji panelu CTIP i endpointu `/auth/me`.
- Brak ważnej sesji powoduje powrót do strony logowania.
- Izolowana instancja LAB może działać w zaufanej sieci LAN po ustawieniu `CRM_PUBLIC_PROTOTYPE_MODE=true`, `CRM_ENABLED=true` i `CRM_LAB_MODE=true`.
- Instancja korzysta z `app.crm_prototype_app:app`, która udostępnia `/crm`, pliki `/static` i ograniczone API `/api/crm/v1`; endpointy logowania i panelu administracyjnego nie są rejestrowane.
- API LAB dodatkowo wymaga `PGDATABASE=ctip_test`, lokalnego Firebird i `SMS_TEST_MODE=true`. Niespełnienie dowolnego warunku kończy się odpowiedzią 503.
- Kafelek „Centrum Obsługi” jest widoczny dla użytkowników posiadających dostęp do sekcji operatora.
- Interfejs nie ładuje startowych spraw demonstracyjnych; pokazuje wyłącznie rekordy zapisane w `ctip_test`.
- Operacje przejęcia, notatki, zamknięcia i testowego przekazania zapisują się w osi sprawy. SMS, e-mail i zapis Firebird pozostają zablokowane.

## Widoki prototypu

1. **Strona główna** — statystyki spraw, rozbudowane karty spraw wymagających uwagi z danymi firmy, osoby kontaktowej i treścią zgłoszenia, rozkład kanałów, ostatnia aktywność oraz obciążenie działów.
2. **Skrzynka wejściowa** — wspólna lista wszystkich otwartych kontaktów, niezależnie od kanału i kolejki działowej.
3. **Moje sprawy** — kolejka spraw przypisanych do zalogowanego pracownika.
4. **Kolejki operacyjne** — Handel, Serwis + IT, Księgowość, Umowy, Liczniki oraz Inne sprawy.
5. **Formularze WWW** — katalog formularzy planowanych jako zamiennik integracji z Bitrix.
6. **Archiwum** — zakończone sprawy z zachowaną osią czynności.

## Interakcje demonstracyjne

- wyszukiwanie po numerze sprawy, firmie, osobie, temacie, wiadomości, telefonie i adresie e-mail;
- filtrowanie kolejki według stanu;
- otwieranie szczegółów sprawy i osi aktywności;
- przejęcie sprawy handlowej przez przeciągnięcie do Michała albo Kamila;
- ręczne utworzenie sprawy LAB z wyborem kanału, kolejki i priorytetu;
- dodanie lokalnej notatki;
- przypisanie sprawy dowolnemu aktywnemu użytkownikowi CTIP bez wysyłki SMS i e-maila;
- testowy zapis numeru zlecenia MS dla kolejki Serwis + IT bez zapisu Firebird;
- zapis testowych liczników B/W, kolor i skan wyłącznie w audycie sprawy;
- autoarchiwizacja po 30 dniach od pierwszego przejęcia handlowego albo zakończenia pozostałej sprawy;
- kontrolowany reset wyłącznie spraw `is_lab=true` z zachowaniem podsumowania audytowego.

## Widoki operacyjne

1. **Kanban Handlu** — trzy kolumny: Nowe, Obsługiwane — Michał oraz Obsługiwane — Kamil. Kafelki zawierają firmę, osobę kontaktową, telefon, e-mail i treść zgłoszenia. Pierwsze przypisanie rozpoczyna 30-dniowy termin autoarchiwizacji, który nie resetuje się po zmianie handlowca.
2. **Pulpit dyspozytora** — dwukolumnowy widok pozostałych kolejek. Po lewej znajduje się strumień spraw pokazujący temat, firmę, osobę kontaktową, telefon i skrót treści. Po prawej prezentowane są powiększone dane firmy, dane kontaktowe ułożone w opisanych wierszach, pełna treść i akcje operacyjne. Widok nie zawiera statystyk obciążenia zespołu.
3. **Lista operacyjna** — opcjonalna zwarta tabela dostępna jako przełącznik w każdej kolejce.
4. **Typografia i separacja spraw** — wersja `0.5.2` podnosi minimalne rozmiary etykiet, treści, przycisków, tabel i kart we wszystkich widokach. Sprawy na stronie głównej i w strumieniu dyspozytora mają subtelne obramowanie, odstęp oraz naprzemiennie biały i lekko niebieskoszary odcień, natomiast aktywna sprawa zachowuje wyraźniejsze zaznaczenie.

## Docelowy kierunek wdrożenia

1. Uzgodnić słownik działów, statusów, priorytetów i uprawnień.
2. Zaprojektować modele spraw, uczestników, wiadomości, aktywności, załączników i źródeł kontaktu.
3. Podłączyć centralny katalog tożsamości klientów z bezpiecznym rozpoznawaniem kanałów.
4. Zastąpić formularze Bitrix własnymi formularzami publicznymi i zachować kontekst strony wejścia.
5. Dodać kontrolowane adaptery do Menadżera Serwisu, poczty, centrali, SMS i pozostałych systemów.
6. Wprowadzić kolejki pracowników, atomowe przejmowanie spraw, zastępstwa, terminy reakcji i pełny audyt operacji.
7. Uruchamiać kolejne integracje osobno, domyślnie w trybie wyłączonym, po testach bezpieczeństwa i akceptacji procesu.

## Granica bieżącego etapu

Wersja `0.6.0` jest trwałym laboratorium CTIP. Numery zleceń MS, wyniki SMS/e-mail i aktualizacje liczników pozostają testowe i nie uruchamiają adapterów wykonawczych. Integracja WordPress i CHAT_KP jest opisana w `laboratorium_formularzy_chat_crm_2026-07-27.md`.
