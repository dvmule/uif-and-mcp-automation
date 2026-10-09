# test_stack_naming.py
# The CLI scripts hardcoded `eds-{env}-*`. That is correct for prod and wrong
# for dev and uat, because there is no formula — the prefixes are per-env
# overrides in tfvars:
#
#   env    name_prefix     legacy_name_prefix   what each governs
#   dev    uif-echo-dev    echo-dev             jobs/roles vs data-holding tables
#   uat    echo-uat        echo-uat             (equal — nothing has diverged)
#   prod   eds-prod        eds-prod             (never migrated off eds-)
#
# So stack_naming reads the tfvars rather than computing anything, and these
# tests assert the reader agrees with locals.tf — including its fallbacks, so
# adding an env that sets no override cannot silently resolve to the wrong name.

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

import gold_resources
import stack_naming as sn

_TF = Path(__file__).resolve().parents[2] / "infrastructure" / "terraform"


class TestEnvironmentsComeFromDisk(unittest.TestCase):
    """The env list is derived, not typed out.

    Eleven CLI scripts offered `--env staging` after ADR-0015 D6 deleted
    staging.tfvars, and migrate_pipeline_profiles.py offered dev/staging/prod,
    omitting uat entirely. Deriving the list from the files present makes both
    classes of drift impossible.
    """

    def test_matches_the_tfvars_files_present(self):
        on_disk = {p.stem for p in (_TF / "envs").glob("*.tfvars")}
        self.assertEqual(set(sn.ENVIRONMENTS), on_disk)

    def test_staging_is_gone(self):
        self.assertNotIn("staging", sn.ENVIRONMENTS)

    def test_uat_is_present(self):
        self.assertIn("uat", sn.ENVIRONMENTS)

    def test_environments_are_sorted_for_stable_cli_help(self):
        self.assertEqual(list(sn.ENVIRONMENTS), sorted(sn.ENVIRONMENTS))


class TestDevDescribesTheLiveStackExactly(unittest.TestCase):
    """`dev` is the running echo-dev-* stack and is not being modified.

    UIFPE-355 renames dev by building a new stack beside this one, not by
    renaming this one — so `dev` must resolve to echo-dev-* throughout. It
    briefly did not: dev.tfvars carried `name_prefix_override = "uif-echo-dev"`
    while backends/dev.hcl points at the live stack's state, which made every
    plan propose 178 add / 178 destroy against the running platform.
    """

    def setUp(self):
        self.names = sn.load("dev")

    def test_both_prefixes_are_the_live_ones(self):
        self.assertEqual(self.names.name_prefix, "echo-dev")
        self.assertEqual(self.names.legacy_name_prefix, "echo-dev")

    def test_nothing_here_resolves_to_the_uif_prefix(self):
        self.assertEqual(self.names.resource("bronze-ingest"), "echo-dev-bronze-ingest")
        self.assertEqual(self.names.config_table, "echo-dev-glue-job-config")

    def test_buckets_are_the_live_ones(self):
        # The variable was renamed to artifacts_bucket; the bucket itself is
        # still echo-dev-glue-scripts, and renaming a bucket is destroy+create.
        self.assertEqual(self.names.bucket("artifacts"), "echo-dev-glue-scripts")
        self.assertEqual(self.names.bucket("bronze"), "echo-dev-bronze")
        self.assertEqual(self.names.bucket("silver"), "echo-dev-silver")

    def test_dev_tfvars_sets_no_prefix_override_at_all(self):
        """The hazard guard. Do not delete this test.

        An override in this file, whose backend key is the live state, turns
        `make plan ENV=dev` into a proposal to destroy and recreate the running
        platform. The new stack's overrides belong in dev-uif.tfvars.
        """
        raw = sn._parse_tfvars(
            (_TF / "envs" / "dev.tfvars").read_text(encoding="utf-8")
        )
        for key in ("name_prefix_override", "legacy_name_prefix_override",
                    "glue_db_prefix_override", "ui_bucket_override"):
            self.assertNotIn(
                key, raw,
                f"{key} in dev.tfvars would rename the LIVE echo-dev-* stack "
                f"in place. Put it in envs/dev-uif.tfvars instead.",
            )

    def test_the_ui_bucket_expression_still_defaults_to_the_live_shape(self):
        """locals.tf's fallback must not be 'improved' into a rename.

        Verified live: dev's UI bucket is 283556840805-s3-echo-dev-ui and it
        serves m/beacon and m/transform alongside Echo. Tidying the account-id
        prefix out of the expression, rather than overriding it per stack,
        renames that bucket — destroy + create — and takes both apps down.
        """
        locals_tf = (_TF / "locals.tf").read_text(encoding="utf-8")
        self.assertIn(
            '"${var.aws_account_id}-s3-${local.legacy_name_prefix}-ui"',
            locals_tf,
        )


