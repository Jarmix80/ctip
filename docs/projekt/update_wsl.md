🔧 Zmiany w konfiguracji WSL (Ubuntu 24.04 pod Windows 11)
1️⃣ Konfiguracja sieci

Utworzony plik konfiguracyjny:

%UserProfile%\.wslconfig


Zawartość:

[wsl2]
networkingMode=bridged
vmSwitch=WSLBridge


Cel: aby WSL miał adres IP z tej samej podsieci (192.168.0.x) co Windows.

2️ Utworzenie zewnętrznego vSwitcha (bridge)

W PowerShell (Administrator):

Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-Management-PowerShell -All
New-VMSwitch -Name "WSLBridge" -NetAdapterName "Ethernet" -AllowManagementOS $true

3️ Restart WSL
wsl --shutdown
wsl

4️ Weryfikacja w Ubuntu i Windows
PS C:\Users\Marcin> Get-NetAdapter

Name                      InterfaceDescription                    ifIndex Status       MacAddress             LinkSpeed
----                      --------------------                    ------- ------       ----------             ---------
vEthernet (Default Swi... Hyper-V Virtual Ethernet Adapter             20 Up           00-15-5D-2F-FD-ED        10 Gbps
Połączenie sieciowe Bl... Bluetooth Device (Personal Area Netw...      14 Not Present  8C-88-2B-61-CB-5B          0 bps
Ethernet 2                DrayTek Virtual PPP Adapter                  11 Disconnected 54-50-49-00-00-31          0 bps
Ethernet                  Intel(R) Ethernet Connection (7) I21...       8 Up           E4-54-E8-5F-2C-3A         1 Gbps
vEthernet (WSLBridge)     Hyper-V Virtual Ethernet Adapter #2          27 Up           E4-54-E8-5F-2C-3A         1 Gbps
ip -4 addr show
marcin@MarcinJKP:/mnt/c/Users/Marcin$ ip -4 addr show
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default qlen 1000
    inet 192.168.0.133/24 brd 192.168.0.255 scope global eth0
       valid_lft forever preferred_lft forever

✅ oczekiwany wynik: eth0 z adresem np. 192.168.0.133/24

5️ Alternatywa (gdy Hyper-V niedostępny)

Jeśli New-VMSwitch nie działa → w .wslconfig ustawiono:

[wsl2]
networkingMode=mirrored
dnsTunneling=true
autoProxy=true


(działa również bez Hyper-V, ale nie zawsze z osobnym IP)

6️ Sprawdzenie dostępu do PostgreSQL

Weryfikujemy, czy nowy adres WSL (`192.168.0.133`) ma dostęp do bazy na hoście `192.168.0.8:5433`:

```
timeout 5s nc -v 192.168.0.8 5433
# lub
psql "host=192.168.0.8 port=5433 dbname=ctip user=appuser"
```

Jeżeli połączenie kończy się komunikatem `connection timeout expired`, należy:
- dodać regułę zapory Windows dla procesu `postgres.exe` (port 5433/TCP) z zakresem źródłowym `192.168.0.0/24` lub konkretnie `192.168.0.133`,
- zaktualizować `pg_hba.conf`, aby dopuścić adres WSL, np. `host all appuser 192.168.0.0/24 md5`,
- zrestartować usługę PostgreSQL po zmianach w zaporze/konfiguracji.

Brak powyższych reguł objawia się zawieszeniem logowania administratora – backend FastAPI nie może utworzyć sesji w bazie.
