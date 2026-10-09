"""Multi-file contracts: the authoritative contract is the root openapi.yaml plus every
recursively reachable LOCAL external $ref dependency, and the pipeline consumes ONE generated
self-contained bundle (contract_bundle.py). Domain names here are synthetic; the Stock fixture
is a verbatim copy of a real manually-published asset and is used as EVIDENCE only."""
import copy
import hashlib
import io
import json
import os
import urllib.request
from pathlib import Path

import pytest
import yaml

import affected_domains as ad
import contract_bundle as cb
import contract_gate as cg
import exchange_publish as xp
import generate_mcp_metadata as gm
import oas_projection as op
import release_version as rv
import validate_mcp_metadata as vm

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "multifile-stock"
REPO = HERE.parents[3]
ORG = "8c27297e-4bcf-4c53-99eb-ffd7b1069294"


# ── helpers ────────────────────────────────────────────────────────────────────

def write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".json":
        path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def root_doc(paths=None, components=None):
    doc = {"openapi": "3.1.0", "info": {"title": "t", "version": "1.0.0"}, "paths": paths or {}}
    if components is not None:
        doc["components"] = components
    return doc


def op_post(body_ref, response_ref=None):
    operation = {"operationId": "x", "description": "d",
                 "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": body_ref}}}},
                 "responses": {"200": {"description": "ok"}}}
    if response_ref:
        operation["responses"]["200"]["content"] = {"application/json": {"schema": {"$ref": response_ref}}}
    return {"/p": {"post": operation}}


def api_dir(tmp_path, name="d"):
    d = tmp_path / "domains" / name / "src"  # the contract root = the domain's src/ (containment boundary)
    d.mkdir(parents=True, exist_ok=True)
    return d


def refs_of(doc):
    return sorted(set(cb._refs(doc)))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generate(spec, out):
    return gm.main(["--spec", str(spec), "--source-asset-id", "s", "--source-version", "1.0.0", "--asset-id", "m",
                    "--asset-version", "1.0.0", "--org-id", ORG, "--out", str(out)])


def stock_api(tmp_path):
    d = api_dir(tmp_path, "alpha")
    for f in ("openapi.yaml", "stock-data-api.json"):
        (d / f).write_bytes((FIXTURE / f).read_bytes())
    return d


# ── $ref classification ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ref, kind, file_part, fragment", [
    ("#/components/schemas/Foo", cb.INTERNAL, "", "/components/schemas/Foo"),
    ("#", cb.INTERNAL, "", ""),
    ("./foo.json", cb.LOCAL, "./foo.json", ""),
    ("./foo.json#/definitions/Foo", cb.LOCAL, "./foo.json", "/definitions/Foo"),
    ("./foo.yaml#/components/schemas/Foo", cb.LOCAL, "./foo.yaml", "/components/schemas/Foo"),
    ("schemas/foo.yaml#/Foo", cb.LOCAL, "schemas/foo.yaml", "/Foo"),
    ("../common/bar.json#/Bar", cb.LOCAL, "../common/bar.json", "/Bar"),
    ("/etc/secret.yaml", cb.UNSAFE, "/etc/secret.yaml", ""),
    ("C:\\secret.json", cb.UNSAFE, "C:\\secret.json", ""),
    ("c:/secret.json", cb.UNSAFE, "c:/secret.json", ""),
    ("file:///tmp/schema.json", cb.UNSAFE, "file:///tmp/schema.json", ""),
    ("http://example.com/schema.json", cb.UNSAFE, "http://example.com/schema.json", ""),
    ("https://example.com/schema.json#/Foo", cb.UNSAFE, "https://example.com/schema.json", "/Foo"),
    ("//host/share/x.json", cb.UNSAFE, "//host/share/x.json", ""),
])
def test_classify_ref_separates_kind_file_and_fragment(ref, kind, file_part, fragment):
    assert cb.classify_ref(ref) == (kind, file_part, fragment)


def test_classify_ref_rejects_non_strings():
    with pytest.raises(cb.ContractError):
        cb.classify_ref({"$ref": "x"})


# ── discovery ──────────────────────────────────────────────────────────────────

def test_root_only_contract_discovers_one_file(tmp_path):
    d = api_dir(tmp_path)
    write(d / "openapi.yaml", root_doc(components={"schemas": {"A": {"type": "string"}}}))
    c = cb.discover_contract(d / "openapi.yaml")
    assert c["files"] == ["openapi.yaml"] and c["edges"] == [] and c["root"] == "openapi.yaml"


