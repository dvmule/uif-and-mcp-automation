"""Load a mapping config JSON file into frozen dataclasses.

The engine reads configs with plain open() — no AWS dependencies here.
Glue jobs download the config from S3 to a temp file first (see glue/glue_job.py).
"""
from __future__ import annotations

import json
from typing import Any

from .errors import ConfigError
from .models import (
    JoinConfig,
    MappingConfig,
    MappingRule,
    OutputTarget,
    Policies,
    SourceConfig,
    ThroughList,
    ValidationRule,
)

SUPPORTED_VERSIONS = ("1.0",)


def load_config(path: str) -> MappingConfig:
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        raise ConfigError(f"Cannot read config file {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"Config file {path} is not valid JSON: {e}") from e
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> MappingConfig:
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a JSON object")

    version = str(raw.get("version", ""))
    if version not in SUPPORTED_VERSIONS:
        raise ConfigError(
            f"Unsupported config version {version!r}; supported: {', '.join(SUPPORTED_VERSIONS)}"
        )

    try:
        sources = tuple(
            SourceConfig(
                name=s["name"],
                path=s["path"],
                format=s["format"],
                options=dict(s.get("options", {})),
            )
            for s in raw.get("sources", [])
        )
        joins = tuple(
            JoinConfig(
                left=j["left"],
                right=j["right"],
                type=j.get("type", "inner"),
                # UIFPE-167: a cross join has no condition — missing or null
                # normalises to "" so validate_structure (not a KeyError here)
                # decides whether the join type actually needs one
                condition=j.get("condition") or "",
                hint=j.get("hint"),
                through=_parse_list_step(j.get("through"), f"joins[{i}].through"),
            )
            for i, j in enumerate(raw.get("joins", []))
        )
        unnest_raw = raw.get("unnest", [])
        if not isinstance(unnest_raw, list):
            raise ConfigError("'unnest' must be a list of {list, alias} steps")
        unnest = tuple(_parse_list_step(u, f"unnest[{i}]") for i, u in enumerate(unnest_raw))
        mappings = tuple(_parse_rule(m) for m in raw.get("mappings", []))
        filters_raw = raw.get("filters", [])
        if not isinstance(filters_raw, list) or not all(isinstance(f, str) for f in filters_raw):
            raise ConfigError("'filters' must be a list of condition strings")
        filters = tuple(filters_raw)
        validations = tuple(
            ValidationRule(name=v["name"], condition=v["condition"])
            for v in raw.get("validations", [])
        )
        output_targets = tuple(
            OutputTarget(
                format=t["format"],
                path=t["path"],
                mode=t.get("mode", "overwrite"),
                options=dict(t.get("options", {})),
                partition_by=tuple(t.get("partition_by", [])),
            )
            for t in raw.get("output", {}).get("targets", [])
        )
        policies = Policies(
            on_empty_input=raw.get("policies", {}).get("on_empty_input", "fail")
        )
    except KeyError as e:
        raise ConfigError(f"Config is missing required key {e}") from e

    return MappingConfig(
        version=version,
        sources=sources,
        joins=joins,
        mappings=mappings,
        filters=filters,
        validations=validations,
        rejected_path=raw.get("rejected_path"),
        output_targets=output_targets,
        policies=policies,
        unnest=unnest,
    )


def _parse_list_step(raw: Any, where: str) -> ThroughList | None:
    """UIFPE-580: `{list: "source.column", alias: "name"}`; None when absent.
    Shape errors are ConfigError here (the config cannot even be read);
    validate_structure judges the names."""
    if raw is None:
        return None
    if (not isinstance(raw, dict) or not isinstance(raw.get("list"), str)
            or not isinstance(raw.get("alias"), str)):
        raise ConfigError(f"{where} must be an object {{list: 'source.column', alias: 'name'}}")
    return ThroughList(list=raw["list"], alias=raw["alias"])


def _parse_rule(m: dict[str, Any]) -> MappingRule:
    return MappingRule(
        type=m["type"],
        target=m.get("target"),
        targets=tuple(m.get("targets", [])),
        source=m.get("source"),
        sources=tuple(m.get("sources", [])),
        expression=m.get("expression"),
        expressions=tuple(m.get("expressions", [])),
        data_type=m.get("data_type"),
        value=m.get("value"),
    )
