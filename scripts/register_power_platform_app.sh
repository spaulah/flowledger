#!/usr/bin/env bash
# Registers the flow analysis app as a Power Platform management app, so the
# service principal can call the Power Platform admin API (api.bap.microsoft.com).
#
# This is the same call that New-PowerAppManagementApp makes (PUT to
# .../adminApplications/{appId}), made with an Azure CLI token. It works on
# macOS, where the PowerShell module's sign-in dialog does not.
#
# Needs an Azure CLI login as a Power Platform administrator in the tenant.
# If you are not logged in, the script starts a device-code login.
#
# Usage (from the project root):
#   scripts/register_power_platform_app.sh              # reads M365_CLIENT_ID from .env
#   scripts/register_power_platform_app.sh <client-id>  # uses the ID given

set -euo pipefail

ENV_FILE=".env"
API_VERSION="2020-06-01"
BAP_URL="https://api.bap.microsoft.com"

cd "$(dirname "$0")/.."
command -v az >/dev/null 2>&1 || { echo "Azure CLI not found. Install: brew install azure-cli" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

CLIENT_ID="${1:-}"
if [[ -z "$CLIENT_ID" ]]; then
  # Read M365_CLIENT_ID from .env without printing anything else.
  CLIENT_ID="$(python3 - "$ENV_FILE" <<'PY'
import re, sys
for line in open(sys.argv[1], encoding="utf-8"):
    m = re.match(r"^\s*M365_CLIENT_ID=(.*)$", line)
    if m:
        print(m.group(1).strip().strip('"'))
        break
PY
)"
fi
[[ -n "$CLIENT_ID" ]] || { echo "No client ID: pass one as an argument or set M365_CLIENT_ID in $ENV_FILE" >&2; exit 1; }

# Get a token for the Power Platform admin API as the signed-in admin.
TOKEN="$(az account get-access-token --resource "$BAP_URL/" --query accessToken -o tsv 2>/dev/null || true)"
if [[ -z "$TOKEN" ]]; then
  echo "Starting device-code login. Sign in with a Power Platform administrator account."
  az login --use-device-code --allow-no-subscriptions
  TOKEN="$(az account get-access-token --resource "$BAP_URL/" --query accessToken -o tsv)"
fi

echo "Registering app $CLIENT_ID as a Power Platform management app..."
# A private temporary file, removed on exit. A fixed name in /tmp could be read or replaced by another user.
BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT
HTTP_CODE="$(curl -s -o "$BODY_FILE" -w '%{http_code}' -X PUT \
  "$BAP_URL/providers/Microsoft.BusinessAppPlatform/adminApplications/$CLIENT_ID?api-version=$API_VERSION" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}')"
BODY="$(cat "$BODY_FILE")"

if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "201" ]]; then
  echo "Registered. Response: $BODY"
  echo "Next: run jobs/download_flows.py to confirm the environment list works."
else
  echo "Registration failed with HTTP $HTTP_CODE: $BODY" >&2
  echo "A 403 usually means the signed-in account is not a Power Platform administrator." >&2
  exit 1
fi
