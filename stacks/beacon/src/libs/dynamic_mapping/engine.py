"""The generic transformation engine. Receives a SparkSession — never creates one.

Stages (see main.run for orchestration):
  read_sources      -> aliased DataFrames so table-qualified refs resolve after joins
  apply_joins       -> folds joins in declaration order via F.expr(condition)
  dry_run_issues    -> compiles every rule against df.limit(0) (real schema, zero rows)
  compile_mappings  -> every rule becomes (alias, Column); one df.select() total
  apply_validations -> tags failing rows, splits valid / rejected
  write_outputs     -> fans one DataFrame out to every declared target
"""
from __future__ import annotations

from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.utils import AnalysisException

from . import lists
from .errors import ConfigValidationError, Issue
from .models import (JoinConfig, MappingConfig, MappingRule, OutputTarget, SourceConfig,
                     ThroughList, ValidationRule)

FAILED_RULES_COL = "_failed_rules"


def substitute(text: str, placeholders: dict[str, str]) -> str:
    for key, value in placeholders.items():
        text = text.replace("${" + key + "}", value)
    return text


def read_sources(spark: SparkSession, sources: tuple[SourceConfig, ...]) -> dict[str, DataFrame]:
    frames: dict[str, DataFrame] = {}
    for s in sources:
        reader = spark.read.format(s.format).options(**s.options)
        frames[s.name] = reader.load(s.path).alias(s.name)
    return frames


def prune_sources(frames: dict[str, DataFrame],
                  keep: dict[str, list[str]]) -> dict[str, DataFrame]:
    """Project each source down to the columns the config references
    (config_validator.collect_references), so the scan, the join, and any
    later persist carry config scope instead of full table width. A source
    with no collected references is left untouched. Re-aliasing is
    load-bearing: select() drops the frame's alias, and join conditions and
    mapping expressions address columns as <source>.<column>."""
    return {name: df.select(*keep[name]).alias(name) if keep.get(name) else df
            for name, df in frames.items()}


def element_schemas(frames: dict[str, DataFrame], config: MappingConfig) -> dict[str, list[str]]:
    """UIFPE-580: {element alias: field names} for the config's list steps,
    read from the frames' Spark schemas — merged into the source schemas so
    validate_references and collect_references see `alias.field`."""
    def fields_for(source: str, column: str) -> list[str] | None:
        df = frames.get(source)
        if df is None:
            return None
        matches = [f for f in df.schema.fields if f.name.lower() == column.lower()]
        return lists.element_fields_from_spark_type(matches[0].dataType) if len(matches) == 1 else None
    return lists.element_schemas(config, fields_for)


def explode_list(df: DataFrame, step: ThroughList) -> DataFrame:
    """UIFPE-580: one row per element of `step.list` — OUTER, so an empty or
    null list keeps its row with a null element (a product without marketing
    copy is still exported once). The element is addressed as
    `alias.<field>` for an array of structs and `alias.value` for an array of
    scalars. select("*", …) keeps every existing column WITH its source
    qualifier, so product.x still resolves after the spread."""
    source, column = step.source_column()
    col = F.col(f"{source}.{column}")
    fields = lists.element_fields_from_spark_type(df.select(col).schema[0].dataType)
    if fields is None:
        raise ConfigValidationError([Issue(step.list, f"{step.list!r} is not a list (array) column")])
    if fields == [lists.ELEMENT_VALUE]:
        tmp = f"__{step.alias}_elem"
        return (df.select("*", F.explode_outer(col).alias(tmp))
                  .withColumn(step.alias, F.struct(F.col(tmp).alias(lists.ELEMENT_VALUE)))
                  .drop(tmp))
    return df.select("*", F.explode_outer(col).alias(step.alias))


def apply_unnest(df: DataFrame, steps: tuple[ThroughList, ...]) -> DataFrame:
    """UIFPE-580: the config's `unnest` steps, after every join and before the
    filters — price.price_specifications becomes one row per price."""
    for step in steps:
        df = explode_list(df, step)
    return df


