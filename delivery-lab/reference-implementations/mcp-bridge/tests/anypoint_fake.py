"""Offline stand-in for Anypoint (Exchange v2 + API Manager v1/xapi) and shared-Bridge
fixtures for the lifecycle tests. Every HTTP call is recorded as (method, path) and
every write as (kind, body) so tests assert EXACTLY which writes happened.

TEST-OWNED CONFIGURATION: the generic lifecycle tests run against a platform config and
a Product domain config written HERE (canonical identities: Bridge uif-domains-mcp, MCP
asset uif-domains-mcp, REST asset product-api, instance uif-product-dev), never against
the repository's files - so the repository can carry TEMPORARY isolated "-test"
identities (see test_isolation.py) without the generic suite noticing, and flipping
them back later changes no test here.

Served surfaces (the shapes the live-proven fakes in the single-domain tests used,
plus the shared MCP asset):
  Exchange   GET  /exchange/api/v2/assets/{org}/{rest}/{v}            descriptor + external instances[]
             POST .../versionGroups/v1/instances/external            create external instance
             GET  /exchange/api/v2/assets/{org}/{mcp}/asset           latest shared MCP asset version (404 = absent)
             GET  /exchange/api/v2/assets/{org}/{mcp}/{v}             descriptor with the published file link (404 = absent)
             GET  /files/{mcp}/{v}                                    the published mcp-metadata.json bytes
             POST /exchange/api/v2/organizations/{org}/assets/{org}/{mcp}/{v}  publish (multipart)
  API Mgr    environment list, gateway target, TLS, ONE Bridge create, reads of the
             existing/created Bridge, POST v1 .../upstreams (A1), PATCH xapi instance
             (validated against the platform's live routing schema - upstream REFERENCES
             only - then applied), POST xapi .../policies/outbound-policies (A3), PATCH
             v1 policies. DELETE -> AssertionError.
"""
import copy
import io
import json
import re
import tempfile
import urllib.error
from pathlib import Path

import yaml

import provision_bridge as pb
import shared_bridge as sb

REPO = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PRODUCT_SPEC = FIXTURES / "single-file-product" / "openapi.yaml"  # TEST-OWNED single-file contract (3 operations, /api/v1)

ORG = "8c27297e-4bcf-4c53-99eb-ffd7b1069294"
ENV = "a8c24dc0-02fc-4eb5-bf20-6c5b2627182f"
TARGET = "2936f2eb-716f-47f1-a41f-fcfd09518867"
TLS_SG, TLS_ID = "425b3a87-3817-4678-bdcb-e2138fd702f5", "abeec1e3-c2f0-4b60-845f-ba76240d7f8a"
SHARED_LABEL, SHARED_PATH, SHARED_MCP = "uif-domains-mcp", "domainsTest", "uif-domains-mcp"
PROD_UUID = "07577d9c-ae1c-4826-b0d1-1ecc2546c75d"
STOCK_UUID = "5b1c0d2e-3f4a-4b5c-8d6e-7f8091a2b3c4"
NEW_UUID = "11111111-2222-4333-8444-555555555555"
PROD_URL = "https://upstream.example.test/service/"
STOCK_URL = "https://upstream.example.test/stock/"
BRIDGE_ID = 21170001
NEW_ID = 31168999
FILES = "/files/"

