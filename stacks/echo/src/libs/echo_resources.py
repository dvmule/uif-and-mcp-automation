"""AWS resource names for the Echo stack.

The prefixes are READ FROM THE ENVIRONMENT, not computed here (UIFPE-355).

Terraform passes `RESOURCE_PREFIX` and `LEGACY_RESOURCE_PREFIX` to every Lambda
from `local.name_prefix` and `local.legacy_name_prefix`. That matters because the
same code deploys to all three environments while the `uif-` rename is phased:

    dev   RESOURCE_PREFIX=uif-echo-dev   LEGACY_RESOURCE_PREFIX=echo-dev
    uat   RESOURCE_PREFIX=echo-uat       LEGACY_RESOURCE_PREFIX=echo-uat
    prod  RESOURCE_PREFIX=eds-prod       LEGACY_RESOURCE_PREFIX=eds-prod

Hardcoding either prefix would break the two environments that have not moved.

**Which function to call.** `echo()` is for resources that have been renamed —
Glue jobs, Lambdas, layers, EventBridge rules, IAM. `echo_legacy()` is for
resources that hold data and so cannot be renamed by a name change alone:
renaming a DynamoDB table in Terraform is destroy + create and takes the rows
with it. Those stay on the old prefix until each has a migration behind it.

Where the two prefixes are equal (uat, prod) the choice is a no-op, so calling
the right one is only load-bearing in dev — which is exactly where it is easiest
to get wrong and hardest to notice. When the last stateful resource has migrated,
delete `echo_legacy()` and the Terraform local behind it.
"""

from __future__ import annotations

import os

_ENV = os.environ.get("ENV", os.environ.get("ENVIRONMENT", "dev"))


def _env_name() -> str:
    return os.environ.get("ENV", os.environ.get("ENVIRONMENT", _ENV))


def echo(slug: str, env: str | None = None) -> str:
    """A renamed resource: {RESOURCE_PREFIX}-{slug}.

    e.g. echo('bronze-ingest') -> uif-echo-dev-bronze-ingest

    Falls back to the pre-rename form when RESOURCE_PREFIX is unset, so tests and
    local scripts keep working without the Lambda environment.
    """
    prefix = os.environ.get("RESOURCE_PREFIX")
    if prefix:
        return f"{prefix}-{slug}"
    return f"echo-{env or _env_name()}-{slug}"


def echo_legacy(slug: str, env: str | None = None) -> str:
    """A resource that holds data and has not been renamed: {LEGACY_RESOURCE_PREFIX}-{slug}.

    e.g. echo_legacy('glue-job-config') -> echo-dev-glue-job-config

    Use for the DynamoDB tables. Falls back to RESOURCE_PREFIX's default the same
    way `echo()` does.
    """
    prefix = os.environ.get("LEGACY_RESOURCE_PREFIX")
    if prefix:
        return f"{prefix}-{slug}"
    return f"echo-{env or _env_name()}-{slug}"


def echo_glue_db(domain: str, layer: str = "silver", env: str | None = None) -> str:
    """Glue catalog database: {prefix}_{layer}_{domain}, e.g. echo_dev_silver_product.

    Legacy, and unused: nothing outside this module's own test calls it. The
    Silver rename this docstring once said was still pending has happened —
    dev's four domain-keyed databases became uif_echo_dev_silver_mdm and
    _tep, keyed by source system — so the form derived here no longer names a
    database that exists. Read Silver names from the pipeline config, which
    carries them per source.
    """
    e = env or _env_name()
    prefix = os.environ.get("GLUE_DB_PREFIX")
    if prefix:
        return f"{prefix}_{layer}_{domain}"
    return f"echo_{e}_{layer}_{domain}"
