# test_gold_serving_sync.py
# The ONE serving-sync Lambda that covers every gold entity (UIFPE-518).
#
# The engine is tested in test_gold_serving.py. What this covers is the
# decisions the entry point makes: which entity an event is about, which
# events it acts on, which it declines, and that it refuses rather than
# guesses when the live config or master is missing.
#
# The config fixture is stock's REAL config shape, so a change to that shape
# breaks this rather than being discovered in dev.

import json
from pathlib import Path

import pytest

import gold_serving_sync as mod

STACK_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = STACK_ROOT.parents[1]
MASTER = json.loads(
    (REPO_ROOT / "domains/stock/src/schemas/gold/stock/v1/master.json")
    .read_text(encoding="utf-8"))

TRANSFORM = "uif-echo-dev-gold-transform"
RECONCILE = "uif-echo-dev-gold-reconcile"


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeDdb:
    def __init__(self, config_item=True, served=True):
        self.config_item = config_item
        self.served = served
        self.puts = []

    def get_item(self, TableName, Key):
        if not self.config_item:
            return {}
        item = {
            "gold_id": Key["gold_id"],
            "target": {"M": {
                "database": {"S": "uif_echo_{env}_gold_stock"},
                "table": {"S": "stock"},
                "primary_key_cols": {"L": [{"S": "productId"},
                                           {"S": "organizationCode"}]}}},
            "materialized_properties": {"L": [
                {"S": "availableToPromiseIndicator"},
                {"S": "availableToPromiseTimestamp"},
                {"S": "isbn13"}, {"S": "onHandQuantity"},
                {"S": "organizationCode"}, {"S": "productId"}]},
            "schema": {"M": {
                "version": {"S": "v1"},
                "master_s3_path": {
                    "S": "s3://b/schemas/gold/stock/v1/master.json"}}},
        }
        if self.served:
            item["serving"] = {"M": {"enabled": {"BOOL": True}}}
        return {"Item": item}

    def put_item(self, **kw):
        self.puts.append(kw)


class FakeS3:
    def __init__(self, master=True):
        self.master = master

    def get_object(self, Bucket, Key):
        if not self.master:
            raise RuntimeError("NoSuchKey")

        class B:
            @staticmethod
            def read():
                return json.dumps(MASTER).encode()
        return {"Body": B()}

    def list_objects_v2(self, **kw):
        return {}


class FakeGlue:
    def __init__(self, args):
        self.args = args
        self.calls = 0

    def get_job_run(self, JobName, RunId):
        self.calls += 1
        return {"JobRun": {"Arguments": self.args}}


class FakeAthena:
    def __init__(self):
        self.queries = []

    def start_query_execution(self, **kw):
        self.queries.append(kw["QueryString"])
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId):
        return {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}

    def get_query_results(self, **kw):
        return {"ResultSet": {"Rows": [
            {"Data": [{"VarCharValue": "productId"},
                      {"VarCharValue": "organizationCode"}]}]}}


class FakeLam:
    def __init__(self):
        self.sent = []

    def invoke(self, **kw):
        self.sent.append(json.loads(kw["Payload"]))


def _call(event, glue_args=None, config_item=True, served=True,
          master=True, athena=None, lam=None):
    return mod.handler(
        event, context=None,
        athena=athena or FakeAthena(), glue=FakeGlue(glue_args or {}),
        ddb=FakeDdb(config_item, served), lam=lam or FakeLam(),
        s3=FakeS3(master))


def _glue_event(job=TRANSFORM, state="SUCCEEDED", run="jr_1"):
    return {"detail": {"jobRunId": run, "jobName": job, "state": state}}


# ── which entity is this about ──────────────────────────────────────────────

def test_the_entity_comes_from_the_RUNS_OWN_argument():
    """The gold jobs are shared across every entity, so the job name says
    nothing about which entity finished. Only --gold_id does."""
    glue = FakeGlue({"--gold_id": "price"})
    assert mod.resolve_gold_id(_glue_event(), glue) == "price"
    assert glue.calls == 1


def test_an_explicit_gold_id_wins_and_costs_no_glue_call():
    """A self-chain continuation carries the entity, and its Glue run may be
    long finished - re-reading it would be both wasteful and fragile."""
    glue = FakeGlue({"--gold_id": "price"})
    assert mod.resolve_gold_id({"mode": "prune", "gold_id": "stock"},
                               glue) == "stock"
    assert glue.calls == 0


def test_an_event_naming_no_entity_is_declined():
    assert _call({"mode": "baseline"}) == {"skipped": "no gold_id in the event"}


# ── one Lambda, many entities: the opt-in is the gate ───────────────────────

