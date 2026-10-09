"""contract_gate.py - the OpenAPI is the only source of truth; generated MCP
artifacts are derived; the gate validates the transformation. Synthetic contracts
prove add / remove / rename are validated with NO expected-tool list anywhere,
and that tampering or unsupported input still fails closed."""
import copy
import json
from pathlib import Path

import pytest
import yaml

import contract_gate as cg
from generate_mcp_metadata import generate, write_artifacts

REPO = Path(__file__).resolve().parents[4]

BASE_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Orders API", "version": "1.0.0"},
    "servers": [{"url": "/api/v2"}],
    "paths": {
        "/orders": {
            "get": {"operationId": "listOrders", "summary": "List",
                    "parameters": [{"name": "pageSize", "in": "query", "schema": {"type": "integer"}},
                                   {"name": "X-Tenant", "in": "header", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "ok"}}},
            "post": {"operationId": "createOrder", "summary": "Create",
                     "requestBody": {"required": True, "content": {"application/json": {
                         "schema": {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]}}}},
                     "responses": {"201": {"description": "created"}}},
        },
        "/orders/{orderId}": {
            "parameters": [{"name": "orderId", "in": "path", "required": True, "schema": {"type": "string"}}],
            "get": {"operationId": "getOrder", "summary": "Get", "responses": {"200": {"description": "ok"}}},
        },
    },
}


