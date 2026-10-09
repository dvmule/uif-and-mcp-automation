# test_generate_tier_subsets.py
# Validates the concentric subset invariant (event ⊂ message ⊂ api) and that
# the generator emits the correct properties per tier.

import json
import subprocess
import sys
from pathlib import Path

import pytest

# qe/ -> parents[1] == domains/product; the operator CLIs live under src/apps/.
STACK_ROOT = Path(__file__).resolve().parents[2]
SCRIPT     = STACK_ROOT / "src" / "apps" / "cli" / "generate_tier_subsets.py"

# Placement and the entity-name transform are the stated rule with one
# implementation (ADR-0022 decision 4) - imported, never restated here.
import generate_tier_subsets as gen
from generate_tier_subsets import committed_path


def _write_master(tmp_path: Path, properties: dict, required: list = None) -> Path:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Test",
        "type": "object",
        # metadata.domain is required, not decorative: the generator builds the
        # ADR-0023 coordinate {domain}.{entity}.{type} from it and refuses to
        # guess when it is absent. Every real master declares one.
        "metadata": {"domain": "test", "entity": "test", "version": "v1"},
        "properties": properties,
    }
    if required:
        schema["required"] = required
    path = tmp_path / "master.json"
    path.write_text(json.dumps(schema))
    return path


def _run(master: Path, out_dir: Path, entity: str = "test", version: str = "v1"):
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--master", str(master),
         "--entity", entity,
         "--version", version,
         "--out-dir", str(out_dir)],
        capture_output=True, text=True,
    )


def test_concentric_valid(tmp_path):
    master = _write_master(tmp_path, {
        "id":       {"type": "string", "x-channels": ["event", "message", "api"]},
        "name":     {"type": "string", "x-channels": ["message", "api"]},
        "details":  {"type": "object", "x-channels": ["api"]},
    }, required=["id"])
    out = tmp_path / "out"
    r = _run(master, out)
    assert r.returncode == 0, r.stderr

    # `-created`, arbitrarily: the projection is identical for every operation
    # (UIFPE-617), so the concentric invariant holds on either half of a pair.
    event   = json.loads((out / "test/event-created/v1/schema.json").read_text())
    message = json.loads((out / "test/message-created/v1/schema.json").read_text())
    api     = json.loads((out / "test/api-created/v1/schema.json").read_text())

    assert set(event["properties"].keys())   == {"id"}
    assert set(message["properties"].keys()) == {"id", "name"}
    assert set(api["properties"].keys())     == {"id", "name", "details"}
    # required filters to properties present in the tier
    assert event["required"]   == ["id"]
    assert message["required"] == ["id"]
    assert api["required"]     == ["id"]
    # x-channels is stripped from published subsets
    assert "x-channels" not in event["properties"]["id"]


def test_concentric_violation_event_missing_in_message(tmp_path):
    # event has "id", but "id" is NOT tagged message — should fail invariant
    master = _write_master(tmp_path, {
        "id":   {"type": "string", "x-channels": ["event"]},
        "name": {"type": "string", "x-channels": ["message", "api"]},
    })
    out = tmp_path / "out"
    r = _run(master, out)
    assert r.returncode == 1
    assert "INVARIANT VIOLATED" in r.stderr


def test_untagged_top_level_property_is_REFUSED(tmp_path):
    # UIFPE-523. This used to default to the "api" tier, while the retired
    # generate_channel_schemas.py defaulted the same input to ALL THREE
    # channels — two silent defaults for one input. Neither is defensible:
    # a nested field with no tag inherits its parent, and a top-level
    # property has no parent. An omitted tag means nobody decided, so the
    # generator refuses rather than publishing an undecided attribute.
    master = _write_master(tmp_path, {
        "id":       {"type": "string", "x-channels": ["event", "message", "api"]},
        "untagged": {"type": "string"},
    })
    out = tmp_path / "out"
    r = _run(master, out)

    assert r.returncode == 1, f"expected refusal, got rc={r.returncode}\n{r.stdout}"
    assert "UNTAGGED TOP-LEVEL" in r.stderr, r.stderr
    # the message must NAME the offender and say what to do about it
    assert "untagged" in r.stderr, r.stderr
    assert "x-channels" in r.stderr, r.stderr
    # and nothing may be written on the way out
    assert not (out / "test").exists(), "refused, but subsets were written anyway"


