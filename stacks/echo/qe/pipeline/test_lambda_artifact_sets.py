# test_lambda_artifact_sets.py
#
# Asserts scripts/stage-artifacts.sh packs each Lambda from the same file set
# that lambda.tf's archive_file block declares for it.
#
# WHY. The two lists are the same knowledge written twice, and on 2026-09-23
# they disagreed by one file. stage-artifacts.sh's pipeline_controller list
# omitted src/libs/gold_frequencies.py, which pipeline_controller.py:48 imports
# (`from gold_frequencies import is_transform_frequency, parse_time_of_day`).
# The zip built and uploaded fine, the Terraform apply succeeded, and then every
# control-plane request returned 500:
#
#   [ERROR] Runtime.ImportModuleError: Unable to import module
#           'pipeline_controller': No module named 'gold_frequencies'
#
# Nothing before this test could have caught it. The staging script only checks
# that each file it was TOLD about exists, so a file it was never told about is
# invisible to it; the apply only sees a zip and a hash; and the failure is at
# Lambda INIT, which is after everything CI and Terraform look at.
# stage-artifacts.sh's own comment claims these lists "mirror what lambda.tf's
# archive_file blocks used to assemble" -- this is what makes that true rather
# than aspirational.
#
# WHY COMPARE AGAINST archive_file, which uif stacks no longer use. Because it
# is the set that demonstrably worked: the legacy echo-* stacks still build
# their zips from it, so it stays correct and maintained, and it is the only
# independent record of what each function needs. When the last legacy stack
# retires and those blocks go, this test has to be replaced by a real import
# closure check, not deleted -- there would be nothing else asserting the sets.
import os
import re
import unittest


STACK_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INFRA_DIR = os.path.join(STACK_ROOT, "infrastructure", "terraform")
GOLD_DIR = os.path.join(STACK_ROOT, "infrastructure", "terraform-gold")
STAGE_SH = os.path.join(STACK_ROOT, "scripts", "stage-artifacts.sh")
# The second row-count gate lives in the workflow: stage-artifacts.sh asserts
# the rows it writes, then the UI step appends one more and asserts that.
RELEASE_YML = os.path.join(
    STACK_ROOT, "..", "..", ".github", "workflows", "release-echo.yml"
)

# Both functions' archive_file blocks, since the fifth lives in data_quality.tf
# rather than lambda.tf -- which is why lambda.tf's header says "five" while
# four resources sit in it, and why an inventory that greps lambda.tf alone
# undercounts. That miscount already cost this ticket one correction.
TF_FILES = ("lambda.tf", "data_quality.tf")

# UIFPE-576: the GOLD root's functions are in the SAME release unit, so the
# same drift check has to cover them. Gold folds into echo/v* because `gold`
# is a {layer} in the naming vocabulary and not a {stack}, and the promotion
# model is stack-scoped.
GOLD_TF_FILES = ("ai_draft.tf", "alerts.tf", "announce.tf", "compile_preview.tf",
                 "dispatcher.tf", "grain_probe.tf", "submit.tf", "serving.tf",
                 "serving_api.tf")

# compile_preview is staged from a BUILT TREE (lambda_zip_tree), not a file
# list, because its zip carries a pip-installed sqlglot. It has no file set on
# either side, so it is asserted separately rather than silently skipped --
# see test_compile_preview_is_staged_as_a_tree.
TREE_ARTIFACTS = frozenset({"compile_preview"})


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def archive_file_sets():
    """{function: {basename, ...}} from every archive_file block, BOTH roots."""
    text = "".join(_read(os.path.join(INFRA_DIR, n)) for n in TF_FILES)
    text += "".join(_read(os.path.join(GOLD_DIR, n)) for n in GOLD_TF_FILES)
    out = {}
    for m in re.finditer(r'data\s+"archive_file"\s+"(\w+)"\s*\{(.*?)\n\}', text, re.S):
        name, body = m.group(1), m.group(2)
        # Every .py path in the block, reduced to basenames. The block spells
        # each file twice -- once as a source path under ${path.module} and once
        # as the name inside the zip -- so basenames collapse the pair.
        files = {os.path.basename(p) for p in re.findall(r'"([^"]*\.py)"', body)}
        if files:
            out[name] = files
    return out


def staged_sets():
    """{function: {basename, ...}} from stage-artifacts.sh's lambda_zip calls."""
    text = _read(STAGE_SH)
    # Join shell line continuations so a call spanning three lines reads as one.
    text = re.sub(r"\\\n\s*", " ", text)
    out = {}
    for m in re.finditer(r"^lambda_zip\s+(\w+)\s+(.*)$", text, re.M):
        name, args = m.group(1), m.group(2)
        # A spec is "src" or "src:dest-inside-the-zip"; compare on the
        # SOURCE basename, which is what archive_file's own path yields.
        out[name] = {
            os.path.basename(spec.split(":", 1)[0])
            for spec in re.findall(r'"\$[A-Z]+/([^"]+)"', args)
        }
    return out


