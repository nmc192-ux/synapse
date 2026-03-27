#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"

ensure_dirs
require_cmd openclaw

export OLLAMA_API_KEY="${OPENCLAW_OLLAMA_API_KEY:-ollama-local}"
exec openclaw --profile "${OPENCLAW_PROFILE:-default_local}" gateway run --bind loopback --port "${OPENCLAW_GATEWAY_PORT:-18790}"
