# Marketing-copy content service (UIFPE-254) — the sync materializer and the
# GET API, tested whole with stubbed clients. Every adversarial-round finding
# is pinned here: reconcile-driven deletions, attempt-unique UNLOAD retries,
# mid-file chain resume, conditional puts that suppress stale writes, the
# post-baseline prune, the content-hash ETag surviving silent maintenance,
# and the auth check that never disarms on a fetch error.
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import jwt_verify
import pytest

REPO = Path(__file__).resolve().parents[3]
APPS = REPO / "domains/product/src/apps"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, APPS / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sync = _load("marketing_copy_sync",
             "lambda_marketing_copy_sync/marketing_copy_sync.py")
api = _load("marketing_copy_api",
            "lambda_marketing_copy_api/marketing_copy_api.py")


class FakeUuid:
    class _U:
        hex = "f" * 32

    @staticmethod
    def uuid4():
        return FakeUuid._U()


@pytest.fixture(autouse=True)
def deterministic_env(monkeypatch):
    monkeypatch.setattr(sync, "uuid", FakeUuid)
    monkeypatch.setattr(sync, "UNLOAD_S3",
                        "s3://bkt/marketing-copy-sync/unload")


def gz_lines(rows: list) -> bytes:
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return buf.getvalue()


class ConditionalCheckFailedException(Exception):
    pass


class StubAthena:
    def __init__(self, delete_ids=()):
        self.queries = []
        self._delete_ids = list(delete_ids)

    def start_query_execution(self, **kw):
        self.queries.append(kw["QueryString"])
        return {"QueryExecutionId": f"q{len(self.queries)}"}

    def get_query_execution(self, QueryExecutionId):
        return {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}

    def stop_query_execution(self, QueryExecutionId):
        pass

    def get_query_results(self, QueryExecutionId, MaxResults, **kw):
        rows = [{"Data": [{"VarCharValue": "content_id"}]}] + [
            {"Data": [{"VarCharValue": i}]} for i in self._delete_ids]
        return {"ResultSet": {"Rows": rows}}


class StubDdb:
    """Conditional-put-aware store: goldUpdatedAt may only move forward."""

    def __init__(self, seed=None):
        self.store = dict(seed or {})
        self.deletes = []
        self.scans = 0

    def put_item(self, TableName, Item, **kw):
        cid = Item["contentId"]["S"]
        if "ConditionExpression" in kw:
            existing = self.store.get(cid, {}).get(
                "goldUpdatedAt", {}).get("S")
            new = Item.get("goldUpdatedAt", {}).get("S")
            if existing is not None and new is not None and existing > new:
                raise ConditionalCheckFailedException("stale")
        self.store[cid] = Item

    def batch_write_item(self, RequestItems):
        for req in RequestItems[sync.TABLE_NAME]:
            key = req["DeleteRequest"]["Key"]
            self.deletes.append(key)
            self.store.pop(key["contentId"]["S"], None)
        return {"UnprocessedItems": {}}

    def scan(self, **kw):
        self.scans += 1
        tag = kw["ExpressionAttributeValues"][":tag"]["S"]
        start = kw["ExpressionAttributeValues"][":start"]["S"]
        items = [
            {"contentId": it["contentId"]}
            for it in self.store.values()
            if it.get("syncTag", {}).get("S") != tag
            and it.get("syncedAt", {}).get("S", "") < start
        ]
        return {"Items": items}


class StubS3:
    def __init__(self, files: dict):
        self.files = dict(files)
        self.deleted = []

    def list_objects_v2(self, Bucket, Prefix, **kw):
        return {"Contents": [{"Key": k, "Size": len(v)}
                             for k, v in sorted(self.files.items())
                             if k.startswith(Prefix)]}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.files[Key])}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.files.pop(Key, None)


class StubGlue:
    def __init__(self, gold_id="marketing_copy", extra_args=None):
        self.args = {"--gold_id": gold_id, **(extra_args or {})}
        self.asked = []

    def get_job_run(self, JobName, RunId):
        self.asked.append(JobName)
        return {"JobRun": {"Arguments": self.args}}


class StubLambda:
    def __init__(self):
        self.invocations = []

    def invoke(self, FunctionName, InvocationType, Payload):
        self.invocations.append(json.loads(Payload))


class FakeContext:
    def __init__(self, ms):
        self._ms = ms

    def get_remaining_time_in_millis(self):
        return self._ms


def glue_event(run_id="jr_abc123", state="SUCCEEDED",
               job="uif-echo-dev-gold-transform"):
    return {"source": "aws.glue", "detail-type": "Glue Job State Change",
            "detail": {"jobName": job, "jobRunId": run_id, "state": state}}


