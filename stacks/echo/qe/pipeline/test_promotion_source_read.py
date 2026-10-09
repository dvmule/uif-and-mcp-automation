# test_promotion_source_read.py
#
# Pins the cross-environment read a promote needs, and its read-only shape.
#
# WHY IT EXISTS. A promoted environment reads its artifacts out of the
# environment that BUILT them. dev is the only environment that builds --
# uif_echo_stage_release_artifacts refuses to stage into any other -- so every
# other environment's deploy role needs read access to dev's release bucket.
# That bucket appears nowhere in uat's or prod's Terraform, so
# required-actions.py cannot see it, and the s3_stack_buckets scope is
# arn:aws:s3:::{prefix}-* which covers this environment's own buckets only.
#
# HOW IT WAS MISSED, which is the part worth remembering. That scope's note in
# terraform-iam-map.json says the grant "already let the dev role reach
# uif-echo-dev-release-artifacts without a change". True -- for dev, where
# {prefix} happens to BE uif-echo-dev. For uat the prefix is uif-echo-uat and
# the source bucket falls outside it. A statement that is correct in one
# environment by coincidence is the hardest kind to notice, because the
# evidence for it is real.
#
# The first uat converge then passed the bootstrap's verify step with 225 of 225
# actions allowed and failed at the artifact copy with "cannot list
# s3://uif-echo-dev-release-artifacts". A verification built from the derivation
# can only confirm the derivation, so the one grant that cannot be derived was
# the one it could not check.
#
# apicurio hit this first (UIFPE-486) and carries the same Sid. Echo needs TWO
# actions apicurio does not: ListBucketVersions on the source, and
# ListBucketVersions on this environment's own bucket. copy-artifacts.sh pins a
# version id on every copy -- that is what proves the promoted bytes are the
# ones the gate saw -- and a version cannot be pinned without listing it.
# apicurio copies with crane and aws s3 cp, which never version-list, so its
# policy was no guide.
import json
import os
import re
import unittest


HERE = os.path.dirname(__file__)
DEPLOY = os.path.join(HERE, "..", "..", "infrastructure", "deploy")
BOOTSTRAP = os.path.join(DEPLOY, "bootstrap-environment.sh")
IAM_MAP = os.path.join(DEPLOY, "terraform-iam-map.json")
POLICY_DIR = os.path.join(HERE, "..", "..", "..", "..", "capabilities", "infrastructure")

SOURCE_BUCKET = "uif-echo-dev-release-artifacts"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def uncommented(path):
    return "\n".join(
        l for l in read(path).splitlines() if not l.strip().startswith("#")
    )


class TestTheBootstrapWritesIt(unittest.TestCase):
    def test_the_source_bucket_is_a_constant_not_derived(self):
        # dev builds and every other environment promotes FROM dev, so this is
        # a property of the model, not of the environment being bootstrapped.
        # Deriving it from TFVARS_ENV would make uat read uat's own bucket.
        self.assertRegex(
            uncommented(BOOTSTRAP),
            r'PROMOTION_SOURCE_BUCKET="%s"' % re.escape(SOURCE_BUCKET),
            "the promotion source is dev's release bucket, fixed",
        )

    def test_dev_is_skipped(self):
        # dev IS the source; its own prefix scope already covers that bucket.
        src = uncommented(BOOTSTRAP)
        self.assertRegex(
            src,
            r'\[ "\$TFVARS_ENV" = "dev" \] \|\| \[ "\$TFVARS_ENV" = "dev-uif" \]',
            "dev must not be granted a cross-environment read to itself",
        )

    def test_it_is_a_separate_policy_from_the_derived_one(self):
        # The derived policy is regenerated wholesale on every run, so a
        # hand-added statement inside it would be silently dropped next time --
        # which apicurio's own bootstrap warns about.
        src = uncommented(BOOTSTRAP)
        self.assertIn("PROMOTION_POLICY_NAME", src)
        self.assertRegex(
            src, r'PROMOTION_POLICY_NAME="\$\{PREFIX\}-promotion-source-policy"',
            "it needs its own name so re-rendering cannot lose it",
        )


