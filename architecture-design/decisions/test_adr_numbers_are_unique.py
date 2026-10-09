"""ADR numbers must be unique — the convention in README.md says one file per
decision, zero-padded sequence, and it has been broken five times.

A duplicate number makes every "see ADR-NNNN" reference ambiguous, and there
are ~100 such references in this repository.

GRANDFATHERED lists the duplicates that predate this gate. Resolving them
means renumbering merged records and updating references, which is its own
decision; the gate stops NEW collisions without forcing that.
"""
import re
from collections import defaultdict
from pathlib import Path

DECISIONS = Path(__file__).resolve().parent

GRANDFATHERED = {"0007", "0014", "0015", "0016"}


def _adrs() -> dict:
    by_number = defaultdict(list)
    for p in sorted(DECISIONS.glob("[0-9][0-9][0-9][0-9]-*.md")):
        by_number[p.name[:4]].append(p.name)
    return by_number


def test_no_new_duplicate_adr_numbers():
    dupes = {n: f for n, f in _adrs().items()
             if len(f) > 1 and n not in GRANDFATHERED}
    assert not dupes, (
        "two ADRs share a number, so every 'see ADR-NNNN' reference to it is "
        f"ambiguous: {dupes}. Take the next free number — and if you are "
        "resolving one of the grandfathered duplicates instead, remove it from "
        "GRANDFATHERED in the same change.")


def test_grandfathered_list_stays_honest():
    """A grandfathered number that is no longer duplicated must leave the list,
    so it cannot hide a fresh collision on the same number."""
    by_number = _adrs()
    stale = sorted(n for n in GRANDFATHERED if len(by_number.get(n, [])) < 2)
    assert not stale, (
        f"GRANDFATHERED still lists {stale}, which is no longer duplicated — "
        "remove it, or it would mask a new collision on that number.")


# A migrated record whose heading kept its number from the source repository.
# Renaming it would break inbound references and is a separate decision.
HEADING_MISMATCH_OK = {"0016-gcp-native-mode.md"}


def test_heading_number_matches_the_filename():
    """A renamed file whose heading still carries the old number is worse than
    either, because the two then disagree about what the record is called.

    Several records open with a migration or superseded blockquote, so take the
    first `#` heading rather than the first line.
    """
    bad = []
    for p in sorted(DECISIONS.glob("[0-9][0-9][0-9][0-9]-*.md")):
        if p.name in HEADING_MISMATCH_OK:
            continue
        heading = next((l for l in p.read_text(encoding="utf-8").splitlines()
                        if l.lstrip().startswith("#")), None)
        m = re.match(r"#+\s*(?:ADR[- ])?0*(\d+)\b", heading or "")
        if not m:
            bad.append(f"{p.name}: no numbered heading found")
        elif int(m.group(1)) != int(p.name[:4]):
            bad.append(f"{p.name}: heading says {m.group(1)}")
    assert not bad, bad