# the attempt-unique dest the FakeUuid produces
DELTA_PREFIX = "marketing-copy-sync/unload/jr_abc123-ffffffff/data/"

ROW = {"content_id": "MC1", "marketing_copy_type_code": "TOC", "owning_market_code": "US",
       "audience_code": "Educator", "marketing_copy_text": "d", "marketing_copy_content_text": "<p>hello</p>",
       "updated": "2026-09-03 07:45:00.000"}


def run_sync(rows, delete_ids=(), glue=None, event=None, ddb=None,
             ms=800_000, files=None, lam=None):
    s3 = StubS3(files if files is not None
                else {DELTA_PREFIX + "f1.gz": gz_lines(rows)})
    athena, lam = StubAthena(delete_ids), lam or StubLambda()
    ddb = ddb if ddb is not None else StubDdb()
    out = sync.handler(event if event is not None else glue_event(),
                       FakeContext(ms), athena=athena,
                       glue=glue or StubGlue(), ddb=ddb, lam=lam, s3=s3)
    return out, athena, ddb, lam, s3


class TestSyncDelta:
    def test_delta_upserts_gzipped_with_content_hash(self):
        out, athena, ddb, _, s3 = run_sync([ROW])
        assert out["synced"] == 1 and out["continued"] == 0
        item = ddb.store["MC1"]
        raw = gzip.decompress(item["contentGz"]["B"])
        assert raw == b"<p>hello</p>"
        assert item["contentHash"]["S"] == hashlib.md5(raw).hexdigest()
        assert item["syncTag"]["S"] == "jr_abc123"
        assert "run_id = 'jr_abc123'" in athena.queries[0]
        # attempt-unique destination, and the processed file was deleted
        assert "jr_abc123-ffffffff" in athena.queries[0]
        assert s3.deleted == [DELTA_PREFIX + "f1.gz"]

    def test_stale_write_is_suppressed_never_regresses(self):
        fresh = {"contentId": {"S": "MC1"},
                 "goldUpdatedAt": {"S": "2026-09-09 00:00:00.000"},
                 "contentGz": {"B": gzip.compress(b"newer")}}
        ddb = StubDdb(seed={"MC1": fresh})
        out, _, ddb, _, _ = run_sync([ROW], ddb=ddb)   # ROW is older
        assert out["synced"] == 0 and out["suppressed_stale"] == 1
        assert gzip.decompress(ddb.store["MC1"]["contentGz"]["B"]) == b"newer"

    def test_equal_timestamp_replay_wins_idempotently(self):
        # silent maintenance repairs content WITHOUT moving the timestamp —
        # an equal-timestamp write must land, not be suppressed
        same_ts = {"contentId": {"S": "MC1"},
                   "goldUpdatedAt": {"S": ROW["updated"]},
                   "contentGz": {"B": gzip.compress(b"old-content")}}
        out, _, ddb, _, _ = run_sync([ROW], ddb=StubDdb(seed={"MC1": same_ts}))
        assert out["synced"] == 1
        assert gzip.decompress(ddb.store["MC1"]["contentGz"]["B"]) \
            == b"<p>hello</p>"

    def test_reconcile_job_events_sync_and_deletions_land(self):
        glue = StubGlue()
        out, athena, ddb, _, _ = run_sync(
            [ROW], delete_ids=["GONE1", "GONE2"], glue=glue,
            event=glue_event(job="uif-echo-dev-gold-reconcile"))
        assert glue.asked == ["uif-echo-dev-gold-reconcile"]
        assert out["deleted"] == 2
        assert {d["contentId"]["S"] for d in ddb.deletes} == {"GONE1", "GONE2"}
        assert "IS NULL" in athena.queries[1]   # deletion by absence

    def test_foreign_job_names_are_refused(self):
        out, athena, _, _, _ = run_sync(
            [ROW], event=glue_event(job="echo-dev-something-else"))
        assert out == {"skipped": "not a succeeded gold-job event"}
        assert athena.queries == []

    def test_null_content_row_keeps_metadata_without_blob_or_hash(self):
        out, _, ddb, _, _ = run_sync([{**ROW, "marketing_copy_content_text": None}])
        assert out["synced"] == 1
        assert "contentGz" not in ddb.store["MC1"]
        assert "contentHash" not in ddb.store["MC1"]

    def test_oversized_even_compressed_is_refused_not_truncated(self, capsys):
        keep = sync.MAX_ITEM_BYTES
        try:
            # below gzip's ~20-byte floor, so ANY compressed content refuses
            sync.MAX_ITEM_BYTES = 10
            out, _, ddb, _, _ = run_sync(
                [ROW, {**ROW, "content_id": "OK", "marketing_copy_content_text": None}])
        finally:
            sync.MAX_ITEM_BYTES = keep
        assert out["synced"] == 1 and "OK" in ddb.store
        assert "MC1" not in ddb.store
        assert "REFUSED contentId=MC1" in capsys.readouterr().out

    def test_other_entities_runs_are_skipped(self):
        out, athena, _, _, _ = run_sync([ROW], glue=StubGlue("product"))
        assert out == {"skipped": "gold_id 'product'"}
        assert athena.queries == []

    def test_rebuild_checks_the_value_not_key_presence(self, capsys):
        out, athena, _, _, _ = run_sync(
            [ROW], glue=StubGlue(extra_args={"--rebuild": "true"}))
        assert out == {"skipped": "rebuild — baseline required"}
        assert athena.queries == []
        # an explicit --rebuild false is a NORMAL incremental run
        out2, athena2, _, _, _ = run_sync(
            [ROW], glue=StubGlue(extra_args={"--rebuild": "false"}))
        assert out2["synced"] == 1
        void = capsys.readouterr()

    def test_non_succeeded_or_malformed_events_no_op(self):
        for ev in (glue_event(state="FAILED"), {"detail": {}}, {}):
            out, athena, _, _, _ = run_sync([ROW], event=ev)
            assert "skipped" in out and athena.queries == []

    def test_hostile_run_id_is_fenced_before_any_sql(self):
        with pytest.raises(ValueError):
            run_sync([ROW], event=glue_event(run_id="x' OR '1'='1"))


