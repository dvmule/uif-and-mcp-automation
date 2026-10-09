# conftest.py
# Centralized sys.path setup for the test suite, replacing the per-file
# sys.path.insert() hacks that used to live at the top of each test module.
#
# In the monorepo layout the tests live in domains/product/qe/ while the code
# under test lives in domains/product/src/apps/. pyproject.toml was NOT
# migrated (domain-root packaging files are blocked on a framework decision),
# so there is no [tool.pytest.ini_options] pythonpath here — this conftest is
# the ONLY thing putting the app tree on sys.path. It must therefore keep
# working for pytest, for `py -m unittest`, and for a directly-run test file.

import sys
from pathlib import Path

# domains/product/qe/conftest.py -> parents[1] == domains/product
APPS = Path(__file__).resolve().parents[1] / "src" / "apps"
if str(APPS) not in sys.path:
    sys.path.insert(0, str(APPS))

# Shared runtime packages (platform/python) sit at the zip root beside this
# domain's own modules — the marketing-copy API's zip copies jwt_verify.py in.
# Same line every stack's conftest carries (platform/python/README.md).
_PLATFORM = Path(__file__).resolve().parents[3] / "platform" / "python"
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

# glue_jobs/ and scripts/ are real packages (each has __init__.py), so tests
# can do `from glue_jobs import gold_cdc` / `from scripts import gold_config`
# — both resolve off APPS above. But scripts/sync_pipelines.py and
# scripts/register_gold_pipeline.py use same-directory bare imports
# internally (`import gold_config`, `from env_config import ...`) — that only
# resolves when Python is run as `py src/apps/scripts/foo.py` (Python
# auto-adds the script's own dir to sys.path[0]) or when scripts/ is
# separately on sys.path, as here. Keep both import styles working.
# The gold runtime and control plane live in stacks/echo (UIFPE-358 phase 2);
# the domain tests that exercise them import by bare module name, as Echo does.
_ECHO_SRC = Path(__file__).resolve().parents[3] / "stacks" / "echo" / "src"
for _root in (_ECHO_SRC / "libs", _ECHO_SRC / "apps" / "pipeline",
              _ECHO_SRC / "apps" / "cli", _ECHO_SRC / "apps" / "lambdas"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

# The Lambda source dir is `lambda_dispatcher/` here. In uif-dal-gold it was
# `lambda/`, which could never be a real Python package — "lambda" is a
# keyword, so `from lambda import gold_dispatcher` is a SyntaxError — hence
# the rename in this migration. (The old "never rename this dir" coupling was
# infra/dispatcher.tf's archive_file paths and the source repo's GitHub
# workflows; dispatcher.tf is updated in the same change, and the workflows
# were not migrated.)
#
# Its modules are still imported by BARE name (gold_dispatcher, echo_resources)
# rather than as `lambda_dispatcher.gold_dispatcher`, because that matches the
# flat Lambda zip convention the deployed function relies on: gold_dispatcher.py
# does `from echo_resources import echo` as a bare sibling import, which only
# resolves when this directory is itself on sys.path.

# Same flat-zip convention for the Generate-with-AI draft Lambda (UIFPE-102).

# The Beacon export platform is SHARED across domains (product today;
# hr/legal/identity as they onboard exports) and therefore lives in the
# beacon stack (stacks/beacon/, migrated from domains/beacon/ per ADR-0010 —
# a deployable stack, not a domain), not under this one. This domain holds
# only its export CONFIGS (src/schemas/beacon/) and the tests that exercise
# product data through the platform's packages. At runtime the Fargate
# PySpark container COPYs the packages into the image; in tests we resolve
# them by path, mirroring that consumed-by-reference relationship.
_DX_SRC = (Path(__file__).resolve().parents[3]
           / "stacks" / "beacon" / "src")
for _shared_src in (_DX_SRC / "apps", _DX_SRC / "libs"):
    if str(_shared_src) not in sys.path:
        sys.path.insert(0, str(_shared_src))
