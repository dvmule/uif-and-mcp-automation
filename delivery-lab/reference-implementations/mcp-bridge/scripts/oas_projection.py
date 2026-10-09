"""Deterministic OpenAPI 3.1 -> OAS 3.0.3 projection for Exchange publication.

WHY: Anypoint Exchange supports OAS 2.0/3.0 only (documented; OAS 3.1 is an open
platform request) and parses specs server-side on publish. UIF contracts are
OpenAPI 3.1 and stay that way: the git contract remains the single source of
truth, and this module derives a 3.0.3 rendition as a BUILD ARTIFACT for the
Exchange REST asset only — never committed, never edited by hand.

GUARANTEES:
  - Preserves paths, operations, parameters, request/response schemas,
    descriptions, examples, servers (base-path semantics) and info.version.
  - Converts a nullable type array ONLY when it is exactly representable as
    OAS 3.0 `nullable: true`: `type: [T, 'null']` (one concrete type + null)
    -> `type: T` + `nullable: true`. Anything else fails closed.
  - FAILS CLOSED (ProjectionError) on any OAS 3.1 / JSON Schema 2020-12
    construct with no safe 3.0 equivalent: multi-type arrays, bare
    `type: 'null'`, webhooks, jsonSchemaDialect, $schema, const, prefixItems,
    unevaluated*, dependent*, contentEncoding/contentMediaType, $anchor,
    $dynamicRef/$dynamicAnchor, $comment, patternProperties, schema-level
    `examples`, numeric exclusiveMinimum/Maximum, info.summary,
    license.identifier, mutualTLS security schemes, pathItems components.
  - FAILS CLOSED on any external `$ref` (not starting with '#'): the input must be
    the self-contained bundle (contract_bundle.py); this module never reads files.
  - Deterministic: same input -> byte-identical output (stable key order).

Pure function + CLI. No network access.
"""
import argparse
import copy
import json
import os
import sys

TARGET_VERSION = "3.0.3"

# Keywords that cannot be translated to OAS 3.0 anywhere they appear.
UNSUPPORTED_KEYS = {
    "webhooks", "jsonSchemaDialect", "$schema", "const", "prefixItems",
    "unevaluatedProperties", "unevaluatedItems", "dependentRequired",
    "dependentSchemas", "contentEncoding", "contentMediaType", "$comment",
    "$anchor", "$dynamicRef", "$dynamicAnchor", "patternProperties",
    "pathItems",
}
SCHEMA_MARKERS = {"type", "properties", "items", "allOf", "anyOf", "oneOf", "$ref", "enum", "format"}


class ProjectionError(Exception):
    """Raised when the 3.1 contract contains a construct with no safe 3.0 form."""


def _looks_like_schema(node):
    return isinstance(node, dict) and bool(SCHEMA_MARKERS & set(node))


def _project_node(node, path, stats):
    if isinstance(node, dict):
        for key in node:
            if key in UNSUPPORTED_KEYS:
                raise ProjectionError("%s.%s: no safe OAS 3.0 equivalent" % (path, key))
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            # safety net: the projection receives the SELF-CONTAINED bundle (contract_bundle.py);
            # an external reference here would be published dangling. Never a filesystem resolver.
            raise ProjectionError("%s.$ref=%r: external reference reached the projection - the contract must be "
                                  "bundled first (contract_bundle.py); only internal '#/...' references are allowed" % (path, ref))
        if _looks_like_schema(node):
            if "examples" in node:
                raise ProjectionError("%s.examples: schema-level `examples` is 3.1-only (3.0 uses `example`)" % path)
            for bound in ("exclusiveMinimum", "exclusiveMaximum"):
                if bound in node and not isinstance(node[bound], bool):
                    raise ProjectionError("%s.%s: numeric form is 3.1-only" % (path, bound))
        t = node.get("type")
        if isinstance(t, list):
            concrete = [x for x in t if x != "null"]
            if "null" in t and len(concrete) == 1 and isinstance(concrete[0], str):
                node["type"] = concrete[0]
                node["nullable"] = True
                stats["type_arrays_converted"] += 1
            else:
                raise ProjectionError("%s.type=%s: not exactly one concrete type + null" % (path, json.dumps(t)))
        elif t == "null":
            raise ProjectionError("%s.type='null': bare null type is 3.1-only" % path)
        for key, value in node.items():
            _project_node(value, "%s.%s" % (path, key), stats)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _project_node(value, "%s[%d]" % (path, i), stats)


def project_oas31_to_30(doc):
    """Return (projected_document, stats). Input is not mutated. Raises
    ProjectionError on any construct that cannot be translated safely."""
    version = str((doc or {}).get("openapi", ""))
    if not version.startswith("3.1"):
        raise ProjectionError("expected an OpenAPI 3.1.x document, got openapi=%r" % version)
    info = doc.get("info") or {}
    if "summary" in info:
        raise ProjectionError("$.info.summary is 3.1-only")
    if "identifier" in (info.get("license") or {}):
        raise ProjectionError("$.info.license.identifier is 3.1-only")
    for name, scheme in ((doc.get("components") or {}).get("securitySchemes") or {}).items():
        if isinstance(scheme, dict) and scheme.get("type") == "mutualTLS":
            raise ProjectionError("$.components.securitySchemes.%s: mutualTLS is 3.1-only" % name)

    projected = copy.deepcopy(doc)
    projected["openapi"] = TARGET_VERSION
    stats = {"type_arrays_converted": 0}
    _project_node(projected, "$", stats)
    stats["info_version"] = (projected.get("info") or {}).get("version")
    stats["operations"] = sum(
        1 for item in (projected.get("paths") or {}).values() if isinstance(item, dict)
        for method in item if method in ("get", "put", "post", "delete", "options", "head", "patch", "trace"))
    return projected, stats


def render_yaml(doc):
    """Stable, byte-deterministic YAML rendering (insertion order preserved)."""
    import yaml
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True, help="the authoritative OpenAPI 3.1 contract (read-only)")
    ap.add_argument("--out", required=True, help="output path of the 3.0.3 projection artifact")
    a = ap.parse_args(argv)
    import yaml
    doc = yaml.safe_load(open(a.spec, encoding="utf-8-sig"))
    try:
        projected, stats = project_oas31_to_30(doc)
    except ProjectionError as e:
        sys.exit("FAIL CLOSED (OAS 3.0 projection): " + str(e))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_yaml(projected))
    print("projection: openapi %s -> %s | info.version %s | operations %d | nullable type arrays converted %d"
          % (doc["openapi"], TARGET_VERSION, stats["info_version"], stats["operations"], stats["type_arrays_converted"]))
    print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
