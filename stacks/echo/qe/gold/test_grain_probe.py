# Grain test run service (UIFPE-219, ADR-0008 #5) — the pure half of
# lambda_grain_probe, tested without credentials or network. The Athena
# client is stubbed; every spliced string is pinned because the wrapper is
# the injection surface.
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
MOD = REPO / "stacks/echo/src/apps/lambdas/grain_probe.py"
spec = importlib.util.spec_from_file_location("grain_probe", MOD)
gp = importlib.util.module_from_spec(spec)
sys.modules["grain_probe"] = gp
spec.loader.exec_module(gp)


GOOD = {
    "sql": "SELECT INVENTORY_ITEM_ID, min(C_EXT_ATTR7) AS parentProduct\n"
           "FROM items_ext GROUP BY INVENTORY_ITEM_ID",
    "keyedBy": [{"queryColumn": "INVENTORY_ITEM_ID",
                 "entityColumn": "INVENTORY_ITEM_ID"}],
    "aliasTables": {"items_ext":
                    "uif_echo_{env}_silver_mdm.ego_mtl_sy_items_ext_b"},
}


class TestValidateRequest:
    def test_the_good_request_passes(self):
        assert gp.validate_request(GOOD) == []

    def test_comment_markers_inside_strings_do_not_desync_the_fence(self):
        # found adversarially: sequential comment-then-string stripping let
        # '--' inside a literal eat its own closing quote — false refusals
        # AND false accepts, in one bug
        legit = {**GOOD, "sql":
                 "SELECT INVENTORY_ITEM_ID, min(C_EXT_ATTR7) AS parentProduct"
                 "\nFROM items_ext WHERE code LIKE '%--%'"
                 "\n  AND note = 'delete after review'"
                 "\nGROUP BY INVENTORY_ITEM_ID"}
        assert gp.validate_request(legit) == []
        masked = {**GOOD, "sql": "SELECT '--' AS a; DELETE FROM items_ext"}
        assert gp.validate_request(masked)   # the ';' and DELETE stay visible

    def test_battery_subset_refusals(self):
        assert gp.validate_request({**GOOD, "sql": " "})
        assert gp.validate_request({**GOOD, "sql": "SELECT 1; SELECT 2"})
        assert gp.validate_request(
            {**GOOD, "sql": "DELETE FROM items_ext"})
        assert gp.validate_request({**GOOD, "sql": "CALL something()"})
        # ';' or writes inside strings/comments are NOT refusals
        ok = {**GOOD, "sql": GOOD["sql"] + " -- delete; explained"}
        assert gp.validate_request(ok) == []

    def test_the_injection_surface_is_identifier_locked(self):
        # everything spliced AROUND the query must be a plain identifier or
        # db.table — these are the strings a hostile caller would abuse
        bad_key = {**GOOD, "keyedBy": [
            {"queryColumn": "x) SELECT 1; --", "entityColumn": "X"}]}
        assert gp.validate_request(bad_key)
        bad_alias = {**GOOD, "aliasTables": {
            "items_ext) AS x, evil AS (SELECT 1": "db.t"}}
        assert gp.validate_request(bad_alias)
        bad_target = {**GOOD, "aliasTables": {
            "items_ext": "db.t UNION SELECT secret"}}
        assert gp.validate_request(bad_target)
        assert gp.validate_request({**GOOD, "keyedBy": []})
        assert gp.validate_request({**GOOD, "keyedBy": [{"queryColumn": 5}]})
        assert gp.validate_request({**GOOD, "aliasTables": "x"})

    def test_env_placeholder_targets_are_legal(self):
        assert gp.validate_request(GOOD) == []
        assert gp.resolve_env("uif_echo_{env}_silver_mdm.x") \
            == "uif_echo_dev_silver_mdm.x"


class TestQueryBuilding:
    def test_grain_query_embeds_the_sql_verbatim_and_counts_both_ways(self):
        q = gp.grain_query(GOOD["sql"], GOOD["keyedBy"], GOOD["aliasTables"])
        assert GOOD["sql"] in q                       # verbatim, unrewritten
        assert "items_ext AS (SELECT * FROM "\
               "uif_echo_dev_silver_mdm.ego_mtl_sy_items_ext_b)" in q
        assert "GROUP BY INVENTORY_ITEM_ID)" in q
        assert "row_count" in q and "entity_count" in q and "max_per_entity" in q

    def test_multi_key_grain_groups_by_every_key(self):
        keyed = [{"queryColumn": "iid", "entityColumn": "A"},
                 {"queryColumn": "oid", "entityColumn": "B"}]
        q = gp.grain_query("SELECT 1", keyed, {})
        assert "GROUP BY iid, oid" in q

    def test_sample_query_limits(self):
        q = gp.sample_query(GOOD["sql"], GOOD["aliasTables"])
        assert q.endswith(f"LIMIT {gp.SAMPLE_ROWS}")
        assert GOOD["sql"] in q

    def test_no_aliases_builds_a_clean_with(self):
        q = gp.grain_query("SELECT 1 AS x", [{"queryColumn": "x",
                                              "entityColumn": "X"}], {})
        assert q.startswith("WITH __q AS (")


class TestVerdict:
    def test_the_two_answers_in_the_authors_words(self):
        ok = gp.verdict(14239, 14239, 1)
        assert ok["oneRowPerEntity"] is True
        assert "one row per entity" in ok["note"]
        bad = gp.verdict(15882, 14239, 4)
        assert bad["oneRowPerEntity"] is False
        assert "aggregate or window" in bad["note"]
        assert "worst: 4" in bad["note"]


