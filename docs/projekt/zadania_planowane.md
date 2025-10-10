# Zadania planowane (stan na 2025-10-11)

1. Pełne uporządkowanie kodu pod kątem `ruff` (pliki w `app/`, `conect_sli.py`, `log_utils.py` i inne ostrzeżone B008/UP0xx/I001).
2. Implementacja rzeczywistego transportu SMS w `sms_sender.py` (API dostawcy + obsługa statusów/erroów).
3. Automatyzacja migracji Alembic w CI/CD (`alembic upgrade head` przed testami).
4. Monitoring działania migracji i kolektora (alerty na błędy alembic/SchemaValidationError).
5. Integracja prototypowego UI (`prototype/index.html`) z backendem FastAPI wraz z autoryzacją.
6. Przejrzenie nieśledzonych katalogów (app/, docs/, tests/ itd.), dodanie istotnych plików do repo i wykonanie `git push` po uporządkowaniu środowiska sieciowego.
