# marketing_copy entity gates (UIFPE-252) — the content library's four
# artifacts + pipeline config, pinned for the invariants the runtime relies
# on. The existing intake-hygiene and mapping-parity suites are hardcoded to
# the product ENTITY (their fixtures name product/v1 paths), so a second
# entity in the domain gets its own pins rather than silently riding along
# untested.
import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "domains/product/src/schemas"
MASTER = json.loads(
    (BASE / "gold/marketing_copy/v1/master.json").read_text(encoding="utf-8"))
MANIFEST_TEXT = (BASE / "mapping/marketing_copy/v1/manifest.yaml").read_text(
    encoding="utf-8")
MANIFEST = yaml.safe_load(MANIFEST_TEXT)
SQL = (BASE / "mapping/marketing_copy/v1/sql/marketing_copy.sql").read_text(
    encoding="utf-8")
# comment-stripped view for structural regexes — SQL comments legitimately
# carry parens and keywords that would otherwise derail non-greedy matches
SQL_CODE = "\n".join(ln for ln in SQL.splitlines()
                     if not ln.lstrip().startswith("--"))
INTAKE = json.loads(
    (BASE / "mapping/marketing_copy/v1/product.mapping.json").read_text(
        encoding="utf-8"))
PIPE_DIR = REPO / "domains/product/src/apps/pipelines"
PIPE_BASE = yaml.safe_load(
    (PIPE_DIR / "base/marketing_copy.yaml").read_text(encoding="utf-8"))
PIPE_DEV = yaml.safe_load(
    (PIPE_DIR / "overlays/dev/marketing_copy.yaml").read_text(encoding="utf-8"))


def business_properties() -> list:
    """master.json property names in declared order (no framework _-cols yet)."""
    return [k for k in MASTER["properties"] if not k.startswith("_")]


def sql_select_aliases() -> list:
    """Output aliases of the FINAL SELECT, in order."""
    final = SQL[SQL.rindex("SELECT"):]
    return re.findall(r"AS\s+(\w+)\s*(?:,|\n)", final.split("FROM")[0])


class TestPhysicalOrderParity:
    """One physical column order, four declarations — all must agree, because
    output_columns drives the DDL and the SQL SELECT feeds it positionally."""

    def test_master_manifest_sql_intake_all_agree(self):
        props = business_properties()
        assert MANIFEST["output_columns"] == props
        assert sql_select_aliases() == props
        assert [c["goldColumnName"] for c in INTAKE["columns"]] == props

    def test_lineage_covers_every_output_column(self):
        assert sorted(MANIFEST["canonical_lineage"].keys()) == sorted(
            MANIFEST["output_columns"])

    def test_every_final_select_item_is_aliased(self):
        # the alias regex alone would miss a bare un-aliased column appended
        # later (`, RECORD_ID`) and still count 6 — a false PASS that breaks
        # the positional output_columns -> DDL contract at runtime. Count the
        # top-level select expressions and demand the two numbers agree.
        final = SQL_CODE[SQL_CODE.rindex("SELECT"):]
        select_list = final[len("SELECT"):final.index("FROM")]
        depth, items = 0, 1
        for ch in select_list:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                items += 1
        assert items == len(sql_select_aliases()) == len(business_properties())


class TestDomainFilter:
    """sql WHERE and manifest sot_filter MUST carry the same predicate —
    reconcile applies sot_filter to decide deletions, the transform applies
    the SQL's WHERE to decide upserts; divergence leaks or over-deletes."""

    def _conjuncts(self, s: str) -> set:
        return {c.strip() for c in re.split(r"\bAND\b", s) if c.strip()}

    def test_sot_filter_matches_the_sql_where(self):
        src = MANIFEST["sources"][0]
        m = re.search(r"FROM market_copy\s+WHERE\s+(.*?)\n\)", SQL_CODE, re.S)
        assert m, "dedupe CTE WHERE not found"
        sql_where = " ".join(m.group(1).split())
        assert self._conjuncts(src["sot_filter"]) == self._conjuncts(sql_where)

    def test_the_filter_is_the_agreed_domain(self):
        f = MANIFEST["sources"][0]["sot_filter"]
        assert "VERSION = 'C'" in f
        assert "DISABLED_FLAG" in f
        assert "CONTENT_ID IS NOT NULL" in f


class TestDeterministicDedupe:
    def test_latest_pick_is_fully_ordered(self):
        # row_number over (LAST_UPDATE_DATE, RECORD_ID) — the tie-break is
        # what makes identical runs produce identical picks (check A8).
        w = re.search(r"row_number\(\)\s+OVER\s*\((.*?)\)", SQL_CODE, re.S)
        assert w, "dedupe window not found"
        win = " ".join(w.group(1).split())
        assert "PARTITION BY CONTENT_ID" in win
        assert "LAST_UPDATE_DATE DESC" in win
        assert "RECORD_ID" in win and win.index("LAST_UPDATE_DATE") < win.index("RECORD_ID")
        assert re.search(r"WHERE\s+rn\s*=\s*1", SQL)


class TestChannels:
    """api-only by design: content must never ride event or message feeds
    (the claim-check pattern is the whole point of this entity)."""

    def test_master_tags_every_property_api_only(self):
        for name, prop in MASTER["properties"].items():
            assert prop["x-channels"] == ["api"], name

    def test_intake_channels_agree(self):
        for col in INTAKE["columns"]:
            assert col["channels"] == {
                "event": False, "message": False, "api": True
            }, col["goldColumnName"]


