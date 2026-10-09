# test_copy_artifacts.py
#
# Pins copy-artifacts.sh -- the promotion half of the release model -- and the
# --dump mode of read-manifest.sh that feeds it.
#
# WHAT PROMOTION MEANS HERE, because the tests only make sense against it.
# Application artifacts PROMOTE: the bytes dev built and the gate passed are
# copied unchanged, and the evidence is that the digest did not move.
# Infrastructure CONVERGES: one Terraform root applied with the target
# environment's tfvars. So the properties worth pinning are that nothing is
# rebuilt, nothing is copied that the tag did not name, and the outputs match
# what the Terraform will validate.
#
# WHY SOURCE-LEVEL ASSERTIONS AND NOT AN END-TO-END RUN. Running the script
# needs S3 and a tag; the behavioural proof was done by hand against a stub
# `aws` on PATH and is recorded in the commit. What a test in CI can do
# cheaply, and what actually decays, is the set of INVARIANTS: that the proof
# still precedes the copy, that the copy still pins a version, that the tfvars
# still match variables.tf's own regexes. Each case below names the failure it
# would catch rather than restating the code.
#
# The four that earn their place:
#
#   1. copy-object with a pinned versionId. `aws s3 cp` cannot pin one, so a
#      switch to it would silently start promoting whatever is current at the
#      key instead of what the gate saw. Both halves matter -- the subcommand
#      and the ?versionId= -- so both are asserted.
#   2. Verification before any copy. This was one pass at first, and a missing
#      version on artifact 20 refused correctly after copying 19. Nothing was
#      broken (content-addressed keys cannot be wrong, only absent) but it left
#      a half-populated bucket behind a refusal.
#   3. No second manifest parser. read-manifest.sh is the only thing that reads
#      manifest shape; copy-artifacts.sh must never touch the tag itself.
#   4. --dump validates every field through the same check_leaf as a single
#      field read. A --dump that checked less would be the more dangerous of
#      the two, because it is the one a promotion uses for all 27 at once.
import os
import re
import unittest


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
COPY = os.path.join(SCRIPTS, "copy-artifacts.sh")
READ = os.path.join(SCRIPTS, "read-manifest.sh")
STAGE = os.path.join(SCRIPTS, "stage-artifacts.sh")
VARIABLES_TF = os.path.join(
    os.path.dirname(__file__), "..", "..", "infrastructure", "terraform", "variables.tf"
)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def uncommented(path):
    """Source with whole-line comments dropped.

    Every refusal in these scripts is also explained in a comment right above
    it, so a naive substring search passes on the explanation alone. Assertions
    about what the code DOES run against this.
    """
    out = []
    for line in read(path).splitlines():
        if line.strip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


class TestTheCopyIsPinned(unittest.TestCase):
    def test_uses_copy_object_not_s3_cp(self):
        src = uncommented(COPY)
        self.assertIn(
            "aws s3api copy-object", src,
            "the copy must be copy-object: it is the only form that can pin a "
            "version id",
        )
        self.assertNotRegex(
            src, r"aws s3 cp\s+\"?s3://",
            "`aws s3 cp` between buckets cannot pin a version, so it promotes "
            "whatever is currently at the key rather than what the gate saw",
        )

    def test_copy_source_pins_the_manifests_version(self):
        self.assertRegex(
            uncommented(COPY),
            r"--copy-source\s+\"\$\{SRC_BUCKET\}/\$\{key\}\?versionId=\$\{vid\}\"",
            "the copy source must carry ?versionId=<the manifest's version>",
        )

    def test_nothing_is_downloaded_or_rehashed(self):
        src = uncommented(COPY)
        for forbidden in ("artifact-hash.sh", "restore-artifacts.sh",
                          "openssl dgst", "s3api get-object"):
            self.assertNotIn(
                forbidden, src,
                "promotion does not rebuild or re-hash: the copy is "
                "server-side and no bytes cross the runner (%s)" % forbidden,
            )


