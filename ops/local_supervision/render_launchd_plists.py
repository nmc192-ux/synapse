from __future__ import annotations

import os
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home()
launch_agents = HOME / "Library" / "LaunchAgents"
log_dir = Path(os.environ.get("SYNAPSE_SUPERVISION_LOG_DIR", HOME / "synapse-logs" / "services"))
launch_agents.mkdir(parents=True, exist_ok=True)
log_dir.mkdir(parents=True, exist_ok=True)

services = [
    {"label": "ai.synapse.backend", "args": [str(ROOT / "ops/local_supervision/bin/run_synapse_backend.sh")]},
    {"label": "ai.synapse.ui", "args": [str(ROOT / "ops/local_supervision/bin/run_synapse_ui.sh")]},
    {"label": "ai.openclaw.default-local", "args": [str(ROOT / "ops/local_supervision/bin/run_openclaw_gateway.sh")]},
    {
        "label": "ai.openclaw.discord-hourly-report",
        "args": [str(ROOT / "ops/local_supervision/bin/run_openclaw_discord_report.sh")],
        "run_at_load": True,
        "start_interval": int(os.environ.get("OPENCLAW_DISCORD_REPORT_INTERVAL_SECONDS", "3600")),
        "keep_alive": False,
    },
    {"label": "ai.synapse.swarm.director", "args": [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "director.py", os.environ.get("SWARM_DIRECTOR_INTERVAL", "300")]},
    {"label": "ai.synapse.swarm.browser-runner-1", "args": [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "browser_runner_1.py", os.environ.get("SWARM_BROWSER_RUNNER_1_INTERVAL", "180")]},
    {"label": "ai.synapse.swarm.browser-runner-2", "args": [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "browser_runner_2.py", os.environ.get("SWARM_BROWSER_RUNNER_2_INTERVAL", "180")]},
    {"label": "ai.synapse.swarm.auditor", "args": [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "auditor.py", os.environ.get("SWARM_AUDITOR_INTERVAL", "900")]},
    {"label": "ai.synapse.swarm.reporter", "args": [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "reporter.py", os.environ.get("SWARM_REPORTER_INTERVAL", "3600")]},
    {"label": "ai.synapse.swarm.chaos-monkey", "args": [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "chaos_monkey.py", os.environ.get("SWARM_CHAOS_MONKEY_INTERVAL", "86400")]},
]

if os.environ.get("SYNAPSE_PUBLIC_UI_ENABLE", "false") == "true":
    services.append(
        {
            "label": "ai.synapse.public-ui",
            "args": [str(ROOT / "ops/local_supervision/bin/run_public_ui_tunnel.sh")],
        }
    )

for service in services:
    label = service["label"]
    args = service["args"]
    payload = {
        "Label": label,
        "ProgramArguments": args,
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": service.get("run_at_load", True),
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / f"{label}.log"),
        "StandardErrorPath": str(log_dir / f"{label}.err.log"),
    }
    keep_alive = service.get("keep_alive", True)
    if keep_alive:
        payload["KeepAlive"] = {"SuccessfulExit": False}
    start_interval = service.get("start_interval")
    if start_interval is not None:
        payload["StartInterval"] = start_interval
    target = launch_agents / f"{label}.plist"
    with target.open("wb") as handle:
        plistlib.dump(payload, handle)
    print(target)
