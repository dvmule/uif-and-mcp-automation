"""List columns spread into rows (UIFPE-580).

A Gold row often carries its relationships as a list: product.marketing_copy
is an array of structs whose content_id names a marketing-copy row,
product.associated_programs an array of program PPIDs, price.price_specifications
one element per price. To join THROUGH such a list, or to export one row per
element, the engine spreads the list first (explode) and then treats the
element as a source-like alias: `alias.<field>` for an array of structs,
`alias.value` for an array of scalars.

This module is Spark-free: it knows how to read a list column's ELEMENT
schema from either a Glue type string (the control panel, at submit time)
or a Spark DataType (the engine, at run time), so validate_references can
check `alias.field` the same way on both sides.
"""
from __future__ import annotations

import re
from typing import Callable

from .models import MappingConfig

#: The one field of a spread scalar list (array<string> etc.).
ELEMENT_VALUE = "value"

_ARRAY_RE = re.compile(r"^array\s*<(.*)>$", re.IGNORECASE | re.DOTALL)
_STRUCT_RE = re.compile(r"^struct\s*<(.*)>$", re.IGNORECASE | re.DOTALL)


def _split_top_level(body: str) -> list[str]:
    """Split `a:int,b:struct<c:int,d:int>,c:decimal(38, 2)` on the commas that
    are not inside angle brackets or parentheses."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return [p for p in parts if p.strip()]


split_top_level = _split_top_level   # the control panel walks Glue types too (UIFPE-605)


def element_field_types_from_glue_type(type_str: str | None) -> dict[str, str] | None:
    """{field name: Glue type} a spread element exposes, from a Glue/Hive type
    string: `array<struct<content_id:string,...>>` -> the struct's fields in
    order; `array<string>` -> {ELEMENT_VALUE: "string"}; anything that is not
    an array -> None. The preview renders each field by its type (complex
    ones as JSON) exactly like a source column."""
    m = _ARRAY_RE.match((type_str or "").strip())
    if not m:
        return None
    inner = m.group(1).strip()
    s = _STRUCT_RE.match(inner)
    if not s:
        return {ELEMENT_VALUE: inner}
    fields: dict[str, str] = {}
    for part in _split_top_level(s.group(1)):
        name, _, ftype = part.partition(":")
        name = name.strip().strip("`")
        if name:
            fields[name] = ftype.strip()
    return fields


def element_fields_from_glue_type(type_str: str | None) -> list[str] | None:
    """Field names only — see element_field_types_from_glue_type."""
    typed = element_field_types_from_glue_type(type_str)
    return None if typed is None else list(typed)


def element_fields_from_spark_type(dtype) -> list[str] | None:
    """The same answer from a pyspark DataType (imported here so this module
    stays importable without Spark)."""
    from pyspark.sql.types import ArrayType, StructType

    if not isinstance(dtype, ArrayType):
        return None
    element = dtype.elementType
    return [f.name for f in element.fields] if isinstance(element, StructType) else [ELEMENT_VALUE]


def element_schemas(config: MappingConfig,
                    fields_for: Callable[[str, str], list[str] | None]) -> dict[str, list[str]]:
    """{element alias: field names} for every list step the config declares.
    `fields_for(source, column)` answers from whatever schema the caller has
    (Glue types or Spark types); an unknown or non-list column yields no
    entry, and validate_references then reports the alias as unresolvable
    rather than guessing."""
    out: dict[str, list[str]] = {}
    for _where, step in config.list_steps():
        source, column = step.source_column()
        fields = fields_for(source, column) if source and column else None
        if fields:
            out[step.alias] = list(fields)
    return out