def test_every_real_master_satisfies_the_tagging_rule(tmp_path):
    # The refusal above only earns its keep if the rule holds for the masters
    # actually in the repo — otherwise it is a gate that blocks real work.
    # This is the control for "the rule is achievable", paired with the
    # known-bad control above for "the rule bites".
    repo_root = STACK_ROOT.parents[1]
    masters = sorted(repo_root.glob("domains/*/src/schemas/gold/*/v1/master.json"))
    assert masters, "no gold masters found — this test would pass vacuously"
    for master in masters:
        entity = master.parents[1].name
        r = _run(master, tmp_path / entity, entity=entity)
        assert r.returncode == 0, f"{entity} master refused:\n{r.stderr}"


def test_explicit_empty_x_channels_stays_excluded(tmp_path):
    # x-channels: [] is an EXPLICIT "no channel" marker (used for
    # internal/PK columns) and is NOT the same as a missing key — it must
    # still be excluded from every tier, including api.
    master = _write_master(tmp_path, {
        "id":  {"type": "string", "x-channels": ["event", "message", "api"]},
        "_pk": {"type": "string", "x-channels": []},
    })
    out = tmp_path / "out"
    r = _run(master, out)
    assert r.returncode == 0, r.stderr

    for tier in ("event", "message", "api"):
        subset = json.loads((out / f"test/{tier}-created/v1/schema.json").read_text())
        assert "_pk" not in subset["properties"]


# ── UIFPE-523: the subset envelope is consumer-audience, not the master's ────

def _master_with_metadata(tmp_path: Path) -> Path:
    """A master shaped like product's: an engineering metadata block naming the
    gold primary key AND its Oracle source columns, plus a non-ASCII
    description."""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Test",
        "type": "object",
        "description": "Materialized from Oracle EBS PDH — an em-dash lives here.",
        "registry": {"group": "test", "artifact": "test.test.gold",
                     "compatibility": "BACKWARD_TRANSITIVE", "tier": "T2"},
        "metadata": {
            "domain": "test", "entity": "test", "version": "v1",
            "dataClassification": "internal",
            "owner": {"contact": "someone@pearson.com"},
            "primaryKey": [
                {"goldColumn": "inventoryItemId", "drivingColumn": "INVENTORY_ITEM_ID"},
                {"goldColumn": "organizationId", "drivingColumn": "ORGANIZATION_ID"},
            ],
        },
        "properties": {
            "id": {"type": "string", "x-channels": ["event", "message", "api"]},
            "inventoryItemId": {"type": "string", "x-channels": []},
        },
    }
    path = tmp_path / "master.json"
    path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    return path


def test_subset_metadata_does_not_disclose_the_surrogate_key(tmp_path):
    # N7: a source-system surrogate key must never reach a consumer surface.
    # The property filter strips inventoryItemId from `properties`; inheriting
    # the master's metadata put it straight back, naming the Oracle column too
    # — in a subset this repo PUBLISHES to the schema registry.
    out = tmp_path / "out"
    r = _run(_master_with_metadata(tmp_path), out)
    assert r.returncode == 0, r.stderr

    for tier in ("event", "message", "api"):
        raw = (out / f"test/{tier}-created/v1/schema.json").read_text(encoding="utf-8")
        subset = json.loads(raw)
        assert "inventoryItemId" not in subset["properties"], tier
        assert "primaryKey" not in subset["metadata"], tier
        assert "owner" not in subset["metadata"], tier
        # the strongest form: the surrogate's names appear NOWHERE in the file
        assert "INVENTORY_ITEM_ID" not in raw, tier
        assert "inventoryItemId" not in raw, tier


