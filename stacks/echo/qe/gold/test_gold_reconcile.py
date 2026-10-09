# test_gold_reconcile.py
# generic_gold_reconcile.py is the ONLY job that deletes gold rows, and it had
# no tests. These pin the three things that stand between a config flag and a
# mass delete.
#
# They are structural — the job imports pyspark and awsglue, so it cannot be
# imported here — and each one reads the specific block rather than grepping
# the whole file, because a whole-file match passes on a reordered guard.

from pathlib import Path

import pytest
import yaml

STACK_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = STACK_ROOT.parents[1]
SRC = (STACK_ROOT / "src/apps/pipeline/generic_gold_reconcile.py").read_text(
    encoding="utf-8")


def _between(start: str, end: str) -> str:
    """The source between two anchors, so a pin reads one block."""
    assert SRC.count(start) == 1, f"anchor {start!r} is not unique"
    body = SRC.split(start, 1)[1]
    assert end in body, f"{end!r} does not follow {start!r}"
    return body.split(end, 1)[0]


def test_dry_run_honours_the_CONFIG_and_not_only_the_job_arg():
    """A job arg protects an operator running it by hand. The config flag is
    what protects the SCHEDULED run, which nobody is watching. The guard has
    to read both, so `dry_run: true` in the pipeline YAML cannot be defeated
    by a run that simply omits --dry-run.
    """
    guard = [ln.strip() for ln in SRC.splitlines()
             if ln.strip().startswith("if ") and "DRY_RUN" in ln]
    assert len(guard) == 1, f"expected one dry-run guard, found {guard}"
    assert 'rec_cfg.get("dry_run"' in guard[0], (
        f"the CONFIG flag is not consulted in {guard[0]!r} — a scheduled run "
        f"would delete while the pipeline YAML says dry_run: true")


def test_the_dry_run_guard_RETURNS_before_the_delete():
    """Ordering is the whole guarantee. If the delete MERGE ever moves above
    this return, `dry_run: true` silently stops meaning anything.
    """
    body = _between("if DRY_RUN or rec_cfg.get", "# ─── Delete orphans")
    assert "return" in body, (
        "no return between the dry-run branch and the delete — dry_run would "
        "report and then delete anyway")


def test_reconcile_is_OFF_when_a_config_does_not_mention_it():
    """The default decides what happens to an entity whose YAML predates
    reconcile. It must be False: absent config means do nothing, never
    'delete whatever the anti-join finds'.
    """
    block = _between('rec_cfg = cfg.get("reconcile", {})', "return")
    assert 'rec_cfg.get("enabled", False)' in block, (
        "reconcile's enabled default is not False — an entity that never "
        "declared reconcile could start deleting")


def test_the_safety_cap_RAISES_rather_than_truncating():
    """Capping by deleting only the first N would be worse than failing: it
    half-applies a delete nobody reviewed. The cap must abort the run.
    """
    block = _between("if orphan_count > max_deletes_per_run", "# ─── Dry-run")
    assert "raise RuntimeError" in block, (
        "the safety cap does not abort — a run over the cap must fail, not "
        "delete a subset")


# ── the config this change actually ships ───────────────────────────────────

PIPELINES = sorted(
    (REPO_ROOT / "domains").glob("*/src/apps/pipelines/base/*.yaml"))


#: Entities whose owner has READ the orphan list and accepted the deletes.
#: An allow-list rather than a blanket rule, because "this pipeline may delete
#: gold rows unattended" is a per-entity decision on per-entity evidence, and
#: adding a name here should cost a review.
DELETE_ALLOWED = {
    # 2026-09-29: the 14 orphans are absent from mtl_system_items_b at every
    # retained Silver snapshot while the gold rows predate the oldest of them,
    # and the item master grew across those reloads rather than truncating.
    # The surviving mtl_system_items_tl rows are Oracle not cascading a delete,
    # not Silver losing data. Owner confirmed the source deletes.
    "program",
    # 2026-09-29: the job's OWN dry run reported orphan_count=269,479 —
    # identical to the Athena measurement by a different path (a Spark
    # anti-join versus SQL) with rows_deleted=0. 99.3% are RETIRED products
    # that accumulated because reconcile was off since first load. Cleared for
    # ONE catch-up; step three returns the cap to 5,000 and removes the
    # CAP_RAISED entry, which un-clears this pairing too.
    "product",
}


