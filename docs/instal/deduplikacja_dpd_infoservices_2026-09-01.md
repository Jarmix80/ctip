# Deduplikacja zdarzeń DPD InfoServices

## Cel i zakres

DPD może zwrócić to samo logiczne zdarzenie przez kanał klienta i zapytanie historii listu z różnymi wartościami `id`, `objectId`, opisem, oddziałem, referencją i opisami danych dodatkowych. CTIP zachowuje oba rekordy techniczne, ale migracja `f2b7c9d4e6a1` oznacza jeden z nich jako kanoniczny na podstawie numeru listu, kodu biznesowego, dokładnego czasu i wartości danych dodatkowych. Interfejs, status przesyłki i kamienie milowe Menadżera Serwisu nie pokazują dzięki temu zdarzenia podwójnie.

Operacja nie usuwa rekordów, nie łączy się z Firebirdem i nie zmienia zleceń, RW, WZ ani FV. Modyfikuje wyłącznie pola `semantic_event_key`, `canonical_event_id`, a w grupie objętej `CANCEL` także spójne znaczniki anulowania.

## Kontrola przed wdrożeniem

1. Wykonaj pełny backup PostgreSQL i Firebird zgodnie z głównym runbookiem produkcyjnym.
2. Potwierdź czysty stan Git oraz bieżącą rewizję Alembic `d6e8f0a2b4c7`.
3. Wdróż dokładny commit wydania i wykonaj migrację do `f2b7c9d4e6a1`.
4. Nie włączaj globalnych kamieni milowych Firebirda podczas porządkowania historii.

## Dry-run i zastosowanie

Na serwerze Windows użyj środowiska NSSM usługi `CTIP-Web`; nie wypisuj jego wartości. W środowisku Linux aktywuj `.venv` i właściwy plik środowiskowy. Najpierw wykonaj wyłącznie podgląd:

```bash
python scripts/dpd_infoservices_dedupe.py
```

Raport pokazuje liczbę rekordów technicznych, zdarzeń logicznych, aliasów, grup oraz rekordów wymagających aktualizacji. Dla wcześniej rozpoznanego pilota oczekiwany wynik to 18 rekordów technicznych, 9 zdarzeń logicznych i 9 aliasów. Każda inna wartość wymaga przerwania operacji i ponownej analizy.

Po zatwierdzeniu raportu użyj dokładnego `state_token`:

```bash
python scripts/dpd_infoservices_dedupe.py --apply \
  --state-token TOKEN_Z_DRY_RUN \
  --confirmation "ZASTOSUJ DEDUPLIKACJE DPD"
```

Skrypt najpierw przejmuje tę samą blokadę PostgreSQL co harmonogram i ręczna synchronizacja InfoServices, następnie blokuje zmieniane rekordy, ponownie przelicza plan i odrzuca zapis, jeśli stan zmienił się od dry-run. Raport operacji trafia do ignorowanego katalogu `runtime/shipping_dpd_dedupe` i zawiera stan potrzebny do precyzyjnego rollbacku.

## Weryfikacja

1. Otwórz numer listu w zakładce „Status przesyłek” i potwierdź pojedyncze wystąpienie każdego logicznego zdarzenia.
2. Uruchom ponowny backfill wskazanego listu; liczba nowych rekordów technicznych i aliasów powinna wynieść zero.
3. Potwierdź, że bieżący status listu nie zmienił znaczenia.
4. Potwierdź, że `SHIPPING_DPD_FIREBIRD_MILESTONES_ENABLED=false` pozostało bez zmian.
5. Sprawdź `/health`, `/shipping`, `/shipping/legacy`, moduły SMS, e-mail, formularze i aktualność backupów.

## Rollback metadanych

Rollback wymaga raportu konkretnego przebiegu i nie usuwa zdarzeń:

```bash
python scripts/dpd_infoservices_dedupe.py \
  --rollback RUN_ID \
  --confirmation "WYCOFAJ DEDUPLIKACJE DPD RUN_ID"
```

Skrypt odmawia działania, jeśli po zastosowaniu deduplikacji którykolwiek z objętych rekordów został zmieniony. Kod aplikacji można niezależnie cofnąć do poprzedniego commita; addytywne kolumny mogą pozostać w bazie do czasu zaplanowanego downgrade.
