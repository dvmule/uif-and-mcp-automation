#!/usr/bin/env python3
# generate_tier_subsets.py
# Reads a Gold master JSON Schema and emits 3 tier subsets:
#   {entity}/event/v1.json    — properties tagged x-channels including "event"
#   {entity}/message/v1.json  — properties tagged "message" (or "event", since event ⊂ message)
#   {entity}/api/v1.json      — properties tagged "api" (full superset)
#
# Asserts the concentric invariant: event ⊂ message ⊂ api.
# Used by CI (post-merge) before publishing to Apicurio.

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

#: D21 (normative, Confluence ITSOC) requires every artifact to declare a
#: criticality tier at registration: T1 critical, T2 standard, T3 observational.
#: T2 is the repository default because it is the least-claiming of the three —
#: T1 asserts business-critical without evidence and T3 forfeits statusing and
#: retention. Tier drives retention, DLQ alerting and parallel-run windows, so
#: it is a DATA OWNER's call, not a generator's; it is registry metadata and
#: changeable without a schema version bump, so correcting it later is cheap.
#: Owner sign-off is tracked on UIFPE-574 before anything registers.
REGISTRY_TIER = "T2"

TIERS = ("event", "message", "api")

#: Where a projection is COMMITTED, per ADR-0022 decision 4. Every tier lands
#: in the same per-entity directory under the schema capability's own tree,
#: because a projection is a schema whichever channel carries it. One entry
#: per tier rather than one shared string, so a tier that ever needs its own
#: location has somewhere to say so.
COMMITTED_DIRS = {tier: "src/schemas/channels/{entity}" for tier in TIERS}


def entity_token(entity: str) -> str:
    """The entity as it appears in a PATH or a FILENAME: hyphenated.

    The master and the gold table keep the underscore form, and every committed
    artifact uses a hyphen. One function, because this was an unstated fact
    about one directory until UIFPE-554.
    """
    return entity.replace("_", "-")


#: Where a CONTRACT lives: the API capability's own tree. Deliberately not
#: derived from COMMITTED_DIRS - a contract is not a projection, and it stays
#: in its capability folder while its projection lives with the schemas.
CONTRACT_DIR = "src/api/{entity}"


def contract_path(domain: str, entity: str) -> str:
    """The OpenAPI contract for an entity (ADR-0022 decision 4).

    Entity-scoped like the projections, because a domain can host more than
    one contract — and the first domain to do so kept one entity's contract
    loose and the other's in a folder, which is the drift this rule ends.
    """
    tok = entity_token(entity)
    return (f"domains/{domain}/{CONTRACT_DIR.format(entity=tok)}/"
            "openapi.yaml")


def committed_path(domain: str, entity: str, tier: str, operation: str) -> str:
    """The repo-relative path of a committed projection (ADR-0022 decision 4).

    The ONE place this is derived. A second copy — in a test, in a workflow —
    is the drift the rule exists to prevent, so callers ask this rather than
    rebuilding the path from the parts.
    """
    tok = entity_token(entity)
    return (f"domains/{domain}/{COMMITTED_DIRS[tier].format(entity=tok)}/"
            f"{tok}-data-{tier}-{operation}.json")


#: How each tier is delivered — carried over from the retired
#: generate_channel_schemas.py, which stated it in the subset and was right to.
TRANSPORTS = {"event": "SQS", "message": "Kinesis", "api": "webhook / API destination"}

#: A channel is registered PER OPERATION (owner instruction 2026-09-25), so
#: every tier is emitted once per operation and the type token is
#: `{channel}-{operation}`.
#:
#: Both files carry the SAME projection, because the projection slices by
#: AUDIENCE and the operation slices by what happened -- two independent axes.
#: They are separate artifacts so the two can version independently: a field
#: added to the created contract need not widen the updated one.
#:
#: Only created and updated exist. A delete is detected and counted but never
#: published (the product AsyncAPI records this), so a `deleted` projection
#: would describe a contract nothing serves.
OPERATIONS = ("created", "updated")

