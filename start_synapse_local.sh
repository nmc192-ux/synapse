#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env.local.dev"
UI_ENV_FILE="${ROOT_DIR}/ui/.env.local"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
PIP_BIN="${ROOT_DIR}/.venv/bin/pip"
PLAYWRIGHT_BIN="${ROOT_DIR}/.venv/bin/playwright"
UI_PORT="${SYNAPSE_UI_PORT:-3001}"
API_BASE_URL="http://127.0.0.1:8000"
WS_BASE_URL="ws://127.0.0.1:8000/api/ws"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy config/examples/local-dev.env.example first." >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

LOG_DIR="${SYNAPSE_LOCAL_LOG_DIR:-${HOME}/synapse-logs}"
RUNTIME_DIR="${SYNAPSE_LOCAL_RUNTIME_DIR:-${HOME}/synapse-runtime}"
DATA_DIR="${SYNAPSE_LOCAL_DATA_DIR:-${HOME}/synapse-data}"
TMP_DIR="${RUNTIME_DIR}/tmp"
BACKEND_PID_FILE="${RUNTIME_DIR}/synapse-backend.pid"
UI_PID_FILE="${RUNTIME_DIR}/synapse-ui.pid"
BACKEND_LOG_FILE="${LOG_DIR}/synapse-backend.log"
UI_LOG_FILE="${LOG_DIR}/synapse-ui.log"

mkdir -p "${LOG_DIR}" "${RUNTIME_DIR}" "${DATA_DIR}" "${TMP_DIR}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd brew
require_cmd createdb
require_cmd curl
require_cmd npm
require_cmd psql
require_cmd redis-cli

if [[ ! -x "${PYTHON_BIN}" ]]; then
  python3 -m venv "${ROOT_DIR}/.venv"
fi

if ! "${PYTHON_BIN}" -c "import synapse" >/dev/null 2>&1; then
  "${PIP_BIN}" install -e "${ROOT_DIR}"
fi

if [[ ! -d "${ROOT_DIR}/ui/node_modules" ]]; then
  (cd "${ROOT_DIR}/ui" && npm install)
fi

if [[ -x "${PLAYWRIGHT_BIN}" ]]; then
  "${PLAYWRIGHT_BIN}" install chromium >/dev/null
fi

brew services start postgresql@17 >/dev/null
brew services start redis >/dev/null

if ! psql -lqt | cut -d '|' -f 1 | tr -d ' ' | grep -qx "synapse"; then
  createdb synapse
fi

psql -d synapse -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
redis-cli ping >/dev/null
psql -d synapse -c "SELECT 1" >/dev/null

is_pid_live() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] || return 1
  local pid
  pid="$(cat "${pid_file}")"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1
}

wait_for_http() {
  local url="$1"
  local retries="${2:-30}"
  local delay="${3:-1}"
  local attempt=1
  until curl -fsS "${url}" >/dev/null 2>&1; do
    if (( attempt >= retries )); then
      echo "Timed out waiting for ${url}" >&2
      return 1
    fi
    sleep "${delay}"
    attempt=$(( attempt + 1 ))
  done
}

