#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"

ensure_dirs
require_cmd cloudflared

if [[ "${SYNAPSE_PUBLIC_UI_ENABLE:-false}" != "true" ]]; then
  echo "Public UI tunnel disabled."
  exit 0
fi

PUBLIC_URL_FILE="${SYNAPSE_PUBLIC_UI_URL_FILE:-${RUNTIME_DIR}/public-ui-url.txt}"
mkdir -p "$(dirname "${PUBLIC_URL_FILE}")"
: > "${PUBLIC_URL_FILE}"

TARGET_URL="http://127.0.0.1:${SYNAPSE_UI_PORT:-3001}"

cloudflared tunnel --url "${TARGET_URL}" --no-autoupdate 2>&1 | while IFS= read -r line; do
  printf '%s\n' "${line}"
  if [[ "${line}" =~ https://[a-z0-9-]+\.trycloudflare\.com ]]; then
    printf '%s\n' "${BASH_REMATCH[0]}" > "${PUBLIC_URL_FILE}"
  fi
done
