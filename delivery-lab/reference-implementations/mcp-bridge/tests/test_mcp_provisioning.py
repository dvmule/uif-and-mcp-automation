"""Provisioning-preview gate: the full Bridge create payload builds correctly
from (generated artifacts + bridge-config), and the input discipline holds —
external inputs that are not supplied surface as <UNRESOLVED:...> markers and
are never invented. No network access anywhere in these tests.

Imports resolve via qe/conftest.py (src/mcp on sys.path, bare names).
"""
from anypoint_fake import PLATFORM, PRODUCT_SPEC, TEST_PLATFORM_ENV
import json
from pathlib import Path

import pytest
import yaml

from contract_gate import expected_tools_from_contract
from generate_mcp_metadata import generate, write_artifacts
from provision_bridge import POLICY_PINS, build_payload, find_unresolved

REPO = Path(__file__).resolve().parents[4]
DOMAIN = REPO / "domains" / "product"  # fixture domain: the first MCP realization; generic behaviour is what is asserted
SPEC = PRODUCT_SPEC
DOMAIN_CONFIG = DOMAIN / "src" / "mcp" / "bridge-config.yaml"


def _effective_config_path():
    """provision_bridge consumes the EFFECTIVE config deploy_bridge.py hands it: the
    domain's inputs + the shared Bridge identity (mcp_asset_id). Test-owned canonical
    identities (anypoint_fake), written once per test session, never into the domain."""
    import tempfile
    from anypoint_fake import PRODUCT_CFG, effective
    return Path(effective(PRODUCT_CFG, tempfile.mkdtemp(prefix="uif-mcp-effective-")))


CONFIG = _effective_config_path()


def _contract_tools():
    """name -> derived record for every operation of the fixture contract (never a hand-written list)."""
    return {e["name"]: e for e in expected_tools_from_contract(str(SPEC))}


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def artifacts_dir(tmp_path_factory, cfg):
    out = tmp_path_factory.mktemp("mcp-artifacts")
    artifacts = generate(
        str(SPEC),
        source_ref={"assetId": cfg["source_asset_id"], "version": "1.0.0", "groupId": "test-group"},
        mcp_asset={"assetId": cfg["mcp_asset_id"], "version": "0.0.0-test"},
        org_id="test-group",
        route_label=cfg.get("route_label"),
        transcoding_path=cfg.get("transcoding_path", "/mcp"),
    )
    write_artifacts(artifacts, str(out))
    return str(out)


def _payload(cfg, artifacts_dir, **opts):
    base = dict(org_id=None, anypoint_env_id=None, gateway_id=None, gateway_name=None,
                gateway_version=None, tls_secret_group_id=None, tls_context_id=None,
                mcp_asset_version=None, instance_label=None, base_path=None,
                base_url="https://example.invalid")
    base.update(opts)
    return build_payload(cfg, "dev", artifacts_dir, base)


def _cfg_with_null_upstream(cfg):
    """Discipline tests use a synthetic unset env so they don't depend on the
    CURRENT dev value (which may hold the temporary bootstrap upstream)."""
    c = dict(cfg)
    c["environments"] = {"dev": {"upstream_url": None}}
    return c


def test_unresolved_inputs_are_marked_not_invented(cfg, artifacts_dir):
    payload = _payload(_cfg_with_null_upstream(cfg), artifacts_dir)
    problems = find_unresolved(payload)
    joined = "\n".join(problems)
    # every REQUIRED external input class is represented while unset
    assert "upstream_url" in joined
    assert "org_id" in joined and "tls_secret_group_id" in joined
    # source_api_instance_id is OPTIONAL — its absence is NOT an unresolved input
    assert "source_api_instance_id" not in joined
    # deploy-time values are their own marker kind, NOT unresolved blockers
    assert "gatewayVersion" not in joined and "mcp-asset-version" not in joined
    deploy_time = find_unresolved(payload, markers=("<CAPTURED-AT-DEPLOY:", "<DISCOVERED-AT-DEPLOY:"))
    assert len(deploy_time) == 2  # spec.version (captured) + gatewayVersion (discovered)


def test_naming_conventions_derived_from_domain(cfg, artifacts_dir):
    """DEV decisions: instance name uif-<domain>-mcp, base path /<domain> —
    derived, never typed; CLI overrides kept for controlled experiments."""
    p = _payload(cfg, artifacts_dir)
    assert p["instanceLabel"] == "uif-product-mcp"
    assert p["endpoint"]["proxyUri"] == "https://0.0.0.0:443/product"
    p2 = _payload(cfg, artifacts_dir, base_path="path-x", instance_label="label-x")
    assert p2["endpoint"]["proxyUri"] == "https://0.0.0.0:443/path-x"
    assert p2["instanceLabel"] == "label-x"


def test_shared_platform_config_covers_dev(cfg):
    """The shared MCP platform/environment configuration supplies the DEV
    coordinates once for all domains (never duplicated into bridge-config)."""
    import platform_config as pc
    reg = yaml.safe_load((Path(__file__).resolve().parents[1] / "scripts" / "anypoint-environments.yaml").read_text(encoding="utf-8"))
    assert all(str(reg["dev"][k]).startswith("${") for k in ("org_id", "env_id", "gateway_id", "tls_secret_group_id", "tls_context_id"))  # placeholders in Git
    dev = pc.resolve_environment(reg, "dev", env=TEST_PLATFORM_ENV)  # resolved with injected LOCAL TEST values
    for key in ("org_id", "env_id", "gateway_id", "gateway_name",
                "tls_secret_group_id", "tls_context_id"):
        assert dev.get(key), "missing platform registry key: %s" % key
    assert dev["gateway_name"] == "mcp-dev-global"
    assert "gateway_version" not in dev  # discovered dynamically, never configured
    assert "dev" in reg and "uat" not in reg and "prod" not in reg  # DEV-only for now
    # domain config stays minimal: no platform coordinates duplicated there
    assert not any(k in cfg for k in ("org_id", "env_id", "gateway_id",
                                      "tls_secret_group_id", "tls_context_id"))