def test_ANY_opted_in_entity_is_served_not_just_one():
    """The behavioural difference from a per-entity Lambda, and the reason
    adding an entity needs no infrastructure: a price event is handled by the
    same function, on the strength of price's own config."""
    out = _call(_glue_event(), glue_args={"--gold_id": "price"})
    assert "skipped" not in out


def test_an_entity_that_never_opted_in_is_DECLINED():
    """One Lambda sees every entity's run. Without this gate, registering any
    gold entity would silently start serving it."""
    out = _call(_glue_event(), glue_args={"--gold_id": "program"},
                served=False)
    assert out == {"skipped": "program is not served (serving.enabled)"}


# ── what it declines ────────────────────────────────────────────────────────

def test_a_failed_run_is_declined():
    out = _call(_glue_event(state="FAILED"), glue_args={"--gold_id": "stock"})
    assert "skipped" in out


def test_an_unrelated_job_is_declined_without_reading_the_config():
    out = _call(_glue_event(job="some-other-job"))
    assert out == {"skipped": "no gold_id in the event"}


def test_the_reconcile_job_is_ACCEPTED():
    """Reconcile is the one producer of gold deletions, so declining it would
    mean a deleted row is never removed from the serving copy."""
    out = _call(_glue_event(job=RECONCILE), glue_args={"--gold_id": "stock"})
    assert "skipped" not in out


def test_a_rebuild_run_is_declined_with_the_baseline_hint():
    """A rebuild REPLACEs the table and writes no change-log rows, so reading
    the change log for it would sync nothing while reporting success."""
    out = _call(_glue_event(),
                glue_args={"--gold_id": "stock", "--rebuild": "true"})
    assert out == {"skipped": "rebuild — baseline required"}


def test_a_run_id_that_fails_the_identifier_fence_RAISES():
    """The run id is interpolated into SQL and arrives from an EventBridge
    payload, so it is not ours until it passes the fence."""
    with pytest.raises(ValueError, match="identifier fence"):
        _call(_glue_event(run="jr_1'; DROP TABLE x--"),
              glue_args={"--gold_id": "stock"})


# ── refusing rather than guessing ───────────────────────────────────────────

def test_a_missing_config_item_REFUSES():
    with pytest.raises(RuntimeError, match="no config for gold_id"):
        _call({"mode": "baseline", "gold_id": "stock"}, config_item=False)


def test_a_config_without_a_master_path_REFUSES():
    """Without the master no property is cast, and an uncast timestamp fails
    the UNLOAD mid-query. Refusing up front beats dying halfway."""
    class NoMaster(FakeDdb):
        def get_item(self, TableName, Key):
            item = super().get_item(TableName, Key)["Item"]
            item["schema"] = {"M": {"version": {"S": "v1"}}}
            return {"Item": item}

    with pytest.raises(RuntimeError, match="master_s3_path"):
        mod.handler({"mode": "baseline", "gold_id": "stock"}, context=None,
                    athena=FakeAthena(), glue=FakeGlue({}), ddb=NoMaster(),
                    lam=FakeLam(), s3=FakeS3())


# ── the SQL it ends up running ──────────────────────────────────────────────

def test_baseline_unloads_without_a_change_log_join():
    athena = FakeAthena()
    _call({"mode": "baseline", "gold_id": "stock"}, athena=athena)
    unload = [q for q in athena.queries if q.startswith("UNLOAD")]
    assert unload and "stock_changes" not in unload[0]
    assert "`" not in unload[0], "Athena rejects backticks"


def test_a_delta_unloads_with_the_channel_filter():
    athena = FakeAthena()
    _call(_glue_event(run="jr_ok"), glue_args={"--gold_id": "stock"},
          athena=athena)
    unload = [q for q in athena.queries if q.startswith("UNLOAD")][0]
    assert "contains(changed_channels, 'api')" in unload
    assert "run_id = 'jr_ok'" in unload


def test_a_continuation_payload_NAMES_THE_ENTITY():
    """One Lambda serves every entity, so a self-chain leg that does not carry
    gold_id cannot know what it is resuming: it declines with "no gold_id" and
    a long baseline stops silently after leg one.

    Added because a mutation removing gold_id from the chained state SURVIVED
    the suite - nothing looked at the payload.
    """
    lam = FakeLam()
    _call({"mode": "baseline", "gold_id": "stock"}, lam=lam)
    assert lam.sent, "a baseline must hand off to the prune"
    assert lam.sent[0]["gold_id"] == "stock"
    assert lam.sent[0]["mode"] == "prune"


@pytest.mark.parametrize("env,expected,why", [
    ({}, 16, "the default"),
    ({"PUT_THREADS": "4"}, 4, "an explicit override is honoured"),
    ({"PUT_THREADS": "0"}, 1, "a zero pool would write NOTHING while the run "
                              "still reported success"),
    ({"PUT_THREADS": "-3"}, 1, "so would a negative"),
    ({"PUT_THREADS": "many"}, 16, "a typo falls back rather than crashing the "
                                  "whole sync at import time"),
])
def test_put_threads_parses_and_FLOORS_its_environment(env, expected, why):
    assert mod.put_threads(env) == expected, why


