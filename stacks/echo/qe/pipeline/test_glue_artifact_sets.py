# test_glue_artifact_sets.py
#
# The Glue counterpart of test_lambda_artifact_sets.py: asserts that what
# scripts/stage-artifacts.sh stages for the GOLD root is what that root's Glue
# jobs actually read.
#
# WHY. Until UIFPE-576 phase 1b the gold root uploaded its own nine Glue files
# from the repo at apply time, and nothing compared that set to anything.
# There was no Glue equivalent of the Lambda guard at all -- the same hole
# gold_frequencies.py fell through on the Lambda side, where a file missing
# from one of two lists shipped and failed at RUNTIME.
#
# Now a job's script_location is resolved from local.gold_glue_uri, which is
# built from var.artifact_shas. That makes three things able to disagree
# silently, and each one fails only when a job runs:
#
#   1. a file staged here but not declared in the Terraform map, or the reverse
#   2. a job still interpolating the fixed prefix, which never promotes
#   3. two artifacts sharing a NAME -- artifact_shas is one flat map across
#      every class, so a duplicate key silently overwrites its twin and one of
#      the two jobs is handed the other's hash
#
# None of these is visible to terraform validate, to bash -n, or to a plan in
# dev, where nothing is staged and every URI falls back to the fixed prefix.
import os
import re
import unittest


STACK_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GOLD_DIR = os.path.join(STACK_ROOT, "infrastructure", "terraform-gold")
STAGE_SH = os.path.join(STACK_ROOT, "scripts", "stage-artifacts.sh")

# Every .tf in the gold root that declares or consumes a Glue artifact.
GOLD_TF_FILES = ("locals.tf", "glue_jobs.tf", "ops_report.tf", "ops_stats.tf")

# The classes stage-artifacts.sh emits, and the one the workflow appends.
EMITTERS = ("glue_script", "lambda_zip", "lambda_zip_tree", "layer")
WORKFLOW_APPENDED = ("ui_bundle",)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _gold_tf():
    return "\n".join(_read(os.path.join(GOLD_DIR, f)) for f in GOLD_TF_FILES)


def declared_artifacts():
    """local.gold_glue_artifacts -> {name: (file, src)}."""
    text = _read(os.path.join(GOLD_DIR, "locals.tf"))
    block = re.search(r"gold_glue_artifacts\s*=\s*\{(.*?)\n  \}", text, re.S)
    if block is None:
        return {}
    out = {}
    for m in re.finditer(
        r'^\s*(\w+)\s*=\s*\{\s*file\s*=\s*"([^"]+)"\s*,\s*src\s*=\s*"([^"]+)"\s*\}',
        block.group(1), re.M,
    ):
        out[m.group(1)] = (m.group(2), m.group(3))
    return out


def s3_objects():
    """Every aws_s3_object in the gold root -> {resource: (key, source, body)}."""
    out = {}
    for f in GOLD_TF_FILES:
        text = _read(os.path.join(GOLD_DIR, f))
        for m in re.finditer(
            r'resource\s+"aws_s3_object"\s+"(\w+)"\s*\{(.*?)\n\}', text, re.S
        ):
            body = m.group(2)
            key = re.search(r'key\s*=\s*"([^"]+)"', body)
            src = re.search(r'source\s*=\s*"([^"]+)"', body)
            out[m.group(1)] = (
                key.group(1) if key else None,
                src.group(1) if src else None,
                body,
            )
    return out


def staged_calls():
    """{class: {name: path}} for every artifact stage-artifacts.sh emits."""
    text = re.sub(r"\\\n\s*", " ", _read(STAGE_SH))
    prefixes = dict(re.findall(r"\b([A-Z])=([^\s;\"]+/[^\s;\"]+)", text))
    out = {}
    for cls in EMITTERS:
        found = {}
        for m in re.finditer(r"^%s\s+(\w+)\s+(.*)$" % cls, text, re.M):
            arg = m.group(2).split()[0].strip('"')
            var, _, rest = arg.partition("/")
            if var.startswith("$") and var[1:] in prefixes:
                arg = prefixes[var[1:]] + "/" + rest
            found[m.group(1)] = arg
        out[cls] = found
    return out


