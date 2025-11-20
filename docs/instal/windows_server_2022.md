# CTIP – wdrożenie na Windows Server 2022 (D:\CTIP)

Dokument opisuje kompletną konfigurację środowiska produkcyjnego na serwerze Windows Server 2022. Maszyna utrzymuje zarówno bazę PostgreSQL (`192.168.0.8:5433`), jak i usługę `CollectorService` uruchamiającą `collector_full.py`. Wszystkie pliki aplikacji, skrypty i logi znajdują się w `D:\CTIP`.

## Wymagania wstępne
1. Zainstaluj Python 3.11 x64 (opcja „Add python.exe to PATH”).
2. Zainstaluj Git for Windows (potrzebny do `git pull`).
3. Utwórz katalog `D:\CTIP` i sklonuj repozytorium:
   ```powershell
   git clone https://github.com/Jarmix80/ctip.git D:\CTIP
   ```
4. W `D:\CTIP` przygotuj plik `.env` z parametrami centrali (`PBX_HOST=192.168.0.11`, `PBX_PORT=5524`, `PBX_PIN=1234`), bazy PostgreSQL na tym samym serwerze (`PGHOST=192.168.0.8`, `PGPORT=5433`) i modułów SMS. Sekrety trzymaj wyłącznie w `.env`.
5. Upewnij się, że zapora Windows zezwala na ruch TCP do centrali Slican (`192.168.0.11:5524`) i że `postgres.exe` akceptuje połączenia lokalne oraz z hostów WSL (np. `192.168.0.133`).

## Struktura katalogów
```
D:\CTIP
│  collector_full.py
│  collector_service.py
│  collector_service_config.json   ← generowany automatycznie
│  .env                            ← ręcznie utrzymywany
│
├─.venv                           ← środowisko wirtualne produkcji
├─logs
│  └─collector
│     ├─collector_stdout.log
│     └─collector_stderr.log
└─scripts
   └─windows
      ├─install_service.ps1
      ├─update_ctip.ps1
      └─uninstall_service.ps1
```
Logi rosną według dnia; rotację wykonuje zadanie logrotate Windows lub harmonogram (nie jest częścią niniejszego skryptu).

## Instalacja krok po kroku
1. Otwórz PowerShell jako Administrator i przejdź do `D:\CTIP`.
2. Jednorazowo zezwól na wykonywanie skryptów w bieżącej sesji:
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
   ```
3. Uruchom skrypt instalacyjny:
   ```powershell
   .\scripts\windows\install_service.ps1 -InstallDir "D:\CTIP" -PythonVersion "3.11"
   ```
   Skrypt:
   - tworzy `.venv` (Python 3.11) i instaluję zależności (`requirements.txt` wraz z `pywin32`),
   - uruchamia `pywin32_postinstall -install`, aby zarejestrować `servicemanager` wymagany do startu usługi,
   - generuje `collector_service_config.json` (ścieżki `.venv`, `collector_full.py` i logi `D:\CTIP\logs\collector`),
   - rejestruje usługę Windows `CollectorService` oraz od razu ją uruchamia.
   Uwaga: komunikaty w skryptach PowerShell są zapisane w ASCII (bez polskich znaków), aby Windows PowerShell 5.1 z domyślnym kodowaniem nie zgłaszał błędów parsowania.
   Uwaga operacyjna: jeżeli na hoście domyślny jest Python 3.13 (`py --list` pokazuje `* Python 3.13`), wymuszaj wersję 3.11 parametrem `-PythonVersion "3.11"`. Skrypt przed utworzeniem `.venv` sprawdza dostępność `py -3.11` i przerwie działanie z jasnym komunikatem, jeśli brak wymaganej wersji.
4. Sprawdź status usługi i ewentualne błędy:
   ```powershell
   Get-Service -Name CollectorService
   Get-Content -Tail 50 D:\CTIP\logs\collector\collector_stdout.log
   Get-Content -Tail 50 D:\CTIP\logs\collector\collector_stderr.log
   ```
5. Po pierwszym starcie zweryfikuj w logu komunikaty `aWHO` oraz `aLOGA`, aby potwierdzić handshake z centralą.

## Aktualizacje aplikacji
1. Zachowaj konwencję pracy tylko w katalogu `D:\CTIP` (wiązanie z repozytorium git).
2. Podczas wdrażania poprawek uruchom PowerShell jako Administrator i wykonaj:
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
   .\scripts\windows\update_ctip.ps1 -InstallDir "D:\CTIP" -GitRemote origin -GitBranch main
   ```
   Skrypt zatrzymuje usługę (jeżeli działa), pobiera zmiany `git fetch/pull`, aktualizuje zależności w `.venv`, a następnie ponownie startuje `CollectorService`. Przy błędzie aktualizacji zostaną zachowane logi i usługa nie zostanie ponownie uruchomiona dopóki administrator nie rozwiąże problemu.
3. Po udanej aktualizacji skontroluj logi kolektora i status tabeli `ctip.sms_out`.

## Odinstalowanie
Jeśli serwer wymaga reinstalacji lub migracji, użyj w zależności od potrzeb:

1. **Tylko usunięcie usługi** (pozostawia repozytorium i `.venv`):
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
   .\scripts\windows\uninstall_service.ps1 -InstallDir "D:\CTIP" -RemoveConfig
   ```
   Skrypt zatrzymuje i usuwa usługę oraz – opcjonalnie – kasuje `collector_service_config.json`.
2. **Pełne sprzątanie środowiska** (usuwa usługę, `.venv`, logi i – opcjonalnie – cały katalog `D:\CTIP`):
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
   .\scripts\windows\cleanup_ctip.ps1 -InstallDir "D:\CTIP" -RemoveRepo
   ```
   Bez przełącznika `-RemoveRepo` skrypt pozostawi repozytorium (kod źródłowy) i usunie jedynie artefakty uruchomieniowe.

## Pakiet do dystrybucji
Gotowe skrypty i instrukcję spakowano w `docs/instal/ctip_windows_service_package.zip`. Po wypakowaniu pakiet zawiera:
- `windows_server_2022.md` – niniejszy dokument,
- `scripts/windows/install_service.ps1`, `scripts/windows/update_ctip.ps1`, `scripts/windows/uninstall_service.ps1`, `scripts/windows/cleanup_ctip.ps1` – komplet narzędzi administracyjnych dla Windows Server 2022 (z zachowaniem struktury katalogu repozytorium).
Rozpakuj całość w katalogu docelowym (`D:\CTIP`), aby uzyskać gotowy podkatalog `scripts/windows` zgodny z klonem Git.
