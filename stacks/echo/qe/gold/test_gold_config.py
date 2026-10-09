# test_gold_config.py
# Unit tests for scripts/gold_config.py — the config-as-code control plane's
# validation, auto-init, and overlay merge (ADR-0004).

import unittest

import gold_config


def valid_body():
    return {
        "gold_id": "product",
        "domain": "product",
        "schema": {
            "master_s3_path": "s3://arts/schemas/gold/product/v1/master.json",
            "version": "v1",
        },
        "mapping": {
            "manifest_s3_path": "s3://arts/mappings/product/v1/manifest.yaml",
            "sql_s3_path": "s3://arts/mappings/product/v1/sql/product.sql",
        },
        "sources": [
            {"alias": "items",
             "silver_table": "silver_product.mtl_system_items_b",
             "is_source_of_truth": True},
            {"alias": "items_ext",
             "silver_table": "silver_product.ego_mtl_sy_items_ext_b"},
        ],
        "target": {
            "database": "gold_product",
            "table": "product",
            "s3_path": "s3://gold/product/",
            "primary_key_cols": ["inventory_item_id", "org_id"],
        },
    }


class TestValidate(unittest.TestCase):
    def test_valid_passes(self):
        self.assertIsNone(gold_config.validate(valid_body()))

    def test_missing_top_field(self):
        b = valid_body(); del b["mapping"]
        self.assertIn("mapping", gold_config.validate(b))

    def test_empty_pk(self):
        b = valid_body(); b["target"]["primary_key_cols"] = []
        self.assertIn("primary_key_cols", gold_config.validate(b))

    def test_non_s3_paths_rejected(self):
        b = valid_body(); b["target"]["s3_path"] = "/local/path"
        self.assertIn("s3://", gold_config.validate(b))

    def test_source_neecho_alias(self):
        b = valid_body(); del b["sources"][1]["alias"]
        self.assertIn("alias", gold_config.validate(b))

    def test_exactly_one_source_of_truth(self):
        b = valid_body(); b["sources"][1]["is_source_of_truth"] = True
        self.assertIn("is_source_of_truth", gold_config.validate(b))
        b = valid_body(); b["sources"][0]["is_source_of_truth"] = False
        self.assertIn("is_source_of_truth", gold_config.validate(b))

    def test_duplicate_aliases_rejected(self):
        b = valid_body(); b["sources"][1]["alias"] = "items"
        self.assertIn("unique", gold_config.validate(b))

    def test_watermark_shapes(self):
        b = valid_body()
        # ADR-0020: one bookmark string; the retired per-source map is
        # tolerated on a row until the next run REMOVEs it
        b["watermark"] = {"bookmark": "2026-09-23T06:31:13+00:00"}
        self.assertIsNone(gold_config.validate(b))
        b["watermark"] = {"last_processed_silver_updated_at": {"items": "x"}}
        self.assertIsNone(gold_config.validate(b))
        b["watermark"] = {"bookmark": {"items": "x"}}
        self.assertIn("watermark.bookmark", gold_config.validate(b))
        b["watermark"] = {"bookmark": 42}
        self.assertIn("watermark.bookmark", gold_config.validate(b))

    def test_mode_enums(self):
        b = valid_body(); b["backfill_mode"] = "loud"
        self.assertIn("backfill_mode", gold_config.validate(b))
        b = valid_body(); b["channels"] = {"bootstrap_mode": "shout"}
        self.assertIn("bootstrap_mode", gold_config.validate(b))

    def test_schedule_frequency_enum(self):
        b = valid_body(); b["schedule"] = {"frequency": "gold-1h"}
        self.assertIsNone(gold_config.validate(b))
        b["schedule"] = {"frequency": "every-minute"}
        self.assertIn("frequency", gold_config.validate(b))

    def test_every_cadence_on_the_ladder_validates(self):
        """UIFPE-149: eleven rungs. If validate() falls behind the ladder, a
        legitimately-authored config is rejected at sync time."""
        from gold_frequencies import TRANSFORM_FREQUENCIES
        for freq in TRANSFORM_FREQUENCIES:
            b = valid_body(); b["schedule"] = {"frequency": freq}
            self.assertIsNone(gold_config.validate(b), f"{freq} was rejected")

    def test_legacy_aliases_still_validate(self):
        """An environment whose overlay has not been migrated must still sync —
        otherwise the rename becomes a hard cutover across every env at once.
        The dispatcher normalizes these to their canonical rung."""
        for alias in ("gold", "gold-hourly", "gold-60m"):
            b = valid_body(); b["schedule"] = {"frequency": alias}
            self.assertIsNone(gold_config.validate(b), f"{alias} was rejected")

    def test_start_time_must_be_a_time_of_day(self):
        """REJECTED at sync time on purpose. The dispatcher deliberately falls
        back to the cadence default so one bad field cannot stop a pipeline
        mid-flight — which means if validate() let a typo through, the pipeline
        would run at the wrong time forever with no error anywhere."""
        for bad in ("25:00", "06:60", "half six", "0630", ":30", "6:30pm", 630):
            b = valid_body()
            b["schedule"] = {"frequency": "gold-4h", "start_time": bad}
            self.assertIn("start_time", gold_config.validate(b) or "",
                          f"{bad!r} was accepted")

    def test_valid_start_times_pass(self):
        for good in ("00:00", "06:30", "23:59", "6:30"):
            b = valid_body()
            b["schedule"] = {"frequency": "gold-4h", "start_time": good}
            self.assertIsNone(gold_config.validate(b), f"{good!r} was rejected")

    def test_auto_init_gives_each_cadence_its_historical_clock(self):
        """Filling in a default must not MOVE a running pipeline: a config that
        named no start time keeps the clock its cadence had before UIFPE-149."""
        from gold_frequencies import TRANSFORM_FREQUENCIES, default_start_time
        for freq in TRANSFORM_FREQUENCIES:
            b = valid_body()
            b["schedule"] = {"frequency": freq}
            gold_config.auto_init(b)
            self.assertEqual(b["schedule"]["start_time"],
                             default_start_time(freq))
        # The two that were live before the change, stated explicitly.
        b = valid_body(); b["schedule"] = {"frequency": "gold"}
        gold_config.auto_init(b)
        self.assertEqual(b["schedule"]["start_time"], "06:30")

    def test_weekly_days_only_on_the_weekly_rung(self):
        """UIFPE-532: a day set beside a non-weekly cadence is a config that
        says two things; rejected at sync time like a bad start_time."""
        b = valid_body()
        b["schedule"] = {"frequency": "gold-24h", "weekly_days": ["TUE"]}
        self.assertIn("weekly_days", gold_config.validate(b))
        b["schedule"] = {"frequency": "gold-weekly", "weekly_days": ["TUE", "THU"]}
        self.assertIsNone(gold_config.validate(b))
        b["schedule"] = {"frequency": "gold-weekly"}         # default day set
        self.assertIsNone(gold_config.validate(b))

    def test_weekly_days_must_be_a_non_empty_list_of_weekdays(self):
        for bad in ([], ["FUNDAY"], "TUE", ["TUE", 3], {"TUE": True}, 5):
            b = valid_body()
            b["schedule"] = {"frequency": "gold-weekly", "weekly_days": bad}
            self.assertIn("weekly_days", gold_config.validate(b) or "",
                          f"{bad!r} was accepted")
        for good in (["MON"], ["tue", "Thu"], ["SAT", "SUN", "SAT"]):
            b = valid_body()
            b["schedule"] = {"frequency": "gold-weekly", "weekly_days": good}
            self.assertIsNone(gold_config.validate(b), f"{good!r} was rejected")

    def test_auto_init_states_the_weekly_day_set_and_keeps_a_chosen_one(self):
        b = valid_body(); b["schedule"] = {"frequency": "gold-weekly"}
        gold_config.auto_init(b)
        self.assertEqual(b["schedule"]["weekly_days"], ["MON"])
        self.assertEqual(b["schedule"]["start_time"], "06:30")
        b = valid_body()
        b["schedule"] = {"frequency": "gold-weekly", "weekly_days": ["TUE", "THU"]}
        gold_config.auto_init(b)
        self.assertEqual(b["schedule"]["weekly_days"], ["TUE", "THU"])
        # and a day-tiling rung gets no day set at all
        b = valid_body(); b["schedule"] = {"frequency": "gold-24h"}
        gold_config.auto_init(b)
        self.assertNotIn("weekly_days", b["schedule"])

    def test_auto_init_does_not_overwrite_a_chosen_start_time(self):
        b = valid_body()
        b["schedule"] = {"frequency": "gold-4h", "start_time": "14:45"}
        gold_config.auto_init(b)
        self.assertEqual(b["schedule"]["start_time"], "14:45")

    def test_failure_cooldown_must_be_a_non_negative_int(self):
        for bad in (-1, "30", 30.0, True):
            b = valid_body()
            b["schedule"] = {"frequency": "gold-5m",
                             "failure_cooldown_minutes": bad}
            self.assertIn("failure_cooldown_minutes",
                          gold_config.validate(b) or "",
                          f"{bad!r} was accepted")
        # 0 is a legitimate opt-out, not a typo.
        b = valid_body()
        b["schedule"] = {"frequency": "gold-5m", "failure_cooldown_minutes": 0}
        self.assertIsNone(gold_config.validate(b))