@pytest.mark.parametrize("dep_name", ["sibling.json", "sibling.yaml"])
def test_sibling_dependency_json_or_yaml(tmp_path, dep_name):
    d = api_dir(tmp_path)
    write(d / dep_name, {"type": "string"})
    write(d / "openapi.yaml", root_doc(op_post("./" + dep_name)))
    c = cb.discover_contract(d / "openapi.yaml")
    assert c["files"] == ["openapi.yaml", dep_name] and c["edges"] == [("openapi.yaml", "./" + dep_name, dep_name)]


def test_nested_and_recursive_dependencies_resolve_relative_to_the_referring_document(tmp_path):
    d = api_dir(tmp_path)
    write(d / "schemas" / "product.yaml", {"components": {"schemas": {"Product": {"type": "object", "properties": {"addr": {"$ref": "../common/address.yaml#/Address"}}}}}})
    write(d / "common" / "address.yaml", {"Address": {"type": "object", "properties": {"zip": {"type": "string"}}}})
    write(d / "openapi.yaml", root_doc(op_post("./schemas/product.yaml#/components/schemas/Product")))
    c = cb.discover_contract(d / "openapi.yaml")
    assert c["files"] == ["common/address.yaml", "openapi.yaml", "schemas/product.yaml"]
    assert ("schemas/product.yaml", "../common/address.yaml#/Address", "common/address.yaml") in c["edges"]


def test_dependency_of_a_dependency_is_resolved_against_that_file_not_the_root(tmp_path):
    """Two files called second.yaml: the one BESIDE the referring file wins."""
    d = api_dir(tmp_path)
    write(d / "second.yaml", {"type": "string", "description": "ROOT LEVEL - must not be used"})
    write(d / "a" / "second.yaml", {"type": "integer", "description": "beside first.yaml"})
    write(d / "a" / "first.yaml", {"type": "object", "properties": {"n": {"$ref": "./second.yaml"}}})
    write(d / "openapi.yaml", root_doc(op_post("./a/first.yaml")))
    doc, c = cb.bundle(d / "openapi.yaml")
    assert c["files"] == ["a/first.yaml", "a/second.yaml", "openapi.yaml"]
    assert doc["components"]["schemas"]["a_second"]["description"] == "beside first.yaml"


def test_missing_dependency_fails_closed_with_a_useful_message(tmp_path):
    d = api_dir(tmp_path)
    write(d / "openapi.yaml", root_doc(op_post("./schemas/missing.json")))
    with pytest.raises(cb.MissingDependency) as e:
        cb.discover_contract(d / "openapi.yaml")
    assert "./schemas/missing.json" in str(e.value) and "does not exist" in str(e.value) and "openapi.yaml" not in str(e.value).split("referenced from")[0][-5:]
    assert isinstance(e.value, FileNotFoundError) and isinstance(e.value, cb.ContractError)


def test_unreferenced_file_under_src_api_is_not_part_of_the_contract(tmp_path):
    d = api_dir(tmp_path)
    write(d / "used.json", {"type": "string"})
    write(d / "unrelated.json", {"type": "integer"})
    write(d / "notes" / "draft.yaml", {"type": "boolean"})
    write(d / "openapi.yaml", root_doc(op_post("./used.json")))
    assert cb.discover_contract(d / "openapi.yaml")["files"] == ["openapi.yaml", "used.json"]


# ── fragments ──────────────────────────────────────────────────────────────────