@pytest.mark.parametrize("path", PIPELINES, ids=lambda p: p.stem)
def test_an_entity_deletes_unattended_only_if_ALLOW_LISTED(path):
    """`dry_run: false` means this pipeline deletes gold rows on a schedule
    nobody is watching. That is fine once someone has read the orphan list and
    said so — and it must not become fine by default for the next entity.

    So the assertion is not "nothing deletes"; it is "only what we decided
    deletes". A new entity flipping dry_run fails here until its name is added
    with its reason, which is the review this deserves.
    """
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rec = cfg.get("reconcile") or {}
    if not rec.get("enabled", False):
        pytest.skip(f"{path.stem} has reconcile off entirely")
    if rec.get("dry_run") is not False:
        return                              # observing; nothing to authorise
    assert path.stem in DELETE_ALLOWED, (
        f"{path.stem} has reconcile dry_run=false and would DELETE gold rows "
        f"on its next scheduled run, but is not in DELETE_ALLOWED. Read its "
        f"orphan list first, then add it with the evidence.")
    assert rec.get("max_deletes_per_run", 0) > 0, (
        f"{path.stem} deletes with no cap — a bad upstream day becomes a "
        f"mass-delete with nothing to stop it")


#: Entities whose delete cap is raised above the ordinary bound, each with why
#: and what returns it. A raised cap is temporary by nature: it is sized for a
#: one-off catch-up, and left in place it stops catching the upstream
#: mass-delete it exists for. The owner rejected raising caps permanently
#: (2026-09-29), so this list is how that decision stays enforced rather than
#: remembered.
ORDINARY_CAP = 10_000
#: Empty, and that is the point. product's entry was removed on 2026-09-29 the
#: same day its 269,479-row catch-up completed and the cap went back to 5,000.
#: The two asserts in the delete guard below are what would have caught those
#: halves drifting apart — dropping this entry while the cap stayed raised
#: fails, and so does raising a cap without adding one.
CAP_RAISED: dict[str, str] = {}


@pytest.mark.parametrize("path", PIPELINES, ids=lambda p: p.stem)
def test_a_raised_delete_cap_is_LISTED_with_its_reason(path):
    """A cap is the only thing standing between a bad upstream day and a mass
    delete, and raising it is sometimes right — a genuine backlog has to be
    cleared somehow. What must not happen is a raise that nobody notices has
    outlived its reason.

    So the assertion is not "caps stay small"; it is "a raised cap is on the
    record". Removing the entry fails the test, which is the prompt to put the
    cap back.
    """
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rec = cfg.get("reconcile") or {}
    cap = rec.get("max_deletes_per_run")
    if cap is None or cap <= ORDINARY_CAP:
        return
    assert path.stem in CAP_RAISED, (
        f"{path.stem} has max_deletes_per_run={cap:,}, above the ordinary "
        f"{ORDINARY_CAP:,}, and is not in CAP_RAISED. Either bring it back "
        f"down or record why it is raised and what returns it.")
    assert len(CAP_RAISED[path.stem]) > 60, (
        f"{path.stem}'s CAP_RAISED entry does not say why")


def test_a_raised_cap_deletes_only_where_CLEARED():
    """A raised cap with dry_run off is an unattended delete of up to the cap.
    That is right exactly once per backlog and wrong every other time, so it
    needs naming rather than forbidding.

    The pairing with CAP_RAISED is deliberate: an entity here must also be
    there, so step three cannot half-finish and leave a 300,000 cap armed.
    """
    for p in PIPELINES:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        rec = cfg.get("reconcile") or {}
        if (rec.get("max_deletes_per_run") or 0) <= ORDINARY_CAP:
            continue
        if rec.get("dry_run") is not False:
            continue
        # Both lists, deliberately. A third list for the intersection would
        # be one more thing to forget; requiring membership in the two that
        # already exist means step three cannot half-finish — removing the
        # CAP_RAISED entry while the cap is still raised fails right here.
        assert p.stem in DELETE_ALLOWED, (
            f"{p.stem} has a raised cap AND dry_run off — an unattended "
            f"delete of up to {rec['max_deletes_per_run']:,} rows — and is "
            f"not in DELETE_ALLOWED")
        assert p.stem in CAP_RAISED, (
            f"{p.stem} is cleared to delete but missing from CAP_RAISED, so "
            f"nothing records that its cap must come back down")
