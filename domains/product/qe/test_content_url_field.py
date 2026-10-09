# marketingCopy.contentUrl (UIFPE-253) + the sql_vars mechanism that carries
# its per-environment URL base into the mapping SQL.
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "domains/product/src/schemas"

sys.path.insert(0, str(REPO / "stacks/echo/src/apps/pipeline"))
import gold_cdc  # noqa: E402
import gold_config  # noqa: E402

MASTER = json.loads((BASE / "gold/product/v1/master.json").read_text(encoding="utf-8"))
MANIFEST = yaml.safe_load(
    (BASE / "mapping/product/v1/manifest.yaml").read_text(encoding="utf-8"))
SQL = (BASE / "mapping/product/v1/sql/product.sql").read_text(encoding="utf-8")
INTAKE = json.loads(
    (BASE / "mapping/product/v1/product.mapping.json").read_text(encoding="utf-8"))


class TestApplySqlVars:
    def test_substitutes_declared_tokens(self):
        out = gold_cdc.apply_sql_vars(
            "concat('{content_url_base}/marketing-copy/', x)",
            {"content_url_base": "https://api1-dev.pearson.com/uif/v1"})
        assert "https://api1-dev.pearson.com/uif/v1/marketing-copy/" in out
        assert "{content_url_base}" not in out

    def test_missing_required_var_refuses_loudly(self):
        with pytest.raises(ValueError, match="content_url_base"):
            gold_cdc.apply_sql_vars("select 1", {}, required=["content_url_base"])

    def test_values_that_would_break_a_sql_literal_refuse(self):
        for bad in ("a'b", "a\\b", "a\nb", "a{env}b", "x{other}"):
            with pytest.raises(ValueError):
                gold_cdc.apply_sql_vars("x", {"k": bad})
        for bad_key in ("env", "Bad-Key", "1x"):
            with pytest.raises(ValueError):
                gold_cdc.apply_sql_vars("x", {bad_key: "v"})

    def test_gold_config_validates_the_same_rules_at_sync_time(self):
        base = {"gold_id": "x", "domain": "product",
                "schema": {"master_s3_path": "s3://b/m", "version": "v1"},
                "mapping": {"manifest_s3_path": "s3://b/mf",
                            "sql_s3_path": "s3://b/s"},
                "sources": [{"alias": "a", "silver_table": "t",
                             "is_source_of_truth": True}],
                "target": {"database": "d", "table": "t", "s3_path": "s3://b/",
                           "primary_key_cols": ["k"]}}
        assert gold_config.validate(
            {**base, "sql_vars": {"content_url_base": "https://x/v1"}}) is None
        assert "sql_vars" in gold_config.validate(
            {**base, "sql_vars": {"content_url_base": "ha'x"}})
        assert "sql_vars" in gold_config.validate({**base, "sql_vars": "x"})

    def test_dev_overlay_carries_the_base_and_the_manifest_requires_it(self):
        sys.path.insert(0, str(REPO / "stacks/echo/src/apps/cli"))
        import sync_pipelines
        cfg = sync_pipelines.load_desired(
            "dev", repo_root=REPO / "domains/product/src/apps")["product"]
        assert cfg["sql_vars"]["content_url_base"] \
            == "https://api1-dev.pearson.com/uif/v1"
        assert MANIFEST["required_sql_vars"] == ["content_url_base"]
        # the guard actually fires for a config without the var
        with pytest.raises(ValueError):
            gold_cdc.apply_sql_vars(SQL, {}, required=MANIFEST["required_sql_vars"])


class TestContentUrlParity:
    """The new member must agree across all four artifacts, LAST everywhere —
    Iceberg's nested ADD COLUMN appends, so struct order is load-bearing."""

    def test_master_members_end_with_content_url(self):
        members = list(MASTER["properties"]["marketingCopy"]["items"]
                       ["properties"].keys())
        assert members[-1] == "contentUrl"
        assert members == ["contentId", "marketingCopyText", "marketingCopyTypeCode", "audienceCode",
                           "geoVisibilityCode", "owningMarketCode", "contentUrl"]

    def test_column_types_struct_matches_master_order(self):
        ct = MANIFEST["column_types"]["marketingCopy"]
        names = re.findall(r"(\w+):\s*STRING", ct)
        assert names == ["contentId", "marketingCopyText", "marketingCopyTypeCode", "audienceCode",
                           "geoVisibilityCode", "owningMarketCode", "contentUrl"]

    def test_sql_named_struct_matches_and_guards_null_content_id(self):
        cte = re.search(r"(?ms)^mktg AS \(.*?\n\),", SQL)
        assert cte, "mktg CTE not found"
        m = re.search(r"'p', named_struct\((.*)\)\)\)\),", cte.group(0), re.S)
        assert m, "mktg payload struct not found"
        names = re.findall(r"'(\w+)',", m.group(1))
        assert names == ["contentId", "marketingCopyText", "marketingCopyTypeCode", "audienceCode",
                           "geoVisibilityCode", "owningMarketCode", "contentUrl"]
        assert "{content_url_base}/marketing-copy/" in m.group(1)
        assert "CASE WHEN C_EXT_ATTR1 IS NOT NULL" in m.group(1)

    def test_intake_field_carries_the_same_expression(self):
        col = next(c for c in INTAKE["columns"]
                   if c["goldColumnName"] == "marketingCopy")
        assert [f["name"] for f in col["fields"]][-1] == "contentUrl"
        f = col["fields"][-1]
        assert f["source"]["column"] == "C_EXT_ATTR1"
        assert "{content_url_base}/marketing-copy/" in f["transform"]["sqlExpr"]

    def test_event_schemas_and_examples_mirror_the_field(self):
        for name in ("product-created", "product-updated"):
            data = json.loads((BASE / f"events/{name}-data.json")
                              .read_text(encoding="utf-8"))
            text = json.dumps(data)
            assert '"contentUrl"' in text, name
            ex = json.loads((BASE / f"events/{name}-example.json")
                            .read_text(encoding="utf-8"))
            extext = json.dumps(ex)
            assert "/uif/v1/marketing-copy/" in extext, name
