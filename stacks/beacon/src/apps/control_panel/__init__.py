"""control_panel — Beacon self-service authoring API (RELAY control plane).

One API for all three authoring doors: the web UI, MCP agents, and CI
validation of Git-authored configs (design doc F13, D9). Deploys as a
FastAPI container on ECS Fargate behind the RELAY API Gateway (D19).

This package deliberately reuses the shared beacon_export package (export
config validation) and, when importable, the dynamic-mapping engine's own
static validators — the panel can never accept a config the pipeline would
reject, because they are the same code.
"""

import sys
from pathlib import Path

# beacon_export is a sibling under src/apps/; the dynamic-mapping engine
# lives under src/libs/. In the Fargate image all packages sit side by side
# under /app and PYTHONPATH covers it — there the repo-relative paths don't
# exist (nor do enough parent dirs), so resolve defensively.
_here = Path(__file__).resolve()
if len(_here.parents) > 2:
    _src = _here.parents[2]          # control_panel/ -> apps/ -> src/
    for _sibling in (_src / "apps", _src / "libs"):
        if _sibling.is_dir() and str(_sibling) not in sys.path:
            sys.path.insert(0, str(_sibling))
