# conftest.py
# Centralized sys.path setup for the whole domain suite (same convention as
# domains/product/qe/conftest.py — this is the ONLY place that edits sys.path).
#
# Runtime layout is /app with all packages side by side (both Dockerfiles);
# in tests we mirror that by putting src/apps and src/libs on the path, so
# `from beacon_export import ...`, `import control_panel`, and
# `import dynamic_mapping` resolve exactly as they do in the images.

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
# Shared runtime packages (platform/python) sit at the zip root beside the
# stack's own - see platform/python/README.md (UIFPE-543).
_PLATFORM = Path(__file__).resolve().parents[3] / "platform" / "python"
for _p in (_SRC / "apps", _SRC / "libs", _PLATFORM):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
