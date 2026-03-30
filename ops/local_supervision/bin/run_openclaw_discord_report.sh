#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"

ensure_dirs
require_cmd curl
require_cmd python3
require_cmd "${OPENCLAW_BIN}"

REPORT_CHANNEL="${OPENCLAW_DISCORD_REPORT_CHANNEL:-discord}"
REPORT_TARGET="${OPENCLAW_DISCORD_REPORT_TARGET:-}"
BACKEND_BASE_URL="${SYNTHETIC_ALPHA_SWARM_BASE_URL:-http://127.0.0.1:${SYNAPSE_BACKEND_PORT:-8000}}"
UI_URL="http://127.0.0.1:${SYNAPSE_UI_PORT:-3001}"
OPENCLAW_HEALTH_URL="http://127.0.0.1:${OPENCLAW_GATEWAY_PORT:-18790}/health"
LATEST_REPORT_JSON="${HOME}/synapse-logs/reports/synthetic_alpha/synthetic_alpha_summary_latest.json"

if [[ -f "${SWARM_ENV_FILE}" ]]; then
  set -a
  source "${SWARM_ENV_FILE}"
  set +a
fi

if [[ -z "${REPORT_TARGET}" ]]; then
  echo "Skipping OpenClaw Discord report: OPENCLAW_DISCORD_REPORT_TARGET is not configured."
  exit 0
fi

fetch_json_with_retries() {
  local url="$1"
  local fallback="$2"
  local attempts="${3:-5}"
  local delay="${4:-2}"
  local result=""
  for ((i=1; i<=attempts; i++)); do
    if result="$(curl -fsS --max-time 5 "${url}" 2>/dev/null)"; then
      printf '%s\n' "${result}"
      return 0
    fi
    sleep "${delay}"
  done
  printf '%s\n' "${fallback}"
}

fetch_http_code_with_retries() {
  local url="$1"
  local attempts="${2:-5}"
  local delay="${3:-2}"
  local code=""
  for ((i=1; i<=attempts; i++)); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${url}" 2>/dev/null || true)"
    if [[ -n "${code}" && "${code}" != "000" ]]; then
      printf '%s\n' "${code}"
      return 0
    fi
    sleep "${delay}"
  done
  printf '000\n'
}

export BACKEND_HEALTH_JSON="$(fetch_json_with_retries "${BACKEND_BASE_URL}/api/health" '{"status":"error"}')"
export BACKEND_READY_JSON="$(fetch_json_with_retries "${BACKEND_BASE_URL}/api/ready" '{"status":"error"}')"
export OPENCLAW_HEALTH_JSON="$(fetch_json_with_retries "${OPENCLAW_HEALTH_URL}" '{"status":"error"}')"
export UI_HTTP_CODE="$(fetch_http_code_with_retries "${UI_URL}")"
export OLLAMA_STATUS="$(fetch_json_with_retries "http://127.0.0.1:11434/api/version" '{"status":"error"}' 3 1)"
export OPENCLAW_PROFILE_VALUE="${OPENCLAW_PROFILE:-default_local}"
export REPORT_PATH_VALUE="${LATEST_REPORT_JSON}"
export NOW_UTC="$(date -u +'%Y-%m-%d %H:%M:%SZ')"
export OPENCLAW_REPORT_TARGET_VALUE="${REPORT_TARGET}"

REPORT_MESSAGE="$(
python3 - <<'PY'
import json
import os
from pathlib import Path

def parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}

health = parse_json(os.environ.get("BACKEND_HEALTH_JSON", ""))
ready = parse_json(os.environ.get("BACKEND_READY_JSON", ""))
openclaw = parse_json(os.environ.get("OPENCLAW_HEALTH_JSON", ""))
ollama = parse_json(os.environ.get("OLLAMA_STATUS", ""))
ui_code = os.environ.get("UI_HTTP_CODE", "000")
profile = os.environ.get("OPENCLAW_PROFILE_VALUE", "default_local")
report_path = Path(os.environ.get("REPORT_PATH_VALUE", ""))
summary = {}
if report_path.is_file():
    try:
        summary = json.loads(report_path.read_text())
    except Exception:
        summary = {}

daily = summary.get("daily", {}) if isinstance(summary, dict) else {}
daily_summary = daily.get("summary", {}) if isinstance(daily, dict) else {}
projects = daily.get("projects", []) if isinstance(daily, dict) else []

def project_metrics(alias: str) -> dict:
    for item in projects:
        if isinstance(item, dict) and item.get("project_alias") == alias:
            return item
    return {}

steady = project_metrics("steady")
chaos = project_metrics("chaos")
a2a_sent = daily_summary.get("a2a_messages_sent", 0)
a2a_succeeded = daily_summary.get("a2a_messages_succeeded", 0)
a2a_failed = daily_summary.get("a2a_messages_failed", 0)

lines = [
    f"OpenClaw hourly Synapse report ({os.environ.get('NOW_UTC')})",
    f"Profile: {profile}",
    f"Backend health: {health.get('status', 'unknown')}",
    f"Readiness: {ready.get('status', 'unknown')} | postgres={ready.get('checks', {}).get('postgres', {}).get('status', 'unknown')} redis={ready.get('checks', {}).get('redis', {}).get('status', 'unknown')}",
    f"UI HTTP: {ui_code}",
    f"OpenClaw gateway: {openclaw.get('status', openclaw.get('ok', 'unknown'))}",
    f"Ollama: {ollama.get('version', ollama.get('status', 'unknown'))}",
    f"Steady runs: started={steady.get('runs_started', 0)} completed={steady.get('runs_completed', 0)} failed={steady.get('runs_failed', 0)} failure_rate={steady.get('per_project_failure_rate', 0)}",
    f"Chaos runs: started={chaos.get('runs_started', 0)} completed={chaos.get('runs_completed', 0)} failed={chaos.get('runs_failed', 0)} failure_rate={chaos.get('per_project_failure_rate', 0)}",
    f"A2A: sent={a2a_sent} succeeded={a2a_succeeded} failed={a2a_failed}",
]

top_regressions = sorted(
    [
        (label, count)
        for label, count in (daily_summary.get("browser_errors_by_category", {}) or {}).items()
        if count
    ],
    key=lambda item: (-item[1], item[0]),
)[:3]
if top_regressions:
    lines.append("Top regressions:")
    for label, value in top_regressions:
        lines.append(f"- {label}: {value}")

print("\n".join(lines))
PY
)"

"${OPENCLAW_BIN}" --profile "${OPENCLAW_PROFILE:-default_local}" message send \
  --channel "${REPORT_CHANNEL}" \
  --target "${REPORT_TARGET}" \
  --message "${REPORT_MESSAGE}" \
  --json