# The canonical (target-state) platform configuration the generic suite runs against.
TEST_PLATFORM = {"dev": {
    "org_id": ORG, "env_id": ENV, "gateway_id": TARGET, "gateway_name": "mcp-dev-global",
    "gateway_public_host": "mcp-dev.pearson.com", "tls_secret_group_id": TLS_SG, "tls_context_id": TLS_ID,
    "shared_bridge": {"instance_label": SHARED_LABEL, "mcp_asset_id": SHARED_MCP, "base_path": SHARED_PATH, "transcoding_path": "/mcp"},
}}
PRODUCT_DOMAIN_CFG = {"domain": "product", "source_asset_id": "product-api", "environments": {"dev": {"upstream_url": PROD_URL}}}
# LOCAL TEST values injected into the ${VAR} placeholders of the checked-in anypoint-environments.yaml (platform_config.py);
# offline tests never need - and never read - the real GitHub Environment variables
TEST_PLATFORM_ENV = {"ANYPOINT_ORG_ID": ORG, "ANYPOINT_ENV_ID": ENV, "ANYPOINT_GATEWAY_ID": TARGET,  "ANYPOINT_GATEWAY_NAME": "mcp-dev-global", "ANYPOINT_GATEWAY_PUBLIC_HOST": "mcp-dev.pearson.com",
                     "UIF_UPSTREAM_ORIGIN": "https://upstream.example.test/"}

EXTERNAL_PROD = {"id": PROD_UUID, "type": "external", "name": "uif-product-dev", "endpointUri": PROD_URL, "isPublic": False, "productAPIVersion": "v1"}
EXTERNAL_STOCK = {"id": STOCK_UUID, "type": "external", "name": "uif-stock-dev", "endpointUri": STOCK_URL, "isPublic": False, "productAPIVersion": "v1"}

STOCK_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Stock API", "version": "1.0.0", "description": "synthetic second domain for the shared-Bridge tests"},
    "servers": [{"url": "/api/v1"}],
    "paths": {
        "/stock": {"get": {"operationId": "listStock", "summary": "List stock levels",
                           "parameters": [{"name": "sku", "in": "query", "schema": {"type": "string"}}],
                           "responses": {"200": {"description": "ok"}}}},
        "/stock/{sku}": {"get": {"operationId": "getStock", "summary": "Stock level of one SKU",
                                 "parameters": [{"name": "sku", "in": "path", "required": True, "schema": {"type": "string"}}],
                                 "responses": {"200": {"description": "ok"}}}},
    },
}
STOCK_TOOLS = ["get_stock", "get_stock_sku"]

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="uif-mcp-testcfg-"))


def platform_config(root=None):
    """Path of the TEST-OWNED anypoint-environments.yaml (canonical identities, no protected block)."""
    root = Path(root) if root else _SESSION_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / "anypoint-environments.yaml"
    path.write_text(yaml.safe_dump(TEST_PLATFORM, sort_keys=False), encoding="utf-8")
    return str(path)


def product_domain(root=None):
    """Path of the TEST-OWNED Product bridge-config.yaml (canonical identities). The contract
    stays the repository's real Product OpenAPI."""
    root = Path(root) if root else _SESSION_DIR / "product"
    (root / "src" / "mcp").mkdir(parents=True, exist_ok=True)
    path = root / "src" / "mcp" / "bridge-config.yaml"
    path.write_text(yaml.safe_dump(PRODUCT_DOMAIN_CFG, sort_keys=False), encoding="utf-8")
    return str(path)


PLATFORM = platform_config()
PRODUCT_CFG = product_domain()


def synthetic_domain(root, domain, resource, upstream_url):
    """A minimal synthetic domain tree (contract with two operations + bridge-config)
    under `root`: tools get_<resource> and get_<resource>_<key>."""
    spec_doc = copy.deepcopy(STOCK_SPEC)
    spec_doc["info"]["title"] = "%s API" % domain.capitalize()
    spec_doc["paths"] = {"/%s" % resource: spec_doc["paths"]["/stock"], "/%s/{sku}" % resource: spec_doc["paths"]["/stock/{sku}"]}
    (root / "src" / "api").mkdir(parents=True, exist_ok=True)
    (root / "src" / "mcp").mkdir(parents=True, exist_ok=True)
    spec = root / "src" / "api" / "openapi.yaml"
    spec.write_text(yaml.safe_dump(spec_doc, sort_keys=False), encoding="utf-8")
    cfg = root / "src" / "mcp" / "bridge-config.yaml"
    cfg.write_text(yaml.safe_dump({"domain": domain, "source_asset_id": "%s-api" % domain,
                                   "environments": {"dev": {"upstream_url": upstream_url}}}, sort_keys=False), encoding="utf-8")
    return spec, cfg


