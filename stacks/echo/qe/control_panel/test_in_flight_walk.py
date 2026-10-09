# test_in_flight_walk.py
# UIFPE-565: the controller's in-flight Glue run walk is BOUNDED. Every runs
# route and the gold run guard call it; unbounded it paged a job's whole history
# (13 pages, ~12 s for bronze-ingest) once per Syncs row every 30 s and starved
# the console. These tests count the pages the walk consumes.

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture
def pc():
    with mock.patch("boto3.resource"), mock.patch("boto3.client"), \
         mock.patch.dict(os.environ, {"GLUE_JOB_NAME": "uif-echo-dev-bronze-ingest"}):
        sys.modules.pop("pipeline_controller", None)
        module = importlib.import_module("pipeline_controller")
    module.datetime = FrozenDatetime
    yield module
    sys.modules.pop("pipeline_controller", None)


def run(state, started_min_ago, pipeline="p1", run_id=None, started=True):
    r = {"Id": run_id or f"jr_{state}_{started_min_ago}_{pipeline}", "JobRunState": state,
         "Arguments": {"--pipeline_id": pipeline}}
    if started:
        r["StartedOn"] = NOW - timedelta(minutes=started_min_ago)
    return r


class CountingPaginator:
    """Yields the given pages lazily and records how many were pulled."""

    def __init__(self, pages):
        self.pages, self.pulled = pages, 0

    def paginate(self, **_):
        for page in self.pages:
            self.pulled += 1
            yield {"JobRuns": page}


def wire(pc, pages):
    pag = CountingPaginator(pages)
    pc.glue = mock.MagicMock()
    pc.glue.get_paginator.return_value = pag
    return pag


def test_walk_stops_at_the_first_page_older_than_the_horizon(pc):
    """Newest first: page 1 is today, page 2 straddles the horizon (its newest
    run is inside it, so it is read), page 3 is the first page entirely past
    it (read, then the walk stops). Pages 4..13 are never pulled."""
    pages = [
        [run("SUCCEEDED", 30), run("RUNNING", 45, pipeline="p2"), run("SUCCEEDED", 90)],
        [run("SUCCEEDED", 23 * 60), run("SUCCEEDED", 26 * 60)],           # newest run inside the horizon
        [run("SUCCEEDED", 30 * 60), run("RUNNING", 31 * 60, pipeline="p1")],  # all past it - stop here
    ] + [[run("SUCCEEDED", d * 60)] for d in range(40, 300, 24)]
    pag = wire(pc, pages)
    out = pc._in_flight_glue_runs("uif-echo-dev-bronze-ingest", "p2")
    assert [r["run_id"] for r in out] == ["jr_RUNNING_45_p2"]
    assert pag.pulled == 3
    assert len(pages) == 14  # so 11 pages of history were skipped


def test_a_page_entirely_past_the_horizon_ends_the_walk(pc):
    pages = [[run("SUCCEEDED", 25 * 60), run("SUCCEEDED", 26 * 60)], [run("RUNNING", 27 * 60)]]
    pag = wire(pc, pages)
    assert pc._in_flight_glue_runs("uif-echo-dev-bronze-ingest", "p1") == []
    assert pag.pulled == 1


def test_a_waiting_run_without_started_on_never_stops_the_walk(pc):
    pages = [
        [run("WAITING", 0, started=False, run_id="jr_wait")],   # no StartedOn at all
        [run("RUNNING", 10, pipeline="p1", run_id="jr_run")],
    ]
    pag = wire(pc, pages)
    out = pc._in_flight_glue_runs("uif-echo-dev-bronze-ingest", "p1")
    assert [r["run_id"] for r in out] == ["jr_wait", "jr_run"]
    assert pag.pulled == 2


def test_horizon_is_a_day_far_beyond_any_glue_timeout(pc):
    # bronze/silver 120 min, gold 180-240 min; the horizon leaves 6x headroom
    assert pc.IN_FLIGHT_HORIZON_MINUTES == 1440


def test_entity_filter_and_states_unchanged(pc):
    pages = [[run("RUNNING", 5, pipeline="p1"), run("STARTING", 6, pipeline="p2"),
              run("SUCCEEDED", 7, pipeline="p1"), run("STOPPING", 8, pipeline="p1")]]
    wire(pc, pages)
    out = pc._in_flight_glue_runs("uif-echo-dev-bronze-ingest", "p1")
    assert sorted(r["status"] for r in out) == ["RUNNING", "STOPPING"]
    out_all = pc._in_flight_glue_runs("uif-echo-dev-bronze-ingest", None)
    assert len(out_all) == 3


def test_runs_route_reads_one_page_for_a_busy_job(pc):
    """The route the Syncs page calls 93 times per refresh."""
    table = mock.MagicMock()
    table.query.return_value = {"Items": [{"run_id": "jr_done", "status": "SUCCEEDED"}]}
    pc.dynamo = mock.MagicMock(); pc.dynamo.Table.return_value = table
    pages = [[run("RUNNING", 3, pipeline="p9", run_id="jr_live"), run("SUCCEEDED", 20)]] \
        + [[run("SUCCEEDED", 30 * 60 + i)] for i in range(12)]
    pag = wire(pc, pages)
    resp = pc.list_runs("p9")
    assert resp["statusCode"] == 200
    assert pag.pulled <= 2
    body = __import__("json").loads(resp["body"])
    assert [r["run_id"] for r in body["runs"]] == ["jr_live", "jr_done"]


def test_a_glue_failure_still_returns_what_was_read(pc):
    class Boom(CountingPaginator):
        def paginate(self, **_):
            yield {"JobRuns": [run("RUNNING", 1, pipeline="p1", run_id="jr_a")]}
            raise RuntimeError("ThrottlingException")
    pc.glue = mock.MagicMock(); pc.glue.get_paginator.return_value = Boom([])
    out = pc._in_flight_glue_runs("uif-echo-dev-bronze-ingest", "p1")
    assert [r["run_id"] for r in out] == ["jr_a"]