bootstrap_ui_env() {
  if [[ -f "${UI_ENV_FILE}" ]]; then
    local api_key
    local project_id
    api_key="$(grep '^NEXT_PUBLIC_SYNAPSE_API_KEY=' "${UI_ENV_FILE}" | tail -n 1 | cut -d '=' -f 2- || true)"
    project_id="$(grep '^NEXT_PUBLIC_SYNAPSE_PROJECT_ID=' "${UI_ENV_FILE}" | tail -n 1 | cut -d '=' -f 2- || true)"
    if [[ -n "${api_key}" && -n "${project_id}" ]]; then
      if curl -fsS -H "X-API-Key: ${api_key}" -H "X-Synapse-Project-Id: ${project_id}" "${API_BASE_URL}/api/interventions" >/dev/null 2>&1; then
        return 0
      fi
    fi
  fi

  local bootstrap_json
  bootstrap_json="$(${PYTHON_BIN} - <<'PY'
import json
import os
import urllib.request

from synapse.security.tokens import JWTCodec

base = "http://127.0.0.1:8000/api"
jwt_secret = os.environ.get("SYNAPSE_JWT_SECRET", "synapse-dev-secret")
codec = JWTCodec(jwt_secret, "synapse", "synapse-api")
token = codec.encode(
    {
        "sub": "local-bootstrap-admin",
        "type": "operator",
        "scopes": ["admin"],
        "organization_id": "bootstrap-org",
        "project_id": "bootstrap-project",
    },
    expires_in_seconds=86400,
)
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def request(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())


org_slug = "synapse-local"
project_slug = "synapse-local-ui"
user_email = "operator@synapse.local"

orgs = request("GET", "/platform/organizations")
org = next((item for item in orgs if item.get("slug") == org_slug), None)
if org is None:
    org = request(
        "POST",
        "/platform/organizations",
        {"name": "Synapse Local", "slug": org_slug, "metadata": {"host": "mac-mini"}},
    )

projects = request("GET", f"/platform/projects?organization_id={org['organization_id']}")
project = next((item for item in projects if item.get("slug") == project_slug), None)
if project is None:
    project = request(
        "POST",
        "/platform/projects",
        {
            "organization_id": org["organization_id"],
            "name": "Synapse Local UI",
            "slug": project_slug,
            "description": "Local Mac Mini dashboard project",
            "metadata": {"host": "mac-mini"},
        },
    )

users = request("GET", f"/platform/users?organization_id={org['organization_id']}&project_id={project['project_id']}")
user = next((item for item in users if item.get("email") == user_email), None)
if user is None:
    user = request(
        "POST",
        "/platform/users",
        {
            "organization_id": org["organization_id"],
            "project_ids": [project["project_id"]],
            "email": user_email,
            "display_name": "Local Operator",
            "metadata": {"host": "mac-mini"},
        },
    )

issued = request(
    "POST",
    "/platform/api-keys",
    {
        "organization_id": org["organization_id"],
        "project_id": project["project_id"],
        "user_id": user["user_id"],
        "name": "mac-mini-ui",
        "scopes": ["tasks:read", "tasks:write"],
        "metadata": {"host": "mac-mini", "purpose": "ui"},
    },
)

print(json.dumps({"project_id": project["project_id"], "api_key": issued["api_key"]}))
PY
)"

  local project_id
  local api_key
  project_id="$(printf '%s' "${bootstrap_json}" | "${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin)["project_id"])')"
  api_key="$(printf '%s' "${bootstrap_json}" | "${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin)["api_key"])')"

  cat > "${UI_ENV_FILE}" <<EOF2
NEXT_PUBLIC_SYNAPSE_API_BASE_URL=${API_BASE_URL}
NEXT_PUBLIC_SYNAPSE_WS_URL=${WS_BASE_URL}
NEXT_PUBLIC_SYNAPSE_PROJECT_ID=${project_id}
NEXT_PUBLIC_SYNAPSE_API_KEY=${api_key}
EOF2
}

if ! curl -fsS "${API_BASE_URL}/api/health" >/dev/null 2>&1; then
  if is_pid_live "${BACKEND_PID_FILE}"; then
    echo "Backend PID is live but healthcheck is failing." >&2
    exit 1
  fi

  (
    cd "${ROOT_DIR}"
    export TMPDIR="${TMP_DIR}"
    set -a
    source "${ENV_FILE}"
    set +a
    source "${ROOT_DIR}/.venv/bin/activate"
    exec uvicorn synapse.main:app --host 0.0.0.0 --port 8000
  ) >"${BACKEND_LOG_FILE}" 2>&1 < /dev/null &
  echo $! > "${BACKEND_PID_FILE}"
  wait_for_http "${API_BASE_URL}/api/health"
fi

bootstrap_ui_env

if ! curl -fsS "http://127.0.0.1:${UI_PORT}" >/dev/null 2>&1; then
  if is_pid_live "${UI_PID_FILE}"; then
    echo "UI PID is live but the UI endpoint is not responding." >&2
    exit 1
  fi

  (
    cd "${ROOT_DIR}/ui"
    exec npm run dev -- --port "${UI_PORT}"
  ) >"${UI_LOG_FILE}" 2>&1 < /dev/null &
  echo $! > "${UI_PID_FILE}"
  wait_for_http "http://127.0.0.1:${UI_PORT}"
fi

echo "Synapse backend: ${API_BASE_URL}"
echo "Synapse UI: http://127.0.0.1:${UI_PORT}"
echo "Backend log: ${BACKEND_LOG_FILE}"
echo "UI log: ${UI_LOG_FILE}"