def archive_file_placements():
    """{function: {path-inside-the-zip, ...}} from each block's `filename`."""
    text = "".join(_read(os.path.join(INFRA_DIR, n)) for n in TF_FILES)
    text += "".join(_read(os.path.join(GOLD_DIR, n)) for n in GOLD_TF_FILES)
    out = {}
    for m in re.finditer(r'data\s+"archive_file"\s+"(\w+)"\s*\{(.*?)\n\}', text, re.S):
        names = set(re.findall(r'filename\s*=\s*"([^"]+)"', m.group(2)))
        if names:
            out[m.group(1)] = names
    return out


def staged_placements():
    """{function: {path-inside-the-zip, ...}} from stage-artifacts.sh."""
    text = re.sub(r"\\\n\s*", " ", _read(STAGE_SH))
    out = {}
    for m in re.finditer(r"^lambda_zip\s+(\w+)\s+(.*)$", text, re.M):
        dests = set()
        for spec in re.findall(r'"\$[A-Z]+/([^"]+)"', m.group(2)):
            src, sep, dest = spec.partition(":")
            dests.add(dest if sep else os.path.basename(src))
        out[m.group(1)] = dests
    return out


class TestLambdaArtifactSets(unittest.TestCase):
    def setUp(self):
        self.tf = archive_file_sets()
        self.staged = staged_sets()

    def test_both_sides_parsed(self):
        # Guards the test itself. Both parsers read text, so a formatting change
        # could silently yield {} and make every assertion below vacuous -- the
        # failure mode this whole file exists to prevent, one level up.
        # 5 silver + 8 gold. compile_preview is a tree on both sides, so it
        # appears in neither map and is covered by its own test.
        self.assertEqual(
            len(self.tf), 13,
            f"expected 13 archive_file blocks, parsed {sorted(self.tf)}"
        )
        self.assertEqual(
            len(self.staged),
            13,
            f"expected 13 lambda_zip calls, parsed {sorted(self.staged)}",
        )
        for name, files in self.tf.items():
            self.assertTrue(files, f"archive_file.{name} parsed to an empty set")
        for name, files in self.staged.items():
            self.assertTrue(files, f"lambda_zip {name} parsed to an empty set")

    def test_the_same_functions_on_both_sides(self):
        self.assertEqual(sorted(self.staged), sorted(self.tf))

    def test_compile_preview_is_staged_as_a_tree(self):
        """It has no file list on either side, so the set comparison above
        cannot cover it. Assert both halves agree it is a TREE: source_dir in
        the Terraform, lambda_zip_tree in the staging script. Without this the
        artifact could vanish from the release entirely and every other
        assertion here would still pass."""
        tf = _read(os.path.join(GOLD_DIR, "compile_preview.tf"))
        self.assertIn("source_dir", tf,
                      "compile_preview no longer builds a tree")
        self.assertRegex(
            _read(STAGE_SH),
            r"lambda_zip_tree\s+compile_preview",
            "compile_preview is not staged, so echo/v* would carry no "
            "artifact for it and a uat converge would rebuild it from source",
        )
        for name in TREE_ARTIFACTS:
            self.assertNotIn(name, self.tf, f"{name} should have no file list")
            self.assertNotIn(name, self.staged, f"{name} should have no file list")

    def test_file_sets_match(self):
        for name in sorted(self.tf):
            with self.subTest(function=name):
                missing = self.tf[name] - self.staged.get(name, set())
                extra = self.staged.get(name, set()) - self.tf[name]
                self.assertEqual(
                    (missing, extra),
                    (set(), set()),
                    f"{name}: stage-artifacts.sh is missing {sorted(missing)} and "
                    f"carries {sorted(extra)} that archive_file does not. A file "
                    f"missing here ships a zip whose function fails at INIT with "
                    f"Runtime.ImportModuleError, which no earlier gate sees.",
                )

    def test_each_file_lands_in_the_SAME_PLACE_inside_the_zip(self):
        """Basenames are not enough. gold_alert_notify puts two files in a
        PACKAGE directory -- pearson_notifications/__init__.py -- and a zip that
        flattens them to the root imports nothing, so no alert mail is ever
        sent. That failure is invisible to the basename comparison above: the
        source file is present either way.
        """
        tf, staged = archive_file_placements(), staged_placements()
        self.assertTrue(tf, "no filename= values parsed; the check would be vacuous")
        # At least one function must actually exercise a package path, or this
        # test passes for a trivial reason.
        self.assertTrue(
            any("/" in p for paths in tf.values() for p in paths),
            "no archive_file places a file in a subdirectory, so this test "
            "cannot be proving anything",
        )
        for name in sorted(set(tf) & set(staged)):
            with self.subTest(function=name):
                self.assertEqual(
                    staged[name],
                    tf[name],
                    f"{name}: stage-artifacts.sh puts files at {sorted(staged[name])} "
                    f"while archive_file puts them at {sorted(tf[name])}. A file in "
                    f"the wrong place inside the zip fails at INIT, not at apply.",
                )

    def test_every_staged_file_exists(self):
        # stage-artifacts.sh dies on a missing path at run time; failing here is
        # cheaper than failing mid-release.
        text = re.sub(r"\\\n\s*", " ", _read(STAGE_SH))
        # The three prefixes share one line -- `L=src/apps/lambdas;
        # C=src/apps/control_panel; B=src/libs` -- so match every assignment
        # rather than one per line, which picked up only the last.
        # NOT anchored to src/ any more: gold_alert_notify's package comes
        # from ../../platform/python/pearson_notifications, outside the stack
        # root (platform/python/README.md).
        prefixes = dict(re.findall(r"\b([A-Z])=([^\s;\"]+/[^\s;\"]+)", text))
        for m in re.finditer(r"^lambda_zip\s+(\w+)\s+(.*)$", text, re.M):
            for var, rel in re.findall(r'"\$([A-Z])/([^"]+)"', m.group(2)):
                rel = rel.split(":", 1)[0]
                path = os.path.join(STACK_ROOT, prefixes.get(var, var), rel)
                with self.subTest(function=m.group(1), file=rel):
                    self.assertTrue(
                        os.path.isfile(path), f"{prefixes.get(var, var)}/{rel} not found"
                    )