#: A subset is CONSUMER-audience, so its metadata is an explicit allow-list
#: rather than the master's block inherited wholesale (UIFPE-523).
#:
#: This is not tidiness. The master's `primaryKey` names product's gold key —
#: `inventoryItemId` / `organizationId` AND their Oracle columns
#: `INVENTORY_ITEM_ID` / `ORGANIZATION_ID`. Those are the source-system
#: surrogates rule N7 keeps off every consumer surface, and the property
#: filter strips them from `properties` precisely so they never leave the
#: platform. Inheriting the master's metadata put them straight back, in a
#: subset this repo publishes to the schema registry. The retired generator
#: emitted a narrow envelope and did not have this bug; consolidating onto
#: this one has to keep that discipline.
SUBSET_METADATA_KEYS = ("domain", "entity", "version", "dataClassification")


def _repo_relative(master: str) -> str:
    """The master's path as the repo spells it, whatever was passed on the
    command line.

    The generated subset records where it came from, so that value must not
    depend on HOW the generator was invoked — an absolute path and a relative
    one produced different bytes for the same master, which made the artifact
    unreproducible and the committed-copy check unpassable.
    """
    parts = Path(master).as_posix().split("/")
    if "domains" in parts:
        return "/".join(parts[parts.index("domains"):])
    return Path(master).name


def _nested_container(prop: dict):
    """The dict holding nested `properties` for an array-of-struct or object
    property, else None."""
    t = prop.get("type")
    types = t if isinstance(t, list) else [t]
    if "array" in types and isinstance(prop.get("items"), dict) \
            and prop["items"].get("properties"):
        return prop["items"]
    if "object" in types and prop.get("properties"):
        return prop
    return None


def _filter_nested(prop: dict, tier: str) -> None:
    """Per-field channel tags inside arrays/objects: keep fields untagged
    (inherit parent) or tagged with this tier; strip the markers. In-place."""
    container = _nested_container(prop)
    if not container:
        return
    kept = {}
    for fname, fprop in container["properties"].items():
        tags = fprop.get("x-channels")
        if tags is None or tier in tags:
            fp = copy.deepcopy(fprop)
            fp.pop("x-channels", None)
            # an array INSIDE a struct (fulfillmentOrgDetails.marketingInitiative,
            # UIFPE-270): filter and strip its element fields the same way, so
            # no extension marker survives into a published subset
            _filter_nested(fp, tier)
            kept[fname] = fp
    container["properties"] = kept
    if "required" in container:
        container["required"] = [r for r in container["required"] if r in kept]


def _filter_properties(master: dict, tier: str) -> dict:
    """Return a deep-copy of master with `properties` filtered to those whose
    x-channels list contains `tier`, recursing into array/object properties
    whose fields carry their own x-channels tags. The `required` list is
    filtered accordingly.

    A top-level property with NO `x-channels` key at all is REFUSED
    (UIFPE-523). It used to default to the "api" tier, and the retired
    generate_channel_schemas.py defaulted it to all three — two silent
    defaults for the same input, which is the drift this consolidation
    removes. Neither default is defensible: a nested field with no tag
    inherits its parent's channels, and a top-level property has no parent,
    so there is nothing to inherit. An untagged property means nobody
    decided which channels may carry it, and this platform refuses rather
    than guesses in that position (a manifest source without `key_mapping`
    refuses the transform; an unanchorable wizard column refuses the
    compile). Guessing here publishes an attribute to a registry, and from
    there to consumers, because someone left out a line.

    An EXPLICIT `x-channels: []` is a deliberate "no channel" marker (used
    for internal/PK columns) and is NOT the same as a missing key — it is
    excluded from every tier, and beacon's export guard reads it the same
    way (UIFPE-540).
    """
    out = copy.deepcopy(master)
    props_in   = master.get("properties", {})
    props_out  = {}
    untagged = [n for n, p in props_in.items()
                if isinstance(p, dict) and "x-channels" not in p]
    if untagged:
        raise ValueError(
            "UNTAGGED TOP-LEVEL PROPERTIES — every top-level property must "
            "declare x-channels explicitly: "
            f"{sorted(untagged)}. "
            "Tag each one with the channels it belongs on (e.g. "
            '["event", "message", "api"]), or with [] if it is internal to '
            "the platform and must reach no channel. There is no default: "
            "an omitted tag is an undecided attribute, not a permissive one."
        )
    for name, prop in props_in.items():
        channels = prop["x-channels"]
        if tier in channels:
            p = copy.deepcopy(prop)
            _filter_nested(p, tier)
            props_out[name] = p
    out["properties"] = props_out

    if "required" in master:
        out["required"] = [r for r in master["required"] if r in props_out]

    # Drop the extension marker from the published subset — it's not needed
    # by consumers and Apicurio rejects some unknown $-keywords on validate.
    for p in out["properties"].values():
        p.pop("x-channels", None)

    return out


