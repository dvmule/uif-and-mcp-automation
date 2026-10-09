# test_manifest_reader_has_no_pipe_race.py
#
# `printf "$manifest" | head -1` is a race, and it cost a converge round.
#
# WHAT HAPPENED. Run 36332201702 died in the promote step, before any Terraform
# ran, with:
#
#   read-manifest.sh: line 78: printf: write error: Broken pipe
#
# `head -1` exits the instant it has its line. bash's printf builtin writes a
# 14KB manifest in several chunks, so head can close the read end between two of
# them; the next write returns EPIPE, and `set -o pipefail` turns that into a
# dead script. Under 64KB the write usually completes in one go and the race is
# invisible -- the same tag had promoted successfully four times before this.
#
# That invisibility is the reason for a test rather than a fix and a comment. A
# bug that fires on the fifth identical run does not look like a latent bug; it
# looks like a new one, and the first instinct is to go looking at what changed
# about the manifest or the bucket. Nothing had.
#
# WHAT IS AND IS NOT AT RISK. The race needs printf's IMMEDIATE consumer to exit
# early. `printf | head -1` qualifies. `printf | grep -E ... | head -1` does not:
# printf's reader is grep, which consumes all of it, and grep's own output is one
# line that fits a single write. read-manifest.sh's four accessors are that
# second shape and are deliberately left alone -- changing them would alter
# behaviour to fix a bug they do not have.
#
# So this does not ban `head`. It bans printf feeding a truncating reader
# directly, which is the shape that actually broke.
import os
import re
import unittest


HERE = os.path.dirname(__file__)
SCRIPTS = os.path.join(HERE, "..", "..", "scripts")

# Readers that stop before consuming their input. `sed -n '1p'` is absent on
# purpose: it reads everything and exits 0, which is why it is the safe
# replacement where a pipeline is genuinely wanted.
TRUNCATING = r"(?:head\b|sed -n '[0-9]+q|sed [0-9]+q|grep -m *[0-9])"


def shell_scripts():
    out = {}
    for name in sorted(os.listdir(SCRIPTS)):
        if not name.endswith(".sh"):
            continue
        with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
            out[name] = fh.read()
    return out


def code_lines(src):
    """Non-comment lines, so the explanation above a fix is not a finding."""
    return [l for l in src.splitlines() if not l.strip().startswith("#")]


class TestNoPrintfIntoATruncatingReader(unittest.TestCase):
    def test_some_scripts_were_scanned(self):
        # Guards the listing. An empty set would make the case below vacuous.
        self.assertGreaterEqual(
            len(shell_scripts()), 4,
            "expected several shell scripts under stacks/echo/scripts",
        )

    def test_no_script_pipes_printf_straight_into_one(self):
        bad = []
        for name, src in shell_scripts().items():
            for n, line in enumerate(code_lines(src), 1):
                # Only the DIRECT pipe: printf, then a pipe, then a truncating
                # reader with nothing in between.
                if re.search(r"printf\b[^|]*\|\s*" + TRUNCATING, line):
                    bad.append("%s: %s" % (name, line.strip()))
        self.assertEqual(
            [], bad,
            "printf's reader exits before printf finishes writing, the next "
            "write gets EPIPE, and pipefail kills the script. It fires on a "
            "large input and hides on a small one: %s. Use parameter expansion "
            "for a first line, or a reader that consumes its input." % bad,
        )


class TestTheFirstLineCheckSurvived(unittest.TestCase):
    """The guard the racing line implemented must still be there."""

    def _reader(self):
        with open(os.path.join(SCRIPTS, "read-manifest.sh"), encoding="utf-8") as fh:
            return fh.read()

    def test_it_uses_parameter_expansion(self):
        self.assertIn(
            'first_line="${manifest%%$\'\\n\'*}"', self._reader(),
            "the first line must come from expansion, not a pipeline",
        )

    def test_it_still_refuses_a_manifest_for_a_different_tag(self):
        # The point of reading the first line at all: a tag whose message was
        # rewritten to describe another release must not be promoted. Fixing
        # the race must not have removed the check it was performing.
        src = self._reader()
        self.assertRegex(
            src, r'\[ "\$first_line" = "\$\{tag\}:" \]',
            "the first line must still be compared against the tag",
        )
        self.assertIn(
            "does not describe this tag", src,
            "and the refusal must still say why it matters",
        )

    def test_an_empty_manifest_is_refused_before_the_expansion(self):
        # ${var%%...} on an empty string yields an empty string rather than
        # failing, so the expansion cannot itself detect an empty manifest.
        # The check above it is what does, and the comment leans on that.
        src = self._reader()
        i = src.index('[ -n "$manifest" ]')
        j = src.index('first_line="${manifest')
        self.assertLess(
            i, j,
            "the empty-manifest check must come BEFORE the expansion, which "
            "cannot distinguish empty from malformed on its own",
        )


if __name__ == "__main__":
    unittest.main()
