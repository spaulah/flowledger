#!/usr/bin/env bash
# Sets up a dev Entra app registration for the flow analysis tool and writes
# M365_CLIENT_ID, M365_TENANT_ID and M365_CLIENT_SECRET into .env.
#
# What it does:
#   1. Logs in to Azure CLI with a device code, only if you are not logged in.
#      The signed-in account must be a Power Platform administrator.
#   2. Creates the app registration (or reuses one with the same name).
#   3. Creates its service principal.
#   4. Requests the Power Automate (Flow Service) application permissions and
#      tries to grant admin consent.
#   5. Creates a new client secret.
#   6. Registers the app as a Power Platform management app (needed for the
#      admin environment list). Calls scripts/register_power_platform_app.sh.
#   7. Smoke-tests the credentials and writes the values into .env, keeping a
#      backup at .env.bak.
#
# Manual steps that remain (printed at the end): make the signed-in admin a
# System Administrator, then add the app as an application user in the
# environment. Power Platform does not expose these to the Azure CLI.
#
# Usage:
#   scripts/setup_dev_app_registration.sh [--tenant <tenant-guid>] [--app-name <name>] [--secret-years <n>] [--env-file <path>]

set -euo pipefail

# Power Automate "Flow Service" first-party app ID (same in every tenant).
FLOW_SERVICE_APP_ID="7df0a125-d3be-4c96-aa54-591f83ff541c"

APP_NAME="flow-analysis-dev"
SECRET_YEARS=1
TENANT=""
ENV_FILE=".env"

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant)        TENANT="$2"; shift 2 ;;
    --app-name)      APP_NAME="$2"; shift 2 ;;
    --secret-years)  SECRET_YEARS="$2"; shift 2 ;;
    --env-file)      ENV_FILE="$2"; shift 2 ;;
    -h|--help)       usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

log()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Run from the project root so .env resolves correctly.
cd "$(dirname "$0")/.."

command -v az >/dev/null 2>&1 || die "Azure CLI not found. Install it with: brew install azure-cli"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

# ---------------------------------------------------------------------------
# 1. Login (device code), only if needed
# ---------------------------------------------------------------------------
log "Checking Azure CLI login"
CURRENT_TENANT="$(az account get-access-token --resource-type ms-graph --query tenant -o tsv 2>/dev/null || true)"

NEED_LOGIN=false
if [[ -z "$CURRENT_TENANT" ]]; then
  NEED_LOGIN=true
  info "Not logged in."
elif [[ -n "$TENANT" && "$TENANT" =~ ^[0-9a-fA-F-]{36}$ && "$TENANT" != "$CURRENT_TENANT" ]]; then
  NEED_LOGIN=true
  info "Logged in to tenant $CURRENT_TENANT, but --tenant $TENANT was requested."
else
  info "Already logged in to tenant $CURRENT_TENANT."
fi

if [[ "$NEED_LOGIN" == true ]]; then
  log "Starting device-code login (follow the code shown below)"
  if [[ -n "$TENANT" ]]; then
    az login --use-device-code --allow-no-subscriptions --tenant "$TENANT"
  else
    az login --use-device-code --allow-no-subscriptions
  fi
  CURRENT_TENANT="$(az account get-access-token --resource-type ms-graph --query tenant -o tsv)"
fi

TENANT_ID="$CURRENT_TENANT"
[[ -n "$TENANT_ID" ]] || die "Could not determine the tenant after login."
info "Using tenant $TENANT_ID"

# ---------------------------------------------------------------------------
# 2. App registration (create or reuse)
# ---------------------------------------------------------------------------
log "App registration '$APP_NAME'"
APP_ID="$(az ad app list --display-name "$APP_NAME" --query "[?displayName=='$APP_NAME'] | [0].appId" -o tsv)"
if [[ -n "$APP_ID" && "$APP_ID" != "None" ]]; then
  info "Reusing existing app: $APP_ID"
else
  APP_ID="$(az ad app create --display-name "$APP_NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)"
  info "Created app: $APP_ID"
fi

# ---------------------------------------------------------------------------
# 3. Service principal
# ---------------------------------------------------------------------------
log "Service principal"
if az ad sp show --id "$APP_ID" >/dev/null 2>&1; then
  info "Already exists."
else
  az ad sp create --id "$APP_ID" >/dev/null
  info "Created. Waiting for it to propagate..."
  for _ in $(seq 1 10); do
    az ad sp show --id "$APP_ID" >/dev/null 2>&1 && break
    sleep 3
  done
fi

# ---------------------------------------------------------------------------
# 4. API permissions (Power Automate Flow Service, application roles)
# ---------------------------------------------------------------------------
log "Requesting Power Automate (Flow Service) application permissions"
# Make sure the Flow Service principal exists in this tenant so its roles can be read.
if ! az ad sp show --id "$FLOW_SERVICE_APP_ID" >/dev/null 2>&1; then
  az ad sp create --id "$FLOW_SERVICE_APP_ID" >/dev/null 2>&1 || info "Could not create the Flow Service principal here."
fi

ROLE_IDS="$(az ad sp show --id "$FLOW_SERVICE_APP_ID" \
  --query "appRoles[?isEnabled && contains(allowedMemberTypes, 'Application')].id" -o tsv 2>/dev/null || true)"