def _write(tmp_path, doc, name="openapi.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return str(p)


def _gen(spec):
    return generate(spec, source_ref={"assetId": "orders-api", "version": "1.0.0", "groupId": "g"},
                    mcp_asset={"assetId": "uif-orders-mcp", "version": "1.0.0"}, org_id="g")


def _gate(spec, art=None):
    art = art or _gen(spec)
    return cg.check_generation(spec, art["metadata"], art["mappings"]), art


# ── derivation from the contract ──────────────────────────────────────────────────

def test_base_contract_passes_and_names_are_derived_not_listed(tmp_path):
    spec = _write(tmp_path, BASE_SPEC)
    problems, art = _gate(spec)
    assert problems == []
    expected = cg.expected_tools_from_contract(spec)
    assert [e["name"] for e in expected] == ["get_orders", "post_orders", "get_orders_orderid"]
    by = {e["name"]: e for e in expected}
    assert by["get_orders"]["path"] == "/api/v2/orders" and by["get_orders"]["params"]["query"] == ["pageSize"]
    assert by["get_orders"]["params"]["header"] == ["X-Tenant"] and by["get_orders"]["required_params"] == ["X-Tenant"]
    assert by["post_orders"]["has_body"] and by["post_orders"]["body_required"]
    assert by["get_orders_orderid"]["params"]["path"] == ["orderId"]  # shared path-level parameter picked up


def test_adding_an_operation_needs_only_the_openapi_change(tmp_path):
    doc = copy.deepcopy(BASE_SPEC)
    doc["paths"]["/orders/{orderId}/lines"] = {
        "parameters": [{"name": "orderId", "in": "path", "required": True, "schema": {"type": "string"}}],
        "get": {"operationId": "listOrderLines", "summary": "Lines", "responses": {"200": {"description": "ok"}}}}
    spec = _write(tmp_path, doc)
    problems, art = _gate(spec)
    assert problems == []
    names = sorted(t["name"] for t in art["metadata"]["tools"])
    assert names == ["get_orders", "get_orders_orderid", "get_orders_orderid_lines", "post_orders"]
    new = next(m for m in art["mappings"]["tools"] if m["name"] == "get_orders_orderid_lines")
    assert (new["method"], new["path"]) == ("GET", "/api/v2/orders/{orderId}/lines")
    assert new["uriParams"] == [{"key": "orderId", "value": "#[vars.params['orderId']]"}]


def test_removing_and_renaming_operations_is_reflected_dynamically(tmp_path):
    doc = copy.deepcopy(BASE_SPEC)
    del doc["paths"]["/orders"]["post"]                                  # remove
    doc["paths"]["/orders/{orderId}/summary"] = doc["paths"].pop("/orders/{orderId}")  # rename (path change)
    spec = _write(tmp_path, doc)
    problems, art = _gate(spec)
    assert problems == []
    assert sorted(t["name"] for t in art["metadata"]["tools"]) == ["get_orders", "get_orders_orderid_summary"]
    assert "post_orders" not in {m["name"] for m in art["mappings"]["tools"]}
    assert art["mappings"]["routes"][0]["tools"] == ["get_orders", "get_orders_orderid_summary"]


def test_stale_artifacts_after_a_contract_change_fail_the_gate(tmp_path):
    """The failure mode the gate exists for: the contract changed, the artifacts did not."""
    spec_v1 = _write(tmp_path, BASE_SPEC, "v1.yaml")
    stale = _gen(spec_v1)
    doc = copy.deepcopy(BASE_SPEC)
    doc["paths"]["/orders/{orderId}/lines"] = {"get": {"operationId": "x", "responses": {"200": {"description": "ok"}}}}
    spec_v2 = _write(tmp_path, doc, "v2.yaml")
    problems = cg.check_generation(spec_v2, stale["metadata"], stale["mappings"])
    assert any("missing tools derived from the contract: ['get_orders_orderid_lines']" in p for p in problems)
    assert any(p.startswith("route tools") for p in problems)


# ── fail closed ─────────────────────────────────────────────────────────────────

def test_duplicate_derived_tool_names_fail_closed(tmp_path):
    doc = copy.deepcopy(BASE_SPEC)
    doc["paths"]["/orders/orderId"] = {"get": {"operationId": "clash", "responses": {"200": {"description": "ok"}}}}  # -> get_orders_orderid too
    spec = _write(tmp_path, doc)
    problems, _ = _gate(spec)
    assert any("duplicate tool names ['get_orders_orderid']" in p for p in problems)


@pytest.mark.parametrize("tamper", ["drop_tool", "wrong_path", "wrong_method", "drop_uri_param", "extra_tool",
                                    "drop_body", "drop_required", "route_missing"])
def test_tampered_artifacts_are_named_precisely(tmp_path, tamper):
    spec = _write(tmp_path, BASE_SPEC)
    art = _gen(spec)
    meta, maps = copy.deepcopy(art["metadata"]), copy.deepcopy(art["mappings"])
    m_get = next(m for m in maps["tools"] if m["name"] == "get_orders_orderid")
    if tamper == "drop_tool":
        meta["tools"] = [t for t in meta["tools"] if t["name"] != "post_orders"]
    elif tamper == "wrong_path":
        m_get["path"] = "/api/v2/order/{orderId}"
    elif tamper == "wrong_method":
        m_get["method"] = "POST"
    elif tamper == "drop_uri_param":
        m_get["uriParams"] = []
    elif tamper == "extra_tool":
        maps["tools"].append(dict(m_get, name="get_ghost"))
    elif tamper == "drop_body":
        del next(m for m in maps["tools"] if m["name"] == "post_orders")["body"]
    elif tamper == "drop_required":
        next(t for t in meta["tools"] if t["name"] == "post_orders")["inputSchema"].pop("required")
    elif tamper == "route_missing":
        maps["routes"][0]["tools"].remove("get_orders")
    problems = cg.check_generation(spec, meta, maps)
    assert problems, tamper
    expected_fragment = {
        "drop_tool": "missing tools derived from the contract: ['post_orders']",
        "wrong_path": "mapping path '/api/v2/order/{orderId}' != contract server base + path '/api/v2/orders/{orderId}'",
        "wrong_method": "mapping method 'POST' != contract 'GET'",
        "drop_uri_param": "uriParams [] != contract path parameters ['orderId']",
        "extra_tool": "tools with no contract operation: ['get_ghost']",
        "drop_body": "mapping body presence False != contract requestBody True",
        "drop_required": "inputSchema required [] != contract required ['body']",
        "route_missing": "route tools",
    }[tamper]
    assert any(expected_fragment in p for p in problems), problems


def test_unsupported_31_construct_still_fails_the_projection_closed(tmp_path):
    doc = copy.deepcopy(BASE_SPEC)
    doc["components"] = {"schemas": {"Bad": {"type": ["string", "integer"]}}}  # a real union, not a nullable
    spec = _write(tmp_path, doc)
    problems, stats = cg.check_projection(spec)
    assert stats is None and problems and problems[0].startswith("projection fails closed:")
    good, stats = cg.check_projection(_write(tmp_path, BASE_SPEC, "good.yaml"))
    assert good == [] and stats["operations"] == 3


def test_cli_passes_on_matching_artifacts_and_fails_on_drift(tmp_path, capsys):
    spec = _write(tmp_path, BASE_SPEC)
    paths = write_artifacts(_gen(spec), str(tmp_path / "out"))
    assert cg.main(["--spec", spec, "--metadata", paths["metadata"], "--mappings", paths["mappings"]]) == 0
    out = capsys.readouterr().out
    assert "3 operation(s) -> 3 tool(s): get_orders, post_orders, get_orders_orderid" in out and "RESULT: PASS" in out
    doc = copy.deepcopy(BASE_SPEC)
    doc["paths"]["/refunds"] = {"post": {"operationId": "refund", "responses": {"200": {"description": "ok"}}}}
    changed = _write(tmp_path, doc, "changed.yaml")
    assert cg.main(["--spec", changed, "--metadata", paths["metadata"], "--mappings", paths["mappings"]]) == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


# ── the real domain contracts pass through the same generic gate ───────────────────

import affected_domains as _ad


@pytest.mark.parametrize("domain", _ad.processable(str(REPO)))  # every READY domain of this repository
def test_real_domain_contracts_pass_the_generic_gate(domain):
    spec = str(REPO / _ad.resolve_contract(str(REPO), domain)[0])
    problems, art = _gate(spec)
    assert problems == []
    assert cg.check_projection(spec)[0] == []
    assert len(art["metadata"]["tools"]) == len(cg.expected_tools_from_contract(spec)) >= 1
