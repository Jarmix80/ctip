#!/usr/bin/env bash
set -euo pipefail

: "${TENANT_ID:?Brak TENANT_ID}"
: "${CLIENT_ID:?Brak CLIENT_ID}"
: "${CLIENT_SECRET:?Brak CLIENT_SECRET}"
: "${SITE_URL:?Brak SITE_URL}"

if ! command -v jq >/dev/null 2>&1; then
  echo "[ERR] Brak jq. Zainstaluj: sudo apt-get install -y jq" >&2
  exit 1
fi

SITE_HOST="$(echo "$SITE_URL" | awk -F/ '{print $3}')"
TOKEN_URL="https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token"
SCOPE="https://${SITE_HOST}/.default"

echo "[INFO] Pobieranie tokenu OAuth..."
TOKEN_RESP="$(curl -sS -X POST "$TOKEN_URL" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "client_id=${CLIENT_ID}" \
  --data-urlencode "client_secret=${CLIENT_SECRET}" \
  --data-urlencode "scope=${SCOPE}" \
  --data-urlencode "grant_type=client_credentials")"

ACCESS_TOKEN="$(echo "$TOKEN_RESP" | jq -r '.access_token // empty')"
if [[ -z "$ACCESS_TOKEN" ]]; then
  echo "[ERR] Brak access_token. Odpowiedz:" >&2
  echo "$TOKEN_RESP" | jq . >&2 || echo "$TOKEN_RESP" >&2
  exit 2
fi

echo "[INFO] Dekodowanie payload JWT..."
PAYLOAD_B64="$(echo "$ACCESS_TOKEN" | cut -d'.' -f2)"
PAYLOAD_B64="${PAYLOAD_B64//-/+}"
PAYLOAD_B64="${PAYLOAD_B64//_/\/}"
PAD=$(( (4 - ${#PAYLOAD_B64} % 4) % 4 ))
PAYLOAD_B64="${PAYLOAD_B64}$(printf '=%.0s' $(seq 1 $PAD))"
PAYLOAD_JSON="$(echo "$PAYLOAD_B64" | base64 -d 2>/dev/null || true)"

if [[ -z "$PAYLOAD_JSON" ]]; then
  echo "[ERR] Nie udalo sie zdekodowac payload JWT." >&2
  exit 3
fi

echo "aud   : $(echo "$PAYLOAD_JSON" | jq -r '.aud // empty')"
echo "appid : $(echo "$PAYLOAD_JSON" | jq -r '.appid // empty')"
echo "roles : $(echo "$PAYLOAD_JSON" | jq -r '(.roles // []) | join(", ")')"

echo "[INFO] Test dostepu do SharePoint REST..."
HTTP_CODE="$(curl -sS -o /tmp/sp_api_test.json -w "%{http_code}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Accept: application/json;odata=nometadata" \
  "${SITE_URL}/_api/web?\$select=Title,Url")"

echo "[INFO] HTTP: ${HTTP_CODE}"
cat /tmp/sp_api_test.json | jq . 2>/dev/null || cat /tmp/sp_api_test.json