def test_connectionless_payload_omits_connection_entirely(cfg, artifacts_dir):
    """source_api_instance_id absent -> NO connection key at all (no null, no
    placeholder). The upstream stays a plain URI + embedded outbound policy."""
    p = _payload(cfg, artifacts_dir, org_id="org-x")
    up = p["routing"][0]["upstreams"][0]
    assert "connection" not in up
    assert set(up) == {"weight", "uri", "policies"}
    outbound = up["policies"][0]
    assert outbound["assetId"] == "mcp-transcoding"
    mapped = {t["name"]: t for t in outbound["configurationData"]["tools"]}
    derived = _contract_tools()
    assert set(mapped) == set(derived) and len(derived) >= 1
    assert all(mapped[n]["path"] == derived[n]["path"] and mapped[n]["method"] == derived[n]["method"] for n in derived)


def test_supplied_instance_id_preserves_full_association(cfg, artifacts_dir):
    cfg2 = dict(cfg)
    cfg2["environments"] = {"dev": {"upstream_url": "https://backend.example/",
                                    "source_api_instance_id": "12345678"}}
    p = _payload(cfg2, artifacts_dir, org_id="org-x", instance_label="label-x")
    up = p["routing"][0]["upstreams"][0]
    conn = up["connection"]
    assert conn == {
        "label": "v1:12345678",
        "assetId": cfg["source_asset_id"],
        "groupId": "org-x",
        "version": "1.0.0",
        "assetName": cfg["source_asset_id"],
        "assetType": "rest",
        "apiVersionId": "12345678",
        "productAPIVersion": "v1",
    }
    assert up["uri"] == "https://backend.example/"


def test_payload_carries_the_proven_bridge_anatomy(cfg, artifacts_dir):
    p = _payload(cfg, artifacts_dir, org_id="org-x")
    assert p["technology"] == "flexGateway"
    assert p["metadata"] == {"generatedBy": "mcp_bridge"}
    route = p["routing"][0]
    expected_label = cfg.get("route_label", cfg["source_asset_id"])
    assert route["label"] == expected_label
    assert route["rules"]["headers"]["X-UPSTREAM-NAME"] == expected_label


def test_inline_policies_use_the_live_verified_pins(cfg, artifacts_dir):
    p = _payload(cfg, artifacts_dir)
    inline = {pol["assetId"]: pol for pol in p["policies"]}
    assert set(inline) == {"mcp-support", "mcp-schema-validation", "mcp-transcoding-router"}
    for asset_id, pol in inline.items():
        assert pol["assetVersion"] == POLICY_PINS[asset_id]
    router = inline["mcp-transcoding-router"]["configurationData"]
    assert router["transcodingPath"] == cfg.get("transcoding_path", "/mcp")
    assert router["routes"][0]["upstreamName"] == cfg.get("route_label", cfg["source_asset_id"])
    assert set(router["routes"][0]["tools"]) == set(_contract_tools())
    # router pinned to 1.0.1 — 1.0.0 stores but cannot render transcodingPath
    assert inline["mcp-transcoding-router"]["assetVersion"] == "1.0.1"


def test_supplying_inputs_resolves_the_markers(cfg, artifacts_dir):
    # platform values supplied; gateway version and MCP asset version NOT
    # supplied (deploy-time discovered/captured); label/base path derived
    opts = dict(org_id="org-x", anypoint_env_id="env-x", gateway_id="gw-x",
                gateway_name="gw", tls_secret_group_id="sg-x", tls_context_id="tls-x")
    # with the env upstream unset, exactly ONE required input remains
    p = _payload(_cfg_with_null_upstream(cfg), artifacts_dir, **opts)
    problems = find_unresolved(p)
    assert len(problems) == 1 and "upstream_url" in problems[0]
    deploy_time = find_unresolved(p, markers=("<CAPTURED-AT-DEPLOY:", "<DISCOVERED-AT-DEPLOY:"))
    assert len(deploy_time) == 2
    # with the CURRENT config (dev carries the temporary bootstrap upstream),
    # nothing blocking remains at all
    if (cfg["environments"].get("dev") or {}).get("upstream_url"):
        assert find_unresolved(_payload(cfg, artifacts_dir, **opts)) == []


def test_detector_finds_markers_embedded_in_strings():
    """Regression: a marker inside a longer string (e.g. proxyUri) must be
    detected — containment, not prefix."""
    node = {"endpoint": {"proxyUri": "https://0.0.0.0:443/<UNRESOLVED:base_path (bridge-config)>"},
            "ok": "https://resolved.example/path"}
    found = find_unresolved(node)
    assert len(found) == 1 and "proxyUri" in found[0]


def test_payload_is_json_serializable(cfg, artifacts_dir):
    json.dumps(_payload(cfg, artifacts_dir))


# ── --preflight: authenticated READ-ONLY checks, never a create ────────────────

class _FakeResponse:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode()
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


LIST_SUMMARY_KEYS = ("id", "instanceLabel", "assetVersion", "endpointUri", "status")