class TestThePolicyIsReadOnly(unittest.TestCase):
    """A promote reads the source and writes only to its own bucket."""

    def _docs(self):
        import glob
        return sorted(glob.glob(
            os.path.join(POLICY_DIR, "uif-echo-*-promotion-source-policy.json")))

    def test_at_least_one_document_exists(self):
        self.assertTrue(self._docs(), "expected a rendered promotion-source policy")

    def test_no_write_action_anywhere(self):
        for doc in self._docs():
            d = json.loads(read(doc))
            acts = [a for s in d["Statement"] for a in s["Action"]]
            bad = [a for a in acts
                   if any(w in a for w in ("Put", "Delete", "Write", "Restore"))]
            with self.subTest(document=os.path.basename(doc)):
                self.assertEqual(
                    [], bad,
                    "a role that could write into dev's release bucket could "
                    "overwrite a content-addressed key a dev release tag still "
                    "attests to: %s" % bad,
                )

    def test_it_grants_both_arn_forms(self):
        # Bucket-level actions take the bucket ARN, object-level actions take
        # /*. A policy with only one fails halfway: the listing succeeds and
        # the copy is denied, or the reverse.
        for doc in self._docs():
            d = json.loads(read(doc))
            resources = set()
            for s in d["Statement"]:
                r = s["Resource"]
                resources.update(r if isinstance(r, list) else [r])
            with self.subTest(document=os.path.basename(doc)):
                self.assertIn("arn:aws:s3:::%s" % SOURCE_BUCKET, resources)
                self.assertIn("arn:aws:s3:::%s/*" % SOURCE_BUCKET, resources)

    def test_it_grants_the_version_listing_apicurio_does_not_need(self):
        for doc in self._docs():
            d = json.loads(read(doc))
            acts = {a for s in d["Statement"] for a in s["Action"]}
            with self.subTest(document=os.path.basename(doc)):
                self.assertIn(
                    "s3:ListBucketVersions", acts,
                    "copy-artifacts.sh pins a version id on every copy, which "
                    "requires listing the source's versions",
                )
                self.assertIn("s3:GetObjectVersion", acts)


class TestTheDerivedPolicyCoversTheDestination(unittest.TestCase):
    def test_list_bucket_versions_is_in_the_iam_map(self):
        # The DESTINATION listing is derivable -- it is an action on a scope
        # that already exists -- so it belongs in the map, not in the
        # hand-written policy.
        d = json.loads(read(IAM_MAP))
        acts = d["resources"]["aws_s3_bucket"]["actions"]
        self.assertIn(
            "s3:ListBucketVersions", acts,
            "copy-artifacts.sh lists the destination's versions so the TSV "
            "records this environment's version ids, which release-manifest.sh "
            "reads as ARTIFACT_FILE",
        )


class TestTheVerifyStepChecksWhatCannotBeDerived(unittest.TestCase):
    def test_the_bootstrap_probes_the_source_bucket(self):
        # Without these the bootstrap reports every derived action allowed and
        # still leaves the promote broken -- which is exactly what happened.
        src = uncommented(BOOTSTRAP)
        for action in ("s3:ListBucket", "s3:ListBucketVersions",
                       "s3:GetObject", "s3:GetObjectVersion"):
            self.assertRegex(
                src,
                r'simulate_one %s\s+"arn:aws:s3:::\$\{PROMOTION_SOURCE_BUCKET\}'
                % re.escape(action),
                "the verify step must probe %s on the promotion source" % action,
            )

    def test_it_also_probes_the_destination_version_listing(self):
        self.assertRegex(
            uncommented(BOOTSTRAP),
            r'simulate_one s3:ListBucketVersions\s+"arn:aws:s3:::\$\{PREFIX\}-release-artifacts"',
        )


if __name__ == "__main__":
    unittest.main()
