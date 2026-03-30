#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/ops/local_supervision" && pwd)/common.sh"

echo "Homebrew services:"
brew services list | egrep 'postgresql@17|redis|ollama' || true
echo

echo "Launchd services:"
for label in "${STACK_LABELS[@]}"; do
  print_service_status "${label}"
done

echo
printf 'Backend logs: %s\n' "${LOG_DIR}/ai.synapse.backend.log"
printf 'UI logs: %s\n' "${LOG_DIR}/ai.synapse.ui.log"
printf 'OpenClaw logs: %s\n' "${LOG_DIR}/ai.openclaw.default-local.log"
if [[ "${SYNAPSE_PUBLIC_UI_ENABLE:-false}" == "true" ]]; then
  printf 'Public UI tunnel logs: %s\n' "${LOG_DIR}/ai.synapse.public-ui.log"
  printf 'Public UI URL file: %s\n' "${SYNAPSE_PUBLIC_UI_URL_FILE:-${RUNTIME_DIR}/public-ui-url.txt}"
  if [[ -f "${SYNAPSE_PUBLIC_UI_URL_FILE:-${RUNTIME_DIR}/public-ui-url.txt}" ]]; then
    printf 'Public UI URL: %s\n' "$(cat "${SYNAPSE_PUBLIC_UI_URL_FILE:-${RUNTIME_DIR}/public-ui-url.txt}")"
  fi
fi
printf 'Swarm logs dir: %s\n' "${LOG_DIR}"
printf 'Repo runtime dir: %s\n' "${ROOT_DIR}/examples/synthetic_alpha_swarm/runtime"
printf 'Machine runtime dir: %s\n' "${RUNTIME_DIR}" 
