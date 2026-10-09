"""Regression for the first live ADD (2026-09-30, Bridge 21206639 in the isolated test namespace):
the exact two-domain scenario, replayed offline against a fake that enforces the
platform's routing schema exactly as the live HTTP 400 RoutingValidationError showed.

State before (fixture names are the canonical test-owned ones; the live run used an isolated
namespace): Product route product-api@2.0.1 (3 tools, upstream
605851c7-403c-4ad7-9426-ff547bbbb36e, source instance 47b416b5-...), shared asset
shared asset 1.0.1 bound. Price prepared: price-api@1.0.0, external
instance uif-price-dev = 12ac22aa-bc47-4d2f-90ab-07abd7e1c636, shared asset 1.0.2
already published with both connections (the run that failed had got that far).

Proves: the old inline shape is rejected by the fake exactly like the platform; the
corrected ADD passes the same fake with the documented four-operation sequence, adds
Price by upstream REFERENCE and leaves Product byte-identical; a re-run is a NO-OP.
"""
import copy
import json
import urllib.request
from pathlib import Path

import pytest
import yaml

import deploy_bridge
import shared_bridge as sb
import update_bridge as ub
from anypoint_fake import (AnypointFake, ORG, PLATFORM, SHARED_LABEL, SHARED_MCP, SHARED_PATH, domain_payload, merged_asset,
                           shared_state, synthetic_domain)
from generate_mcp_metadata import generate, write_artifacts

REPO = Path(__file__).resolve().parents[4]
from anypoint_fake import PRODUCT_SPEC  # test-owned single-file fixture contract
UPSTREAM = "https://upstream.example.test/service/"
LABEL, MCP, PATH_ = SHARED_LABEL, SHARED_MCP, SHARED_PATH  # the test-owned shared identity (anypoint_fake.TEST_PLATFORM)
BRIDGE_ID = 21206639
PRODUCT_UP_ID = "605851c7-403c-4ad7-9426-ff547bbbb36e"
PRODUCT_INSTANCE = "47b416b5-a34f-41ed-9f55-e4c3bdc5ed64"
PRICE_INSTANCE = "12ac22aa-bc47-4d2f-90ab-07abd7e1c636"


def _gen(spec, asset, out, version):
    write_artifacts(generate(str(spec), source_ref={"assetId": asset, "version": version, "groupId": ORG},
                             mcp_asset={"assetId": MCP, "version": version}, org_id=ORG), str(out))
    return str(out)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """The exact live state + the Price artifacts, with the test-owned identities."""
    shared = sb.load_shared(yaml.safe_load(Path(PLATFORM).read_text(encoding="utf-8"))["dev"], "dev")
    product_cfg = {"domain": "product", "source_asset_id": "product-api", "source_instance_name": "uif-product-dev",
                   "environments": {"dev": {"upstream_url": UPSTREAM}}}
    product_eff = sb.write_effective_config(sb.effective_config(product_cfg, shared), str(tmp_path_factory.mktemp("eff-product")))
    product_art = _gen(PRODUCT_SPEC, "product-api", tmp_path_factory.mktemp("p201"), "2.0.1")
    price_spec, price_cfg_path = synthetic_domain(tmp_path_factory.mktemp("price"), "price", "prices", UPSTREAM)
    doc = yaml.safe_load(price_cfg_path.read_text(encoding="utf-8"))
    doc["source_asset_id"], doc["source_instance_name"] = "price-api", "uif-price-dev"
    price_cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    price_eff = sb.write_effective_config(sb.effective_config(doc, shared), str(tmp_path_factory.mktemp("eff-price")))
    price_art = _gen(price_spec, "price-api", tmp_path_factory.mktemp("pr100"), "1.0.0")

    product_payload = domain_payload(product_eff, product_art, "1.0.1", PRODUCT_INSTANCE, "uif-product-dev", UPSTREAM, LABEL, PATH_)
    state = shared_state([product_payload], "1.0.1", instance_id=BRIDGE_ID, label=LABEL, base_path=PATH_, mcp_asset_id=MCP)
    # the live upstream id / product API version of the real state
    state["ups"]["upstreams"][0]["id"] = PRODUCT_UP_ID
    state["inst"]["routing"][0]["upstreams"][0]["id"] = PRODUCT_UP_ID
    state["pols"]["policies"][3]["upstreamId"] = PRODUCT_UP_ID
    state["ups"]["upstreams"][0]["connection"]["productAPIVersion"] = "v2"
    assets = {"1.0.1": merged_asset(Path(product_art) / "mcp-metadata.json"),
              "1.0.2": merged_asset(Path(product_art) / "mcp-metadata.json", Path(price_art) / "mcp-metadata.json")}  # already published by the failed run
    ext_product = {"id": PRODUCT_INSTANCE, "type": "external", "name": "uif-product-dev", "endpointUri": UPSTREAM, "isPublic": False, "productAPIVersion": "v2"}
    ext_price = {"id": PRICE_INSTANCE, "type": "external", "name": "uif-price-dev", "endpointUri": UPSTREAM, "isPublic": False, "productAPIVersion": "v1"}
    rest_assets = {"product-api": {"2.0.1": [ext_product]}, "price-api": {"1.0.0": [ext_price]}}
    return {"state": state, "assets": assets, "rest_assets": rest_assets, "price_cfg": str(price_cfg_path), "price_eff": price_eff,
            "price_art": price_art, "product_art": product_art}


