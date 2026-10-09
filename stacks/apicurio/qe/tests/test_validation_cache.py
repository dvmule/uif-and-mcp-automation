"""Unit tests for ValidationCache."""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.validation import ValidationResult
from app.services.validation_cache import ValidationCache


def _result(
    artifact_id: str = "product.product.created-value",
    group_id: str = "default",
) -> ValidationResult:
    return ValidationResult(
        artifact_id=artifact_id,
        group_id=group_id,
        passed=True,
        failures=[],
        validated_at=datetime.now(tz=UTC),
    )


def test_new_cache_is_empty() -> None:
    cache = ValidationCache()
    assert cache.is_empty()
    assert cache.get_all() == []
    assert cache.populated_at is None


def test_set_and_get() -> None:
    cache = ValidationCache()
    r = _result()
    cache.set("default", "product.product.created-value", r)
    assert cache.get("default", "product.product.created-value") is r


def test_get_missing_returns_none() -> None:
    cache = ValidationCache()
    assert cache.get("default", "does-not-exist") is None


def test_get_all_returns_all_entries() -> None:
    cache = ValidationCache()
    r1 = _result("product.product.created-value")
    r2 = _result("content.asset.published-value")
    cache.set("default", r1.artifact_id, r1)
    cache.set("default", r2.artifact_id, r2)
    assert len(cache.get_all()) == 2


def test_is_empty_false_after_set() -> None:
    cache = ValidationCache()
    cache.set("default", "product.product.created-value", _result())
    assert not cache.is_empty()


def test_clear_resets_data_and_timestamp() -> None:
    cache = ValidationCache()
    cache.set("default", "product.product.created-value", _result())
    cache.mark_populated(datetime.now(tz=UTC))
    cache.clear()
    assert cache.is_empty()
    assert cache.populated_at is None


def test_mark_populated_sets_timestamp() -> None:
    cache = ValidationCache()
    now = datetime.now(tz=UTC)
    cache.mark_populated(now)
    assert cache.populated_at == now


def test_overwrite_existing_key() -> None:
    cache = ValidationCache()
    r1 = _result()
    r2 = _result()
    cache.set("default", "product.product.created-value", r1)
    cache.set("default", "product.product.created-value", r2)
    assert cache.get("default", "product.product.created-value") is r2
    assert len(cache.get_all()) == 1
