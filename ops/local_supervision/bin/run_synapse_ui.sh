#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"

ensure_dirs
require_cmd npm

if [[ ! -f "${UI_ENV_FILE}" ]]; then
  echo "Missing UI env file: ${UI_ENV_FILE}" >&2
  exit 1
fi

cd "${ROOT_DIR}/ui"

if [[ "${SYNAPSE_UI_MODE:-production}" == "production" ]]; then
  exec npm run start -- --port "${SYNAPSE_UI_PORT:-3001}"
fi

exec npm run dev -- --port "${SYNAPSE_UI_PORT:-3001}"