def _snapshot(state, label):
    r = next(x for x in state["inst"]["routing"] if x["label"] == label)
    uid = r["upstreams"][0]["id"]
    return copy.deepcopy({"routing": r, "upstream": next(u for u in state["ups"]["upstreams"] if u["id"] == uid),
                          "policy": next(p for p in state["pols"]["policies"] if p.get("upstreamId") == uid),
                          "route": next(x for x in state["pols"]["policies"][2]["configuration"]["routes"] if x["upstreamName"] == label)})


def _fake(world):
    return AnypointFake(mcp_asset_id=MCP, bridge=copy.deepcopy(world["state"]), mcp_assets=dict(world["assets"]),
                        rest_assets=copy.deepcopy(world["rest_assets"]))


def _live(monkeypatch):
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")


def test_the_rejected_live_payload_is_rejected_by_the_fake_exactly_like_the_platform():
    """mcp-test-artifacts/price/update-rejected.json, replayed: same instancePath, same four errors."""
    rejected = REPO / "mcp-test-artifacts" / "price" / "update-rejected.json"
    if rejected.exists():
        body = json.load(open(rejected, encoding="utf-8"))["body"]
    else:  # the shape, reconstructed
        body = {"upstreams": [{"id": PRODUCT_UP_ID, "uri": UPSTREAM, "connection": {}}],
                "routing": [{"label": "product-api", "upstreams": [{"id": PRODUCT_UP_ID, "weight": 100}], "rules": {}},
                            {"label": "price-api", "upstreams": [{"weight": 100, "uri": UPSTREAM, "connection": {}, "policies": []}], "rules": {}}]}
    errors = AnypointFake.routing_validation_errors(body)
    assert [e["instancePath"] for e in errors] == ["/1/upstreams/0"] * 4
    assert [e["keyword"] for e in errors] == ["required", "unevaluatedProperties", "unevaluatedProperties", "unevaluatedProperties"]
    assert {e["params"].get("missingProperty") or e["params"]["unevaluatedProperty"] for e in errors} == {"id", "uri", "connection", "policies"}
    with pytest.raises(SystemExit):
        ub.assert_patch_routing_is_references_only(body)  # and the tooling refuses to build/send it at all


