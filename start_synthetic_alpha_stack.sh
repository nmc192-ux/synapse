#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/ops/local_supervision" && pwd)/common.sh"

ensure_dirs
require_cmd brew
require_cmd npm
require_cmd psql
require_cmd redis-cli
require_cmd openclaw

if [[ ! -f "${SUPERVISION_ENV_FILE}" ]]; then
  echo "Missing supervision env file: ${SUPERVISION_ENV_FILE}" >&2
  echo "Copy config/examples/synthetic-alpha-supervision.env.example to .env.synthetic-alpha-supervision and adjust local values." >&2
  exit 1
fi

if [[ ! -f "${BACKEND_ENV_FILE}" ]]; then
  echo "Missing backend env file: ${BACKEND_ENV_FILE}" >&2
  exit 1
fi

if [[ ! -f "${UI_ENV_FILE}" ]]; then
  echo "Missing UI env file: ${UI_ENV_FILE}" >&2
  exit 1
fi

if [[ "${SYNTHETIC_ALPHA_SWARM_ENABLE:-true}" == "true" && ! -f "${SWARM_ENV_FILE}" ]]; then
  echo "Missing swarm env file: ${SWARM_ENV_FILE}" >&2
  exit 1
fi

brew services start postgresql@17 >/dev/null
brew services start redis >/dev/null
brew services start ollama >/dev/null

if [[ "${SYNAPSE_UI_MODE:-production}" == "production" ]]; then
  (cd "${ROOT_DIR}/ui" && npm run build >/dev/null)
fi

"${ROOT_DIR}/ops/local_supervision/install_launchd_stack.sh"
for label in "${STACK_LABELS[@]}"; do
  kickstart_label "${label}"
done

echo "Started Synapse synthetic alpha stack."
