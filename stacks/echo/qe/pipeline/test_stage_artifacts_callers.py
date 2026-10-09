# test_stage_artifacts_callers.py
#
# Asserts that every caller of scripts/stage-artifacts.sh builds the
# compile_preview tree (`make ... build-preview`) BEFORE it stages.
#
# WHY. compile_preview's artifact is a BUILT TREE (handler + pip-installed
# sqlglot), not a file list, and stage-artifacts.sh refuses when the tree does
# not exist. UIFPE-576 added that artifact and taught release-echo.yml to build
# it, but not the dev deploy path (_uif-echo-deploy-common.sh), so from
# 2026-09-25 every dev deploy -- plan or apply, push or dispatch -- died at
# "Stage release artifacts" before Terraform ran (UIFPE-672). The artifact-set
# tests compare WHICH artifacts each side declares; nothing compared whether
# each caller PRODUCES them first.
#
# The rule is scoped to "while stage-artifacts.sh stages a built tree": if the
# last lambda_zip_tree call goes, the test says so instead of asserting a
# build nobody needs. Callers are discovered, not listed, so a third caller is
# covered the day it is added.
import os
import re
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
STACK_ROOT = os.path.join(REPO_ROOT, "stacks", "echo")
STAGE_SH = os.path.join(STACK_ROOT, "scripts", "stage-artifacts.sh")
GOLD_MAKEFILE = os.path.join(STACK_ROOT, "infrastructure", "terraform-gold", "Makefile")

# Where a caller can live: CI workflows and the stack's shell scripts.
SEARCH_DIRS = (
    (os.path.join(REPO_ROOT, ".github", "workflows"), (".yml", ".yaml")),
    (os.path.join(STACK_ROOT, "infrastructure", "deploy"), (".sh",)),
    (os.path.join(STACK_ROOT, "scripts"), (".sh",)),
)
EXPECTED_CALLERS = {
    os.path.join(".github", "workflows", "release-echo.yml"),
    os.path.join("stacks", "echo", "infrastructure", "deploy", "_uif-echo-deploy-common.sh"),
}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _code_lines(text):
    """(line_no, line) for lines that are not comments. A yml `run:` body and
    a shell script both use `#` comments, which mention both names freely."""
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield n, line


def _invokes_stage(line):
    # An invocation runs the script; a path mentioned inside a quoted message
    # (e.g. a die/echo string) is not one.
    return bool(re.search(r"(^|[\s/\"])stage-artifacts\.sh(\s|\"|$)", line)) \
        and not re.search(r"\b(echo|die|printf|ok|step)\b", line)


def _callers():
    found = {}
    for d, exts in SEARCH_DIRS:
        for name in sorted(os.listdir(d)):
            path = os.path.join(d, name)
            if not name.endswith(exts) or os.path.abspath(path) == os.path.abspath(STAGE_SH):
                continue
            lines = [n for n, line in _code_lines(_read(path)) if _invokes_stage(line)]
            if lines:
                found[os.path.relpath(path, REPO_ROOT)] = (path, lines[0])
    return found


class TestStageArtifactsCallersBuildThePreviewTree(unittest.TestCase):

    def test_stage_artifacts_still_stages_a_built_tree(self):
        calls = re.findall(r"^lambda_zip_tree\s+(\w+)", _read(STAGE_SH), re.M)
        self.assertIn(
            "compile_preview", calls,
            "stage-artifacts.sh no longer stages compile_preview as a built tree. "
            "If that is deliberate, this test's premise is gone: retire it rather "
            "than leave it asserting a build nobody needs.",
        )

    def test_build_preview_target_produces_the_staged_tree(self):
        mk = _read(GOLD_MAKEFILE)
        self.assertRegex(mk, r"(?m)^build-preview:")
        self.assertIn("PREVIEW_BUILD := .build/compile_preview_src", mk)
        self.assertIn("terraform-gold/.build/compile_preview_src", _read(STAGE_SH))

    def test_known_callers_are_discovered(self):
        # Guards the discovery itself: if it finds nothing, the next test
        # passes vacuously.
        self.assertTrue(
            EXPECTED_CALLERS <= set(_callers()),
            f"expected callers {sorted(EXPECTED_CALLERS)}, discovered {sorted(_callers())}",
        )

    def test_every_caller_builds_the_tree_before_staging(self):
        for rel, (path, stage_line) in sorted(_callers().items()):
            with self.subTest(caller=rel):
                builds = [n for n, line in _code_lines(_read(path))
                          if re.search(r"\bmake\b.*\bbuild-preview\b", line)]
                self.assertTrue(
                    builds and builds[0] < stage_line,
                    f"{rel} runs stage-artifacts.sh (line {stage_line}) without "
                    "first running `make -C .../terraform-gold build-preview`; "
                    "staging will fail with 'lambda tree ... does not exist "
                    "(compile_preview)' (UIFPE-672).",
                )


if __name__ == "__main__":
    unittest.main()
