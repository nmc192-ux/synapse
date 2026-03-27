#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"

ensure_dirs

ROLE="${1:-}"
if [[ -z "${ROLE}" ]]; then
  echo "Usage: $0 <role-script> [interval-seconds]" >&2
  exit 1
fi

INTERVAL="${2:-300}"
if [[ ! -f "${SWARM_ENV_FILE}" ]]; then
  echo "Missing swarm env file: ${SWARM_ENV_FILE}" >&2
  exit 1
fi

set -a
source "${SWARM_ENV_FILE}"
set +a

exec "${VENV_PYTHON}" "${ROOT_DIR}/examples/synthetic_alpha_swarm/${ROLE}" --interval-seconds "${INTERVAL}"