def _fake_anypoint(record, *, gateway_targets_status=200, env_apis=None, target_ports=None,
                   target_apis=None, list_total=None, summaries_only=True):
    """urlopen stand-in: serves the PoC-verified response shapes and records
    every (method, path). The environment LIST returns instance SUMMARIES
    (no endpoint.proxyUri - the live shape that hid '/product'); the detail
    read returns the full record. A create POST raises so the test fails loudly."""
    import urllib.error
    apis = env_apis if env_apis is not None else [{
        "id": 21164696, "instanceLabel": "poc-uifull",
        "endpoint": {"proxyUri": "https://0.0.0.0:443/poc-uifull",
                     "tlsContexts": {"inbound": {"secretGroupId": "425b3a87-3817-4678-bdcb-e2138fd702f5",
                                                 "tlsContextId": "abeec1e3-c2f0-4b60-845f-ba76240d7f8a"}}},
        "deployment": {"targetId": "2936f2eb-716f-47f1-a41f-fcfd09518867", "gatewayVersion": "1.13.1"}}]
    listed = [{k: v for k, v in a.items() if k in LIST_SUMMARY_KEYS} for a in apis] if summaries_only else apis

    def urlopen(req, timeout=None):
        method, path = req.get_method(), req.full_url.split("anypoint.mulesoft.com", 1)[1]
        record.append((method, path))
        if path.endswith("/oauth2/token"):
            return _FakeResponse({"access_token": "t"})
        if method != "GET":
            raise AssertionError("non-GET call during preflight: %s %s" % (method, path))
        if "/gateway-targets/" in path and path.endswith("/ports"):
            if target_ports is None:
                raise urllib.error.HTTPError(req.full_url, 404, "x", {}, None)
            return _FakeResponse(target_ports)
        if "/gateway-targets/" in path and path.endswith("/apis"):
            if target_apis is None:
                raise urllib.error.HTTPError(req.full_url, 404, "x", {}, None)
            return _FakeResponse(target_apis)
        if "/gateway-targets/" in path:
            if gateway_targets_status != 200:
                raise urllib.error.HTTPError(req.full_url, gateway_targets_status, "x", {}, None)
            return _FakeResponse({"id": "2936f2eb-716f-47f1-a41f-fcfd09518867", "gatewayVersion": "1.13.5"})
        if "/secrets-manager/" in path:
            return _FakeResponse({"name": "mcp-dev-global-tls"})
        if "/apis/" in path and "/apis?" not in path:  # single-instance v1 read (detail)
            inst_id = int(path.rsplit("/apis/", 1)[1].split("?")[0])
            return _FakeResponse(next((a for a in apis if a.get("id") == inst_id), {}))
        if path.endswith("/apis?limit=250&offset=0"):
            page = {"assets": [{"apis": listed}]}
            if list_total is not None:
                page["total"] = list_total
            return _FakeResponse(page)
        if "/apis?limit=250&offset=" in path:
            return _FakeResponse({"assets": []})
        raise AssertionError("unexpected read " + path)
    return urlopen


def _run_preflight(monkeypatch, record, artifacts_dir, extra_args=(), **fake_kwargs):
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")
    monkeypatch.setattr(urllib.request, "urlopen", _fake_anypoint(record, **fake_kwargs))
    return provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir, "--platform-config", PLATFORM,  # test-owned literal platform
                                  "--environment", "dev", "--mcp-asset-version", "1.0.0", "--preflight", *extra_args])


def test_preflight_is_read_only_and_never_creates(artifacts_dir, monkeypatch, capsys):
    record = []
    assert _run_preflight(monkeypatch, record, artifacts_dir) == 0
    methods = {m for m, p in record if not p.endswith("/oauth2/token")}
    assert methods == {"GET"}
    assert not any("/apis" == p.rsplit("/", 1)[-1] and m == "POST" for m, p in record)
    out = capsys.readouterr().out
    assert "gatewayVersion 1.13.5 [gateway target read" in out
    assert "secrets-manager read: PASS" in out
    assert "bound to existing instances (v1 read): PASS (21164696)" in out
    assert "connection-less (source_api_instance_id not supplied" in out
    assert "collision scan: PASS" in out and "'uif-product-mcp'" in out and "/product" in out
    assert "PREFLIGHT ONLY" in out and "NO create was sent" in out
    assert "s3cr3t-VALUE-never-printed" not in out  # credential value never printed


def test_preflight_falls_back_to_deployed_instance_for_gateway_version(artifacts_dir, monkeypatch, capsys):
    record = []
    assert _run_preflight(monkeypatch, record, artifacts_dir, gateway_targets_status=405) == 0
    out = capsys.readouterr().out
    assert "gatewayVersion 1.13.1 [deployment.gatewayVersion of instance 21164696" in out
    assert "gateway-targets read HTTP 405" in out


def test_preflight_refuses_label_and_base_path_collisions(artifacts_dir, monkeypatch):
    record = []
    tls = {"inbound": {"secretGroupId": "425b3a87-3817-4678-bdcb-e2138fd702f5",
                       "tlsContextId": "abeec1e3-c2f0-4b60-845f-ba76240d7f8a"}}
    taken = [{"id": 1, "instanceLabel": "uif-product-mcp",
              "endpoint": {"proxyUri": "https://0.0.0.0:443/other", "tlsContexts": tls},
              "deployment": {"targetId": "2936f2eb-716f-47f1-a41f-fcfd09518867", "gatewayVersion": "1.13.1"}},
             {"id": 2, "instanceLabel": "x",
              "endpoint": {"proxyUri": "https://0.0.0.0:443/product", "tlsContexts": tls},
              "deployment": {"targetId": "2936f2eb-716f-47f1-a41f-fcfd09518867", "gatewayVersion": "1.13.1"}}]
    with pytest.raises(SystemExit) as e:
        _run_preflight(monkeypatch, record, artifacts_dir, env_apis=taken)
    msg = str(e.value)
    assert "collision guard" in msg and "label collision with instance 1" in msg
    assert "base path /product collision with instance 2 (proxyUri https://0.0.0.0:443/product)" in msg
    assert all(m == "GET" for m, p in record if not p.endswith("/oauth2/token"))


def test_preflight_requires_live_latch_and_asset_version(artifacts_dir, monkeypatch):
    import provision_bridge
    monkeypatch.delenv("MCP_LIVE_ENABLED", raising=False)
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir,
                               "--environment", "dev", "--mcp-asset-version", "1.0.0", "--preflight"])
    assert "MCP_LIVE_ENABLED" in str(e.value)
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir,
                               "--environment", "dev", "--preflight"])
    assert "--mcp-asset-version" in str(e.value)


# ── post-create read-back verification matches what was sent ──────────────────

