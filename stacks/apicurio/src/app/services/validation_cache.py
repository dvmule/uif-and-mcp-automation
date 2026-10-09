"""In-process validation result cache keyed by (group_id, artifact_id).

Cache state is lost on restart and is not queryable outside this process. The
plan had been to move it into the KG's g:drift named graph; with the graph
removed (ADR-0007) it needs a different durable store if that matters.
"""

from __future__ import annotations

from datetime import datetime

from app.schemas.validation import ValidationResult


class ValidationCache:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], ValidationResult] = {}
        self._populated_at: datetime | None = None

    def is_empty(self) -> bool:
        return not self._data

    def get_all(self) -> list[ValidationResult]:
        return list(self._data.values())

    def get(self, group_id: str, artifact_id: str) -> ValidationResult | None:
        return self._data.get((group_id, artifact_id))

    def set(self, group_id: str, artifact_id: str, result: ValidationResult) -> None:
        self._data[(group_id, artifact_id)] = result

    def mark_populated(self, at: datetime) -> None:
        self._populated_at = at

    def clear(self) -> None:
        self._data.clear()
        self._populated_at = None

    @property
    def populated_at(self) -> datetime | None:
        return self._populated_at


_cache = ValidationCache()


def get_validation_cache() -> ValidationCache:
    return _cache
