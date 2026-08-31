# Testowy runtime CRM i LAB w Dockerze

## Cel

Procedura uruchamia `ctip-crm-prototype` i `ctip-lab-portal` na serwerze
testowym `192.168.0.9`. Usługi korzystają z niemutowalnego obrazu zawierającego
pakiet `app`, dlatego zmiana gałęzi w roboczym repozytorium nie usuwa modułów z
działających kontenerów.

Procedura nie jest przeznaczona dla produkcji. Nie wolno jej uruchamiać z bazą
PostgreSQL inną niż `ctip_test`, z produkcyjną centralą ani z Firebird
dopuszczającym zapis.

## Zabezpieczenia

- obraz działa jako użytkownik `10001:10001` i ma system plików tylko do odczytu;
- kontenery nie montują katalogu `app` ani całego repozytorium;
- sekrety pozostają w ignorowanym pliku `.env.crm-lab.runtime`;
- preflight wymaga `CRM_ENABLED=true`, `CRM_LAB_MODE=true` i
  `CRM_PUBLIC_PROTOTYPE_MODE=true`;
- preflight wymaga `PGDATABASE=ctip_test`, lokalnego Firebird testowego,
  `FB_ALLOW_WRITES=false` i `SMS_TEST_MODE=true`;
- `BLOCK_CLIENT_COMMUNICATIONS=true` blokuje wysyłkę wiadomości do klientów;
- ustawienia Compose czyszczą dane operatora SMS i kierują pocztę do Mailpit;
- porty są publikowane wyłącznie na adresie testowym `192.168.0.9`.

## Budowa i kontrola obrazu

W izolowanym worktree wykonaj:

```bash
source .venv/bin/activate
REVISION="$(git rev-parse --short=12 HEAD)"
IMAGE="ctip/crm-lab-test:${REVISION}"
./scripts/docker_build.sh --build-arg VCS_REF="$(git rev-parse HEAD)" \
  --tag "$IMAGE" --file Dockerfile.crm-lab .
docker run --rm "$IMAGE" python -c \
  'from app.crm_prototype_app import app as crm; from app.lab_portal_app import app as lab; assert crm and lab'
```

Plik `.env.crm-lab.runtime` należy utworzyć poza Git na podstawie testowej
konfiguracji serwera. Nie należy wyświetlać jego zawartości w logach.

## Uruchomienie

```bash
export CRM_LAB_IMAGE="ctip/crm-lab-test:<commit>"
export CRM_LAB_ENV_FILE="$PWD/.env.crm-lab.runtime"
./crm-lab-test config >/dev/null
./crm-lab-test up -d --no-build --force-recreate crm lab
./crm-lab-test ps
```

Compose używa istniejącej sieci brzegowej `ctip-prod-mirror_ctip_test_edge` do
publikacji portów oraz sieci wewnętrznych `ctip-prod-mirror_ctip_test_internal`
i `chat_kp_chat_kp`. Brama LAB łączy się z CHAT_KP przez adres
`http://chat_kp-public-1:8787`.

## Weryfikacja

```bash
curl --fail --silent http://192.168.0.9:8001/health
curl --fail --silent http://192.168.0.9:8790/health
curl --fail --silent http://192.168.0.9:8001/crm >/dev/null
curl --fail --silent http://192.168.0.9:8790/forms >/dev/null
docker inspect ctip-crm-prototype ctip-lab-portal \
  --format '{{.Name}} {{.State.Status}} {{.State.Health.Status}}'
```

Oba endpointy `/health` muszą zwrócić `status=ok` i `safe_lab=true`. Jeżeli
skonfigurowano sekret iframe, zwykłe wejście na chronione trasy LAB bez biletu
zwróci HTTP 403; nie jest to błąd healthchecku.

## Rollback

Przed podmianą należy zachować wynik `docker inspect` oraz poprzedni tag obrazu.
Rollback polega na zatrzymaniu nowych usług i ponownym uruchomieniu poprzednich
kontenerów albo poprzedniego, zweryfikowanego tagu:

```bash
export CRM_LAB_IMAGE="ctip/crm-lab-test:<poprzedni-commit>"
./crm-lab-test up -d --no-build --force-recreate crm lab
```

Rollback nie usuwa wolumenów, baz ani sieci. Nie należy wykonywać szerokiego
`docker compose down`.
