"""build_extract_sql and the {{WATERMARK}} token (UIFPE-665).

These call the real builder in src/apps/pipeline/bronze_extract_sql.py, the
module bronze-ingest.py imports at runtime, not a copy of it.
"""
import unittest

from bronze_extract_sql import (
    WATERMARK_TOKEN,
    build_extract_sql,
    require_watermark_column,
    watermark_predicate,
)

TABLE = "SCHEMA.ORDERS"
WM = "2026-05-11T00:00:00"
TS = "TO_TIMESTAMP('2026-05-11T00:00:00', 'YYYY-MM-DD\"T\"HH24:MI:SS')"


def _inc(**extra):
    ext = {"mode": "incremental", "watermark_col": "LAST_UPDATE_DATE", "watermark_value": WM}
    ext.update(extra)
    return ext


class TestBuildSql(unittest.TestCase):
    """Table-path and no-token cases, moved from test_bronze_ingest.py, which
    used to run them against its own copy of the builder (UIFPE-665)."""

    def _make_ext(self, mode, table="SCHEMA.ORDERS", wm_col=None, wm_val=None, wm_fb=None, custom_sql=None):
        ext = {"mode": mode, "table": table}
        if wm_col:
            ext["watermark_col"] = wm_col
        if wm_fb:
            ext["watermark_fallback_col"] = wm_fb
        if wm_val:
            ext["watermark_value"] = wm_val
        if custom_sql:
            ext["custom_sql"] = custom_sql
        return ext

    def test_full_mode_selects_all(self):
        ext = self._make_ext("full")
        sql = build_extract_sql(ext)
        self.assertIn("SELECT * FROM SCHEMA.ORDERS", sql)
        self.assertNotIn("WHERE", sql)
        self.assertTrue(sql.endswith(") t"))

    def test_incremental_adds_where_clause(self):
        ext = self._make_ext(
            "incremental",
            wm_col="LAST_UPDATED",
            wm_val="2026-05-11T00:00:00",
        )
        sql = build_extract_sql(ext)
        self.assertIn("WHERE LAST_UPDATED >", sql)
        self.assertIn("2026-05-11T00:00:00", sql)
        self.assertIn("TO_TIMESTAMP", sql)

    def test_incremental_sentinel_watermark_skips_where_clause(self):
        ext = self._make_ext("incremental", wm_col="LAST_UPDATED")
        sql = build_extract_sql(ext)
        self.assertIn("SELECT * FROM SCHEMA.ORDERS", sql)
        self.assertNotIn("WHERE", sql)

    def test_incremental_sentinel_watermark_explicit_epoch_skips_where(self):
        ext = self._make_ext(
            "incremental",
            wm_col="LAST_UPDATE_DATE",
            wm_fb="CREATION_DATE",
            wm_val="1970-01-01T00:00:00",
        )
        sql = build_extract_sql(ext)
        self.assertNotIn("WHERE", sql)
        self.assertNotIn("COALESCE", sql)

    def test_incremental_uses_coalesce_when_fallback_set(self):
        ext = self._make_ext(
            "incremental",
            wm_col="LAST_UPDATE_DATE",
            wm_fb="CREATION_DATE",
            wm_val="2026-05-11T00:00:00",
        )
        sql = build_extract_sql(ext)
        self.assertIn("COALESCE(LAST_UPDATE_DATE, CREATION_DATE) >", sql)

    def test_incremental_without_fallback_unchanged(self):
        ext = self._make_ext("incremental", wm_col="LAST_UPDATED", wm_val="2026-05-11T00:00:00")
        sql = build_extract_sql(ext)
        self.assertIn("WHERE LAST_UPDATED >", sql)
        self.assertNotIn("COALESCE", sql)

    def test_empty_fallback_uses_primary_only(self):
        ext = self._make_ext(
            "incremental",
            wm_col="LAST_UPDATE_DATE",
            wm_fb="",
            wm_val="2026-05-11T00:00:00",
        )
        sql = build_extract_sql(ext)
        self.assertIn("WHERE LAST_UPDATE_DATE >", sql)
        self.assertNotIn("COALESCE", sql)

    def test_custom_sql_used_verbatim(self):
        # Verbatim now means full mode with no token. Incremental without the
        # token raises; with it, the token is rendered (TestWatermarkToken).
        custom = "SELECT ID, NAME FROM SPECIAL_VIEW WHERE ACTIVE=1"
        ext    = self._make_ext("full", custom_sql=custom)
        sql    = build_extract_sql(ext)
        self.assertEqual(sql, f"({custom}) t")

    def test_full_mode_sql_is_wrapped_in_subquery(self):
        ext = self._make_ext("full", table="HR.EMPLOYEES")
        sql = build_extract_sql(ext)
        self.assertTrue(sql.startswith("(SELECT * FROM HR.EMPLOYEES)"))