class TestCapacityProfileValidation(unittest.TestCase):
    """F1/F2 config additions: strict-bool gate flag, typed capacity keys,
    full_load restricted to capacity keys only."""

    def test_snapshot_gate_must_be_bool(self):
        body = valid_body()
        body["snapshot_gate"] = "false"
        self.assertIn("boolean", gold_config.validate(body))
        body["snapshot_gate"] = False
        self.assertIsNone(gold_config.validate(body))

    def test_capacity_keys_typed(self):
        body = valid_body()
        body["glue"] = {"num_workers": "sixteen"}
        self.assertIn("positive integer", gold_config.validate(body))
        body["glue"] = {"num_workers": 0}
        self.assertIn("positive integer", gold_config.validate(body))
        body["glue"] = {"num_workers": True}   # bool is not an int here
        self.assertIn("positive integer", gold_config.validate(body))

    def test_full_load_rejects_non_capacity_keys(self):
        body = valid_body()
        body["glue"] = {"full_load": {"shuffle_partitions": 64}}
        err = gold_config.validate(body)
        self.assertIn("full_load", err)
        self.assertIn("shuffle_partitions", err)

    def test_full_load_capacity_keys_typed(self):
        body = valid_body()
        body["glue"] = {"full_load": {"timeout_minutes": -1}}
        self.assertIn("glue.full_load.timeout_minutes",
                      gold_config.validate(body))

    def test_auto_init_full_load_defaults_are_heavy_not_lean(self):
        # The undersized-CTAS trap: a lean glue block must NOT become the
        # full_load default.
        body = valid_body()
        body["glue"] = {"worker_type": "G.1X", "num_workers": 2,
                        "timeout_minutes": 30}
        out = gold_config.auto_init(dict(body))
        self.assertEqual(out["glue"]["full_load"],
                         {"worker_type": "G.2X", "num_workers": 16,
                          "timeout_minutes": 180})
        # lean values untouched
        self.assertEqual(out["glue"]["worker_type"], "G.1X")

    def test_auto_init_gate_defaults_on(self):
        out = gold_config.auto_init(valid_body())
        self.assertIs(out["snapshot_gate"], True)