class TestProofPrecedesCopy(unittest.TestCase):
    def test_there_are_two_passes(self):
        src = uncommented(COPY)
        self.assertIn("verify_source()", src)
        self.assertIn("promote()", src)

    def test_verify_runs_over_every_artifact_before_any_copy(self):
        src = uncommented(COPY)
        first_verify = src.index("verify_source \"$class\"")
        first_promote = src.index("promote \"$class\"")
        self.assertLess(
            first_verify, first_promote,
            "the verify loop must complete before the copy loop starts, or a "
            "refusal leaves a half-populated destination bucket",
        )

    def test_verify_source_makes_no_api_calls(self):
        # It answers from the listing already in hand. If it ever calls aws
        # per artifact, the pre-pass stops being free and the two-pass shape
        # becomes a cost rather than a safeguard.
        src = read(COPY)
        body = src[src.index("verify_source() {"):src.index("promote() {")]
        self.assertNotIn(
            "aws ", body,
            "verify_source must read SRC_VERSIONS, not call aws per artifact",
        )

    def test_the_destination_is_listed_by_versions_not_keys(self):
        src = uncommented(COPY)
        self.assertIn("aws s3api list-object-versions --bucket \"$TO_BUCKET\"", src)
        self.assertNotIn(
            "list-objects-v2", src,
            "the TSV records the DESTINATION's version id for every artifact, "
            "including ones already present on a re-run; a key-only listing "
            "cannot supply that and an earlier version wrote the literal "
            "'PRESENT' into that column",
        )


class TestRefusals(unittest.TestCase):
    """Each of these was exercised against a stub aws; these pin them."""

    def test_required_flags(self):
        src = uncommented(COPY)
        self.assertIn('[ -n "$TAG" ]', src)
        self.assertIn('[ -n "$TO_BUCKET" ]', src)

    def test_refuses_promoting_a_bucket_to_itself(self):
        self.assertIn(
            '[ "$SRC_BUCKET" != "$TO_BUCKET" ]', uncommented(COPY),
            "a bucket promoted to itself reports 27 skips and looks like a "
            "successful promotion that moved nothing",
        )

    def test_requires_destination_versioning(self):
        self.assertIn(
            '[ "$dest_versioning" = "Enabled" ]', uncommented(COPY),
            "without versioning every recorded version id is null, so the "
            "manifest this produces cannot identify the objects it wrote",
        )

    def test_source_bucket_is_not_a_flag(self):
        src = uncommented(COPY)
        self.assertIn('SRC_BUCKET="$(mf bucket)"', src)
        self.assertNotIn(
            "--from-bucket", src,
            "the tag says where its artifacts are; a flag would let somebody "
            "promote from the wrong bucket while every hash still checked out",
        )

    def test_the_loop_is_not_a_pipeline(self):
        # `... | while read` puts the loop in a subshell, where die exits only
        # the subshell: a refusal on artifact 9 would print and then the rest
        # would promote anyway, exiting 0.
        src = uncommented(COPY)
        self.assertNotRegex(
            src, r"mf --dump\s*\|\s*while",
            "the manifest must be read from a file, not piped into the loop",
        )
        # EVERY such loop, counted -- not just one. An earlier version of this
        # case used assertIn, which passed with one loop redirected and the
        # other reading stdin, because there are two loops over the manifest
        # and assertIn is satisfied by either. Mutation testing found it.
        loops = len(re.findall(r"while IFS=\$'\\t' read -r class", src))
        redirected = src.count('done < "$MANIFEST_ROWS"')
        self.assertEqual(
            loops, redirected,
            "all %d loops over the manifest must redirect from the file; %d "
            "do. A loop left reading stdin consumes nothing and silently "
            "iterates zero times" % (loops, redirected),
        )
        self.assertEqual(2, loops, "expected the verify pass and the copy pass")


class TestOutputsMatchTerraform(unittest.TestCase):
    def test_tfvars_emits_exactly_the_two_variables(self):
        src = uncommented(COPY)
        self.assertIn('"artifact_shas"', src)
        self.assertIn('"lambda_code_hashes"', src)

    def test_lambda_code_hashes_covers_only_lambdas(self):
        # source_code_hash exists on aws_lambda_function alone. Layers carry a
        # code_sha256 in the manifest but no Terraform argument takes it.
        self.assertIn(
            "$1 == \"lambdas\"", uncommented(COPY),
            "lambda_code_hashes must be filtered to the lambdas class",
        )

    def test_the_tfvars_writer_matches_stage_artifacts(self):
        # Terraform cannot tell which script produced its inputs, so the two
        # writers must not disagree. Compared as the awk programs themselves.
        def awk_lines(path):
            # Only the awks that read the rows file and emit tfvars. Both
            # scripts also use awk to look things up in the S3 listings, and
            # an earlier version of this filter compared those too.
            return sorted(
                l.strip() for l in uncommented(path).splitlines()
                if "awk -F'\\t'" in l and '"$ROWS"' in l
            )
        self.assertEqual(
            awk_lines(STAGE), awk_lines(COPY),
            "copy-artifacts.sh and stage-artifacts.sh must emit tfvars "
            "identically",
        )

    def test_asserted_row_count_is_derived_not_hardcoded(self):
        src = uncommented(COPY)
        self.assertIn('[ "$rows" -eq "$expected" ]', src)
        self.assertNotRegex(
            src, r'\[ "\$rows" -eq 27 \]',
            "the manifest defines the set here; a constant would defeat "
            "--dump and need editing whenever an artifact is added",
        )