def _readback(payload, inst_extra=None, connection=None, policies=None, tools=None, router=None,
              upstream_id="up-1"):
    """Read-back triple that mirrors WHAT THE PAYLOAD SENT (PoC read-back shapes):
    outbound mcp-transcoding persisted under `configuration.tools` bound to the
    upstream id; router config with transcodingPath + routes; routing rules."""
    from provision_bridge import EXPECTED_POLICIES
    sent_up = payload["routing"][0]["upstreams"][0]
    sent_router = next(p for p in payload["policies"] if p["assetId"] == "mcp-transcoding-router")["configurationData"]
    inst = {"id": 1, "metadata": {"generatedBy": "mcp_bridge"}, "status": "active",
            "assetId": payload["spec"]["assetId"], "assetVersion": payload["spec"]["version"],
            "endpoint": {"proxyUri": payload["endpoint"]["proxyUri"]},
            "routing": [{"label": payload["routing"][0]["label"], "rules": payload["routing"][0]["rules"],
                         "upstreams": [{"id": upstream_id, "weight": 100}]}]}
    inst.update(inst_extra or {})
    up = {"id": upstream_id, "uri": sent_up["uri"]}
    if connection:
        up["connection"] = connection
    names = policies if policies is not None else sorted(EXPECTED_POLICIES)
    plist = []
    for a in names:
        pol = {"template": {"assetId": a, "assetVersion": "1.0.1"}, "configuration": {}}
        if a == "mcp-transcoding":
            pol["upstreamId"] = upstream_id
            pol["configuration"] = {"tools": tools if tools is not None else sent_up["policies"][0]["configurationData"]["tools"]}
        if a == "mcp-transcoding-router":
            pol["configuration"] = router if router is not None else sent_router
        plist.append(pol)
    return inst, {"upstreams": [up]}, {"policies": plist}


def test_readback_passes_for_connection_less_bridge(cfg, artifacts_dir):
    """Connection-less is DATA-complete but UI-incomplete (21166173, 2026-09-11):
    the verifier reports the UI gap and fails unless it is explicitly accepted."""
    from provision_bridge import verify_readback
    payload = _payload(cfg, artifacts_dir, mcp_asset_version="1.0.0")
    assert not any("connection" in u for r in payload["routing"] for u in r["upstreams"])
    ok, lines = verify_readback(payload, *_readback(payload))
    assert not ok, lines  # connection-less = UI gap -> FAIL unless explicitly accepted
    assert any(l.startswith("UI parity: GAP") and "FAIL" in l for l in lines)
    ok, lines = verify_readback(payload, *_readback(payload), allow_ui_gap=True)
    assert ok, lines
    assert any("ACCEPTED (--allow-connectionless-ui-gap)" in l for l in lines)


def test_readback_demands_connection_when_payload_sent_one(cfg, artifacts_dir):
    from provision_bridge import verify_readback
    c = dict(cfg)
    c["environments"] = {"dev": {"upstream_url": "https://u/", "source_api_instance_id": "123"}}
    payload = build_payload(c, "dev", artifacts_dir, dict(
        org_id="o", anypoint_env_id="e", gateway_id="g", gateway_name="n", gateway_version="1",
        tls_secret_group_id="s", tls_context_id="t", mcp_asset_version="1.0.0",
        instance_label=None, base_path=None, base_url="https://example.invalid"))
    ok, lines = verify_readback(payload, *_readback(payload))
    assert not ok and "upstream connection present (UI APIs table + Tools & mapping source): FAIL" in lines
    # a partially persisted connection is NOT enough any more: it must round-trip as sent
    ok, lines = verify_readback(payload, *_readback(payload, connection={"assetId": "product-api", "apiVersionId": "123"}))
    assert not ok and any("connection persisted as sent" in l and l.endswith("FAIL") for l in lines)
    sent = payload["routing"][0]["upstreams"][0]["connection"]
    ok, _ = verify_readback(payload, *_readback(payload, connection=dict(sent)))
    assert ok


def test_readback_fails_on_wrong_generatedby_path_or_policies(cfg, artifacts_dir):
    from provision_bridge import verify_readback
    payload = _payload(cfg, artifacts_dir, mcp_asset_version="1.0.0")
    kw = dict(allow_ui_gap=True)
    assert verify_readback(payload, *_readback(payload), **kw)[0]
    assert not verify_readback(payload, *_readback(payload, inst_extra={"metadata": {}}), **kw)[0]
    assert not verify_readback(payload, *_readback(payload, inst_extra={"endpoint": {"proxyUri": "https://0.0.0.0:443/other"}}), **kw)[0]
    assert not verify_readback(payload, *_readback(payload, policies=["mcp-support"]), **kw)[0]


# ── failed create: capture the platform's rejection safely; prove nothing left behind ──

def test_outbound_policy_matches_live_proven_shape_no_order(cfg, artifacts_dir):
    """The embedded outbound mcp-transcoding policy is sent WITHOUT `order`
    (the shape of the PoC payloads that created 21164696/21164754); inline
    policies keep order 1..3. Sending order on the outbound policy was the
    one structural deviation from the proven payload in the 2026-09-11 400."""
    p = _payload(cfg, artifacts_dir)
    outbound = p["routing"][0]["upstreams"][0]["policies"][0]
    assert set(outbound) == {"groupId", "assetId", "assetVersion", "configurationData"}
    assert [pol["order"] for pol in p["policies"]] == [1, 2, 3]


def test_describe_http_error_redacts_secrets_and_keeps_safe_headers_only():
    import io
    import urllib.error
    from email.message import Message
    from provision_bridge import describe_http_error
    hdrs = Message()
    hdrs["Content-Type"] = "application/json"
    hdrs["X-Request-Id"] = "req-1"
    hdrs["Set-Cookie"] = "session=abc"
    body = json.dumps({"message": "Invalid payload", "errors": [{"field": "routing[0].upstreams[0].connection",
                                                                  "token": "TOKEN-VALUE"}]}).encode()
    err = urllib.error.HTTPError("https://x/apis", 400, "Bad Request", hdrs, io.BytesIO(body))
    rep = describe_http_error(err, "TOKEN-VALUE", "s3cr3t")
    assert rep["status"] == 400
    assert rep["headers"] == {"Content-Type": "application/json", "X-Request-Id": "req-1"}
    assert "TOKEN-VALUE" not in rep["body"] and "<redacted>" in rep["body"]
    assert "routing[0].upstreams[0].connection" in rep["body"]  # the actual validation detail survives


