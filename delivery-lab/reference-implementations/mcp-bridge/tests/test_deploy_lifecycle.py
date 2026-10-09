"""deploy_bridge.py as the lifecycle orchestrator for the SHARED Bridge: ensure/reuse
this domain's upstream -> inspect the shared Bridge -> merge + version the shared MCP
asset -> CREATE | ADD | UPDATE in place | NO-OP. Never delete/recreate; never touch
another domain's route.

Exchange + API Manager are faked together (anypoint_fake.AnypointFake); every test
asserts the exact writes. Domains: Product (the real fixture domain) and a synthetic
Stock (second domain) / Customer (third domain).

Scenario map (the acceptance list of the shared-Bridge change):
  1 shared Bridge absent -> the first domain creates it
  2 Bridge has Product -> Stock deployment ADDS Stock, Product preserved
  3 Bridge has Product + Stock -> Product's contract/version changes: Product UPDATED, Stock unchanged
  4 no Product change -> NO-OP (no write, not even an Exchange publish)
  5 Product's mappings drifted (same versions) -> Product's tools/routes fixed, Stock untouched, asset reused
  6 a third domain is added -> both existing routes intact
  7 a domain absent from the run's input is never removed
  9 conformance (initialize + tools/list) is checked against the MERGED tool set
  (8, concurrency: test_mcp_workflow_contract.py + test_update_bridge.py)
"""
import copy
import json
import urllib.request
from pathlib import Path

import pytest
import yaml

import deploy_bridge
import mcp_conformance
import shared_bridge as sb
from anypoint_fake import (PRODUCT_SPEC, BRIDGE_ID, ENV, EXTERNAL_PROD, EXTERNAL_STOCK, NEW_ID, NEW_UUID, ORG, PLATFORM, PROD_URL, PROD_UUID,
                           PRODUCT_CFG, SHARED_LABEL, SHARED_MCP, SHARED_PATH, STOCK_TOOLS, STOCK_URL, STOCK_UUID, AnypointFake,
                           domain_payload, effective, merged_asset, shared_state, stock_domain, synthetic_domain)
from contract_gate import expected_tools_from_contract
from generate_mcp_metadata import generate, write_artifacts

REPO = Path(__file__).resolve().parents[4]
DOMAIN = REPO / "domains" / "product"
CONFIG = DOMAIN / "src" / "mcp" / "bridge-config.yaml"
SPEC = PRODUCT_SPEC  # test-owned single-file fixture contract
BASE_TOOLS = len(expected_tools_from_contract(str(SPEC)))
PRODUCT_TOOLS = sorted(e["name"] for e in expected_tools_from_contract(str(SPEC)))
CUSTOMER_URL = "https://upstream.example.test/customer/"
CUSTOMER_UUID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


def _gen(spec, asset, out, version):
    write_artifacts(generate(str(spec), source_ref={"assetId": asset, "version": version, "groupId": ORG},
                             mcp_asset={"assetId": SHARED_MCP, "version": version}, org_id=ORG), str(out))
    return str(out)