def test_subset_declares_the_artifact_id_it_is_registered_under(tmp_path):
    # The subset used to inherit the master's `registry` block, so all three
    # tiers claimed to be the master and none matched what was registered.
    #
    # The name is ADR-0023's {domain}.{entity}.{type}, where the type IS the
    # tier. It is NOT the master's own coordinate, and it is NOT the former
    # {entity}.gold-{tier}-value, which was only ever publish.sh's argument.
    out = tmp_path / "out"
    r = _run(_master_with_metadata(tmp_path), out)
    assert r.returncode == 0, r.stderr
    # Both halves of every pair, because the per-operation coordinate is the
    # whole point: a file that declared its channel rather than its operation
    # would reintroduce exactly the UIFPE-523 disagreement this pins against.
    for tier in ("event", "message", "api"):
        for operation in ("created", "updated"):
            token = f"{tier}-{operation}"
            subset = json.loads(
                (out / f"test/{token}/v1/schema.json").read_text(encoding="utf-8"))
            assert subset["registry"]["artifact"] == f"test.test.{token}", token
            assert subset["registry"]["artifact"] != "test.test.gold", (
                f"{token} claims the master's coordinate"
            )
            assert subset["registry"]["artifact"] != f"test.test.{tier}", (
                f"{token} declares the bare channel, which is a superseded type"
            )
            # Normative Pearson rules, not the master's values (ADR-0023 dec. 5).
            assert subset["registry"]["compatibility"] == "BACKWARD_TRANSITIVE", token
            assert subset["registry"]["tier"] == "T2", token
            assert subset["metadata"]["channel"] == tier
            assert subset["metadata"]["operation"] == operation
        assert subset["metadata"]["transport"]


def test_a_master_with_no_domain_is_REFUSED(tmp_path):
    """The coordinate's domain comes from the master (ADR-0022 decision 2), so a
    master without one cannot be projected.

    Guessing it — from the output path, the entity, or a default — would publish
    a contract to a coordinate no consumer is watching, and nothing downstream
    could tell that from a contract with no subscribers yet.
    """
    master = tmp_path / "master.json"
    master.write_text(json.dumps({
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Test", "type": "object",
        "metadata": {"entity": "test", "version": "v1"},   # no domain
        "properties": {"a": {"type": "string", "x-channels": ["event", "message", "api"]}},
    }))
    r = _run(master, tmp_path / "out")
    assert r.returncode == 1, f"expected refusal, got {r.returncode}\n{r.stdout}"
    assert "metadata.domain" in r.stderr, r.stderr


def test_non_ascii_survives_generation(tmp_path):
    # read_text()/write_text() default to the platform encoding, which is
    # cp1252 on Windows: without an explicit utf-8 the master's em-dash became
    # mojibake in every published subset.
    out = tmp_path / "out"
    r = _run(_master_with_metadata(tmp_path), out)
    assert r.returncode == 0, r.stderr
    raw = (out / "test/api-created/v1/schema.json").read_text(encoding="utf-8")
    assert "â€" not in raw, "non-ASCII was mojibaked"


# ── UIFPE-523/554: the committed copies must equal what the generator makes ──

def _gold_masters():
    """Every gold master in the repository, as (domain, entity, path)."""
    repo_root = STACK_ROOT.parents[1]
    found = []
    for master in sorted(repo_root.glob("domains/*/src/schemas/gold/*/v*/master.json")):
        parts = master.relative_to(repo_root).parts
        found.append((parts[1], parts[5], master))
    return repo_root, found


def _committed_copies(repo_root, domain, entity, tier, operation):
    """Where a projection is committed, DERIVED from the stated rule.

    Placement is ADR-0022 decision 4 now, with one implementation, so this asks
    for the path instead of hunting for a filename (UIFPE-554). The difference
    matters: an rglob finds a copy wherever someone put it, and so cannot fail
    when a copy is in the wrong place — which is how the api tier came to sit
    in two different directories without anything noticing.

    Returned as a list of one so callers read unchanged, and so a future rule
    that commits a tier in two places needs no caller change.
    """
    rel = committed_path(domain, entity, tier, operation)
    p = repo_root / rel
    return [p] if p.exists() else []