class TestTheNewStackIsFullyOnTheStandardPrefix(unittest.TestCase):
    """`dev-uif` is the from-scratch uif-echo-dev-* stack.

    Built whole, deployed, verified, and only then does echo-dev-* get
    destroyed. Because nothing is being held back, there is no split between
    the two prefixes here — DynamoDB tables included.
    """

    def setUp(self):
        self.names = sn.load("dev-uif")

    def test_it_exists_as_an_environment(self):
        self.assertIn("dev-uif", sn.ENVIRONMENTS)

    def test_both_prefixes_are_uif_echo_dev(self):
        self.assertEqual(self.names.name_prefix, "uif-echo-dev")
        self.assertEqual(self.names.legacy_name_prefix, "uif-echo-dev")

    def test_the_phased_rename_mechanism_is_inert_here(self):
        # legacy_name_prefix_override is not set, so it falls through. Once
        # echo-dev-* is gone and prod is migrated, the local can be deleted.
        self.assertEqual(self.names.name_prefix, self.names.legacy_name_prefix)

    def test_tables_are_new_and_standard_named(self):
        self.assertEqual(self.names.config_table, "uif-echo-dev-pipeline-config")
        self.assertEqual(self.names.metrics_table, "uif-echo-dev-pipeline-metrics")
        self.assertEqual(
            self.names.gold_config_table, "uif-echo-dev-gold-pipeline-config"
        )

    def test_every_bucket_is_new(self):
        # gold and gold_artifacts are terraform-gold's (ADR-0018) and no longer
        # declared here, so this root only reports the three it creates.
        for kind in ("artifacts", "bronze", "silver"):
            self.assertTrue(
                self.names.bucket(kind).startswith("uif-echo-dev"),
                f"{kind}_bucket = {self.names.bucket(kind)}",
            )

    def test_no_bucket_is_shared_with_the_live_stack(self):
        live = {sn.load("dev").bucket(k) for k in sn.load("dev").buckets}
        new = {self.names.bucket(k) for k in self.names.buckets}
        self.assertEqual(set(), live & new, "the two stacks must not share a bucket")

    def test_glue_databases_do_not_land_inside_the_old_stacks(self):
        # A database name cannot contain hyphens, and the fallback is
        # echo_{environment} — without the override the new stack would create
        # its Iceberg tables inside echo_dev_silver_product.
        self.assertEqual(self.names.glue_db_prefix, "uif_echo_dev")

    def test_uif_silver_catalogs_are_tep_and_mdm(self):
        lf = (_TF / "lakeformation.tf").read_text(encoding="utf-8")
        self.assertIn("silver_tep", lf)
        self.assertIn("silver_mdm", lf)
        self.assertIn("local.is_uif_stack", lf)

    def test_glue_scripts_use_prefix_local(self):
        glue_tf = (_TF / "glue.tf").read_text(encoding="utf-8")
        locals_tf = (_TF / "locals.tf").read_text(encoding="utf-8")
        self.assertIn('glue_scripts_prefix = local.is_uif_stack ? "code" : "jobs"', locals_tf)
        self.assertIn("${local.glue_scripts_prefix}/bronze-ingest.py", glue_tf)
        self.assertNotIn("glue_jobs/", glue_tf)

    def test_data_bucket_name_in_dev_uif_tfvars(self):
        raw = sn._parse_tfvars(
            (_TF / "envs" / "dev-uif.tfvars").read_text(encoding="utf-8")
        )
        self.assertEqual(raw.get("artifacts_bucket"), "uif-echo-dev-data")

    def test_dynamodb_uses_config_table_slug(self):
        ddb = (_TF / "dynamodb.tf").read_text(encoding="utf-8")
        self.assertIn("${local.config_table_slug}", ddb)
        self.assertIn("${local.metrics_table_slug}", ddb)

    def test_schedules_are_disabled_until_cutover(self):
        raw = sn._parse_tfvars(
            (_TF / "envs" / "dev-uif.tfvars").read_text(encoding="utf-8")
        )
        self.assertEqual(
            raw.get("event_rule_state"), "DISABLED",
            "two stacks with enabled schedules double-dispatch every pipeline",
        )

    def test_the_ui_bucket_is_new_and_standard_named(self):
        raw = sn._parse_tfvars(
            (_TF / "envs" / "dev-uif.tfvars").read_text(encoding="utf-8")
        )
        self.assertEqual("uif-echo-dev-ui", raw.get("ui_bucket_override"))

    def test_it_has_its_own_terraform_state(self):
        backend = (_TF / "backends" / "dev-uif.hcl").read_text(encoding="utf-8")
        live = (_TF / "backends" / "dev.hcl").read_text(encoding="utf-8")
        self.assertIn('bucket       = "uif-echo-dev-data"', backend)
        self.assertIn('key          = "tfstate/infra-terraform.tfstate"', backend)
        self.assertNotIn("uif-echo-dev-data", live)