class TestStageArtifactsIsRunnable(unittest.TestCase):
    """The checks above read the script as text. These two ask whether that text
    is a script that can RUN, which is a different question -- and the one that
    was not being asked when gold's entries were added."""

    # Every call that appends a row to the TSV.
    EMITTERS = ("glue_script", "lambda_zip", "lambda_zip_tree", "layer")

    def test_every_helper_is_defined_before_it_is_called(self):
        # bash executes top-down: a call above its own definition is
        # "command not found", exit 127, and under `set -e` nothing after it
        # runs at all. lambda_zip_tree was written that way and neither
        # `bash -n` nor the text checks above noticed -- bash -n parses, it
        # does not resolve a command name.
        text = _read(STAGE_SH)
        defined = {}
        for m in re.finditer(r"^(\w+)\(\)\s*\{", text, re.M):
            defined[m.group(1)] = text[: m.start()].count("\n") + 1
        self.assertIn("lambda_zip", defined, "parsed no helper definitions at all")

        for m in re.finditer(r"^(\w+)[ \t]", text, re.M):
            name = m.group(1)
            if name not in defined:
                continue
            line = text[: m.start()].count("\n") + 1
            with self.subTest(helper=name, called_on_line=line):
                self.assertLess(
                    defined[name],
                    line,
                    f"{name} is called on line {line} but defined on line "
                    f"{defined[name]}. bash reads top-down, so this is exit 127 "
                    f"and every artifact after it is never staged.",
                )

    def test_both_row_count_gates_match_the_artifacts_actually_staged(self):
        # Two gates, one number. stage-artifacts.sh asserts the rows it writes,
        # then release-echo.yml appends the ui_bundle row and asserts one more.
        # Gold took the count from 26 to 35 and both gates were left behind --
        # the same miss the workflow's own comment already records once.
        raw = _read(STAGE_SH)
        joined = re.sub(r"\\n\s*", " ", raw)
        staged = sum(
            len(re.findall(r"^%s[ \t]" % e, joined, re.M)) for e in self.EMITTERS
        )
        self.assertGreater(staged, 0, "found no artifact calls to count")

        own = re.search(r'\[ "\$rows" -eq (\d+) \]', raw)
        self.assertIsNotNone(own, "stage-artifacts.sh has no row-count gate")
        self.assertEqual(
            int(own.group(1)),
            staged,
            f"stage-artifacts.sh stages {staged} artifacts but its own gate "
            f"asserts {own.group(1)}, so the release fails even when every "
            f"artifact is staged correctly.",
        )

        wf = re.search(r'\[ "\$rows" -eq (\d+) \]', _read(RELEASE_YML))
        self.assertIsNotNone(wf, "release-echo.yml has no row-count gate")
        self.assertEqual(
            int(wf.group(1)),
            staged + 1,
            f"release-echo.yml asserts {wf.group(1)} rows; it should assert "
            f"{staged + 1} -- the {staged} this script writes plus the ui_bundle "
            f"row the workflow appends itself.",
        )


if __name__ == "__main__":
    unittest.main()