class TestGoldGlueArtifactSets(unittest.TestCase):
    def setUp(self):
        self.declared = declared_artifacts()
        self.objects = s3_objects()
        self.staged = staged_calls()

    def test_both_sides_parsed(self):
        # A regex that silently matches nothing turns every assertion below
        # green. This is the tripwire for that.
        self.assertEqual(
            len(self.declared), 9,
            f"parsed {len(self.declared)} entries from local.gold_glue_artifacts, "
            "expected the four job scripts and five libraries",
        )
        self.assertEqual(
            len(self.objects), 9,
            f"parsed {len(self.objects)} aws_s3_object blocks in the gold root",
        )
        self.assertGreaterEqual(
            len(self.staged["glue_script"]), 27,
            "parsed fewer glue_script calls than the 18 silver + 9 gold that exist",
        )

    def test_every_declared_artifact_is_staged(self):
        for name, (_, src) in sorted(self.declared.items()):
            with self.subTest(artifact=name):
                self.assertIn(
                    name, self.staged["glue_script"],
                    f"local.gold_glue_artifacts declares {name} but "
                    "stage-artifacts.sh never stages it, so a release records no "
                    "hash for it and its URI becomes ARTIFACT-SHA-NOT-SUPPLIED",
                )
                self.assertEqual(
                    self.staged["glue_script"][name], src,
                    f"{name}: Terraform reads {src} and the staging script reads "
                    f"{self.staged['glue_script'][name]}. Two different files "
                    "under one artifact name.",
                )

    def test_every_staged_gold_artifact_is_declared(self):
        # The reverse direction: a file staged under a name Terraform does not
        # know is dead weight in the manifest, and reads as coverage it is not.
        gold_names = {
            n for n in self.staged["glue_script"]
            if n.startswith("gold_")
        }
        for name in sorted(gold_names):
            with self.subTest(artifact=name):
                self.assertIn(
                    name, self.declared,
                    f"stage-artifacts.sh stages {name} but no entry in "
                    "local.gold_glue_artifacts consumes it",
                )

    def test_the_content_key_uses_the_basename(self):
        # Glue FLATTENS every --extra-py-files entry into the working directory,
        # and the content key is built from `file`, so `file` has to be the
        # basename of `src` or the URI points at a key nothing was written to.
        for name, (file, src) in sorted(self.declared.items()):
            with self.subTest(artifact=name):
                self.assertEqual(
                    file, os.path.basename(src),
                    f"{name}: file is {file} but src's basename is "
                    f"{os.path.basename(src)}",
                )

    def test_every_declared_source_file_exists(self):
        for name, (_, src) in sorted(self.declared.items()):
            with self.subTest(artifact=name):
                self.assertTrue(
                    os.path.isfile(os.path.join(STACK_ROOT, src)),
                    f"{name}: {src} does not exist",
                )

    def test_each_s3_object_matches_a_declared_artifact(self):
        # The uploads and the URIs must describe the same nine files, or a dev
        # apply uploads one file while the jobs read another.
        by_src = {src: name for name, (_, src) in self.declared.items()}
        for res, (key, src, _) in sorted(self.objects.items()):
            with self.subTest(resource=res):
                self.assertIsNotNone(src, f"{res} has no source")
                rel = src.replace("${path.module}/../../", "")
                self.assertIn(
                    rel, by_src,
                    f"{res} uploads {rel}, which no entry in "
                    "local.gold_glue_artifacts declares",
                )
                self.assertEqual(
                    key, "${local.gold_scripts_prefix}/" + os.path.basename(rel),
                    f"{res}: the unstaged key must stay the fixed prefix plus the "
                    "basename, which is what the unstaged branch of "
                    "local.gold_glue_uri builds",
                )

    def test_every_upload_is_switched_off_once_staged(self):
        # A promoted environment must not upload from the repo -- that is a
        # build, and stack-promotion-model.md is explicit that uat receives
        # artifacts and never makes them.
        for res, (_, src, body) in sorted(self.objects.items()):
            with self.subTest(resource=res):
                m = re.search(r'count\s*=\s*local\.glue_staged\["(\w+)"\]', body)
                self.assertIsNotNone(
                    m, f"{res} has no `count = local.glue_staged[...]` guard, so a "
                    "promoted apply would re-upload it from the repo",
                )
                declared_src = self.declared.get(m.group(1), (None, None))[1]
                self.assertIsNotNone(
                    declared_src,
                    f"{res} guards on glue_staged[{m.group(1)!r}], which is not a "
                    "declared artifact",
                )
                self.assertEqual(
                    declared_src, src.replace("${path.module}/../../", ""),
                    f"{res} uploads {src} but guards on {m.group(1)}, whose "
                    f"declared source is {declared_src}",
                )

    def test_no_job_still_points_at_the_fixed_prefix(self):
        # The failure this catches is invisible until a promote: the job runs
        # fine in dev off the fixed prefix, and in uat reads a path nothing
        # wrote to.
        tf = _gold_tf()
        for m in re.finditer(
            r'(script_location|"--extra-py-files")\s*=\s*(.+)', tf
        ):
            with self.subTest(setting=m.group(1), value=m.group(2)[:60]):
                self.assertNotIn(
                    "gold_scripts_prefix", m.group(2),
                    "this must resolve through local.gold_glue_uri; "
                    "interpolating the fixed prefix cannot promote",
                )

    def test_every_py_files_list_is_built_from_the_uri_map(self):
        tf = _gold_tf()
        lists = re.findall(r"(\w*py_files)\s*=\s*join\(\",\", \[(.*?)\]\)", tf, re.S)
        self.assertGreaterEqual(
            len(lists), 3, "expected the CDC, ops-report and ops-stats lists"
        )
        for name, body in lists:
            with self.subTest(list=name):
                entries = [e.strip().rstrip(",") for e in body.strip().splitlines()]
                entries = [e for e in entries if e]
                self.assertTrue(entries, f"{name} is empty")
                for e in entries:
                    self.assertRegex(
                        e, r'^local\.gold_glue_uri\["\w+"\],?$',
                        f"{name} carries {e}, which is not a gold_glue_uri lookup",
                    )