def stock_domain(root, upstream_url=STOCK_URL):
    """The synthetic Stock domain (the second domain of the shared Bridge in these tests)."""
    return synthetic_domain(root, "stock", "stock", upstream_url)


def shared():
    return sb.load_shared(TEST_PLATFORM["dev"], "dev")


def effective(cfg_path, out_dir):
    return sb.write_effective_config(sb.effective_config(pb.load_yaml(str(cfg_path)), shared()), str(out_dir))


def opts(mcp_version, uuid, source_label, upstream_url, label=SHARED_LABEL, base_path=SHARED_PATH):
    return {"org_id": ORG, "anypoint_env_id": ENV, "gateway_id": TARGET, "gateway_name": "mcp-dev-global",
            "gateway_version": "1.13.1", "tls_secret_group_id": TLS_SG, "tls_context_id": TLS_ID, "mcp_asset_version": mcp_version,
            "instance_label": label, "base_path": base_path, "base_url": "https://anypoint.mulesoft.com",
            "upstream_url": upstream_url, "artifacts_dir": None, "source_api_instance_id": uuid,
            "source_instance_label": source_label, "source_product_api_version": "v1"}


def domain_payload(eff_cfg, artifacts_dir, mcp_version, uuid, source_label, upstream_url, label=SHARED_LABEL, base_path=SHARED_PATH):
    """This domain's route exactly as the CREATE builder renders it."""
    return pb.build_payload(pb.load_yaml(str(eff_cfg)) if not isinstance(eff_cfg, dict) else eff_cfg, "dev", str(artifacts_dir),
                            opts(mcp_version, uuid, source_label, upstream_url, label, base_path))


def shared_state(payloads, mcp_version, *, instance_id=BRIDGE_ID, label=SHARED_LABEL, base_path=SHARED_PATH,
                 mcp_asset_id=SHARED_MCP, transcoding_path="/mcp"):
    """A live-looking SHARED Bridge serving one route per payload (in order): upstream
    ids up-1, up-2, ...; outbound policy ids 9213993, 9214003, ...; router 9213992."""
    routing, upstreams, policies, routes = [], [], [], []
    for i, p in enumerate(payloads):
        uid, pid = "up-%d" % (i + 1), 9213993 + 10 * i
        route, up = p["routing"][0], p["routing"][0]["upstreams"][0]
        routing.append({"label": route["label"], "rules": copy.deepcopy(route["rules"]), "upstreams": [{"id": uid}]})
        upstreams.append({"id": uid, "uri": up["uri"], "connection": copy.deepcopy(up["connection"])})
        policies.append({"policyId": pid, "order": 4, "upstreamId": uid, "template": {"assetId": "mcp-transcoding", "assetVersion": "1.0.1"},
                         "configuration": {"tools": copy.deepcopy(up["policies"][0]["configurationData"]["tools"])}})
        routes += copy.deepcopy(p["policies"][2]["configurationData"]["routes"])
    inst = {"id": instance_id, "instanceLabel": label, "environmentId": ENV, "status": "active",
            "metadata": {"generatedBy": "mcp_bridge"}, "assetId": mcp_asset_id, "assetVersion": mcp_version,
            "endpoint": {"type": "mcp", "proxyUri": "https://0.0.0.0:443/%s" % base_path,
                         "tlsContexts": {"inbound": {"secretGroupId": TLS_SG, "tlsContextId": TLS_ID}}},
            "deployment": {"environmentId": ENV, "type": "HY", "targetId": TARGET, "targetName": "mcp-dev-global",
                           "gatewayVersion": "1.13.1", "expectedStatus": "deployed"},
            "routing": routing}
    pols = {"policies": [
        {"policyId": 9213990, "order": 1, "template": {"assetId": "mcp-support", "assetVersion": "1.0.1"}, "configuration": {}},
        {"policyId": 9213991, "order": 2, "template": {"assetId": "mcp-schema-validation", "assetVersion": "1.1.0"}, "configuration": {"validateToolSchema": True}},
        {"policyId": 9213992, "order": 3, "template": {"assetId": "mcp-transcoding-router", "assetVersion": "1.0.1"},
         "configuration": {"transcodingPath": transcoding_path, "routes": routes}}] + policies}
    return {"inst": inst, "ups": {"upstreams": upstreams}, "pols": pols}


