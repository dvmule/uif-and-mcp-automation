# drift.py — gold drift detection (UIFPE-220).
#
# "Discovery is live, execution is frozen": the wizard reads the Glue catalog
# at request time, but a published version's mapping.json froze its table
# paths and column names at publish (D22). When gold moves — a value
# disappears, columns get renamed, a whole database migrates — the export
# fails at its next scheduled fire, and the operator finds out from a failed
# 00:30 batch. Three such incidents in the week of 8–15 Sep 2026 (ppid value,
# snake_case column rename, the uif_ database migration) are this module's
# reason to exist.
#
# The check reuses the submit-time machinery: resolve each frozen source path
# against the live catalog, then run the engine's own reference validation
# (the exact check publishing runs on drafts) over the LATEST published
# mapping config. Nothing here mutates anything — it answers one question:
# "would this export's next run survive the catalog as it is right now?"

from __future__ import annotations

from datetime import datetime, timezone


def check_version(version: dict, *, _columns_cache: dict | None = None) -> dict:
    """Drift status of one catalog version against the live gold catalog.

    Returns {job_id, name, version, status, issues} where status is:
      ok        — every frozen source resolves live and every referenced
                  column exists
      drift     — a source's database is no longer configured, its table is
                  gone from the catalog, or a referenced column disappeared
      unchecked — the catalog could not be consulted (offline, permissions);
                  said honestly instead of guessing either way
    """
    from control_panel import registries, validation

    mapping = version.get("mapping_config") or {}
    config_sources = mapping.get("sources") or []
    cache = _columns_cache if _columns_cache is not None else {}
    issues: list[dict] = []
    unchecked: list[dict] = []
    schemas: dict[str, list[str]] = {}
    types: dict[str, dict] = {}

    for source in config_sources:
        path = source.get("path") or ""
        alias = source.get("name") or path
        source_id = registries.source_id_for_path(path)
        if source_id is None:
            # today's incident shape: the config still points at a database
            # this deployment no longer reads (e.g. pre-migration names)
            issues.append({
                "where": f"sources.{alias}",
                "message": (f"{path} is not in any configured gold database "
                            "anymore — the export must be republished against "
                            "the current catalog")})
            continue
        if source_id not in cache:
            cache[source_id] = registries.source_columns(source_id)
        result = cache[source_id]
        if result.get("provenance") == "live" and result.get("columns"):
            schemas[alias] = result["columns"]
            types[alias] = result.get("types") or {}
        elif "EntityNotFoundException" in str(result.get("reason", "")):
            issues.append({
                "where": f"sources.{alias}",
                "message": f"table {path} no longer exists in the catalog"})
        else:
            unchecked.append({
                "where": f"sources.{alias}",
                "message": (f"could not verify {path} against the live "
                            f"catalog ({result.get('reason') or result.get('error') or 'no live columns'})")})

    # Column references — the engine's own validator, the same one publishing
    # runs, but against the FROZEN config. Only when every source resolved
    # live: a partial schema map would produce false unknown-source noise.
    columns_checked = False
    if not issues and not unchecked and schemas and validation.ENGINE_AVAILABLE:
        import dynamic_mapping
        from dynamic_mapping import lists
        try:
            config = dynamic_mapping.parse_config(mapping)
            # UIFPE-605: a spread list's element fields (price_spec.*) come
            # from the list column's catalogued type, exactly as at publish
            # (validation.py) — without them every element mapping read as
            # drift and the banner promised a failure the run would not have
            schemas.update(lists.element_schemas(
                config, lambda source, column: lists.element_fields_from_glue_type(
                    next((t for c, t in types.get(source, {}).items() if c.lower() == column.lower()), None))))
            issues += [{"where": i.where, "message": i.message}
                       for i in dynamic_mapping.validate_references(config, schemas)]
            columns_checked = True
        except dynamic_mapping.ConfigError as e:
            unchecked.append({"where": "config", "message": str(e)})

    status = "drift" if issues else ("unchecked" if unchecked or not schemas else "ok")
    return {"job_id": version.get("job_id"),
            "name": version.get("name"),
            "version": version.get("version"),
            "status": status,
            # ok with columns_checked=false means "tables exist, column-level
            # check unavailable" (engine not installed) — stated, not hidden
            "columns_checked": columns_checked,
            "issues": issues + unchecked}


def check_job(job_id: str) -> dict | None:
    """Drift status of one export's LATEST published version (schedules run
    the latest, so that is the version whose next fire is at stake)."""
    from control_panel import lifecycle

    latest = next((c for c in lifecycle.catalog()
                   if str(c.get("job_id")) == str(job_id)), None)
    if latest is None:
        return None
    result = check_version(latest)
    result["checked_at"] = _now()
    return result


def check_all() -> dict:
    """Every export's drift status in one call — the dashboard's view.
    Live column lookups are cached per source across exports, so ten exports
    on one gold table cost one catalog read."""
    from control_panel import lifecycle

    cache: dict = {}
    exports = [check_version(c, _columns_cache=cache)
               for c in lifecycle.catalog()]
    return {"checked_at": _now(),
            "drift_count": sum(1 for e in exports if e["status"] == "drift"),
            "exports": exports}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
