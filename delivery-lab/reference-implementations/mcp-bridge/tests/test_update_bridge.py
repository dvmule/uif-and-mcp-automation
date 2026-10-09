"""update_bridge.py (in-place Apply-Changes, reconciling ONE domain into the SHARED Bridge)
+ regression proof that the known-good CREATE / REUSE modules are untouched.

Every test is offline: Anypoint is faked (anypoint_fake.AnypointFake) and the fake
records each HTTP call so the tests can assert exactly which writes happened (if any).
Fixture Bridge: the shared DEV Bridge serving Product (route product-api) and a
synthetic Stock (route stock-api); Product is the domain being reconciled, Stock is
the domain that must come out untouched.

Regression: provision_bridge / ensure_source_instance / generate / validate / publish /
projection byte-match the snapshot manifest; update_bridge.py, bridge-config.yaml and
anypoint-environments.yaml were re-pinned on 2026-09-29 for the shared-Bridge model
(see tests/known-good/MANIFEST.sha256).
"""
import copy
import hashlib
import json
import os
import urllib.request
from pathlib import Path

import pytest
import yaml

import provision_bridge as pb
import update_bridge as ub
from anypoint_fake import (BRIDGE_ID, ENV, NEW_UUID, PLATFORM, PROD_URL, PROD_UUID, PRODUCT_CFG, PRODUCT_SPEC, SHARED_LABEL, SHARED_MCP, SHARED_PATH,
                           STOCK_TOOLS, STOCK_URL, STOCK_UUID, AnypointFake, domain_payload, effective, shared_state, stock_domain)
from contract_gate import expected_tools_from_contract
from generate_mcp_metadata import generate, write_artifacts

REPO = Path(__file__).resolve().parents[4]
DOMAIN = REPO / "domains" / "product"  # fixture domain (first MCP realization)
DOMAIN_CONFIG = DOMAIN / "src" / "mcp" / "bridge-config.yaml"
BASE_TOOLS = len(expected_tools_from_contract(str(PRODUCT_SPEC)))  # derived, never a literal
EXTRA_PATH, EXTRA_TOOL = "/probes/{id}", "get_probes_id"  # the synthetic 1.0.1 addition (a path no real contract uses)
SRC = Path(__file__).resolve().parents[1] / "scripts"  # the generic tooling under test
SNAPSHOT = Path(os.environ.get("TEMP", "")) / "uif-mcp-known-good-2026-09-11"          # CREATE/REUSE proven state
SNAPSHOT_LIFECYCLE = Path(os.environ.get("TEMP", "")) / "uif-mcp-proven-lifecycle-2026-09-11"  # + in-place UPDATE proven live


# ── fixtures: two domains, one shared Bridge ───────────────────────────────────────

@pytest.fixture(scope="module")
def product_cfg(tmp_path_factory):
    """Product's EFFECTIVE config (test-owned canonical domain inputs + shared identity), as deploy_bridge hands it over."""
    return effective(PRODUCT_CFG, tmp_path_factory.mktemp("eff-product"))


@pytest.fixture(scope="module")
def stock(tmp_path_factory):
    root = tmp_path_factory.mktemp("stock-domain")
    spec, cfg = stock_domain(root)
    eff = effective(cfg, tmp_path_factory.mktemp("eff-stock"))
    art = tmp_path_factory.mktemp("stock-art")
    write_artifacts(generate(str(spec), source_ref={"assetId": "stock-api", "version": "1.0.0", "groupId": pb.load_yaml(eff)["source_asset_id"] and "8c27297e-4bcf-4c53-99eb-ffd7b1069294"},
                             mcp_asset={"assetId": SHARED_MCP, "version": "1.0.0"}, org_id="8c27297e-4bcf-4c53-99eb-ffd7b1069294"), str(art))
    return {"eff": eff, "artifacts": str(art)}


def _gen(eff, out, version, spec_path=None):
    cfg = pb.load_yaml(eff)
    write_artifacts(generate(str(spec_path or (PRODUCT_SPEC)),
                             source_ref={"assetId": cfg["source_asset_id"], "version": version, "groupId": "8c27297e-4bcf-4c53-99eb-ffd7b1069294"},
                             mcp_asset={"assetId": cfg["mcp_asset_id"], "version": version}, org_id="8c27297e-4bcf-4c53-99eb-ffd7b1069294",
                             route_label=cfg.get("route_label"), transcoding_path=cfg.get("transcoding_path", "/mcp")), str(out))
    return str(out)


@pytest.fixture(scope="module")
def artifacts_100(tmp_path_factory, product_cfg):
    return _gen(product_cfg, tmp_path_factory.mktemp("art100"), "1.0.0")