def merged_asset(*metadata_paths):
    """The shared MCP asset content for the given domains' generated metadata (bytes)."""
    merged = None
    for path in metadata_paths:
        meta = json.load(open(path, encoding="utf-8"))
        merged = sb.merge_metadata(merged, meta, [])
    return sb.render_metadata(merged)


def multipart_file(body, filename):
    """The bytes of the multipart part carrying `filename`."""
    marker = ('filename="%s"' % filename).encode()
    start = body.index(marker)
    start = body.index(b"\r\n\r\n", start) + 4
    end = body.index(b"\r\n--", start)
    return body[start:end]


class Resp:
    def __init__(self, obj, status=200, raw=None):
        self._b = raw if raw is not None else json.dumps(obj).encode()
        self.status = status
        self.headers = {}
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _404(url):
    return urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"message":"not found"}'))


def _semver(v):
    return tuple(int(x) for x in v.split("."))


class AnypointFake:
    """urlopen stand-in. `bridge` = live state {inst, ups, pols} of the shared Bridge (None = absent);
    `mcp_assets` = {version: metadata bytes} of the shared MCP asset already in Exchange;
    `rest_assets` = {asset id: {version: [external instances]}} - a REST asset absent from it
    answers 404 (so a run can never 'find' an asset the test did not declare); when None, every
    REST asset exists and lists `externals`."""

    def __init__(self, *, externals=(), bridge=None, extra_bridges=(), mcp_assets=None, mcp_asset_id=SHARED_MCP,
                 reject=None, mutate_on_instance_read=None, list_extra=(), rest_assets=None, other_mcp_assets=None):
        self.record, self.writes = [], []
        self.externals = [copy.deepcopy(e) for e in externals]
        self.bridge = bridge
        self.extra_bridges = list(extra_bridges) + list(list_extra)
        self.mcp_assets = dict(mcp_assets or {})
        self.mcp_asset_id = mcp_asset_id
        self.other_mcp_assets = dict(other_mcp_assets or {})  # {asset id: {version: bytes}} - other MCP assets in Exchange (never written)
        self.rest_assets = rest_assets
        self.rest_files = {}  # {rest asset id: {version: published oas projection bytes}} (filled by REST publishes or tests)
        self.reject = reject
        self.mutate_on_instance_read = mutate_on_instance_read  # (n, fn(state)) -> applied when the n-th instance GET happens
        self.instance_reads = 0
        self.created = None
        self.upstream_seq = 0
        self.policy_seq = 9300
        self.mcp_calls = []  # JSON-RPC methods served by the fake MCP endpoint (conformance)

    def promote_created_bridge(self, payload):
        """After a CREATE the platform serves the new instance like any other: build its live
        state from the create payload (ids assigned) so later reads, PATCHes and the MCP
        endpoint see it. Legacy tests that only inspect `created` keep working."""
        base_path = pb.base_path_of(payload["endpoint"]["proxyUri"])  # every segment, as the tooling derives it
        router = next(p for p in payload["policies"] if p["assetId"] == "mcp-transcoding-router")["configurationData"]
        self.bridge = shared_state([payload], payload["spec"]["version"], instance_id=NEW_ID, label=payload["instanceLabel"],
                                   base_path=base_path, mcp_asset_id=payload["spec"]["assetId"], transcoding_path=router["transcodingPath"])

    def _mcp_endpoint(self, url, data):
        """The gateway's MCP endpoint for the shared Bridge (mcp_conformance): initialize,
        notifications/initialized, tools/list = every tool of every outbound policy."""
        req = json.loads(data or b"{}")
        self.mcp_calls.append(req.get("method"))
        if not self.bridge or pb._path_component(url) != pb._path_component(self.bridge["inst"]["endpoint"]["proxyUri"]) + "/mcp":  # exact listener path
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"no bridge serves this path"))
        if req.get("method") == "initialize":
            body = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"protocolVersion": "2025-03-26", "serverInfo": {"name": self.bridge["inst"]["instanceLabel"], "version": "fake"}, "capabilities": {}}}
        elif req.get("method") == "tools/list":
            tools = [{"name": t["name"]} for p in self.bridge["pols"]["policies"] if p["template"]["assetId"] == "mcp-transcoding"
                     for t in p["configuration"]["tools"]]
            body = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"tools": tools}}
        else:
            body = None
        r = Resp(body) if body is not None else Resp(None, raw=b"")
        r.headers = {"Mcp-Session-Id": "fake-session"}
        return r

    # ── helpers ──
    def _latest(self):
        return max(self.mcp_assets, key=_semver) if self.mcp_assets else None

    def _rest_descriptor(self, asset, version):
        d = {"groupId": ORG, "assetId": asset, "version": version, "versionGroup": "v1",
             "instances": (self.rest_assets or {}).get(asset, {}).get(version, [])}
        if version in self.rest_files.get(asset, {}):
            d["files"] = [{"classifier": "oas", "packaging": "yaml", "mainFile": "openapi.yaml",
                           "externalLink": "https://anypoint.mulesoft.com%s%s/%s" % (FILES, asset, version)}]
        return d

    def _mcp_descriptor(self, asset_id, version):
        return {"groupId": ORG, "assetId": asset_id, "version": version, "versionGroup": "v1",
                "files": [{"classifier": "mcp-metadata", "packaging": "json",
                           "externalLink": "https://anypoint.mulesoft.com%s%s/%s" % (FILES, asset_id, version)}]}

    @staticmethod
    def routing_validation_errors(body):
        """The platform's xapi PATCH routing schema, as observed live on 2026-09-30 (HTTP 400
        RoutingValidationError on Bridge 21206639): every routing[].upstreams[] item is a
        REFERENCE - `id` required, any other property but `weight` is an unevaluated property.
        Returns the platform-shaped error list (empty = valid)."""
        errors = []
        for i, r in enumerate(body.get("routing") or []):
            for j, u in enumerate(r.get("upstreams") or []):
                path = "/%d/upstreams/%d" % (i, j)
                if "id" not in u:
                    errors.append({"instancePath": path, "schemaPath": "#/items/properties/upstreams/items/required", "keyword": "required",
                                   "params": {"missingProperty": "id"}, "message": "must have required property 'id'"})
                for extra in sorted(set(u) - {"id", "weight"}):
                    errors.append({"instancePath": path, "schemaPath": "#/items/properties/upstreams/items/unevaluatedProperties",
                                   "keyword": "unevaluatedProperties", "params": {"unevaluatedProperty": extra},
                                   "message": "must NOT have unevaluated properties"})
        return errors

    def _apply_instance_patch(self, st, body, url="https://anypoint.mulesoft.com/xapi"):
        errors = self.routing_validation_errors(body)
        if errors:
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, io.BytesIO(json.dumps(
                {"name": "RoutingValidationError", "errors": errors, "message": "The routing configuration is invalid"}).encode()))
        by_id = {u["id"]: u for u in st["ups"]["upstreams"]}
        for u in body["upstreams"]:
            if u.get("id") not in by_id:  # an upstream must exist (POST .../upstreams) before the PATCH references it
                raise urllib.error.HTTPError(url, 400, "Bad Request", None, io.BytesIO(json.dumps(
                    {"name": "UpstreamValidationError", "message": "unknown upstream %r" % u.get("id")}).encode()))
        for r in body["routing"]:
            if r["upstreams"][0]["id"] not in by_id:
                raise urllib.error.HTTPError(url, 400, "Bad Request", None, io.BytesIO(json.dumps(
                    {"name": "RoutingValidationError", "message": "route %r references unknown upstream %r" % (r["label"], r["upstreams"][0]["id"])}).encode()))
        st["inst"]["assetVersion"] = body["assetVersion"]
        for u in body["upstreams"]:
            by_id[u["id"]]["uri"] = u["uri"]
            by_id[u["id"]]["connection"] = copy.deepcopy(u["connection"])
            if u.get("label"):
                by_id[u["id"]]["label"] = u["label"]
        st["inst"]["routing"] = [{"label": r["label"], "rules": copy.deepcopy(r["rules"]), "upstreams": [{"id": r["upstreams"][0]["id"]}]}
                                 for r in body["routing"]]
        router = next(p for p in st["pols"]["policies"] if p["template"]["assetId"] == "mcp-transcoding-router")
        router["configuration"] = copy.deepcopy(next(p for p in body["policies"] if p["assetId"] == "mcp-transcoding-router")["configurationData"])

    def _create_upstream(self, st, body):
        """A1 - documented v1 POST .../apis/{id}/upstreams -> {id, label, uri}."""
        self.upstream_seq += 1
        up = {"id": "up-new-%d" % self.upstream_seq, "uri": body["uri"], "label": body.get("label")}
        st["ups"]["upstreams"].append(up)
        return up

    def _attach_outbound_policy(self, st, body, url):
        """A3 - xapi POST .../policies/outbound-policies: upstreamIds required (live 400 otherwise)."""
        ids = body.get("upstreamIds") or []
        known = {u["id"] for u in st["ups"]["upstreams"]}
        if not ids or any(i not in known for i in ids):
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, io.BytesIO(b'{"message":"upstreamIds must reference existing upstreams"}'))
        self.policy_seq += 1
        pol = {"policyId": self.policy_seq, "order": 4, "upstreamId": ids[0],
               "template": {"assetId": body["assetId"], "assetVersion": body["assetVersion"]},
               "configuration": copy.deepcopy(body["configurationData"])}
        st["pols"]["policies"].append(pol)
        return {"policyId": pol["policyId"]}

    # ── the fake ──
    def __call__(self, req, timeout=None):
        if isinstance(req, str):
            method, url, data = "GET", req, None
        else:
            method, url, data = req.get_method(), req.full_url, req.data
        if "anypoint.mulesoft.com" not in url:  # the gateway's MCP endpoint (conformance), never Anypoint's control plane
            self.record.append((method, url))
            return self._mcp_endpoint(url, data)
        path = url.split("anypoint.mulesoft.com", 1)[1]
        self.record.append((method, path))
        if path.endswith("/oauth2/token"):
            return Resp({"access_token": "tok"})
        if method == "DELETE":
            raise AssertionError("DELETE must never happen in the lifecycle: " + path)

        # ── Exchange: the shared MCP asset (+ other MCP assets that must only ever be read) ──
        for asset_id, versions in [(self.mcp_asset_id, self.mcp_assets)] + list(self.other_mcp_assets.items()):
            prefix = "/exchange/api/v2/assets/%s/%s/" % (ORG, asset_id)
            if method == "GET" and path.startswith(prefix):
                version = path[len(prefix):]
                if version == "asset":
                    if not versions:
                        raise _404(url)
                    latest = max(versions, key=_semver)
                    return Resp(dict(self._mcp_descriptor(asset_id, latest), otherVersions=sorted(versions, key=_semver)))
                if version not in versions:
                    raise _404(url)
                return Resp(self._mcp_descriptor(asset_id, version))
            if method == "GET" and path.startswith(FILES + asset_id + "/"):
                return Resp(None, raw=versions[path.rsplit("/", 1)[1]])
            publish_prefix = "/exchange/api/v2/organizations/%s/assets/%s/%s/" % (ORG, ORG, asset_id)
            if method == "POST" and path.startswith(publish_prefix):
                if asset_id != self.mcp_asset_id:
                    raise AssertionError("publish to a foreign MCP asset: " + path)
                version = path[len(publish_prefix):]
                content = multipart_file(data, "mcp-metadata.json")
                self.writes.append(("mcp-asset", {"version": version, "content": content, "metadata": json.loads(content.decode("utf-8"))}))
                self.mcp_assets[version] = content
                return Resp({"assetId": asset_id, "version": version})

        # ── Exchange: REST assets (descriptor, latest, published oas file, publish) + external instances ──
        m = re.match(r"^/exchange/api/v2/assets/%s/([a-z0-9-]+)/([^/]+)$" % ORG, path)
        if method == "GET" and m:
            asset, version = m.group(1), m.group(2)
            if version == "asset":  # release_version: the LATEST version of the REST asset
                versions = list((self.rest_assets or {}).get(asset) or {})
                if not versions:
                    raise _404(url)
                latest = max(versions, key=_semver)
                return Resp(dict(self._rest_descriptor(asset, latest), otherVersions=sorted(versions, key=_semver)))
            if self.rest_assets is not None:
                if asset not in self.rest_assets or version not in self.rest_assets[asset]:
                    raise _404(url)
                return Resp(self._rest_descriptor(asset, version))
            return Resp({"groupId": ORG, "assetId": asset, "version": version, "versionGroup": "v1", "instances": self.externals})
        m = re.match(r"^%s([a-z0-9-]+)/([^/]+)$" % FILES, path)
        if method == "GET" and m and m.group(1) in self.rest_files:
            return Resp(None, raw=self.rest_files[m.group(1)][m.group(2)])
        m = re.match(r"^/exchange/api/v2/organizations/%s/assets/%s/([a-z0-9-]+)/([^/]+)$" % (ORG, ORG), path)
        if method == "POST" and m:  # REST asset publish (exchange_publish rest --execute): classifier oas, file openapi.yaml
            asset, version = m.group(1), m.group(2)
            if self.reject == "rest-publish":
                raise urllib.error.HTTPError(url, 500, "boom", None, io.BytesIO(b'{"message":"publish rejected"}'))
            content = multipart_file(data, "openapi.yaml")
            self.writes.append(("rest-asset", {"asset": asset, "version": version, "content": content}))
            if self.rest_assets is None:
                self.rest_assets = {}
            self.rest_assets.setdefault(asset, {}).setdefault(version, [])
            self.rest_files.setdefault(asset, {})[version] = content
            return Resp({"assetId": asset, "version": version})
        m = re.match(r"^/exchange/api/v2/assets/%s/([a-z0-9-]+)/versionGroups/v1/instances/external$" % ORG, path)
        if method == "POST" and m:
            body = json.loads(data)
            self.writes.append(("external", dict(body, _asset=m.group(1))))
            inst = {"id": NEW_UUID, "type": "external", "name": body["name"], "endpointUri": body["endpointUri"], "isPublic": False, "productAPIVersion": "v1"}
            if self.rest_assets is not None:
                for v in self.rest_assets[m.group(1)].values():
                    v.append(inst)
            else:
                self.externals.append(inst)
            return Resp(inst)

        # ── API Manager reads ──
        if path.endswith("/apis?limit=250&offset=0"):
            apis = list(self.extra_bridges)
            if self.bridge:
                b = self.bridge["inst"]
                apis.append({"id": b["id"], "instanceLabel": b["instanceLabel"], "endpoint": {"proxyUri": b["endpoint"]["proxyUri"]},
                             "deployment": b["deployment"]})
            return Resp({"assets": [{"assetId": "x", "groupId": ORG, "apis": apis}]})
        if "/apis?limit=250&offset=" in path:
            return Resp({"assets": []})
        if "/gateway-targets/" in path and path.endswith(("/ports", "/apis")):
            return Resp([])
        if "/gateway-targets/" in path:
            return Resp({"id": TARGET, "gatewayVersion": "1.13.1"})
        if "/secrets-manager/" in path:
            return Resp({"name": "mcp-dev-global-tls"})

        # ── Bridge create ──
        if method == "POST" and path.endswith("/environments/%s/apis" % ENV):
            payload = json.loads(data)
            if self.reject == "create":
                raise urllib.error.HTTPError(url, 400, "Bad Request", None, io.BytesIO(b'{"message":"create rejected"}'))
            self.writes.append(("create", payload))
            self.created = payload
            self.promote_created_bridge(payload)  # from now on the new Bridge is served like any existing one
            return Resp({"id": NEW_ID, "instanceLabel": payload["instanceLabel"]})

        # ── the existing shared Bridge: reads + PATCHes ──
        if self.bridge and "/apis/%s" % self.bridge["inst"]["id"] in path:
            st = self.bridge
            if method == "GET":
                if path.split("?")[0].endswith("/upstreams"):
                    return Resp(st["ups"])
                if path.split("?")[0].endswith("/policies"):
                    return Resp(st["pols"])
                self.instance_reads += 1
                if self.mutate_on_instance_read and self.instance_reads == self.mutate_on_instance_read[0]:
                    self.mutate_on_instance_read[1](st)
                return Resp(st["inst"])
            if method == "POST" and "/xapi/" in path and path.endswith("/policies/outbound-policies"):
                body = json.loads(data)
                if self.reject == "outbound-policy":
                    raise urllib.error.HTTPError(url, 500, "boom", None, io.BytesIO(b'{"message":"outbound policy rejected"}'))
                result = self._attach_outbound_policy(st, body, url)
                self.writes.append(("post-outbound-policy", body))
                return Resp(result)
            if method == "POST" and "/api/v1/" in path and path.endswith("/upstreams"):
                body = json.loads(data)
                if self.reject == "upstream":
                    raise urllib.error.HTTPError(url, 400, "Bad Request", None, io.BytesIO(b'{"message":"upstream rejected"}'))
                up = self._create_upstream(st, body)
                self.writes.append(("post-upstream", body))
                return Resp(up)
            if method == "PATCH":
                body = json.loads(data)
                if "/xapi/" in path:
                    if self.reject == "instance":
                        raise urllib.error.HTTPError(url, 400, "Bad Request", None, io.BytesIO(b'{"message":"rejected"}'))
                    self._apply_instance_patch(st, body, url)  # validates the routing schema BEFORE recording the write
                    self.writes.append(("patch-instance", body))
                    return Resp({"id": st["inst"]["id"]})
                pid = int(path.rsplit("/", 1)[1])
                if self.reject == "policy" and pid == 9213992:
                    raise urllib.error.HTTPError(url, 500, "boom", None, io.BytesIO(b'{"message":"policy rejected"}'))
                self.writes.append(("patch-policy", {"policyId": pid, **body}))
                next(p for p in st["pols"]["policies"] if p["policyId"] == pid)["configuration"] = copy.deepcopy(body["configurationData"])
                return Resp({"policyId": pid})
        if method in ("PATCH", "POST") and "/apis/" in path:
            raise AssertionError("write to an instance the test did not declare as the shared Bridge: %s %s" % (method, path))
        if method == "GET" and "/apis/" in path:  # detail read of another instance during the collision scan
            iid = int(path.rsplit("/apis/", 1)[1].split("?")[0])
            return Resp(next((x for x in self.extra_bridges if x["id"] == iid), {}))
        raise AssertionError("unexpected %s %s" % (method, path))

    # ── assertions helpers ──
    def kinds(self):
        return [w[0] for w in self.writes]

    def methods(self):
        return {m for m, p in self.record if not p.endswith("/oauth2/token")}
