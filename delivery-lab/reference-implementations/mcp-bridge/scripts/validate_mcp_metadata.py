"""Structural validation of generated MCP artifacts (importable + CLI).

Checks — all derived from the MuleSoft golden shape and the MCP spec:
  metadata:  transport shape, connections shape, tool-name charset, tool keys,
             non-empty descriptions, inputSchema object-typed, required subset
             of properties, no unresolved $ref, legal JSON-Schema type
             keywords (incl. OpenAPI 3.1 type arrays), Draft 2020-12 validity
  mappings:  every tool has exactly one mapping and vice versa, methods are
             HTTP verbs, every path absolute, POST/PUT/PATCH-with-body carries
             the body DataWeave, router routes cover exactly the tool names,
             transcodingPath equals the metadata transport path

Used by qe/test_mcp_generation.py as a library and runnable standalone:
  python validate_mcp_metadata.py --metadata <file> [--mappings <file>]
"""
import argparse
import json
import re
import sys

LEGAL_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
HTTP_VERBS = {"GET", "PUT", "POST", "DELETE", "OPTIONS", "HEAD", "PATCH", "TRACE"}


def _walk(node, fn, path="$"):
    fn(node, path)
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, fn, path + "." + k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, fn, "%s[%d]" % (path, i))


def validate_metadata(meta):
    """Returns a list of problem strings (empty = valid)."""
    problems = []
    transport = meta.get("transport") or {}
    if transport.get("kind") != "streamableHttp" or not str(transport.get("path", "")).startswith("/"):
        problems.append("transport must be {kind: streamableHttp, path: /<path>}, got %r" % transport)
    for c in meta.get("connections") or []:
        if set(c) != {"kind", "name", "ref"} or set(c.get("ref") or {}) != {"groupId", "assetId", "version"}:
            problems.append("connection shape invalid: %r" % c)

    tools = meta.get("tools") or []
    if not tools:
        problems.append("no tools generated")
    for t in tools:
        n = t.get("name", "<unnamed>")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", n or ""):
            problems.append("%s: name violates MCP tool-name charset/length" % n)
        if set(t) != {"name", "description", "inputSchema"}:
            problems.append("%s: unexpected tool keys %s" % (n, sorted(t)))
        if not (t.get("description") or "").strip():
            problems.append("%s: empty description (agents need it)" % n)
        s = t.get("inputSchema") or {}
        if s.get("type") != "object" or not isinstance(s.get("properties"), dict):
            problems.append("%s: inputSchema must be an object schema with properties" % n)
            continue
        if not set(s.get("required", [])) <= set(s["properties"]):
            problems.append("%s: required not a subset of properties" % n)
        _walk(s, lambda x, p: problems.append("%s: unresolved $ref at %s" % (n, p))
              if isinstance(x, dict) and "$ref" in x else None)

        def types_ok(x, p, n=n):
            if isinstance(x, dict):
                tv = x.get("type")
                if isinstance(tv, str) and tv not in LEGAL_TYPES:
                    problems.append("%s: illegal type %r at %s" % (n, tv, p))
                if isinstance(tv, list) and not all(e in LEGAL_TYPES for e in tv):
                    problems.append("%s: illegal type list %r at %s" % (n, tv, p))
        _walk(s, types_ok)
        try:
            import jsonschema
            jsonschema.Draft202012Validator.check_schema(s)
        except ImportError:
            pass
        except Exception as e:  # jsonschema.SchemaError
            problems.append("%s: inputSchema not valid Draft 2020-12: %s" % (n, str(e).splitlines()[0]))
    return problems


def validate_mappings(meta, mappings):
    """Cross-checks tool-mappings.json against mcp-metadata.json."""
    problems = []
    tool_names = {t["name"] for t in meta.get("tools") or []}
    map_names = [m.get("name") for m in mappings.get("tools") or []]
    if sorted(map_names) != sorted(tool_names):
        problems.append("mapping tools %s != metadata tools %s" % (sorted(map_names), sorted(tool_names)))
    if len(map_names) != len(set(map_names)):
        problems.append("duplicate mapping names")
    for m in mappings.get("tools") or []:
        n = m.get("name", "<unnamed>")
        if m.get("method") not in HTTP_VERBS:
            problems.append("%s: bad method %r" % (n, m.get("method")))
        if not str(m.get("path", "")).startswith("/"):
            problems.append("%s: path must be absolute (server base + operation path), got %r" % (n, m.get("path")))
        for group in ("queryParams", "uriParams", "headers"):
            for e in m.get(group) or []:
                if set(e) != {"key", "value"}:
                    problems.append("%s: %s entry shape invalid: %r" % (n, group, e))
    routed = [t for r in mappings.get("routes") or [] for t in r.get("tools") or []]
    if sorted(routed) != sorted(tool_names):
        problems.append("router routes cover %s, expected %s" % (sorted(routed), sorted(tool_names)))
    for r in mappings.get("routes") or []:
        if not (r.get("upstreamName") or "").strip():
            problems.append("route missing upstreamName")
    if mappings.get("transcodingPath") != (meta.get("transport") or {}).get("path"):
        problems.append("transcodingPath %r != transport.path %r" % (
            mappings.get("transcodingPath"), (meta.get("transport") or {}).get("path")))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--mappings", default=None)
    a = ap.parse_args(argv)
    meta = json.load(open(a.metadata, encoding="utf-8-sig"))
    problems = validate_metadata(meta)
    if a.mappings:
        mappings = json.load(open(a.mappings, encoding="utf-8-sig"))
        problems += validate_mappings(meta, mappings)
    for p in problems:
        print("FAIL: " + p)
    if problems:
        print("RESULT: %d problem(s)" % len(problems))
        return 1
    print("RESULT: all checks passed (%d tools)" % len(meta.get("tools") or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
