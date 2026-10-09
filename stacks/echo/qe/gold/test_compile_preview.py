# Query preview service (UIFPE-219) — lambda_compile_preview. The handler is
# a pure function (no AWS at import or at runtime), so these tests run it
# whole: real repo artifacts in, recompiled texts out. The fences are pinned
# individually because domain/entity become filesystem paths.
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
MOD = (REPO / "stacks/echo/src/apps/lambdas"
       / "compile_preview.py")
spec = importlib.util.spec_from_file_location("compile_preview", MOD)
cp = importlib.util.module_from_spec(spec)
sys.modules["compile_preview"] = cp
spec.loader.exec_module(cp)

SCHEMAS = REPO / "domains/product/src/schemas"


def real_artifacts(pipeline=True) -> dict:
    arts = {
        "mapping": (SCHEMAS / "mapping/product/v1/product.mapping.json")
        .read_text(encoding="utf-8"),
        "master": (SCHEMAS / "gold/product/v1/master.json")
        .read_text(encoding="utf-8"),
        "manifest": (SCHEMAS / "mapping/product/v1/manifest.yaml")
        .read_text(encoding="utf-8"),
        "sql": (SCHEMAS / "mapping/product/v1/sql/product.sql")
        .read_text(encoding="utf-8"),
    }
    if pipeline:
        arts["pipeline"] = (
            REPO / "domains/product/src/apps/pipelines/base/product.yaml"
        ).read_text(encoding="utf-8")
    return arts


def envelope(cols, intent="add", job_props=None, refresh=None):
    env = {
        "task": "qe preview task", "intent": intent,
        "jobProperties": job_props or {},
        "intake": {
            "domain": "product", "table": "gold_product.product",
            "drivingTable": "MTL_SYSTEM_ITEMS_B",
            "batch": {"requestedBy": "qe", "targetDate": None,
                      "backfillMode": "silent", "newChannel": None},
            "columns": cols,
        },
    }
    if refresh:
        env["refresh"] = refresh
    return env


def eav_scalar(name="previewAttr", group="999", column="C_EXT_ATTR20"):
    return {
        "goldColumnName": name,
        "businessDescription": f"Preview test attribute {name}",
        "kind": "scalar", "dataType": "string",
        "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B", "column": column,
                   "filter": f"ATTR_GROUP_ID = {group}", "join": None},
        "transform": None,
        "channels": {"event": False, "message": False, "api": True},
        "nullable": True, "notes": None,
    }


def derived_scalar(sql, name="previewDerived", value="previewDerived"):
    return {
        "goldColumnName": name,
        "businessDescription": "Preview test derived column",
        "kind": "scalar", "dataType": "string",
        "source": {"table": "", "column": "", "filter": None, "join": None},
        "transform": None,
        "channels": {"event": False, "message": False, "api": True},
        "nullable": True, "notes": None,
        # reads is deliberately absent: the compiler recomputes it
        # (computed, never authored) — the preview must show that too
        "derivation": {
            "sql": sql,
            "keyedBy": [{"queryColumn": "INVENTORY_ITEM_ID",
                         "entityColumn": "INVENTORY_ITEM_ID"}],
            "valueColumn": value,
        },
    }


def post(body: dict) -> dict:
    event = {"requestContext": {"http": {"method": "POST"}},
             "body": json.dumps(body)}
    return cp.handler(event)


def ok_body(r):
    assert r["statusCode"] == 200, r["body"]
    return json.loads(r["body"])