def test_execute_reports_400_body_writes_evidence_and_does_no_readback(artifacts_dir, monkeypatch, capsys):
    import io
    import urllib.error
    import urllib.request
    import provision_bridge
    record = []
    base = _fake_anypoint(record)
    rejection = json.dumps({"name": "BadRequestError", "message": "field X is required"}).encode()

    def urlopen(req, timeout=None):
        if req.get_method() == "POST" and req.full_url.endswith("/apis"):
            record.append(("POST", "/apis"))
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", None, io.BytesIO(rejection))
        return base(req, timeout)
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir,
                               "--environment", "dev", "--mcp-asset-version", "1.0.0", "--execute"])
    msg = str(e.value)
    assert "CREATE REJECTED - HTTP 400" in msg and "field X is required" in msg and "--check-existing" in msg
    assert "s3cr3t-VALUE-never-printed" not in msg
    evidence = json.load(open(f"{artifacts_dir}/create-rejected.json", encoding="utf-8"))
    assert evidence["status"] == 400 and "field X is required" in evidence["response_body"]
    assert evidence["payload"]["instanceLabel"] == "uif-product-mcp"
    # exactly one POST to /apis, and no read-backs afterwards
    assert [p for m, p in record if m == "POST" and p == "/apis"] == ["/apis"]
    assert not any("/apis/" in p and m == "GET" and p.endswith(("/upstreams", "/policies")) for m, p in record)


def test_check_existing_is_read_only_and_reports_none_or_hits(artifacts_dir, monkeypatch, capsys):
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    args = ["--config", str(CONFIG), "--artifacts", artifacts_dir, "--environment", "dev", "--check-existing"]
    record = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_anypoint(record))
    assert provision_bridge.main(args) == 0
    out = capsys.readouterr().out
    assert "NONE FOUND" in out and "nothing was created" in out
    assert {m for m, p in record if not p.endswith("/oauth2/token")} == {"GET"}
    record = []
    leftover = [{"id": 99, "instanceLabel": "uif-product-mcp", "status": "unregistered",
                 "endpoint": {"proxyUri": "https://0.0.0.0:443/product"},
                 "deployment": {"targetName": "mcp-dev-global"}}]
    monkeypatch.setattr(urllib.request, "urlopen", _fake_anypoint(record, env_apis=leftover))
    assert provision_bridge.main(args) == 0
    out = capsys.readouterr().out
    assert "FOUND [label+base path]: id=99" in out and "status=unregistered" in out
    assert {m for m, p in record if not p.endswith("/oauth2/token")} == {"GET"}


# ── target-level path occupancy (regression for the 2026-09-11 '/product already in use' 400) ──

TARGET = "2936f2eb-716f-47f1-a41f-fcfd09518867"


def _inst(iid, label, path, **extra):
    d = {"id": iid, "instanceLabel": label, "endpoint": {"proxyUri": "https://0.0.0.0:443/%s" % path},
         "deployment": {"targetId": TARGET, "gatewayVersion": "1.13.1"}}
    d.update(extra)
    return d


def test_list_summaries_hide_proxy_uri_so_detail_reads_are_mandatory(artifacts_dir, monkeypatch):
    """Reproduces the miss: the environment list carries no endpoint.proxyUri.
    An instance serving /product under another label must now be found via
    the per-instance detail read (includeProxyConfiguration=true)."""
    record = []
    apis = [_inst(21164696, "poc-uifull", "poc-uifull"), _inst(555, "someone-elses-api", "product")]
    with pytest.raises(SystemExit) as e:
        _run_preflight(monkeypatch, record, artifacts_dir, env_apis=apis)
    assert "base path /product collision with instance 555" in str(e.value)
    assert any(p.endswith("/apis/555?includeProxyConfiguration=true") for m, p in record)
    assert {m for m, p in record if not p.endswith("/oauth2/token")} == {"GET"}


def test_target_inventory_detects_occupancy_no_instance_exposes(artifacts_dir, monkeypatch):
    """The platform's validation is target-scoped; when the xapi gateway-target
    inventory lists the path, refuse even if no instance detail shows it."""
    record = []
    ports = [{"port": 443, "paths": [{"path": "/product", "apiVersionId": 777}, {"path": "/other"}]}]
    with pytest.raises(SystemExit) as e:
        _run_preflight(monkeypatch, record, artifacts_dir, target_ports=ports)
    assert "base path /product already in use on the target [gateway-target ports (xapi)]" in str(e.value)


def test_target_inventory_matches_exact_segment_only(artifacts_dir, monkeypatch, capsys):
    """/product occupied must NOT block /productTest (and vice versa)."""
    record = []
    ports = [{"port": 443, "paths": [{"path": "/product"}]}]
    assert _run_preflight(monkeypatch, record, artifacts_dir, extra_args=["--base-path", "productTest"],
                          target_ports=ports) == 0
    out = capsys.readouterr().out
    assert "collision scan: PASS" in out and "/productTest" in out


def test_base_path_override_is_run_scoped_and_keeps_identity(artifacts_dir, monkeypatch, capsys):
    record = []
    assert _run_preflight(monkeypatch, record, artifacts_dir, extra_args=["--base-path", "productTest"]) == 0
    out = capsys.readouterr().out
    assert "BASE PATH OVERRIDE (this run only): /productTest - the domain standard remains /product" in out
    assert '"proxyUri": "https://0.0.0.0:443/productTest"' in out
    assert '"instanceLabel": "uif-product-mcp"' in out
    assert "no instance labeled 'uif-product-mcp'; /productTest not served" in out
    # the committed standard is untouched
    assert yaml.safe_load(CONFIG.read_text(encoding="utf-8")).get("base_path") in (None, "product")


def test_unusable_target_inventory_is_reported_not_passed(artifacts_dir, monkeypatch, capsys):
    record = []
    assert _run_preflight(monkeypatch, record, artifacts_dir) == 0  # default fake: both target reads 404
    out = capsys.readouterr().out
    assert "target inventory - gateway-target ports (xapi): HTTP 404 (not usable)" in out
    assert "target inventory - gateway-target apis (xapi): HTTP 404 (not usable)" in out


def test_incomplete_environment_listing_fails_closed(artifacts_dir, monkeypatch):
    record = []
    with pytest.raises(SystemExit) as e:
        _run_preflight(monkeypatch, record, artifacts_dir, list_total=300)
    assert "inventory incomplete" in str(e.value)