def test_whole_file_fragment_and_same_document_pointers_are_not_conflated(tmp_path):
    d = api_dir(tmp_path)
    write(d / "foo.json", {"definitions": {"Foo": {"type": "object", "properties": {"n": {"$ref": "#/definitions/N"}}}, "N": {"type": "integer"}},
                           "type": "object", "properties": {"whole": {"type": "string"}}})
    paths = op_post("./foo.json#/definitions/Foo", response_ref="./foo.json")
    paths["/p"]["post"]["parameters"] = [{"name": "q", "in": "query", "schema": {"$ref": "#/components/schemas/Local"}}]
    write(d / "openapi.yaml", root_doc(paths, components={"schemas": {"Local": {"type": "string"}}}))
    doc, c = cb.bundle(d / "openapi.yaml")
    schemas = doc["components"]["schemas"]
    assert set(schemas) == {"Local", "foo_definitions_Foo", "foo_definitions_N", "foo"}
    body = doc["paths"]["/p"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/foo_definitions_Foo"}
    assert doc["paths"]["/p"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/foo"}
    assert doc["paths"]["/p"]["post"]["parameters"][0]["schema"] == {"$ref": "#/components/schemas/Local"}  # same-document ref untouched
    assert schemas["foo_definitions_Foo"]["properties"]["n"] == {"$ref": "#/components/schemas/foo_definitions_N"}  # internal pointer INSIDE the dependency
    assert "definitions" not in schemas["foo"] and schemas["foo"]["properties"] == {"whole": {"type": "string"}}  # container never emitted
    assert cb.external_refs(doc) == [] and c["lifted"] == ["schemas/foo_definitions_Foo", "schemas/foo_definitions_N", "schemas/foo"]


def test_fragment_that_does_not_exist_fails_closed(tmp_path):
    d = api_dir(tmp_path)
    write(d / "foo.json", {"definitions": {"Foo": {"type": "string"}}})
    write(d / "openapi.yaml", root_doc(op_post("./foo.json#/definitions/Bar")))
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    assert "fragment" in str(e.value) and "Bar" in str(e.value)


def test_non_schema_targets_are_refused(tmp_path):
    d = api_dir(tmp_path)
    write(d / "params.yaml", {"P": {"name": "q", "in": "query", "schema": {"type": "string"}}})
    write(d / "openapi.yaml", root_doc(op_post("./params.yaml#/P")))
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    assert "Schema Object" in str(e.value)


# ── cycles ─────────────────────────────────────────────────────────────────────

def test_file_cycle_is_bundled_once_per_target(tmp_path):
    d = api_dir(tmp_path)
    write(d / "a.yaml", {"A": {"type": "object", "properties": {"b": {"$ref": "./b.yaml#/B"}}}})
    write(d / "b.yaml", {"B": {"type": "object", "properties": {"a": {"$ref": "./a.yaml#/A"}}}})
    write(d / "openapi.yaml", root_doc(components={"schemas": {"X": {"$ref": "./a.yaml#/A"}}}))
    doc, c = cb.bundle(d / "openapi.yaml")
    assert sorted(doc["components"]["schemas"]) == ["X", "a_A", "b_B"]
    assert doc["components"]["schemas"]["a_A"]["properties"]["b"] == {"$ref": "#/components/schemas/b_B"}
    assert doc["components"]["schemas"]["b_B"]["properties"]["a"] == {"$ref": "#/components/schemas/a_A"}
    assert c["files"] == ["a.yaml", "b.yaml", "openapi.yaml"]


def test_schema_recursion_is_preserved_and_never_mistaken_for_a_file_cycle(tmp_path):
    d = api_dir(tmp_path)
    write(d / "tree.yaml", {"Node": {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#/Node"}}}}})
    write(d / "openapi.yaml", root_doc(components={"schemas": {
        "Root": {"$ref": "./tree.yaml#/Node"},
        "Self": {"type": "object", "properties": {"me": {"$ref": "#/components/schemas/Self"}}}}}))
    doc, c = cb.bundle(d / "openapi.yaml")
    node = doc["components"]["schemas"]["tree_Node"]
    assert node["properties"]["children"]["items"] == {"$ref": "#/components/schemas/tree_Node"}  # recursion kept, lifted once
    assert doc["components"]["schemas"]["Self"]["properties"]["me"] == {"$ref": "#/components/schemas/Self"}  # root recursion untouched
    assert c["files"] == ["openapi.yaml", "tree.yaml"]
    op.project_oas31_to_30(doc)  # recursive internal refs are fine for the projection


# ── security / containment ─────────────────────────────────────────────────────

def test_safe_parent_traversal_that_stays_under_src_api_is_allowed(tmp_path):
    d = api_dir(tmp_path)
    write(d / "schemas" / "product.yaml", {"Product": {"type": "object", "properties": {"addr": {"$ref": "../common/address.yaml#/Address"}}}})
    write(d / "common" / "address.yaml", {"Address": {"type": "string"}})
    write(d / "openapi.yaml", root_doc(op_post("./schemas/product.yaml#/Product")))
    doc, _ = cb.bundle(d / "openapi.yaml")
    assert "common_address_Address" in doc["components"]["schemas"]


@pytest.mark.parametrize("bad", [
    "../../../../outside/secret.json", "../../../secret.json", "/etc/secret.yaml", "C:\\secret.json", "D:/secret.json",
    "file:///tmp/schema.json", "http://example.com/schema.json", "https://example.com/schema.json", "./missing.json",
])
def test_unsafe_escaping_or_missing_references_are_refused_by_discovery_bundle_and_loader(tmp_path, bad):
    d = api_dir(tmp_path)
    write(tmp_path / "outside" / "secret.json", {"type": "integer", "description": "OUTSIDE"})
    write(tmp_path / "domains" / "secret.json", {"type": "integer"})
    write(d / "openapi.yaml", root_doc(op_post(bad)))
    for fn in (lambda: cb.discover_contract(d / "openapi.yaml"), lambda: cb.bundle(d / "openapi.yaml")):
        with pytest.raises(cb.ContractError) as e:
            fn()
        assert "REFUSED" in str(e.value)
    with pytest.raises((cb.ContractError, FileNotFoundError)):
        gm.SpecLoader(str(d / "openapi.yaml")).resolve({"$ref": bad})
    assert "OUTSIDE" not in json.dumps(cg.check_projection(str(d / "openapi.yaml"))[0])
    assert cg.check_projection(str(d / "openapi.yaml"))[0][0].startswith("contract bundle fails closed")


def test_escape_message_names_the_boundary(tmp_path):
    d = api_dir(tmp_path)
    write(tmp_path / "outside" / "secret.json", {"type": "integer"})
    write(d / "openapi.yaml", root_doc(op_post("../../../../outside/secret.json")))
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    assert "outside the contract root" in str(e.value) and "src/ directory" in str(e.value)


def test_symlink_escape_is_refused_where_the_runtime_allows_symlinks(tmp_path):
    d = api_dir(tmp_path)
    write(tmp_path / "outside" / "secret.json", {"type": "integer", "description": "OUTSIDE"})
    try:
        os.symlink(str(tmp_path / "outside" / "secret.json"), str(d / "link.json"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not creatable on this runtime")
    write(d / "openapi.yaml", root_doc(op_post("./link.json")))
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    assert "outside the contract root" in str(e.value)


def test_containment_uses_canonical_paths_not_string_prefixes(tmp_path):
    """src-evil is a sibling directory whose name merely starts with the boundary name (src)."""
    d = api_dir(tmp_path)
    evil = d.parent / "src-evil"
    write(evil / "x.json", {"type": "integer", "description": "SIBLING DIR"})
    write(d / "openapi.yaml", root_doc(op_post("../src-evil/x.json")))
    with pytest.raises(cb.ContractError):
        cb.bundle(d / "openapi.yaml")
    assert not cb.inside(evil / "x.json", d) and cb.inside(d / "sub" / "x.json", d)


# ── $id policy ─────────────────────────────────────────────────────────────────

def test_dollar_id_without_relative_external_refs_is_accepted_and_relocated(tmp_path):
    d = api_dir(tmp_path)
    write(d / "s.json", {"$id": "https://schemas.example.com/s.json", "type": "object", "properties": {"n": {"$ref": "#/definitions/N"}},
                         "definitions": {"N": {"type": "integer"}}})
    write(d / "openapi.yaml", root_doc(op_post("./s.json")))
    doc, _ = cb.bundle(d / "openapi.yaml")
    s = doc["components"]["schemas"]["s"]
    assert s["x-id"] == "https://schemas.example.com/s.json" and "$id" not in s


def test_dollar_id_with_relative_external_refs_fails_closed_with_a_clear_diagnostic(tmp_path):
    d = api_dir(tmp_path)
    write(d / "withid.json", {"$id": "https://schemas.example.com/base/withid.json", "type": "object", "properties": {"o": {"$ref": "other.json"}}})
    write(d / "other.json", {"type": "boolean"})
    write(d / "openapi.yaml", root_doc(op_post("./withid.json")))
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    msg = str(e.value)
    assert "$id" in msg and "withid.json" in msg and "supported subset" in msg and "base URI" in msg
    # a NESTED $id counts too (embedded resources change the base as well)
    write(d / "withid.json", {"type": "object", "properties": {"o": {"$id": "https://x/y", "type": "object", "properties": {"p": {"$ref": "other.json"}}}}})
    with pytest.raises(cb.ContractError):
        cb.bundle(d / "openapi.yaml")


# ── generated artifact ─────────────────────────────────────────────────────────

def test_source_files_are_never_modified_and_output_is_deterministic(tmp_path):
    d = api_dir(tmp_path)
    write(d / "schemas" / "p.json", {"type": "object", "properties": {"a": {"$ref": "../common/e.yaml#/E"}}, "registry": {"g": 1}})
    write(d / "common" / "e.yaml", {"E": {"type": "string"}})
    write(d / "openapi.yaml", root_doc(op_post("./schemas/p.json")))
    before = {p: sha(p) for p in d.rglob("*") if p.is_file()}
    doc1, c1 = cb.bundle(d / "openapi.yaml")
    doc2, c2 = cb.bundle(d / "openapi.yaml")
    assert {p: sha(p) for p in d.rglob("*") if p.is_file()} == before
    assert doc1 == doc2 and c1 == c2 and op.render_yaml(doc1) == op.render_yaml(doc2)
    assert cb.external_refs(doc1) == [] and list(doc1["components"]["schemas"]) == ["schemas_p", "common_e_E"]


@pytest.mark.parametrize("rel, frag, name", [
    ("foo.json", "", "foo"), ("stock-data-api.json", "", "stock-data-api"), ("foo.json", "/definitions/Foo", "foo_definitions_Foo"),
    ("schemas/product.yaml", "/components/schemas/Product", "schemas_product_components_schemas_Product"),
    ("common/address.yaml", "/Address", "common_address_Address"), ("a/b/c.v2.yaml", "/X~1Y", "a_b_c.v2_X_Y"),
])
def test_lifted_component_names_are_deterministic_and_valid_component_keys(rel, frag, name):
    assert cb.component_name(rel, frag) == name and cb._COMPONENT_KEY.search(name) is None


def test_collision_with_an_authored_component_fails_closed(tmp_path):
    d = api_dir(tmp_path)
    write(d / "foo.json", {"type": "string"})
    write(d / "openapi.yaml", root_doc(op_post("./foo.json"), components={"schemas": {"foo": {"type": "integer"}}}))
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    assert "collides with an authored" in str(e.value) and "'foo'" in str(e.value)


def test_two_targets_deriving_the_same_name_fail_closed(tmp_path):
    d = api_dir(tmp_path)
    write(d / "foo.json", {"type": "string"})
    write(d / "foo.yaml", {"type": "integer"})
    write(d / "openapi.yaml", root_doc(op_post("./foo.json", response_ref="./foo.yaml")))
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    assert "derives from both" in str(e.value) and "rename" in str(e.value)


def test_metadata_is_relocated_generically_to_x_extensions_without_touching_property_names(tmp_path):
    d = api_dir(tmp_path)
    write(d / "s.json", {
        "$schema": "http://json-schema.org/draft-07/schema#", "$id": "https://x/s.json", "$comment": "generated",
        "registry": {"group": "g"}, "metadata": {"tier": "T2"}, "anything-else": 1, "title": "S", "type": "object",
        "additionalProperties": False, "required": ["registry"],
        "properties": {
            "registry": {"type": "string", "description": "a PROPERTY named registry"},   # property name, never a keyword
            "metadata": {"type": "object", "properties": {"registry": {"type": "string"}}},  # nested property named like a vendor key
            "n": {"type": ["integer", "null"], "vendorKey": True},                            # unknown key INSIDE a property schema
        },
        "x-already": "kept"})
    write(d / "openapi.yaml", root_doc(op_post("./s.json")))
    doc, _ = cb.bundle(d / "openapi.yaml")
    s = doc["components"]["schemas"]["s"]
    assert {k: s[k] for k in ("x-schema", "x-id", "x-comment", "x-registry", "x-metadata", "x-anything-else", "x-already")} == {
        "x-schema": "http://json-schema.org/draft-07/schema#", "x-id": "https://x/s.json", "x-comment": "generated",
        "x-registry": {"group": "g"}, "x-metadata": {"tier": "T2"}, "x-anything-else": 1, "x-already": "kept"}
    assert not ({"$schema", "$id", "$comment", "registry", "metadata", "anything-else"} & set(s))
    assert s["required"] == ["registry"] and s["additionalProperties"] is False and s["title"] == "S"
    assert s["properties"]["registry"] == {"type": "string", "description": "a PROPERTY named registry"}
    assert s["properties"]["metadata"]["properties"] == {"registry": {"type": "string"}}
    assert s["properties"]["n"] == {"type": ["integer", "null"], "x-vendorKey": True}
    projected, stats = op.project_oas31_to_30(doc)  # the real projection accepts the result
    assert stats["type_arrays_converted"] == 1 and projected["components"]["schemas"]["s"]["properties"]["n"]["nullable"] is True


def test_keywords_that_change_reference_semantics_and_extension_clashes_fail_closed(tmp_path):
    d = api_dir(tmp_path)
    write(d / "openapi.yaml", root_doc(op_post("./s.json")))
    write(d / "s.json", {"type": "object", "$anchor": "here"})
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    assert "$anchor" in str(e.value) and "reference semantics" in str(e.value)
    write(d / "s.json", {"type": "object", "registry": 1, "x-registry": 2})
    with pytest.raises(cb.ContractError) as e:
        cb.bundle(d / "openapi.yaml")
    assert "x-registry" in str(e.value) and "already exists" in str(e.value)


def test_validation_keywords_are_kept_verbatim_so_the_projection_still_decides(tmp_path):
    """`const` is 3.1-only: the bundle must NOT hide it behind an x- key - the projection refuses it as today."""
    d = api_dir(tmp_path)
    write(d / "s.json", {"type": "string", "const": "fixed"})
    write(d / "openapi.yaml", root_doc(op_post("./s.json")))
    doc, _ = cb.bundle(d / "openapi.yaml")
    assert doc["components"]["schemas"]["s"] == {"type": "string", "const": "fixed"}
    with pytest.raises(op.ProjectionError):
        op.project_oas31_to_30(doc)


def test_projection_safety_net_refuses_any_external_ref_that_reaches_it():
    doc = root_doc(op_post("./schemas/foo.json"))
    with pytest.raises(op.ProjectionError) as e:
        op.project_oas31_to_30(doc)
    assert "external reference" in str(e.value) and "bundled first" in str(e.value)
    op.project_oas31_to_30(root_doc(components={"schemas": {"A": {"$ref": "#/components/schemas/B"}, "B": {"type": "string"}}}))


# ── release versioning ─────────────────────────────────────────────────────────

def _release_world(tmp_path):
    d = api_dir(tmp_path)
    write(d / "schemas" / "product.json", {"type": "object", "properties": {"qty": {"type": "integer"}}})
    write(d / "unrelated.json", {"type": "string"})
    write(d / "openapi.yaml", root_doc(op_post("./schemas/product.json")))
    return d


def test_dependency_only_change_changes_the_release_comparison(tmp_path):
    d = _release_world(tmp_path)
    before = rv.projection_at(str(d / "openapi.yaml"), "1.0.0", str(tmp_path / "w1"))
    write(d / "schemas" / "product.json", {"type": "object", "properties": {"qty": {"type": "number"}}})
    after = rv.projection_at(str(d / "openapi.yaml"), "1.0.0", str(tmp_path / "w2"))
    assert before != after and rv.contracts_equal(before, after) == (False, "different")
    assert b"qty" in before and b"./schemas/product.json" not in before


def test_unreferenced_file_change_keeps_the_comparison_identical(tmp_path):
    d = _release_world(tmp_path)
    before = rv.projection_at(str(d / "openapi.yaml"), "1.0.0", str(tmp_path / "w1"))
    write(d / "unrelated.json", {"type": "boolean", "description": "changed"})
    write(d / "brand-new.yaml", {"type": "string"})
    after = rv.projection_at(str(d / "openapi.yaml"), "1.0.0", str(tmp_path / "w2"))
    assert before == after and rv.contracts_equal(before, after) == (True, "byte-identical")


def test_root_change_still_changes_the_comparison(tmp_path):
    d = _release_world(tmp_path)
    before = rv.projection_at(str(d / "openapi.yaml"), "1.0.0", str(tmp_path / "w1"))
    doc = yaml.safe_load((d / "openapi.yaml").read_text(encoding="utf-8"))
    doc["paths"]["/p"]["post"]["description"] = "changed"
    write(d / "openapi.yaml", doc)
    assert before != rv.projection_at(str(d / "openapi.yaml"), "1.0.0", str(tmp_path / "w2"))


def test_release_writes_a_self_contained_stamped_copy_and_records_the_contract_files(tmp_path, capsys):
    d = _release_world(tmp_path)
    out = tmp_path / "build"
    assert rv.main(["--spec", str(d / "openapi.yaml"), "--rest-asset-id", "uif-d-api", "--org-id", ORG, "--out", str(out), "--dry-run"]) == 0
    rel = json.loads((out / rv.RELEASE_FILE).read_text(encoding="utf-8"))
    assert rel["contract_files"] == ["openapi.yaml", "schemas/product.json"] and rel["decision"] == "preview"
    stamped = yaml.safe_load((out / rv.RELEASE_SPEC).read_text(encoding="utf-8"))
    assert cb.external_refs(stamped) == [] and "schemas_product" in stamped["components"]["schemas"] and stamped["info"]["version"] == "1.0.0"
    assert not (out / "schemas").exists()  # nothing to mirror: the copy is self-contained
    assert "2 file(s) bundled" in capsys.readouterr().out


def test_release_fails_closed_before_writing_when_a_dependency_is_missing(tmp_path):
    d = api_dir(tmp_path)
    write(d / "openapi.yaml", root_doc(op_post("./gone.json")))
    with pytest.raises(SystemExit) as e:
        rv.main(["--spec", str(d / "openapi.yaml"), "--rest-asset-id", "x", "--org-id", ORG, "--out", str(tmp_path / "b"), "--dry-run"])
    assert "FAIL CLOSED (contract bundle)" in str(e.value) and "gone.json" in str(e.value)
    assert not (tmp_path / "b" / rv.RELEASE_FILE).exists()


# ── contract gate ──────────────────────────────────────────────────────────────

def test_gate_fails_on_a_missing_dependency_even_when_generation_succeeds(tmp_path):
    """Response-only external ref (the real-world shape): generation never touches it, the gate must."""
    d = api_dir(tmp_path)
    write(d / "openapi.yaml", root_doc({"/p": {"get": {"description": "d", "responses": {"200": {"description": "ok", "content": {
        "application/json": {"schema": {"$ref": "./resp.json"}}}}}}}}))
    assert generate(d / "openapi.yaml", tmp_path / "gen") == 0
    assert cg.main(["--spec", str(d / "openapi.yaml"), "--metadata", str(tmp_path / "gen" / "mcp-metadata.json"),
                    "--mappings", str(tmp_path / "gen" / "tool-mappings.json")]) == 1
    problems, stats = cg.check_projection(str(d / "openapi.yaml"))
    assert stats is None and "resp.json" in problems[0] and "does not exist" in problems[0]


def test_gate_passes_for_a_complete_multi_file_contract(tmp_path):
    d = api_dir(tmp_path)
    write(d / "resp.json", {"type": "object", "properties": {"n": {"type": ["integer", "null"]}}})
    write(d / "openapi.yaml", root_doc({"/p": {"get": {"description": "d", "responses": {"200": {"description": "ok", "content": {
        "application/json": {"schema": {"$ref": "./resp.json"}}}}}}}}))
    assert generate(d / "openapi.yaml", tmp_path / "gen") == 0
    assert cg.main(["--spec", str(d / "openapi.yaml"), "--metadata", str(tmp_path / "gen" / "mcp-metadata.json"),
                    "--mappings", str(tmp_path / "gen" / "tool-mappings.json")]) == 0
    problems, stats = cg.check_projection(str(d / "openapi.yaml"))
    assert problems == [] and stats["type_arrays_converted"] == 1


# ── generation ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body_ref, files", [
    ("./schemas/product.json", {"schemas/product.json": {"type": "object", "properties": {"qty": {"type": "integer"}}}}),
    ("./foo.json#/definitions/Foo", {"foo.json": {"definitions": {"Foo": {"type": "object", "properties": {"qty": {"type": "integer"}}}}}}),
    ("./schemas/product.yaml#/Product", {"schemas/product.yaml": {"Product": {"type": "object", "properties": {"qty": {"$ref": "../common/q.yaml#/Q"}}}},
                                         "common/q.yaml": {"Q": {"type": "integer"}}}),
])
def test_request_body_external_schemas_generate_from_the_self_contained_stamped_copy(tmp_path, body_ref, files):
    d = api_dir(tmp_path)
    for rel, doc in files.items():
        write(d / rel, doc)
    write(d / "openapi.yaml", root_doc(op_post(body_ref)))
    out = tmp_path / "build"
    assert rv.main(["--spec", str(d / "openapi.yaml"), "--rest-asset-id", "x", "--org-id", ORG, "--out", str(out), "--dry-run"]) == 0
    stamped = out / rv.RELEASE_SPEC
    assert generate(stamped, out) == 0  # from build/, where NO dependency file exists
    body = json.load(open(out / "mcp-metadata.json", encoding="utf-8"))["tools"][0]["inputSchema"]["properties"]["body"]
    assert body["properties"]["qty"] == {"type": "integer"} and "$ref" not in json.dumps(body)
    assert vm.main(["--metadata", str(out / "mcp-metadata.json"), "--mappings", str(out / "tool-mappings.json")]) == 0
    assert cg.main(["--spec", str(stamped), "--metadata", str(out / "mcp-metadata.json"), "--mappings", str(out / "tool-mappings.json")]) == 0
    assert generate(d / "openapi.yaml", tmp_path / "raw") == 0  # the raw multi-file contract still generates directly (gate job)


# ── backward compatibility: the repository's single-file contracts ─────────────

def test_single_file_contracts_bundle_to_themselves_byte_identically(tmp_path):
    from anypoint_fake import PRODUCT_SPEC
    spec = PRODUCT_SPEC  # test-owned single-file contract
    raw = yaml.safe_load(spec.read_text(encoding="utf-8-sig"))
    doc, c = cb.bundle(str(spec))
    assert doc == raw and c["files"] == ["openapi.yaml"] and c["lifted"] == []
    assert op.render_yaml(op.project_oas31_to_30(doc)[0]) == op.render_yaml(op.project_oas31_to_30(raw)[0])
    # the stamped copy is byte-identical to the pre-bundling stamping (plain load -> stamp -> dump)
    legacy = copy.deepcopy(raw)
    legacy["info"]["version"] = "9.9.9"
    legacy_bytes = yaml.safe_dump(legacy, sort_keys=False, allow_unicode=True, default_flow_style=False).encode("utf-8")
    stamped, original = rv.stamp_spec(str(spec), "9.9.9", str(tmp_path / "s.yaml"))
    assert Path(stamped).read_bytes() == legacy_bytes and original == str(raw["info"]["version"])
    assert rv.projection_at(str(spec), "9.9.9", str(tmp_path / "w")) == xp.build_rest_projection(stamped, "9.9.9")[0]


# ── workflow / detection ───────────────────────────────────────────────────────

def test_workflow_wakes_for_any_src_api_change_and_detection_stays_generic():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "mcp.yml").read_text(encoding="utf-8"))
    paths = wf[True]["push"]["paths"] if True in wf else wf["on"]["push"]["paths"]
    assert "domains/*/src/api/**" in paths and "domains/*/src/api/openapi.yaml" not in paths
    assert wf["jobs"]["domain"]["strategy"]["matrix"]["domain"] == "${{ fromJson(needs.detect.outputs.domains) }}"
    assert ad.affected(["domains/zeta/src/api/schemas/product.json"], ["zeta"]) == ["zeta"]
    assert ad.affected(["domains/zeta/src/api/common/address.yaml"], ["zeta", "eta"]) == ["zeta"]


# ── Stock fixture: the real multi-file asset, end to end, offline ───────────────

def test_stock_fixture_end_to_end_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("NETWORK")))
    d = stock_api(tmp_path)
    before = {p.name: sha(p) for p in d.iterdir()}
    assert before == {f: sha(FIXTURE / f) for f in ("openapi.yaml", "stock-data-api.json")}
    # discovery + bundle
    c = cb.discover_contract(d / "openapi.yaml")
    assert c["files"] == ["openapi.yaml", "stock-data-api.json"] and len(c["edges"]) == 2
    doc, _ = cb.bundle(d / "openapi.yaml")
    assert cb.external_refs(doc) == []
    lifted = doc["components"]["schemas"]["stock-data-api"]
    assert {"x-schema", "x-id", "x-comment", "x-registry", "x-metadata"} <= set(lifted)
    assert not ({"$schema", "$id", "$comment", "registry", "metadata"} & set(lifted))
    assert lifted["x-registry"]["artifact"] == "stock.stock.api" and lifted["required"][0] == "productId"
    assert doc["paths"]["/stock/{productId}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["items"] == {"$ref": "#/components/schemas/stock-data-api"}
    # stamp + release comparison (dry-run) + projection
    out = tmp_path / "build"
    assert rv.main(["--spec", str(d / "openapi.yaml"), "--rest-asset-id", "uif-alpha-api", "--org-id", ORG, "--out", str(out), "--dry-run"]) == 0
    rel = json.loads((out / rv.RELEASE_FILE).read_text(encoding="utf-8"))
    assert rel["contract_files"] == ["openapi.yaml", "stock-data-api.json"]
    stamped = out / rv.RELEASE_SPEC
    projected, stats = op.project_oas31_to_30(yaml.safe_load(stamped.read_text(encoding="utf-8")))
    assert stats["type_arrays_converted"] == 3 and stats["operations"] == 2
    ps = projected["components"]["schemas"]["stock-data-api"]["properties"]
    assert ps["isbn13"] == {"type": "string", "nullable": True, "description": ps["isbn13"]["description"]}
    # MCP generation + validation + gate from the stamped copy
    assert generate(stamped, out) == 0
    tools = [t["name"] for t in json.load(open(out / "mcp-metadata.json", encoding="utf-8"))["tools"]]
    assert tools == ["get_stock_productid_organization_organizationcode", "get_stock_productid"]
    assert vm.main(["--metadata", str(out / "mcp-metadata.json"), "--mappings", str(out / "tool-mappings.json")]) == 0
    assert cg.main(["--spec", str(stamped), "--metadata", str(out / "mcp-metadata.json"), "--mappings", str(out / "tool-mappings.json")]) == 0
    assert cg.check_projection(str(d / "openapi.yaml"))[0] == []  # the gate on the RAW multi-file contract passes too
    # Exchange publication DRY-RUN only: single-file request, no external ref in the artifact
    assert xp.main(["rest", "--spec", str(stamped), "--out", str(out), "--org-id", ORG, "--asset-id", "uif-alpha-api", "--asset-version", "1.0.0", "--dry-run"]) == 0
    artifact = (out / "openapi-3.0-projection.yaml").read_text(encoding="utf-8")
    assert "./stock-data-api.json" not in artifact and "x-registry" in artifact and "nullable: true" in artifact
    log = capsys.readouterr().out
    assert "-F files.oas.yaml=@" in log and "DRY-RUN: nothing sent" in log
    # sources untouched, bundle only under the build area
    assert {p.name: sha(p) for p in d.iterdir()} == before
    assert not any(p.name == rv.RELEASE_SPEC for p in d.rglob("*"))
