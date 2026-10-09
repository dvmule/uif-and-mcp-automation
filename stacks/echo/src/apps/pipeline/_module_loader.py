"""Load pipeline modules whose on-disk names use kebab-case.

Glue and pytest cannot `import bronze-ingest`. Callers use importlib via this
helper. DQ handlers live under ``dq-kinds/`` (also not a valid package name).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PIPELINE_DIR = Path(__file__).resolve().parent


def _exec_module(path: Path, mod_name: str | None = None) -> ModuleType:
    name = mod_name or path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_pipeline_module(filename: str) -> ModuleType:
    """Load a script from ``src/apps/pipeline/{filename}``."""
    path = _PIPELINE_DIR / filename
    if not path.is_file():
        raise ImportError(f"Pipeline module not found: {path}")
    mod_name = filename.removesuffix(".py").replace("-", "_")
    return _exec_module(path, mod_name)


def load_dq_kind_module(stem: str) -> ModuleType:
    """Load ``dq-kinds/{stem}.py`` (e.g. ``reconcile``, ``null-rate``)."""
    base = _PIPELINE_DIR
    for path in (base / "dq-kinds" / f"{stem}.py", base / f"{stem}.py"):
        if path.is_file():
            return _exec_module(path, stem.replace("-", "_"))
    raise ImportError(f"DQ kind module '{stem}' not found under {base}")
