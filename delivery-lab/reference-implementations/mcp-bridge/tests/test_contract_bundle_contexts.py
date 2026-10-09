"""Context-aware lifting: the components/<section> of an external target is decided by WHERE the
reference sits in the OpenAPI structure, never by what the target looks like. Regression for the
2026-09-30 finding that Header, Example and SecurityScheme targets were silently lifted into
components/schemas. Domain names are synthetic."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

import contract_bundle as cb
import generate_mcp_metadata as gm
import oas_projection as op
import release_version as rv
import validate_mcp_metadata as vm

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "multifile-stock"
REPO = HERE.parents[3]
ORG = "8c27297e-4bcf-4c53-99eb-ffd7b1069294"


def write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1) if path.suffix == ".json" else yaml.safe_dump(doc, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def api_dir(tmp_path):
    d = tmp_path / "domains" / "d" / "src"  # the contract root = the domain's src/ (containment boundary)
    d.mkdir(parents=True, exist_ok=True)
    return d


def operation(**extra):
    base = {"operationId": "x", "description": "d", "responses": {"200": {"description": "ok"}}}
    base.update(extra)
    return base


def root(paths=None, components=None):
    doc = {"openapi": "3.1.0", "info": {"title": "t", "version": "1.0.0"}, "paths": paths or {}}
    if components is not None:
        doc["components"] = components
    return doc


@pytest.fixture
def api(tmp_path):
    """A library of external component files of every type."""
    d = api_dir(tmp_path)
    write(d / "schemas" / "product.yaml", {"Product": {"type": "object", "properties": {"id": {"type": "integer"}}}})
    write(d / "schemas" / "error.yaml", {"Error": {"type": "object", "properties": {"error": {"type": "string"}}}})
    write(d / "common" / "parameters.yaml", {"ProductId": {"name": "productId", "in": "path", "required": True, "schema": {"$ref": "../schemas/product.yaml#/Product"}},
                                            "Plain": {"name": "q", "in": "query", "schema": {"type": "string"}}})
    write(d / "common" / "responses.yaml", {"NotFound": {"description": "nf", "content": {"application/json": {"schema": {"$ref": "../schemas/error.yaml#/Error"}}},
                                                        "headers": {"ETag": {"$ref": "./headers.yaml#/ETag"}}},
                                           "Empty": {"description": "empty"}})
    write(d / "common" / "headers.yaml", {"ETag": {"description": "etag", "schema": {"$ref": "../schemas/error.yaml#/Error"}},
                                         "Plain": {"description": "plain", "schema": {"type": "string"}}})
    write(d / "common" / "request-bodies.yaml", {"CreateProduct": {"required": True, "content": {"application/json": {"schema": {"$ref": "../schemas/product.yaml#/Product"}}}}})
    write(d / "common" / "examples.yaml", {"Sample": {"summary": "s", "value": {"id": 1}}})
    write(d / "common" / "security.yaml", {"EntraID": {"type": "openIdConnect", "openIdConnectUrl": "https://login.example/x"}})
    write(d / "common" / "links.yaml", {"Next": {"operationId": "x", "parameters": {"productId": "$response.body#/id"}}})
    write(d / "common" / "callbacks.yaml", {"OnEvent": {"{$request.body#/url}": {"post": operation()}}})
    write(d / "common" / "path-items.yaml", {"Probe": {"get": operation()}})
    return d


def bundle(d, doc):
    write(d / "openapi.yaml", doc)
    return cb.bundle(d / "openapi.yaml")


def sections(doc):
    return {s: sorted(m) for s, m in (doc.get("components") or {}).items() if m}


# ── every supported type lands in its own section ───────────────────────────────

def test_schema_targets_whole_file_fragment_and_nested(api):
    write(api / "whole.json", {"type": "object", "properties": {"p": {"$ref": "./schemas/product.yaml#/Product"}}})
    doc, c = bundle(api, root({"/p": {"post": operation(requestBody={"content": {"application/json": {"schema": {"$ref": "./whole.json"}}}})}}))
    assert sections(doc) == {"schemas": ["schemas_product_Product", "whole"]}
    assert doc["components"]["schemas"]["whole"]["properties"]["p"] == {"$ref": "#/components/schemas/schemas_product_Product"}
    assert c["lifted"] == ["schemas/whole", "schemas/schemas_product_Product"]


def test_parameter_target_lands_in_parameters_and_its_schema_in_schemas(api):
    doc, c = bundle(api, root({"/p/{productId}": {"get": operation(parameters=[{"$ref": "./common/parameters.yaml#/ProductId"}])}}))
    assert sections(doc) == {"parameters": ["common_parameters_ProductId"], "schemas": ["schemas_product_Product"]}
    param = doc["components"]["parameters"]["common_parameters_ProductId"]
    assert param["name"] == "productId" and param["in"] == "path" and param["schema"] == {"$ref": "#/components/schemas/schemas_product_Product"}
    assert doc["paths"]["/p/{productId}"]["get"]["parameters"] == [{"$ref": "#/components/parameters/common_parameters_ProductId"}]
    assert c["lifted"] == ["parameters/common_parameters_ProductId", "schemas/schemas_product_Product"]


def test_path_item_level_parameters_are_parameters_too(api):
    doc, _ = bundle(api, root({"/p": {"parameters": [{"$ref": "./common/parameters.yaml#/Plain"}], "get": operation()}}))
    assert sections(doc) == {"parameters": ["common_parameters_Plain"]}


def test_response_target_lands_in_responses_with_nested_schema_and_header(api):
    doc, _ = bundle(api, root({"/p": {"get": operation(responses={"200": {"description": "ok"}, "404": {"$ref": "./common/responses.yaml#/NotFound"}})}}))
    assert sections(doc) == {"responses": ["common_responses_NotFound"], "schemas": ["schemas_error_Error"], "headers": ["common_headers_ETag"]}
    resp = doc["components"]["responses"]["common_responses_NotFound"]
    assert resp["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/schemas_error_Error"}
    assert resp["headers"]["ETag"] == {"$ref": "#/components/headers/common_headers_ETag"}  # nested Header, resolved relative to responses.yaml
    assert doc["components"]["headers"]["common_headers_ETag"]["schema"] == {"$ref": "#/components/schemas/schemas_error_Error"}
    assert doc["paths"]["/p"]["get"]["responses"]["404"] == {"$ref": "#/components/responses/common_responses_NotFound"}


def test_request_body_target_lands_in_request_bodies_with_nested_schema(api):
    doc, _ = bundle(api, root({"/p": {"post": operation(requestBody={"$ref": "./common/request-bodies.yaml#/CreateProduct"})}}))
    assert sections(doc) == {"requestBodies": ["common_request-bodies_CreateProduct"], "schemas": ["schemas_product_Product"]}
    body = doc["components"]["requestBodies"]["common_request-bodies_CreateProduct"]
    assert body["required"] is True and body["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/schemas_product_Product"}


def test_header_target_lands_in_headers_not_schemas(api):
    """REGRESSION: previously lifted into components/schemas."""
    doc, _ = bundle(api, root({"/p": {"get": operation(responses={"200": {"description": "ok", "headers": {"ETag": {"$ref": "./common/headers.yaml#/ETag"}}}})}}))
    assert sections(doc) == {"headers": ["common_headers_ETag"], "schemas": ["schemas_error_Error"]}
    assert "common_headers_ETag" not in doc["components"]["schemas"]
    assert doc["paths"]["/p"]["get"]["responses"]["200"]["headers"]["ETag"] == {"$ref": "#/components/headers/common_headers_ETag"}


def test_example_target_lands_in_examples_not_schemas(api):
    """REGRESSION: previously lifted into components/schemas."""
    media = {"schema": {"type": "object"}, "examples": {"S": {"$ref": "./common/examples.yaml#/Sample"}}}
    doc, _ = bundle(api, root({"/p": {"post": operation(requestBody={"content": {"application/json": media}})}}))
    assert sections(doc) == {"examples": ["common_examples_Sample"]}
    assert doc["components"]["examples"]["common_examples_Sample"] == {"summary": "s", "value": {"id": 1}}  # verbatim, no schema policy


def test_security_scheme_target_lands_in_security_schemes_not_schemas(api):
    """REGRESSION: previously lifted into components/schemas."""
    doc, _ = bundle(api, root(components={"securitySchemes": {"EntraID": {"$ref": "./common/security.yaml#/EntraID"}}}))
    assert sections(doc) == {"securitySchemes": ["EntraID", "common_security_EntraID"]}
    assert doc["components"]["securitySchemes"]["EntraID"] == {"$ref": "#/components/securitySchemes/common_security_EntraID"}
    assert doc["components"]["securitySchemes"]["common_security_EntraID"]["type"] == "openIdConnect"


def test_link_and_callback_targets_land_in_their_sections(api):
    doc, _ = bundle(api, root({"/p": {"post": operation(responses={"200": {"description": "ok", "links": {"n": {"$ref": "./common/links.yaml#/Next"}}}},
                                                        callbacks={"ev": {"$ref": "./common/callbacks.yaml#/OnEvent"}})}}))
    assert sections(doc) == {"links": ["common_links_Next"], "callbacks": ["common_callbacks_OnEvent"]}
    assert doc["components"]["callbacks"]["common_callbacks_OnEvent"]["{$request.body#/url}"]["post"]["operationId"] == "x"


def test_components_section_context_is_derived_from_the_section_key(api):
    doc, _ = bundle(api, root(components={
        "schemas": {"P": {"$ref": "./schemas/product.yaml#/Product"}},
        "parameters": {"Q": {"$ref": "./common/parameters.yaml#/Plain"}},
        "responses": {"E": {"$ref": "./common/responses.yaml#/Empty"}},
        "headers": {"H": {"$ref": "./common/headers.yaml#/Plain"}},
        "requestBodies": {"B": {"$ref": "./common/request-bodies.yaml#/CreateProduct"}},
        "examples": {"X": {"$ref": "./common/examples.yaml#/Sample"}},
        "links": {"L": {"$ref": "./common/links.yaml#/Next"}},
        "callbacks": {"C": {"$ref": "./common/callbacks.yaml#/OnEvent"}}}))
    got = sections(doc)
    assert set(got) == {"schemas", "parameters", "responses", "headers", "requestBodies", "examples", "links", "callbacks"}
    assert got["parameters"] == ["Q", "common_parameters_Plain"] and got["headers"] == ["H", "common_headers_Plain"]
    assert got["schemas"] == ["P", "schemas_product_Product"]  # CreateProduct's nested schema is the same target, lifted once
    op.project_oas31_to_30(doc)


# ── internal references stay untouched ───────────────────────────────────────────

def test_internal_refs_of_every_type_in_the_root_are_left_alone(api):
    doc = root({"/p": {"get": operation(parameters=[{"$ref": "#/components/parameters/P"}],
                                        responses={"400": {"$ref": "#/components/responses/R"}, "200": {"description": "ok", "headers": {"h": {"$ref": "#/components/headers/H"}},
                                                   "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S"}}}}})}},
               components={"parameters": {"P": {"name": "q", "in": "query", "schema": {"type": "string"}}}, "responses": {"R": {"description": "r"}},
                           "headers": {"H": {"schema": {"type": "string"}}}, "schemas": {"S": {"type": "object"}}})
    out, c = bundle(api, doc)
    assert out == doc and c["lifted"] == []


# ── fail closed ─────────────────────────────────────────────────────────────────

def test_external_path_item_reference_fails_closed(api):
    with pytest.raises(cb.ContractError) as e:
        bundle(api, root({"/probe": {"$ref": "./common/path-items.yaml#/Probe"}}))
    assert "PathItem" in str(e.value) and "3.0.3" in str(e.value)
    with pytest.raises(cb.ContractError):  # also inside a callback and under components.pathItems
        bundle(api, root({"/p": {"post": operation(callbacks={"ev": {"{$url}": {"$ref": "./common/path-items.yaml#/Probe"}}})}}))
    with pytest.raises(cb.ContractError):
        bundle(api, root(components={"pathItems": {"X": {"$ref": "./common/path-items.yaml#/Probe"}}}))


@pytest.mark.parametrize("doc_factory, where_hint", [
    (lambda: root({"/p": {"get": operation(**{"x-vendor": {"$ref": "./schemas/product.yaml#/Product"}})}}), "x-vendor"),
    (lambda: {**root(), "info": {"title": "t", "version": "1.0.0", "contact": {"$ref": "./schemas/product.yaml#/Product"}}}, "info"),
    (lambda: root({"/p": {"get": operation(servers=[{"$ref": "./schemas/product.yaml#/Product"}])}}), "servers"),
    (lambda: root({"/p": {"get": operation(responses={"200": {"description": "ok", "content": {"application/json": {"example": {"$ref": "./schemas/product.yaml#/Product"}}}}})}}), "example"),
    (lambda: root(components={"schemas": {"S": {"type": "object", "vendorKey": {"$ref": "./schemas/product.yaml#/Product"}}}}), "vendorKey"),
])
def test_external_ref_in_an_unknown_context_fails_closed_never_defaults_to_schema(api, doc_factory, where_hint):
    with pytest.raises(cb.ContractError) as e:
        bundle(api, doc_factory())
    msg = str(e.value)
    assert "cannot be determined" in msg and where_hint in msg and "never guesses" in msg


def test_same_target_in_two_incompatible_contexts_fails_closed(api):
    write(api / "thing.yaml", {"T": {"description": "d", "type": "object"}})
    with pytest.raises(cb.ContractError) as e:
        bundle(api, root({"/p": {"get": operation(responses={"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "./thing.yaml#/T"}}}},
                                                             "404": {"$ref": "./thing.yaml#/T"}})}}))
    assert "referenced as a Response" in str(e.value) and "already lifted as a Schema" in str(e.value)


@pytest.mark.parametrize("context_doc, message", [
    (lambda: root({"/p": {"get": operation(parameters=[{"$ref": "./common/responses.yaml#/Empty"}])}}), "no required 'name' field"),
    (lambda: root({"/p": {"get": operation(responses={"404": {"$ref": "./common/parameters.yaml#/Plain"}})}}), "no required 'description' field"),
    (lambda: root({"/p": {"post": operation(requestBody={"$ref": "./common/examples.yaml#/Sample"})}}), "no required 'content' field"),
    (lambda: root(components={"securitySchemes": {"S": {"$ref": "./common/examples.yaml#/Sample"}}}), "no required 'type' field"),
    (lambda: root({"/p": {"get": operation(responses={"200": {"description": "ok", "headers": {"h": {"$ref": "./common/parameters.yaml#/Plain"}}}})}}), "carries 'in'"),
    (lambda: root({"/p": {"post": operation(requestBody={"content": {"application/json": {"schema": {"$ref": "./common/parameters.yaml#/Plain"}}}})}}), "carries 'in'"),
])
def test_invalid_target_for_its_context_fails_closed(api, context_doc, message):
    with pytest.raises(cb.ContractError) as e:
        bundle(api, context_doc())
    assert message in str(e.value) and "REFUSED" in str(e.value)


def test_collisions_are_scoped_to_the_destination_section(api):
    write(api / "foo.yaml", {"type": "object"})  # whole-file schema -> schemas/foo
    write(api / "responses" / "foo.yaml", {"description": "r"})  # would also be named... responses_foo (different)
    write(api / "bar.yaml", {"description": "a response called bar"})
    write(api / "bar.json", {"type": "string"})
    # schemas/bar (from bar.json) and responses/bar (from bar.yaml): different sections, no collision
    doc, _ = bundle(api, root({"/p": {"post": operation(requestBody={"content": {"application/json": {"schema": {"$ref": "./bar.json"}}}},
                                                        responses={"200": {"description": "ok"}, "404": {"$ref": "./bar.yaml"}})}}))
    assert sections(doc) == {"schemas": ["bar"], "responses": ["bar"]}
    # two different SCHEMA targets deriving the same name: fail closed
    with pytest.raises(cb.ContractError) as e:
        bundle(api, root({"/p": {"post": operation(requestBody={"content": {"application/json": {"schema": {"$ref": "./bar.json"}}}},
                                                   responses={"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "./bar.yaml"}}}}})}}))
    assert "derives from both" in str(e.value)
    # authored component in the SAME section collides; in ANOTHER section it does not
    with pytest.raises(cb.ContractError):
        bundle(api, root({"/p": {"post": operation(responses={"200": {"description": "ok"}, "404": {"$ref": "./bar.yaml"}})}}, components={"responses": {"bar": {"description": "x"}}}))
    doc, _ = bundle(api, root({"/p": {"post": operation(responses={"200": {"description": "ok"}, "404": {"$ref": "./bar.yaml"}})}}, components={"schemas": {"bar": {"type": "string"}}}))
    assert sections(doc) == {"schemas": ["bar"], "responses": ["bar"]}


# ── schema policy applies to Schema context only ────────────────────────────────

def test_schema_metadata_policy_applies_only_where_schema_context_begins(api):
    write(api / "resp.yaml", {"R": {"description": "r", "registry": "RESPONSE-LEVEL vendor key stays as authored",
                                   "content": {"application/json": {"schema": {"$schema": "http://json-schema.org/draft-07/schema#", "registry": {"g": 1},
                                                                               "type": "object", "properties": {"registry": {"type": "string"}}}}}}})
    doc, _ = bundle(api, root({"/p": {"get": operation(responses={"200": {"$ref": "./resp.yaml#/R"}})}}))
    resp = doc["components"]["responses"]["resp_R"]
    assert resp["registry"] == "RESPONSE-LEVEL vendor key stays as authored" and "x-registry" not in resp
    schema = resp["content"]["application/json"]["schema"]
    assert schema["x-schema"].startswith("http://json-schema.org") and schema["x-registry"] == {"g": 1} and "$schema" not in schema
    assert schema["properties"]["registry"] == {"type": "string"}  # property NAME untouched


def test_parameter_and_header_schemas_get_the_policy_but_the_parameter_itself_does_not(api):
    write(api / "params.yaml", {"P": {"name": "q", "in": "query", "deprecated": False, "vendor": 1, "schema": {"type": "string", "$comment": "c", "vendor": 2}}})
    doc, _ = bundle(api, root({"/p": {"get": operation(parameters=[{"$ref": "./params.yaml#/P"}])}}))
    p = doc["components"]["parameters"]["params_P"]
    assert p["vendor"] == 1 and p["schema"] == {"type": "string", "x-comment": "c", "x-vendor": 2}


# ── versioning: any reachable component file matters, unreferenced ones do not ───

def test_change_in_an_external_parameter_or_response_file_changes_the_release_comparison(api):
    write(api / "openapi.yaml", root({"/p/{productId}": {"get": operation(parameters=[{"$ref": "./common/parameters.yaml#/ProductId"}],
                                                                              responses={"200": {"description": "ok"}, "404": {"$ref": "./common/responses.yaml#/NotFound"}})}}))
    spec = api / "openapi.yaml"
    base = rv.projection_at(str(spec), "1.0.0", str(api.parent / "w0"))
    doc = yaml.safe_load((api / "common" / "parameters.yaml").read_text(encoding="utf-8"))
    doc["ProductId"]["description"] = "changed"
    write(api / "common" / "parameters.yaml", doc)
    assert rv.projection_at(str(spec), "1.0.0", str(api.parent / "w1")) != base
    write(api / "common" / "unused.yaml", {"X": {"description": "never referenced"}})
    after_unused = rv.projection_at(str(spec), "1.0.0", str(api.parent / "w2"))
    assert after_unused == rv.projection_at(str(spec), "1.0.0", str(api.parent / "w3"))  # unreferenced file: no effect
    rel = rv.write_release(str(api.parent / "build"), {"version": "1.0.0", "decision": "preview"}, str(spec))
    assert rel["contract_files"] == ["common/headers.yaml", "common/parameters.yaml", "common/responses.yaml", "openapi.yaml", "schemas/error.yaml", "schemas/product.yaml"]


# ── generation from the bundled artifact ────────────────────────────────────────

def test_generation_from_the_stamped_bundle_with_external_parameter_and_body(api, tmp_path):
    write(api / "openapi.yaml", root({"/p/{productId}": {"post": operation(parameters=[{"$ref": "./common/parameters.yaml#/ProductId"}],
                                                                               requestBody={"$ref": "./common/request-bodies.yaml#/CreateProduct"})}}))
    out = tmp_path / "build"
    assert rv.main(["--spec", str(api / "openapi.yaml"), "--rest-asset-id", "x", "--org-id", ORG, "--out", str(out), "--dry-run"]) == 0
    assert gm.main(["--spec", str(out / rv.RELEASE_SPEC), "--source-asset-id", "s", "--source-version", "1.0.0", "--asset-id", "m",
                    "--asset-version", "1.0.0", "--org-id", ORG, "--out", str(out)]) == 0
    tool = json.load(open(out / "mcp-metadata.json", encoding="utf-8"))["tools"][0]
    assert tool["inputSchema"]["properties"]["productId"]["properties"] == {"id": {"type": "integer"}}  # parameter schema inlined
    assert tool["inputSchema"]["properties"]["body"]["properties"] == {"id": {"type": "integer"}} and tool["inputSchema"]["required"] == ["productId", "body"]
    assert vm.main(["--metadata", str(out / "mcp-metadata.json"), "--mappings", str(out / "tool-mappings.json")]) == 0


# ── backward compatibility ──────────────────────────────────────────────────────

def test_repository_single_file_contracts_are_still_an_identity():
    from anypoint_fake import PRODUCT_SPEC
    spec = PRODUCT_SPEC  # test-owned single-file contract
    raw = yaml.safe_load(spec.read_text(encoding="utf-8-sig"))
    doc, c = cb.bundle(str(spec))
    assert doc == raw and c["lifted"] == [] and c["files"] == ["openapi.yaml"]


def test_stock_fixture_still_yields_two_tools_and_untouched_sources(tmp_path):
    d = api_dir(tmp_path)
    for f in ("openapi.yaml", "stock-data-api.json"):
        (d / f).write_bytes((FIXTURE / f).read_bytes())
    before = {f: hashlib.sha256((d / f).read_bytes()).hexdigest() for f in ("openapi.yaml", "stock-data-api.json")}
    doc, c = cb.bundle(d / "openapi.yaml")
    assert c["lifted"] == ["schemas/stock-data-api"] and sections(doc)["schemas"] == ["Error", "stock-data-api"]
    out = tmp_path / "build"
    assert rv.main(["--spec", str(d / "openapi.yaml"), "--rest-asset-id", "x", "--org-id", ORG, "--out", str(out), "--dry-run"]) == 0
    assert gm.main(["--spec", str(out / rv.RELEASE_SPEC), "--source-asset-id", "s", "--source-version", "1.0.0", "--asset-id", "m",
                    "--asset-version", "1.0.0", "--org-id", ORG, "--out", str(out)]) == 0
    assert [t["name"] for t in json.load(open(out / "mcp-metadata.json", encoding="utf-8"))["tools"]] == [
        "get_stock_productid_organization_organizationcode", "get_stock_productid"]
    assert {f: hashlib.sha256((d / f).read_bytes()).hexdigest() for f in before} == before
