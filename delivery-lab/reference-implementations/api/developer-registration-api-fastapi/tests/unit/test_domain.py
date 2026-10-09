"""Unit tests: pure domain logic, no network. Fast, run on every commit."""
import pytest
from pydantic import ValidationError

from app.domain import RegisterDeveloperRequest, normalize_email


def test_accepts_valid_payload():
    req = RegisterDeveloperRequest(email="ada@example.com", displayName="Ada")
    assert req.email == "ada@example.com"


def test_rejects_invalid_email():
    with pytest.raises(ValidationError):
        RegisterDeveloperRequest(email="nope", displayName="Ada")


def test_rejects_empty_display_name():
    with pytest.raises(ValidationError):
        RegisterDeveloperRequest(email="ada@example.com", displayName="")


def test_rejects_overlong_display_name():
    with pytest.raises(ValidationError):
        RegisterDeveloperRequest(email="ada@example.com", displayName="a" * 121)


def test_rejects_unknown_field():
    with pytest.raises(ValidationError):
        RegisterDeveloperRequest(email="ada@example.com", displayName="Ada", role="admin")


def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  Ada@Example.COM ") == "ada@example.com"