class TestSyncBaselineAndChain:
    def test_baseline_unloads_unfiltered_then_chains_into_prune(self):
        files = {"marketing-copy-sync/unload/baseline-ffffffffffff-ffffffff/"
                 "data/a.gz": gz_lines([ROW])}
        out, athena, ddb, lam, _ = run_sync(
            [], event={"mode": "baseline"}, files=files)
        assert len(athena.queries) == 1            # UNLOAD only, no deletes
        head = athena.queries[0].split(" TO ")[0]
        assert "WHERE" not in head                 # unfiltered
        assert out["synced"] == 1
        assert out["continued"] == 1               # the prune chain
        assert lam.invocations[0]["mode"] == "prune"
        assert lam.invocations[0]["prune_started"] is True

    def test_prune_removes_orphans_but_keeps_fresh_delta_writes(self):
        tag, start = "baseline-x", "2026-09-03T10:00:00.000000"
        seed = {
            "ORPHAN": {"contentId": {"S": "ORPHAN"},
                       "syncTag": {"S": "jr_old"},
                       "syncedAt": {"S": "2026-09-01T00:00:00.000000"}},
            "MINE": {"contentId": {"S": "MINE"}, "syncTag": {"S": tag},
                     "syncedAt": {"S": "2026-09-03T11:00:00.000000"}},
            "FRESH_DELTA": {"contentId": {"S": "FRESH_DELTA"},
                            "syncTag": {"S": "jr_newer"},
                            "syncedAt": {"S": "2026-09-03T12:00:00.000000"}},
        }
        ddb = StubDdb(seed=seed)
        out = sync.handler({"mode": "prune", "sync_tag": tag,
                            "baseline_start": start, "prune_started": True},
                           FakeContext(800_000), athena=StubAthena(),
                           glue=StubGlue(), ddb=ddb, lam=StubLambda(),
                           s3=StubS3({}))
        assert out["pruned"] == 1
        assert "ORPHAN" not in ddb.store
        assert "MINE" in ddb.store and "FRESH_DELTA" in ddb.store

    def test_low_budget_pauses_mid_file_and_resumes_at_the_offset(self):
        rows = [dict(ROW, content_id=f"MC{i}") for i in range(5)]
        files = {DELTA_PREFIX + "a.gz": gz_lines(rows)}
        keep = sync.BATCH_LINES
        try:
            sync.BATCH_LINES = 2      # force multiple flushes per file
            out, _, ddb, lam, s3 = run_sync([], files=files, ms=60_000)
            assert out["continued"] >= 1
            cont = lam.invocations[0]
            assert cont["mode"] == "process"
            assert cont["line_offset"] > 0        # paused INSIDE the file
            assert s3.deleted == []               # unfinished file kept
            # the continuation finishes and deletes the file
            out2 = sync.handler(cont, FakeContext(800_000),
                                athena=StubAthena(), glue=StubGlue(),
                                ddb=ddb, lam=StubLambda(), s3=s3)
        finally:
            sync.BATCH_LINES = keep
        assert out2["continued"] == 0
        assert len(ddb.store) == 5
        assert s3.deleted == [DELTA_PREFIX + "a.gz"]