def _assert_concentric(subsets: dict) -> None:
    event_props   = set(subsets["event"]["properties"].keys())
    message_props = set(subsets["message"]["properties"].keys())
    api_props     = set(subsets["api"]["properties"].keys())

    missing_in_message = event_props - message_props
    missing_in_api     = message_props - api_props
    if missing_in_message:
        raise ValueError(
            f"event ⊂ message INVARIANT VIOLATED — "
            f"properties in event but missing from message: {sorted(missing_in_message)}. "
            f"Fix by tagging them with x-channels: [\"event\", \"message\", \"api\"]."
        )
    if missing_in_api:
        raise ValueError(
            f"message ⊂ api INVARIANT VIOLATED — "
            f"properties in message but missing from api: {sorted(missing_in_api)}. "
            f"Fix by tagging them with x-channels: [..., \"api\"]."
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master",     required=True, help="Path to master.json")
    ap.add_argument("--entity",     required=True, help="Entity name (e.g. 'product')")
    ap.add_argument("--version",    default="v1",  help="Schema version (default v1)")
    ap.add_argument("--out-dir",    required=True, help="Output directory for generated subsets")
    args = ap.parse_args()

    # encoding is explicit on purpose: read_text()/write_text() default to the
    # platform encoding, which is cp1252 on Windows, and the masters carry
    # em-dashes in their descriptions. Without this a Windows run silently
    # mojibakes every non-ASCII character into the published subset.
    master = json.loads(Path(args.master).read_text(encoding="utf-8"))

    # The registry coordinate's domain comes from the MASTER, not a flag
    # (ADR-0022 decision 2: the master is the configuration). A flag would be a
    # second place to state the domain and so the next thing to drift.
    #
    # Absent is a hard failure, not a default. Guessing the domain publishes an
    # artifact to a coordinate no consumer is looking at, which is silent.
    domain = (master.get("metadata") or {}).get("domain")
    if not domain:
        raise ValueError(
            f"{args.master} declares no metadata.domain, so the registry "
            "coordinate for its tier projections cannot be built "
            "(ADR-0023: artifact = {domain}.{entity}.{type})."
        )
    # The gold table name is snake_case (the Athena/Glue convention); a subject
    # renders it kebab (ADR-0023 §1). One transform, stated once — and the same
    # one the committed path uses, so they cannot disagree.
    entity_tok = entity_token(args.entity)

    subsets = {tier: _filter_properties(master, tier) for tier in TIERS}
    _assert_concentric(subsets)

    # An entity that serves one audience is a legitimate shape. A CONTRACT for a
    # channel it does not serve is not (UIFPE-553): a consumer handed an empty
    # schema cannot tell "this entity publishes nothing" from "this entity
    # publishes something I failed to parse".
    #
    # Three existing guards all pass an empty projection, which is why this one
    # is needed: there was no size check here, the concentric invariant holds
    # trivially (empty within empty within N), and each per-attribute tag is
    # individually valid. Nothing was wrong except the result.
    emitted = [t for t in TIERS if subsets[t]["properties"]]
    skipped = [t for t in TIERS if not subsets[t]["properties"]]
    if not emitted:
        raise ValueError(
            f"{args.entity} reaches NO channel — every tier projection is "
            "empty, so this master produces no consumer contract at all. Either "
            "tag at least one attribute with a channel (e.g. "
            '["event", "message", "api"]), or, if the entity really is internal '
            "to the platform, it should not be generating subsets."
        )

    out_root = Path(args.out_dir) / args.entity
    written = []
    for tier in emitted:
        for operation in OPERATIONS:
            type_token = f"{tier}-{operation}"
            # The directory IS the type token, so the coordinate and the path
            # cannot drift apart.
            tier_dir = out_root / type_token / args.version
            tier_dir.mkdir(parents=True, exist_ok=True)
            # A copy per operation: the block below mutates it, and the two
            # operations must not share one object.
            subset = copy.deepcopy(subsets[tier])
            # Patch the $id so each subset has a unique identifier in Apicurio
            subset["$id"] = (
                f"https://schemas.pearson.com/gold/{args.entity}/{type_token}/"
                f"{args.version}/schema.json"
            )
            subset["title"] = f"{master.get('title', args.entity)} ({type_token})"
            subset["description"] = (
                f"The `data` block of a {args.entity} {operation} event as "
                f"delivered on the {tier} channel ({TRANSPORTS[tier]}): the "
                f"Gold master projected onto attributes whose x-channels "
                f"includes '{tier}'. The projection is identical for every "
                f"operation; the operation is carried by the coordinate so the "
                f"contracts can version independently."
            )
            master_md = master.get("metadata") or {}
            subset["metadata"] = {
                **{k: master_md[k] for k in SUBSET_METADATA_KEYS if k in master_md},
                "channel": tier,
                "operation": operation,
                "transport": TRANSPORTS[tier],
                "source": _repo_relative(args.master),
                # Mark projected subsets as consumer-audience; master keeps
                # engineering. See ADR-14.
                "audience": "consumer",
            }
            # UIFPE-523. A committed copy of this file is a TRANSITION, not the
            # source of truth: the registry is where consumers will read it from
            # (owner direction 2026-09-23 — every module refers to Apicurio).
            # Until then a file at a stable path is what an OpenAPI contract can
            # $ref, which is why a copy is committed at all.
            subset["$comment"] = (
                f"GENERATED from {_repo_relative(args.master)} by "
                "stacks/echo/src/apps/cli/generate_tier_subsets.py — do not "
                "hand-edit; regenerate. A committed copy is transitional: the "
                "schema registry is the destination for consumers."
            )
            # The registry coordinate a subset DECLARES must be the one it is
            # registered under (UIFPE-523). It used to inherit the master's
            # `registry` block verbatim, so all three tiers claimed to be the
            # master while publish.sh registered them as
            # `{entity}.gold-{tier}-value` from its own --artifact-id — three
            # names for one artifact, and the file agreed with none of them.
            #
            # The name is ADR-0023's `{domain}.{entity}.{type}`, where the type
            # is now `{channel}-{operation}`. That invariant is why there are
            # six files and not three: a file cannot declare two coordinates.
            if isinstance(subset.get("registry"), dict):
                subset["registry"] = {
                    **subset["registry"],
                    "artifact": f"{domain}.{entity_tok}.{type_token}",
                    "compatibility": "BACKWARD_TRANSITIVE",
                    "tier": REGISTRY_TIER,
                }
            (tier_dir / "schema.json").write_text(
                json.dumps(subset, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            written.append(type_token)
            print(f"  wrote {tier_dir / 'schema.json'} "
                  f"({len(subset['properties'])} properties)")

    for tier in skipped:
        print(f"  skipped {tier} — no attribute is tagged for that channel, so "
              f"{args.entity} serves no {tier} contract")

    # Declare which tiers exist, so a consumer READS the set instead of probing
    # the filesystem or re-deriving it from x-channels. Re-deriving is the
    # restatement ADR-0022 decision 1 forbids; probing cannot tell a tier that
    # was deliberately skipped from one missing because generation failed.
    meta_dir = out_root / args.version
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "tiers.json").write_text(
        json.dumps(
            {
                "entity": args.entity,
                "version": args.version,
                "source": _repo_relative(args.master),
                "emitted": emitted,
                "skipped": skipped,
                # The coordinates actually produced: one per channel per
                # operation. `emitted` names the channels; a consumer wanting
                # the subjects should read this rather than re-derive the
                # cross product, which is the restatement ADR-0022 forbids.
                "types": written,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {meta_dir / 'tiers.json'} "
          f"(channels: {', '.join(emitted)}; types: {len(written)})")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        sys.exit(1)