class TestAutoInit(unittest.TestCase):
    def test_initializes_all_defaults(self):
        b = gold_config.auto_init(valid_body(), now="2026-07-21T00:00:00+00:00")
        self.assertEqual(b["version"], "latest")
        self.assertTrue(b["enabled"])
        # ADR-0020: no per-source seed - the first run writes the bookmark
        self.assertEqual(b["watermark"], {})
        # UIFPE-149 renamed the daily cadence; same 06:30 UTC clock.
        self.assertEqual(b["schedule"]["frequency"], "gold-24h")
        self.assertEqual(b["schedule"]["failure_cooldown_minutes"], 30)
        self.assertEqual(b["channels"], {"registered": [], "bootstrap_mode": "silent"})
        self.assertEqual(b["materialized_properties"], [])
        self.assertEqual(b["backfill_mode"], "silent")
        self.assertEqual(b["hash_spec_version"], 1)
        self.assertEqual(b["retention"], {"changes_days": 365, "snapshot_days": 14})
        self.assertFalse(b["reconcile"]["enabled"])
        self.assertEqual(b["metadata"]["registered_at"], "2026-07-21T00:00:00+00:00")

    def test_idempotent_preserves_existing_values(self):
        b = valid_body()
        b["watermark"] = {"bookmark": "2026-06-01T00:00:00+00:00"}
        b["channels"] = {"registered": ["api", "canonical"], "bootstrap_mode": "emit"}
        b["enabled"] = False
        out = gold_config.auto_init(b)
        self.assertEqual(out["watermark"]["bookmark"], "2026-06-01T00:00:00+00:00")
        self.assertEqual(out["channels"]["registered"], ["api", "canonical"])
        self.assertEqual(out["channels"]["bootstrap_mode"], "emit")
        self.assertFalse(out["enabled"])


