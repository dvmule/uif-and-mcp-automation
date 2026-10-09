"""The step-2 naming rule, executable: an artifact name and its event `type`
must agree.

The normative standard states the two forms separately —

    event type : com.pearson.<domain>.<entity>.<action>.v<major>     (D5)
    artifact   : <entity>-<action>-data                              (D5, D7)

— and `<entity>` and `<action>` appear in BOTH. Nothing compared them. The
reverse-DNS type is pinned by each domain's envelope schema in
`domains/<domain>/qe/test_event_schemas.py`, and the artifact coordinate is
checked for conformance-or-allowlisting by `test_subject_naming.py`, but no
check tied one to the other. So an artifact could be `product-created-data`
while the events it governs carry `...product.updated.v1`, and every existing
gate would pass: the type is well-formed, the artifact is well-formed, and they
describe different things.

That is the same failure shape as the two-writer drift this standard already
guards elsewhere — one fact recorded in two places, with nothing asserting they
agree. Here the consequence is a payload registered at the coordinate for a
different operation, which no consumer resolving the type would find.

This is the step-2 lint rule set's *naming* row and nothing more. The other
rows — D16 field patterns and class words, the canonical-vocabulary collision
check, D17 privacy derivation, D18 shape, D19 definition quality, D20 analytics
metadata — are separate and not implemented here. Compatibility mode and D21
tier are asserted in `test_subject_naming.py`.

Derived, never hardcoded: the expected artifact name is computed FROM the type,
so there is no action vocabulary to maintain. The standard puts no closed list
on `<action>` (`.corrected` is named in the producer rules), and inventing one
here would be adding a rule the standard does not have.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

STANDARDS = Path(__file__).resolve().parent
REPO = STANDARDS.parents[2]
DOMAINS = REPO / "domains"

#: D5. Kept in step with the envelope schemas in each domain's qe/ suite, which
#: pin `^com\.pearson\.[a-z0-9]+\.[a-z0-9-]+\.[a-z0-9]+\.v[0-9]+$`. The entity
#: slot admits a hyphen because a kebab entity is legal (`marketing-copy`).
EVENT_TYPE = re.compile(
    r"^com\.pearson\."
    r"(?P<domain>[a-z0-9]+)\."
    r"(?P<entity>[a-z0-9][a-z0-9-]*)\."
    r"(?P<action>[a-z0-9]+)\."
    r"v(?P<major>[0-9]+)$"
)

DATASCHEMA_BASE = "https://schemas.pearson.com"


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


def _examples() -> list[tuple[str, dict, Path]]:
    """Every committed envelope example, which is where a `type` and the
    `dataschema` that resolves it sit side by side."""
    found = []
    for path in sorted(DOMAINS.glob("*/src/schemas/events/*-example.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as err:
            # A malformed example is a real failure, not something to skip
            # past: a walk that silently drops files reports a green it has
            # not earned. Chained, so the parse error itself is still visible.
            raise AssertionError(f"{_rel(path)} is not readable JSON") from err
        if isinstance(doc, dict) and doc.get("type"):
            # domains/<domain>/src/schemas/events/<file>.json -> index -5
            found.append((path.parts[-5], doc, path))
    return found


EXAMPLES = _examples()


def test_examples_were_actually_found():
    """Guards every parametrization below from going vacuous."""
    assert len(EXAMPLES) >= 8, (
        f"only {len(EXAMPLES)} envelope examples found; expected at least the eight "
        "per-operation payloads (price/product/program/stock x created/updated)"
    )


@pytest.mark.parametrize("domain,doc,path", EXAMPLES,
                         ids=[f"{d}:{p.stem}" for d, _, p in EXAMPLES])
def test_the_event_type_is_well_formed(domain, doc, path):
    """D5, restated here so a later failure reads as a mismatch, not a crash."""
    assert EVENT_TYPE.match(doc["type"]), (
        f"{_rel(path)}: type {doc['type']!r} is not "
        "com.pearson.<domain>.<entity>.<action>.v<major>"
    )


@pytest.mark.parametrize("domain,doc,path", EXAMPLES,
                         ids=[f"{d}:{p.stem}" for d, _, p in EXAMPLES])
def test_the_dataschema_resolves_the_artifact_the_type_implies(domain, doc, path):
    """The artifact name is derived from the type, then compared.

    This is the rule. `<entity>-<action>-data` is not matched as a shape — it is
    computed from the type's own entity and action slots, so the two cannot
    drift apart without this failing.
    """
    m = EVENT_TYPE.match(doc["type"])
    assert m, f"{_rel(path)}: unparseable type, see the previous test"
    expected_artifact = f"{m['entity']}-{m['action']}-data"
    expected_uri = f"{DATASCHEMA_BASE}/{m['domain']}/{expected_artifact}/v{m['major']}"

    assert doc.get("dataschema") == expected_uri, (
        f"{_rel(path)}: dataschema is {doc.get('dataschema')!r}, but type "
        f"{doc['type']!r} implies {expected_uri!r}. The artifact name and the event "
        "type disagree about the entity, the action or the major — registering that "
        "payload puts it at a coordinate no consumer resolving this type will find."
    )

    assert m["domain"] == domain, (
        f"{_rel(path)}: type declares domain {m['domain']!r} but the file lives under "
        f"domains/{domain}/"
    )


@pytest.mark.parametrize("domain,doc,path", EXAMPLES,
                         ids=[f"{d}:{p.stem}" for d, _, p in EXAMPLES])
def test_the_referenced_data_schema_declares_that_same_coordinate(domain, doc, path):
    """Close the loop: the schema the dataschema points at must claim the name.

    Without this the check stops at the example. The registry is fed from the
    `registry` block of the data schema file, so that block is what actually
    decides the coordinate — an example agreeing with its type while the schema
    it references claims something else is still publishable drift.
    """
    m = EVENT_TYPE.match(doc["type"])
    assert m, f"{_rel(path)}: unparseable type, see the earlier test"
    expected_artifact = f"{m['entity']}-{m['action']}-data"

    schema_path = DOMAINS / domain / "src" / "schemas" / "events" / f"{expected_artifact}.json"
    assert schema_path.is_file(), (
        f"{_rel(path)}: type {doc['type']!r} implies {expected_artifact}.json, which "
        f"does not exist at {_rel(schema_path)}"
    )

    block = json.loads(schema_path.read_text(encoding="utf-8")).get("registry") or {}
    assert block.get("artifact") == expected_artifact, (
        f"{_rel(schema_path)}: registry.artifact is {block.get('artifact')!r}; the event "
        f"type {doc['type']!r} implies {expected_artifact!r}"
    )
    assert block.get("group") == m["domain"], (
        f"{_rel(schema_path)}: registry.group is {block.get('group')!r}; the event type "
        f"implies {m['domain']!r}"
    )