@pytest.fixture(scope="module")
def product(tmp_path_factory):
    eff = effective(PRODUCT_CFG, tmp_path_factory.mktemp("eff-product"))
    spec101 = yaml.safe_load(SPEC.read_text(encoding="utf-8-sig"))
    spec101["info"]["version"] = "1.0.1"
    spec101["paths"]["/probes/{id}"] = {"get": {"operationId": "getProbeById", "summary": "one",
                                                 "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                                                 "responses": {"200": {"description": "ok"}}}}
    d = tmp_path_factory.mktemp("spec101")
    (d / "openapi.yaml").write_text(yaml.safe_dump(spec101, sort_keys=False), encoding="utf-8")
    return {"cfg": PRODUCT_CFG, "eff": eff,
            "art100": _gen(SPEC, "product-api", tmp_path_factory.mktemp("p100"), "1.0.0"),
            "art101": _gen(d / "openapi.yaml", "product-api", tmp_path_factory.mktemp("p101"), "1.0.1")}


@pytest.fixture(scope="module")
def stock(tmp_path_factory):
    spec, cfg = stock_domain(tmp_path_factory.mktemp("stock"))
    return {"cfg": str(cfg), "eff": effective(cfg, tmp_path_factory.mktemp("eff-stock")),
            "art100": _gen(spec, "stock-api", tmp_path_factory.mktemp("s100"), "1.0.0")}


@pytest.fixture(scope="module")
def customer(tmp_path_factory):
    spec, cfg = synthetic_domain(tmp_path_factory.mktemp("customer"), "customer", "customers", CUSTOMER_URL)
    return {"cfg": str(cfg), "eff": effective(cfg, tmp_path_factory.mktemp("eff-customer")),
            "art100": _gen(spec, "customer-api", tmp_path_factory.mktemp("c100"), "1.0.0")}


def _payload(dom, artifacts, mcp_version, uuid, name, url):
    return domain_payload(dom["eff"], artifacts, mcp_version, uuid, name, url)


def _product_payload(product, artifacts, mcp_version):
    return _payload(product, artifacts, mcp_version, PROD_UUID, "uif-product-dev", PROD_URL)


def _stock_payload(stock, mcp_version):
    return _payload(stock, stock["art100"], mcp_version, STOCK_UUID, "uif-stock-dev", STOCK_URL)


def _two_domain_world(product, stock):
    """Bridge serving Product@1.0.0 + Stock@1.0.0 bound to the shared asset 1.0.1 whose content IS that merge."""
    state = shared_state([_product_payload(product, product["art100"], "1.0.1"), _stock_payload(stock, "1.0.1")], "1.0.1")
    assets = {"1.0.0": merged_asset(Path(product["art100"]) / "mcp-metadata.json"),
              "1.0.1": merged_asset(Path(product["art100"]) / "mcp-metadata.json", Path(stock["art100"]) / "mcp-metadata.json")}
    return state, assets


def _snapshot(state, label):
    r = next(x for x in state["inst"]["routing"] if x["label"] == label)
    uid = r["upstreams"][0]["id"]
    return copy.deepcopy({"routing": r, "upstream": next(u for u in state["ups"]["upstreams"] if u["id"] == uid),
                          "policy": next(p for p in state["pols"]["policies"] if p.get("upstreamId") == uid),
                          "route": next(x for x in state["pols"]["policies"][2]["configuration"]["routes"] if x["upstreamName"] == label)})


def _args(cfg, artifacts, out, rest_version, mode, *extra):
    return ["--config", cfg, "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--rest-asset-version", rest_version, "--out", str(out), mode, *extra]


def _live(monkeypatch):
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")


def _shared_meta(out):
    return json.loads((Path(out) / deploy_bridge.SHARED_METADATA_FILE).read_text(encoding="utf-8"))


# 1. shared Bridge absent -> the first domain creates it (after publishing the shared asset 1.0.0)
def test_first_domain_creates_the_shared_bridge(product, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(externals=[EXTERNAL_PROD], bridge=None)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(product["cfg"], product["art100"], tmp_path, "1.0.0", "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["mcp-asset", "create"]
    asset, create = fake.writes[0][1], fake.writes[1][1]
    assert asset["version"] == "1.0.0" and sb.tool_names(asset["metadata"]) == PRODUCT_TOOLS and sb.connection_versions(asset["metadata"]) == {"product-api": "1.0.0"}
    assert create["instanceLabel"] == SHARED_LABEL and create["endpoint"]["proxyUri"].endswith("/" + SHARED_PATH)
    assert create["spec"] == {"assetId": SHARED_MCP, "groupId": ORG, "version": "1.0.0"}
    assert create["routing"][0]["label"] == "product-api" and create["routing"][0]["upstreams"][0]["connection"]["apiVersionId"] == PROD_UUID
    assert "bridge lookup: none labeled 'uif-domains-mcp' -> CREATE path" in out
    assert "shared MCP asset: uif-domains-mcp@1.0.0 (decision initial" in out
    assert "CREATED instance %s (label uif-domains-mcp)" % NEW_ID in out and "read-back verification passed." in out
    assert sb.tool_names(_shared_meta(tmp_path)) == PRODUCT_TOOLS
    assert (tmp_path / "effective-bridge-config.yaml").exists() and CONFIG.read_text(encoding="utf-8").count("mcp_asset_id:") == 0


def test_first_domain_with_absent_upstream_creates_upstream_asset_and_bridge(product, tmp_path, monkeypatch):
    _live(monkeypatch)
    fake = AnypointFake(externals=[], bridge=None)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(product["cfg"], product["art100"], tmp_path, "1.0.0", "--execute")) == 0
    assert fake.kinds() == ["external", "mcp-asset", "create"]
    assert fake.writes[2][1]["routing"][0]["upstreams"][0]["connection"]["apiVersionId"] == NEW_UUID


# 2. Bridge has Product -> Stock is ADDED, Product preserved
def test_second_domain_is_added_and_the_first_is_preserved(product, stock, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    state = shared_state([_product_payload(product, product["art100"], "1.0.0")], "1.0.0")
    product_before = _snapshot(state, "product-api")
    fake = AnypointFake(externals=[EXTERNAL_PROD], bridge=state, mcp_assets={"1.0.0": merged_asset(Path(product["art100"]) / "mcp-metadata.json")})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(stock["cfg"], stock["art100"], tmp_path, "1.0.0", "--execute")) == 0
    out = capsys.readouterr().out
    # external instance -> shared asset -> A1 upstream -> A2 PATCH (references) -> A3 outbound policy -> A4 router
    assert fake.kinds() == ["external", "mcp-asset", "post-upstream", "patch-instance", "post-outbound-policy", "patch-policy"]
    assert fake.writes[0][1]["name"] == "uif-stock-dev"
    asset = fake.writes[1][1]
    assert asset["version"] == "1.0.1" and sb.tool_names(asset["metadata"]) == sorted(PRODUCT_TOOLS + STOCK_TOOLS)
    assert sb.connection_versions(asset["metadata"]) == {"product-api": "1.0.0", "stock-api": "1.0.0"}
    assert fake.writes[2][1] == {"label": "uif-stock-dev", "uri": STOCK_URL}
    body = fake.writes[3][1]
    assert body["upstreams"][0] == {"id": "up-1", "uri": PROD_URL, "connection": product_before["upstream"]["connection"]}
    assert body["upstreams"][1]["id"] == "up-new-1" and body["upstreams"][1]["connection"]["apiVersionId"] == NEW_UUID
    assert body["routing"][0] == {"label": "product-api", "upstreams": [{"id": "up-1", "weight": 100}], "rules": product_before["routing"]["rules"]}
    assert body["routing"][1] == {"label": "stock-api", "upstreams": [{"id": "up-new-1", "weight": 100}], "rules": {"headers": {"X-UPSTREAM-NAME": "stock-api"}}}
    assert fake.writes[4][1]["upstreamIds"] == ["up-new-1"]
    assert [r["upstreamName"] for r in fake.writes[5][1]["configurationData"]["routes"]] == ["product-api", "stock-api"]
    assert _snapshot(state, "product-api") == product_before  # Product untouched
    assert state["inst"]["assetVersion"] == "1.0.1" and [r["label"] for r in state["inst"]["routing"]] == ["product-api", "stock-api"]
    assert "decision: ADD" in out and "ADDED in place" in out and "1 other route(s) preserved (product-api)" in out
    assert sb.tool_names(_shared_meta(tmp_path)) == sorted(PRODUCT_TOOLS + STOCK_TOOLS)


# 3. Bridge has Product + Stock -> Product changes: Product UPDATED, Stock unchanged
def test_product_version_change_updates_product_and_preserves_stock(product, stock, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    stock_before = _snapshot(state, "stock-api")
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(product["cfg"], product["art101"], tmp_path, "1.0.1", "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["mcp-asset", "patch-instance", "patch-policy", "patch-policy"]
    asset = fake.writes[0][1]
    assert asset["version"] == "1.0.2" and sb.connection_versions(asset["metadata"]) == {"stock-api": "1.0.0", "product-api": "1.0.1"}
    assert len(asset["metadata"]["tools"]) == BASE_TOOLS + 1 + len(STOCK_TOOLS)
    assert [w[1]["policyId"] for w in fake.writes[2:]] == [9213992, 9213993]  # router + Product's transcoding; Stock's 9214003 never touched
    assert state["inst"]["assetVersion"] == "1.0.2" and state["inst"]["id"] == BRIDGE_ID
    assert state["ups"]["upstreams"][0]["connection"]["version"] == "1.0.1" and state["ups"]["upstreams"][0]["connection"]["apiVersionId"] == PROD_UUID
    assert _snapshot(state, "stock-api") == stock_before  # BEFORE: Stock 1.0.0 -> AFTER: Stock 1.0.0
    assert "decision: UPDATE (differs in: mcp_version, connection_version, tools, routes)" in out
    assert "shared MCP asset: uif-domains-mcp@1.0.2 (decision bump" in out
    assert "route stock-api PRESERVED (upstream id, uri, connection, tools, outbound policy id): PASS" in out
    assert not any(m == "POST" and p.endswith("/environments/%s/apis" % ENV) for m, p in fake.record)  # no create
    merged = _shared_meta(tmp_path)
    assert sb.connection_versions(merged) == {"stock-api": "1.0.0", "product-api": "1.0.1"} and set(STOCK_TOOLS) <= set(sb.tool_names(merged))


# 4. no Product change -> NO-OP (nothing written, not even the Exchange publish)
def test_no_change_is_a_noop_with_reads_only(product, stock, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(product["cfg"], product["art100"], tmp_path, "1.0.0", "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.writes == [] and fake.methods() == {"GET"}
    assert "shared MCP asset: uif-domains-mcp@1.0.1 (decision reuse" in out and "NO-OP" in out
    assert sb.connection_versions(_shared_meta(tmp_path)) == {"product-api": "1.0.0", "stock-api": "1.0.0"}  # conformance input still written


# 5. Product's mappings drifted at the same versions -> tools/routes fixed, Stock untouched, asset reused
def test_mapping_drift_of_product_is_fixed_without_touching_stock_or_publishing(product, stock, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    state["pols"]["policies"][3]["configuration"]["tools"].pop()  # Product's outbound mapping lost a tool
    state["pols"]["policies"][2]["configuration"]["routes"][0]["tools"].pop()
    stock_before = _snapshot(state, "stock-api")
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(product["cfg"], product["art100"], tmp_path, "1.0.0", "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["patch-instance", "patch-policy", "patch-policy"]  # no mcp-asset publish: content identical -> reuse 1.0.1
    assert "decision: UPDATE (differs in: tools, routes)" in out
    assert len(state["pols"]["policies"][3]["configuration"]["tools"]) == BASE_TOOLS
    assert _snapshot(state, "stock-api") == stock_before


# 6. a third domain is added -> both existing routes intact
def test_third_domain_is_added_and_both_existing_routes_stay_intact(product, stock, customer, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    before = {l: _snapshot(state, l) for l in ("product-api", "stock-api")}
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(customer["cfg"], customer["art100"], tmp_path, "1.0.0", "--execute")) == 0
    assert fake.kinds() == ["external", "mcp-asset", "post-upstream", "patch-instance", "post-outbound-policy", "patch-policy"]
    assert fake.writes[1][1]["version"] == "1.0.2"
    assert sb.connection_versions(fake.writes[1][1]["metadata"]) == {"product-api": "1.0.0", "stock-api": "1.0.0", "customer-api": "1.0.0"}
    assert [r["label"] for r in state["inst"]["routing"]] == ["product-api", "stock-api", "customer-api"]
    assert {l: _snapshot(state, l) for l in before} == before
    assert "2 other route(s) preserved (product-api, stock-api)" in capsys.readouterr().out


# 7. a domain absent from the run's input is never removed (Product run keeps Stock in Bridge AND asset)
def test_absent_domain_is_never_removed(product, stock, tmp_path, monkeypatch):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(product["cfg"], product["art101"], tmp_path, "1.0.1", "--execute")) == 0
    assert [r["label"] for r in state["inst"]["routing"]] == ["product-api", "stock-api"]
    assert [r["upstreamName"] for r in state["pols"]["policies"][2]["configuration"]["routes"]] == ["product-api", "stock-api"]
    assert sorted(t["name"] for t in state["pols"]["policies"][4]["configuration"]["tools"]) == STOCK_TOOLS
    published = json.loads(fake.mcp_assets["1.0.2"].decode("utf-8"))
    assert "stock-api" in sb.connection_versions(published) and set(STOCK_TOOLS) <= set(sb.tool_names(published))


# 9. conformance is checked against the MERGED tool set the shared Bridge serves
def test_conformance_expects_every_domains_tools(product, stock, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(product["cfg"], product["art100"], tmp_path, "1.0.0", "--execute")) == 0
    expected = str(tmp_path / deploy_bridge.SHARED_METADATA_FILE)
    served = {"tools": [{"name": n} for n in PRODUCT_TOOLS + STOCK_TOOLS]}
    monkeypatch.setattr(mcp_conformance, "call", lambda url, payload, sid=None, timeout=30: (
        {"result": {"serverInfo": {"name": "shared"}}} if payload["method"] == "initialize" else
        {"result": served} if payload["method"] == "tools/list" else None, "sid"))
    assert mcp_conformance.main(["--mcp-url", "https://mcp-dev.pearson.com/domainsTest/mcp", "--expected-metadata", expected, "--execute"]) == 0
    assert "CONFORMANT" in capsys.readouterr().out
    served["tools"] = [{"name": n} for n in PRODUCT_TOOLS]  # a Bridge serving only Product is NOT conformant for the shared set
    assert mcp_conformance.main(["--mcp-url", "https://mcp-dev.pearson.com/domainsTest/mcp", "--expected-metadata", expected, "--execute"]) == 1
    assert "MISSING: %s" % ", ".join(STOCK_TOOLS) in capsys.readouterr().out


# ── refusals: nothing written, not even an Exchange publish ───────────────────────

def test_identity_guard_refuses_before_any_exchange_write(product, stock, tmp_path, monkeypatch):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    state["ups"]["upstreams"][0]["connection"]["assetId"] = "foreign-api"  # my route label serves someone else's asset
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(_args(product["cfg"], product["art101"], tmp_path, "1.0.1", "--execute"))
    assert "identity guard" in str(e.value) and "no Exchange write either" in str(e.value) and fake.writes == []
    # two instances with the shared label -> ambiguous, refused before any guard
    dup = {"id": 555, "instanceLabel": SHARED_LABEL, "endpoint": {"proxyUri": "https://0.0.0.0:443/other"}, "deployment": {}}
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets, extra_bridges=[dup])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(_args(product["cfg"], product["art101"], tmp_path, "1.0.1", "--execute"))
    assert "ambiguous shared Bridge identity" in str(e.value) and fake.writes == []


def test_bound_asset_missing_in_exchange_fails_closed_before_any_write(product, stock, tmp_path, monkeypatch):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    del assets["1.0.1"]  # the Bridge binds a version Exchange no longer serves
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(_args(product["cfg"], product["art101"], tmp_path, "1.0.1", "--execute"))
    assert "FAIL CLOSED (shared MCP asset)" in str(e.value) and "other domains' tools cannot be preserved" in str(e.value)
    assert fake.writes == []


def test_cross_domain_tool_name_collision_fails_closed(product, tmp_path, monkeypatch):
    """Another domain already serves a tool with the same derived name -> refuse before any write."""
    _live(monkeypatch)
    clone_root = tmp_path / "clone"
    clone_root.mkdir()
    (clone_root / "src" / "api").mkdir(parents=True)
    (clone_root / "src" / "mcp").mkdir(parents=True)
    (clone_root / "src" / "api" / "openapi.yaml").write_bytes(SPEC.read_bytes())  # same paths -> same tool names
    (clone_root / "src" / "mcp" / "bridge-config.yaml").write_text(yaml.safe_dump(
        {"domain": "clone", "source_asset_id": "clone-api", "environments": {"dev": {"upstream_url": "https://clone/"}}}), encoding="utf-8")
    art = _gen(clone_root / "src" / "api" / "openapi.yaml", "clone-api", tmp_path / "clone-art", "1.0.0")
    state = shared_state([_product_payload(product, product["art100"], "1.0.0")], "1.0.0")
    fake = AnypointFake(externals=[EXTERNAL_PROD], bridge=state, mcp_assets={"1.0.0": merged_asset(Path(product["art100"]) / "mcp-metadata.json")})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(_args(str(clone_root / "src" / "mcp" / "bridge-config.yaml"), art, tmp_path / "out", "1.0.0", "--execute"))
    assert "already served by ANOTHER domain" in str(e.value) and fake.kinds() == ["external"]  # only its own upstream was ensured


@pytest.mark.parametrize("reject, expected_writes", [("instance", ["mcp-asset"]), ("policy", ["mcp-asset", "patch-instance"])])
def test_update_failure_stops_immediately_without_delete_or_recreate(product, stock, tmp_path, monkeypatch, reject, expected_writes):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    fake = AnypointFake(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=state, mcp_assets=assets, reject=reject)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(_args(product["cfg"], product["art101"], tmp_path, "1.0.1", "--execute"))
    assert "UPDATE REJECTED" in str(e.value)
    assert fake.kinds() == expected_writes  # the immutable asset version stays published (harmless: a re-run reuses it)
    assert not any(m == "POST" and p.endswith("/environments/%s/apis" % ENV) for m, p in fake.record)
    assert (Path(product["art101"]) / "update-rejected.json").exists()


# ── preflight is read-only for every branch ──────────────────────────────────────

def test_preflight_reports_create_add_update_or_noop_without_writes(product, stock, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    world, assets = _two_domain_world(product, stock)
    product_only = shared_state([_product_payload(product, product["art100"], "1.0.0")], "1.0.0")
    cases = (
        (product, product["art100"], "1.0.0", dict(externals=[EXTERNAL_PROD], bridge=None), "Decision: CREATE"),
        (stock, stock["art100"], "1.0.0", dict(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=product_only, mcp_assets={"1.0.0": assets["1.0.0"]}), "decision: ADD"),
        (product, product["art101"], "1.0.1", dict(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=world, mcp_assets=assets), "decision: UPDATE"),
        (product, product["art100"], "1.0.0", dict(externals=[EXTERNAL_PROD, EXTERNAL_STOCK], bridge=world, mcp_assets=assets), "NO-OP"),
    )
    for dom, arts, rest, fake_kwargs, expect in cases:
        fake = AnypointFake(**fake_kwargs)
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        assert deploy_bridge.main(_args(dom["cfg"], arts, tmp_path, rest, "--preflight")) == 0
        out = capsys.readouterr().out
        assert expect in out and fake.writes == [] and fake.methods() == {"GET"}, expect
    assert not (tmp_path / "source-instance.json").exists()  # preflight never writes the real capture
    assert (tmp_path / "preflight" / "source-instance.json").exists()  # its read-only twin lives apart


def test_preflight_refuses_when_bridge_exists_but_upstream_missing(product, stock, tmp_path, monkeypatch):
    _live(monkeypatch)
    state, assets = _two_domain_world(product, stock)
    fake = AnypointFake(externals=[EXTERNAL_STOCK], bridge=state, mcp_assets=assets)  # Product's upstream absent
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(_args(product["cfg"], product["art101"], tmp_path, "1.0.1", "--preflight"))
    assert "shared Bridge %s exists but no external instance 'uif-product-dev'" % BRIDGE_ID in str(e.value) and fake.writes == []


def test_dry_run_still_sends_nothing(product, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    assert deploy_bridge.main(_args(product["cfg"], product["art101"], tmp_path, "1.0.1", "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "route absent: ADD | differs: UPDATE in place | identical: NO-OP | every other domain's route preserved | never delete/recreate" in out
    assert "shared MCP asset (dry-run) -- uif-domains-mcp: merged at deploy time" in out
    assert '"instanceLabel": "uif-domains-mcp"' in out and "DRY-RUN: nothing sent" in out
