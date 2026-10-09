# Path setup lives in qe/conftest.py (one level up) — pytest loads parent
# conftests first, so control_panel, beacon_export and dynamic_mapping are
# already importable here.
#
# This directory ALSO goes on sys.path (UIFPE-152): the repo runs pytest in
# importlib import mode, which no longer puts test directories on sys.path,
# and this suite shares fixtures via a plain helper module
# (lifecycle_fixtures.py) that its test files import.

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