@pytest.fixture(scope="module")
def artifacts_101(tmp_path_factory, product_cfg):
    """1.0.1 contract = the real one plus a third operation (new MCP tool)."""
    spec = yaml.safe_load((PRODUCT_SPEC).read_text(encoding="utf-8-sig"))
    spec["info"]["version"] = "1.0.1"
    spec["paths"][EXTRA_PATH] = {"get": {"operationId": "getProbeById", "summary": "Get one probe",
                                         "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                                         "responses": {"200": {"description": "ok"}}}}
    d = tmp_path_factory.mktemp("spec101")
    (d / "openapi.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return _gen(product_cfg, tmp_path_factory.mktemp("art101"), "1.0.1", d / "openapi.yaml")


@pytest.fixture
def source_file(tmp_path):
    p = tmp_path / "source-instance.json"
    p.write_text(json.dumps({"source_api_instance_id": PROD_UUID, "kind": "exchange-external", "label": "uif-product-dev",
                             "endpointUri": PROD_URL, "productAPIVersion": "v1",
                             "asset": {"groupId": "8c27297e-4bcf-4c53-99eb-ffd7b1069294", "assetId": "product-api", "version": "1.0.0"}}), encoding="utf-8")
    return str(p)


def _product(product_cfg, artifacts, mcp_version, uuid=PROD_UUID):
    return domain_payload(product_cfg, artifacts, mcp_version, uuid, "uif-product-dev", PROD_URL)


def _stock(stock, mcp_version):
    return domain_payload(stock["eff"], stock["artifacts"], mcp_version, STOCK_UUID, "uif-stock-dev", STOCK_URL)


def _state(product_cfg, artifacts, stock, mcp_version="1.0.1"):
    """The shared Bridge serving Product (route product-api, upstream up-1, policy 9213993)
    and Stock (route stock-api, upstream up-2, policy 9214003), bound to MCP asset mcp_version."""
    return shared_state([_product(product_cfg, artifacts, mcp_version), _stock(stock, mcp_version)], mcp_version)


def _snapshot(state, label):
    """Everything that belongs to one route: routing entry, upstream, outbound policy, router route."""
    r = next(x for x in state["inst"]["routing"] if x["label"] == label)
    uid = r["upstreams"][0]["id"]
    return copy.deepcopy({
        "routing": r,
        "upstream": next(u for u in state["ups"]["upstreams"] if u["id"] == uid),
        "policy": next(p for p in state["pols"]["policies"] if p.get("upstreamId") == uid),
        "route": next(x for x in state["pols"]["policies"][2]["configuration"]["routes"] if x["upstreamName"] == label),
    })


def _args(eff, artifacts, source_file, version, mode, *extra):
    return ["--config", eff, "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--mcp-asset-version", version,
            "--source-instance-file", source_file, "--instance-label", SHARED_LABEL, "--base-path", SHARED_PATH, mode, *extra]


def _live(monkeypatch):
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")


# ── regression: the known-good modules are byte-identical to the live-proven snapshot ──
# The manifest committed under tests/known-good/ makes this CI-effective; the original
# %TEMP% snapshots (on the machine that took them) remain a fallback only.

REPO_MANIFEST = Path(__file__).resolve().parent / "known-good" / "MANIFEST.sha256"


def _manifest_file(snapshot):
    return REPO_MANIFEST if REPO_MANIFEST.exists() else snapshot / "MANIFEST.sha256"


def _manifest(snapshot):
    manifest = {}
    for line in _manifest_file(snapshot).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)  # sha256sum: "<hash>  path" or binary-mode "<hash> *path"
        manifest[name.strip().lstrip("*").lstrip("./")] = digest
    return manifest


def _sha256(path):
    """Digest of the file CONTENT with line endings normalized to LF: the manifest pins
    the committed (LF) bytes; a Windows checkout with core.autocrlf=true must not fail
    the freeze on line endings alone."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# Frozen: the proven implementation modules. deploy_bridge.py is the ORCHESTRATOR and
# is deliberately allowed to evolve (its behaviour is covered by test_deploy_bridge.py
# + test_deploy_lifecycle.py); everything it calls must stay byte-identical.
FROZEN_CREATE_REUSE = ("provision_bridge.py", "ensure_source_instance.py", "generate_mcp_metadata.py",
                       "validate_mcp_metadata.py", "exchange_publish.py", "oas_projection.py", "bridge-config.yaml",
                       "anypoint-environments.yaml")


def _live_path(fname):
    """The snapshot was taken when everything sat in domains/product/src/mcp (manifest key
    src_mcp/<fname>). The generic tooling has since moved to the shared home; the domain
    input contract stayed with the domain. Same bytes, two locations."""
    return DOMAIN_CONFIG if fname == "bridge-config.yaml" else SRC / fname


def test_repository_manifest_makes_the_freeze_ci_effective():
    """The freeze must not depend on a developer machine: the committed manifest exists
    and pins every frozen file (so the two tests below never silently skip in CI)."""
    assert REPO_MANIFEST.exists(), "tests/known-good/MANIFEST.sha256 is missing - the freeze would only run locally"
    manifest = _manifest(REPO_MANIFEST.parent)
    for fname in FROZEN_CREATE_REUSE + ("update_bridge.py",):
        assert manifest.get("src_mcp/" + fname), "%s is not pinned by the committed manifest" % fname
    assert set(manifest) == {"src_mcp/" + f for f in FROZEN_CREATE_REUSE + ("update_bridge.py",)}  # frozen files only


@pytest.mark.skipif(not _manifest_file(SNAPSHOT).exists(), reason="no known-good manifest (repo or %TEMP%) present")
def test_known_good_create_reuse_modules_are_unchanged():
    manifest = _manifest(SNAPSHOT)
    for fname in FROZEN_CREATE_REUSE:
        assert manifest.get("src_mcp/" + fname) == _sha256(_live_path(fname)), "%s differs from the known-good snapshot" % fname


@pytest.mark.skipif(not _manifest_file(SNAPSHOT_LIFECYCLE).exists(), reason="no proven-lifecycle manifest (repo or %TEMP%) present")
def test_proven_update_module_is_unchanged():
    manifest = _manifest(SNAPSHOT_LIFECYCLE)
    for fname in FROZEN_CREATE_REUSE + ("update_bridge.py",):
        assert manifest.get("src_mcp/" + fname) == _sha256(_live_path(fname)), "%s differs from the proven-lifecycle snapshot" % fname


def test_update_module_only_imports_helpers_and_defines_no_monkeypatching():
    src = (SRC / "update_bridge.py").read_text(encoding="utf-8")
    assert "pb." in src and "provision_bridge as pb" in src
    assert "pb.build_payload =" not in src and "setattr(pb" not in src  # never rewires the proven builder


# ── pure decision pieces ─────────────────────────────────────────────────────────

def test_routes_view_pairs_every_route_with_its_upstream_and_outbound_policy(product_cfg, artifacts_100, stock):
    st = _state(product_cfg, artifacts_100, stock)
    view = ub.routes_view(st["inst"], st["ups"], st["pols"])
    assert set(view) == {"product-api", "stock-api"}
    assert view["product-api"]["upstream_id"] == "up-1" and view["product-api"]["transcoding_policy_id"] == 9213993
    assert view["stock-api"]["upstream_id"] == "up-2" and view["stock-api"]["transcoding_policy_id"] == 9214003
    assert sorted(view["stock-api"]["tool_names"]) == STOCK_TOOLS and len(view["product-api"]["tool_names"]) == BASE_TOOLS
    assert view["stock-api"]["connection"]["apiVersionId"] == STOCK_UUID and view["stock-api"]["uri"] == STOCK_URL


def test_noop_when_route_already_at_target(product_cfg, artifacts_100, stock):
    st = _state(product_cfg, artifacts_100, stock, "1.0.1")
    payload = _product(product_cfg, artifacts_100, "1.0.1")
    assert ub.decide(ub.current_state(st["inst"], st["ups"], st["pols"]), ub.target_state(payload)) == ("noop", [])


def test_update_decision_lists_what_differs(product_cfg, artifacts_100, artifacts_101, stock):
    st = _state(product_cfg, artifacts_100, stock, "1.0.1")
    payload = _product(product_cfg, artifacts_101, "1.0.2")
    action, diff = ub.decide(ub.current_state(st["inst"], st["ups"], st["pols"]), ub.target_state(payload))
    assert action == "update" and set(diff) == {"mcp_version", "connection_version", "tools", "routes"}
    tgt = ub.target_state(payload)
    assert len(tgt["tools"]) == BASE_TOOLS + 1 and any(n == EXTRA_TOOL for n, m, p in tgt["tools"])


def test_a_changed_source_instance_of_the_same_asset_is_a_reported_update_not_a_refusal(product_cfg, artifacts_100, stock):
    """The shared Bridge may have been wired by hand to another instance of the same REST
    asset; rebinding to the pipeline's external instance is an explicit, verified UPDATE."""
    st = _state(product_cfg, artifacts_100, stock, "1.0.1")
    payload = _product(product_cfg, artifacts_100, "1.0.1", uuid=NEW_UUID)
    exp = {"label": SHARED_LABEL, "base_path": SHARED_PATH, "mcp_asset_id": SHARED_MCP, "env_id": ENV,
           "source_asset_id": "product-api", "route_label": "product-api"}
    assert ub.identity_guards(st["inst"], st["ups"], st["pols"], exp)[0]
    assert ub.decide(ub.current_state(st["inst"], st["ups"], st["pols"]), ub.target_state(payload)) == ("update", ["source_instance"])


def test_add_decision_when_the_domain_route_is_absent(product_cfg, artifacts_100, stock):
    st = shared_state([_product(product_cfg, artifacts_100, "1.0.0")], "1.0.0")  # Product only
    action, diff = ub.decide(ub.current_state(st["inst"], st["ups"], st["pols"]), ub.target_state(_stock(stock, "1.0.1")))
    assert action == "add" and diff == ["route stock-api absent", "mcp_version"]


def test_identity_guards_fail_closed_on_wrong_bridge_or_state(product_cfg, artifacts_100, stock):
    st = _state(product_cfg, artifacts_100, stock)
    inst, ups, pols = st["inst"], st["ups"], st["pols"]
    exp = {"label": SHARED_LABEL, "base_path": SHARED_PATH, "mcp_asset_id": SHARED_MCP, "env_id": ENV,
           "source_asset_id": "product-api", "route_label": "product-api"}
    assert ub.identity_guards(inst, ups, pols, exp)[0]
    # a per-domain Bridge (other label / path) can never pass the shared guards
    other = dict(inst, instanceLabel="uif-product-mcp", endpoint=dict(inst["endpoint"], proxyUri="https://0.0.0.0:443/productTest"))
    ok, lines = ub.identity_guards(other, ups, pols, exp)
    assert not ok and "instanceLabel uif-domains-mcp: FAIL" in lines and "proxyUri path /domainsTest: FAIL" in lines
    # my route label bound to ANOTHER domain's REST asset -> refuse (never rewire someone else's route)
    foreign = copy.deepcopy(ups)
    foreign["upstreams"][0]["connection"]["assetId"] = "foreign-api"
    ok, lines = ub.identity_guards(inst, foreign, pols, exp)
    assert not ok and "route product-api -> REST asset product-api (never another domain's asset): FAIL" in lines
    # my route absent but another route already serves my REST asset -> refuse
    exp_new = dict(exp, route_label="product-v2")
    ok, lines = ub.identity_guards(inst, ups, pols, exp_new)
    assert not ok and any(l.startswith("route product-v2 absent") and l.endswith("FAIL") for l in lines)
    # a connection-less upstream, a missing outbound policy, or duplicate route labels also refuse
    connless = copy.deepcopy(ups)
    del connless["upstreams"][1]["connection"]
    assert not ub.identity_guards(inst, connless, pols, exp)[0]
    assert not ub.identity_guards(inst, ups, {"policies": pols["policies"][:4]}, exp)[0]
    dup = dict(inst, routing=inst["routing"] + [dict(inst["routing"][0])])
    assert not ub.identity_guards(dup, ups, pols, exp)[0]


def test_patch_body_preserves_every_other_route_and_merges_the_router(product_cfg, artifacts_100, artifacts_101, stock):
    st = _state(product_cfg, artifacts_100, stock, "1.0.1")
    stock_before = _snapshot(st, "stock-api")
    payload = _product(product_cfg, artifacts_101, "1.0.2")
    cur, tgt = ub.current_state(st["inst"], st["ups"], st["pols"]), ub.target_state(payload)
    body = ub.build_patch_body(payload, st["inst"], cur, tgt)
    assert body["assetVersion"] == "1.0.2" and body["spec"] == {"assetId": SHARED_MCP, "groupId": payload["spec"]["groupId"], "version": "1.0.2"}
    assert body["metadata"] == {"generatedBy": "mcp_bridge"} and body["instanceLabel"] == SHARED_LABEL
    assert "uri" not in body["endpoint"] and body["endpoint"]["proxyUri"] == "https://0.0.0.0:443/domainsTest"
    assert body["deployment"]["targetId"] == st["inst"]["deployment"]["targetId"] and body["deployment"]["gatewayVersion"] == "1.13.1"
    # upstreams: Product's rebound at 1.0.1, Stock's byte-identical, both keep their ids
    assert [u["id"] for u in body["upstreams"]] == ["up-1", "up-2"]
    assert body["upstreams"][0]["connection"]["version"] == "1.0.1" and body["upstreams"][0]["connection"]["apiVersionId"] == PROD_UUID
    assert body["upstreams"][1] == {"id": "up-2", "uri": stock_before["upstream"]["uri"], "connection": stock_before["upstream"]["connection"]}
    # routing: both routes by id, rules preserved
    assert body["routing"] == [
        {"label": "product-api", "upstreams": [{"id": "up-1", "weight": 100}], "rules": {"headers": {"X-UPSTREAM-NAME": "product-api"}}},
        {"label": "stock-api", "upstreams": [{"id": "up-2", "weight": 100}], "rules": {"headers": {"X-UPSTREAM-NAME": "stock-api"}}}]
    # inline router: Stock's route verbatim, Product's with the new tool
    routes = body["policies"][2]["configurationData"]["routes"]
    assert [r["upstreamName"] for r in routes] == ["product-api", "stock-api"]
    assert routes[1] == stock_before["route"] and len(routes[0]["tools"]) == BASE_TOOLS + 1 and EXTRA_TOOL in routes[0]["tools"]
    assert [p["assetId"] for p in body["policies"]] == ["mcp-support", "mcp-schema-validation", "mcp-transcoding-router"]
    router_body, transcoding_body = ub.policy_patch_bodies(cur, tgt)
    assert router_body["configurationData"]["routes"] == routes and router_body["configurationData"]["transcodingPath"] == "/mcp"
    assert len(transcoding_body["configurationData"]["tools"]) == BASE_TOOLS + 1
    assert _snapshot(st, "stock-api") == stock_before  # building bodies never mutates the live state


def test_patch_body_for_a_new_route_references_the_upstream_created_first(product_cfg, artifacts_100, stock):
    """ADD (live-verified 2026-09-30): the xapi PATCH carries the NEW upstream by id in
    upstreams[] (uri + connection) and its route as a {id, weight} REFERENCE - never an
    inline upstream object (the platform answers 400 RoutingValidationError)."""
    st = shared_state([_product(product_cfg, artifacts_100, "1.0.0")], "1.0.0")
    payload = _stock(stock, "1.0.1")
    cur, tgt = ub.current_state(st["inst"], st["ups"], st["pols"]), ub.target_state(payload)
    body = ub.build_patch_body(payload, st["inst"], cur, tgt, new_upstream_id="up-new-1")
    assert [u["id"] for u in body["upstreams"]] == ["up-1", "up-new-1"]
    assert body["upstreams"][1] == {"id": "up-new-1", "uri": STOCK_URL, "connection": tgt["connection"], "label": "uif-stock-dev"}
    assert body["routing"][0]["upstreams"] == [{"id": "up-1", "weight": 100}]
    assert body["routing"][1] == {"label": "stock-api", "upstreams": [{"id": "up-new-1", "weight": 100}], "rules": {"headers": {"X-UPSTREAM-NAME": "stock-api"}}}
    for r in body["routing"]:
        for u in r["upstreams"]:
            assert set(u) == {"id", "weight"}  # references only
    assert AnypointFake.routing_validation_errors(body) == []
    routes = body["policies"][2]["configurationData"]["routes"]
    assert [r["upstreamName"] for r in routes] == ["product-api", "stock-api"] and sorted(routes[1]["tools"]) == STOCK_TOOLS
    # without the A1 upstream id the body cannot be built at all
    with pytest.raises(SystemExit) as e:
        ub.build_patch_body(payload, st["inst"], cur, tgt)
    assert "upstream REFERENCES only" in str(e.value)
    # the A1 / A3 bodies
    assert ub.upstream_create_body(tgt) == {"label": "uif-stock-dev", "uri": STOCK_URL}
    a3 = ub.outbound_policy_body(tgt)
    assert a3["assetId"] == "mcp-transcoding" and a3["assetVersion"] == "1.0.1" and a3["groupId"] == pb.MULESOFT_POLICY_ORG
    assert sorted(t["name"] for t in a3["configurationData"]["tools"]) == STOCK_TOOLS and "upstreamIds" not in a3  # bound at send time


def test_patch_shape_guard_rejects_the_2026_09_30_inline_upstream_shape():
    """Regression: the exact invalid shape the first live ADD sent (routing[1].upstreams[0]
    carrying uri/connection/policies and no id) can never be sent again."""
    body = {"upstreams": [{"id": "605851c7-403c-4ad7-9426-ff547bbbb36e", "uri": PROD_URL, "connection": {"assetId": "product-api"}}],
            "routing": [{"label": "product-api", "upstreams": [{"id": "605851c7-403c-4ad7-9426-ff547bbbb36e", "weight": 100}], "rules": {}},
                        {"label": "price-api", "upstreams": [{"weight": 100, "uri": PROD_URL, "connection": {"assetId": "price-api"},
                                                                    "policies": [{"assetId": "mcp-transcoding"}]}], "rules": {}}]}
    with pytest.raises(SystemExit) as e:
        ub.assert_patch_routing_is_references_only(body)
    assert "routing[1].upstreams[0]" in str(e.value) and "no id" in str(e.value) and "'connection', 'policies', 'uri'" in str(e.value)
    # the fake mirrors the platform's answer for the same body
    errors = AnypointFake.routing_validation_errors(body)
    assert [e_["instancePath"] for e_ in errors] == ["/1/upstreams/0"] * 4
    assert {e_["params"].get("missingProperty") or e_["params"].get("unevaluatedProperty") for e_ in errors} == {"id", "uri", "connection", "policies"}
    # a top-level upstream without id is refused too
    with pytest.raises(SystemExit):
        ub.assert_patch_routing_is_references_only({"upstreams": [{"uri": PROD_URL}], "routing": []})


def test_verify_update_detects_drift_of_another_route_or_a_removed_route(product_cfg, artifacts_100, artifacts_101, stock):
    st = _state(product_cfg, artifacts_100, stock, "1.0.1")
    cur = ub.current_state(st["inst"], st["ups"], st["pols"])
    payload = _product(product_cfg, artifacts_101, "1.0.2")
    tgt = ub.target_state(payload)
    before = {"id": BRIDGE_ID, "label": SHARED_LABEL, "proxyUri": st["inst"]["endpoint"]["proxyUri"], "router_policy_id": 9213992,
              "routes": copy.deepcopy(cur["routes"]), "router_routes": copy.deepcopy(cur["router_routes"]), "adding": False}
    fake = AnypointFake(bridge=copy.deepcopy(st))
    fake._apply_instance_patch(fake.bridge, ub.build_patch_body(payload, st["inst"], cur, tgt))
    router_body, transcoding_body = ub.policy_patch_bodies(cur, tgt)  # the two v1 policy PATCHes that follow the xapi PATCH
    for pid, body in ((9213992, router_body), (9213993, transcoding_body)):
        next(p for p in fake.bridge["pols"]["policies"] if p["policyId"] == pid)["configuration"] = body["configurationData"]
    ok, lines = ub.verify_update(fake.bridge["inst"], fake.bridge["ups"], fake.bridge["pols"], before, tgt)
    assert ok, [l for l in lines if l.endswith("FAIL")]
    # Stock's tools drift -> FAIL
    drifted = copy.deepcopy(fake.bridge)
    drifted["pols"]["policies"][4]["configuration"]["tools"].pop()
    ok, lines = ub.verify_update(drifted["inst"], drifted["ups"], drifted["pols"], before, tgt)
    assert not ok and "route stock-api PRESERVED (upstream id, uri, connection, tools, outbound policy id): FAIL" in lines
    # Stock's route removed -> FAIL
    removed = copy.deepcopy(fake.bridge)
    removed["inst"]["routing"] = removed["inst"]["routing"][:1]
    ok, lines = ub.verify_update(removed["inst"], removed["ups"], removed["pols"], before, tgt)
    assert not ok and "no route removed (2 before -> 2 after): FAIL" in lines


def test_fingerprint_changes_when_any_route_changes(product_cfg, artifacts_100, stock):
    st = _state(product_cfg, artifacts_100, stock)
    fp = ub.fingerprint(st["inst"], st["ups"], st["pols"])
    assert fp == ub.fingerprint(copy.deepcopy(st["inst"]), copy.deepcopy(st["ups"]), copy.deepcopy(st["pols"]))
    other = copy.deepcopy(st)
    other["ups"]["upstreams"][1]["connection"]["version"] = "1.0.7"  # Stock moved
    assert ub.fingerprint(other["inst"], other["ups"], other["pols"]) != fp


# ── CLI flows ─────────────────────────────────────────────────────────────────────

def test_dry_run_sends_nothing(product_cfg, artifacts_101, source_file, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    assert ub.main(_args(product_cfg, artifacts_101, source_file, "1.0.2", "--dry-run")) == 0
    out = capsys.readouterr().out
    assert '"assetVersion": "1.0.2"' in out and PROD_UUID in out and "preserved verbatim" in out and "DRY-RUN: nothing sent" in out


def test_preflight_reads_only_and_reports_update(product_cfg, artifacts_100, artifacts_101, stock, source_file, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(bridge=_state(product_cfg, artifacts_100, stock, "1.0.1"))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert ub.main(_args(product_cfg, artifacts_101, source_file, "1.0.2", "--preflight")) == 0
    out = capsys.readouterr().out
    assert "decision: UPDATE (differs in: mcp_version, connection_version, tools, routes)" in out
    assert "PATCH" in out and "(2 upstreams, 2 routes)" in out and "PREFLIGHT ONLY: nothing was modified." in out
    assert fake.methods() == {"GET"} and fake.writes == []
    assert fake.bridge["inst"]["assetVersion"] == "1.0.1"


def test_execute_on_identical_state_writes_nothing(product_cfg, artifacts_100, stock, source_file, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(bridge=_state(product_cfg, artifacts_100, stock, "1.0.1"))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert ub.main(_args(product_cfg, artifacts_100, source_file, "1.0.1", "--execute")) == 0
    assert "NO-OP" in capsys.readouterr().out and fake.methods() == {"GET"}


def test_execute_updates_product_in_place_and_leaves_stock_untouched(product_cfg, artifacts_100, artifacts_101, stock, source_file, monkeypatch, capsys):
    _live(monkeypatch)
    st = _state(product_cfg, artifacts_100, stock, "1.0.1")
    stock_before = _snapshot(st, "stock-api")
    fake = AnypointFake(bridge=st)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert ub.main(_args(product_cfg, artifacts_101, source_file, "1.0.2", "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["patch-instance", "patch-policy", "patch-policy"]
    assert [w[1].get("policyId") for w in fake.writes[1:]] == [9213992, 9213993]  # router + PRODUCT's transcoding; never Stock's 9214003
    xapi = [p for m, p in fake.record if m == "PATCH"][0]
    assert "?checkAutomatedPolicies=true" in xapi and "/xapi/" in xapi
    assert st["inst"]["assetVersion"] == "1.0.2" and st["inst"]["id"] == BRIDGE_ID
    assert st["ups"]["upstreams"][0]["connection"] == {**st["ups"]["upstreams"][0]["connection"], "version": "1.0.1", "apiVersionId": PROD_UUID}
    assert len(st["pols"]["policies"][3]["configuration"]["tools"]) == BASE_TOOLS + 1
    assert _snapshot(st, "stock-api") == stock_before  # Stock: routing, upstream, policy and router route byte-identical
    for line in ("same instance id %s: PASS" % BRIDGE_ID, "route product-api connection.apiVersionId %s: PASS" % PROD_UUID,
                 "router policy id unchanged (no policy churn): PASS", "route product-api tools persisted as sent: PASS",
                 "route stock-api PRESERVED (upstream id, uri, connection, tools, outbound policy id): PASS",
                 "route stock-api router route PRESERVED: PASS", "UPDATED in place", "1 other route(s) preserved (stock-api)"):
        assert line in out
    assert "s3cr3t-VALUE-never-printed" not in out


def test_execute_adds_stock_to_a_product_only_bridge_and_leaves_product_untouched(product_cfg, artifacts_100, stock, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    st = shared_state([_product(product_cfg, artifacts_100, "1.0.0")], "1.0.0")
    product_before = _snapshot(st, "product-api")
    fake = AnypointFake(bridge=st)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    sf = tmp_path / "stock-source.json"
    sf.write_text(json.dumps({"source_api_instance_id": STOCK_UUID, "label": "uif-stock-dev", "productAPIVersion": "v1"}), encoding="utf-8")
    assert ub.main(_args(stock["eff"], stock["artifacts"], str(sf), "1.0.1", "--execute")) == 0
    out = capsys.readouterr().out
    # A1 upstream create -> A2 xapi PATCH (references) -> A3 outbound policy bound to the new upstream -> A4 router
    assert fake.kinds() == ["post-upstream", "patch-instance", "post-outbound-policy", "patch-policy"]
    assert fake.writes[0][1] == {"label": "uif-stock-dev", "uri": STOCK_URL}
    body = fake.writes[1][1]
    assert [u["id"] for u in body["upstreams"]] == ["up-1", "up-new-1"] and body["upstreams"][1]["connection"]["apiVersionId"] == STOCK_UUID
    assert body["routing"][1] == {"label": "stock-api", "upstreams": [{"id": "up-new-1", "weight": 100}], "rules": {"headers": {"X-UPSTREAM-NAME": "stock-api"}}}
    assert fake.writes[2][1]["upstreamIds"] == ["up-new-1"] and sorted(t["name"] for t in fake.writes[2][1]["configurationData"]["tools"]) == STOCK_TOOLS
    assert fake.writes[3][1]["policyId"] == 9213992
    assert [r["label"] for r in st["inst"]["routing"]] == ["product-api", "stock-api"]
    assert _snapshot(st, "product-api") == product_before
    new = _snapshot(st, "stock-api")
    assert new["upstream"]["id"] == "up-new-1" and new["upstream"]["connection"]["apiVersionId"] == STOCK_UUID
    assert new["policy"]["upstreamId"] == "up-new-1" and sorted(t["name"] for t in new["policy"]["configuration"]["tools"]) == STOCK_TOOLS
    assert "decision: ADD (differs in: route stock-api absent, mcp_version)" in out and "ADDED in place" in out
    assert "A1 CREATED upstream up-new-1" in out and "route stock-api served by the upstream created/reused in A1 (up-new-1): PASS" in out
    # the PATCH order in the fake proves the platform schema was satisfied: a rejected PATCH is never recorded
    assert all(AnypointFake.routing_validation_errors(w[1]) == [] for w in fake.writes if w[0] == "patch-instance")


def test_add_reuses_an_unrouted_upstream_left_by_an_interrupted_add(product_cfg, artifacts_100, stock, tmp_path, monkeypatch, capsys):
    """A1 succeeded earlier but A2 failed: the next run must route the existing upstream,
    never create a duplicate."""
    _live(monkeypatch)
    st = shared_state([_product(product_cfg, artifacts_100, "1.0.0")], "1.0.0")
    st["ups"]["upstreams"].append({"id": "up-orphan", "uri": STOCK_URL, "label": "uif-stock-dev"})  # created by A1, never routed
    fake = AnypointFake(bridge=st)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    sf = tmp_path / "stock-source.json"
    sf.write_text(json.dumps({"source_api_instance_id": STOCK_UUID, "label": "uif-stock-dev", "productAPIVersion": "v1"}), encoding="utf-8")
    assert ub.main(_args(stock["eff"], stock["artifacts"], str(sf), "1.0.1", "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["patch-instance", "post-outbound-policy", "patch-policy"]  # no second upstream
    assert "A1 REUSE: unrouted upstream up-orphan" in out
    assert _snapshot(st, "stock-api")["upstream"]["id"] == "up-orphan" and fake.writes[1][1]["upstreamIds"] == ["up-orphan"]
    # an unrouted upstream that is NOT this domain's is a foreign leftover -> identity guard refuses
    st2 = shared_state([_product(product_cfg, artifacts_100, "1.0.0")], "1.0.0")
    st2["ups"]["upstreams"].append({"id": "up-foreign", "uri": "https://x/", "label": "somebody-else", "connection": {"assetId": "other-api"}})
    fake = AnypointFake(bridge=st2)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        ub.main(_args(stock["eff"], stock["artifacts"], str(sf), "1.0.1", "--execute"))
    assert "identity guard" in str(e.value) and fake.writes == []


def test_add_stops_at_the_first_rejected_operation_and_leaves_evidence(product_cfg, artifacts_100, stock, tmp_path, monkeypatch):
    _live(monkeypatch)
    sf = tmp_path / "stock-source.json"
    sf.write_text(json.dumps({"source_api_instance_id": STOCK_UUID, "label": "uif-stock-dev", "productAPIVersion": "v1"}), encoding="utf-8")
    for reject, expected_writes, step in (("upstream", [], "A1 upstream create"), ("instance", ["post-upstream"], "A2 xapi instance"),
                                          ("outbound-policy", ["post-upstream", "patch-instance"], "A3 outbound")):
        st = shared_state([_product(product_cfg, artifacts_100, "1.0.0")], "1.0.0")
        fake = AnypointFake(bridge=st, reject=reject)
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        with pytest.raises(SystemExit) as e:
            ub.main(_args(stock["eff"], stock["artifacts"], str(sf), "1.0.1", "--execute"))
        assert "UPDATE REJECTED at %s" % step in str(e.value) and fake.kinds() == expected_writes, reject
        assert not any(m == "DELETE" for m, p in fake.record)
    assert Path(stock["artifacts"], "update-rejected.json").exists()


def test_execute_refuses_wrong_bridge_before_any_write(product_cfg, artifacts_100, artifacts_101, stock, source_file, monkeypatch):
    _live(monkeypatch)
    st = _state(product_cfg, artifacts_100, stock, "1.0.1")
    st["inst"]["instanceLabel"] = "uif-product-mcp"  # a per-domain Bridge answered instead
    fake = AnypointFake(bridge=st)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        ub.main(_args(product_cfg, artifacts_101, source_file, "1.0.2", "--execute", "--instance-id", str(BRIDGE_ID)))
    assert "identity guard" in str(e.value) and fake.writes == []


def test_execute_reports_rejection_and_stops(product_cfg, artifacts_100, artifacts_101, stock, source_file, monkeypatch):
    _live(monkeypatch)
    fake = AnypointFake(bridge=_state(product_cfg, artifacts_100, stock, "1.0.1"), reject="instance")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        ub.main(_args(product_cfg, artifacts_101, source_file, "1.0.2", "--execute"))
    assert "UPDATE REJECTED at xapi instance - HTTP 400" in str(e.value) and "rejected" in str(e.value)
    assert sum(1 for m, p in fake.record if m == "PATCH") == 1 and fake.writes == []  # stopped at the first rejected write
    assert Path(artifacts_101, "update-rejected.json").exists()


def test_concurrent_modification_between_decision_and_write_is_refused(product_cfg, artifacts_100, artifacts_101, stock, source_file, monkeypatch):
    """Stock's pipeline lands between Product's decision read and Product's PATCH: the
    re-read fingerprint differs -> nothing written, the run is repeated."""
    _live(monkeypatch)

    def stock_moves(state):
        state["ups"]["upstreams"][1]["connection"]["version"] = "1.0.1"
        state["inst"]["assetVersion"] = "1.0.2"

    fake = AnypointFake(bridge=_state(product_cfg, artifacts_100, stock, "1.0.1"), mutate_on_instance_read=(2, stock_moves))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        ub.main(_args(product_cfg, artifacts_101, source_file, "1.0.2", "--execute"))
    assert "REFUSED (concurrent modification)" in str(e.value) and "re-run" in str(e.value)
    assert fake.writes == [] and not any(m == "PATCH" for m, p in fake.record)
