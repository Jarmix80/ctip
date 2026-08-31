# Audyt repozytorium i sekretów z 2026-09-01

## Ocena ryzyka

Repozytorium GitHub jest prywatne, a dostęp posiadał wyłącznie właściciel. Znacząco ogranicza to prawdopodobieństwo przejęcia danych przez osoby trzecie, ale nie usuwa ryzyka wynikającego z historii Git, lokalnych kopii, backupów, logów, cache narzędzi i ewentualnego przejęcia konta GitHub lub komputera.

W bieżącym drzewie usunięto aktywne wartości kontrolowanych sekretów i dodano skaner uruchamiany przez pre-commit oraz CI. Skaner nie wypisuje wykrytych wartości. Historia Git nie została przepisana, ponieważ taka operacja zmieniłaby wszystkie hashe i wymagałaby skoordynowanego force-push.

## Zabezpieczenia bieżącego drzewa

- `.env`, `.env.*`, `.runtime-secrets/`, `.runtime-firebird/`, `runtime/`, `inbox/`, backupy i logi są ignorowane przez Git.
- Pliki sekretów testowych mają uprawnienia `0600`, a katalog `0700`.
- Kod nie zawiera domyślnych haseł Firebird ani aktywnych danych SMS, e-mail i PostgreSQL.
- `scripts/secret_scan.py` kontroluje staged files, całe drzewo i CI bez ujawniania wartości.
- Obrazy Docker nie zawierają `.env`, sekretów, baz, logów ani katalogu `inbox`.

## Plan rotacji

Rotację należy przeprowadzić w osobnym oknie utrzymaniowym, kolejno dla:

1. hasła Firebird, jeżeli stara wartość była kiedykolwiek użyta produkcyjnie;
2. tokenu i hasła bramki SMS;
3. hasła skrzynki e-mail i skrzynki umów;
4. haseł PostgreSQL;
5. kluczy API DPD i OpenAI;
6. `ADMIN_SECRET_KEY` wyłącznie z zaplanowaną procedurą ponownego szyfrowania danych.

Po każdej rotacji należy zaktualizować tylko bezpieczny magazyn konfiguracji, zrestartować wyłącznie zależne usługi i wykonać kontrolę healthchecków. Nie wolno zapisywać nowych wartości w zgłoszeniu, commicie ani raporcie.

## Historia Git i kopie lokalne

Przepisywanie historii jest opcją ostateczną. Przy prywatnym repozytorium z jednym użytkownikiem praktycznym zabezpieczeniem jest najpierw rotacja wszystkich potencjalnie ujawnionych danych. Dopiero potem można rozważyć `git filter-repo`, unieważnienie wszystkich starych klonów i kontrolowany force-push.

Stare katalogi robocze, kopie `.env.bad*`, obrazy i backupy należy usuwać dopiero po ręcznej identyfikacji, potwierdzeniu przydatności rollbacku i osobnej zgodzie właściciela. Do tego czasu powinny mieć ograniczone uprawnienia i pozostawać poza katalogami udostępnianymi przez SMB lub HTTP.

## Kontrola cykliczna

```bash
source .venv/bin/activate
python scripts/secret_scan.py --all-files
pre-commit run --all-files
git status --short
```

Co najmniej raz na kwartał należy także przejrzeć listę użytkowników i kluczy GitHub, aktywne tokeny integracji, uprawnienia plików runtime oraz listę obrazów i kontenerów na serwerach testowym i produkcyjnym.
