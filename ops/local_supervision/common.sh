#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUPERVISION_ENV_FILE="${SYNAPSE_SUPERVISION_ENV_FILE:-${ROOT_DIR}/.env.synthetic-alpha-supervision}"
DEFAULT_SUPERVISION_ENV_FILE="${ROOT_DIR}/config/examples/synthetic-alpha-supervision.env.example"

if [[ -f "${SUPERVISION_ENV_FILE}" ]]; then
  set -a
  source "${SUPERVISION_ENV_FILE}"
  set +a
elif [[ -f "${DEFAULT_SUPERVISION_ENV_FILE}" ]]; then
  set -a
  source "${DEFAULT_SUPERVISION_ENV_FILE}"
  set +a
fi

LOG_DIR="${SYNAPSE_SUPERVISION_LOG_DIR:-${HOME}/synapse-logs/services}"
RUNTIME_DIR="${SYNAPSE_LOCAL_RUNTIME_DIR:-${HOME}/synapse-runtime}"
DATA_DIR="${SYNAPSE_LOCAL_DATA_DIR:-${HOME}/synapse-data}"
BACKEND_ENV_FILE="${ROOT_DIR}/${SYNAPSE_BACKEND_ENV_FILE:-.env.local.dev}"
UI_ENV_FILE="${ROOT_DIR}/${SYNAPSE_UI_ENV_FILE:-ui/.env.local}"
SWARM_ENV_FILE="${ROOT_DIR}/${SYNTHETIC_ALPHA_SWARM_ENV_FILE:-examples/synthetic_alpha_swarm/.env.synthetic-alpha}"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
VENV_UVICORN="${ROOT_DIR}/.venv/bin/uvicorn"
NPM_BIN="$(command -v npm)"
OPENCLAW_BIN="$(command -v openclaw)"
BREW_BIN="$(command -v brew)"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
LAUNCHD_DOMAIN="gui/$(id -u)"

STACK_LABELS=(
  "ai.synapse.backend"
  "ai.synapse.ui"
  "ai.openclaw.default-local"
  "ai.synapse.swarm.director"
  "ai.synapse.swarm.browser-runner-1"
  "ai.synapse.swarm.browser-runner-2"
  "ai.synapse.swarm.auditor"
  "ai.synapse.swarm.reporter"
  "ai.synapse.swarm.chaos-monkey"
)

ensure_dirs() {
  mkdir -p "${LOG_DIR}" "${RUNTIME_DIR}" "${DATA_DIR}" "${LAUNCH_AGENTS_DIR}"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

service_plist_path() {
  local label="$1"
  echo "${LAUNCH_AGENTS_DIR}/${label}.plist"
}

is_launchd_loaded() {
  local label="$1"
  launchctl print "${LAUNCHD_DOMAIN}/${label}" >/dev/null 2>&1
}

bootout_label() {
  local label="$1"
  launchctl bootout "${LAUNCHD_DOMAIN}/${label}" >/dev/null 2>&1 || true
}

bootstrap_plist() {
  local plist="$1"
  bootout_label "$(basename "${plist}" .plist)"
  launchctl bootstrap "${LAUNCHD_DOMAIN}" "${plist}"
}

kickstart_label() {
  local label="$1"
  launchctl kickstart -k "${LAUNCHD_DOMAIN}/${label}" >/dev/null 2>&1 || true
}

print_service_status() {
  local label="$1"
  if is_launchd_loaded "${label}"; then
    echo "${label}: loaded"
  else
    echo "${label}: not loaded"
  fi
}
