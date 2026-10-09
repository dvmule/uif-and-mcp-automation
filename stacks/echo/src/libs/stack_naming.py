"""Resolve Echo's AWS resource names for a given environment, from the tfvars.

**This module is for CLI scripts and developer tooling only.** Code running
inside a Lambda or a Glue job has no repository checkout, and must keep using
``echo_resources.echo`` / ``echo_legacy``, which read ``RESOURCE_PREFIX`` and
``LEGACY_RESOURCE_PREFIX`` out of the environment. Terraform sets those.

Why read the tfvars rather than compute a name? Because there is no formula.
The CLI scripts assumed one — they hardcoded ``eds-{env}-*`` — and that is
right for prod and wrong for dev and uat:

    env    name_prefix     legacy_name_prefix
    dev    uif-echo-dev    echo-dev
    uat    echo-uat        echo-uat
    prod   eds-prod        eds-prod

prod was never migrated off the ``eds-`` name, which is exactly why the
hardcoding survived: it worked in the environment people were most careful
about. dev now carries two prefixes because of the UIFPE-355 rename, and the
lake buckets have their own variables because renaming a bucket that holds data
is destroy + create.

The two prefixes are not interchangeable:

* ``name_prefix`` — Glue jobs, IAM roles, Glue connections, Lambdas. Renamed.
* ``legacy_name_prefix`` — DynamoDB tables. Hold data, so not renamed yet.

``qe/libs/test_stack_naming.py`` asserts the fallbacks below still match
``locals.tf``, and that every bucket ``s3.tf`` declares has a value in every
env's tfvars.
"""

from __future__ import annotations

import os
import re

import gold_resources
from dataclasses import dataclass
from pathlib import Path

_TERRAFORM_DIR = Path(__file__).resolve().parents[2] / "infrastructure" / "terraform"
_ENVS_DIR = _TERRAFORM_DIR / "envs"


def _discover_environments() -> tuple[str, ...]:
    """The environments that exist, per the tfvars on disk.

    Derived rather than listed so it cannot drift: ADR-0015 D6 deleted
    staging.tfvars, and eleven CLI scripts went on offering `--env staging`
    while one of them omitted uat.
    """
    if not _ENVS_DIR.is_dir():
        return ()
    return tuple(sorted(p.stem for p in _ENVS_DIR.glob("*.tfvars")))


ENVIRONMENTS = _discover_environments()

# Keys in s3.tf, as `var.{kind}_bucket`. The gold buckets are NOT here: they
# belong to infrastructure/terraform-gold (ADR-0018), which names them itself,
# and this root only references them through a derived local.
#
# `release_artifacts` exists only on a uif-echo-* stack (UIFPE-515): s3.tf
# gates that bucket on local.is_uif_stack, so the legacy echo-dev / echo-uat /
# echo-prod tfvars carry no value for it. The comprehension below drops any
# kind an environment has no value for, so listing it here is safe — a legacy
# environment simply has three buckets and a uif one has four, and
# StackNames.bucket() raises for the missing kind rather than returning "".
_BUCKET_KINDS = ("artifacts", "bronze", "silver", "release_artifacts")

_ASSIGNMENT = re.compile(r'^\s*([A-Za-z_][\w-]*)\s*=\s*(.+?)\s*$')


def _strip_comment(value: str) -> str:
    """Drop a trailing `#` comment, but not a `#` inside a quoted string."""
    out, quote = [], None
    for ch in value:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).strip()


def _parse_tfvars(text: str) -> dict[str, str]:
    """Parse `key = value` lines. Enough for these files; not a HCL parser."""
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ASSIGNMENT.match(line)
        if not m:
            continue
        key, raw = m.group(1), _strip_comment(m.group(2))
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        parsed[key] = raw
    return parsed


# ── the three fallbacks, mirroring locals.tf ──────────────────────

def _fallback_name_prefix(environment: str) -> str:
    """locals.tf: `"echo-${var.environment}"`."""
    return f"echo-{environment}"


def _fallback_glue_db_prefix(environment: str) -> str:
    """locals.tf: `"echo_${var.environment}"` — underscores, it is a database."""
    return f"echo_{environment}"