# ── the GET API ──────────────────────────────────────────────────────────────

CONTENT = b"<p>full copy</p>"
HASH = hashlib.md5(CONTENT).hexdigest()


class StubServingDdb:
    def __init__(self, items: dict):
        self.items = items
        self.lookups = []

    def get_item(self, TableName, Key):
        self.lookups.append(Key["contentId"]["S"])
        item = self.items.get(Key["contentId"]["S"])
        return {"Item": item} if item else {}


class StubSecrets:
    """value=None -> ResourceNotFound (disabled); mode='error' -> transient
    failure (must NOT disarm); else returns the value."""

    def __init__(self, value=None, mode="ok"):
        self._value, self.mode = value, mode

    def get_secret_value(self, SecretId):
        if self.mode == "error":
            raise RuntimeError("throttled")
        if self._value is None:
            raise type("ResourceNotFoundException", (Exception,), {})("gone")
        return {"SecretString": self._value}


def served_item(content=CONTENT, updated="2026-09-03 07:45:00.000",
                with_hash=True):
    item = {"contentId": {"S": "MC1"}, "marketingCopyTypeCode": {"S": "TOC"},
            "owningMarketCode": {"S": "US"}, "goldUpdatedAt": {"S": updated}}
    if content is not None:
        item["contentGz"] = {"B": gzip.compress(content)}
        if with_hash:
            item["contentHash"] = {
                "S": hashlib.md5(content).hexdigest()}
    return item


@pytest.fixture(autouse=True)
def _consumer_auth_off(monkeypatch):
    """These tests exercise the READ path, so consumer auth is off here.

    It is on by default in the module as shipped, which would make every
    handler test in this file a 500. The wiring that makes the handler call it
    at all is proven in TestConsumerAuth below.
    """
    monkeypatch.setattr(api, "_auth", jwt_verify.Authorizer(required=False))


def reset_secret_cache():
    api._secret_cache.update(value=None, fetched=0.0, state="never")


def get(path="/v1/marketing-copy/MC1", headers=None, items=None,
        secrets=None):
    reset_secret_cache()
    event = {"requestContext": {"http": {"method": "GET"}},
             "rawPath": path, "headers": headers or {}}
    return api.handler(event,
                       ddb=StubServingDdb(items or {"MC1": served_item()}),
                       secrets=secrets or StubSecrets())


class TestApi:
    def test_200_envelope_with_content_hash_etag(self):
        r = get()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["marketingCopyContentText"] == CONTENT.decode()
        # id + content only; the classifying attributes live on the product record
        assert set(body) == {"contentId", "marketingCopyContentText"}
        assert r["headers"]["etag"] == f'"{HASH}"'
        assert " " not in r["headers"]["etag"]      # RFC 9110 etagc
        assert "max-age=" in r["headers"]["cache-control"]

    def test_etag_roundtrip_gives_304(self):
        r = get(headers={"If-None-Match": f'"{HASH}"'})
        assert r["statusCode"] == 304

    def test_silent_repair_changes_the_etag_even_with_same_timestamp(self):
        # the exact staleness class the platform guarantees: content moved,
        # _gold_updated_at deliberately did not
        repaired = served_item(content=b"<p>repaired</p>")
        r = get(headers={"If-None-Match": f'"{HASH}"'},
                items={"MC1": repaired})
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["marketingCopyContentText"] == "<p>repaired</p>"

    def test_pre_hash_items_get_the_hash_computed_on_the_fly(self):
        r = get(items={"MC1": served_item(with_hash=False)})
        assert r["headers"]["etag"] == f'"{HASH}"'

    def test_unknown_id_and_unpublished_content_both_404(self):
        assert get(path="/v1/marketing-copy/NOPE")["statusCode"] == 404
        assert get(items={"MC1": served_item(content=None)})[
            "statusCode"] == 404

    def test_raw_segment_tried_before_decoding(self):
        # a contentId LITERALLY containing %20 must resolve raw first —
        # double-decoding would 404 a published row
        items = {"AB%20CD": dict(served_item(),
                                 contentId={"S": "AB%20CD"})}
        stub = StubServingDdb(items)
        reset_secret_cache()
        r = api.handler({"requestContext": {"http": {"method": "GET"}},
                         "rawPath": "/v1/marketing-copy/AB%20CD",
                         "headers": {}}, ddb=stub, secrets=StubSecrets())
        assert r["statusCode"] == 200
        assert stub.lookups[0] == "AB%20CD"

    def test_gateway_header_enforced_once_the_secret_has_a_value(self):
        assert get(secrets=StubSecrets("s3cret"))["statusCode"] == 403
        ok = get(secrets=StubSecrets("s3cret"),
                 headers={"X-UIF-Gateway-Auth": "s3cret"})
        assert ok["statusCode"] == 200

    def test_fetch_error_never_disarms_an_armed_check(self, capsys):
        stub = StubSecrets("s3cret")
        reset_secret_cache()
        ev = {"requestContext": {"http": {"method": "GET"}},
              "rawPath": "/v1/marketing-copy/MC1", "headers": {}}
        ddb = StubServingDdb({"MC1": served_item()})
        assert api.handler(ev, ddb=ddb, secrets=stub)["statusCode"] == 403
        # cache expires, next fetch THROWS — the check must keep enforcing
        api._secret_cache["fetched"] = 0.0
        stub.mode = "error"
        assert api.handler(ev, ddb=ddb, secrets=stub)["statusCode"] == 403
        assert "keeping the last known posture" in capsys.readouterr().out

    def test_check_disabled_while_the_secret_is_absent(self, capsys):
        assert get(secrets=StubSecrets(None))["statusCode"] == 200
        assert "DISABLED" in capsys.readouterr().out

    def test_versionless_path_resolves(self):
        assert get(path="/marketing-copy/MC1")["statusCode"] == 200

    def test_methods_and_no_cors(self):
        reset_secret_cache()
        ev = {"requestContext": {"http": {"method": "POST"}},
              "rawPath": "/v1/marketing-copy/MC1", "headers": {}}
        r = api.handler(ev, ddb=StubServingDdb({}), secrets=StubSecrets())
        assert r["statusCode"] == 405
        assert not any(k.lower().startswith("access-control")
                       for k in r["headers"])