class TestWatermarkToken(unittest.TestCase):
    CUSTOM = "SELECT * FROM V a WHERE a.ACTIVE = 1 AND {{WATERMARK}}"

    def test_token_is_the_exact_string(self):
        self.assertEqual(WATERMARK_TOKEN, "{{WATERMARK}}")

    def test_incremental_real_watermark_renders_predicate(self):
        sql = build_extract_sql(_inc(custom_sql=self.CUSTOM))
        self.assertEqual(
            sql,
            f"(SELECT * FROM V a WHERE a.ACTIVE = 1 AND LAST_UPDATE_DATE > {TS}) t",
        )

    def test_incremental_with_fallback_renders_coalesce(self):
        sql = build_extract_sql(
            _inc(custom_sql=self.CUSTOM, watermark_fallback_col="CREATION_DATE")
        )
        self.assertEqual(
            sql,
            "(SELECT * FROM V a WHERE a.ACTIVE = 1 AND "
            f"COALESCE(LAST_UPDATE_DATE, CREATION_DATE) > {TS}) t",
        )

    def test_sentinel_watermark_renders_1_eq_1(self):
        # Initial backfill: never the epoch timestamp (ORA-01427, see module).
        for label, ext in (
            ("empty", _inc(custom_sql=self.CUSTOM, watermark_value="")),
            ("none", _inc(custom_sql=self.CUSTOM, watermark_value=None)),
            ("epoch", _inc(custom_sql=self.CUSTOM, watermark_value="1970-01-01T00:00:00")),
            ("missing", {"mode": "incremental", "watermark_col": "LAST_UPDATE_DATE",
                         "custom_sql": self.CUSTOM}),
        ):
            with self.subTest(watermark=label):
                sql = build_extract_sql(ext)
                self.assertEqual(sql, "(SELECT * FROM V a WHERE a.ACTIVE = 1 AND 1=1) t")
                self.assertNotIn("TO_TIMESTAMP", sql)

    def test_full_mode_renders_1_eq_1(self):
        ext = {"mode": "full", "custom_sql": self.CUSTOM}
        self.assertEqual(
            build_extract_sql(ext), "(SELECT * FROM V a WHERE a.ACTIVE = 1 AND 1=1) t"
        )

    def test_full_mode_ignores_a_real_watermark(self):
        ext = _inc(custom_sql=self.CUSTOM, mode="full")
        sql = build_extract_sql(ext)
        self.assertNotIn("TO_TIMESTAMP", sql)
        self.assertIn("AND 1=1", sql)

    def test_every_occurrence_is_rendered(self):
        custom = (
            "SELECT * FROM V a WHERE {{WATERMARK}} AND a.ID IN "
            "(SELECT ID FROM W WHERE {{WATERMARK}})"
        )
        inc = build_extract_sql(_inc(custom_sql=custom))
        self.assertNotIn(WATERMARK_TOKEN, inc)
        self.assertEqual(inc.count(f"LAST_UPDATE_DATE > {TS}"), 2)

        initial = build_extract_sql(_inc(custom_sql=custom, watermark_value=""))
        self.assertNotIn(WATERMARK_TOKEN, initial)
        self.assertEqual(initial.count("1=1"), 2)

    def test_near_miss_token_is_not_a_token(self):
        # Exact match only: a near-miss counts as no token, so it is left
        # alone on a full run and refused on an incremental one (D1).
        custom = "SELECT * FROM V WHERE {{ watermark }}"
        self.assertEqual(
            build_extract_sql({"mode": "full", "custom_sql": custom}), f"({custom}) t"
        )
        with self.assertRaises(ValueError):
            build_extract_sql(_inc(custom_sql=custom))


class TestCustomSqlWithoutToken(unittest.TestCase):
    CUSTOM = "SELECT ID, NAME FROM SPECIAL_VIEW WHERE ACTIVE=1"

    def test_full_mode_verbatim(self):
        ext = {"mode": "full", "custom_sql": self.CUSTOM}
        self.assertEqual(build_extract_sql(ext), f"({self.CUSTOM}) t")

    def test_incremental_real_watermark_raises(self):
        # D1: a silent full re-read on every incremental run is the bug.
        with self.assertRaises(ValueError) as cm:
            build_extract_sql(_inc(custom_sql=self.CUSTOM))
        self.assertIn("{{WATERMARK}}", str(cm.exception))

    def test_incremental_sentinel_is_verbatim(self):
        # The first run is a full read regardless, so it does not raise.
        for wm in ("", None, "1970-01-01T00:00:00"):
            with self.subTest(watermark=wm):
                self.assertEqual(
                    build_extract_sql(_inc(custom_sql=self.CUSTOM, watermark_value=wm)),
                    f"({self.CUSTOM}) t",
                )


