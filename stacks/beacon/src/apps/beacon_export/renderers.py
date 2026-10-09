# renderers.py
# Renderer registry: which output formats Beacon can emit, how each maps to
# Spark writer options, and what per-format options are legal. The actual
# DataFrame writes happen in beacon_export_job.py (the only Spark-aware
# module); everything here is pure so qe/ can test it without Spark.
#
# Adding a data format = add a registry entry here (renderer plugin, design
# decision D3).
#
# XML rides the spark-xml data source (external package on Spark 3.5 —
# version-pinned with the Spark version, design risk R4). The Glue job loads
# it via --extra-jars (glue.tf, UIFPE-250); the retired Fargate image baked it
# in instead. Entity escaping of field content comes from the writer; never
# string-build XML.

from __future__ import annotations

import re

_CSV_ALLOWED = {"delimiter", "header", "quote_all", "encoding", "formula_escape"}
_JSON_ALLOWED = {"style"}
_XML_ALLOWED = {"root_tag", "row_tag", "declaration", "encoding"}

# UIFPE-586: the author names the wrapper and record elements so a partner's
# own document shape can be delivered. Deliberately narrower than the XML
# spec: no colon, because a prefixed name needs a namespace declaration this
# renderer cannot emit — `dynamic_mapping.xml_paths` excludes ":" for exactly
# the same reason. An invalid name must be refused here rather than handed to
# the writer, which would emit a malformed document.
_XML_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

# Prefixes that make a cell executable when a partner opens the CSV in Excel.
# Applied by the job when formula_escape is on (default) — see escape_plan().
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t")

# Delimiter selection (UIFPE-171): configs carry a symbolic name — readable in
# the registry-driven UI dropdown and unambiguous in JSON — resolved to the
# separator character here. Literal characters remain accepted because
# published versions are immutable and pre-date the names.
DELIMITERS = {"comma": ",", "tab": "\t", "pipe": "|"}


def _resolve_delimiter(value) -> str | None:
    """Separator character for a delimiter value, or None if unsupported."""
    if not isinstance(value, str):
        return None  # config values are author-controlled JSON — never raise
    if value in DELIMITERS:
        return DELIMITERS[value]
    if value in DELIMITERS.values():
        return value
    return None


def complex_typed_columns(fmt: str, dtypes: list) -> list[str]:
    """Column names whose Spark type `fmt` cannot serialize (UIFPE-237).

    Spark's csv writer raises an opaque AnalysisException on array/struct/map
    columns AFTER the quality gate has passed (D4 makes that late failure
    recorded but unexplained); the job calls this pre-render to fail with an
    actionable message instead. `dtypes` is df.dtypes: [(name, typeString)].
    json and xml serialize nested types natively — nothing flagged for them."""
    if fmt != "csv":
        return []
    return [name for name, dtype in dtypes
            if dtype.startswith(("array<", "struct<", "map<"))]

REGISTRY = {
    "csv": {"extension": "csv", "spark_format": "csv", "allowed": _CSV_ALLOWED},
    "json": {"extension": "json", "spark_format": "json", "allowed": _JSON_ALLOWED},
    "xml": {"extension": "xml", "spark_format": "xml", "allowed": _XML_ALLOWED},
}


def validate_rendering(rendering: dict) -> str | None:
    """None when valid, else the reason. Mirrors export_config.validate style."""
    if not isinstance(rendering, dict):
        return "rendering must be an object"
    fmt = rendering.get("format")
    if fmt not in REGISTRY:
        return f"unknown format {fmt!r} (registered: {sorted(REGISTRY)})"
    options = rendering.get("options", {})
    if not isinstance(options, dict):
        return "options must be an object"
    unknown = set(options) - REGISTRY[fmt]["allowed"]
    if unknown:
        return (f"format {fmt!r} does not accept option(s) {sorted(unknown)} "
                f"(allowed: {sorted(REGISTRY[fmt]['allowed'])})")
    if fmt == "csv":
        delimiter = options.get("delimiter", "comma")
        if _resolve_delimiter(delimiter) is None:
            return (f"csv delimiter {delimiter!r} is not supported "
                    f"(choose one of: {sorted(DELIMITERS)})")
    if fmt == "json" and options.get("style", "jsonlines") != "jsonlines":
        return "json style 'jsonlines' is the only style supported in Phase 1"
    if fmt == "xml":
        for key in ("root_tag", "row_tag"):
            if key not in options:
                continue  # absent = the renderer's default (rows / row)
            name = options[key]
            if not isinstance(name, str) or not _XML_NAME.match(name):
                return (f"xml {key} {name!r} is not a valid element name — "
                        "letters, digits, '_', '.' and '-' only, starting "
                        "with a letter or '_', and no ':' (namespaces are "
                        "not supported)")
    return None


def spark_write_options(rendering: dict) -> dict:
    """Spark DataFrameWriter options for a validated rendering. Safe defaults:
    UTF-8, header on, quote-all (embedded delimiters/newlines survive)."""
    fmt = rendering["format"]
    options = rendering.get("options", {})
    if fmt == "csv":
        return {
            "sep": _resolve_delimiter(options.get("delimiter", "comma")),
            "header": str(options.get("header", True)).lower(),
            "quoteAll": str(options.get("quote_all", True)).lower(),
            "encoding": options.get("encoding", "UTF-8"),
            "escape": '"',
        }
    if fmt == "json":
        return {}  # Spark's json writer is JSON Lines already
    if fmt == "xml":
        return {
            "rootTag": options.get("root_tag", "rows"),
            "rowTag": options.get("row_tag", "row"),
            "declaration": ('version="1.0" encoding="'
                            + options.get("encoding", "UTF-8") + '"')
                           if options.get("declaration", True) else "",
            "charset": options.get("encoding", "UTF-8"),
        }
    raise ValueError(f"unknown format {fmt!r}")


def formula_escape_enabled(rendering: dict) -> bool:
    """CSV cells starting with =, +, -, @ execute as formulas in Excel.
    Escaping is ON unless the config explicitly opts out."""
    if rendering["format"] != "csv":
        return False
    return bool(rendering.get("options", {}).get("formula_escape", True))


def extension(rendering: dict) -> str:
    return REGISTRY[rendering["format"]]["extension"]
