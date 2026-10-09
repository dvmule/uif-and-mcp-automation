"""Scheduler dispatcher daily slot filtering (no AWS calls)."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "lambda"))

import scheduler_dispatcher as sd  # noqa: E402


def _daily_pipeline(pid: str, slot: int | None) -> dict:
    sched = {"frequency": "daily"}
    if slot is not None:
        sched["daily_slot"] = slot
    return {
        "pipeline_id": pid,
        "enabled": True,
        "version": "latest",
        "schedule": sched,
        "glue": {"worker_type": "G.1X", "num_workers": 2, "timeout_minutes": 60},
    }


@patch.object(sd, "glue")
@patch.object(sd, "dynamo")
def test_daily_slot_filters_pipelines(mock_dynamo, mock_glue):
    table = MagicMock()
    mock_dynamo.Table.return_value = table
    mock_glue.get_paginator.return_value.paginate.return_value = [{"JobRuns": []}]
    mock_glue.start_job_run.return_value = {"JobRunId": "jr-1"}

    items = [
        _daily_pipeline("slot-0-default", None),
        _daily_pipeline("slot-3", 3),
        _daily_pipeline("hourly-only", 0) | {"schedule": {"frequency": "hourly"}},
    ]

    with patch.object(sd, "_scan_config_items", return_value=items):
        with patch.object(sd, "_dispatch_dq", return_value=([], [])):
            result = sd.handler({"frequency": "daily", "daily_slot": 3}, None)

    assert result["daily_slot"] == 3
    assert result["triggered_count"] == 1
    assert result["triggered"][0]["pipeline_id"] == "slot-3"
    args = mock_glue.start_job_run.call_args.kwargs["Arguments"]
    assert args["--pipeline_id"] == "slot-3"
    assert args["--env"] == sd.ENV


@patch.object(sd, "glue")
@patch.object(sd, "dynamo")
def test_four_hourly_frequency_filters_pipelines(mock_dynamo, mock_glue):
    table = MagicMock()
    mock_dynamo.Table.return_value = table
    mock_glue.get_paginator.return_value.paginate.return_value = [{"JobRuns": []}]
    mock_glue.start_job_run.return_value = {"JobRunId": "jr-4hr"}

    items = [
        {
            "pipeline_id": "four-hr-pipeline",
            "enabled": True,
            "version": "latest",
            "schedule": {"frequency": "4hr"},
            "glue": {"worker_type": "G.1X", "num_workers": 2, "timeout_minutes": 60},
        },
        _daily_pipeline("hourly-only", 0) | {"schedule": {"frequency": "hourly"}},
    ]

    with patch.object(sd, "_scan_config_items", return_value=items):
        with patch.object(sd, "_dispatch_dq", return_value=([], [])):
            result = sd.handler({"frequency": "4hr"}, None)

    assert result["frequency"] == "4hr"
    assert result["triggered_count"] == 1
    assert result["triggered"][0]["pipeline_id"] == "four-hr-pipeline"


@patch.object(sd, "glue")
@patch.object(sd, "dynamo")
def test_legacy_daily_event_defaults_to_slot_zero(mock_dynamo, mock_glue):
    table = MagicMock()
    mock_dynamo.Table.return_value = table
    mock_glue.get_paginator.return_value.paginate.return_value = [{"JobRuns": []}]
    mock_glue.start_job_run.return_value = {"JobRunId": "jr-2"}

    items = [
        _daily_pipeline("legacy-default", None),
        _daily_pipeline("other-slot", 5),
    ]

    with patch.object(sd, "_scan_config_items", return_value=items):
        with patch.object(sd, "_dispatch_dq", return_value=([], [])):
            result = sd.handler({"frequency": "daily"}, None)

    assert result["daily_slot"] == 0
    assert result["triggered_count"] == 1
    assert result["triggered"][0]["pipeline_id"] == "legacy-default"


def _reconcile_pipeline(pid: str, slot: int | None, enabled: bool = True) -> dict:
    sched = {"frequency": "daily"}
    if slot is not None:
        sched["daily_slot"] = slot
    return {
        "pipeline_id": pid,
        "enabled": True,
        "version": "latest",
        "schedule": sched,
        "reconcile": {"enabled": enabled},
        "destination": {"cloud": "gcp"},
        "silver": {"write_mode": "bq_native"},
        "glue": {"worker_type": "G.1X", "num_workers": 2, "timeout_minutes": 60},
    }


@patch.object(sd, "glue")
@patch.object(sd, "dynamo")
def test_reconcile_slot_filters_pipelines(mock_dynamo, mock_glue):
    mock_dynamo.Table.return_value = MagicMock()
    mock_glue.get_paginator.return_value.paginate.return_value = [{"JobRuns": []}]
    mock_glue.start_job_run.return_value = {"JobRunId": "jr-rec-1"}

    items = [
        _reconcile_pipeline("slot-2", 2),
        _reconcile_pipeline("slot-5", 5),
        _reconcile_pipeline("disabled", 2, enabled=False),
    ]

    with patch.object(sd, "_scan_config_items", return_value=items):
        result = sd.handler({"frequency": "reconcile", "daily_slot": 2}, None)

    assert result["daily_slot"] == 2
    assert result["triggered_count"] == 1
    assert result["triggered"][0]["pipeline_id"] == "slot-2"
    args = mock_glue.start_job_run.call_args.kwargs["Arguments"]
    assert args["--env"] == sd.ENV


@patch.object(sd, "glue")
@patch.object(sd, "dynamo")
def test_legacy_reconcile_event_defaults_to_slot_zero(mock_dynamo, mock_glue):
    mock_dynamo.Table.return_value = MagicMock()
    mock_glue.get_paginator.return_value.paginate.return_value = [{"JobRuns": []}]
    mock_glue.start_job_run.return_value = {"JobRunId": "jr-rec-0"}

    items = [
        _reconcile_pipeline("legacy-default", None),
        _reconcile_pipeline("other-slot", 4),
    ]

    with patch.object(sd, "_scan_config_items", return_value=items):
        result = sd.handler({"frequency": "reconcile"}, None)

    assert result["daily_slot"] == 0
    assert result["triggered_count"] == 1
    assert result["triggered"][0]["pipeline_id"] == "legacy-default"