def test_the_handler_SIZES_THE_POOL_from_put_threads(monkeypatch):
    """Sizing the pool is only durable if it is DERIVED from the writer count.
    A hard-coded 16 in two places drifts the first time either moves, and the
    symptom is a log full of pool warnings that nobody reads."""
    seen = {}

    def spy(athena=None, glue=None, ddb=None, lam=None, s3=None, ddb_pool=None):
        seen["pool"] = ddb_pool
        return athena, glue, ddb, lam, s3

    # A NON-DEFAULT value, deliberately: with put_threads at its default of
    # 16 this test cannot tell a derived value from a hard-coded 16, and a
    # mutation replacing one with the other survived exactly that way.
    monkeypatch.setattr(mod, "PUT_THREADS", 7)
    monkeypatch.setattr(mod, "clients", spy)
    _call({"mode": "baseline", "gold_id": "stock"})
    assert seen["pool"] is not None, "the pool was left at botocore's default"
    assert seen["pool"] == 7 == mod.runtime().put_threads


# ── the runtime it builds ───────────────────────────────────────────────────

def test_one_shared_table_for_every_entity():
    rt = mod.runtime()
    assert rt.table_name == "uif-echo-dev-gold-serving"
    assert "stock" not in rt.table_name, (
        "a per-entity table name would make every new entity a Terraform apply")
    assert rt.output_s3.startswith("s3://uif-echo-dev-gold-artifacts/")


# ── the compress callable the engine's compression needs ────────────────────
# Every test above drives routing: which entity, accepted or declined. None
# reached the WRITE path, which is why nothing caught that this handler never
# supplied the `compress` callable at all. The engine's compression had no
# supplier, so the first entity to declare compress_prop (marketing_copy,
# UIFPE-767) died on its first row with "'NoneType' object is not callable" --
# in production, after a 2.97 GB Athena UNLOAD had already run.

def test_the_handler_SUPPLIES_a_compress_callable(monkeypatch):
    """Captured from the real call rather than grepped for: the handler is
    driven, and what it actually passes to the engine is inspected."""
    seen = {}

    def spy(event, spec, rt, **kw):
        seen.update(kw)
        return {"spied": True}

    monkeypatch.setattr(mod, "sync", spy)
    out = _call({"mode": "baseline", "gold_id": "stock"})
    assert out == {"spied": True}
    assert "compress" in seen, (
        "the handler called the engine without a compress argument at all — "
        "an entity declaring serving.compress_prop would die on its first row")
    assert callable(seen["compress"]), seen["compress"]


def test_the_compress_callable_IS_gzip(monkeypatch):
    """The reader calls gzip.decompress, so the writer must be gzip. Anything
    else stores bytes the API cannot read back."""
    import gzip as _gzip
    seen = {}
    monkeypatch.setattr(mod, "sync",
                        lambda event, spec, rt, **kw: seen.update(kw) or {})
    _call({"mode": "baseline", "gold_id": "stock"})
    payload = b"round trip me" * 100
    assert _gzip.decompress(seen["compress"](payload)) == payload


def test_build_item_REFUSES_BY_NAME_when_no_compress_is_supplied():
    """What the production failure should have said. The old behaviour was
    None(...) -> "'NoneType' object is not callable", naming neither the
    entity, the property, nor the caller's omission."""
    import gold_serving as gs

    spec = gs.ServingSpec(
        entity="marketing_copy", gold_db="d", gold_table="t",
        key_props=("contentId",), props=("contentId", "marketingCopyText"),
        channel_props=("contentId", "marketingCopyText"),
        compress_prop="marketingCopyText",
    )
    row = {"contentId": "MC1", "marketingCopyText": "some copy", "etag": "e"}
    with pytest.raises(ValueError, match="compress callable"):
        gs.build_item(spec, row, "tag", compress=None)


def test_an_entity_with_no_compressed_property_needs_no_compress():
    """The four entities served before this one pass nothing and must stay
    unaffected."""
    import gold_serving as gs

    spec = gs.ServingSpec(
        entity="stock", gold_db="d", gold_table="t",
        key_props=("productId", "organizationCode"),
        props=("productId", "organizationCode", "onHandQuantity"),
        channel_props=("productId", "organizationCode", "onHandQuantity"),
    )
    row = {"productId": "A1", "organizationCode": "01E",
           "onHandQuantity": 644, "etag": "e"}
    item = gs.build_item(spec, row, "tag", compress=None)
    assert item["onHandQuantity"] == {"N": "644"}
