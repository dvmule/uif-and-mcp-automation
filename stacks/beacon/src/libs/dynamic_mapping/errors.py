"""Error types and the validation Issue record."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    """A single validation finding, tied back to the config location it came from."""
    where: str      # e.g. "mappings[2]", "joins[0].condition", "sources"
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


class ConfigError(Exception):
    """Config file could not be read or parsed at all."""


class ConfigValidationError(Exception):
    """Config parsed but failed validation. Carries per-rule issues."""

    def __init__(self, issues: list[Issue]):
        self.issues = issues
        lines = "\n".join(f"  - {i}" for i in issues)
        super().__init__(f"Config validation failed with {len(issues)} issue(s):\n{lines}")


class EmptyInputError(Exception):
    """Raised when an input source is empty and policy on_empty_input=fail."""
