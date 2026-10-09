# test_scripts_are_executable.py
#
# Every script a workflow invokes directly must be recorded in git as mode
# 100755.
#
# WHY THIS IS A REAL CLASS AND NOT PEDANTRY. `core.fileMode` is `false` on
# Windows checkouts of this repo, so git ignores the filesystem's executable
# bit entirely: a new script is committed as 100644 no matter what `chmod +x`
# did locally. `git update-index --chmod=+x` sets it, but only on a path that
# is already staged -- run it before `git add` and it fails, and if its stderr
# is discarded the failure is invisible.
#
# The result runs fine on the author's machine, passes every test, passes
# review, merges, and then fails in CI with:
#
#     stacks/echo/scripts/copy-artifacts.sh: Permission denied
#     Process completed with exit code 126
#
# That happened on 2026-09-25 to copy-artifacts.sh (UIFPE-555), in the first
# real dispatch of the uat converge, after the run had already cleared the
# environment's reviewer gate and assumed the deploy role. Checking the modes
# then showed app-only-addresses.sh (UIFPE-576) was 100644 too -- the same
# defect, two steps further into the same job, from a different author. Neither
# had been exercised because neither workflow had ever run.
#
# WHY NOT JUST CALL THEM WITH `bash`. Some steps do, and those are unaffected.
# But the two workflows invoke most of these directly, apicurio's do the same,
# and rewriting every call site to add `bash` would be a larger change that
# also loses the shebang's meaning. Asserting the mode is one line per script.
#
# The list is DERIVED from the workflows, not restated here, so a script that
# gains a direct call site is covered without editing this file.
import os
import re
import subprocess
import unittest


HERE = os.path.dirname(__file__)
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
WORKFLOW_DIR = os.path.join(REPO, ".github", "workflows")

# A direct invocation looks like `stacks/echo/scripts/x.sh` or, from inside a
# Terraform root, `../../scripts/x.sh`. A `bash stacks/.../x.sh` call does not
# need the bit, but requiring it anyway costs nothing and avoids depending on
# which form a given step happens to use.
INVOCATION = re.compile(r"(?:\.\./\.\./|stacks/echo/)scripts/([a-z0-9._-]+\.sh)")


def echo_workflows():
    return [
        os.path.join(WORKFLOW_DIR, n)
        for n in sorted(os.listdir(WORKFLOW_DIR))
        if n.endswith((".yml", ".yaml")) and "echo" in n
    ]


def referenced_scripts():
    found = set()
    for path in echo_workflows():
        with open(path, encoding="utf-8") as fh:
            found.update(INVOCATION.findall(fh.read()))
    return found


def git_modes():
    """Path -> mode string, as git has it recorded."""
    out = subprocess.run(
        ["git", "ls-files", "-s", "stacks/echo/scripts/"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    modes = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        modes[os.path.basename(path.strip())] = meta.split()[0]
    return modes


class TestWorkflowScriptsAreExecutable(unittest.TestCase):
    def test_the_workflows_reference_some_scripts(self):
        # Guards the regex. A change to how the workflows spell these paths
        # would otherwise make every case below pass against an empty set.
        self.assertGreaterEqual(
            len(referenced_scripts()), 5,
            "expected the echo workflows to invoke several scripts; the "
            "invocation pattern may have changed",
        )

    def test_every_referenced_script_is_mode_100755(self):
        modes = git_modes()
        wrong = []
        for script in sorted(referenced_scripts()):
            mode = modes.get(script)
            if mode is None:
                # Referenced but absent from stacks/echo/scripts -- a different
                # problem, and not this test's business.
                continue
            if mode != "100755":
                wrong.append("%s is %s" % (script, mode))
        self.assertEqual(
            [], wrong,
            "these scripts are invoked by a workflow but are not executable "
            "in git, so the step fails with 'Permission denied' and exit 126: "
            "%s. Fix with: git add <path> && git update-index --chmod=+x "
            "<path>. Note the order -- --chmod=+x only works on a staged path."
            % ", ".join(wrong),
        )


class TestEveryScriptWithAShebangIsExecutable(unittest.TestCase):
    """Broader than the workflows, because a human runs these too."""

    def test_no_shebanged_script_is_left_non_executable(self):
        modes = git_modes()
        wrong = []
        for script, mode in sorted(modes.items()):
            path = os.path.join(REPO, "stacks", "echo", "scripts", script)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                if fh.read(2) != b"#!":
                    continue
            if mode != "100755":
                wrong.append("%s is %s" % (script, mode))
        self.assertEqual(
            [], wrong,
            "a file declaring an interpreter it cannot be run with is a "
            "contradiction, whoever invokes it: %s" % ", ".join(wrong),
        )


if __name__ == "__main__":
    unittest.main()