def test_every_gold_master_has_committed_projections_matching_the_generator(tmp_path):
    """Generalised from two hardcoded entities to all of them (UIFPE-554).

    A failure means one of three things: the master changed and the committed
    copies were not regenerated, someone hand-edited a generated file, or an
    entity has no committed projection at all. The first two are fixed by
    rerunning the generator; the third is the gap that let marketing_copy ship
    a master and an OpenAPI contract with no projection beside it, unnoticed,
    because the only checks were written per entity by hand.
    """
    repo_root, masters = _gold_masters()
    assert len(masters) >= 5, (
        f"found only {len(masters)} gold masters — the glob is wrong, and a "
        "check that silently finds nothing is worse than no check"
    )

    for domain, entity, master in masters:
        out = tmp_path / domain / entity
        r = _run(master, out, entity=entity)
        assert r.returncode == 0, f"{domain}/{entity}: {r.stderr}"

        declared = json.loads(
            (out / entity / "v1" / "tiers.json").read_text(encoding="utf-8"))

        for type_token in declared["types"]:
            tier, operation = type_token.rsplit("-", 1)
            generated = (out / entity / type_token / "v1" / "schema.json") \
                .read_text(encoding="utf-8")
            hits = _committed_copies(repo_root, domain, entity, tier, operation)
            assert hits, (
                f"{domain}/{entity}: {type_token} is generated but no committed "
                f"copy exists at {committed_path(domain, entity, tier, operation)}, "
                f"where the rule puts it. Generate it: python3 "
                f"{SCRIPT.relative_to(repo_root).as_posix()} --master "
                f"{master.relative_to(repo_root).as_posix()} --entity {entity} "
                f"--version v1 --out-dir <dir>"
            )
            assert len(hits) == 1, (
                f"{domain}/{entity}: {type_token} has {len(hits)} committed copies "
                f"({[h.relative_to(repo_root).as_posix() for h in hits]}) — two files "
                "claiming one coordinate is the UIFPE-523 disagreement again"
            )
            assert hits[0].read_text(encoding="utf-8") == generated, (
                f"{hits[0].relative_to(repo_root).as_posix()} does not match the "
                f"generator. Regenerate: python3 "
                f"{SCRIPT.relative_to(repo_root).as_posix()} --master "
                f"{master.relative_to(repo_root).as_posix()} --entity {entity} "
                f"--version v1 --out-dir <dir>"
            )

        # A skipped tier must have no committed copy: a leftover file from
        # before an attribute lost its channel tag would be a contract nothing
        # serves, and the coordinate it declares would still be registrable.
        for channel in declared["skipped"]:
            for op in ("created", "updated"):
                stale = _committed_copies(repo_root, domain, entity,
                                          channel, op)
                assert not stale, (
                    f"{domain}/{entity}: {channel} is skipped — no attribute is tagged "
                    f"for it — yet {[s.relative_to(repo_root).as_posix() for s in stale]} "
                    "is committed. Delete it; it is a contract nothing serves."
                )

# ── UIFPE-553: a channel an entity does not serve gets no contract ──────────

def test_a_tier_with_no_attributes_is_not_emitted(tmp_path):
    """An entity serving one audience is a legitimate shape; a contract for a
    channel it does not serve is not.

    The empty projection used to be written anyway, and three guards all passed
    it: no size check existed, the concentric invariant holds trivially for
    empty sets, and each per-attribute tag is individually valid.
    """
    master = _write_master(tmp_path, {
        "contentId": {"type": "string", "x-channels": ["api"]},
        "bodyText":  {"type": "string", "x-channels": ["api"]},
    })
    out = tmp_path / "out"
    r = _run(master, out)
    assert r.returncode == 0, r.stderr

    assert (out / "test/api-created/v1/schema.json").exists()
    assert (out / "test/api-updated/v1/schema.json").exists()
    for tier in ("event-created", "event-updated", "message-created", "message-updated"):
        assert not (out / f"test/{tier}").exists(), (
            f"{tier} has no attributes tagged for it, so no {tier} contract "
            f"should have been written"
        )