if [[ -z "$ROLE_IDS" ]]; then
  info "No application roles found on the Flow Service. Add permissions manually in the portal."
else
  while read -r ROLE_ID; do
    [[ -z "$ROLE_ID" ]] && continue
    az ad app permission add --id "$APP_ID" --api "$FLOW_SERVICE_APP_ID" \
      --api-permissions "${ROLE_ID}=Role" >/dev/null
  done <<< "$ROLE_IDS"
  info "Added $(echo "$ROLE_IDS" | wc -l | tr -d ' ') application role(s). Review them in the portal under API permissions."

  log "Granting admin consent"
  if az ad app permission admin-consent --id "$APP_ID" >/dev/null 2>&1; then
    info "Admin consent granted."
  else
    info "Could not grant admin consent (requires a Global or Privileged Role Administrator)."
    info "Ask an admin to consent in the portal: App registrations > $APP_NAME > API permissions."
  fi
fi

# ---------------------------------------------------------------------------
# 5. Client secret
# ---------------------------------------------------------------------------
log "Creating a client secret (valid $SECRET_YEARS year(s))"
info "Note: this resets all existing secrets on the app. Re-run this script to rotate."
APP_SECRET="$(az ad app credential reset --id "$APP_ID" --display-name "flow-analysis-cli" \
  --years "$SECRET_YEARS" --query password -o tsv)"
[[ -n "$APP_SECRET" ]] || die "Secret creation returned nothing."

# ---------------------------------------------------------------------------
# 6. Register as a Power Platform management app (admin environment list)
# ---------------------------------------------------------------------------
log "Registering as a Power Platform management app"
if ! "$(dirname "$0")/register_power_platform_app.sh" "$APP_ID"; then
  info "Registration failed. The smoke test below will show the BAP call as 403."
fi

# ---------------------------------------------------------------------------
# 7. Smoke test and write .env (secret passed via environment, not argv)
# ---------------------------------------------------------------------------
log "Smoke-testing credentials and updating $ENV_FILE"
APP_ID="$APP_ID" TENANT_ID="$TENANT_ID" APP_SECRET="$APP_SECRET" ENV_FILE="$ENV_FILE" python3 - <<'PY'
import json, os, re, shutil, sys, urllib.error, urllib.parse, urllib.request

app_id = os.environ["APP_ID"]
tenant = os.environ["TENANT_ID"]
secret = os.environ["APP_SECRET"]
env_file = os.environ["ENV_FILE"]

def get_token(scope):
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": secret,
        "scope": scope,
    }).encode()
    req = urllib.request.Request(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"], None
    except urllib.error.HTTPError as e:
        return None, e.read().decode(errors="replace")[:200]

def smoke(label, scope, url):
    token, err = get_token(scope)
    if not token:
        print(f"    {label}: token NOT issued ({err})")
        return
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            count = len(json.load(r).get("value", []))
            print(f"    {label}: OK ({count} environment(s) visible)")
    except urllib.error.HTTPError as e:
        print(f"    {label}: token OK, call failed with HTTP {e.code} (check the app's permissions and environment access)")

smoke("Power Platform admin API (BAP)", "https://api.bap.microsoft.com/.default",
      "https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/scopes/admin/environments?api-version=2023-06-01")
smoke("Flow service", "https://service.flow.microsoft.com/.default",
      "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments?api-version=2016-11-01")

# Update .env: replace the three keys in place, append any that are missing.
updates = {"M365_CLIENT_ID": app_id, "M365_TENANT_ID": tenant, "M365_CLIENT_SECRET": secret}
lines = open(env_file, encoding="utf-8").read().splitlines() if os.path.exists(env_file) else []
if os.path.exists(env_file):
    shutil.copy2(env_file, env_file + ".bak")
    os.chmod(env_file + ".bak", 0o600)
    print(f"    Backed up previous {env_file} to {env_file}.bak")

seen = set()
out = []
for line in lines:
    m = re.match(r"^([A-Z0-9_]+)=", line)
    if m and m.group(1) in updates:
        key = m.group(1)
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")

fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print(f"    Wrote M365_CLIENT_ID, M365_TENANT_ID and M365_CLIENT_SECRET to {env_file} (permissions 600).")
PY

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log "Done"
info "M365_CLIENT_ID: $APP_ID"
info "M365_TENANT_ID: $TENANT_ID"
info "Secret:     written to $ENV_FILE only (not printed)"
echo
echo "Remaining manual steps (Power Platform, one time per environment):"
echo "  1. Make sure the admin account you use can add app users. In the environment:"
echo "     Settings > Users + permissions > Users > your user > Manage roles > System Administrator."
echo "     Without it, adding the app user fails with 'missing prvReadApplicationUser privilege'."
echo "  2. Open Settings > Users + permissions > Application users > + New app user."
echo "     Select the app '$APP_NAME' (client ID $APP_ID) and assign the System Administrator role."
echo "  3. Run jobs/download_flows.py. If the smoke test above showed a BAP 403, the registration"
echo "     in step 6 did not succeed. Check that the signed-in account is a Power Platform administrator."
