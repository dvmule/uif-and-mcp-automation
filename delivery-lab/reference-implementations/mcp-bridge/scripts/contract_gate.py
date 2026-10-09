"""contract_gate.py - contract-derived validation of the generated MCP artifacts.

Principle: the domain OpenAPI is the ONLY source of truth; mcp-metadata.json and
tool-mappings.json are DERIVED artifacts; CI validates the TRANSFORMATION. No
test maintains a second, hand-written list of expected tools - adding, removing or
renaming an operation in the contract is validated automatically.

What is re-derived from the contract, independently of the generator's output:
  * the set of eligible operations (every HTTP method under paths/), each of which
    must yield exactly one tool - none missing, none extra, no duplicates;
  * the deterministic tool name: <method>_<path segments without braces>, lower-case;
  * the HTTP mapping: method, server base path + operation path, query / path /
    header parameter wiring, and a body mapping iff the operation has a requestBody;
  * the tool input schema surface: one property per parameter plus `body` iff a
    requestBody exists, and the `required` list;
  * a single route naming every tool.

The contract is MULTI-FILE-AWARE: check_projection bundles the root with every reachable
local dependency (contract_bundle.py) before projecting, so an unsafe or missing dependency
fails the gate. Fail-closed constructs are unchanged: the OAS 3.0.3 projection (oas_projection.py)
still refuses 3.1-only constructs, and the structural validator
(validate_mcp_metadata.py) still runs. This module only adds the contract <->
artifact reconciliation. It reuses the generator's primitive derivation RULES
(tool_name, server_base_path, iter_operations, SpecLoader) - those rules are the
standard - but never the generator's output.

CLI:  contract_gate.py --spec <openapi> --metadata <mcp-metadata.json>
                       --mappings <tool-mappings.json>          -> exit 1 on any problem
"""
import argparse
import json
import sys

import contract_bundle as cb
from generate_mcp_metadata import SpecLoader, iter_operations, server_base_path, tool_name
from oas_projection import ProjectionError, project_oas31_to_30

DATAWEAVE = "#[vars.params['%s']]"


def expected_tools_from_contract(spec_path):
    """One record per eligible operation, derived from the contract alone."""
    loader = SpecLoader(spec_path)
    base = server_base_path(loader.entry)
    expected = []
    for path, method, op, shared in iter_operations(loader.entry):
        params = {"query": [], "path": [], "header": []}
        required = []
        for p in list(shared) + (op.get("parameters") or []):
            p = loader.resolve(p)
            if not isinstance(p, dict) or p.get("in") not in params:
                continue
            params[p["in"]].append(p["name"])
            if p.get("required"):
                required.append(p["name"])
        body = op.get("requestBody")
        body = loader.resolve(body) if body else None
        expected.append({
            "name": tool_name(method, path),
            "operationId": op.get("operationId"),
            "method": method.upper(),
            "path": base + path,
            "params": params,
            "required_params": sorted(required, key=str.lower),
            "has_body": bool(body),
            "body_required": bool(body and body.get("required")),
        })
    return expected


