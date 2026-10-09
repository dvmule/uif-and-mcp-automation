"""Pipeline draft JSON locations: drafts/{env}/{cloud}/…"""

from __future__ import annotations

from pathlib import Path

_FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent
DRAFTS_ROOT = _FRAMEWORK_ROOT / "drafts"

DEFAULT_DRAFT_ENV = "dev"


def env_root(env: str = DEFAULT_DRAFT_ENV) -> Path:
    return DRAFTS_ROOT / env


def gcp_eds(env: str = DEFAULT_DRAFT_ENV) -> Path:
    return env_root(env) / "gcp" / "eds"


def gcp_rnp(env: str = DEFAULT_DRAFT_ENV) -> Path:
    return env_root(env) / "gcp" / "rnp"


def gcp_ipm(env: str = DEFAULT_DRAFT_ENV) -> Path:
    return env_root(env) / "gcp" / "ipm"


def aws_root(env: str = DEFAULT_DRAFT_ENV) -> Path:
    return env_root(env) / "aws"