class TestNoSecondManifestParser(unittest.TestCase):
    def test_copy_artifacts_never_reads_the_tag(self):
        src = uncommented(COPY)
        for forbidden in ("git tag", "git cat-file", "%(contents)"):
            self.assertNotIn(
                forbidden, src,
                "read-manifest.sh is the only thing that parses manifest "
                "shape; reading the tag here would be the second parser its "
                "header exists to prevent (%s)" % forbidden,
            )

    def test_every_manifest_read_goes_through_the_helper(self):
        self.assertIn('mf() { "$READ" "$TAG" "$@"; }', uncommented(COPY))


class TestDumpMode(unittest.TestCase):
    def test_dump_and_list_share_one_name_finder(self):
        src = uncommented(READ)
        self.assertIn("class_names()", src)
        # Both modes must call it rather than re-deriving the names.
        self.assertGreaterEqual(
            len(re.findall(r"class_names \"", src)), 2,
            "--list and --dump must both use class_names, or they can "
            "disagree about what a class contains",
        )

    def test_dump_validates_through_the_shared_checker(self):
        src = uncommented(READ)
        self.assertIn("check_leaf()", src)
        dump = src[src.index('if [ "$field" = "--dump" ]'):]
        for leaf in ("sha256", "key", "version_id"):
            self.assertIn(
                'check_leaf "${label}.%s"' % leaf, dump,
                "--dump must validate %s through check_leaf, the same "
                "function a single-field read uses" % leaf,
            )

    def test_dump_covers_the_single_artifact(self):
        # ui_bundle is in no class and the dev deploy publishes it directly
        # rather than promoting it, so it is the one that gets forgotten.
        self.assertIn("dump_row ui_bundle", uncommented(READ))

    def test_dump_label_avoids_the_set_e_trap(self):
        # `label="${class}$([ -n "$name" ] && printf ...)"` exits 1 when name
        # is empty, and under set -e that killed the script after 26 good rows
        # with no message. ui_bundle is the only empty-named artifact.
        self.assertNotRegex(
            uncommented(READ),
            r'label="\$\{class\}\$\(\[',
            "a conditional command substitution in an assignment takes the "
            "script down under set -e when the condition is false",
        )

    def test_dump_row_count_matches_the_manifest_shape(self):
        # 27 Glue + 14 Lambda + 4 layers + 1 ui_bundle. Asserted against
        # stage-artifacts.sh's own count of the 45 it defines, plus the bundle
        # it deliberately does not stage. UIFPE-576 folded the gold root into
        # this release unit: phase 1a took the Lambda zips from 5 to 14, phase
        # 1b the Glue artifacts from 17 to 26, and UIFPE-665 to 27.
        #
        # This literal is a tripwire, not the authoritative check. The gate is
        # compared against the calls that actually stage an artifact in
        # test_lambda_artifact_sets.py, which cannot go stale the way this can.
        self.assertIn(
            '[ "$rows" -eq 45 ]', uncommented(STAGE),
            "stage-artifacts.sh defines 45 artifacts; copy-artifacts.sh "
            "promotes those plus ui_bundle",
        )


class TestTerraformValidationsStillMatch(unittest.TestCase):
    """The shapes read-manifest.sh enforces must be the ones Terraform wants."""

    def test_hex_and_base64_regexes_agree_with_variables_tf(self):
        tf = read(VARIABLES_TF)
        rm = read(READ)
        self.assertIn("^[0-9a-f]{64}$", tf)
        self.assertIn("^[0-9a-f]{64}$", rm)
        self.assertIn("^[A-Za-z0-9+/]{43}=$", tf)
        self.assertIn("^[A-Za-z0-9+/]{43}=$", rm)


if __name__ == "__main__":
    unittest.main()
