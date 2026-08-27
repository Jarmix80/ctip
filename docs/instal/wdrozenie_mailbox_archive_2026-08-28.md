# Wdrożenie rejestru i archiwum mailboxa GRENKE

## Cel

Wdrożenie migracji `a7c4e2f9b1d3`, trwałego rejestru wiadomości, archiwum historycznego oraz bezpiecznej obsługi konfliktów właściciela urządzeń. Zmiana dotyczy wyłącznie usługi `CTIP-Web`; usługi `CollectorService`, `CTIP-SMS` i `CTIP-FormsPublic` pozostają uruchomione.

## Warunki wejściowe

- bieżący commit produkcji: `7ca3178e5295b6af52c7756f15d4e174bed38b31`,
- bieżąca rewizja Alembic: `f9a0b1c2d3e4`,
- docelowa rewizja Alembic: `a7c4e2f9b1d3`,
- czysty katalog roboczy produkcji,
- aktywne i sprawdzone kopie PostgreSQL, pliku Firebird, `.env` oraz katalogu archiwum mailboxa,
- działające usługi `CTIP-Web`, `CTIP-FormsPublic`, `CollectorService` i `CTIP-SMS` przed rozpoczęciem prac.

## Sekwencja wdrożenia

1. Zapisać stan usług, bieżący commit i rewizję Alembic.
2. Wykonać pełną kopię produkcyjną do nowego katalogu z datą i godziną.
3. Zatrzymać wyłącznie `CTIP-Web`. Zatrzymanie usługi wyłącza scheduler i zapobiega pobraniu wiadomości podczas migracji.
4. Pobrać zatwierdzony commit z GitHub i sprawdzić czystość katalogu roboczego.
5. Ustawić `CONTRACTS_MAILBOX_PROCESSING_ENABLED=false` w środowisku `CTIP-Web`.
6. Uruchomić `python -m alembic upgrade a7c4e2f9b1d3` z konfiguracją produkcyjną.
7. Uruchomić próbny backfill: `python scripts/contracts_mailbox_sync.py --backfill --dry-run`.
8. Porównać wynik z wartościami kontrolnymi. Dla audytowanej skrzynki oczekiwane są: `linked_form=118`, `historical_archived=51`, `ignored=2`, `manual_hold=1`, `error=0`, łącznie `172` wiadomości i `11` spraw historycznych.
9. Jeżeli liczniki są inne, nie wykonywać zapisu. Wyjaśnić różnicę na kopii testowej.
10. Uruchomić zapis: `python scripts/contracts_mailbox_sync.py --backfill --apply-backfill`.
11. Powtórzyć zapytania kontrolne do rejestru i sprawdzić unikalność `message_id`.
12. Ustawić `CONTRACTS_MAILBOX_PROCESSING_ENABLED=true` i uruchomić `CTIP-Web`.
13. Sprawdzić `/health`, `/genform`, listę „Wiadomości historyczne”, podgląd treści i pobranie jednego załącznika.
14. Zweryfikować, że `CTIP-FormsPublic`, `CollectorService` i `CTIP-SMS` nie były restartowane oraz nadal działają.

## Kontrole biznesowe

- Formularz `59`: wiadomość zgody ma zostać przypięta, status ma przejść do `APPROVED_ORDER`, a urządzenie już przypisane do klienta docelowego ma otrzymać snapshot bez zmiany `ID_KLIENT` w Firebird.
- Formularz `60`: wiadomość ma mieć stan `manual_hold`, status formularza ma pozostać `WAITING_SIGNATURE`, a Firebird nie może otrzymać żadnego zapisu dla pakietu mieszanego.
- Korespondencja pomocnicza przypięta do formularzy nie może zmienić statusu biznesowego.
- Stany końcowe nie mogą zwiększać licznika prób w kolejnych przebiegach schedulera.

## Zapytania kontrolne

```sql
SELECT processing_status, count(*)
FROM ctip.contracts_mailbox_message
GROUP BY processing_status
ORDER BY processing_status;

SELECT count(*) AS total,
       count(DISTINCT message_id) AS unique_message_ids
FROM ctip.contracts_mailbox_message;

SELECT count(*)
FROM ctip.contracts_mailbox_history_case;

SELECT form_request_id, processing_status, classification, subject
FROM ctip.contracts_mailbox_message
WHERE form_request_id IN (59, 60)
ORDER BY received_at;
```

## Wycofanie

1. Zatrzymać wyłącznie `CTIP-Web`.
2. Przywrócić poprzedni commit.
3. Jeżeli backfill nie został wykonany, można zastosować `python -m alembic downgrade f9a0b1c2d3e4`.
4. Jeżeli backfill został wykonany, przed downgrade wykonać eksport obu tabel mailboxa; downgrade usuwa wyłącznie nowe tabele.
5. Przywrócić poprzednią konfigurację środowiska i uruchomić `CTIP-Web`.
6. Ponownie sprawdzić wszystkie cztery usługi i podstawowe endpointy.
