# test_row_filter_shapes.py
# The wizard's structured "Filter rows" builder compiles clauses into the
# engine's `filters` block — identifier lists as `alias.col IN (...)`, typed
# attribute conditions (LIKE, BETWEEN, array_contains, size, to_date, IS
# NULL), and backslash-escaped literals. Nothing in the suite exercised those
# shapes before: the validator's filter tests stopped at `a > 0 OR b > 0`.
# This module pins that every shape the builder can emit passes all three
# static layers, that the reference walk reaches inside each node type, that
# a 5,000-identifier list is cheap to validate, and that an author's bad
# quote is an ISSUE rather than an unhandled TokenError (an HTTP 500 before).

import time

import sqlglot
from sqlglot import exp

from dynamic_mapping.config_loader import parse_config
from dynamic_mapping.config_validator import (
    MAX_FILTER_BYTES,
    check_expression,
    collect_references,
    validate_expressions,
    validate_references,
    validate_structure,
)

SCHEMA = {"product": ["ppid", "isbn13", "title", "price", "pubDate", "flag",
                      "programPpid", "n", "author"]}

# one entry per operator row of the builder's catalogue, plus the connectors
BUILDER_SHAPES = [
    "product.ppid IN ('A1', 'A2')",
    "product.isbn13 NOT IN ('9780000000001', '9780000000002')",
    "NOT product.ppid IN ('A1')",
    "product.title = 'BOOK'",
    "product.title <> 'BOOK'",
    "product.title LIKE '%x%'",
    "product.title LIKE 'A%'",
    "product.title LIKE '%\\%%'",           # an escaped LIKE metacharacter
    "product.price BETWEEN 1 AND 2",
    "product.price >= 1.5",
    "array_contains(product.programPpid, 'A1')",
    "size(product.programPpid) > 0",
    "(product.programPpid IS NULL OR size(product.programPpid) = 0)",
    "product.isbn13 IS NULL",
    "product.isbn13 IS NOT NULL",
    "product.pubDate = to_date('2024-01-01')",
    "to_date(product.pubDate) >= to_date('2024-01-01')",
    "product.pubDate BETWEEN to_date('2024-01-01') AND to_date('2024-12-31')",
    "product.pubDate >= to_timestamp('2024-01-01 09:30:00')",
    "product.pubDate >= DATE '2024-01-01'",
    "product.flag = true",
    "product.flag = false",
    "product.n <> 3",
    "product.ppid IN ('A1', 'A2') AND product.title LIKE '%x%'",
]


def _product_config(*filters, joins=(), extra_sources=()):
    return parse_config({
        "version": "1.0",
        "sources": [{"name": "product", "path": "s3://b/p/", "format": "parquet"},
                    *extra_sources],
        "joins": list(joins),
        "mappings": [{"type": "one_to_one", "source": "product.ppid", "target": "ppid"}],
        "filters": list(filters),
        "output": {"targets": [{"format": "parquet", "path": "s3://b/out/"}]},
    })


def _isbn_list(n):
    return "product.isbn13 IN (" + ", ".join(f"'978{i:010d}'" for i in range(n)) + ")"


# ── every builder shape passes all three layers ──────────────────────────────

def test_builder_shapes_pass_check_expression():
    for expr in BUILDER_SHAPES:
        assert check_expression(expr) == [], expr


def test_builder_shapes_pass_every_static_layer():
    cfg = _product_config(*BUILDER_SHAPES)
    assert validate_structure(cfg) == []
    assert validate_expressions(cfg) == []
    assert validate_references(cfg, SCHEMA) == []


def test_columns_inside_in_between_like_and_array_functions_are_checked():
    # the reference walk (find_all(exp.Column)) must reach INSIDE each node
    # type the builder emits — a typo in a list clause is caught at submit
    cfg = _product_config(
        "product.ppidd IN ('A1')",
        "product.pricee BETWEEN 1 AND 2",
        "product.titlee LIKE '%x%'",
        "array_contains(product.programPpidd, 'A1')",
        "size(product.tags) = 0",
    )
    messages = [i.message for i in validate_references(cfg, SCHEMA)]
    for missing in ("ppidd", "pricee", "titlee", "programPpidd", "tags"):
        assert any(f"'{missing}' not found" in m for m in messages), missing
    wheres = {i.where for i in validate_references(cfg, SCHEMA)}
    assert wheres == {f"filters[{i}]" for i in range(5)}


