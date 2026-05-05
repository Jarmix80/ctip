# Runbook awaryjny CTIP na Windows Server (`D:\CTIP`)

Dokument opisuje szybka diagnostyke i naprawe po typowych awariach:
- niedzialajacy `CTIP-Web` (`:8000`, panel i `/assistant`),
- blad `500` na formularzach publicznych (`CTIP-FormsPublic`, `:8100`),
- rozjazd wersji po aktualizacji.

## 1. Szybki check (60 sekund)

```powershell
git branch --show-current
git rev-parse --short HEAD

Get-Service CollectorService,CTIP-Web,CTIP-SMS,CTIP-FormsPublic

Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/assistant -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8100/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8100/formularz/abc -UseBasicParsing
```

Interpretacja:
- `:8000/health` musi zwrocic `200`.
- `:8000/assistant` musi zwrocic `200`.
- `:8100/health` musi zwrocic `200`.
- `:8100/formularz/abc` moze zwrocic `404` (PowerShell pokazuje `WebException`), ale tresc strony ma byc "Link formularza jest nieaktywny" i to jest poprawny stan.

## 2. CTIP-Web nie odpowiada na `:8000`

### 2.1. Objaw
- `Invoke-WebRequest http://127.0.0.1:8000/health` zwraca `Unable to connect to the remote server`.
- W `web_stderr.log` widac:
  `Psycopg cannot use the 'ProactorEventLoop' ... WindowsSelectorEventLoopPolicy()`.

### 2.2. Naprawa docelowa
`CTIP-Web` ma startowac przez wrapper:
`D:\CTIP\scripts\windows\run_ctip_web.py`

Sprawdz i ustaw:

```powershell
$svc = "HKLM:\SYSTEM\CurrentControlSet\Services\CTIP-Web\Parameters"
Get-ItemProperty $svc | Select-Object Application,AppDirectory,AppParameters,AppStdout,AppStderr
Set-ItemProperty -Path $svc -Name AppParameters -Value "D:\CTIP\scripts\windows\run_ctip_web.py"
Restart-Service CTIP-Web -Force
Start-Sleep -Seconds 5
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/assistant -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/choice -UseBasicParsing
```

### 2.3. Gdy brakuje wrappera

```powershell
$code = @'
import asyncio
import uvicorn

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1)
'@

Set-Content -Path D:\CTIP\scripts\windows\run_ctip_web.py -Value $code -Encoding UTF8
```

## 3. Formularze publiczne zwracaja `500`

Uruchom skrypt naprawczy:

```powershell
cd D:\CTIP
.\scripts\windows\fix_forms_public_500.ps1 -Apply
```

Skrypt:
- ustawia komplet `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD/PGSSLMODE` w `AppEnvironmentExtra`,
- wymusza `PGHOST=127.0.0.1` dla `CTIP-FormsPublic`,
- restartuje usluge i testuje endpointy.

## 4. Standardowa aktualizacja po merge do `main`

```powershell
cd D:\CTIP
git fetch origin
git checkout main
git pull --ff-only origin main
.\.venv\Scripts\pip.exe install -e .
Restart-Service CTIP-Web,CTIP-FormsPublic,CTIP-SMS,CollectorService -Force
```

## 5. Minimalna diagnostyka logow

```powershell
Get-Content -Tail 120 D:\CTIP\logs\web\web_stderr.log
Get-Content -Tail 120 D:\CTIP\logs\forms_public\forms_stderr.log
```

## 6. Zasady operacyjne (zeby nie powtorzyc awarii)

- Nie uruchamiaj opisow tekstowych jako komend PowerShell (np. "Jesli dalej...").
- Po kazdym `git pull` wykonaj check z punktu 1.
- Dla `CTIP-Web` utrzymuj `AppParameters = D:\CTIP\scripts\windows\run_ctip_web.py`.
- Dla `CTIP-FormsPublic` utrzymuj komplet `PG*` w `AppEnvironmentExtra`.
- Trzymaj produkcje na `main` po merge, nie na tymczasowej galezi.
