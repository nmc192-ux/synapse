#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

ensure_dirs
require_cmd launchctl
require_cmd python3

export SYNAPSE_SUPERVISION_LOG_DIR="${LOG_DIR}"
export SWARM_DIRECTOR_INTERVAL="${SWARM_DIRECTOR_INTERVAL:-300}"
export SWARM_BROWSER_RUNNER_1_INTERVAL="${SWARM_BROWSER_RUNNER_1_INTERVAL:-180}"
export SWARM_BROWSER_RUNNER_2_INTERVAL="${SWARM_BROWSER_RUNNER_2_INTERVAL:-180}"
export SWARM_AUDITOR_INTERVAL="${SWARM_AUDITOR_INTERVAL:-900}"
export SWARM_REPORTER_INTERVAL="${SWARM_REPORTER_INTERVAL:-3600}"
export SWARM_CHAOS_MONKEY_INTERVAL="${SWARM_CHAOS_MONKEY_INTERVAL:-86400}"

python3 "${ROOT_DIR}/ops/local_supervision/render_launchd_plists.py" >/dev/null

for label in "${STACK_LABELS[@]}"; do
  bootstrap_plist "$(service_plist_path "${label}")"
done

echo "Installed launchd agents into ${LAUNCH_AGENTS_DIR}."
