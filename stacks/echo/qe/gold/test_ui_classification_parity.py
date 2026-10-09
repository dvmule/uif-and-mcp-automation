# The Transform UI shows every attribute an AUTO/PLAN verdict WHILE the author
# is still editing, so they know whether they are promising "appears on the next
# run" or "waits for a data engineer". That verdict is a mirror of this
# compiler's own classification, written in TypeScript because it has to run in
# the browser with no backend.
#
# Two implementations of one rule drift silently, and the failure is worse than
# a missing feature: the UI would tell a business owner "no engineer needed" for
# a change that lands in the engineer's queue. So the verdict for every
# registered column is pinned in a fixture both sides assert against:
#
#   stacks/echo/transform-ui/src/lib/__fixtures__/classificationVerdicts.json
#     · this module checks the fixture still matches what compile_delta reaches
#     · ui/src/lib/__tests__/classify.test.ts checks the mirror reaches the same
#
# Changing the classification rules is allowed. Changing them without
# regenerating the fixture (and so without the UI following) is not — that is
# what this test blocks. Regenerate with:
#
#   REGEN_UI_VERDICTS=1 py -m pytest domains/product/qe/test_ui_classification_parity.py
#
# (an env var rather than a --regen flag: pytest only honours addoption in
# conftest.py, and this test does not need to reach into the shared one)
#
# The intent split is the reason this matters at all: on the registered product
# entity, ADD yields 49 AUTO and MODIFY yields 13, because every driving-table
# scalar becomes plan:driving-modify, (UIFPE-106) every conditional and
# lookup-join becomes plan:conditional-modify / plan:lookup-join-modify, and
# (UIFPE-217) a derived column's modify is plan:derived-sql-modify while its
# add splices the author's query automatically.
# Modify is the wizard's most-travelled path — the home page deep-links
# straight into it.

import json
import os
import re
from pathlib import Path

import pytest

import compile_intake_delta as cid

REPO = Path(__file__).resolve().parents[4]
INTAKE = (REPO / "domains" / "product" / "src" / "schemas" / "mapping"
          / "product" / "v1" / "product.mapping.json")
FIXTURE = (REPO / "stacks" / "echo" / "transform-ui" / "src" / "lib" / "__fixtures__"
           / "classificationVerdicts.json")

AUTO_RE = re.compile(r"^\[AUTO\] ([A-Za-z0-9_.]+): ")
PLAN_RE = re.compile(r"^\[PLAN\] ([A-Za-z0-9_.]+) \(([^)]+)\)")

INTENTS = ("add", "modify")


REGEN = bool(os.environ.get("REGEN_UI_VERDICTS"))


def _verdicts(intent: str) -> dict[str, str]:
    """The verdict compile_delta reaches per column, read from its own log.

    Parsing the log rather than re-deriving from classify() is deliberate: the
    log is downstream of the intent overrides, which is exactly the part a
    hand-written mirror gets wrong.
    """
    intake = json.loads(INTAKE.read_text(encoding="utf-8"))
    _, log = cid.compile_delta(REPO, intake, intent, None)
    out: dict[str, str] = {}
    for line in log:
        first = line.splitlines()[0]
        m = AUTO_RE.match(first)
        if m:
            out[m.group(1)] = "AUTO"
            continue
        m = PLAN_RE.match(first)
        if m:
            out[m.group(1)] = m.group(2)
    return out


@pytest.fixture(scope="module")
def compiler_verdicts() -> dict[str, dict[str, str]]:
    return {intent: _verdicts(intent) for intent in INTENTS}


def test_fixture_exists():
    assert FIXTURE.exists(), (
        f"{FIXTURE.relative_to(REPO)} is missing — the UI mirror has nothing to "
        "assert against. Regenerate it with REGEN_UI_VERDICTS=1.")


def test_fixture_covers_every_registered_column(compiler_verdicts):
    expected = len(json.loads(INTAKE.read_text(encoding="utf-8"))["columns"])
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for intent in INTENTS:
        assert len(fixture[intent]) == expected, (
            f"fixture[{intent}] has {len(fixture[intent])} columns, intake has "
            f"{expected}")
        assert len(compiler_verdicts[intent]) == expected, (
            f"compile_delta logged {len(compiler_verdicts[intent])} verdicts for "
            f"{intent}, intake has {expected} columns")


@pytest.mark.parametrize("intent", INTENTS)
def test_fixture_matches_the_compiler(intent, compiler_verdicts):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    got = compiler_verdicts[intent]

    if REGEN:  # pragma: no cover - maintenance path
        fixture[intent] = dict(sorted(got.items()))
        FIXTURE.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        pytest.skip(f"regenerated fixture[{intent}]")

    drifted = {name: (fixture[intent].get(name), verdict)
               for name, verdict in got.items()
               if fixture[intent].get(name) != verdict}
    assert not drifted, (
        f"classification changed for {len(drifted)} column(s) on {intent} "
        f"(fixture -> compiler): {drifted}. If this change is intended, "
        "regenerate the fixture with REGEN_UI_VERDICTS=1 so the UI mirror follows; "
        "ui/src/lib/classify.ts must reach the same verdicts.")


def test_intent_swing_is_still_real(compiler_verdicts):
    """Guards the assumption the UI is built on: modifying a driving-table
    scalar is PLAN even though adding one is AUTO. If this ever stops being
    true the UI can stop warning about it — but not silently."""
    add, mod = compiler_verdicts["add"], compiler_verdicts["modify"]
    flipped = [n for n in add
               if add[n] == "AUTO" and mod.get(n) == "plan:driving-modify"]
    assert flipped, (
        "no column flips AUTO -> plan:driving-modify on modify; the compiler's "
        "intent override may have been removed, in which case classify.ts and "
        "the Attributes page should drop the warning")
    assert all(cid.classify(c, "MTL_SYSTEM_ITEMS_B") == "driving"
               for c in json.loads(INTAKE.read_text(encoding="utf-8"))["columns"]
               if c["goldColumnName"] in flipped)
