"""Frozen dataclasses describing the mapping config contract (version 1.0)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceConfig:
    name: str
    path: str
    format: str
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ThroughList:
    """UIFPE-580: one list column spread into one row per element.

    `list` is `source.column`, an ARRAY column on a source already in the
    frame; `alias` is the name the element is addressed by afterwards —
    `alias.<field>` for an array of structs, `alias.value` for an array of
    scalars. Empty and null lists keep their row (outer explode), so a
    product without marketing copy is still exported once.
    """
    list: str
    alias: str

    def source_column(self) -> tuple[str, str]:
        source, _, column = self.list.partition(".")
        return source, column


@dataclass(frozen=True)
class JoinConfig:
    left: str
    right: str
    type: str
    condition: str
    # Optional Spark join hint applied to the right side, e.g. "broadcast"
    hint: str | None = None
    # UIFPE-580: a join THROUGH a list column — the list on the joined frame is
    # spread first, then `condition` matches its element to `right`
    # (product.marketing_copy[].content_id = marketing_copy.content_id). The
    # alternative, array_contains in the condition, is a nested-loop join.
    through: ThroughList | None = None


@dataclass(frozen=True)
class MappingRule:
    """One mapping rule. Which fields are required depends on `type`:

    one_to_one   source, target            (data_type optional)
    many_to_one  sources, target, expression
    one_to_many  source, targets, expressions
    literal      target, value
    derived      target, expression
    """
    type: str
    target: str | None = None
    targets: tuple[str, ...] = ()
    source: str | None = None
    sources: tuple[str, ...] = ()
    expression: str | None = None
    expressions: tuple[str, ...] = ()
    data_type: str | None = None
    value: Any = None

    def target_names(self) -> tuple[str, ...]:
        return self.targets if self.type == "one_to_many" else ((self.target,) if self.target else ())


@dataclass(frozen=True)
class ValidationRule:
    name: str
    condition: str


@dataclass(frozen=True)
class OutputTarget:
    format: str
    path: str
    mode: str = "overwrite"
    options: dict[str, str] = field(default_factory=dict)
    partition_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policies:
    on_empty_input: str = "fail"  # fail | skip | continue


@dataclass(frozen=True)
class MappingConfig:
    version: str
    sources: tuple[SourceConfig, ...]
    joins: tuple[JoinConfig, ...]
    mappings: tuple[MappingRule, ...]
    # Row filters: Spark SQL boolean conditions over the JOINED sources,
    # applied before mapping and validations. Filters define what is IN
    # SCOPE for the pipeline — rows failing a filter are silently excluded,
    # not quarantined (that distinction is validations' job).
    filters: tuple[str, ...] = ()
    validations: tuple[ValidationRule, ...] = ()
    rejected_path: str | None = None
    output_targets: tuple[OutputTarget, ...] = ()
    policies: Policies = field(default_factory=Policies)
    # UIFPE-580: list columns spread AFTER the joins and before the filters —
    # price.price_specifications becomes one row per price, so a filter on
    # the price list narrows rows the way a filter on stock's org does.
    unnest: tuple[ThroughList, ...] = ()

    def source_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.sources)

    def list_steps(self) -> tuple[tuple[str, ThroughList], ...]:
        """Every list spread the config declares, in execution order, with the
        config location it came from: joins' `through` first (in join order),
        then `unnest`."""
        steps = [(f"joins[{i}].through", j.through) for i, j in enumerate(self.joins) if j.through]
        steps += [(f"unnest[{i}]", u) for i, u in enumerate(self.unnest)]
        return tuple(steps)

    def element_aliases(self) -> tuple[str, ...]:
        return tuple(step.alias for _, step in self.list_steps())
