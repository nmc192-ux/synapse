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
    ("ai.synapse.backend", [str(ROOT / "ops/local_supervision/bin/run_synapse_backend.sh")]),
    ("ai.synapse.ui", [str(ROOT / "ops/local_supervision/bin/run_synapse_ui.sh")]),
    ("ai.openclaw.default-local", [str(ROOT / "ops/local_supervision/bin/run_openclaw_gateway.sh")]),
    ("ai.synapse.swarm.director", [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "director.py", os.environ.get("SWARM_DIRECTOR_INTERVAL", "300")]),
    ("ai.synapse.swarm.browser-runner-1", [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "browser_runner_1.py", os.environ.get("SWARM_BROWSER_RUNNER_1_INTERVAL", "180")]),
    ("ai.synapse.swarm.browser-runner-2", [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "browser_runner_2.py", os.environ.get("SWARM_BROWSER_RUNNER_2_INTERVAL", "180")]),
    ("ai.synapse.swarm.auditor", [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "auditor.py", os.environ.get("SWARM_AUDITOR_INTERVAL", "900")]),
    ("ai.synapse.swarm.reporter", [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "reporter.py", os.environ.get("SWARM_REPORTER_INTERVAL", "3600")]),
    ("ai.synapse.swarm.chaos-monkey", [str(ROOT / "ops/local_supervision/bin/run_swarm_role.sh"), "chaos_monkey.py", os.environ.get("SWARM_CHAOS_MONKEY_INTERVAL", "86400")]),
]

for label, args in services:
    payload = {
        "Label": label,
        "ProgramArguments": args,
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / f"{label}.log"),
        "StandardErrorPath": str(log_dir / f"{label}.err.log"),
    }
    target = launch_agents / f"{label}.plist"
    with target.open("wb") as handle:
        plistlib.dump(payload, handle)
    print(target)
