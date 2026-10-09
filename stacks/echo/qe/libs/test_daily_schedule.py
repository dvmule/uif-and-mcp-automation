"""Unit tests for daily_schedule slot helpers."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "lambda"))

from daily_schedule import (  # noqa: E402
    DAILY_SLOT_COUNT,
    cron_for_reconcile_slot,
    cron_for_slot,
    pipeline_matches_daily_slot,
    pipeline_matches_reconcile_slot,
    reconcile_slot_utc_hhmm,
    resolve_pipeline_daily_slot,
    slot_utc_hhmm,
)


def test_slot_window_endpoints():
    assert slot_utc_hhmm(0) == (5, 30)
    assert slot_utc_hhmm(11) == (11, 0)
    assert DAILY_SLOT_COUNT == 12


def test_cron_for_slot():
    assert cron_for_slot(0) == "cron(30 5 * * ? *)"
    assert cron_for_slot(11) == "cron(0 11 * * ? *)"


def test_missing_daily_slot_defaults_to_zero():
    assert resolve_pipeline_daily_slot({"frequency": "daily"}) == 0
    assert pipeline_matches_daily_slot({"frequency": "daily"}, 0) is True
    assert pipeline_matches_daily_slot({"frequency": "daily"}, 1) is False


def test_explicit_daily_slot():
    sched = {"frequency": "daily", "daily_slot": 4}
    assert resolve_pipeline_daily_slot(sched) == 4
    assert pipeline_matches_daily_slot(sched, 4) is True
    assert pipeline_matches_daily_slot(sched, 3) is False


def test_non_daily_schedule():
    assert resolve_pipeline_daily_slot({"frequency": "hourly"}) is None
    assert pipeline_matches_daily_slot({"frequency": "hourly"}, 0) is False
    assert pipeline_matches_reconcile_slot({"frequency": "hourly"}, 0) is False


def test_reconcile_slot_window():
    assert reconcile_slot_utc_hhmm(0) == (6, 0)
    assert reconcile_slot_utc_hhmm(11) == (11, 30)
    assert cron_for_reconcile_slot(0) == "cron(0 6 * * ? *)"
    assert cron_for_reconcile_slot(11) == "cron(30 11 * * ? *)"


def test_reconcile_slot_matches_daily_slot():
    sched = {"frequency": "daily", "daily_slot": 2}
    assert pipeline_matches_reconcile_slot(sched, 2) is True
    assert pipeline_matches_reconcile_slot(sched, 1) is False