class TestConsumerAuth:
    """That the handler CALLS the shared check, and returns what it says.

    What the check itself DECIDES — signatures, algorithms, claims, the
    subject allow-list — is tested once, exhaustively, in
    stacks/echo/qe/gold/test_jwt_verify.py. This API and the gold retrieval
    API construct the SAME jwt_verify.Authorizer, so asserting its behaviour
    twice would test one object through two doors. What is specific to this
    API, and therefore tested here, is that the call happens at all: without
    it a tokenless request is served.
    """

    def _get(self, headers=None):
        reset_secret_cache()
        event = {"requestContext": {"http": {"method": "GET"}},
                 "rawPath": "/v1/marketing-copy/MC1",
                 "headers": headers or {}}
        return api.handler(event, ddb=StubServingDdb({"MC1": served_item()}),
                           secrets=StubSecrets())

    def test_no_token_is_401(self, monkeypatch):
        monkeypatch.setattr(api, "_auth", jwt_verify.Authorizer(
            required=True, issuer="IES",
            jwks_url="https://issuer.invalid/jwks.json",
            allowed_subs={"someone"}, token_type="at"))
        r = self._get()
        assert r["statusCode"] == 401
        assert r["headers"]["www-authenticate"] == "Bearer"
        assert json.loads(r["body"]) == {"error": "unauthorized"}

    def test_armed_but_unconfigured_refuses_every_request(self, monkeypatch):
        monkeypatch.setattr(api, "_auth",
                            jwt_verify.Authorizer(required=True))
        r = self._get()
        assert r["statusCode"] == 500
        assert json.loads(r["body"]) == {"error": "authorization misconfigured"}

    def test_switched_off_still_serves(self):
        assert self._get()["statusCode"] == 200

    def test_the_check_runs_BEFORE_the_item_is_read(self, monkeypatch):
        """A refused request must not cost a DynamoDB read."""
        monkeypatch.setattr(api, "_auth", jwt_verify.Authorizer(
            required=True, issuer="IES",
            jwks_url="https://issuer.invalid/jwks.json",
            allowed_subs={"someone"}))
        ddb = StubServingDdb({"MC1": served_item()})
        reset_secret_cache()
        event = {"requestContext": {"http": {"method": "GET"}},
                 "rawPath": "/v1/marketing-copy/MC1", "headers": {}}
        assert api.handler(event, ddb=ddb,
                           secrets=StubSecrets())["statusCode"] == 401
        assert ddb.lookups == []

    def test_this_api_DEFAULTS_the_check_on(self):
        """Read from an empty mapping, so this is the default rather than
        whatever is in the developer's environment."""
        assert jwt_verify.Authorizer.from_env(env={}).required is True
