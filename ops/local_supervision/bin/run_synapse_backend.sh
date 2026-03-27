#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"

ensure_dirs
require_cmd curl
require_cmd psql
require_cmd redis-cli

if [[ ! -f "${BACKEND_ENV_FILE}" ]]; then
  echo "Missing backend env file: ${BACKEND_ENV_FILE}" >&2
  exit 1
fi

set -a
source "${BACKEND_ENV_FILE}"
set +a

if [[ ! -x "${VENV_UVICORN}" ]]; then
  echo "Missing virtualenv uvicorn binary at ${VENV_UVICORN}" >&2
  exit 1
fi

TMP_DIR="${RUNTIME_DIR}/tmp"
mkdir -p "${TMP_DIR}"
export TMPDIR="${TMP_DIR}"

exec "${VENV_UVICORN}" synapse.main:app --host 0.0.0.0 --port "${SYNAPSE_BACKEND_PORT:-8000}"