def check_generation(spec_path, metadata, mappings):
    """Reconcile the generated artifacts with the contract. Returns problem strings."""
    problems = []
    expected = expected_tools_from_contract(spec_path)
    names = [e["name"] for e in expected]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        problems.append("contract derives duplicate tool names %s - two operations map to the same MCP tool; "
                        "rename a path so the derivation stays unique" % dupes)
    exp_by_name = {e["name"]: e for e in expected}

    meta_tools = {t.get("name"): t for t in metadata.get("tools") or []}
    map_tools = {m.get("name"): m for m in mappings.get("tools") or []}
    if len(meta_tools) != len(metadata.get("tools") or []):
        problems.append("mcp-metadata.json contains duplicate tool names")
    if len(map_tools) != len(mappings.get("tools") or []):
        problems.append("tool-mappings.json contains duplicate tool names")
    for label, got in (("mcp-metadata.json tools", set(meta_tools)), ("tool-mappings.json tools", set(map_tools))):
        missing, extra = sorted(set(exp_by_name) - got), sorted(got - set(exp_by_name))
        if missing:
            problems.append("%s missing tools derived from the contract: %s" % (label, missing))
        if extra:
            problems.append("%s has tools with no contract operation: %s" % (label, extra))

    for name, e in exp_by_name.items():
        m = map_tools.get(name)
        if m is not None:
            if m.get("method") != e["method"]:
                problems.append("%s: mapping method %r != contract %r" % (name, m.get("method"), e["method"]))
            if m.get("path") != e["path"]:
                problems.append("%s: mapping path %r != contract server base + path %r" % (name, m.get("path"), e["path"]))
            for kind, key in (("query", "queryParams"), ("path", "uriParams"), ("header", "headers")):
                want = [{"key": n, "value": DATAWEAVE % n} for n in e["params"][kind]]
                if m.get(key, []) != want:
                    problems.append("%s: %s %r != contract %s parameters %r" % (name, key, m.get(key), kind, e["params"][kind]))
            if e["has_body"] != ("body" in m):
                problems.append("%s: mapping body presence %s != contract requestBody %s" % (name, "body" in m, e["has_body"]))
        t = meta_tools.get(name)
        if t is not None:
            schema = t.get("inputSchema") or {}
            want_props = sorted(e["params"]["query"] + e["params"]["path"] + e["params"]["header"] + (["body"] if e["has_body"] else []))
            got_props = sorted((schema.get("properties") or {}).keys())
            if got_props != want_props:
                problems.append("%s: inputSchema properties %r != contract parameters/body %r" % (name, got_props, want_props))
            want_required = sorted(e["required_params"] + (["body"] if e["body_required"] else []), key=str.lower)
            got_required = sorted(schema.get("required") or [], key=str.lower)
            if got_required != want_required:
                problems.append("%s: inputSchema required %r != contract required %r" % (name, got_required, want_required))

    routes = mappings.get("routes") or []
    routed = sorted(n for r in routes for n in (r.get("tools") or []))
    if len(routes) != 1:
        problems.append("expected exactly one route, found %d" % len(routes))
    if routed != sorted(exp_by_name):
        problems.append("route tools %r != contract-derived tools %r" % (routed, sorted(exp_by_name)))
    return problems


def check_projection(spec_path):
    """The Exchange REST asset is the OAS 3.0.3 projection of the COMPLETE authoritative contract
    (root + every reachable local dependency, bundled by contract_bundle.py): a missing, unsafe
    or escaping dependency and any 3.1-only construct fail closed here, BEFORE publication."""
    try:
        doc, _contract = cb.bundle(spec_path)
    except cb.ContractError as e:
        return ["contract bundle fails closed: %s" % e], None
    try:
        _projected, stats = project_oas31_to_30(doc)
    except ProjectionError as e:
        return ["projection fails closed: %s" % e], None
    return [], stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, help="the OpenAPI contract the artifacts were generated from")
    ap.add_argument("--metadata", required=True, help="generated mcp-metadata.json")
    ap.add_argument("--mappings", required=True, help="generated tool-mappings.json")
    a = ap.parse_args(argv)
    metadata = json.load(open(a.metadata, encoding="utf-8-sig"))
    mappings = json.load(open(a.mappings, encoding="utf-8-sig"))
    problems, stats = check_projection(a.spec)
    problems += check_generation(a.spec, metadata, mappings)
    expected = expected_tools_from_contract(a.spec)
    print("== CONTRACT GATE == %s: %d operation(s) -> %d tool(s): %s" % (
        a.spec, len(expected), len(expected), ", ".join(e["name"] for e in expected)))
    if stats:
        print("projection: openapi 3.1 -> 3.0.3 | operations %s | nullable type arrays converted %s"
              % (stats["operations"], stats["type_arrays_converted"]))
    if problems:
        print("RESULT: FAIL - generated artifacts do not match the contract:")
        for p in problems:
            print("  - " + p)
        return 1
    print("RESULT: PASS - every contract operation is a tool with the derived name, mapping and input schema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
