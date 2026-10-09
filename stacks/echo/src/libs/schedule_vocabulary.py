"""The closed cadence vocabulary — single source of truth.

Every value in ``DISPATCHABLE_FREQUENCIES`` is a payload that an EventBridge
rule in ``infrastructure/terraform/scheduler.tf`` actually sends. Nothing else
is. ``qe/libs/test_schedule_vocabulary.py`` reads that Terraform and asserts the
two sets are equal in both directions, so a rule added without a vocabulary
entry fails, and so does an entry no rule dispatches.

**Matching is exact.** ``scheduler_dispatcher`` selects pipelines with
``schedule.get("frequency") != frequency`` against the literal payload — no
``.lower()``, no ``.strip()``, in either the Bronze/Silver branch or the Gold
one. So ``"Hourly"``, ``"hourly "`` and ``"4 hr"`` are all values that would
pass a forgiving check and then never dispatch. Accepting them would only make
the failure quieter, which is the opposite of the point: this module exists
because a live enabled dev pipeline holds ``"12 hr"`` and has never run.

The vocabulary was previously written out by hand in six places, no two of them
agreeing — see the header of the test file for the table.
"""

from __future__ import annotations

# ── Bronze and Silver: schedule-driven ticks ──────────────────────
# 30min / hourly / 4hr / weekly fire on a fixed cron; `daily` fires twelve
# staggered slots (see daily_schedule.py) and carries schedule.daily_slot.
BRONZE_SILVER_FREQUENCIES = ("30min", "hourly", "4hr", "daily", "weekly")

# ── Silver reconcile: hard-delete detection, +30 min after the Bronze slot ──
# Reuses schedule.daily_slot rather than defining its own.
RECONCILE_FREQUENCIES = ("reconcile",)

# Gold cadences (gold-5m … gold-24h) are the gold dispatcher's vocabulary —
# libs/gold_frequencies.py — and never reach this Lambda (ADR-0018).
DISPATCHABLE_FREQUENCIES = BRONZE_SILVER_FREQUENCIES + RECONCILE_FREQUENCIES

# Cadences frequent enough that a fat incremental Glue profile is a cost bug.
# pipeline_controller's sizing guard and the UI's copy of it both read this.
HIGH_CADENCE_FREQUENCIES = ("30min", "hourly", "4hr")

_DISPATCHABLE = frozenset(DISPATCHABLE_FREQUENCIES)


def is_dispatchable(frequency: object) -> bool:
    """True when some EventBridge rule sends exactly this string.

    The isinstance guard is load-bearing, not defensive noise: the argument
    comes straight out of a JSON request body, so `{"frequency": []}` is
    reachable, and `[] in frozenset(...)` raises TypeError — which would 500
    the Lambda on input the validator exists to reject with a 400.
    """
    return isinstance(frequency, str) and frequency in _DISPATCHABLE


def is_scheduled(frequency: object) -> bool:
    """True when the config asks to be scheduled at all.

    An absent or empty frequency is legitimate: adhoc pipelines are driven by
    ``POST /pipelines/{id}/run`` and must stay registerable.
    """
    return isinstance(frequency, str) and frequency.strip() != ""


def unknown_frequency_error(frequency: object) -> str | None:
    """Return a caller-facing error, or None when the value is acceptable.

    Acceptable means either absent (unscheduled) or exactly dispatchable.
    """
    if not is_scheduled(frequency):
        return None
    if is_dispatchable(frequency):
        return None

    hint = ""
    if isinstance(frequency, str):
        near = frequency.strip().lower().replace(" ", "").replace("_", "-")
        for canonical in DISPATCHABLE_FREQUENCIES:
            if near == canonical.replace("-", ""):
                hint = f' Did you mean "{canonical}"?'
                break
        else:
            if near in _DISPATCHABLE:
                hint = f' Did you mean "{near}"? Matching is case-sensitive.'

    return (
        f'schedule.frequency="{frequency}" is not dispatched by any EventBridge '
        f"rule, so this pipeline would never run.{hint} "
        f"Expected exactly one of: {', '.join(DISPATCHABLE_FREQUENCIES)} "
        f"(or omit schedule.frequency for an adhoc pipeline)."
    )