class TestKeyDeclarations:
    def test_one_key_declared_identically_everywhere(self):
        assert [p["goldColumn"] for p in MASTER["metadata"]["primaryKey"]] \
            == ["contentId"]
        assert [p["goldColumn"] for p in INTAKE["entityKey"]] == ["contentId"]
        assert MANIFEST["target"]["primary_key_cols"] == ["contentId"]
        assert PIPE_BASE["target"]["primary_key_cols"] == ["contentId"]
        assert MASTER["required"] == ["contentId"]

    def test_key_mapping_and_sot_pk_project_the_driving_column(self):
        src = MANIFEST["sources"][0]
        assert src["key_mapping"] == {"contentId": "CONTENT_ID"}
        assert src["sot_pk_mapping"] == {"contentId": "CONTENT_ID"}


class TestPipelineConfig:
    def test_alias_sets_match_between_pipeline_and_manifest(self):
        # asymmetric failure modes: missing here -> TABLE_OR_VIEW_NOT_FOUND;
        # missing in the manifest -> no key_mapping and the run is refused
        p = {s["alias"] for s in PIPE_BASE["sources"]}
        m = {s["alias"] for s in MANIFEST["sources"]}
        assert p == m == {"market_copy"}
        assert PIPE_BASE["sources"][0]["silver_table"] \
            == MANIFEST["sources"][0]["silver_table"]

    def test_merged_dev_config_validates(self):
        import gold_config
        import sync_pipelines
        desired = sync_pipelines.load_desired(
            "dev", repo_root=REPO / "domains/product/src/apps")
        assert "marketing_copy" in desired
        cfg = desired["marketing_copy"]
        assert gold_config.validate(cfg) is None
        assert cfg["target"]["database"] == "uif_echo_{env}_gold_product"
        assert cfg["target"]["table"] == "marketing_copy"
        assert cfg["schedule"]["start_time"] == "07:45"

    def test_target_is_the_product_gold_database(self):
        # second TABLE in the existing DB — the LF gold-layer tag already
        # covers it, so arming needs zero grant work
        assert MANIFEST["target"]["database"] == "uif_echo_{env}_gold_product"
        assert MANIFEST["target"]["table"] == "marketing_copy"


class TestCompilerPipelinePath:
    """The adversarial round's HIGH finding: the delta compiler wrote every
    schedule edit into pipelines/base/{domain}.yaml — so ANY product-domain
    envelope rescheduled product's live 06:30 run, and marketing_copy's own
    yaml was unreachable forever. The path is keyed by ENTITY (= gold_id =
    filename stem) now; this pins both directions."""

    @pytest.fixture()
    def repo_clone(self, tmp_path):
        base = tmp_path / "domains/product/src/schemas"
        for rel in ("mapping/marketing_copy/v1/product.mapping.json",
                    "gold/marketing_copy/v1/master.json",
                    "mapping/marketing_copy/v1/manifest.yaml",
                    "mapping/marketing_copy/v1/sql/marketing_copy.sql"):
            dst = base / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text((BASE / rel).read_text(encoding="utf-8"),
                           encoding="utf-8")
        pdst = tmp_path / "domains/product/src/apps/pipelines/base"
        pdst.mkdir(parents=True, exist_ok=True)
        for name in ("marketing_copy.yaml", "product.yaml"):
            pdst.joinpath(name).write_text(
                (PIPE_DIR / "base" / name).read_text(encoding="utf-8"),
                encoding="utf-8")
        return tmp_path

    def test_schedule_edit_lands_in_this_entitys_yaml_only(self, repo_clone):
        import compile_intake_delta as cid
        intake = {"domain": "product", "table": "gold_product.marketing_copy",
                  "drivingTable": "XXPSO_EGO_MARKET_COPY_DATA",
                  "batch": {"requestedBy": "qe", "targetDate": None,
                            "backfillMode": "silent", "newChannel": None},
                  "columns": []}
        edits, log = cid.compile_delta(
            repo_clone, intake, "add", None,
            {"frequency": "gold-12h", "start_time": "09:15"})
        pipeline_edits = [p for p in edits if "pipelines" in str(p)]
        assert pipeline_edits, "schedule edit produced no pipeline file"
        assert all(p.name == "marketing_copy.yaml" for p in pipeline_edits)
        new = edits[pipeline_edits[0]]
        assert "frequency: gold-12h" in new and '"09:15"' in new
        # the sibling pipeline is untouched — the exact clobber the old
        # {domain}.yaml path caused
        product_yaml = (repo_clone / "domains/product/src/apps/pipelines"
                        / "base/product.yaml")
        assert product_yaml not in edits
        assert '06:30' in product_yaml.read_text(encoding="utf-8")


class TestIntakeHygiene:
    def test_intake_validates_against_the_shared_schema(self):
        import jsonschema
        schema = json.loads(
            (REPO / "stacks/apicurio/standards/gold-attribute-mapping.schema.json")
            .read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(INTAKE)

    def test_no_derivation_blocks_and_no_transforms(self):
        # every column is a straight scalar copy; anything richer must come
        # back through the intake contract, not slip in via the SQL
        for col in INTAKE["columns"]:
            assert col.get("derivation") is None, col["goldColumnName"]
            assert col.get("transform") is None, col["goldColumnName"]

    def test_dev_overlay_carries_the_entity_paths(self):
        for v in (PIPE_DEV["schema"]["master_s3_path"],
                  PIPE_DEV["mapping"]["manifest_s3_path"],
                  PIPE_DEV["mapping"]["sql_s3_path"]):
            assert "marketing_copy" in v and v.startswith("s3://uif-echo-dev-gold-artifacts/")
        assert PIPE_DEV["target"]["s3_path"] == "s3://uif-echo-dev-gold/marketing_copy/"