class TestFences:
    def test_identifier_locked_paths(self):
        arts = real_artifacts(pipeline=False)
        for domain, table in (("../evil", "gold_product.product"),
                              ("Product", "gold_product.product"),
                              ("product", "gold_product.__init__/.."),
                              ("product", "gold_product.Product"),
                              ("", "gold_product.product")):
            env = envelope([eav_scalar()])
            env["intake"]["domain"] = domain
            env["intake"]["table"] = table
            r = post({"envelope": env, "artifacts": arts})
            assert r["statusCode"] == 400, (domain, table)
            assert json.loads(r["body"])["refusal"] is False

    def test_artifacts_must_be_bounded_strings(self):
        env = envelope([eav_scalar()])
        arts = real_artifacts(pipeline=False)
        r = post({"envelope": env, "artifacts": {**arts, "sql": 5}})
        assert r["statusCode"] == 400
        r = post({"envelope": env,
                  "artifacts": {**arts, "unexpected": "x"}})
        assert r["statusCode"] == 400
        missing = dict(arts)
        del missing["master"]
        r = post({"envelope": env, "artifacts": missing})
        assert r["statusCode"] == 400
        big = {**arts, "sql": "x" * (cp.MAX_ARTIFACT + 1)}
        r = post({"envelope": env, "artifacts": big})
        assert r["statusCode"] == 400

    def test_malformed_bodies_are_400_not_500(self):
        for raw in ("not json", '"x"', "[1]"):
            event = {"requestContext": {"http": {"method": "POST"}},
                     "body": raw}
            assert cp.handler(event)["statusCode"] == 400
        r = post({"envelope": {"intake": {"domain": "product",
                                          "table": "gold_product.product",
                                          "columns": []}},
                  "artifacts": real_artifacts(pipeline=False)})
        assert r["statusCode"] == 400   # empty columns

    def test_methods(self):
        assert cp.handler({"requestContext": {"http": {"method": "OPTIONS"}}}
                          )["statusCode"] == 200
        assert cp.handler({"requestContext": {"http": {"method": "GET"}}}
                          )["statusCode"] == 405

    def test_no_cors_headers_from_the_handler(self):
        r = post({"envelope": {}, "artifacts": {}})
        assert not any(k.lower().startswith("access-control")
                       for k in r["headers"])


class TestPreview:
    def test_eav_add_recompiles_all_four_artifacts(self):
        body = ok_body(post({"envelope": envelope([eav_scalar()]),
                             "artifacts": real_artifacts()}))
        assert any(line.startswith("[AUTO] previewAttr") for line in body["log"])
        files = body["files"]
        assert files["sql"]["changed"] is True
        assert "ag999 AS (" in files["sql"]["after"]
        assert "LEFT JOIN ag999" in files["sql"]["after"]
        assert "AS previewAttr" in files["sql"]["after"]
        for k in ("mapping", "master", "manifest"):
            assert files[k]["changed"] is True, k
            assert "previewAttr" in files[k]["after"]
        # untouched schedule: pipeline text comes back unchanged, no after
        assert files["pipeline"]["changed"] is False
        assert "after" not in files["pipeline"]

    def test_derived_add_splices_the_query_verbatim_and_computes_reads(self):
        sql = ("SELECT INVENTORY_ITEM_ID, min(C_EXT_ATTR7) AS previewDerived\n"
               "FROM items_ext GROUP BY INVENTORY_ITEM_ID")
        body = ok_body(post({"envelope": envelope([derived_scalar(sql)]),
                             "artifacts": real_artifacts(pipeline=False)}))
        after = body["files"]["sql"]["after"]
        assert "d_previewDerived AS (" in after
        for line in sql.splitlines():        # verbatim, indented in place
            assert line in after
        assert "LEFT JOIN d_previewDerived" in after
        # reads recomputed by the parser lands in the mapping the reviewer sees
        mapping_after = json.loads(body["files"]["mapping"]["after"])
        col = next(c for c in mapping_after["columns"]
                   if c["goldColumnName"] == "previewDerived")
        assert col["derivation"]["reads"] == {
            "items_ext": ["C_EXT_ATTR7", "INVENTORY_ITEM_ID"]}

    def test_battery_refusal_reads_as_the_compilers_answer(self):
        sql = ("SELECT INVENTORY_ITEM_ID, min(x) AS previewDerived\n"
               "FROM totally_unregistered GROUP BY INVENTORY_ITEM_ID")
        r = post({"envelope": envelope([derived_scalar(sql)]),
                  "artifacts": real_artifacts(pipeline=False)})
        assert r["statusCode"] == 400
        body = json.loads(r["body"])
        assert body["refusal"] is True
        assert "not a registered source" in body["error"]

    def test_derived_modify_is_a_plan_and_the_sql_stands_still(self):
        mapping = json.loads(real_artifacts(pipeline=False)["mapping"])
        col = next(c for c in mapping["columns"]
                   if c["goldColumnName"] == "parentProductId")
        body = ok_body(post({
            "envelope": envelope([col], intent="modify",
                                 refresh={"historical": True, "mode": "silent"}),
            "artifacts": real_artifacts(pipeline=False)}))
        assert body["files"]["sql"]["changed"] is False
        assert any("plan:derived-sql-modify" in line or
                   "derived column (ADR-0008" in line
                   for line in body["log"])
        assert any(line.startswith("[RUNBOOK]") for line in body["log"])

    def test_schedule_change_previews_the_pipeline_config(self):
        body = ok_body(post({
            "envelope": envelope([eav_scalar()],
                                 job_props={"frequency": "gold-12h",
                                            "start_time": "07:15"}),
            "artifacts": real_artifacts()}))
        pipe = body["files"]["pipeline"]
        assert pipe["changed"] is True
        assert "frequency: gold-12h" in pipe["after"]
        assert '"07:15"' in pipe["after"]

    def test_grain_mismatch_refuses_with_the_compilers_words(self):
        # ag802 is item-level; declaring the (item, org) join against it must
        # surface compile_intake_delta's own refusal through the service
        col = eav_scalar(group="802", column="C_EXT_ATTR9")
        col["source"]["join"] = {"type": "left", "on": [
            {"leftTable": "MTL_SYSTEM_ITEMS_B",
             "leftColumn": "INVENTORY_ITEM_ID",
             "rightColumn": "INVENTORY_ITEM_ID"},
            {"leftTable": "MTL_SYSTEM_ITEMS_B",
             "leftColumn": "ORGANIZATION_ID",
             "rightColumn": "ORGANIZATION_ID"}]}
        r = post({"envelope": envelope([col]),
                  "artifacts": real_artifacts(pipeline=False)})
        assert r["statusCode"] == 400
        body = json.loads(r["body"])
        assert body["refusal"] is True
        assert "cannot serve two grains" in body["error"]

    def test_nothing_left_behind_in_tmp(self, tmp_path, monkeypatch):
        import tempfile
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        ok_body(post({"envelope": envelope([eav_scalar()]),
                      "artifacts": real_artifacts(pipeline=False)}))
        assert list(tmp_path.iterdir()) == []


