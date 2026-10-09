# test_job_name_drift.py
# The guard that was missing when the UIFPE-355 job renames shipped broken.
#
# Terraform renamed five Glue jobs. The pipeline scripts that call those jobs by
# name kept the old slugs, and the suite stayed green because
# qe/pipeline/test_bronze_ingest.py held a *copy* of the routing logic and the
# copy agreed with the stale original. Nothing compared either to glue.tf.
#
# So this file compares them. Every job name a script can construct must exist
# in the Terraform, and every slug the routing module declares must be a real
# job. A rename on either side fails here.

from __future__ import annotations

import re
import unittest
from pathlib import Path

from job_loader import load_pipeline_module

_TF = Path(__file__).resolve().parents[2] / "infrastructure" / "terraform"
_GOLD_TF = _TF.parent / "terraform-gold"
_PIPELINE = Path(__file__).resolve().parents[2] / "src" / "apps" / "pipeline"


def _terraform_job_slugs() -> set[str]:
    """Slugs from `name = "${local.name_prefix}-<slug>"` across the Glue files."""
    slugs: set[str] = set()
    for tf in ("glue.tf", "data_quality.tf"):
        path = _TF / tf
        if not path.is_file():
            continue
        slugs |= set(
            re.findall(
                r'name\s*=\s*"\$\{local\.name_prefix\}-([a-z0-9-]+)"',
                path.read_text(encoding="utf-8"),
            )
        )
    return slugs


class TestTerraformIsParsable(unittest.TestCase):
    def test_slugs_were_found(self):
        slugs = _terraform_job_slugs()
        self.assertTrue(slugs, "parsed no job slugs out of glue.tf")
        # Sanity: the two that must always exist.
        self.assertIn("bronze-ingest", slugs)
        self.assertIn("silver-transform", slugs)


class TestSilverRoutingMatchesTerraform(unittest.TestCase):
    """silver_routing declares the Silver job slugs; Terraform creates them."""

    def setUp(self):
        self.routing = load_pipeline_module("silver_routing.py")
        self.tf = _terraform_job_slugs()

    def test_every_declared_slug_is_a_real_job(self):
        missing = sorted(set(self.routing.SILVER_JOB_SLUGS) - self.tf)
        self.assertEqual(
            [], missing,
            f"silver_routing names {missing}, which glue.tf does not create. "
            f"Bronze would call StartJobRun on a job that does not exist — or "
            f"worse, one that exists only in the other stack.",
        )

    def test_the_renamed_slugs_are_the_new_ones(self):
        # The five UIFPE-355 renames, spelled out so a revert is loud.
        self.assertIn("silver-transform-gcs-iceberg", self.tf)
        self.assertIn("silver-transform-bq-native", self.tf)
        self.assertIn("silver-reconcile-gcs", self.tf)
        self.assertIn("silver-reconcile-bq", self.tf)
        self.assertIn("silver-transform-bq", self.tf)

    def test_the_pre_rename_slugs_are_gone(self):
        for dead in ("silver-bq-native", "silver-transform-gcp-iceberg",
                     "silver-reconcile-gcp", "silver-reconcile-gcp-bq",
                     "silver-transform-gcp-bq"):
            self.assertNotIn(
                dead, self.tf,
                f"{dead} is the pre-rename name; it exists in the live "
                f"echo-dev-* stack, so a script naming it would silently "
                f"target that stack instead of failing",
            )


