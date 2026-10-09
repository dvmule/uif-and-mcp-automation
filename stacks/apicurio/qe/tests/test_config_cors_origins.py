"""Regression coverage for CORS_ORIGINS env parsing.

Lifted from feature/apicurio-entra-federation-design (UIFPE-317 final review
F1) and adapted: that branch's copy also sets AAD_TENANT_ID / AAD_CLIENT_ID,
which do not exist on this branch.

pydantic-settings parses a complex-typed field's env value (cors_origins is a
list[str]) as JSON, not as a delimited string. bff.tf therefore emits
jsonencode(split(",", var.cors_origins)) rather than the bare comma-separated
variable -- see the comment there. Nothing else exercised this path: every
other test reaches Settings through TestClient, which only ever sees the field
default. These tests construct Settings directly from the environment, so an
edit that lets a bare comma-separated value reach Settings fails here instead
of at container start.

All four behaviours below were observed against pydantic-settings 2.9.1 before
being asserted.

What this does NOT guard: the Terraform side. Nothing in this Python suite can
see bff.tf, so reverting its `value = jsonencode(split(",", var.cors_origins))`
back to a bare `value = var.cors_origins` would not fail here, or anywhere else
in this suite -- it fails only at container start. The comment at that line in
bff.tf is the only thing guarding against that revert.
"""

import pytest
from pydantic_settings import SettingsError

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cors_origins_parses_the_json_array_bff_tf_emits(monkeypatch):
    """bff.tf emits jsonencode(split(",", var.cors_origins)) -- a JSON array."""
    monkeypatch.setenv(
        "CORS_ORIGINS",
        '["https://d111111abcdef8.cloudfront.net", "http://localhost:5173"]',
    )

    assert Settings().cors_origins == [
        "https://d111111abcdef8.cloudfront.net",
        "http://localhost:5173",
    ]


def test_a_bare_comma_separated_value_raises_rather_than_parsing(monkeypatch):
    """The container-killing case, and the reason bff.tf jsonencodes.

    SettingsError is raised from Settings() construction, which the BFF reaches
    via app.main:create_app -> get_settings. It therefore kills the process
    before Uvicorn binds a port, so ECS sees a task that exits rather than a
    task that fails its health check.
    """
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example,http://localhost:5173")

    with pytest.raises(SettingsError):
        Settings()


def test_an_empty_json_array_of_one_empty_string_closes_cors_silently(monkeypatch):
    """Why var.cors_origins carries no default.

    jsonencode(split(",", "")) is the JSON array [""], which pydantic-settings
    accepts. The BFF then starts healthily with one allowed origin that no
    browser can ever send, so CORS is closed and nothing says so. A variable
    with `default = ""` produces exactly this, which is why the variable is
    required instead.
    """
    monkeypatch.setenv("CORS_ORIGINS", '[""]')

    assert Settings().cors_origins == [""]


def test_cors_origins_default_applies_when_env_var_is_absent(monkeypatch, tmp_path):
    """No CORS_ORIGINS set at all -> the field default, not an error.

    chdir to a scratch directory first: Settings reads env_file=".env", so a
    developer's local .env would otherwise decide this assertion.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    assert Settings().cors_origins == ["http://localhost:5173"]