def test_corrected_add_of_price_passes_the_platform_schema_and_preserves_product(world, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = _fake(world)
    product_before = _snapshot(fake.bridge, "product-api")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    rc = deploy_bridge.main(["--config", world["price_cfg"], "--artifacts", world["price_art"], "--environment", "dev",
                             "--rest-asset-version", "1.0.0", "--out", str(tmp_path), "--platform-config", PLATFORM, "--execute"])
    out = capsys.readouterr().out
    assert rc == 0
    # the shared asset 1.0.2 already exists with identical merged content -> reused, not republished
    assert "shared MCP asset: %s@1.0.2 (decision reuse" % MCP in out
    assert fake.kinds() == ["post-upstream", "patch-instance", "post-outbound-policy", "patch-policy"]
    a1, a2, a3, a4 = (w[1] for w in fake.writes)
    assert a1 == {"label": "uif-price-dev", "uri": UPSTREAM}
    assert a2["assetVersion"] == "1.0.2" and a2["spec"]["assetId"] == MCP
    assert [u["id"] for u in a2["upstreams"]] == [PRODUCT_UP_ID, "up-new-1"]
    assert a2["upstreams"][0] == {"id": PRODUCT_UP_ID, "uri": UPSTREAM, "connection": product_before["upstream"]["connection"]}
    assert a2["upstreams"][1]["connection"]["apiVersionId"] == PRICE_INSTANCE and a2["upstreams"][1]["connection"]["assetId"] == "price-api"
    assert a2["routing"] == [
        {"label": "product-api", "upstreams": [{"id": PRODUCT_UP_ID, "weight": 100}], "rules": {"headers": {"X-UPSTREAM-NAME": "product-api"}}},
        {"label": "price-api", "upstreams": [{"id": "up-new-1", "weight": 100}], "rules": {"headers": {"X-UPSTREAM-NAME": "price-api"}}}]
    assert AnypointFake.routing_validation_errors(a2) == []
    routes = next(p for p in a2["policies"] if p["assetId"] == "mcp-transcoding-router")["configurationData"]["routes"]
    price_tools = ["get_prices", "get_prices_sku"]  # the synthetic Price fixture derives two tools
    assert routes[0]["upstreamName"] == "product-api" and len(routes[0]["tools"]) == 3 and routes[1] == {"upstreamName": "price-api", "tools": price_tools}
    assert a3["upstreamIds"] == ["up-new-1"] and a3["assetId"] == "mcp-transcoding" and [t["name"] for t in a3["configurationData"]["tools"]] == price_tools
    assert a4["policyId"] == 9213992 and [r["upstreamName"] for r in a4["configurationData"]["routes"]] == ["product-api", "price-api"]
    # end state: Product byte-identical, Price routed to the A1 upstream with its outbound policy
    assert _snapshot(fake.bridge, "product-api") == product_before
    price = _snapshot(fake.bridge, "price-api")
    assert price["upstream"]["id"] == "up-new-1" and price["upstream"]["connection"]["apiVersionId"] == PRICE_INSTANCE and price["policy"]["upstreamId"] == "up-new-1"
    assert fake.bridge["inst"]["assetVersion"] == "1.0.2"
    for line in ("route price-api served by the upstream created/reused in A1 (up-new-1): PASS", "no unrouted upstream left behind: PASS",
                 "route product-api PRESERVED (upstream id, uri, connection, tools, outbound policy id): PASS", "ADDED in place"):
        assert line in out
    # and the same run again is a NO-OP
    fake.record.clear(); fake.writes.clear()
    assert deploy_bridge.main(["--config", world["price_cfg"], "--artifacts", world["price_art"], "--environment", "dev",
                               "--rest-asset-version", "1.0.0", "--out", str(tmp_path), "--platform-config", PLATFORM, "--execute"]) == 0
    assert fake.writes == [] and fake.methods() == {"GET"} and "NO-OP" in capsys.readouterr().out


def test_preflight_of_the_corrected_add_prints_all_four_requests_and_writes_nothing(world, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = _fake(world)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(["--config", world["price_cfg"], "--artifacts", world["price_art"], "--environment", "dev",
                               "--rest-asset-version", "1.0.0", "--out", str(tmp_path), "--platform-config", PLATFORM, "--preflight"]) == 0
    out = capsys.readouterr().out
    assert "decision: ADD (differs in: route price-api absent, mcp_version)" in out
    for marker in ("A1 POST", "/apis/%d/upstreams" % BRIDGE_ID, '{"label": "uif-price-dev", "uri": "%s"}' % UPSTREAM,
                   "A2 PATCH", "routes as {id, weight} references", "A3 POST", "/policies/outbound-policies", "A4 PATCH", "PREFLIGHT ONLY"):
        assert marker in out, marker
    assert fake.writes == [] and fake.methods() == {"GET"}
