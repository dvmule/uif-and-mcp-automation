"""Complete MCP Bridge provisioning — the UI-EQUIVALENT create, ported from the PoC.

SURFACE CLASSIFICATION (keep this distinction in every review):
  This script's write uses `POST /apimanager/xapi/v1/organizations/{org}/
  environments/{env}/apis` — the SAME call the Anypoint UI's MCP Bridge wizard
  makes, with the SAME payload (reverse-engineered from the published UI
  source, reproduced live in the 2026-09 PoC). It is **UI-observed, not
  publicly documented** — unlike the Exchange publishes (exchange_publish.py,
  documented) and the read-backs below (API Manager v1 GETs, documented
  family). If MuleSoft documents an equivalent or the Terraform provider
  closes its gaps, this script is the first thing to retire.

WHAT ONE POST CREATES (all live-verified):
  instance (spec -> the MCP Exchange asset) + HTTPS/443 listener with inbound
  TLS context + routing (labeled upstream, X-UPSTREAM-NAME rule, upstream
  `connection` -> source REST asset + selected API instance) +
  metadata.generatedBy=mcp_bridge + the four MCP policies (three inbound
  inline incl. mcp-transcoding-router@1.0.1 with transcodingPath; outbound
  mcp-transcoding@1.0.1 embedded per upstream with the tool->HTTP mappings).

INPUT DISCIPLINE — nothing is invented:
  - tools/mappings come from the GENERATED artifacts (contract-derived);
  - domain identity from bridge-config.yaml; conventions derive the rest:
    instance label = uif-<domain>-mcp, base path = /<domain>;
  - upstream_url is the ONLY required per-environment upstream input
    (bridge-config environments.<env>) and may point to ANY reachable HTTP
    API — the backend is not assumed to be API-Manager-fronted; absent ->
    <UNRESOLVED:...> in dry-run and REFUSED on --execute;
  - source_api_instance_id is OPTIONAL: supplied -> the full source API
    Manager association (`connection` block, UI/APIs-panel lineage) is
    emitted; absent -> the connection block is OMITTED COMPLETELY (no null,
    no placeholder). DEV acceptance criterion before the standard declares
    the association fully optional: the first connection-less Bridge must
    pass MCP initialize + tools/list (mcp-bridge.md §acceptance);
  - Anypoint org/env/gateway/TLS coordinates come from the shared MCP
    platform/environment configuration (anypoint-environments.yaml,
    --platform-config) — configured once per UIF environment, reused by
    every domain; CLI/env overrides win;
  - gatewayVersion is DISCOVERED at deployment time from the gateway target
    (never configured; shown as <DISCOVERED-AT-DEPLOY:...> in dry-run);
  - the MCP asset version is CAPTURED from the pipeline's own publish stage
    (shown as <CAPTURED-AT-DEPLOY:...> in dry-run; --execute REQUIRES
    --mcp-asset-version).

SAFETY MODEL: --dry-run default (prints the complete payload; sends nothing,
not even a token request). --preflight requires MCP_LIVE_ENABLED=true +
credentials and performs ONLY reads (gateway target + gatewayVersion
discovery, TLS context resolution, marker sweep, label/base-path collision
scan) then stops — no create. --execute = the same preflight, then ONE POST,
then documented v1 read-backs (generatedBy, upstream connection, policy set).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://anypoint.mulesoft.com"
MAX_INSTANCE_READS = 25  # cap on per-instance v1 reads during preflight fallbacks
MULESOFT_POLICY_ORG = "68ef9520-24e9-4cf2-b2f5-620025690913"  # MuleSoft's public policy-template org
POLICY_PINS = {  # live-verified versions (capabilities/mcp/mcp-bridge.md §Bridge anatomy)
    "mcp-support": "1.0.1",
    "mcp-schema-validation": "1.1.0",
    "mcp-transcoding-router": "1.0.1",  # 1.0.0 stores but cannot render transcodingPath
    "mcp-transcoding": "1.0.1",
}


def unresolved(name):
    return "<UNRESOLVED:%s>" % name


def load_yaml(path):
    import yaml
    return yaml.safe_load(open(path, encoding="utf-8-sig"))


def build_payload(cfg, env, artifacts_dir, opts):
    """The complete UI-equivalent create payload. Missing external inputs become
    <UNRESOLVED:...> markers (dry-run display; execute refuses them)."""
    meta = json.load(open(os.path.join(artifacts_dir, "mcp-metadata.json"), encoding="utf-8-sig"))
    mappings = json.load(open(os.path.join(artifacts_dir, "tool-mappings.json"), encoding="utf-8-sig"))
    env_cfg = (cfg.get("environments") or {}).get(env) or {}

    source_asset = cfg["source_asset_id"]
    source_version = meta["connections"][0]["ref"]["version"]
    # productAPIVersion = the Exchange version group, captured by
    # ensure_source_instance.py from the asset descriptor; the "v<major>"
    # derivation is only the fallback for a hand-supplied instance id.
    product_api_version = opts.get("source_product_api_version") or "v" + str(source_version).split(".")[0]
    upstream_url = opts.get("upstream_url") or env_cfg.get("upstream_url") or unresolved("environments.%s.upstream_url" % env)
    # Source association: normally the Exchange EXTERNAL instance captured by
    # ensure_source_instance.py (UUID string, passed unchanged); a hand-set
    # bridge-config value is the override. Absent -> no connection block at all.
    source_instance = opts.get("source_api_instance_id") or env_cfg.get("source_api_instance_id")
    # connection.label = the instance's display name (the UI sets it from the
    # selected/created instance's `name`; managed instances carry "v1:<id>")
    source_label = opts.get("source_instance_label") or "%s:%s" % (product_api_version, source_instance)
    route_label = cfg.get("route_label", source_asset)
    org = opts.get("org_id") or unresolved("org_id (anypoint-environments.yaml)")
    domain = cfg.get("domain") or unresolved("domain (bridge-config)")
    # UIF conventions (decided 2026-09): label uif-<domain>-mcp, path /<domain>.
    # CLI overrides kept for controlled experiments only.
    instance_label = opts.get("instance_label") or ("uif-%s-mcp" % domain if "<" not in str(domain) else unresolved("domain (bridge-config)"))
    base_path = opts.get("base_path") or cfg.get("base_path") or domain

    def policy(asset_id, config, order=None):
        p = {"groupId": MULESOFT_POLICY_ORG, "assetId": asset_id,
             "assetVersion": POLICY_PINS[asset_id], "configurationData": config}
        if order is not None:
            p["order"] = order
        return p

    upstream = {
        "weight": 100,
        "uri": upstream_url,
        # embedded outbound policy carries NO `order` (live-proven payload shape,
        # PoC 21164696/21164754); the platform assigns it (read back as order 4)
        "policies": [policy("mcp-transcoding", {"tools": mappings["tools"]})],
    }
    if source_instance:
        upstream["connection"] = {
            "label": source_label,
            "assetId": source_asset,
            "groupId": org,
            "version": source_version,
            "assetName": source_asset,
            "assetType": "rest",
            "apiVersionId": str(source_instance),
            "productAPIVersion": product_api_version,
        }

    return {
        "technology": "flexGateway",
        "providerId": None,
        "endpointUri": None,
        "instanceLabel": instance_label,
        "metadata": {"generatedBy": "mcp_bridge"},
        "spec": {"assetId": cfg["mcp_asset_id"], "groupId": org,
                 "version": opts.get("mcp_asset_version")
                 or "<CAPTURED-AT-DEPLOY:mcp-asset-version (= contract info.version, confirmed by the publish-mcp stage)>"},
        "endpoint": {
            "type": "mcp",
            "deploymentType": "HY",
            "proxyUri": "https://0.0.0.0:443/%s" % base_path,
            "tlsContexts": {"inbound": {
                "secretGroupId": opts.get("tls_secret_group_id") or unresolved("tls_secret_group_id (anypoint-environments.yaml)"),
                "tlsContextId": opts.get("tls_context_id") or unresolved("tls_context_id (anypoint-environments.yaml)"),
            }},
        },
        "deployment": {
            "environmentId": opts.get("anypoint_env_id") or unresolved("env_id (anypoint-environments.yaml)"),
            "type": "HY",
            "expectedStatus": "deployed",
            "overwrite": False,
            "targetId": opts.get("gateway_id") or unresolved("gateway_id (anypoint-environments.yaml)"),
            "targetName": opts.get("gateway_name") or unresolved("gateway_name (anypoint-environments.yaml)"),
            "gatewayVersion": opts.get("gateway_version")
            or "<DISCOVERED-AT-DEPLOY:gatewayVersion (read from the gateway target)>",
        },
        "routing": [{
            "label": route_label,
            "upstreams": [upstream],
            "rules": {"headers": {"X-UPSTREAM-NAME": route_label}},
        }],
        "policies": [
            policy("mcp-support", {}, 1),
            policy("mcp-schema-validation", {"validateToolSchema": True}, 2),
            policy("mcp-transcoding-router",
                   {"transcodingPath": mappings["transcodingPath"], "routes": mappings["routes"]}, 3),
        ],
    }


def find_unresolved(node, path="$", markers=("<UNRESOLVED:",)):
    """Markers can be EMBEDDED anywhere in a string (e.g. inside proxyUri) —
    detect by containment, never by prefix. Default finds only the blocking
    <UNRESOLVED:...> markers; execute() additionally sweeps the
    deploy-time-resolved kinds (<CAPTURED-AT-DEPLOY:...>, <DISCOVERED-AT-DEPLOY:...>)."""
    out = []
    if isinstance(node, str) and any(m in node for m in markers):
        out.append("%s = %s" % (path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            out += find_unresolved(v, path + "." + k, markers)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += find_unresolved(v, "%s[%d]" % (path, i), markers)
    return out


def _live_gates():
    if os.environ.get("MCP_LIVE_ENABLED") != "true":
        sys.exit("REFUSED: --preflight/--execute require MCP_LIVE_ENABLED=true (live MCP operations are latched off).")
    cid = os.environ.get("ANYPOINT_CLIENT_ID")
    sec = os.environ.get("ANYPOINT_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("REFUSED: set ANYPOINT_CLIENT_ID / ANYPOINT_CLIENT_SECRET (Connected App).")
    return cid, sec


def _get(base_url, token, path, extra=None):
    headers = {"Authorization": "Bearer " + token}
    headers.update(extra or {})
    req = urllib.request.Request(base_url + path, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _list_env_apis(base_url, token, org, env_id):
    """All API instances of the environment (documented v1 list, paged).
    The list returns SUMMARIES (instanceLabel, endpointUri, assetVersion...) —
    NOT the listener `endpoint.proxyUri`; resolve that per instance with
    _resolve_proxy_uris. Fails closed if the server-reported `total` of assets
    was not fully paged."""
    offset, out, assets_seen, total = 0, [], 0, None
    while offset < 4000:
        page = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis?limit=250&offset=%d" % (org, env_id, offset))
        assets = page.get("assets") or []
        if total is None and isinstance(page.get("total"), int):
            total = page["total"]
        if not assets:
            break
        assets_seen += len(assets)
        for asset in assets:
            ctx = {k: asset.get(k) for k in ("assetId", "groupId", "exchangeAssetName", "name") if asset.get(k) is not None}
            for a in (asset.get("apis") or []):
                a.setdefault("_asset", ctx)  # parent asset context (assetId/groupId) for per-asset reads
                out.append(a)
        offset += len(assets)
    if total is not None and assets_seen < total:
        sys.exit("REFUSED: environment inventory incomplete (%d of %d assets paged) - collision scan would be blind."
                 % (assets_seen, total))
    return out


MAX_FULL_SCAN_READS = 600


def _proxy_uri_of(api):
    return ((api.get("endpoint") or {}).get("proxyUri")) or api.get("proxyUri") or ""


def _resolve_proxy_uris(base_url, token, org, env_id, env_apis):
    """[(api, proxyUri)] for EVERY instance. The v1 list omits the listener
    path (root cause of the 2026-09-11 miss: '/product' was in use on the
    target while the scan compared against empty strings), so each instance
    lacking it is read with includeProxyConfiguration=true (documented v1)."""
    out, reads = [], 0
    for a in env_apis:
        proxy = _proxy_uri_of(a)
        if not proxy and a.get("id"):
            if reads >= MAX_FULL_SCAN_READS:
                sys.exit("REFUSED: more than %d instances need a detail read - collision scan cannot be completed." % MAX_FULL_SCAN_READS)
            reads += 1
            inst = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s?includeProxyConfiguration=true" % (org, env_id, a["id"]))
            proxy = _proxy_uri_of(inst)
            if inst:
                a = dict(a, **{k: v for k, v in inst.items() if k in ("endpoint", "deployment", "status", "instanceLabel")})
        out.append((a, proxy))
    return out


def _path_component(value):
    """The complete, normalized listener path of a proxyUri or path string, parsed as a URI:
    '/x', 'https://0.0.0.0:443/x/', 'http://host/x' -> '/x'; 'https://h/uif/domains/' -> '/uif/domains';
    'https://h/a/b/c?q=1#f' -> '/a/b/c'. Every segment is preserved (no last-segment assumption),
    a query string or fragment is never part of the path, one trailing slash is dropped, and
    'https://host' (no path) -> '/'. None for anything that is not a path or an absolute URI."""
    if not isinstance(value, str):
        return None
    if not (value.startswith("/") or "://" in value):
        return None
    path = urllib.parse.urlsplit(value).path.rstrip("/")
    return path or "/"


def base_path_of(proxy_uri):
    """The Bridge base path a proxyUri serves, in the shared_bridge.base_path convention used
    throughout this tooling (all segments, no leading/trailing slash): 'https://0.0.0.0:443/uif/domains'
    -> 'uif/domains'. Comparisons put the leading slash back: '/' + base_path == _path_component(uri).
    Fails closed: a proxyUri without a listener path is refused instead of yielding ''."""
    component = _path_component(proxy_uri)
    if not component or component == "/":
        sys.exit("REFUSED: proxyUri %r carries no listener path - a Bridge base path cannot be derived from it." % (proxy_uri,))
    return component.strip("/")


def _collect_paths(node, out):
    if isinstance(node, dict):
        for v in node.values():
            _collect_paths(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_paths(v, out)
    else:
        p = _path_component(node)
        if p:
            out.add(p)


def _target_path_inventory(base_url, token, org, env_id, gateway_id):
    """Target-level occupancy from the xapi gateway-targets reads the UI uses
    (routes '/ports' and '/apis' traced from the UI bundle). The platform's
    own create validation is target-scoped ('port 443 path ... already in use
    for Flex Target'), so this is the authoritative view when reachable.
    Returns {source: {'status': str, 'paths': set}} - unknown shapes are
    reported, never treated as a pass."""
    inventory = {}
    for sub in ("ports", "apis"):
        key = "gateway-target %s (xapi)" % sub
        try:
            data = _get(base_url, token,
                        "/apimanager/xapi/v1/organizations/%s/environments/%s/gateway-targets/%s/%s" % (org, env_id, gateway_id, sub),
                        {"X-ANYPNT-ORG-ID": org, "X-ANYPNT-ENV-ID": env_id})
            paths = set()
            _collect_paths(data, paths)
            size = len(data) if isinstance(data, (list, dict)) else 0
            inventory[key] = {"status": "ok (%d entries, %d paths)" % (size, len(paths)), "paths": paths}
        except urllib.error.HTTPError as e:
            inventory[key] = {"status": "HTTP %s (not usable)" % e.code, "paths": set()}
    return inventory


def collision_hits(label, base_path, resolved, inventory):
    """Pure: label/path hits across resolved instances + target inventory."""
    want, hits = "/" + base_path.strip("/"), []
    for a, proxy in resolved:
        if a.get("instanceLabel") == label:
            hits.append("label collision with instance %s" % a.get("id"))
        if proxy and _path_component(proxy) == want:
            hits.append("base path %s collision with instance %s (proxyUri %s)" % (want, a.get("id"), proxy))
    for source, info in inventory.items():
        if want in info["paths"]:
            hits.append("base path %s already in use on the target [%s]" % (want, source))
    return sorted(set(hits))


def discover_gateway_version(base_url, token, org, env_id, gateway_id, env_apis=None):
    """Read the gateway target's current version at deployment time (the value
    drifts with gateway upgrades and is never configured).
    Primary source: the xapi gateway-targets read (route traced from the UI
    bundle — UI-observed). Fallback, documented family: `deployment.gatewayVersion`
    of an instance already deployed to that target (v1 instance read; the shape
    is live-verified in the PoC evidence). Returns (version, source_label)."""
    try:
        data = _get(base_url, token,
                    "/apimanager/xapi/v1/organizations/%s/environments/%s/gateway-targets/%s" % (org, env_id, gateway_id),
                    {"X-ANYPNT-ORG-ID": org, "X-ANYPNT-ENV-ID": env_id})
        version = (data.get("gatewayVersion") or data.get("version")
                   or (data.get("target") or {}).get("gatewayVersion"))
        if version:
            return str(version), "gateway target read (xapi gateway-targets)"
        note = "gateway-targets read had no version (keys: %s)" % ", ".join(sorted(data))
    except urllib.error.HTTPError as e:
        note = "gateway-targets read HTTP %s" % e.code
    reads = 0
    for a in (env_apis if env_apis is not None else _list_env_apis(base_url, token, org, env_id)):
        dep = a.get("deployment") or {}
        if not dep and a.get("id") and reads < MAX_INSTANCE_READS:
            reads += 1
            inst = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s" % (org, env_id, a["id"]))
            dep = inst.get("deployment") or {}
        if dep.get("targetId") == gateway_id and dep.get("gatewayVersion"):
            return str(dep["gatewayVersion"]), "deployment.gatewayVersion of instance %s on the target (v1 read; %s)" % (a.get("id"), note)
    sys.exit("REFUSED: could not discover gatewayVersion for gateway target %s (%s; no deployed instance on it either)" % (gateway_id, note))


def _resolve_tls_context(base_url, token, org, env_id, sg_id, tls_id, env_apis):
    """Two independent read-only sources for the shared TLS context:
    documented Secrets Manager read, and any existing instance in the
    environment already bound to the same (secretGroupId, tlsContextId)."""
    results = {}
    try:
        data = _get(base_url, token, "/secrets-manager/api/v1/organizations/%s/environments/%s/secretGroups/%s/tlsContexts/%s" % (org, env_id, sg_id, tls_id))
        results["secrets-manager read"] = "PASS (name=%s)" % (data.get("name") or (data.get("meta") or {}).get("name") or "?")
    except urllib.error.HTTPError as e:
        results["secrets-manager read"] = "FAIL (HTTP %s)" % e.code
    bound, reads = [], 0
    for a in env_apis:
        inbound = ((a.get("endpoint") or {}).get("tlsContexts") or {}).get("inbound") or {}
        if not inbound and a.get("id") and reads < MAX_INSTANCE_READS:
            reads += 1
            inst = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s?includeTlsContexts=true" % (org, env_id, a["id"]))
            inbound = ((inst.get("endpoint") or {}).get("tlsContexts") or {}).get("inbound") or {}
        if inbound.get("secretGroupId") == sg_id and inbound.get("tlsContextId") == tls_id:
            bound.append(str(a.get("id")))
        if len(bound) >= 3:
            break
    results["bound to existing instances (v1 read)"] = ("PASS (%s)" % ", ".join(bound)) if bound else "N-OBS (no instance in the environment uses these ids)"
    return results


SAFE_RESPONSE_HEADERS = ("content-type", "x-request-id", "x-correlation-id", "x-anypnt-trace-id", "date")


def describe_http_error(err, *secrets):
    """Read an HTTPError's body once and return {status, headers, body} with
    every supplied secret (bearer token, credentials) redacted. Only a fixed
    allow-list of response headers is kept (never Set-Cookie/Authorization)."""
    try:
        body = err.read().decode("utf-8", "replace")
    except Exception:
        body = "<unreadable body>"
    for s in secrets:
        if s:
            body = body.replace(s, "<redacted>")
    try:
        body = json.dumps(json.loads(body), indent=2)
    except ValueError:
        pass
    headers = {k: v for k, v in (err.headers.items() if err.headers else []) if k.lower() in SAFE_RESPONSE_HEADERS}
    return {"status": err.code, "headers": headers, "body": body[:8000]}


def check_existing(opts, cfg):
    """READ-ONLY: report every instance in the environment whose label or
    proxyUri base path matches this domain's Bridge identity (establishes
    whether a failed create left anything behind). Never writes."""
    cid, sec = _live_gates()
    token = _token_for(opts, cid, sec)
    org, env_id = opts["org_id"], opts["anypoint_env_id"]
    domain = cfg.get("domain")
    label = opts.get("instance_label") or "uif-%s-mcp" % domain
    base_path = opts.get("base_path") or cfg.get("base_path") or domain
    env_apis = _list_env_apis(opts["base_url"], token, org, env_id)
    resolved = _resolve_proxy_uris(opts["base_url"], token, org, env_id, env_apis)
    print("== CHECK EXISTING (read-only) == label %r, base path /%s, %d instances scanned (%d with a proxyUri)"
          % (label, base_path, len(resolved), sum(1 for _, p in resolved if p)))
    want, hits = "/" + base_path, []
    for a, proxy in resolved:
        why = []
        if a.get("instanceLabel") == label:
            why.append("label")
        if proxy and _path_component(proxy) == want:
            why.append("base path")
        if why:
            dep = a.get("deployment") or {}
            hits.append(a)
            print("FOUND [%s]: id=%s label=%s proxyUri=%s status=%s target=%s asset=%s@%s" % (
                "+".join(why), a.get("id"), a.get("instanceLabel"), proxy, a.get("status"),
                dep.get("targetName") or dep.get("targetId"), a.get("assetId") or (a.get("spec") or {}).get("assetId"),
                a.get("assetVersion") or a.get("productVersion") or (a.get("spec") or {}).get("version")))
    inventory = _target_path_inventory(opts["base_url"], token, org, env_id, opts["gateway_id"])
    for source, info in inventory.items():
        print("target inventory - %s: %s%s" % (source, info["status"],
                                              "; %s PRESENT" % want if want in info["paths"] else ""))
    if not hits and not any(want in i["paths"] for i in inventory.values()):
        print("NONE FOUND: no instance is labeled %r and none serves /%s - nothing was created." % (label, base_path))
    return 0


def _token_for(opts, cid, sec):
    body = json.dumps({"grant_type": "client_credentials", "client_id": cid,
                       "client_secret": sec}).encode()
    req = urllib.request.Request(opts["base_url"] + "/accounts/api/v2/oauth2/token", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def preflight(payload, opts, cfg=None, environment=None):
    """READ-ONLY preflight (GETs only; the only POST is the OAuth token request):
    gates -> token -> gateway target + gatewayVersion discovery -> TLS context
    resolution -> marker sweep -> label/base-path collision scan. Fills the
    discovered gatewayVersion into the payload and returns the token. Exits
    non-zero on any blocking finding. Never creates anything."""
    cid, sec = _live_gates()
    if not opts.get("mcp_asset_version"):
        sys.exit("REFUSED: --mcp-asset-version is required (captured from the publish-mcp stage).")
    base_url = opts["base_url"]
    token = _token_for(opts, cid, sec)
    org, env_id, gateway_id = opts["org_id"], opts["anypoint_env_id"], opts["gateway_id"]
    print("== PREFLIGHT (read-only) ==")
    print("platform: org=%s env=%s gateway=%s (%s) tls=%s/%s" % (
        org, env_id, opts.get("gateway_name"), gateway_id, opts.get("tls_secret_group_id"), opts.get("tls_context_id")))
    env_apis = _list_env_apis(base_url, token, org, env_id)
    print("environment instances scanned: %d" % len(env_apis))

    version, source = discover_gateway_version(base_url, token, org, env_id, gateway_id, env_apis)
    payload["deployment"]["gatewayVersion"] = version
    print("gateway target %s: PASS - gatewayVersion %s [%s]" % (gateway_id, version, source))

    for what, verdict in _resolve_tls_context(base_url, token, org, env_id,
                                              opts["tls_secret_group_id"], opts["tls_context_id"], env_apis).items():
        print("tls context - %s: %s" % (what, verdict))

    leftover = find_unresolved(payload, markers=("<UNRESOLVED:", "<CAPTURED-AT-DEPLOY:", "<DISCOVERED-AT-DEPLOY:"))
    if leftover:
        sys.exit("REFUSED: unresolved/uncaptured inputs:\n  " + "\n  ".join(leftover))
    print("payload markers: PASS - none remain (mcp asset %s@%s bound)" % (payload["spec"]["assetId"], payload["spec"]["version"]))
    has_conn = any("connection" in u for r in payload["routing"] for u in r["upstreams"])
    print("source association: %s" % ("connection block present" if has_conn
                                       else "connection-less (source_api_instance_id not supplied - block omitted)"))

    label, base_path = payload["instanceLabel"], base_path_of(payload["endpoint"]["proxyUri"])
    resolved = _resolve_proxy_uris(base_url, token, org, env_id, env_apis)
    with_path = sum(1 for _, p in resolved if p)
    print("listener paths resolved: %d of %d instances expose a proxyUri (per-instance v1 reads)" % (with_path, len(resolved)))
    inventory = _target_path_inventory(base_url, token, org, env_id, gateway_id)
    for source, info in inventory.items():
        print("target inventory - %s: %s" % (source, info["status"]))
    hits = collision_hits(label, base_path, resolved, inventory)
    if hits:
        sys.exit("REFUSED (collision guard): " + "; ".join(hits))
    print("collision scan: PASS - no instance labeled %r; /%s not served by any instance proxyUri and not present in the target inventory"
          % (label, base_path))
    return token


def execute(payload, opts):
    token = preflight(payload, opts)
    base_url, org, env_id = opts["base_url"], opts["org_id"], opts["anypoint_env_id"]

    post = urllib.request.Request(
        "%s/apimanager/xapi/v1/organizations/%s/environments/%s/apis" % (base_url, org, env_id),
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "X-ANYPNT-ORG-ID": org, "X-ANYPNT-ENV-ID": env_id})
    try:
        with urllib.request.urlopen(post, timeout=120) as r:
            created = json.load(r)
    except urllib.error.HTTPError as e:
        report = describe_http_error(e, token)
        rejected_path = os.path.join(opts.get("artifacts_dir") or ".", "create-rejected.json")
        with open(rejected_path, "w", encoding="utf-8") as f:
            json.dump({"status": report["status"], "response_headers": report["headers"],
                       "response_body": report["body"], "payload": payload}, f, indent=2)
        sys.exit("CREATE REJECTED - HTTP %s from the xapi create (nothing was created by this call unless the "
                 "read-only check says otherwise; run --check-existing).\nresponse headers: %s\nresponse body:\n%s\n"
                 "payload + response saved for inspection: %s"
                 % (report["status"], json.dumps(report["headers"]), report["body"], rejected_path))
    new_id = created.get("id")
    print("CREATED instance %s (label %s)" % (new_id, created.get("instanceLabel")))

    # read-backs: DOCUMENTED v1 family (the UI's own read path)
    inst = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s?includeProxyConfiguration=true&includeTlsContexts=true" % (org, env_id, new_id))
    ups = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s/upstreams" % (org, env_id, new_id))
    pol = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s/policies" % (org, env_id, new_id))
    ok, lines = verify_readback(payload, inst, ups, pol, allow_ui_gap=opts.get("allow_ui_gap", False))
    for line in lines:
        print("read-back: " + line)
    if not ok:
        sys.exit("read-back verification FAILED - inspect instance %s (--inspect %s) before any further step (nothing is deleted or patched)" % (new_id, new_id))
    print("read-back verification passed.")
    return 0


EXPECTED_POLICIES = {"mcp-support", "mcp-schema-validation", "mcp-transcoding-router", "mcp-transcoding"}


def _policies_list(pol):
    return pol.get("policies") if isinstance(pol, dict) else (pol if isinstance(pol, list) else [])


def _policy_by_template(plist, asset_id):
    return [p for p in plist if (p.get("template") or {}).get("assetId") == asset_id]


def summarize_readback(inst, ups, pol):
    """The UI-relevant facts of an instance from the documented v1 reads
    (R1 instance, R2 upstreams, R3 policies) - the same reads the API Manager
    Settings page performs. Pure; used by --inspect and by verify_readback."""
    plist = _policies_list(pol)
    outbound = _policy_by_template(plist, "mcp-transcoding")
    router = _policy_by_template(plist, "mcp-transcoding-router")
    upstreams = ups.get("upstreams") or []
    routing = inst.get("routing") or []
    return {
        "id": inst.get("id"),
        "instanceLabel": inst.get("instanceLabel"),
        "status": inst.get("status"),
        "generatedBy": (inst.get("metadata") or {}).get("generatedBy"),
        "mcp asset binding": "%s@%s" % (inst.get("assetId"), inst.get("assetVersion")),
        "proxyUri": ((inst.get("endpoint") or {}).get("proxyUri")) or "",
        "gatewayVersion": (inst.get("deployment") or {}).get("gatewayVersion"),
        "routing labels": [r.get("label") for r in routing],
        "X-UPSTREAM-NAME rules": [((r.get("rules") or {}).get("headers") or {}).get("X-UPSTREAM-NAME") for r in routing],
        "upstreams": [{"id": u.get("id"), "uri": u.get("uri"),
                       "connection": ("%s %s@%s instance %s" % (c.get("assetType"), c.get("assetId"), c.get("version"), c.get("apiVersionId"))
                                      if (c := u.get("connection")) else None)} for u in upstreams],
        "policy templates": sorted("%s@%s" % ((p.get("template") or {}).get("assetId"), (p.get("template") or {}).get("assetVersion")) for p in plist),
        "outbound mcp-transcoding": [{"upstreamId": p.get("upstreamId"), "order": p.get("order"),
                                      "tools": [t.get("name") for t in ((p.get("configuration") or {}).get("tools") or [])],
                                      "mappings": ["%s %s %s" % (t.get("name"), t.get("method"), t.get("path"))
                                                   for t in ((p.get("configuration") or {}).get("tools") or [])]}
                                     for p in outbound],
        "router": [{"transcodingPath": (p.get("configuration") or {}).get("transcodingPath"),
                    "routes": (p.get("configuration") or {}).get("routes")} for p in router],
    }


def verify_readback(payload, inst, ups, pol, allow_ui_gap=False):
    """Pure check of the documented v1 read-backs against WHAT WAS SENT.
    Data assertions: generatedBy, proxyUri base path, MCP asset binding,
    upstream count + URI, outbound mcp-transcoding persisted with EXACTLY the
    sent tool mappings (name/method/path) bound to a real upstream id, router
    transcodingPath + routes, X-UPSTREAM-NAME rule, four policy templates.
    UI-parity assertion: the API Manager UI populates the APIs table and the
    'MCP tools & mapping' panel from upstream.connection; a connection-less
    Bridge leaves both EMPTY (live 21166173, 2026-09-11). That is reported as
    a GAP and FAILS unless allow_ui_gap is set explicitly."""
    s = summarize_readback(inst, ups, pol)
    plist = _policies_list(pol)
    upstreams = ups.get("upstreams") or []
    sent_upstreams = [u for r in payload["routing"] for u in r["upstreams"]]
    sent_tools = {t["name"]: t for u in sent_upstreams for p in u["policies"] for t in p["configurationData"]["tools"]}
    sent_router = next(p for p in payload["policies"] if p["assetId"] == "mcp-transcoding-router")["configurationData"]
    base_path = base_path_of(payload["endpoint"]["proxyUri"])
    upstream_ids = {u.get("id") for u in upstreams}

    outbound = _policy_by_template(plist, "mcp-transcoding")
    persisted_tools = {t.get("name"): t for p in outbound for t in ((p.get("configuration") or {}).get("tools") or [])}
    tools_identical = (set(persisted_tools) == set(sent_tools) and all(
        persisted_tools[n].get("method") == sent_tools[n]["method"] and persisted_tools[n].get("path") == sent_tools[n]["path"]
        for n in sent_tools))
    router = _policy_by_template(plist, "mcp-transcoding-router")
    router_cfg = (router[0].get("configuration") or {}) if router else {}
    routes_identical = (router_cfg.get("transcodingPath") == sent_router["transcodingPath"] and
                        [(r.get("upstreamName"), sorted(r.get("tools") or [])) for r in (router_cfg.get("routes") or [])]
                        == [(r["upstreamName"], sorted(r["tools"])) for r in sent_router["routes"]])
    connection_present = bool(upstreams) and all(u.get("connection") for u in upstreams)
    connection_expected = any("connection" in u for u in sent_upstreams)
    sent_conn = [u["connection"] for u in sent_upstreams if "connection" in u]
    persisted_conn = [u.get("connection") or {} for u in upstreams]
    connection_identical = connection_present and len(sent_conn) == len(persisted_conn) and all(
        str(p.get("apiVersionId")) == str(s["apiVersionId"]) and p.get("label") == s["label"]
        and p.get("assetId") == s["assetId"] and str(p.get("version")) == str(s["version"])
        and p.get("productAPIVersion") == s["productAPIVersion"]
        for p, s in zip(persisted_conn, sent_conn))

    checks = {
        "generatedBy=mcp_bridge": s["generatedBy"] == "mcp_bridge",
        "proxyUri path /%s" % base_path: _path_component(s["proxyUri"]) == "/" + base_path,
        "mcp asset binding %s@%s" % (payload["spec"]["assetId"], payload["spec"]["version"]):
            s["mcp asset binding"] == "%s@%s" % (payload["spec"]["assetId"], payload["spec"]["version"]),
        "upstream count %d" % len(sent_upstreams): len(upstreams) == len(sent_upstreams),
        "upstream uri %s" % sent_upstreams[0]["uri"]: [u.get("uri") for u in upstreams] == [u["uri"] for u in sent_upstreams],
        "outbound mcp-transcoding persisted with tools %s" % ", ".join(sorted(sent_tools)): bool(outbound) and tools_identical,
        "outbound policy bound to a real upstream id": bool(outbound) and all(p.get("upstreamId") in upstream_ids for p in outbound),
        "router transcodingPath %s + routes" % sent_router["transcodingPath"]: routes_identical,
        "X-UPSTREAM-NAME rule %s" % payload["routing"][0]["rules"]["headers"]["X-UPSTREAM-NAME"]:
            s["X-UPSTREAM-NAME rules"] == [r["rules"]["headers"]["X-UPSTREAM-NAME"] for r in payload["routing"]],
        "policies include %s" % ", ".join(sorted(EXPECTED_POLICIES)):
            EXPECTED_POLICIES <= {(p.get("template") or {}).get("assetId") for p in plist},
    }
    if connection_expected:
        checks["upstream connection present (UI APIs table + Tools & mapping source)"] = connection_present
        checks["connection persisted as sent (apiVersionId %s, label %s, %s@%s, %s)" % (
            sent_conn[0]["apiVersionId"], sent_conn[0]["label"], sent_conn[0]["assetId"], sent_conn[0]["version"],
            sent_conn[0]["productAPIVersion"])] = connection_identical
    lines = ["generatedBy=%s status=%s proxyUri=%s binding=%s" % (s["generatedBy"], s["status"], s["proxyUri"], s["mcp asset binding"]),
             "policies=%s" % ", ".join(s["policy templates"]),
             "persisted outbound tools=%s" % (sorted(persisted_tools) or "NONE")]
    lines += ["%s: %s" % (name, "PASS" if ok else "FAIL") for name, ok in checks.items()]
    ok_all = all(checks.values())
    if not connection_expected:
        if connection_present:
            lines.append("UI parity: PASS (upstream connection present)")
        else:
            lines.append("UI parity: GAP - connection-less upstream: the API Manager UI shows NO API in the APIs table and an "
                         "EMPTY 'MCP tools & mapping' panel (both render from upstream.connection). %s"
                         % ("ACCEPTED (--allow-connectionless-ui-gap)" if allow_ui_gap else "FAIL (pass --allow-connectionless-ui-gap to accept)"))
            ok_all = ok_all and allow_ui_gap
    return ok_all, lines


def wizard_instances(base_url, token, org, group_id, asset_id, product_version):
    """The MCP Bridge wizard's own source-instance list ("Selected Instance"):
    xapi GET .../groups/{g}/assets/{a}/productVersions/{pv}/instances (traced
    from the UI bundle, PoC R5). Returns (status_label, [instances])."""
    try:
        data = _get(base_url, token,
                    "/apimanager/xapi/v1/organizations/%s/groups/%s/assets/%s/productVersions/%s/instances" % (org, group_id, asset_id, product_version),
                    {"X-ANYPNT-ORG-ID": org})
    except urllib.error.HTTPError as e:
        return "HTTP %s" % e.code, []
    items = data.get("instances") if isinstance(data, dict) else data
    return "ok", list(items or [])


def probe_source_instances(opts, cfg, extra_assets=()):
    """READ-ONLY: does the wizard's source-instance mechanism list NON-Flex /
    unregistered (registration-only) API Manager instances? Inventories the
    environment by technology/status, picks assets that own such instances,
    and compares the environment's instances with what the wizard lists for
    the same asset/productVersion. Also queries the domain's own source asset
    (expected empty until a source instance exists). GETs only."""
    cid, sec = _live_gates()
    token = _token_for(opts, cid, sec)
    base_url, org, env_id = opts["base_url"], opts["org_id"], opts["anypoint_env_id"]
    out_dir = opts.get("artifacts_dir") or "."
    env_apis = _list_env_apis(base_url, token, org, env_id)
    print("== PROBE SOURCE-INSTANCE SELECTION (read-only; wizard R5 list vs environment inventory) ==")
    dist = {}
    for a in env_apis:
        key = (a.get("technology") or "?", a.get("status") or "?")
        dist[key] = dist.get(key, 0) + 1
    print("environment inventory (%d instances): %s" % (
        len(env_apis), ", ".join("%s/%s=%d" % (t, s, n) for (t, s), n in sorted(dist.items()))))

    def is_registration_like(a):
        return (a.get("technology") or "") != "flexGateway" or (a.get("status") or "") != "active"

    by_asset = {}
    for a in env_apis:
        ctx = a.get("_asset") or {}
        key = (ctx.get("groupId") or org, ctx.get("assetId") or a.get("assetId"), a.get("productVersion") or "v1")
        by_asset.setdefault(key, []).append(a)
    candidates = [k for k, apis in by_asset.items() if k[1] and any(is_registration_like(a) for a in apis)]
    candidates = candidates[:8]
    for asset_id in extra_assets:
        candidates.append((cfg.get("source_group_id") or org, asset_id, "v%s" % str(cfg.get("_source_version", "1")).split(".")[0]))
    if not candidates:
        print("no asset in the environment owns a non-Flex or non-active instance - nothing to compare; "
              "the registration-only question needs a disposable instance to answer")
    verdict = {"listed": 0, "not_listed": 0}
    raw = {}
    for group_id, asset_id, pv in candidates:
        status, listed = wizard_instances(base_url, token, org, group_id, asset_id, pv)
        raw["%s/%s/%s" % (group_id, asset_id, pv)] = {"status": status, "instances": listed}
        listed_ids = {str(i.get("apiVersionId") or i.get("id")) for i in listed}
        env_for_asset = by_asset.get((group_id, asset_id, pv), [])
        print("asset %s (%s) wizard list: %s, %d listed; environment has %d" % (asset_id, pv, status, len(listed), len(env_for_asset)))
        for a in env_for_asset:
            hit = str(a.get("id")) in listed_ids
            tag = "registration-like" if is_registration_like(a) else "flex/active"
            print("  instance %s label=%s technology=%s status=%s -> %s [%s]" % (
                a.get("id"), a.get("instanceLabel"), a.get("technology"), a.get("status"),
                "LISTED BY WIZARD" if hit else "NOT listed", tag))
            if is_registration_like(a):
                verdict["listed" if hit else "not_listed"] += 1
        for i in listed:
            if str(i.get("apiVersionId") or i.get("id")) not in {str(a.get("id")) for a in env_for_asset}:
                print("  wizard-only entry: %s" % json.dumps(i, default=str)[:300])
    path = os.path.join(out_dir, "probe-source-instances.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, default=str)
    print("raw wizard responses saved: %s" % path)
    if verdict["listed"] or verdict["not_listed"]:
        print("VERDICT: registration-like instances listed by the wizard mechanism: %d; not listed: %d -> %s" % (
            verdict["listed"], verdict["not_listed"],
            "registration-only source instances ARE selectable" if verdict["listed"] and not verdict["not_listed"]
            else "registration-only source instances are NOT selectable" if verdict["not_listed"] and not verdict["listed"]
            else "MIXED - inspect per-instance lines"))
    else:
        print("VERDICT: N-OBS - no registration-like instance could be compared")
    return 0


# Instances that must NEVER be deleted by this tooling (PoC references kept for
# evidence; the source API instances are excluded structurally by the guards).
PROTECTED_INSTANCE_IDS = {"20834050", "20989217", "21000308", "21162919", "21164592", "21164696", "21164754"}


def delete_guards(inst, expected):
    """Pure identity guards for an immutable-Bridge replacement. `expected` =
    {id, label, base_path, mcp_asset_id, env_id}. Every check must PASS."""
    proxy = ((inst.get("endpoint") or {}).get("proxyUri")) or ""
    checks = {
        "id %s" % expected["id"]: str(inst.get("id")) == str(expected["id"]),
        "not a protected PoC instance": str(expected["id"]) not in PROTECTED_INSTANCE_IDS,
        "environment %s" % expected["env_id"]: inst.get("environmentId") == expected["env_id"],
        "instanceLabel %s" % expected["label"]: inst.get("instanceLabel") == expected["label"],
        "proxyUri path /%s" % expected["base_path"]: _path_component(proxy) == "/" + expected["base_path"],
        "generatedBy mcp_bridge": (inst.get("metadata") or {}).get("generatedBy") == "mcp_bridge",
        "endpoint.type mcp (a Bridge, never a source API instance)": (inst.get("endpoint") or {}).get("type") == "mcp",
        "bound MCP asset %s" % expected["mcp_asset_id"]: inst.get("assetId") == expected["mcp_asset_id"],
    }
    return all(checks.values()), ["%s: %s" % (k, "PASS" if v else "FAIL") for k, v in checks.items()]


def delete_bridge(opts, cfg, instance_id, confirm_id, mode, environment):
    """Guarded delete of ONE Bridge instance (documented v1 DELETE) for the
    immutable-replacement procedure. dry-run: print the guards only (no network);
    preflight: authenticated reads + guard evaluation, no delete; execute:
    guards -> ONE DELETE -> read-back must be 404/gone."""
    expected = {"id": str(instance_id), "label": opts.get("instance_label") or "uif-%s-mcp" % cfg["domain"],
                "base_path": (opts.get("base_path") or cfg.get("base_path") or cfg["domain"]).strip("/"),
                "mcp_asset_id": cfg["mcp_asset_id"], "env_id": opts["anypoint_env_id"]}
    print("== GUARDED BRIDGE DELETE (%s) == instance %s" % (mode, instance_id))
    print("expected identity: label=%s proxy path=/%s generatedBy=mcp_bridge endpoint.type=mcp asset=%s env=%s" % (
        expected["label"], expected["base_path"], expected["mcp_asset_id"], expected["env_id"]))
    if str(confirm_id) != str(instance_id):
        sys.exit("REFUSED: --confirm-delete must repeat the exact instance id (%s != %s)." % (confirm_id, instance_id))
    if str(instance_id) in PROTECTED_INSTANCE_IDS:
        sys.exit("REFUSED: %s is a protected PoC instance - never deleted by this tooling." % instance_id)
    if mode == "dry-run":
        print("DRY-RUN: guards above would be evaluated against the live instance; nothing sent (no token request either).")
        return 0
    cid, sec = _live_gates()
    token = _token_for(opts, cid, sec)
    base_url, org, env_id = opts["base_url"], opts["org_id"], opts["anypoint_env_id"]
    url = "%s/apimanager/api/v1/organizations/%s/environments/%s/apis/%s" % (base_url, org, env_id, instance_id)
    try:
        inst = _get(base_url, token, url[len(base_url):] + "?includeProxyConfiguration=true")
    except urllib.error.HTTPError as e:
        sys.exit("REFUSED: instance %s not readable (HTTP %s) - nothing deleted." % (instance_id, e.code))
    ok, lines = delete_guards(inst, expected)
    for line in lines:
        print("guard: " + line)
    ups = _get(base_url, token, url[len(base_url):] + "/upstreams")
    src = [str((u.get("connection") or {}).get("apiVersionId")) for u in ups.get("upstreams") or [] if u.get("connection")]
    print("guard: upstream source instances referenced: %s (NOT deleted by this operation - only the Bridge instance is)" % (src or "none"))
    if not ok:
        sys.exit("REFUSED (identity guard): instance %s does not match the expected Bridge identity - nothing deleted." % instance_id)
    if mode == "preflight":
        print("PREFLIGHT ONLY: all guards PASS; no delete was sent.")
        return 0
    req = urllib.request.Request(url, method="DELETE", headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print("DELETE %s -> HTTP %s" % (url, r.status))
    except urllib.error.HTTPError as e:
        report = describe_http_error(e, token, sec)
        sys.exit("DELETE REJECTED - HTTP %s\n%s" % (report["status"], report["body"]))
    try:
        again = _get(base_url, token, url[len(base_url):])
        sys.exit("DELETE NOT CONFIRMED: instance %s is still readable (status=%s) - inspect before recreating." % (instance_id, again.get("status")))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("VERIFIED: instance %s is gone (HTTP 404)." % instance_id)
            return 0
        sys.exit("DELETE NOT CONFIRMED: read-back HTTP %s - inspect before recreating." % e.code)


def inspect_instance(opts, instance_id, reference_id=None):
    """READ-ONLY: the three documented v1 reads the UI performs (instance with
    proxy+TLS, upstreams, policies) for `instance_id` and optionally a
    known-good `reference_id`; raw JSON saved under the artifacts dir, UI-relevant
    summary printed side by side with the differing fields listed."""
    cid, sec = _live_gates()
    token = _token_for(opts, cid, sec)
    base_url, org, env_id = opts["base_url"], opts["org_id"], opts["anypoint_env_id"]
    out_dir = opts.get("artifacts_dir") or "."

    def read(iid):
        inst = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s?includeProxyConfiguration=true&includeTlsContexts=true" % (org, env_id, iid))
        ups = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s/upstreams" % (org, env_id, iid))
        pol = _get(base_url, token, "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s/policies" % (org, env_id, iid))
        for name, doc in (("instance", inst), ("upstreams", ups), ("policies", pol)):
            with open(os.path.join(out_dir, "inspect-%s-%s.json" % (iid, name)), "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
        return summarize_readback(inst, ups, pol)

    print("== INSPECT (read-only; raw JSON saved under %s) ==" % out_dir)
    try:
        target = read(instance_id)
    except urllib.error.HTTPError as e:
        sys.exit("instance %s: NOT READABLE (HTTP %s) - nothing to inspect" % (instance_id, e.code))
    ref = None
    if reference_id:
        try:
            ref = read(reference_id)
        except urllib.error.HTTPError as e:
            print("reference %s: NOT FOUND (HTTP %s) - showing instance %s alone" % (reference_id, e.code, instance_id))
    for key in target:
        left = json.dumps(target[key], default=str)
        if ref is None:
            print("%-28s %s" % (key, left))
        else:
            right = json.dumps(ref[key], default=str)
            print("%-28s %s\n%-28s %s   [%s]" % (key, left, "   reference %s" % reference_id, right,
                                                 "SAME" if key in ("id", "instanceLabel", "proxyUri", "mcp asset binding", "status") or left == right else "DIFF"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="domain bridge-config.yaml")
    ap.add_argument("--artifacts", required=True, help="generated artifacts dir (generate_mcp_metadata.py --out)")
    ap.add_argument("--environment", required=True, help="UIF environment key in bridge-config (dev|uat|prod)")
    ap.add_argument("--mcp-asset-version", default=None, help="published MCP asset version to bind")
    ap.add_argument("--instance-label", default=None, help="release instance label (naming convention TBD — explicit input)")
    ap.add_argument("--source-api-instance-id", default=None,
                    help="id of the source REST asset's instance (Exchange external UUID or API Manager id) -> "
                         "emits the full upstream.connection block; overrides --source-instance-file")
    ap.add_argument("--source-instance-file", default=None,
                    help="source-instance.json captured by ensure_source_instance.py (default: <artifacts>/source-instance.json "
                         "when present; pass 'none' to ignore it); supplies id, label and the Exchange productAPIVersion")
    ap.add_argument("--base-path", default=None, help="override for bridge-config base_path (convention: /<domain>)")
    ap.add_argument("--upstream-url", default=None,
                    help="override bridge-config environments.<env>.upstream_url for THIS RUN ONLY (controlled experiments)")
    ap.add_argument("--platform-config",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "anypoint-environments.yaml"),
                    help="shared MCP platform/environment configuration (per UIF environment)")
    ap.add_argument("--org-id", default=os.environ.get("ANYPOINT_ORG_ID"))
    ap.add_argument("--anypoint-env-id", default=os.environ.get("ANYPOINT_ENV_ID"))
    ap.add_argument("--gateway-id", default=os.environ.get("ANYPOINT_GATEWAY_ID"))
    ap.add_argument("--gateway-name", default=os.environ.get("ANYPOINT_GATEWAY_NAME"))
    ap.add_argument("--gateway-version", default=None,
                    help="normally DISCOVERED at deploy time from the gateway target; override for tests only")
    # ap.add_argument("--tls-secret-group-id", default=os.environ.get("ANYPOINT_TLS_SECRET_GROUP_ID"))
    # ap.add_argument("--tls-context-id", default=os.environ.get("ANYPOINT_TLS_CONTEXT_ID"))
    ap.add_argument("--base-url", default=os.environ.get("ANYPOINT_BASE_URL", DEFAULT_BASE_URL))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", default="dry-run",
                   help="render the payload only; sends nothing (default)")
    g.add_argument("--preflight", dest="mode", action="store_const", const="preflight",
                   help="authenticated READ-ONLY checks (gateway version discovery, TLS resolution, "
                        "marker sweep, collision scan) and STOP — no create; requires MCP_LIVE_ENABLED=true + credentials")
    g.add_argument("--execute", dest="mode", action="store_const", const="execute",
                   help="preflight, then the ONE create POST and read-back verification")
    g.add_argument("--check-existing", dest="mode", action="store_const", const="check-existing",
                   help="READ-ONLY: list any instance already carrying this domain's label or base path "
                        "(use after a failed create to establish whether anything was created)")
    ap.add_argument("--inspect", metavar="INSTANCE_ID", default=None,
                    help="READ-ONLY: dump + summarize the UI's v1 reads for an instance (with --reference for a side-by-side diff)")
    ap.add_argument("--reference", metavar="INSTANCE_ID", default=None, help="known-good instance to diff against (--inspect)")
    ap.add_argument("--delete-instance", metavar="INSTANCE_ID", default=None,
                    help="immutable-replacement step: delete ONE Bridge instance after identity guards "
                         "(label, proxy path, generatedBy, endpoint.type mcp, bound MCP asset, environment); "
                         "combine with --dry-run | --preflight | --execute and --confirm-delete <same id>")
    ap.add_argument("--confirm-delete", metavar="INSTANCE_ID", default=None, help="must repeat --delete-instance's id")
    ap.add_argument("--probe-source-instances", action="store_true",
                    help="READ-ONLY: compare the MCP wizard's source-instance list with the environment inventory "
                         "to learn whether registration-only (non-Flex/unregistered) instances are selectable")
    ap.add_argument("--require-connection", action="store_true",
                    help="REFUSE (before any network call) unless the payload carries upstream.connection with a non-marker "
                         "apiVersionId - the create can never be connection-less")
    ap.add_argument("--allow-connectionless-ui-gap", dest="allow_ui_gap", action="store_true",
                    help="accept a connection-less Bridge whose UI APIs table / Tools & mapping panel stay empty (explicit decision)")
    a = ap.parse_args(argv)
    a.dry_run = a.mode == "dry-run" and not a.inspect and not a.probe_source_instances

    cfg = load_yaml(a.config)
    opts = {k: getattr(a, k) for k in ("org_id", "anypoint_env_id", "gateway_id", "gateway_name",
                                       "gateway_version", "tls_secret_group_id", "tls_context_id",
                                       "mcp_asset_version", "instance_label", "base_path", "base_url",
                                       "source_api_instance_id", "upstream_url")}
    # shared platform/environment configuration: set once per UIF environment,
    # reused across domains; explicit CLI/env values above take precedence.
    platform_used = []
    if os.path.exists(a.platform_config):
        platform_env = (load_yaml(a.platform_config) or {}).get(a.environment) or {}
        mapping = {"org_id": "org_id", "anypoint_env_id": "env_id", "gateway_id": "gateway_id",
                   "gateway_name": "gateway_name", "tls_secret_group_id": "tls_secret_group_id",
                   "tls_context_id": "tls_context_id"}
        for opt_key, cfg_key in mapping.items():
            if not opts.get(opt_key) and platform_env.get(cfg_key):
                opts[opt_key] = str(platform_env[cfg_key])
                platform_used.append(opt_key)
    opts["artifacts_dir"] = a.artifacts
    opts["allow_ui_gap"] = a.allow_ui_gap
    # source instance captured by ensure_source_instance.py: id (verbatim string),
    # display label and the Exchange version group; an explicit CLI id wins.
    source_file = a.source_instance_file or os.path.join(a.artifacts, "source-instance.json")
    opts["source_product_api_version"] = opts["source_instance_label"] = None
    if a.source_instance_file == "none":
        source_file = None  # explicit opt-out of the <artifacts>/source-instance.json default (run-scoped experiments)
    if source_file and os.path.exists(source_file):
        captured = json.load(open(source_file, encoding="utf-8"))
        if not opts.get("source_api_instance_id"):
            opts["source_api_instance_id"] = str(captured["source_api_instance_id"])
        if str(captured.get("source_api_instance_id")) == str(opts.get("source_api_instance_id")):
            opts["source_instance_label"] = captured.get("label")
            opts["source_product_api_version"] = captured.get("productAPIVersion")
        print("source instance from %s: id=%s label=%s productAPIVersion=%s (%s)" % (
            source_file, opts["source_api_instance_id"], opts["source_instance_label"],
            opts["source_product_api_version"], captured.get("kind")))
    elif a.source_instance_file and a.source_instance_file != "none":
        sys.exit("REFUSED: --source-instance-file %s not found" % a.source_instance_file)
    if a.inspect:
        return inspect_instance(opts, a.inspect, a.reference)
    if a.delete_instance:
        return delete_bridge(opts, cfg, a.delete_instance, a.confirm_delete, a.mode, a.environment)
    if a.probe_source_instances:
        return probe_source_instances(opts, cfg, extra_assets=(cfg["source_asset_id"],))
    if a.mode == "check-existing":
        return check_existing(opts, cfg)
    payload = build_payload(cfg, a.environment, a.artifacts, opts)
    if a.require_connection:
        conns = [u.get("connection") for r in payload["routing"] for u in r["upstreams"]]
        bad = [c for c in conns if not c or "<" in str(c.get("apiVersionId")) or not str(c.get("apiVersionId")).strip()]
        if not conns or bad:
            sys.exit("REFUSED (--require-connection): the Bridge payload would be connection-less or carry an unresolved "
                     "source instance id - a source instance must be captured first (ensure_source_instance.py). Nothing sent.")
        print("connection guard: PASS - every upstream carries connection.apiVersionId=%s label=%s productAPIVersion=%s" % (
            conns[0]["apiVersionId"], conns[0]["label"], conns[0]["productAPIVersion"]))

    print("== FULL BRIDGE CREATE (UI-observed xapi surface — see module docstring) ==")
    standard_path = cfg.get("base_path") or cfg.get("domain")
    if a.base_path and a.base_path.strip("/") != str(standard_path).strip("/"):
        print("BASE PATH OVERRIDE (this run only): /%s - the domain standard remains /%s; bridge-config.yaml is unchanged"
              % (a.base_path.strip("/"), standard_path))
    if platform_used:
        print("shared platform config (%s / %s): %s" % (
            os.path.basename(a.platform_config), a.environment, ", ".join(sorted(platform_used))))
    print("POST %s/apimanager/xapi/v1/organizations/%s/environments/%s/apis" % (
        a.base_url, opts["org_id"] or unresolved("org_id"),
        opts["anypoint_env_id"] or unresolved("env_id")))
    print(json.dumps(payload, indent=2))
    deploy_time = find_unresolved(payload, markers=("<CAPTURED-AT-DEPLOY:", "<DISCOVERED-AT-DEPLOY:"))
    if deploy_time:
        print("\nResolved automatically at deployment time (not blockers):")
        for p in deploy_time:
            print("  - " + p)
    problems = find_unresolved(payload)
    if problems:
        print("\nUNRESOLVED inputs (%d) — execute would be refused:" % len(problems))
        for p in problems:
            print("  - " + p)
    if a.dry_run:
        print("\nDRY-RUN: nothing sent (no token request either).")
        return 0
    if a.mode == "preflight":
        print()
        preflight(payload, opts)
        print("\nPREFLIGHT ONLY: all reads passed; NO create was sent. Final payload gatewayVersion=%s."
              % payload["deployment"]["gatewayVersion"])
        return 0
    return execute(payload, opts)


if __name__ == "__main__":
    sys.exit(main())
