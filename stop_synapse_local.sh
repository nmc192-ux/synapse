#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env.local.dev"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  source "${ENV_FILE}"
  set +a
fi

RUNTIME_DIR="${SYNAPSE_LOCAL_RUNTIME_DIR:-${HOME}/synapse-runtime}"
BACKEND_PID_FILE="${RUNTIME_DIR}/synapse-backend.pid"
UI_PID_FILE="${RUNTIME_DIR}/synapse-ui.pid"

stop_from_pid_file() {
  local pid_file="$1"
  if [[ ! -f "${pid_file}" ]]; then
    return 0
  fi

  local pid
  pid="$(cat "${pid_file}")"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}"
    wait "${pid}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
}

stop_from_pid_file "${UI_PID_FILE}"
stop_from_pid_file "${BACKEND_PID_FILE}"

echo "Stopped local Synapse application processes."