def test_collision_hits_pure_cases():
    from provision_bridge import collision_hits, _path_component
    assert _path_component("https://0.0.0.0:443/product/") == "/product"
    assert _path_component("/productTest") == "/productTest"
    assert _path_component("https://host") == "/"
    assert _path_component("plain") is None
    resolved = [({"id": 1, "instanceLabel": "x"}, "https://0.0.0.0:443/product"),
                ({"id": 2, "instanceLabel": "uif-product-mcp"}, ""),
                ({"id": 3, "instanceLabel": "y"}, "https://0.0.0.0:443/productTest")]
    inv = {"gateway-target ports (xapi)": {"status": "ok", "paths": {"/product", "/z"}}}
    hits = collision_hits("uif-product-mcp", "product", resolved, inv)
    assert hits == sorted([
        "base path /product collision with instance 1 (proxyUri https://0.0.0.0:443/product)",
        "label collision with instance 2",
        "base path /product already in use on the target [gateway-target ports (xapi)]"])
    assert collision_hits("other", "productTest", resolved, inv) == [
        "base path /productTest collision with instance 3 (proxyUri https://0.0.0.0:443/productTest)"]


# ── hardened read-back: the 2026-09-11 gap (21166173 passed the old verifier with an
#    empty UI APIs table and empty Tools & mapping) can never be reported as success ──

def test_readback_fails_when_tool_mappings_not_persisted(cfg, artifacts_dir):
    from provision_bridge import verify_readback
    payload = _payload(cfg, artifacts_dir, mcp_asset_version="1.0.0")
    kw = dict(allow_ui_gap=True)
    sent = payload["routing"][0]["upstreams"][0]["policies"][0]["configurationData"]["tools"]
    ok, lines = verify_readback(payload, *_readback(payload, tools=[]), **kw)
    assert not ok and "persisted outbound tools=NONE" in lines
    assert any(l.startswith("outbound mcp-transcoding persisted with tools %s: FAIL" % ", ".join(sorted(t["name"] for t in sent))) for l in lines)  # provisioner prints them sorted
    # one mapping missing, or a mapping with a wrong path, also fails
    assert not verify_readback(payload, *_readback(payload, tools=sent[:-1]), **kw)[0]
    wrong = [dict(sent[0], path=sent[0]["path"] + "-wrong")] + sent[1:]
    assert not verify_readback(payload, *_readback(payload, tools=wrong), **kw)[0]


def test_readback_fails_on_router_binding_asset_uri_or_rule_drift(cfg, artifacts_dir):
    from provision_bridge import verify_readback
    payload = _payload(cfg, artifacts_dir, mcp_asset_version="1.0.0")
    kw = dict(allow_ui_gap=True)
    assert not verify_readback(payload, *_readback(payload, router={"transcodingPath": "/mcp", "routes": []}), **kw)[0]
    assert not verify_readback(payload, *_readback(payload, router={"routes": payload["policies"][2]["configurationData"]["routes"]}), **kw)[0]
    inst, ups, pol = _readback(payload)
    idx = [p["template"]["assetId"] for p in pol["policies"]].index("mcp-transcoding")
    pol["policies"][idx]["upstreamId"] = "someone-else"
    assert not verify_readback(payload, inst, ups, pol, **kw)[0]
    assert not verify_readback(payload, *_readback(payload, inst_extra={"assetVersion": "0.9.0"}), **kw)[0]
    inst, ups, pol = _readback(payload)
    ups["upstreams"][0]["uri"] = "https://other/"
    assert not verify_readback(payload, inst, ups, pol, **kw)[0]
    inst, ups, pol = _readback(payload)
    inst["routing"][0]["rules"] = {}
    ok, lines = verify_readback(payload, inst, ups, pol, **kw)
    assert not ok and any(l.startswith("X-UPSTREAM-NAME rule product-api: FAIL") for l in lines)


def test_summarize_readback_exposes_ui_relevant_facts(cfg, artifacts_dir):
    from provision_bridge import summarize_readback
    payload = _payload(cfg, artifacts_dir, mcp_asset_version="1.0.0")
    s = summarize_readback(*_readback(payload, connection={"assetType": "rest", "assetId": "product-api",
                                                            "version": "1.0.0", "apiVersionId": "42"}))
    assert s["mcp asset binding"] == "uif-domains-mcp@1.0.0"  # the SHARED Bridge's MCP asset (effective config)
    assert s["upstreams"][0]["connection"] == "rest product-api@1.0.0 instance 42"
    assert s["outbound mcp-transcoding"][0]["tools"] == [t["name"] for t in payload["routing"][0]["upstreams"][0]["policies"][0]["configurationData"]["tools"]]
    assert s["router"][0]["transcodingPath"] == "/mcp"
    assert s["X-UPSTREAM-NAME rules"] == ["product-api"]


def test_inspect_is_read_only_and_saves_raw_reads(artifacts_dir, monkeypatch, capsys):
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    record = []
    base = _fake_anypoint(record)

    def urlopen(req, timeout=None):
        path = req.full_url.split("anypoint.mulesoft.com", 1)[1]
        if req.get_method() == "GET" and "/apis/" in path:
            record.append(("GET", path))
            if path.endswith("/upstreams"):
                return _FakeResponse({"upstreams": [{"id": "u1", "uri": "https://x/"}]})
            if path.endswith("/policies"):
                return _FakeResponse({"policies": [{"template": {"assetId": "mcp-transcoding", "assetVersion": "1.0.1"},
                                                    "upstreamId": "u1", "configuration": {"tools": [{"name": "get_products"}]}}]})
            return _FakeResponse({"id": int(path.rsplit("/apis/", 1)[1].split("?")[0]), "instanceLabel": "uif-product-mcp",
                                  "assetId": "uif-product-mcp", "assetVersion": "1.0.0", "metadata": {"generatedBy": "mcp_bridge"},
                                  "endpoint": {"proxyUri": "https://0.0.0.0:443/productTest"}, "routing": [{"label": "product-api"}]})
        return base(req, timeout)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    rc = provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir, "--environment", "dev",
                                "--inspect", "21166173", "--reference", "21164754"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "== INSPECT (read-only" in out and "reference 21164754" in out
    assert '"connection": null' in out and '"tools": ["get_products"]' in out
    for iid in ("21166173", "21164754"):
        for name in ("instance", "upstreams", "policies"):
            assert (Path(artifacts_dir) / ("inspect-%s-%s.json" % (iid, name))).exists()
    assert all(m == "GET" for m, p in record if not p.endswith("/oauth2/token"))


