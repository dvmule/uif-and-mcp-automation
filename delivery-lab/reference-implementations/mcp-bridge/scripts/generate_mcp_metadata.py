"""OpenAPI -> MuleSoft MCP Bridge artifacts. The contract is the source of truth.

Generates, from ONE parse of the domain's registered OpenAPI contract
(../api/openapi.yaml), everything the MCP Bridge needs — nothing is
hand-maintained alongside the contract:

  mcp-metadata.json   MuleSoft MCP Exchange asset content
                      {transport, tools[{name, description, inputSchema}],
                       connections[{kind: rest-api, ref}]}
  mcp-metadata.zip    the same JSON packaged with its exchange.json descriptor
                      (the shape Exchange stores for UI-generated assets)
  tool-mappings.json  bridge-side wiring derived from the same parse:
                      - transcodingPath (MCP transport path, e.g. /mcp)
                      - router routes [{upstreamName, tools[]}]
                        (inbound mcp-transcoding-router configuration)
                      - tools[{name, method, path, queryParams, uriParams,
                        headers, body?}] (outbound mcp-transcoding
                        configuration — the tool -> HTTP request mappings)

Transform rules were reverse-engineered from a UI-generated golden asset and
verified BYTE-IDENTICAL against MuleSoft's own output (mcp-prep PoC,
2026-09-09/10); the tool-name/description/inputSchema derivation is mechanical:

  tool name        = <method>_<path segments joined by "_", {braces} stripped,
                     lowercased> — the OPERATION path only (no server base)
  tool description = operation.description verbatim (fallback: summary)
  inputSchema      = header/query/path parameters (param description merged
                     into the schema, param-level example dropped, top-level
                     "type" key ordered last) + "body" = the application/json
                     request-body schema, $refs deep-inlined; required =
                     required params + "body" when requestBody.required

PATH COMPOSITION RULE (base paths):
  The contract may declare a relative server base (e.g. servers: [url: /api/v1]).
  That base is CONTRACT-owned, so it belongs in the generated mapping paths:
      mapping.path = <server base path> + <operation path>   (/api/v1/products)
  The bridge upstream URL is DEPLOYMENT-owned and configured per environment
  (bridge-config.yaml); it should be the service origin only. The gateway
  concatenates them at call time:
      backend request = <upstream URL> + <mapping.path>
  (Verified live: reference bridge upstream "https://host/service/" + mapping
  path "/healthcheck" -> backend /service/healthcheck.)

$ref resolution supports single-file (#/...) and multi-file specs ('./x.yaml#/...',
'sub/x.yaml#/...', '../common/x.json#/...'), each external file resolved relative to the
document that references it. Classification and containment come from contract_bundle.py:
the canonical real path must stay under the contract root (the domain's src/ directory,
contract_bundle.contract_root); absolute paths, drive letters, file://, http:// and https://
are refused. In the
pipeline the entry document is already the self-contained bundle (release_version.py).

Local-only: no network calls; writes only into --out.
"""
import argparse
import json
import os
import sys
import zipfile
from urllib.parse import urlparse

import contract_bundle as cb

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
DEFAULT_TRANSPORT_PATH = "/mcp"