def test_collect_references_keeps_columns_used_only_by_list_and_array_filters():
    cfg = _product_config("product.isbn13 IN ('9780000000001')",
                          "array_contains(product.programPpid, 'A1')")
    refs = collect_references(cfg, SCHEMA)
    assert set(refs["product"]) == {"ppid", "isbn13", "programPpid"}


# ── identifier lists: size and speed ─────────────────────────────────────────

def test_five_thousand_identifier_in_list_validates_clean_and_fast():
    # the builder's per-condition cap; measured ~0.06 s on the pinned
    # sqlglot — the 2 s ceiling is 30x headroom for a cold CI runner
    big = _isbn_list(5000)
    cfg = _product_config(big, "NOT " + big)
    started = time.perf_counter()
    assert check_expression(big) == []
    assert validate_structure(cfg) == []
    assert validate_expressions(cfg) == []
    assert validate_references(cfg, SCHEMA) == []
    assert time.perf_counter() - started < 2.0


def test_filter_over_the_size_cap_is_a_structure_issue():
    cfg = _product_config("x" * (MAX_FILTER_BYTES + 1))
    issues = validate_structure(cfg)
    assert [i.where for i in issues] == ["filters[0]"]
    assert "KB" in issues[0].message
    # a full-size builder list sits well inside the cap
    assert validate_structure(_product_config(_isbn_list(5000))) == []


def test_empty_in_list_is_rejected():
    # sqlglot parses `IN ()`; Spark refuses it at run time — catch it here
    problems = check_expression("product.ppid IN ()")
    assert any("IN list is empty" in p for p in problems)
    assert check_expression("product.ppid IN ('A1')") == []


def test_in_with_a_subquery_is_still_rejected():
    assert any("subquer" in p for p in check_expression("product.ppid IN (SELECT 1)"))


# ── literal escaping: why the builder writes \' and never '' ─────────────────

def test_backslash_escaped_apostrophe_is_one_literal():
    expr = "product.author = 'O\\'Brien'"
    assert check_expression(expr) == []
    literal = sqlglot.parse_one(f"SELECT {expr}", read="spark").find(exp.Literal)
    assert literal.this == "O'Brien"


def test_doubled_apostrophe_is_two_adjacent_literals_pinned():
    # Spark has no SQL-standard doubled-quote escape: 'O''Brien' is the two
    # adjacent literals 'O' and 'Brien', which Spark concatenates to OBrien.
    # Pinned under the pinned sqlglot so the builder's escaping rule keeps
    # its reason in the repo (and an upgrade that changes this fails loudly).
    tree = sqlglot.parse_one("SELECT product.author = 'O''Brien'", read="spark")
    literals = [lit.this for lit in tree.find_all(exp.Literal)]
    assert "O'Brien" not in literals
    assert len(literals) == 2


def test_unbalanced_quote_is_an_issue_not_an_exception():
    for expr in ("product.author = 'O'Brien'", "product.author = 'OBrien"):
        problems = check_expression(expr)
        assert problems and all("not parseable" in p for p in problems), expr
    # the same bad text in a filter AND a join condition must not raise out
    # of any layer — validate_expressions reports it, the others skip it
    bad = "product.author = 'OBrien"
    cfg = _product_config(
        bad,
        extra_sources=[{"name": "price", "path": "s3://b/pr/", "format": "parquet"}],
        joins=[{"left": "product", "right": "price", "type": "left", "condition": bad}],
    )
    schemas = {**SCHEMA, "price": ["ppid"]}
    validate_structure(cfg)
    assert {i.where for i in validate_expressions(cfg)} == {"joins[0].condition", "filters[0]"}
    validate_references(cfg, schemas)
    collect_references(cfg, schemas)


# ── what stays off the allowlist ─────────────────────────────────────────────

def test_rlike_and_lambda_functions_stay_off_the_allowlist():
    assert any("allowlist" in p for p in check_expression("product.title RLIKE '^A'"))
    assert any("allowlist" in p
               for p in check_expression("exists(product.programPpid, x -> x = 'A1')"))
