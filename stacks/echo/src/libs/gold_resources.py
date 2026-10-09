"""Gold layer resource names, per environment, on the UIF naming standard.

Every CLI, Lambda and template asks this module rather than spelling a name.
A missing ENV is a hard failure, never a fallback to dev (#15): a Lambda
deployed without ENV would otherwise address dev's table and jobs from every
environment and look healthy. Resolution is lazy so the failure lands on the
call that needs a name, not on import.
"""

from __future__ import annotations

import os


def _env_name() -> str:
    env = (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").strip()
    if not env:
        raise RuntimeError(
            "ENV (or ENVIRONMENT) is not set — refusing to guess an "
            "environment for gold resource names, because guessing would "
            "silently address the wrong account's resources. Set ENV on the "
            "Lambda (Terraform does this in dispatcher.tf) or pass env= "
            "explicitly."
        )
    return env


def _prefix(env: str | None) -> str:
    return f"uif-echo-{env or _env_name()}"


def gold_config_table(env: str | None = None) -> str:
    return f"{_prefix(env)}-gold-pipeline-config"


def gold_transform_job(env: str | None = None) -> str:
    return f"{_prefix(env)}-gold-transform"


def gold_reconcile_job(env: str | None = None) -> str:
    return f"{_prefix(env)}-gold-reconcile"


def gold_dispatch_function(env: str | None = None) -> str:
    return f"{_prefix(env)}-gold-dispatch"


def gold_announce_function(env: str | None = None) -> str:
    return f"{_prefix(env)}-gold-announce"


def gold_alert_notify_function(env: str | None = None) -> str:
    return f"{_prefix(env)}-gold-alert-notify"


def gold_alert_subscribers_table(env: str | None = None) -> str:
    """Who gets gold alert mail; rows are edited from the console, not Git."""
    return f"{_prefix(env)}-alert-subscribers"


def gold_alarm_prefix(env: str | None = None) -> str:
    """Every gold alarm's name starts with this (terraform-gold/alerts.tf)."""
    return f"{_prefix(env)}-gold-"


def event_bus(env: str | None = None) -> str:
    """The eventing layer control bus gold announces completed merges on."""
    return f"uif-event-{env or _env_name()}-bus"


def gold_artifacts_bucket(env: str | None = None) -> str:
    return f"{_prefix(env)}-gold-artifacts"


def gold_data_bucket(env: str | None = None) -> str:
    """The Iceberg warehouse."""
    return f"{_prefix(env)}-gold"


def gold_database(domain: str, env: str | None = None) -> str:
    """Glue database of one gold domain."""
    return f"uif_echo_{env or _env_name()}_gold_{domain}"


GOLD_LAYER_TAG_KEY = "uif-echo-gold-layer"


def gold_layer_tag(env: str | None = None) -> tuple[str, str]:
    """The Lake Formation tag every gold database carries: key and value."""
    return GOLD_LAYER_TAG_KEY, (env or _env_name())


MAINTENANCE_ARGS = ("--refresh_columns", "--rebuild")
"""Job arguments marking a run as MAINTENANCE, not a scheduled incremental.

Neither satisfies a schedule slot (gold_dispatcher): the slot belongs to the
incremental that would otherwise be skipped for the day.

It does NOT follow that the run is unpublishable — see writes_change_log.
This docstring used to claim "a column repair or a rebuild writes no change
log", which is false for an emit refresh and cost every refresh-detected
change its event.
"""

# The refresh mode a run gets when it carries no --refresh_mode argument.
# MUST match generic_gold_transform's own default, or a manually started
# refresh writes a change log that nothing announces.
DEFAULT_REFRESH_MODE_ARG = "emit"


def writes_change_log(args: dict) -> bool:
    """True when this run appends change-log rows, so its merge is worth
    announcing.

    An incremental always does. A --rebuild never does — it REPLACEs the table
    and notifies nobody, which is the whole reason a targeted refresh exists.
    A --refresh_columns run does in `emit` mode and does not in `silent` mode:
    generic_gold_transform appends op=REFRESH only under emit.

    Absent --refresh_mode means emit, mirroring the transform's own argument
    default and normalisation. qe pins the pair.
    """
    if "--rebuild" in args:
        return False
    if "--refresh_columns" in args:
        mode = args.get("--refresh_mode") or DEFAULT_REFRESH_MODE_ARG
        return str(mode).strip().lower() == "emit"
    return True
