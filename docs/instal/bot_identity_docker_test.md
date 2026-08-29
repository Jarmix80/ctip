# Wdrożenie katalogu tożsamości Bot Identity w środowisku testowym

## Cel i ograniczenia

Procedura uruchamia wyłącznie kontenery `ctip-bot-identity-api` oraz
`ctip-bot-identity-sync` na hoście testowym. Kod aplikacji znajduje się w obrazie
Dockera i nie jest montowany z roboczego repozytorium. Zmiana gałęzi w innym
worktree nie może przez to wyłączyć usług.

Wdrożenie korzysta wyłącznie z PostgreSQL `ctip_test` i Firebirda
`/data/BAZAMS_TEST.FDB`. Wymagane są `FB_ALLOW_WRITES=false`,
`SMS_TEST_MODE=true` i `CRM_LAB_MODE=true`. Procedura nie uruchamia migracji,
nie łączy się z `192.168.0.8` ani `192.168.0.11` i nie restartuje pozostałych
usług CTIP.

## Przygotowanie artefaktu

1. Pracuj w izolowanym worktree utworzonym z zatwierdzonego źródła katalogu.
2. Skopiuj `deploy/bot-identity.runtime.example` do ignorowanego przez Git pliku
   `.env.bot-identity.runtime` i uzupełnij sekrety. Nie wyświetlaj pliku w logach.
3. Każda zmienna może wystąpić tylko raz. Przed wdrożeniem sprawdź efektywne
   wartości `PGDATABASE=ctip_test`, `FB_ALLOW_WRITES=false`,
   `SMS_TEST_MODE=true` oraz `CRM_LAB_MODE=true` wewnątrz kontenera.
4. Zbuduj obraz dopiero z czystego, zatwierdzonego commita:

   ```bash
   REVISION="$(git rev-parse HEAD)"
   IMAGE="ctip/bot-identity:${REVISION:0:12}"
   BUILDX="${BUILDX:-/usr/libexec/docker/cli-plugins/docker-buildx}"
   "${BUILDX}" build \
     --load \
     --file Dockerfile.bot-identity \
     --build-arg "VCS_REF=${REVISION}" \
     --build-arg "SOURCE_REF=e69cc72996f432593e39b3c5752c8d36c43cf59c" \
     --tag "${IMAGE}" .
   docker image inspect "${IMAGE}" --format '{{.Id}} {{json .Config.Labels}}'
   ```

## Kontrola przed podmianą

1. Sprawdź importy bez pliku środowiskowego:

   ```bash
   docker run --rm --entrypoint python "${IMAGE}" -c \
     'import app.bot_identity_api_app; import app.bot_identity_worker'
   ```

2. Uruchom canary API pod nazwą tymczasową, bez aliasu `ctip-bot-api`, w obu
   sieciach testowych. Sprawdź `/health` oraz uwierzytelnione
   `/v1/capabilities`. Odpowiedź musi zawierać `service=ctip`,
   `contract_version=1.0`, komplet pięciu kategorii i wszystkie flagi `true`;
   adapter CHAT_KP interpretuje ten zestaw jako `ctip-v1`.
3. W PostgreSQL wykonaj wyłącznie zapytania do `information_schema`. Wymagane są
   tabele `bot_identity_*`, kolumny `bot_identity_device.device_ref`,
   `bot_identity_device.image_url`, `bot_identity_device.model`,
   `bot_identity_device.serial_last4` oraz `crm_case.device_refs`. Brak elementu
   zatrzymuje wdrożenie; nie wolno wykonywać `alembic upgrade`.
4. Uruchom canary workera z limitem czasu i potwierdź zakończony wpis
   `bot_identity_sync_run`. Firebird musi być otwierany w transakcji tylko do
   odczytu, a kontener musi raportować `FB_ALLOW_WRITES=false`.

## Podmiana i odbiór

1. Zapisz `docker inspect`, identyfikatory obrazów, politykę restartu oraz sieci
   obu starych kontenerów. Nie zapisuj ich zmiennych środowiskowych w raporcie.
2. Zatrzymaj i zastąp wyłącznie `ctip-bot-identity-api` oraz
   `ctip-bot-identity-sync`, używając `compose.bot-identity.yml` i dokładnego
   digestu zweryfikowanego obrazu. Nie montuj katalogu `app`. Na hoście, na
   którym plugin nie jest wykrywany jako `docker compose`, wywołaj bezpośrednio
   `/usr/libexec/docker/cli-plugins/docker-compose`.
3. Potwierdź stabilny stan `running`, HTTP 200 na `/health`, zgodność
   `/v1/capabilities`, rozwiązywanie aliasu `ctip-bot-api` w sieci
   `chat_kp_chat_kp` oraz dostęp API z tej sieci.
4. Dopiero po tych kontrolach zrestartuj `chat_kp-public-1` i
   `chat_kp-outbox-1`. Nie restartuj `chat_kp-dashboard-1`.
5. Sprawdź HTTP 200 na `http://192.168.0.9:8787/health/live` i
   `http://192.168.0.9:8787/health/ready`, stabilne liczniki restartów oraz brak
   błędów preflight CHAT_KP.

## Rollback

Pierwotny obraz `ctip/prod-mirror:test` nie jest funkcjonalnym punktem powrotu,
ponieważ nie zawiera modułów Bot Identity. Jeżeli canary nie przejdzie testów,
nie wykonuj podmiany. Jeżeli błąd wystąpi po podmianie, zatrzymaj wyłącznie dwie
usługi Bot Identity, odtwórz zapisane konfiguracje poprzednich kontenerów i
pozostaw CHAT_KP zatrzymany do czasu diagnozy. Pierwszy obraz, który przejdzie
pełny odbiór, staje się właściwym punktem rollbacku dla kolejnych wdrożeń.