class SpecLoader:
    """Loads the entry spec plus any $ref-erenced sibling files (json/yaml)."""

    def __init__(self, entry_path):
        self.entry_abs = os.path.abspath(entry_path)
        self.root_dir = os.path.dirname(self.entry_abs)
        self.contract_root = cb.contract_root(self.entry_abs)  # containment boundary: the domain's src/ (contract_bundle.py)
        self._cache = {}
        self.entry = self.load(self.entry_abs)

    def load(self, abs_path):
        abs_path = os.path.normpath(abs_path)
        if abs_path not in self._cache:
            if not os.path.exists(abs_path):
                raise FileNotFoundError("referenced spec file not found: " + abs_path)
            text = open(abs_path, "rb").read().decode("utf-8-sig")
            if abs_path.lower().endswith((".yaml", ".yml")):
                import yaml  # pinned in qe/requirements-dev.txt
                self._cache[abs_path] = yaml.safe_load(text)
            else:
                self._cache[abs_path] = json.loads(text)
        return self._cache[abs_path]

    def _target_doc(self, file_part, doc, doc_dir):
        if not file_part:
            return doc, doc_dir
        # shared classification + canonical-path containment (contract_bundle.py): a LOCAL
        # reference resolves relative to the REFERRING document and must stay under root_dir
        abs_path = cb.resolve_local(file_part, doc_dir, self.contract_root)
        return self.load(abs_path), os.path.dirname(abs_path)

    def resolve(self, node, doc=None, doc_dir=None, seen=()):
        """Deep-resolve $refs; nested refs resolve relative to THEIR file."""
        if doc is None:
            doc, doc_dir = self.entry, self.root_dir
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                file_part, _, frag = ref.partition("#")
                target_doc, target_dir = self._target_doc(file_part, doc, doc_dir)
                key = (target_dir, file_part, frag)
                if key in seen:
                    raise ValueError("circular $ref not supported: " + ref)
                target = target_doc
                for part in [p for p in frag.split("/") if p]:
                    part = part.replace("~1", "/").replace("~0", "~")
                    try:
                        target = target[part]
                    except (KeyError, TypeError):
                        raise ValueError("cannot resolve %s (missing '%s')" % (ref, part))
                merged = self.resolve(target, target_doc, target_dir, seen + (key,))
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                if siblings and isinstance(merged, dict):
                    merged = dict(merged)
                    merged.update(siblings)
                return merged
            return {k: self.resolve(v, doc, doc_dir, seen) for k, v in node.items()}
        if isinstance(node, list):
            return [self.resolve(v, doc, doc_dir, seen) for v in node]
        return node


def tool_name(method, path):
    segments = [s.strip("{}") for s in path.strip("/").split("/") if s]
    return (method + "_" + "_".join(segments)).lower()


def type_last(schema, description=None):
    """Golden ordering rule (top level only): non-type keys in source order,
    then the merged description, then type last. Nested schemas verbatim."""
    out = {k: v for k, v in schema.items() if k not in ("type", "description")}
    desc = description if description is not None else schema.get("description")
    if desc is not None:
        out["description"] = desc
    if "type" in schema:
        out["type"] = schema["type"]
    return out


def server_base_path(spec):
    """CONTRACT-owned base path from servers[0].url: '/api/v1' stays '/api/v1';
    an absolute URL contributes only its path component; absent -> ''. """
    servers = spec.get("servers") or []
    if not servers:
        return ""
    url = (servers[0] or {}).get("url") or ""
    base = urlparse(url).path if "://" in url else url
    return base.rstrip("/")


def iter_operations(spec):
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters") or []
        for method, op in item.items():
            if method.lower() in HTTP_METHODS and isinstance(op, dict):
                yield path, method, op, shared


def build_tools(loader):
    """The mcp-metadata tools[] — proven byte-identical to MuleSoft's output."""
    tools = []
    for path, method, op, shared in iter_operations(loader.entry):
        param_props = {}
        for p in list(shared) + (op.get("parameters") or []):
            p = loader.resolve(p)
            if not isinstance(p, dict) or p.get("in") not in ("header", "query", "path"):
                continue
            schema = p.get("schema") or {}
            param_props[p["name"]] = (type_last(schema, p.get("description")),
                                      bool(p.get("required")))
        ordered = sorted(param_props, key=str.lower)
        props = {name: param_props[name][0] for name in ordered}
        required = [name for name in ordered if param_props[name][1]]

        body_spec = op.get("requestBody")
        if body_spec:
            body_spec = loader.resolve(body_spec)
            content = (body_spec.get("content") or {}).get("application/json") or {}
            props["body"] = type_last(loader.resolve(content.get("schema") or {}))
            if body_spec.get("required"):
                required.append("body")

        input_schema = {"type": "object", "properties": props}
        if required:
            input_schema["required"] = required
        tools.append({
            "name": tool_name(method, path),
            "description": op.get("description") or op.get("summary") or "",
            "inputSchema": input_schema,
        })
    return tools


