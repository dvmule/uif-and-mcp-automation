# validation.py
# The panel's Validate button, as an API (design doc §10.4). Two contracts:
#   - export configs: validated by the shared beacon package — the exact
#     code the pipeline container runs, so the panel can never accept what
#     the pipeline would reject.
#   - mapping configs: validated by the dynamic-mapping engine's OWN static
#     validators (structure + sqlglot expression allowlist) when the engine
#     is importable. The engine is Spark-free for these checks by design.
#     When it isn't installed (e.g. a minimal dev venv), the API says so
#     explicitly instead of pretending it validated.

from __future__ import annotations

from beacon_export import export_config

try:
    import dynamic_mapping
    ENGINE_AVAILABLE = True
except ImportError:
    dynamic_mapping = None
    ENGINE_AVAILABLE = False


def validate_export_config(body: dict) -> dict:
    error = export_config.validate(body)
    return {"valid": error is None, "error": error}


def validate_mapping_config(body: dict, fmt: str | None = None) -> dict:
    if not ENGINE_AVAILABLE:
        return {"valid": False, "engine_available": False, "issues": [
            {"where": "engine", "message":
             "dynamic_mapping is not installed in this deployment; full "
             "static validation was NOT performed"}]}
    try:
        config = dynamic_mapping.parse_config(body)
    except dynamic_mapping.ConfigError as e:
        return {"valid": False, "engine_available": True,
                "issues": [{"where": "config", "message": str(e)}]}
    issues = (dynamic_mapping.validate_structure(config)
              + dynamic_mapping.validate_expressions(config))

    # Column references checked at SUBMIT time using LIVE source schemas
    # (Glue catalog / CSV header) when every source is resolvable — so
    # "column 'inventory_item_id' not found (has: inventoryitemid, ...)"
    # appears in the Review step, not as a failed run twenty minutes later.
    if not issues:
        resolved = _live_schemas(body.get("sources", []))
        if resolved is not None:
            # Existence is checked against EVERY catalogued column, eligible or
            # not, so "not found" keeps meaning "not in the table" (UIFPE-540).
            # Whether a column MAY be exported is a separate question, asked
            # next, so its refusal can name the real reason.
            schemas = {alias: r["columns"] + sorted(r["excluded"])
                       for alias, r in resolved.items()}
            # UIFPE-580: a spread list's element fields come from the
            # catalogued type of the list column (array<struct<...>>), so
            # `product_copy.content_id` is checked like any source column
            from dynamic_mapping import lists

            def _fields(source, column):
                types = (resolved.get(source) or {}).get("types") or {}
                hit = next((t for c, t in types.items() if c.lower() == column.lower()), None)
                return lists.element_fields_from_glue_type(hit)

            schemas.update(lists.element_schemas(config, _fields))
            issues += dynamic_mapping.validate_references(config, schemas)
            issues += complex_column_issues(config, {a: r["types"] for a, r in resolved.items()}, fmt)
            if not issues:
                issues += _channel_issues(config, resolved, schemas)

    return {"valid": not issues, "engine_available": True,
            "issues": [{"where": i.where, "message": i.message} for i in issues]}


COMPLEX_PREFIXES = ("array<", "struct<", "map<")


def complex_column_issues(config, types_by_alias: dict, fmt: str | None) -> list:
    """UIFPE-609: a list/record column mapped BARE into a CSV export dies at
    the run's rendering guard (renderers.complex_typed_columns — a CSV cell
    cannot carry it) after filter, join and mapping have already run. Say so
    at Validate, Publish and Preview instead, with the remedy. JSON and XML
    serialize nested values natively, so nothing is flagged for them. Only
    one_to_one mappings are typed here: an expression's type is Spark's to
    know, and to_json(...) — the remedy — yields a string."""
    if fmt != "csv":
        return []
    issues = []
    for i, m in enumerate(config.mappings):
        if m.type != "one_to_one" or not m.source:
            continue
        alias, _, column = m.source.partition(".")
        types = types_by_alias.get(alias) or {}
        t = str(next((v for c, v in types.items() if c.lower() == column.lower()), ""))
        if t.lower().startswith(COMPLEX_PREFIXES):
            kind = t.split("<", 1)[0].lower()
            issues.append(dynamic_mapping.Issue(
                f"mappings[{i}]",
                f"{m.source} is {'an' if kind == 'array' else 'a'} {kind} — a CSV cell cannot carry it; "
                f"map it as to_json({m.source}) instead"))
    return issues


def _channel_issues(config, resolved: dict, schemas: dict) -> list:
    """Refuse a config that exports a column the gold master permits on no
    channel, and refuse one whose eligibility could not be established.

    Beacon is the message channel; a column marked `x-channels: []` is a
    source-system surrogate that rule N7 keeps off every external surface.

    This gate fails CLOSED when a gold source's master is unreadable, which is
    deliberately the opposite of the reference check above — that one skips
    rather than risk a false rejection, because a missed reference costs a
    failed run. A missed eligibility check costs a partner receiving internal
    keys, so the two failures are not worth the same and the postures differ.
    """
    issues = [
        dynamic_mapping.Issue(
            f"sources[{alias}]",
            f"cannot verify channel eligibility for {alias!r}: its gold master "
            f"could not be read, so beacon cannot prove the selected columns "
            f"are exportable")
        for alias, r in resolved.items() if r["unverified"]
    ]
    if issues:
        return issues

    # Reached by module path, as main.py and the engine tests do — it is not
    # part of the package's public surface, and this reuses its walk rather
    # than adding a second one.
    from dynamic_mapping.config_validator import collect_references

    referenced = collect_references(config, schemas)
    for alias, used in referenced.items():
        denied = sorted(c for c in used if c in resolved[alias]["excluded"])
        for column in denied:
            issues.append(dynamic_mapping.Issue(
                f"sources[{alias}]",
                f"column {column!r} is not exportable: {alias!r}'s gold master "
                f"declares it on no channel (x-channels: []), so it is internal "
                f"to the platform"))
    return issues


def _live_schemas(config_sources: list[dict]) -> dict[str, dict] | None:
    """Map each config source (by path) to its live introspection result. A
    path resolves through its sources.yaml row or, since UIFPE-167, as a
    gold-catalog table (glue_catalog.<db>.<table> in a configured domain) — so
    join keys on catalog-picked tables are checked at submit too. Returns None
    unless EVERY source resolves live: validate_references reports any alias
    missing from the map as an unknown source, so a partial or fallback schema
    would produce false rejections rather than fewer checks.

    Each entry carries the eligible `columns`, the `excluded` set the entity's
    gold master permits on no channel, and whether eligibility was verifiable
    at all (UIFPE-540)."""
    from control_panel import registries

    schemas: dict[str, dict] = {}
    for source in config_sources:
        source_id = registries.source_id_for_path(source.get("path"))
        if source_id is None:
            return None
        result = registries.source_columns(source_id)
        if result.get("provenance") != "live" or not result.get("columns"):
            return None
        schemas[source["name"]] = {
            "columns": result["columns"],
            "excluded": set(result.get("excluded", [])),
            "unverified": result.get("eligibility") == "unverified",
            "types": result.get("types") or {},   # UIFPE-580: list element fields
        }
    return schemas or None
