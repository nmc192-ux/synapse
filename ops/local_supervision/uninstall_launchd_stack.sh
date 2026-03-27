#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

for label in "${STACK_LABELS[@]}"; do
  bootout_label "${label}"
  rm -f "$(service_plist_path "${label}")"
done

echo "Removed launchd agents for the Synapse synthetic alpha stack."