class TestNoScriptDerivesAJobNameFromEnv(unittest.TestCase):
    """The root cause: names built from --env rather than passed in.

    `echo-{env}-silver-transform` resolves to a job that EXISTS in the live
    stack, so the call succeeds against the wrong lake rather than failing.
    Failing would have been safer.
    """

    # Label-only fallbacks for JOB_NAME, which Glue always supplies anyway.
    # They never select a job to run, so they cannot cross stacks.
    _LABEL_ONLY = {"analyze-table.py", "silver-transform-bq-native.py"}

    def test_no_bucket_or_job_name_is_built_from_env(self):
        offenders = []
        for path in sorted(_PIPELINE.glob("*.py")):
            if path.name in self._LABEL_ONLY:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r'f"[^"]*echo-\{(env|ENV|_env)\}', code):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual([], offenders, "\n" + "\n".join(offenders))

    def test_the_two_label_fallbacks_are_only_labels(self):
        # Permitted, but prove they assign JOB_NAME and nothing else.
        for name in self._LABEL_ONLY:
            text = (_PIPELINE / name).read_text(encoding="utf-8")
            for n, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r'echo-\{(env|ENV|_env|known\.env)\}', code):
                    self.assertIn(
                        "_prefix", code,
                        f"{name}:{n} derives a name from env outside the "
                        f"RESOURCE_PREFIX fallback",
                    )


class TestWarehousesComeFromTerraform(unittest.TestCase):
    """Iceberg warehouse roots are passed, not derived.

    A derived root writes this stack's Iceberg metadata into the other stack's
    bucket, and Iceberg metadata carries absolute paths — so the damage is not
    undone by fixing the config afterwards.
    """

    CONSUMERS = {
        "silver-transform.py": "SILVER_WAREHOUSE",
        "silver-reconcile.py": "SILVER_WAREHOUSE",
        "generic_gold_transform.py": "GOLD_WAREHOUSE",
        "generic_gold_reconcile.py": "GOLD_WAREHOUSE",
    }

    def test_each_consumer_requires_its_warehouse_argument(self):
        for name, arg in self.CONSUMERS.items():
            text = (_PIPELINE / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                # Required via getResolvedOptions, which raises when absent.
                self.assertRegex(
                    text, r"getResolvedOptions\((?:.|\n)*?%s" % arg,
                    f"{name} must take {arg} as a required argument",
                )
                self.assertIn(
                    'args["%s"]' % arg, text,
                    f"{name} declares {arg} but never reads it",
                )

    # Which root declares the jobs that pass each argument (ADR-0018: the gold
    # jobs are the gold root's).
    SUPPLIERS = {
        "--SILVER_WAREHOUSE": (_TF, ("glue.tf",)),
        "--RESOURCE_PREFIX":  (_TF, ("glue.tf", "data_quality.tf")),
        "--GOLD_WAREHOUSE":   (_GOLD_TF, ("glue_jobs.tf",)),
    }

    def test_terraform_supplies_every_one(self):
        for arg, (root, files) in self.SUPPLIERS.items():
            tf = "".join(
                (root / f).read_text(encoding="utf-8") for f in files if (root / f).is_file()
            )
            with self.subTest(arg=arg):
                self.assertIn(
                    '"%s"' % arg, tf,
                    f"{arg} is required by a script but no Glue job passes it — "
                    f"every run of that job would fail at getResolvedOptions",
                )

    def test_the_warehouse_values_track_the_bucket_variables(self):
        # Not a literal: it has to follow whichever bucket the root owns.
        # terraform fmt controls the padding around "=", so match on the pair.
        self.assertRegex(
            (_TF / "glue.tf").read_text(encoding="utf-8"),
            r'"--SILVER_WAREHOUSE"\s*=\s*"s3://\$\{var\.silver_bucket\}/"')
        self.assertRegex(
            (_GOLD_TF / "glue_jobs.tf").read_text(encoding="utf-8"),
            r'"--GOLD_WAREHOUSE"\s*=\s*"s3://\$\{var\.gold_bucket\}/"')

    def test_resource_prefix_is_the_stack_prefix(self):
        tf = (_TF / "glue.tf").read_text(encoding="utf-8")
        self.assertRegex(tf, r'"--RESOURCE_PREFIX"\s*=\s*local\.name_prefix')


if __name__ == "__main__":
    unittest.main()
