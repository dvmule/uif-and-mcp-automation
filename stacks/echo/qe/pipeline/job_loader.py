"""Load kebab-case pipeline scripts for unit tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

PIPELINE_DIR = Path(__file__).resolve().parents[2] / "src" / "apps" / "pipeline"


def load_pipeline_module(filename: str) -> ModuleType:
    path = PIPELINE_DIR / filename
    if not path.is_file():
        raise ImportError(f"Pipeline module not found: {path}")
    mod_name = filename.removesuffix(".py").replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def load_dq_kind_module(stem: str) -> ModuleType:
    for rel in (f"dq-kinds/{stem}.py", f"{stem}.py"):
        path = PIPELINE_DIR / rel
        if path.is_file():
            mod_name = stem.replace("-", "_")
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load module from {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            return module
    raise ImportError(f"DQ kind module '{stem}' not found under {PIPELINE_DIR}")