class StubAthena:
    """Two executions: the grain summary and the sample."""
    def __init__(self, grain_row=("100", "100", "1"), fail=None):
        self.queries = []
        self._fail = fail
        self._grain_row = grain_row

    def start_query_execution(self, **kw):
        self.queries.append(kw["QueryString"])
        return {"QueryExecutionId": f"q{len(self.queries)}"}

    def get_query_execution(self, QueryExecutionId):
        if self._fail:
            return {"QueryExecution": {"Status": {
                "State": "FAILED", "StateChangeReason": self._fail}}}
        return {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}

    def stop_query_execution(self, QueryExecutionId):
        pass

    def get_query_results(self, QueryExecutionId, MaxResults):
        if QueryExecutionId == "q1":
            cols = ["row_count", "entity_count", "max_per_entity"]
            row = self._grain_row
        else:
            cols = ["INVENTORY_ITEM_ID", "parentProduct"]
            row = ("123", "PP1")
        return {"ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": [{"Name": c} for c in cols]},
            "Rows": [{"Data": [{"VarCharValue": c} for c in cols]},
                     {"Data": [{"VarCharValue": v} for v in row]}]}}


def post(body, athena):
    event = {"requestContext": {"http": {"method": "POST"}},
             "body": json.dumps(body)}
    return gp.handler(event, athena=athena)


class TestHandler:
    def test_happy_path_returns_the_verdict_and_sample(self):
        stub = StubAthena(grain_row=("14239", "14239", "1"))
        r = post(GOOD, stub)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["oneRowPerEntity"] is True
        assert body["sample"]["columns"] == ["INVENTORY_ITEM_ID", "parentProduct"]
        assert body["sample"]["rows"] == [["123", "PP1"]]
        assert len(stub.queries) == 2

    def test_duplicate_grain_reports_the_fix(self):
        stub = StubAthena(grain_row=("200", "150", "3"))
        body = json.loads(post(GOOD, stub)["body"])
        assert body["oneRowPerEntity"] is False
        assert "aggregate or window" in body["note"]

    def test_engine_refusals_surface_readably_as_400(self):
        stub = StubAthena(fail="line 2:8: Function collect_list not registered")
        r = post(GOOD, stub)
        assert r["statusCode"] == 400
        assert "collect_list" in r["body"]

    def test_validation_refusals_never_reach_athena(self):
        stub = StubAthena()
        r = post({**GOOD, "sql": "DROP TABLE x"}, stub)
        assert r["statusCode"] == 400
        assert stub.queries == []

    def test_malformed_bodies_are_400_not_500(self):
        stub = StubAthena()
        for raw in ("not json", '"x"', "[1]"):
            event = {"requestContext": {"http": {"method": "POST"}},
                     "body": raw}
            assert gp.handler(event, athena=stub)["statusCode"] == 400

    def test_no_cors_headers_from_the_handler(self):
        r = post({**GOOD, "sql": " "}, StubAthena())
        assert not any(k.lower().startswith("access-control")
                       for k in r["headers"])

# -- mode=entityStatus (UIFPE-219): the step-1 cards' live half -------------

class StubGlue:
    def __init__(self, runs):
        self._runs = runs

    def get_job_runs(self, JobName, MaxResults):
        return {"JobRuns": self._runs}


class StubAthenaCount(StubAthena):
    def get_query_results(self, QueryExecutionId, MaxResults):
        return {"ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": [{"Name": "_col0"}]},
            "Rows": [{"Data": [{"VarCharValue": "_col0"}]},
                     {"Data": [{"VarCharValue": "6833401"}]}]}}


class TestEntityStatus:
    def test_identifier_fences_hold(self):
        for bad in ({"goldId": "x; DROP", "database": "d", "table": "t"},
                    {"goldId": "p", "database": "d.b", "table": "t"},
                    {"goldId": "p", "database": "d", "table": "t UNION x"},
                    {"goldId": 5, "database": "d", "table": "t"}):
            assert gp.validate_status_request(bad), bad
        assert gp.validate_status_request(
            {"goldId": "product", "database": "uif_echo_{env}_gold_product",
             "table": "product"}) == []

    def test_status_resolves_env_and_reports_run_and_count(self):
        import datetime
        athena = StubAthenaCount()
        runs = [
            {"Arguments": {"--gold_id": "program"}, "JobRunState": "SUCCEEDED",
             "StartedOn": datetime.datetime(2026, 8, 31, 12, 0),
             "ExecutionTime": 100},
            {"Arguments": {"--gold_id": "product"}, "JobRunState": "SUCCEEDED",
             "StartedOn": datetime.datetime(2026, 8, 31, 6, 30),
             "ExecutionTime": 754},
        ]
        got = gp.entity_status(
            {"goldId": "product", "database": "uif_echo_{env}_gold_product",
             "table": "product"},
            athena=athena, glue=StubGlue(runs))
        assert got["rowCount"] == 6833401
        assert got["lastRun"]["state"] == "SUCCEEDED"
        assert got["lastRun"]["seconds"] == 754      # product's run, not program's
        assert "uif_echo_dev_gold_product.product" in athena.queries[0]

    def test_a_glue_hiccup_degrades_the_card_never_errors_it(self):
        class BrokenGlue:
            def get_job_runs(self, **kw):
                raise RuntimeError("throttled")
        got = gp.entity_status(
            {"goldId": "product", "database": "uif_echo_{env}_gold_product",
             "table": "product"},
            athena=StubAthenaCount(), glue=BrokenGlue())
        assert got["lastRun"] is None
        assert got["rowCount"] == 6833401

    def test_handler_routes_the_mode(self):
        r = post({"mode": "entityStatus", "goldId": "x; DROP",
                  "database": "d", "table": "t"}, StubAthena())
        assert r["statusCode"] == 400
        assert "identifier" in r["body"]

