# Lokalna baza wiedzy Firebird MS (CTIP)

Ten katalog przechowuje trwały indeks wiedzy o bazie Firebird Menadżera Serwisu, aby:
- chat CTIP nie musiał za każdym razem analizować struktury od zera,
- skrypty i moduły repo mogły korzystać z tego samego źródła wiedzy,
- ograniczyć zużycie tokenów i czas odpowiedzi.

## Zawartość
- `firebird_ms_knowledge.json`:
  - metadane tabel i kolumn (`docs/structure/*.md` z bazams),
  - dokumenty opisowe (`docs/*.md`),
  - analizy reverse (`reverse/analysis/*.md`),
  - znacznik czasu oraz commit źródłowego repozytorium `bazams`.

## Regeneracja indeksu
1. Upewnij się, że źródło `bazams` jest dostępne w repo:
   - preferowana ścieżka: `integrations/bazams`
   - fallback: `docs/firebird/external/bazams`
2. Uruchom:

```bash
source .venv/bin/activate
python scripts/build_firebird_knowledge_index.py
```

Opcjonalnie:

```bash
python scripts/build_firebird_knowledge_index.py --source /sciezka/do/bazams --output /tmp/knowledge.json
```

## Użycie w asystencie
Narzędzie `firebird_knowledge_read` czyta ten plik lokalnie i zwraca:
- katalog tabel,
- szczegóły tabeli (kolumny, PK, opis),
- wyniki wyszukiwania po temacie (tabele + dokumenty).