def test_tiers_json_declares_what_was_emitted_and_skipped(tmp_path):
    """The generator declares its output so consumers read the set rather than
    re-deriving it from x-channels, or probing the filesystem.

    Probing cannot distinguish a deliberately skipped tier from one missing
    because generation failed, which is why the declaration exists at all.
    """
    master = _write_master(tmp_path, {
        "id":   {"type": "string", "x-channels": ["event", "message", "api"]},
        "note": {"type": "string", "x-channels": ["api"]},
    })
    out = tmp_path / "out"
    assert _run(master, out).returncode == 0

    declared = json.loads((out / "test/v1/tiers.json").read_text(encoding="utf-8"))
    assert declared["emitted"] == ["event", "message", "api"]
    assert declared["skipped"] == []
    assert declared["entity"] == "test"
    assert declared["version"] == "v1"

    # api-only: the declaration must name what is missing, not stay silent
    master2 = _write_master(tmp_path, {"only": {"type": "string", "x-channels": ["api"]}})
    out2 = tmp_path / "out2"
    assert _run(master2, out2).returncode == 0
    d2 = json.loads((out2 / "test/v1/tiers.json").read_text(encoding="utf-8"))
    assert d2["emitted"] == ["api"]
    assert d2["skipped"] == ["event", "message"]


def test_an_entity_reaching_no_channel_is_REFUSED(tmp_path):
    """Every attribute internal means no consumer contract at all. Writing three
    empty files would be silently useless, so this refuses instead."""
    master = _write_master(tmp_path, {
        "_pk":      {"type": "string", "x-channels": []},
        "_ingested": {"type": "string", "x-channels": []},
    })
    out = tmp_path / "out"
    r = _run(master, out)

    assert r.returncode == 1, f"expected refusal, got {r.returncode}\n{r.stdout}"
    assert "reaches NO channel" in r.stderr
    assert not out.exists() or not any(out.rglob("schema.json")), (
        "nothing may be written on the way out of a refusal"
    )


def test_every_real_master_emits_at_least_one_tier(tmp_path):
    """The control for the refusal above: it must bite on a bad master without
    blocking any master that exists today."""
    repo_root = STACK_ROOT.parents[1]
    masters = sorted(repo_root.glob("domains/*/src/schemas/gold/*/v1/master.json"))
    assert masters, "no gold masters found - this control would pass on nothing"

    for master in masters:
        entity = master.parents[1].name
        out = tmp_path / entity
        r = _run(master, out, entity=entity)
        assert r.returncode == 0, f"{entity}: {r.stderr}"

        declared = json.loads(
            (out / f"{entity}/v1/tiers.json").read_text(encoding="utf-8"))
        assert declared["emitted"], f"{entity} emitted no tier at all"

        # The declaration and the files on disk must agree in BOTH directions:
        # a declared tier with no file, or a file no tier declared, is drift.
        on_disk = {p.parents[1].name for p in out.rglob("schema.json")}
        # `emitted` names the CHANNELS; `types` names the coordinates actually
        # written, one per channel per operation. The directories are type
        # tokens, so this compares against `types` -- and that field exists
        # for exactly this check rather than re-deriving the cross product,
        # which is the restatement ADR-0022 decision 1 forbids.
        assert on_disk == set(declared["types"]), (
            f"{entity}: tiers.json says {declared['types']} but the files on "
            f"disk are {sorted(on_disk)}"
        )


# ── the stated rule and its implementation, checked BOTH ways (UIFPE-554) ───
#
# ADR-0022 decision 4 states where a projection is committed; committed_path()
# implements it. Either alone reads fine, which is how the api tier came to sit
# in two directories with a passing suite. The same arrangement the
# resource-naming standard has with its hook.

