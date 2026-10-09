"""Daily Bronze and Silver reconcile dispatch windows in UTC.

Bronze window: 05:30 UTC (slot 0) through 11:00 UTC (slot 11), every 30 minutes.
Reconcile window: 06:00 UTC (slot 0) through 11:30 UTC (slot 11), +30 min after Bronze.
Used by scheduler_dispatcher and assign_daily_slots migration script.
"""

from __future__ import annotations

DAILY_SLOT_COUNT = 12
DAILY_WINDOW_START_UTC_MINUTES = 5 * 60 + 30  # 05:30 UTC
RECONCILE_WINDOW_START_UTC_MINUTES = 6 * 60  # 06:00 UTC (+30 min after Bronze slot 0)


def slot_utc_minutes(slot: int) -> int:
    if slot < 0 or slot >= DAILY_SLOT_COUNT:
        raise ValueError(f"daily_slot must be 0..{DAILY_SLOT_COUNT - 1}, got {slot}")
    return DAILY_WINDOW_START_UTC_MINUTES + slot * 30


def slot_utc_hhmm(slot: int) -> tuple[int, int]:
    total = slot_utc_minutes(slot)
    return total // 60, total % 60


def cron_for_slot(slot: int) -> str:
    hour, minute = slot_utc_hhmm(slot)
    return f"cron({minute} {hour} * * ? *)"


def resolve_pipeline_daily_slot(schedule: dict | None) -> int | None:
    """Return effective daily_slot for a daily pipeline; None if not daily."""
    sched = schedule or {}
    if sched.get("frequency") != "daily":
        return None
    raw = sched.get("daily_slot")
    if raw is None:
        return 0
    return int(raw)


def pipeline_matches_daily_slot(schedule: dict | None, event_slot: int) -> bool:
    """True when pipeline should run on this daily slot tick."""
    effective = resolve_pipeline_daily_slot(schedule)
    if effective is None:
        return False
    return effective == event_slot


def reconcile_slot_utc_minutes(slot: int) -> int:
    if slot < 0 or slot >= DAILY_SLOT_COUNT:
        raise ValueError(f"daily_slot must be 0..{DAILY_SLOT_COUNT - 1}, got {slot}")
    return RECONCILE_WINDOW_START_UTC_MINUTES + slot * 30


def reconcile_slot_utc_hhmm(slot: int) -> tuple[int, int]:
    total = reconcile_slot_utc_minutes(slot)
    return total // 60, total % 60


def cron_for_reconcile_slot(slot: int) -> str:
    hour, minute = reconcile_slot_utc_hhmm(slot)
    return f"cron({minute} {hour} * * ? *)"


def pipeline_matches_reconcile_slot(schedule: dict | None, event_slot: int) -> bool:
    """Reconcile reuses schedule.daily_slot; runs +30 min after the Bronze slot."""
    return pipeline_matches_daily_slot(schedule, event_slot)
