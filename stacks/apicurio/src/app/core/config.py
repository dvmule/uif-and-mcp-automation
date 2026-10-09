"""Application settings loaded from environment / .env.

All external endpoints, secrets, and runtime knobs live here.
Never hard-code Apicurio URLs anywhere else in the codebase.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Apicurio Registry (v3)
    apicurio_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL of the Apicurio Registry instance",
    )
    apicurio_api_path: str = Field(
        default="/apis/registry/v3",
        description=(
            "REST API path on Apicurio. v3 as of UIFPE-36 — the registry image is "
            "apicurio/apicurio-registry:3.3.2, and /apis/registry/v2 does not exist "
            "on a v3 server. This is the seam if the version ever moves again."
        ),
    )
    apicurio_request_timeout_seconds: float = Field(
        default=10.0,
        description="HTTP timeout for individual Apicurio calls",
    )

    # NOTE: oxigraph_base_url, twin_sparql_token, and
    # extractor_poll_interval_seconds were removed with the RDF Knowledge Graph
    # (ADR-0007). Deployments still setting OXIGRAPH_BASE_URL are harmless —
    # pydantic-settings ignores unknown env vars — but the value has no effect.

    # CMDB — the only implementation is a mock
    cmdb_base_url: str = Field(
        default="mock://cmdb",
        description="CMDB endpoint. Use 'mock://cmdb' to enable the mock client (the default).",
    )

    # Logging
    log_level: str = Field(default="INFO")

    # Delivery Lab
    # SSM parameter name used in ECS (e.g. "/pgi-sit-cdc-poller/enrollment").
    # When set, the BFF reads/writes config via SSM instead of a local file.
    # Leave empty for local dev — falls back to cdc_tables_path.
    cdc_tables_ssm_parameter: str = Field(
        default="",
        description=(
            "SSM Parameter Store path for the CDC enrollment config. "
            "Set in ECS; empty falls back to cdc_tables_path."
        ),
    )
    cdc_tables_path: str = Field(
        default="/app/config/enrollment.json",
        description=(
            "Local file path for CDC config — used only when "
            "cdc_tables_ssm_parameter is empty."
        ),
    )

    # CORS
    cors_origins: list[str] = Field(default=["http://localhost:5173"])

    @property
    def apicurio_api_url(self) -> str:
        """Full URL prefix for Apicurio v2 API calls."""
        return f"{self.apicurio_base_url.rstrip('/')}{self.apicurio_api_path}"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — instantiated once per process."""
    return Settings()
