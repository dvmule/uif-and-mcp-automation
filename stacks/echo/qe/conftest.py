"""Centralized sys.path setup for the whole Echo suite.

Same convention as stacks/beacon/qe/conftest.py — this is the ONLY place that
edits sys.path. Per the repo's pytest.ini and domains/product/CLAUDE.md: there is
no pyproject.toml, so do NOT add per-file sys.path hacks back.

Runtime layout is FLAT: the Lambda zip and the Glue --extra-py-files set both put
every module side by side at the top level. Echo's apps are therefore loose
modules in a directory, not packages with __init__.py the way Beacon's are — so
each app directory goes on the path individually, not their common parent.

Pipeline Glue scripts use kebab-case filenames (not valid Python import names).
Tests load them via ``qe.pipeline.job_loader`` or read source as text. DQ handlers
live under ``dq-kinds/`` and load the same way.

Note on mocking: the moved test modules each stub awsglue / pyspark / boto3
themselves via sys.modules at import time. That is deliberately left as-is —
hoisting it here would change import-order behaviour across 21 files for no gain
while the suite is still being brought to green (see CHECKLIST.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_APPS = _SRC / "apps"
_QE_PIPELINE = Path(__file__).resolve().parent / "pipeline"
# Shared runtime packages (platform/python) sit at the zip root beside the
# stack's own - see platform/python/README.md (UIFPE-543).
_PLATFORM = Path(__file__).resolve().parents[3] / "platform" / "python"

_ROOTS = (
    _PLATFORM,
    _SRC / "libs",
    _APPS,
    _APPS / "pipeline",
    _APPS / "control_panel",
    _APPS / "lambdas",
    _APPS / "cli",
    _QE_PIPELINE,
)

for _p in _ROOTS:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
