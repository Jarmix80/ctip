# Publiczne formularze produkcyjne (`form.ksero-partner.com.pl`)

Dokument opisuje bezpieczny wariant wystawienia jednorazowych formularzy klienta poza LAN bez publikowania panelu administratora i widoku `/genform`.

## Cel
- `form.ksero-partner.com.pl` ma udostępniać wyłącznie:
  - `/`
  - `/health`
  - `/formularz/{token}`
- panel operatora i administratora (`/`, `/admin`, `/genform`, `/flow`, `/contracts`, `/device`) pozostaje dostępny tylko wewnątrz LAN lub przez oddzielny kanał administracyjny.

## Rekomendowana architektura
1. Produkcyjny panel CTIP działa dalej jako pełna aplikacja `app.main:app` na porcie LAN, np. `http://192.168.0.8:8000`.
2. Publiczne formularze działają jako osobna aplikacja `app.public_forms_app:app` na osobnym porcie wewnętrznym, np. `127.0.0.1:8100`.
3. Reverse proxy na serwerze Windows lub Linux odbiera ruch dla `form.ksero-partner.com.pl` na `80/443` i przekazuje go na `127.0.0.1:8100`.
4. Router/NAT publikuje na zewnątrz tylko `80/443` do reverse proxy.
5. Portu `8000` ani `8100` nie wystawiamy bezpośrednio do Internetu.

## Wymagane ustawienia `.env`
W produkcyjnym `.env` ustaw:

```dotenv
ADMIN_PANEL_URL=http://192.168.0.8:8000/admin
FORM_PUBLIC_BASE_URL=https://form.ksero-partner.com.pl
```

Uwagi:
- `ADMIN_PANEL_URL` może wskazywać adres wewnętrzny panelu.
- `FORM_PUBLIC_BASE_URL` musi wskazywać publiczną subdomenę z `https://`.
- `genform` pozostaje w pełnej aplikacji panelowej i nie jest publikowane na subdomenie formularzy.

## DNS w home.pl
W strefie DNS domeny dodaj rekord:

```text
Typ:   A
Host:  form
Wartość: <publiczny_adres_IP_routera_lub_firewalla>
TTL:   domyślne
```

Jeżeli publiczny adres IP zmienia się dynamicznie, najpierw trzeba ustabilizować dostęp do stałego adresu publicznego albo wdrożyć zewnętrzny mechanizm dynamicznego DNS. Sam rekord `A` w home.pl musi zawsze wskazywać aktualny adres publiczny.

## Router / NAT
Na routerze dodaj przekierowania:

```text
TCP 80   -> <IP_serwera_reverse_proxy>:80
TCP 443  -> <IP_serwera_reverse_proxy>:443
```

Nie publikuj:
- `8000/tcp` – pełny panel CTIP,
- `8100/tcp` – wewnętrzny port aplikacji publicznych formularzy.

## Aplikacja publiczna
Repozytorium zawiera osobny entrypoint:

```text
app.public_forms_app:app
```

Ta aplikacja nie ładuje tras panelowych. Udostępnia tylko publiczne formularze i `healthcheck`.

### Start w środowisku Linux / WSL
Do uruchomienia osobnego procesu służy:

```bash
ALLOW_PRODUCTION_START=true ./run_public_forms_tmux.sh
```

Domyślne parametry:
- host: `127.0.0.1`
- port: `8100`
- wymagane `FORM_PUBLIC_BASE_URL=https://...`

## Windows Server 2022 + NSSM
Skrypt:

```powershell
.\scripts\windows\install_web_sms_nssm.ps1 `
  -InstallDir "D:\CTIP" `
  -ServicePrefix "CTIP" `
  -UvicornPort 8000 `
  -InstallPublicForms `
  -PublicFormsHost "127.0.0.1" `
  -PublicFormsPort 8100 `
  -NssmPath "C:\Program Files\nssm\nssm.exe"
```

Po wykonaniu powstaną usługi:
- `CTIP-Web` – pełna aplikacja panelowa `app.main:app`
- `CTIP-SMS` – kolejka SMS
- `CTIP-FormsPublic` – publiczna aplikacja `app.public_forms_app:app`

## Reverse proxy
Najprostszy wariant to reverse proxy z automatycznym TLS na tym samym serwerze, który łączy ruch z `form.ksero-partner.com.pl` do `127.0.0.1:8100`.

Przykład logiki proxy:

```text
host publiczny: form.ksero-partner.com.pl
listen: 80, 443
upstream: 127.0.0.1:8100
```

Ważne:
- certyfikat TLS ma obejmować `form.ksero-partner.com.pl`,
- wymuszaj przekierowanie `http -> https`,
- nie mieszaj publicznego hosta formularzy z hostem panelu.

## Checklista odbioru
1. `nslookup form.ksero-partner.com.pl` zwraca publiczny adres IP.
2. Z Internetu `https://form.ksero-partner.com.pl/health` zwraca `200`.
3. `https://form.ksero-partner.com.pl/` pokazuje stronę informacyjną, nie login panelu.
4. `https://form.ksero-partner.com.pl/formularz/<token>` otwiera publiczny formularz.
5. `https://form.ksero-partner.com.pl/admin` nie udostępnia panelu.
6. Wygenerowany link z `/genform` wskazuje dokładnie `https://form.ksero-partner.com.pl/formularz/<token>`.

## Decyzja portowa
Rekomendacja:
- osobny port wewnętrzny dla formularzy publicznych: tak,
- osobny port publiczny wystawiony klientowi: nie.

Na zewnątrz klient powinien widzieć wyłącznie standardowy `443` pod subdomeną `form.ksero-partner.com.pl`. Rozdział robimy wewnętrznie przez osobny proces i reverse proxy, nie przez publikację niestandardowego portu.
