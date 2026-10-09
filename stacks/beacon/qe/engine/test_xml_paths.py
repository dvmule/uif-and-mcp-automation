# test_xml_paths.py (UIFPE-557)
# The target-path grammar for partner-shaped XML: parsing, the validation
# issues Check/Validate surfaces, the mixed-content conflict rule, the
# nesting plan the job assembles, and the legacy fast path that keeps every
# plain-name config untouched.

import pytest

from dynamic_mapping import xml_paths
from dynamic_mapping.config_loader import parse_config
from dynamic_mapping.config_validator import validate_structure


# ── parsing ──────────────────────────────────────────────────────────────────

def test_plain_name_is_not_a_path():
    assert not xml_paths.is_path("isbn13")
    assert xml_paths.parse_target_path("isbn13") == (("isbn13",), None)


def test_nested_elements_and_attribute():
    assert xml_paths.parse_target_path("Product/Identifiers/ISBN13") == (
        ("Product", "Identifiers", "ISBN13"), None)
    assert xml_paths.parse_target_path("Title@lang") == (("Title",), "lang")
    assert xml_paths.parse_target_path("Product/Price@currency") == (
        ("Product", "Price"), "currency")


@pytest.mark.parametrize("bad, fragment", [
    ("Title@", "followed by an attribute name"),
    ("@lang", "needs an element"),
    ("A//B", "empty path segment"),
    ("/A", "empty path segment"),
    ("A/B/", "empty path segment"),
    ("A@x@y", "at most one '@'"),
    ("A@x/y", "last part"),
    ("A/1bad", "not a valid XML element name"),
    ("A@1bad", "not a valid XML attribute name"),
    ("A B/c", "not a valid XML element name"),
])
def test_bad_paths_are_named(bad, fragment):
    with pytest.raises(ValueError, match=".*"):
        xml_paths.parse_target_path(bad)
    try:
        xml_paths.parse_target_path(bad)
    except ValueError as e:
        assert fragment in str(e)


# ── path_issues ──────────────────────────────────────────────────────────────

def test_plain_only_set_is_the_fast_path():
    assert xml_paths.path_issues(["isbn13", "title", "weird name!"]) == []


def test_syntax_problems_are_reported_per_target():
    issues = xml_paths.path_issues(["ok/fine", "bad//path", "Title@"])
    assert len(issues) == 2
    assert any("bad//path" in i for i in issues)
    assert any("Title@" in i for i in issues)


def test_value_and_wrapper_conflict_is_mixed_content():
    issues = xml_paths.path_issues(["Title", "Title/Sub"])
    assert len(issues) == 1
    assert "'Title'" in issues[0] and "mixed content" in issues[0]


def test_attribute_on_a_value_element_is_fine():
    assert xml_paths.path_issues(["Title", "Title@lang"]) == []


def test_attribute_beside_deeper_children_is_fine():
    assert xml_paths.path_issues(["Product@id", "Product/Title"]) == []


def test_deep_wrapper_conflict_found_at_any_level():
    issues = xml_paths.path_issues(["A/B", "A/B/C"])
    assert len(issues) == 1 and "'A/B'" in issues[0]


# ── build_plan ───────────────────────────────────────────────────────────────

def test_plan_flat_names_stay_leaves():
    plan = xml_paths.build_plan(["isbn13", "title"])
    assert set(plan["children"]) == {"isbn13", "title"}
    assert plan["children"]["isbn13"]["value"] == "isbn13"
    assert plan["children"]["isbn13"]["children"] == {}


def test_plan_nesting_attributes_and_value_tag():
    plan = xml_paths.build_plan(
        ["Identifiers/ISBN13", "Title", "Title@lang", "Price@currency"])
    ident = plan["children"]["Identifiers"]
    assert ident["children"]["ISBN13"]["value"] == "Identifiers/ISBN13"
    title = plan["children"]["Title"]
    assert title["value"] == "Title"                 # element text
    assert title["attrs"] == {"lang": "Title@lang"}  # plus an attribute
    price = plan["children"]["Price"]
    assert price["value"] is None                    # attribute-only element
    assert price["attrs"] == {"currency": "Price@currency"}


def test_plan_shares_wrappers_and_keeps_column_names():
    plan = xml_paths.build_plan(["P/Ids/ISBN13", "P/Ids/ISBN10", "P/Title"])
    p = plan["children"]["P"]
    assert set(p["children"]) == {"Ids", "Title"}
    ids = p["children"]["Ids"]
    assert ids["children"]["ISBN13"]["value"] == "P/Ids/ISBN13"
    assert ids["children"]["ISBN10"]["value"] == "P/Ids/ISBN10"


# ── validate_structure integration ───────────────────────────────────────────

def _config(targets):
    return parse_config({
        "version": "1.0",
        "job_id": "t",
        "sources": [{"name": "s", "path": "s3://b/k", "format": "parquet"}],
        "mappings": [{"type": "one_to_one", "source": "s.c", "target": t}
                     for t in targets],
    })


def _path_messages(issues):
    return [i.message for i in issues
            if "path" in i.message or "mixed content" in i.message
            or "valid XML" in i.message]


def test_validate_structure_names_bad_paths():
    assert any("bad//path" in m for m in _path_messages(
        validate_structure(_config(["ok/fine", "bad//path"]))))


def test_validate_structure_flags_mixed_content():
    assert any("mixed content" in m for m in _path_messages(
        validate_structure(_config(["Title", "Title/Sub"]))))


def test_validate_structure_plain_names_add_no_path_issues():
    assert _path_messages(validate_structure(_config(["isbn13", "title"]))) == []
