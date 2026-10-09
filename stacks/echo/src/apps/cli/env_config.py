# env_config.py
# Loads the environment picklist from ingestion-framework/environments.json —
# the single source of truth for valid UIF environments. Every CLI script's
# --env argument derives its choices/default from here; adding an environment
# is a JSON edit, not a code change.

import json
import os
from pathlib import Path

# UIF_DAL_GOLD_ROOT overrides the repo root (e.g. a future monorepo layout);
# unset defaults to the current parents[1] behavior, unchanged.
_REPO_ROOT = Path(os.environ.get("UIF_DAL_GOLD_ROOT",
                                  str(Path(__file__).resolve().parents[1])))
_CONFIG_PATH = _REPO_ROOT / "environments.json"
# utf-8-sig: tolerate a BOM if the file was edited by Windows tooling.
_config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8-sig"))

ENVIRONMENTS = list(_config["environments"])
DEFAULT_ENV = _config.get("default", ENVIRONMENTS[0])
