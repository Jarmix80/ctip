# Katalog roboczy Firebird

Katalog służy do przechowywania lokalnych kopii roboczych bazy Menadżer Serwisu używanych podczas prac developerskich.

## Zasady
1. Nie commitujemy plików `.fdb`, `.fbk` ani eksportów z danymi produkcyjnymi.
2. Domyślna ścieżka kopii roboczej jest wskazywana przez `FB_LOCAL_COPY_PATH` w `.env`.
3. Przed rozpoczęciem testów integracyjnych wykonaj test połączenia w panelu administratora (`Baza Firebird`).