class TestUatHasNotDiverged(unittest.TestCase):
    def setUp(self):
        self.names = sn.load("uat")

    def test_both_prefixes_fall_back_to_the_environment(self):
        self.assertEqual(self.names.name_prefix, "echo-uat")
        self.assertEqual(self.names.legacy_name_prefix, "echo-uat")

    def test_the_cli_default_was_wrong_here(self):
        # `eds-uat-glue-job-config` does not exist.
        self.assertNotEqual(
            self.names.legacy_resource("glue-job-config"), "eds-uat-glue-job-config"
        )


class TestProdWasNeverMigratedOffEds(unittest.TestCase):
    """The CLI hardcoding was right for prod, which is why it went unnoticed."""

    def setUp(self):
        self.names = sn.load("prod")

    def test_prod_really_is_eds_prefixed(self):
        self.assertEqual(self.names.name_prefix, "eds-prod")
        self.assertEqual(self.names.legacy_name_prefix, "eds-prod")

    def test_the_old_hardcoded_names_match_prod_exactly(self):
        self.assertEqual(
            self.names.legacy_resource("glue-job-config"), "eds-prod-glue-job-config"
        )
        self.assertEqual(self.names.bucket("bronze"), "eds-prod-bronze")

    def test_glue_db_prefix_uses_underscores_and_its_own_override(self):
        self.assertEqual(self.names.glue_db_prefix, "eds_prod")


class TestFallbacksMatchLocalsTf(unittest.TestCase):
    """The reader reimplements locals.tf's ternaries, so pin them together."""

    def _local(self, name: str) -> str:
        text = (_TF / "locals.tf").read_text(encoding="utf-8")
        m = re.search(rf"^\s*{name}\s*=\s*(.+)$", text, re.MULTILINE)
        self.assertIsNotNone(m, f"{name} not found in locals.tf")
        return m.group(1)

    def test_name_prefix_falls_back_to_echo_dash_environment(self):
        self.assertIn('"echo-${var.environment}"', self._local("name_prefix"))
        self.assertEqual(sn._fallback_name_prefix("sandbox"), "echo-sandbox")

    def test_glue_db_prefix_falls_back_to_echo_underscore_environment(self):
        self.assertIn('"echo_${var.environment}"', self._local("glue_db_prefix"))
        self.assertEqual(sn._fallback_glue_db_prefix("sandbox"), "echo_sandbox")

    def test_legacy_name_prefix_falls_back_to_name_prefix(self):
        self.assertIn("local.name_prefix", self._local("legacy_name_prefix"))
        # An env with a name override but no legacy override inherits it.
        self.assertEqual(
            sn._resolve_legacy("", "uif-echo-dev"), "uif-echo-dev"
        )

    # Buckets that only a uif-echo-* stack has (UIFPE-515). s3.tf gates these
    # on local.is_uif_stack, so the legacy echo-dev / echo-uat / echo-prod
    # stacks deliberately carry no value for them: they keep the plan-time
    # zips and the s3-synced script prefix until they retire, and requiring a
    # value nobody has produced for them would block their next apply.
    #
    # This set is the exception, not a loosening. Every other declared bucket
    # must still resolve in every environment, and a NEW ungated bucket added
    # to s3.tf without a tfvars value still fails this test -- which is what it
    # is for. Delete an entry here when its bucket becomes universal, and
    # delete the set when the legacy stacks are gone.
    UIF_ONLY_BUCKETS = frozenset({"release_artifacts"})

    def test_every_bucket_the_terraform_declares_is_readable(self):
        s3 = (_TF / "s3.tf").read_text(encoding="utf-8")
        declared = set(re.findall(r"bucket\s*=\s*var\.(\w+)_bucket", s3))
        self.assertTrue(declared, "parsed no bucket variables out of s3.tf")
        for env in sn.ENVIRONMENTS:
            names = sn.load(env)
            is_uif = names.name_prefix.startswith("uif-echo")
            for kind in declared:
                if kind in self.UIF_ONLY_BUCKETS and not is_uif:
                    # Assert the absence, so this branch cannot quietly become a
                    # way for a missing value to pass unnoticed.
                    with self.assertRaises(ValueError):
                        names.bucket(kind)
                    continue
                self.assertTrue(
                    names.bucket(kind),
                    f"{env}.tfvars has no value for {kind}_bucket",
                )

    def test_uif_stacks_do_carry_the_uif_only_buckets(self):
        """The other half of the exception above: a uif stack must have them."""
        uif_envs = [
            env for env in sn.ENVIRONMENTS
            if sn.load(env).name_prefix.startswith("uif-echo")
        ]
        self.assertTrue(uif_envs, "no uif-echo-* environment found in envs/")
        for env in uif_envs:
            names = sn.load(env)
            for kind in self.UIF_ONLY_BUCKETS:
                self.assertTrue(
                    names.bucket(kind),
                    f"{env}.tfvars has no value for {kind}_bucket, but its "
                    f"name_prefix is {names.name_prefix} so s3.tf creates it",
                )


