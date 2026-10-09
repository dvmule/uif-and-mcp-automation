"""dynamic_mapping — config-driven PySpark transformation engine.

The engine never creates a SparkSession; the caller (Glue job, test, CLI)
passes one in. Heavy pyspark imports are deferred so that config loading and
static validation work in environments without Spark installed (e.g. CI
config-lint jobs).
"""
from .config_loader import load_config, parse_config
from .config_validator import validate_expressions, validate_references, validate_structure
from .errors import ConfigError, ConfigValidationError, EmptyInputError, Issue
from .models import MappingConfig

__all__ = [
    "load_config", "parse_config",
    "validate_structure", "validate_expressions", "validate_references",
    "ConfigError", "ConfigValidationError", "EmptyInputError", "Issue",
    "MappingConfig",
]


def run(*args, **kwargs):
    """Lazy proxy for dynamic_mapping.main.run (avoids importing pyspark eagerly)."""
    from .main import run as _run
    return _run(*args, **kwargs)
