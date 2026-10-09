# conftest.py
# Centralized sys.path setup for the whole stack suite (same convention as
# stacks/beacon/qe/conftest.py and domains/product/qe/conftest.py — this is the
# ONLY place that edits sys.path).
#
# Runtime layout is /app with the `app` package at its root (src/Dockerfile);
# in tests we mirror that by putting src/ on the path, so `from app.clients...`
# resolves exactly as it does in the image.

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