def build_mappings(loader, route_label, transcoding_path=DEFAULT_TRANSPORT_PATH):
    """The bridge-side wiring: outbound tool->HTTP mappings + inbound router
    routes, from the same parse. Mapping paths carry the contract's server
    base path (see PATH COMPOSITION RULE above)."""
    base = server_base_path(loader.entry)
    dataweave = "#[vars.params['%s']]"
    mappings = []
    for path, method, op, shared in iter_operations(loader.entry):
        headers, query_params, uri_params = [], [], []
        for p in list(shared) + (op.get("parameters") or []):
            p = loader.resolve(p)
            if not isinstance(p, dict):
                continue
            # header mappings mirror the live UI-generated reference verbatim;
            # query/uri use the same DataWeave pattern by extrapolation —
            # VERIFY against the first live bridge for a contract that has them.
            entry = {"key": p.get("name"), "value": dataweave % p.get("name")}
            if p.get("in") == "header":
                headers.append(entry)
            elif p.get("in") == "query":
                query_params.append(entry)
            elif p.get("in") == "path":
                uri_params.append(entry)
        mapping = {
            "name": tool_name(method, path),
            "path": base + path,
            "method": method.upper(),
            "queryParams": query_params,
            "uriParams": uri_params,
            "headers": headers,
        }
        if op.get("requestBody"):
            mapping["body"] = "#[vars.params.body]"
        mappings.append(mapping)
    return {
        "transcodingPath": transcoding_path,
        "routes": [{"upstreamName": route_label,
                    "tools": [m["name"] for m in mappings]}],
        "tools": mappings,
    }


def generate(spec_path, source_ref, mcp_asset, org_id,
             route_label=None, transcoding_path=DEFAULT_TRANSPORT_PATH):
    """Pure derivation. source_ref/mcp_asset: {'assetId':..., 'version':...}.
    Returns {'metadata': ..., 'mappings': ..., 'exchange': ...}."""
    loader = SpecLoader(spec_path)
    route_label = route_label or source_ref["assetId"]
    metadata = {
        "transport": {"kind": "streamableHttp", "path": transcoding_path},
        "tools": build_tools(loader),
        "connections": [{
            "kind": "rest-api",
            "name": source_ref["assetId"],
            "ref": {
                "groupId": source_ref.get("groupId", org_id),
                "assetId": source_ref["assetId"],
                "version": source_ref["version"],
            },
        }],
    }
    exchange = {
        "main": "mcp-metadata.json",
        "name": mcp_asset["assetId"],
        "organizationId": org_id,
        "groupId": org_id,
        "assetId": mcp_asset["assetId"],
        "version": mcp_asset["version"],
        "apiVersion": "v1",
        "classifier": "mcp-metadata",
        "dependencies": [],
        "tags": [],
    }
    return {
        "metadata": metadata,
        "mappings": build_mappings(loader, route_label, transcoding_path),
        "exchange": exchange,
    }


def write_artifacts(artifacts, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    meta_json = json.dumps(artifacts["metadata"], indent=2, ensure_ascii=False)
    paths = {}
    paths["metadata"] = os.path.join(out_dir, "mcp-metadata.json")
    with open(paths["metadata"], "w", encoding="utf-8", newline="\n") as f:
        f.write(meta_json)
    paths["zip"] = os.path.join(out_dir, "mcp-metadata.zip")
    with zipfile.ZipFile(paths["zip"], "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("exchange.json", json.dumps(artifacts["exchange"], separators=(",", ":")))
        z.writestr("mcp-metadata.json", meta_json)
    paths["mappings"] = os.path.join(out_dir, "tool-mappings.json")
    with open(paths["mappings"], "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(artifacts["mappings"], indent=2, ensure_ascii=False))
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True, help="entry OpenAPI file (yaml or json)")
    ap.add_argument("--source-asset-id", required=True, help="Exchange REST asset id, e.g. product-api")
    ap.add_argument("--source-version", required=True, help="source asset version (= contract info.version)")
    ap.add_argument("--source-group-id", default=None, help="defaults to --org-id")
    ap.add_argument("--asset-id", required=True, help="MCP Exchange asset id, e.g. uif-product-mcp")
    ap.add_argument("--asset-version", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--route-label", default=None, help="bridge routing label (default: source asset id)")
    ap.add_argument("--transcoding-path", default=DEFAULT_TRANSPORT_PATH)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    artifacts = generate(
        a.spec,
        source_ref={"assetId": a.source_asset_id, "version": a.source_version,
                    "groupId": a.source_group_id or a.org_id},
        mcp_asset={"assetId": a.asset_id, "version": a.asset_version},
        org_id=a.org_id,
        route_label=a.route_label,
        transcoding_path=a.transcoding_path,
    )
    paths = write_artifacts(artifacts, a.out)
    names = [t["name"] for t in artifacts["metadata"]["tools"]]
    print("wrote %s (%d tools: %s)" % (paths["metadata"], len(names), ", ".join(names)))
    print("wrote %s" % paths["zip"])
    print("wrote %s (mapping base path: '%s')" % (paths["mappings"],
          server_base_path(SpecLoader(a.spec).entry) or "(none)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