class TestTfvarsParsing(unittest.TestCase):
    def test_comments_and_blank_lines_are_ignored(self):
        parsed = sn._parse_tfvars(
            '# a comment with an = sign\n'
            '\n'
            'environment    = "dev"   # trailing comment\n'
            'count          = 3\n'
        )
        self.assertEqual(parsed["environment"], "dev")
        self.assertEqual(parsed["count"], "3")
        self.assertNotIn("# a comment with an", parsed)

    def test_a_hash_inside_a_quoted_value_is_kept(self):
        parsed = sn._parse_tfvars('tag = "sha#1"\n')
        self.assertEqual(parsed["tag"], "sha#1")

    def test_unknown_environment_names_the_ones_that_exist(self):
        with self.assertRaises(ValueError) as caught:
            sn.load("staging")
        msg = str(caught.exception)
        self.assertIn("staging", msg)
        for env in sn.ENVIRONMENTS:
            self.assertIn(env, msg)


class TestApiEndpoints(unittest.TestCase):
    """Generated, because two of the three hand-written maps omitted uat."""

    def setUp(self):
        self._saved = {
            k: v for k, v in os.environ.items()
            if "API_BASE" in k or k.endswith("_API_URL")
        }
        for k in self._saved:
            del os.environ[k]

    def tearDown(self):
        for k in [k for k in os.environ if "API_BASE" in k or k.endswith("_API_URL")]:
            del os.environ[k]
        os.environ.update(self._saved)

    def test_every_environment_has_an_entry(self):
        self.assertEqual(set(sn.api_endpoints()), set(sn.ENVIRONMENTS))

    def test_uat_is_reachable_now(self):
        os.environ["EDS_API_BASE_UAT"] = "https://uat.example/api"
        self.assertEqual(sn.api_base("uat"), "https://uat.example/api")
        self.assertEqual(sn.api_endpoints()["uat"], "https://uat.example/api")

    def test_dev_uses_the_unsuffixed_variable(self):
        os.environ["EDS_API_BASE"] = "https://dev.example/api"
        self.assertEqual(sn.api_base("dev"), "https://dev.example/api")

    def test_the_older_alias_still_works(self):
        os.environ["PROD_API_URL"] = "https://prod.example/api"
        self.assertEqual(sn.api_base("prod"), "https://prod.example/api")

    def test_the_eds_variable_wins_over_the_alias(self):
        os.environ["EDS_API_BASE_PROD"] = "https://a"
        os.environ["PROD_API_URL"] = "https://b"
        self.assertEqual(sn.api_base("prod"), "https://a")

    def test_unset_is_empty_not_an_error(self):
        self.assertEqual(sn.api_base("uat"), "")