def _resolve_legacy(override: str, name_prefix: str) -> str:
    """locals.tf: `legacy_name_prefix` falls back to `local.name_prefix`."""
    return override or name_prefix


@dataclass(frozen=True)
class StackNames:
    """Resolved names for one environment."""

    environment: str
    name_prefix: str
    legacy_name_prefix: str
    glue_db_prefix: str
    buckets: dict[str, str]

    def resource(self, slug: str) -> str:
        """A renamed resource: Glue job, IAM role, Glue connection, Lambda."""
        return f"{self.name_prefix}-{slug}"

    def legacy_resource(self, slug: str) -> str:
        """A data-holding resource that has not been renamed: DynamoDB tables."""
        return f"{self.legacy_name_prefix}-{slug}"

    def bucket(self, kind: str) -> str:
        """A bucket by kind — `bronze`, `silver`, `gold`, `artifacts`, ..."""
        try:
            return self.buckets[kind]
        except KeyError:
            raise ValueError(
                f"no {kind}_bucket in {self.environment}.tfvars; "
                f"known: {', '.join(sorted(self.buckets))}"
            ) from None

    # The three tables every Bronze/Silver tool needs, named once.
    @property
    def config_table(self) -> str:
        slug = "pipeline-config" if self.name_prefix.startswith("uif-echo") else "glue-job-config"
        return self.legacy_resource(slug)

    @property
    def metrics_table(self) -> str:
        slug = "pipeline-metrics" if self.name_prefix.startswith("uif-echo") else "glue-job-metrics"
        return self.legacy_resource(slug)

    @property
    def analysis_table(self) -> str:
        return self.legacy_resource("pipeline-analysis")

    @property
    def gold_config_table(self) -> str:
        """The gold pipeline config table.

        Delegated, not derived: gold was renamed on its own timetable
        (ADR-0018) and `gold_resources` is the single authority for its names,
        shared with the dispatcher, the CLIs and the Terraform local. Spelling
        it a second time here is how the two drift apart.
        """
        return gold_resources.gold_config_table(self.environment)


# ── control-plane API base URLs ───────────────────────────────────
# Three scripts each kept their own hand-written map of these, and two of the
# three omitted uat — so `--env uat` resolved to an empty base URL and the
# script failed on a request it should never have attempted. Generated now.

def api_base(environment: str) -> str:
    """Base URL for an environment's control-plane API, from the shell.

    Both existing conventions are honoured, for every environment rather than
    only the ones that happened to be spelled out before:

      * ``EDS_API_BASE`` (dev) / ``EDS_API_BASE_UAT`` / ``EDS_API_BASE_PROD``
      * ``UAT_API_URL`` / ``PROD_API_URL`` — the older aliases

    Returns "" when unset; callers already report that as a usage error.
    """
    suffix = "" if environment == "dev" else f"_{environment.upper()}"
    return (
        os.environ.get(f"EDS_API_BASE{suffix}")
        or os.environ.get(f"{environment.upper()}_API_URL")
        or ""
    )


def api_endpoints() -> dict[str, str]:
    """``{environment: base_url}`` for every environment that exists."""
    return {env: api_base(env) for env in ENVIRONMENTS}


def load(environment: str, envs_dir: Path | None = None) -> StackNames:
    """Read `{environment}.tfvars` and resolve every name it implies."""
    envs = envs_dir or _ENVS_DIR
    path = envs / f"{environment}.tfvars"
    if not path.is_file():
        known = ", ".join(_discover_environments() or ("<none found>",))
        raise ValueError(
            f'unknown environment "{environment}" — no {path.name} in {envs}. '
            f"Known environments: {known}."
        )

    v = _parse_tfvars(path.read_text(encoding="utf-8"))
    env = v.get("environment", environment)

    name_prefix = v.get("name_prefix_override") or _fallback_name_prefix(env)
    return StackNames(
        environment=env,
        name_prefix=name_prefix,
        legacy_name_prefix=_resolve_legacy(
            v.get("legacy_name_prefix_override", ""), name_prefix
        ),
        glue_db_prefix=(
            v.get("glue_db_prefix_override") or _fallback_glue_db_prefix(env)
        ),
        buckets={
            kind: v[f"{kind}_bucket"]
            for kind in _BUCKET_KINDS
            if v.get(f"{kind}_bucket")
        },
    )
