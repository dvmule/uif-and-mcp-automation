"""Every registry coordinate under `domains/` conforms to ADR-0023, or is
explicitly out of scope.

Three subject conventions grew in this repository because nothing compared
them — the same root cause ADR-0014 recorded for the naming-rule drift
("Drift was possible because nothing tied the rules to the hook, and nothing
ran a check on either"). This is that check.

It is the STRICT half of a deliberate split. The server rule in
`metadata_validator.py` can only judge a subject whose type slot is one of the
five tokens, because the live registry holds artifacts governed by other
standards and flagging those would make the compliance verdict lie. This test
knows the full allowlist, so it can afford to be strict — and a typo'd type
token, which the server deliberately lets past, fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    # A hard failure, never a skip. A coordinate can be declared as a YAML
    # `x-registry` block, and four are; skipping would silently drop exactly
    # the coverage whose absence let them go unnoticed in the first place.
    raise RuntimeError(
        "PyYAML is required: four coordinates are declared as YAML x-registry "
        "blocks, and a run that cannot read them is not a regrowth check."
    ) from None

STANDARDS = Path(__file__).resolve().parent
REPO = STANDARDS.parents[2]
DOMAINS = REPO / "domains"

_CASES = json.loads((STANDARDS / "subject-naming.cases.json").read_text(encoding="utf-8"))
SUBJECT = re.compile(_CASES["pattern"])
TYPES = tuple(_CASES["types"])

#: The normative Pearson form for a per-operation event payload,
#: `<entity>-<action>-data`. These are deliberately NOT ADR-0023 subjects and
#: sit on OUT_OF_SCOPE below — but only their NAMING is out of scope. The
#: normative standard fixes their compatibility mode and their D21 tier just as
#: firmly as it fixes a gold coordinate's, and nothing in this repository
#: checked that: all eight were registered-in-waiting on plain `BACKWARD` with
#: no tier at all, which the step-2 lint fails outright ("no tier declared on
#: the artifact at registration"). Unlike the five ADR-0023 types, there is no
#: question that D21 governs these — they ARE the `data` schema artifacts it
#: names.
DATA_SCHEMA = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+-data$")

#: Coordinates governed by something other than ADR-0023. Each is listed with
#: what governs it, so an addition is a decision a reviewer can weigh rather
#: than a line that makes a failure go away.
OUT_OF_SCOPE: dict[str, str] = {
    # Per-operation event payloads — normative `<entity>-<action>-data`.
    "price:price-created-data": "normative per-operation data schema",
    "price:price-updated-data": "normative per-operation data schema",
    "product:product-created-data": "normative per-operation data schema",
    "product:product-updated-data": "normative per-operation data schema",
    "program:program-created-data": "normative per-operation data schema",
    "program:program-updated-data": "normative per-operation data schema",
    "stock:stock-created-data": "normative per-operation data schema",
    "stock:stock-updated-data": "normative per-operation data schema",
    # AsyncAPI document, on the normative `<domain>-events-asyncapi` form. Still
    # out of scope for ADR-0023 — an AsyncAPI document is not one of the five
    # types and does not use the dotted grammar — but it is no longer a
    # divergence: ADR-0023 recorded the old `product-events` as an open item,
    # and conforming it was free because nothing had registered it yet.
    "product:product-events-asyncapi": "AsyncAPI document, normative <domain>-events-asyncapi",
    # OpenAPI/REST contracts — capabilities/api/guidelines.md §1.
    "product:product-api": "OpenAPI contract, <domain>-<resource>-api",
    "product:marketing-copy-api": "OpenAPI contract, <domain>-<resource>-api",
    "stock:stock-api": "OpenAPI contract, <domain>-<resource>-api",
    "price:price-api": "OpenAPI contract, <domain>-<resource>-api",
    "program:program-api": "OpenAPI contract, <domain>-<resource>-api",
    # Governed by no standard at all, including this one. Their naming is the
    # owning capability's decision.
    "product:product-master-data-contract": "data contract; no standard names it",
    "hr:employee-classification": "field-classification policy; no standard names it",
    "hr:a2a-routing": "A2A capability artifact; no standard names it",
    "legal:vocabulary": "Semantics capability artifact; no standard names it",
    "legal:knowledge-graph": "Knowledge Graph capability artifact; no standard names it",
}


def _coordinates() -> list[tuple[str, str, dict, Path]]:
    """Every coordinate under domains/, in BOTH spellings and BOTH formats.

    Scope matters more than it looks. `domains/*/src/schemas/` finds 21;
    `domains/` finds 25; adding YAML `x-registry` finds 29. The narrower scans
    missed both `src/api/` tier projections — the artifacts this standard
    governs — and four artifacts declared in YAML.
    """
    found = []
    for path in sorted(DOMAINS.rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".yaml", ".yml"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            doc = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        block = doc.get("registry") if path.suffix == ".json" else doc.get("x-registry")
        if isinstance(block, dict) and block.get("artifact"):
            found.append((block.get("group", ""), block["artifact"], block, path))
    return found


ALL = _coordinates()
IN_SCOPE = [c for c in ALL if SUBJECT.match(c[1])]
DATA_SCHEMAS = [c for c in ALL if DATA_SCHEMA.match(c[1])]


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


def test_coordinates_were_actually_found():
    """A walk that silently finds nothing would make every test below vacuous."""
    assert len(ALL) >= 25, f"only {len(ALL)} coordinates found; the walk is broken"
    assert len(IN_SCOPE) >= 14, f"only {len(IN_SCOPE)} in-scope; expected the 14 migrated"
    assert len(DATA_SCHEMAS) >= 8, (
        f"only {len(DATA_SCHEMAS)} normative data schemas found; expected at least the "
        "eight per-operation payloads (price/product/program/stock x created/updated)"
    )


@pytest.mark.parametrize("group,artifact,_block,path", ALL,
                         ids=[f"{g}:{a}" for g, a, _, _ in ALL])
def test_every_coordinate_conforms_or_is_listed_out_of_scope(group, artifact, _block, path):
    if SUBJECT.match(artifact):
        return
    key = f"{group}:{artifact}"
    assert key in OUT_OF_SCOPE, (
        f"{_rel(path)} declares '{key}', which is neither an ADR-0023 subject "
        f"({{domain}}.{{entity}}.{{type}}, type one of {', '.join(TYPES)}) nor on the "
        f"out-of-scope list in {_rel(Path(__file__))}. Either fix the coordinate or add "
        f"it to OUT_OF_SCOPE with what governs it."
    )


@pytest.mark.parametrize("group,artifact,block,path", IN_SCOPE,
                         ids=[f"{g}:{a}" for g, a, _, _ in IN_SCOPE])
def test_an_in_scope_coordinate_declares_the_normative_rules(group, artifact, block, path):
    """Group, compatibility and tier — the three the normative standard fixes."""
    domain_slot = artifact.split(".")[0]
    assert group == domain_slot, (
        f"{_rel(path)}: group '{group}' does not match the artifact's domain slot "
        f"'{domain_slot}'"
    )
    assert (DOMAINS / domain_slot).is_dir(), (
        f"{_rel(path)}: '{domain_slot}' is not a directory under domains/"
    )
    assert block.get("compatibility") == "BACKWARD_TRANSITIVE", (
        f"{_rel(path)}: compatibility is {block.get('compatibility')!r}; the normative "
        "standard requires BACKWARD_TRANSITIVE on data schema artifacts"
    )
    assert block.get("tier") in ("T1", "T2", "T3"), (
        f"{_rel(path)}: tier is {block.get('tier')!r}; D21 requires T1, T2 or T3 at "
        "registration"
    )


@pytest.mark.parametrize("group,artifact,block,path", DATA_SCHEMAS,
                         ids=[f"{g}:{a}" for g, a, _, _ in DATA_SCHEMAS])
def test_a_normative_data_schema_declares_the_normative_rules(group, artifact, block, path):
    """The same two rules, for the artifacts the normative standard names.

    `BACKWARD_TRANSITIVE` is not a preference here: the standard states it
    directly ("BACKWARD_TRANSITIVE on all `data` schema artifacts", with
    FULL_TRANSITIVE reserved for the envelope), and plain `BACKWARD` is
    strictly weaker — it lets a new consumer fail on a historical minor within
    the same major, which is the guarantee the transitive mode exists to give.

    The tier is required by D21 and is the data owner's call; T2 is the
    standard's own stated default for domain business events, and its worked
    T2 example is `com.pearson.catalog.product.updated.v1`.
    """
    assert block.get("compatibility") == "BACKWARD_TRANSITIVE", (
        f"{_rel(path)}: compatibility is {block.get('compatibility')!r}; the normative "
        "standard requires BACKWARD_TRANSITIVE on every `data` schema artifact"
    )
    assert block.get("tier") in ("T1", "T2", "T3"), (
        f"{_rel(path)}: tier is {block.get('tier')!r}; D21 requires T1, T2 or T3 at "
        "registration, and the step-2 lint fails an artifact that declares none"
    )


def test_no_entity_has_two_coordinates_for_one_type():
    """One artifact per entity per type. A duplicate means two files claim one
    address, and whichever registers last silently wins."""
    seen: dict[str, Path] = {}
    for group, artifact, _b, path in IN_SCOPE:
        key = f"{group}:{artifact}"
        assert key not in seen, (
            f"'{key}' is declared twice: {_rel(seen[key])} and {_rel(path)}"
        )
        seen[key] = path


@pytest.mark.parametrize("group,artifact,_block,path", 
                         [c for c in IN_SCOPE if c[1].rsplit(".", 1)[1] in ("api", "event", "message")],
                         ids=[f"{g}:{a}" for g, a, _, _ in IN_SCOPE
                              if a.rsplit(".", 1)[1] in ("api", "event", "message")])
def test_a_tier_projection_names_the_master_version_it_came_from(group, artifact, _block, path):
    """The drift guard.

    Majors are independent per type, so nothing structurally stops a projection
    at one version being generated from a master at another. The projection
    records the master path it was projected from, so the claim is checkable —
    and a mismatch becomes a build failure instead of a contract that quietly
    describes the wrong shape.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    source = (doc.get("metadata") or {}).get("source")
    assert source, f"{_rel(path)}: no metadata.source, so nothing pins its master"

    master = REPO / source
    assert master.is_file(), f"{_rel(path)}: metadata.source '{source}' does not exist"

    claimed = re.search(r"/(v\d+)/", source)
    assert claimed, f"{_rel(path)}: metadata.source '{source}' carries no /vN/ segment"

    actual = (json.loads(master.read_text(encoding="utf-8")).get("metadata") or {}).get("version")
    assert claimed.group(1) == actual, (
        f"{_rel(path)} was projected from {source} ({claimed.group(1)}), but that master "
        f"now declares version {actual!r}. Regenerate the projection."
    )