class TestScaffoldPreview:
    """UIFPE-260: a new entity previews from an empty artifact set."""

    def scaffold_envelope(self):
        env = envelope([])
        env["jobProperties"] = {"frequency": "gold-24h",
                                "start_time": "08:15"}
        env["intake"] = {
            "domain": "stock", "table": "gold_stock.stock",
            "drivingTable": "XXPSO_INV_STOCK_ATP_STG",
            "drivingSilverTable":
                "uif_echo_{env}_silver_tep.xxpso_inv_stock_atp_stg",
            "drivingFilter": "PPID IS NOT NULL",
            "entityKey": [
                {"goldColumn": "ppid", "drivingColumn": "PPID"},
                {"goldColumn": "organizationCode",
                 "drivingColumn": "ORGANIZATION_CODE"}],
            "batch": {"requestedBy": "qe", "targetDate": None,
                      "backfillMode": "silent", "newChannel": None},
            "columns": [
                {"goldColumnName": "ppid",
                 "businessDescription": "key", "kind": "scalar",
                 "dataType": "string",
                 "source": {"table": "XXPSO_INV_STOCK_ATP_STG",
                            "column": "PPID", "filter": None},
                 "transform": None, "nullable": False,
                 "channels": {"event": True, "message": True, "api": True}},
                {"goldColumnName": "organizationCode",
                 "businessDescription": "key", "kind": "scalar",
                 "dataType": "string",
                 "source": {"table": "XXPSO_INV_STOCK_ATP_STG",
                            "column": "ORGANIZATION_CODE", "filter": None},
                 "transform": None, "nullable": False,
                 "channels": {"event": True, "message": True, "api": True}},
            ],
        }
        return env

    def test_scaffold_preview_returns_all_six_files(self):
        body = ok_body(post({"envelope": self.scaffold_envelope(),
                             "artifacts": {}}))
        assert set(body["files"]) == {"mapping", "master", "manifest",
                                      "sql", "pipeline", "overlay"}
        assert all(f["changed"] for f in body["files"].values())

    def test_empty_artifacts_without_identity_names_the_gap(self):
        env = self.scaffold_envelope()
        env["intake"].pop("drivingSilverTable")
        r = post({"envelope": env, "artifacts": {}})
        assert r["statusCode"] == 400
        assert "drivingSilverTable" in json.loads(r["body"])["error"]

    def test_malformed_intake_shapes_return_400_not_500(self):
        env = self.scaffold_envelope()
        env["intake"]["entityKey"] = "ppid"          # string, not a list
        r = post({"envelope": env, "artifacts": {}})
        assert r["statusCode"] == 400