ADR_0022 = (STACK_ROOT.parents[1] / "architecture-design/decisions"
            / "0022-gold-schema-pipeline-repo-first-registry-next.md")


def _adr_placement_rows():
    """The placement table from ADR-0022, as {tier: (directory, filename)}."""
    import re

    rows = {}
    for line in ADR_0022.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*\|\s*`(api|event|message)`\s*\|\s*`([^`]+)`"
                     r"\s*\|\s*`([^`]+)`\s*\|\s*$", line)
        if m:
            rows[m.group(1)] = (m.group(2), m.group(3))
    return rows


def test_the_ADR_states_a_row_for_every_tier_the_code_implements():
    rows = _adr_placement_rows()
    assert set(rows) == set(gen.COMMITTED_DIRS), (
        f"ADR-0022 states {sorted(rows)}, the code implements "
        f"{sorted(gen.COMMITTED_DIRS)} — a tier in one and not the other is a "
        "rule nobody can follow or a rule nothing enforces"
    )


def test_every_ADR_row_matches_what_committed_path_BUILDS():
    """The direction that catches an edit to the code, and the reason the rows
    carry placeholders rather than a worked example: substituting them is what
    makes the document executable.
    """
    rows = _adr_placement_rows()
    assert rows, "no placement table found in ADR-0022 — the rule is the point"
    for tier, (directory, filename) in rows.items():
        for operation in ("created", "updated"):
            stated = (directory.rstrip("/") + "/" + filename).format(
                domain="d", entity="e", operation=operation)
            built = gen.committed_path("d", "e", tier, operation)
            assert built == stated, (
                f"{tier}: ADR-0022 says {stated}, committed_path builds {built}")


def test_a_CONTRACT_is_not_a_projection_and_lives_apart():
    """Stated in the ADR's prose rather than its table, because a contract is
    not a projection: it stays in the API capability's folder while every
    projection lives with the schemas.

    Pinned because the two were briefly derived from ONE constant, so moving
    the projections silently dragged the contract out of its capability folder
    — a mistake that reads as a tidy-up right up until nothing can find the
    contract.
    """
    contract = Path(gen.contract_path("d", "e"))
    assert contract.name == "openapi.yaml"
    assert contract.parent.as_posix() == "domains/d/src/api/e"
    projections = {Path(gen.committed_path("d", "e", t, "created")).parent
                   for t in gen.COMMITTED_DIRS}
    assert len(projections) == 1, "every tier shares one directory"
    assert contract.parent not in projections, (
        "the contract must not be derived from the projection rule")


def test_the_entity_transform_is_the_one_the_REGISTRY_uses():
    """One transform, two consumers. The coordinate and the committed path both
    hyphenate, and an entity whose name carries an underscore is the only case
    that can tell them apart.
    """
    assert gen.entity_token("marketing_copy") == "marketing-copy"
    assert gen.entity_token("product") == "product"
    p = gen.committed_path("product", "marketing_copy", "api", "created")
    assert "marketing-copy/marketing-copy-data-api-created.json" in p
    assert "marketing_copy" not in p, (
        "an underscore reached a committed path — the master keeps that form, "
        "every artifact hyphenates it")


def test_EVERY_committed_projection_is_where_the_rule_says():
    """The sweep. A file matching the conventional name but sitting somewhere
    else is exactly what the previous rglob-based check could not see, because
    finding a copy anywhere counted as finding it.
    """
    repo_root, masters = _gold_masters()
    known = set()
    for domain, entity, _ in masters:
        for tier in gen.COMMITTED_DIRS:
            for op in ("created", "updated"):
                known.add(repo_root / gen.committed_path(domain, entity, tier, op))

    stray = []
    for found in sorted((repo_root / "domains").rglob("*-data-*.json")):
        if "/src/" not in found.as_posix():
            continue
        if found not in known:
            stray.append(found.relative_to(repo_root).as_posix())
    assert not stray, (
        "these look like tier projections but are not where the rule puts "
        f"them: {stray}")