def apply_joins(frames: dict[str, DataFrame], joins: tuple[JoinConfig, ...],
                source_names: tuple[str, ...]) -> DataFrame:
    if not joins:
        return frames[source_names[0]]
    current = frames[joins[0].left]
    for j in joins:
        if j.through:
            # UIFPE-580: spread the list, then an ordinary equality join on
            # its element — never array_contains, which is a nested loop
            current = explode_list(current, j.through)
        right = frames[j.right]
        if j.hint:
            right = right.hint(j.hint)
        if j.type == "cross":
            current = current.crossJoin(right)
        else:
            current = current.join(right, F.expr(j.condition), j.type)
    return current


def apply_filters(df: DataFrame, filters: tuple[str, ...]) -> DataFrame:
    """Row filters: scope definition, applied to the joined frame BEFORE
    mapping and validations. Rows outside a filter are silently excluded —
    they are not failures and never reach the rejected path (that is
    validations' job)."""
    for condition in filters:
        df = df.filter(F.expr(condition))
    return df


def compile_mappings(mappings: tuple[MappingRule, ...],
                     placeholders: dict[str, str]) -> list[tuple[str, Any]]:
    """Compile every rule to (target_name, Column) pairs — consumed by ONE df.select()."""
    pairs: list[tuple[str, Any]] = []
    for m in mappings:
        match m.type:
            case "one_to_one":
                col = F.col(m.source)
                if m.data_type:
                    col = col.cast(m.data_type)
                pairs.append((m.target, col))
            case "many_to_one" | "derived":
                pairs.append((m.target, F.expr(m.expression)))
            case "one_to_many":
                pairs.extend(zip(m.targets, (F.expr(e) for e in m.expressions)))
            case "literal":
                value = substitute(m.value, placeholders) if isinstance(m.value, str) else m.value
                pairs.append((m.target, F.lit(value)))
            case other:
                raise ValueError(f"unknown rule type {other!r} — structure validation missed it")
    return pairs


def dry_run_issues(df: DataFrame, mappings: tuple[MappingRule, ...],
                   placeholders: dict[str, str]) -> list[Issue]:
    """Compile each rule against an empty slice with the real schema.

    Accessing .schema triggers Catalyst's analyser without reading data, so
    column typos and type mismatches surface here, named by rule index.
    """
    empty = df.limit(0)
    issues: list[Issue] = []
    for i, m in enumerate(mappings):
        try:
            pairs = compile_mappings((m,), placeholders)
            empty.select(*[col.alias(name) for name, col in pairs]).schema
        except AnalysisException as e:
            issues.append(Issue(f"mappings[{i}]", f"does not resolve: {e.desc.splitlines()[0]}"
                                if hasattr(e, "desc") else f"does not resolve: {e}"))
    return issues


def apply_validations(df: DataFrame,
                      validations: tuple[ValidationRule, ...]) -> tuple[DataFrame, DataFrame]:
    """Split into (valid, rejected). NULL conditions count as failures —
    a row only passes on an explicit true. Rejected rows carry FAILED_RULES_COL."""
    if not validations:
        return df, None
    flags = [
        F.when(~F.coalesce(F.expr(v.condition).cast("boolean"), F.lit(False)), F.lit(v.name))
        for v in validations
    ]
    tagged = df.withColumn(
        FAILED_RULES_COL, F.filter(F.array(*flags), lambda x: x.isNotNull())
    )
    valid = tagged.filter(F.size(FAILED_RULES_COL) == 0).drop(FAILED_RULES_COL)
    rejected = tagged.filter(F.size(FAILED_RULES_COL) > 0)
    return valid, rejected


def write_outputs(df: DataFrame, targets: tuple[OutputTarget, ...],
                  placeholders: dict[str, str]) -> None:
    """Write the same DataFrame to every target.

    With more than one target the DataFrame is persisted first — otherwise
    Spark recomputes the full DAG (including source reads) per .save() call.
    """
    multi = len(targets) > 1
    if multi:
        df = df.persist()
    try:
        for t in targets:
            writer = df.write.format(t.format).mode(t.mode).options(**t.options)
            if t.partition_by:
                writer = writer.partitionBy(*t.partition_by)
            writer.save(substitute(t.path, placeholders))
    finally:
        if multi:
            df.unpersist()