class TestTablePathUnchanged(unittest.TestCase):
    """Exact strings the table path produced before UIFPE-665."""

    def test_full_select_star(self):
        self.assertEqual(
            build_extract_sql({"mode": "full", "table": TABLE}),
            "(SELECT * FROM SCHEMA.ORDERS) t",
        )

    def test_full_with_columns(self):
        self.assertEqual(
            build_extract_sql({"mode": "full", "table": TABLE, "columns": ["A", "B"]}),
            "(SELECT A, B FROM SCHEMA.ORDERS) t",
        )

    def test_incremental_sentinel_has_no_where(self):
        for label, ext in (
            ("missing", {"mode": "incremental", "table": TABLE,
                         "watermark_col": "LAST_UPDATE_DATE"}),
            ("epoch_with_fallback", _inc(table=TABLE, watermark_fallback_col="CREATION_DATE",
                                         watermark_value="1970-01-01T00:00:00")),
        ):
            with self.subTest(watermark=label):
                self.assertEqual(build_extract_sql(ext), "(SELECT * FROM SCHEMA.ORDERS) t")

    def test_incremental_real_watermark(self):
        self.assertEqual(
            build_extract_sql(_inc(table=TABLE)),
            f"(SELECT * FROM SCHEMA.ORDERS WHERE LAST_UPDATE_DATE > {TS}) t",
        )

    def test_incremental_real_watermark_with_columns(self):
        self.assertEqual(
            build_extract_sql(_inc(table=TABLE, columns=["A", "B"])),
            f"(SELECT A, B FROM SCHEMA.ORDERS WHERE LAST_UPDATE_DATE > {TS}) t",
        )

    def test_incremental_with_fallback(self):
        self.assertEqual(
            build_extract_sql(_inc(table=TABLE, watermark_fallback_col="CREATION_DATE")),
            "(SELECT * FROM SCHEMA.ORDERS WHERE "
            f"COALESCE(LAST_UPDATE_DATE, CREATION_DATE) > {TS}) t",
        )

    def test_blank_fallback_uses_primary_only(self):
        self.assertEqual(
            build_extract_sql(_inc(table=TABLE, watermark_fallback_col="  ")),
            f"(SELECT * FROM SCHEMA.ORDERS WHERE LAST_UPDATE_DATE > {TS}) t",
        )


class TestTableAndCustomPathsAgree(unittest.TestCase):
    """AC4: one predicate implementation. Compared as whole strings, so a
    change to either path's rendering fails here."""

    def _assert_same(self, **cfg):
        table_sql = build_extract_sql(_inc(table=TABLE, **cfg))
        custom_sql = build_extract_sql(
            _inc(custom_sql=f"SELECT * FROM {TABLE} WHERE {WATERMARK_TOKEN}", **cfg)
        )
        self.assertEqual(table_sql, custom_sql)
        self.assertIn(watermark_predicate(_inc(**cfg)), table_sql)

    def test_plain_watermark(self):
        self._assert_same()

    def test_with_fallback(self):
        self._assert_same(watermark_fallback_col="CREATION_DATE")


class TestRequireWatermarkColumn(unittest.TestCase):
    """run_quality_checks calls this before taking MAX(watermark). Without it a
    projection missing the column wrote None and the watermark never moved."""

    COLS = ["PROJECT_ID", "ASSET_ID", "LAST_UPDATE_DATE", "CREATION_DATE"]

    def test_present_passes(self):
        self.assertIsNone(require_watermark_column("LAST_UPDATE_DATE", None, self.COLS))

    def test_present_with_fallback_passes(self):
        self.assertIsNone(
            require_watermark_column("LAST_UPDATE_DATE", "CREATION_DATE", self.COLS)
        )

    def test_missing_primary_raises_naming_column_and_available(self):
        with self.assertRaises(ValueError) as cm:
            require_watermark_column("LAST_UPDATE_DATE", None, ["PROJECT_ID", "ASSET_ID"])
        msg = str(cm.exception)
        self.assertIn("LAST_UPDATE_DATE", msg)
        self.assertIn("PROJECT_ID", msg)
        self.assertIn("ASSET_ID", msg)

    def test_missing_fallback_raises(self):
        # D2: the SQL filters on COALESCE(primary, fallback), so a MAX over the
        # primary alone would disagree with the filter.
        with self.assertRaises(ValueError) as cm:
            require_watermark_column(
                "LAST_UPDATE_DATE", "CREATION_DATE", ["PROJECT_ID", "LAST_UPDATE_DATE"]
            )
        # Only the fallback is reported missing; the primary is present.
        self.assertIn("['CREATION_DATE'] not in", str(cm.exception))

    def test_match_is_exact_like_df_columns(self):
        with self.assertRaises(ValueError):
            require_watermark_column("LAST_UPDATE_DATE", None, ["last_update_date"])


if __name__ == "__main__":
    unittest.main()