class TestDeepMerge(unittest.TestCase):
    def test_overlay_wins_scalars_and_merges_dicts(self):
        base = {"a": 1, "nest": {"x": 1, "y": 2}, "list": [1, 2]}
        overlay = {"a": 9, "nest": {"y": 99, "z": 3}, "list": [3]}
        out = gold_config.deep_merge(base, overlay)
        self.assertEqual(out["a"], 9)
        self.assertEqual(out["nest"], {"x": 1, "y": 99, "z": 3})
        self.assertEqual(out["list"], [3])  # lists replace wholesale

    def test_base_unmodified(self):
        base = {"nest": {"x": 1}}
        gold_config.deep_merge(base, {"nest": {"x": 2}})
        self.assertEqual(base["nest"]["x"], 1)


class TestRecomputeBlock(unittest.TestCase):
    """ADR-0020 decision 5. Every refusal here is SILENT at runtime — the
    dispatcher falls back rather than crashing a sweep over one config — so the
    sync is the only place a typo is caught, and each refusal needs a control
    proving it actually fires. The refresh block it replaces shipped with none.
    """

    def _body(self, recompute):
        b = valid_body()
        b.setdefault("schedule", {})["recompute"] = recompute
        return b

    def test_a_declared_leg_passes(self):
        self.assertIsNone(gold_config.validate(self._body(
            {"undetectable_columns": ["a", "b"], "every_n_runs": 8})))

    def test_every_n_runs_is_optional(self):
        self.assertIsNone(gold_config.validate(self._body(
            {"undetectable_columns": ["a"]})))

    def test_not_a_map_is_rejected(self):
        self.assertIn("must be a map",
                      gold_config.validate(self._body(["a"])))

    def test_columns_is_rejected_by_name(self):
        """Same refusal the refresh block carries: this is not the set of
        columns to load, and the wrong mental model must not survive in YAML."""
        msg = gold_config.validate(self._body({"columns": ["a"]}))
        self.assertIn("undetectable_columns", msg)

    def test_a_cadence_of_its_own_is_rejected(self):
        """Decision 4 retires the second schedule: the leg is placed on the
        entity's one schedule by ratio, so these three have no meaning here."""
        for key, value in (("frequency", "gold-24h"),
                           ("start_time", "07:15"),
                           ("mode", "emit")):
            msg = gold_config.validate(self._body(
                {"undetectable_columns": ["a"], key: value}))
            self.assertIn("every_n_runs", msg, f"{key} was accepted")

    def test_unknown_key_is_rejected(self):
        self.assertIn("unknown", gold_config.validate(self._body(
            {"undetectable_columns": ["a"], "boundary": "gold-2h"})))

    def test_an_empty_column_list_is_rejected(self):
        """A leg naming nothing would be scheduled forever and repair nothing."""
        for cols in ([], None, "priceSpecifications", ["  "]):
            self.assertIn("undetectable_columns", gold_config.validate(
                self._body({"undetectable_columns": cols})),
                f"{cols!r} was accepted")

    def test_every_n_runs_must_be_a_positive_integer(self):
        for bad in (0, -1, True, 1.5, "8"):
            self.assertIn("every_n_runs", gold_config.validate(self._body(
                {"undetectable_columns": ["a"], "every_n_runs": bad})),
                f"{bad!r} was accepted")

    def test_one_means_every_scheduled_run(self):
        self.assertIsNone(gold_config.validate(self._body(
            {"undetectable_columns": ["a"], "every_n_runs": 1})))

    def test_auto_init_states_the_ratio_rather_than_inheriting_it(self):
        b = self._body({"undetectable_columns": ["a"]})
        gold_config.auto_init(b)
        self.assertEqual(b["schedule"]["recompute"]["every_n_runs"], 8)

    def test_auto_init_does_not_overwrite_a_chosen_ratio(self):
        b = self._body({"undetectable_columns": ["a"], "every_n_runs": 4})
        gold_config.auto_init(b)
        self.assertEqual(b["schedule"]["recompute"]["every_n_runs"], 4)

    def test_auto_init_adds_no_leg_where_none_is_declared(self):
        b = valid_body()
        gold_config.auto_init(b)
        self.assertNotIn("recompute", b["schedule"])


if __name__ == "__main__":
    unittest.main()