class TestNoCliStillHardcodesAPrefix(unittest.TestCase):
    """The regression guard: this is what the commit is for."""

    _CLI = Path(__file__).resolve().parents[2] / "src" / "apps" / "cli"

    # register_gold_pipeline.py is Gold, parked per ADR-0015 D11.
    _PARKED = {"register_gold_pipeline.py"}

    # A resource that genuinely IS named eds-* and is not managed by this
    # Terraform can stay, but the exemption must be declared on the line —
    # fails closed, same idea as src/NO-IMAGE.md for the build-contract gate.
    _EXEMPT = "not-echo-managed"

    def test_no_cli_script_builds_a_resource_name_from_eds(self):
        offenders = []
        for path in sorted(self._CLI.glob("*.py")):
            if path.name in self._PARKED:
                continue
            for n, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if self._EXEMPT in line:
                    continue
                code = line.split("#", 1)[0]
                if re.search(r"""eds[-_]\{?(env|dev|uat|prod)""", code):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual([], offenders, "\n" + "\n".join(offenders))

    def test_the_exemption_marker_is_not_used_to_wave_through_a_prefix(self):
        # An exempt line must name a specific resource, not interpolate {env}
        # — anything env-parameterised is this stack's and must be resolved.
        offenders = []
        for path in sorted(self._CLI.glob("*.py")):
            for n, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if self._EXEMPT in line and re.search(r"eds[-_]\{", line):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual([], offenders, "\n" + "\n".join(offenders))

    def test_no_cli_script_offers_staging(self):
        offenders = []
        for path in sorted(self._CLI.glob("*.py")):
            for n, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                code = line.split("#", 1)[0]
                if '"staging"' in code or "'staging'" in code:
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual([], offenders, "\n" + "\n".join(offenders))



class TestGoldNamesHaveOneAuthority(unittest.TestCase):
    """Gold was renamed on its own timetable (ADR-0018), so three things have to
    agree on its names: `gold_resources` (the dispatcher and every CLI), this
    module (the control plane and the scripts), and the echo root's Terraform.
    They disagreed once already, and the failure is invisible — a caller simply
    reads a table that does not exist and the gold pages come back empty."""

    def test_stack_naming_delegates_rather_than_respelling(self):
        # The token is the tfvars `environment`, not the file stem: dev-uif is a
        # second STACK inside the dev environment, and there is one gold estate
        # per environment, so its gold names are dev's.
        for env in sn.ENVIRONMENTS:
            names = sn.load(env)
            with self.subTest(env=env):
                self.assertEqual(
                    names.gold_config_table,
                    gold_resources.gold_config_table(names.environment),
                )

    def test_a_second_stack_in_one_environment_shares_that_gold_estate(self):
        self.assertEqual(
            sn.load("dev-uif").gold_config_table, sn.load("dev").gold_config_table
        )

    def test_the_standard_form_is_what_both_produce(self):
        self.assertEqual(
            sn.load("dev").gold_config_table, "uif-echo-dev-gold-pipeline-config"
        )
        self.assertEqual(
            sn.load("uat").gold_config_table, "uif-echo-uat-gold-pipeline-config"
        )

    def test_the_terraform_derives_the_same_prefix(self):
        # locals.tf must not take the gold prefix per environment, and must not
        # borrow this stack's: one gold estate per env, one spelling.
        locals_tf = (_TF / "locals.tf").read_text(encoding="utf-8")
        self.assertRegex(
            locals_tf, r'gold_prefix\s*=\s*"uif-echo-\$\{var\.environment\}"'
        )
        for slug in ("gold-pipeline-config", "gold-transform", "gold-reconcile",
                     "gold-artifacts"):
            self.assertIn(f'${{local.gold_prefix}}-{slug}"', locals_tf)

    def test_the_echo_root_no_longer_takes_the_gold_buckets_per_env(self):
        # They were variables whose dev value named buckets deleted at the
        # cutover. Derived now, so there is nothing to leave stale.
        # Matched as a pattern, not two literals: "gold_bucket" is not a
        # substring of "gold_artifacts_bucket", so naming them one by one guards
        # whichever is spelled and silently misses the other.
        any_gold_bucket = r"gold\w*_bucket"
        self.assertNotRegex((_TF / "variables.tf").read_text(encoding="utf-8"), any_gold_bucket)
        for env in sn.ENVIRONMENTS:
            tfvars = (_TF / "envs" / f"{env}.tfvars").read_text(encoding="utf-8")
            self.assertNotRegex(tfvars, any_gold_bucket, f"{env}.tfvars still sets a gold bucket")


if __name__ == "__main__":
    unittest.main()
