#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/ops/local_supervision" && pwd)/common.sh"

for label in "${STACK_LABELS[@]}"; do
  bootout_label "${label}"
done

brew services stop ollama >/dev/null || true
brew services stop redis >/dev/null || true
brew services stop postgresql@17 >/dev/null || true

echo "Stopped Synapse synthetic alpha stack."