def test_inspect_survives_missing_reference_and_reports_it(artifacts_dir, monkeypatch, capsys):
    """Regression (2026-09-11): the PoC reference 21164754 returned 404 and the
    whole inspect aborted before printing the primary instance."""
    import urllib.error
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    record = []
    base = _fake_anypoint(record)

    def urlopen(req, timeout=None):
        path = req.full_url.split("anypoint.mulesoft.com", 1)[1]
        if req.get_method() == "GET" and "/apis/" in path:
            record.append(("GET", path))
            if "/apis/21164754" in path:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)
            if path.endswith("/upstreams"):
                return _FakeResponse({"upstreams": [{"id": "u1", "uri": "https://x/"}]})
            if path.endswith("/policies"):
                return _FakeResponse({"policies": [{"template": {"assetId": "mcp-transcoding", "assetVersion": "1.0.1"}, "upstreamId": "u1",
                                                    "configuration": {"tools": [{"name": "get_products", "method": "GET", "path": "/api/v1/products"}]}}]})
            return _FakeResponse({"id": 21166173, "instanceLabel": "uif-product-mcp", "assetId": "uif-product-mcp", "assetVersion": "1.0.0",
                                  "endpoint": {"proxyUri": "https://0.0.0.0:443/productTest"},
                                  "routing": [{"label": "product-api", "rules": {"headers": {"X-UPSTREAM-NAME": "product-api"}}}]})
        return base(req, timeout)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    rc = provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir, "--environment", "dev",
                                "--inspect", "21166173", "--reference", "21164754"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reference 21164754: NOT FOUND (HTTP 404) - showing instance 21166173 alone" in out
    assert '"mappings": ["get_products GET /api/v1/products"]' in out and '"upstreamId": "u1"' in out
    assert '"X-UPSTREAM-NAME rules": ["product-api"]' in out or '["product-api"]' in out
    assert all(m == "GET" for m, p in record if not p.endswith("/oauth2/token"))
    # a primary that does not exist fails with a clear message, still read-only
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir, "--environment", "dev", "--inspect", "21164754"])
    assert "instance 21164754: NOT READABLE (HTTP 404)" in str(e.value)


# ── read-only probe: does the wizard's source-instance list include registration-only instances? ──

def test_probe_source_instances_is_read_only_and_compares_wizard_list(artifacts_dir, monkeypatch, capsys):
    import urllib.error
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    record = []
    org = "8c27297e-4bcf-4c53-99eb-ffd7b1069294"
    listing = {"total": 2, "assets": [
        {"assetId": "legacy-api", "groupId": org, "apis": [
            {"id": 111, "instanceLabel": "legacy-basic", "technology": "mule4", "status": "unregistered", "productVersion": "v1"},
            {"id": 112, "instanceLabel": "legacy-flex", "technology": "flexGateway", "status": "active", "productVersion": "v1"}]},
        {"assetId": "other-api", "groupId": org, "apis": [
            {"id": 200, "instanceLabel": "x", "technology": "flexGateway", "status": "active", "productVersion": "v1"}]}]}

    def urlopen(req, timeout=None):
        method, path = req.get_method(), req.full_url.split("anypoint.mulesoft.com", 1)[1]
        record.append((method, path))
        if path.endswith("/oauth2/token"):
            return _FakeResponse({"access_token": "t"})
        assert method == "GET", path
        if path.endswith("/apis?limit=250&offset=0"):
            return _FakeResponse(listing)
        if "/apis?limit=250&offset=" in path:
            return _FakeResponse({"assets": []})
        if "/assets/legacy-api/productVersions/v1/instances" in path:
            return _FakeResponse({"total": 2, "instances": [{"apiVersionId": 111, "technology": "mule4"},
                                                            {"apiVersionId": 112, "technology": "flexGateway"}]})
        if "/assets/product-api/productVersions/v1/instances" in path:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)
        raise AssertionError("unexpected read " + path)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    rc = provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir, "--environment", "dev",
                                "--probe-source-instances"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "environment inventory (3 instances): flexGateway/active=2, mule4/unregistered=1" in out
    assert "instance 111 label=legacy-basic technology=mule4 status=unregistered -> LISTED BY WIZARD [registration-like]" in out
    assert "asset product-api (v1) wizard list: HTTP 404, 0 listed; environment has 0" in out
    assert "VERDICT: registration-like instances listed by the wizard mechanism: 1; not listed: 0 -> registration-only source instances ARE selectable" in out
    assert (Path(artifacts_dir) / "probe-source-instances.json").exists()
    assert {m for m, p in record if not p.endswith("/oauth2/token")} == {"GET"}
    # the other-api asset (flex/active only) is not a candidate -> no wizard read for it
    assert not any("/assets/other-api/" in p for m, p in record)


def test_probe_reports_not_selectable_when_wizard_omits_registration_like(artifacts_dir, monkeypatch, capsys):
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    org = "8c27297e-4bcf-4c53-99eb-ffd7b1069294"
    listing = {"assets": [{"assetId": "legacy-api", "groupId": org, "apis": [
        {"id": 111, "instanceLabel": "legacy-basic", "technology": "mule4", "status": "unregistered", "productVersion": "v1"},
        {"id": 112, "instanceLabel": "legacy-flex", "technology": "flexGateway", "status": "active", "productVersion": "v1"}]}]}

    def urlopen(req, timeout=None):
        path = req.full_url.split("anypoint.mulesoft.com", 1)[1]
        if path.endswith("/oauth2/token"):
            return _FakeResponse({"access_token": "t"})
        assert req.get_method() == "GET"
        if path.endswith("/apis?limit=250&offset=0"):
            return _FakeResponse(listing)
        if "/apis?limit=250&offset=" in path:
            return _FakeResponse({"assets": []})
        if "/productVersions/v1/instances" in path:
            return _FakeResponse({"instances": [{"apiVersionId": 112}]})
        raise AssertionError("unexpected read " + path)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir, "--environment", "dev",
                                  "--probe-source-instances"]) == 0
    out = capsys.readouterr().out
    assert "instance 111 label=legacy-basic technology=mule4 status=unregistered -> NOT listed [registration-like]" in out
    assert "instance 112 label=legacy-flex technology=flexGateway status=active -> LISTED BY WIZARD [flex/active]" in out
    assert "registration-only source instances are NOT selectable" in out