class TestArtifactNamesAreUnique(unittest.TestCase):
    """artifact_shas is ONE flat map across every class."""

    def test_no_artifact_name_is_used_twice(self):
        # stage-artifacts.sh writes `"<name>": "<sha>"` per row with awk, so two
        # rows sharing a name produce a duplicate JSON key. Terraform keeps one
        # of them, and the loser's job or function is handed the wrong hash --
        # with no error anywhere.
        staged = staged_calls()
        seen = {}
        for cls, names in staged.items():
            for name in names:
                seen.setdefault(name, []).append(cls)
        for name in WORKFLOW_APPENDED:
            seen.setdefault(name, []).append("workflow")
        dupes = {n: c for n, c in seen.items() if len(c) > 1}
        self.assertEqual(
            dupes, {},
            f"artifact names used more than once: {dupes}. The manifest and "
            "var.artifact_shas are keyed by name, so one silently overwrites "
            "the other.",
        )

    def test_the_set_is_the_size_the_gates_assert(self):
        staged = staged_calls()
        total = sum(len(v) for v in staged.values())
        raw = _read(STAGE_SH)
        gate = re.search(r'\[ "\$rows" -eq (\d+) \]', raw)
        self.assertIsNotNone(gate, "stage-artifacts.sh has no row-count gate")
        self.assertEqual(
            int(gate.group(1)), total,
            f"{total} artifacts are staged but the gate asserts {gate.group(1)}",
        )


if __name__ == "__main__":
    unittest.main()