# ── guarded delete for the immutable-Bridge replacement (never touches the source API instance) ──

BRIDGE_21166173 = {"id": 21166173, "instanceLabel": "uif-product-mcp", "environmentId": "a8c24dc0-02fc-4eb5-bf20-6c5b2627182f",
                   "assetId": "uif-domains-mcp", "assetVersion": "1.0.0", "metadata": {"generatedBy": "mcp_bridge"},
                   "endpoint": {"type": "mcp", "proxyUri": "https://0.0.0.0:443/productTest"}, "status": "active"}
SOURCE_21166244 = {"id": 21166244, "instanceLabel": "uif-product-api", "environmentId": "a8c24dc0-02fc-4eb5-bf20-6c5b2627182f",
                   "assetId": "product-api", "assetVersion": "1.0.0", "metadata": None,
                   "endpoint": {"type": "raml", "proxyUri": None}, "status": "unregistered"}


def _expected(iid="21166173"):
    return {"id": iid, "label": "uif-product-mcp", "base_path": "productTest", "mcp_asset_id": "uif-domains-mcp",
            "env_id": "a8c24dc0-02fc-4eb5-bf20-6c5b2627182f"}


def test_delete_guards_pass_only_for_the_exact_bridge_identity():
    from provision_bridge import delete_guards
    ok, lines = delete_guards(BRIDGE_21166173, _expected())
    assert ok, lines
    # the source API instance can never satisfy the Bridge guards
    ok, lines = delete_guards(SOURCE_21166244, _expected("21166244"))
    assert not ok
    assert "instanceLabel uif-product-mcp: FAIL" in lines and "endpoint.type mcp (a Bridge, never a source API instance): FAIL" in lines
    assert "bound MCP asset uif-domains-mcp: FAIL" in lines and "generatedBy mcp_bridge: FAIL" in lines
    # a Bridge on a different path, or a protected PoC id, fails too
    other = dict(BRIDGE_21166173, endpoint={"type": "mcp", "proxyUri": "https://0.0.0.0:443/product"})
    assert not delete_guards(other, _expected())[0]
    poc = dict(BRIDGE_21166173, id=21164696)
    assert not delete_guards(poc, _expected("21164696"))[0]


def _delete_fake(record, instance, deleted):
    import urllib.error
    def urlopen(req, timeout=None):
        method, path = req.get_method(), req.full_url.split("anypoint.mulesoft.com", 1)[1]
        record.append((method, path))
        if path.endswith("/oauth2/token"):
            return _FakeResponse({"access_token": "t"})
        if method == "DELETE":
            deleted.append(path)
            class R:
                status = 204
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()
        if method == "GET" and path.endswith("/upstreams"):
            return _FakeResponse({"upstreams": [{"id": "u", "connection": {"apiVersionId": "21166244"}}]})
        if method == "GET" and "/apis/" in path:
            if deleted:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)
            return _FakeResponse(instance)
        raise AssertionError("unexpected %s %s" % (method, path))
    return urlopen


def _delete_args(artifacts_dir, iid, *extra):
    return ["--config", str(CONFIG), "--artifacts", artifacts_dir, "--environment", "dev", "--base-path", "productTest",
            "--delete-instance", iid, "--confirm-delete", iid, "--platform-config", PLATFORM, *extra]


def test_delete_dry_run_sends_nothing(artifacts_dir, monkeypatch, capsys):
    import urllib.request
    import provision_bridge
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    assert provision_bridge.main(_delete_args(artifacts_dir, "21166173", "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "GUARDED BRIDGE DELETE (dry-run)" in out and "DRY-RUN: guards above would be evaluated" in out


def test_delete_preflight_reads_and_never_deletes(artifacts_dir, monkeypatch, capsys):
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    record, deleted = [], []
    monkeypatch.setattr(urllib.request, "urlopen", _delete_fake(record, BRIDGE_21166173, deleted))
    assert provision_bridge.main(_delete_args(artifacts_dir, "21166173", "--preflight")) == 0
    out = capsys.readouterr().out
    assert "guard: instanceLabel uif-product-mcp: PASS" in out and "guard: proxyUri path /productTest: PASS" in out
    assert "upstream source instances referenced: ['21166244'] (NOT deleted" in out
    assert "PREFLIGHT ONLY: all guards PASS; no delete was sent." in out
    assert deleted == [] and "DELETE" not in {m for m, p in record}


def test_delete_execute_deletes_exactly_once_and_verifies_gone(artifacts_dir, monkeypatch, capsys):
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    record, deleted = [], []
    monkeypatch.setattr(urllib.request, "urlopen", _delete_fake(record, BRIDGE_21166173, deleted))
    assert provision_bridge.main(_delete_args(artifacts_dir, "21166173", "--execute")) == 0
    out = capsys.readouterr().out
    assert deleted == ["/apimanager/api/v1/organizations/8c27297e-4bcf-4c53-99eb-ffd7b1069294/environments/"
                       "a8c24dc0-02fc-4eb5-bf20-6c5b2627182f/apis/21166173"]
    assert "VERIFIED: instance 21166173 is gone (HTTP 404)." in out
    assert not any(m == "POST" and not p.endswith("/oauth2/token") for m, p in record)  # no create in this step


def test_delete_refuses_source_instance_wrong_confirm_and_protected_ids(artifacts_dir, monkeypatch):
    import urllib.request
    import provision_bridge
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s")
    record, deleted = [], []
    # pointing the delete at the SOURCE API instance fails the identity guards
    monkeypatch.setattr(urllib.request, "urlopen", _delete_fake(record, SOURCE_21166244, deleted))
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(_delete_args(artifacts_dir, "21166244", "--execute"))
    assert "identity guard" in str(e.value) and deleted == []
    # confirmation id must match exactly
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts_dir, "--environment", "dev",
                               "--delete-instance", "21166173", "--confirm-delete", "21166174", "--execute"])
    assert "--confirm-delete must repeat" in str(e.value) and deleted == []
    # protected PoC ids are refused before any network call
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(_delete_args(artifacts_dir, "21164696", "--execute"))
    assert "protected PoC instance" in str(e.value) and deleted == []
