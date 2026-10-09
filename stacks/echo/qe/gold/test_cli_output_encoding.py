# test_cli_output_encoding.py
# The echo CLIs print box-drawing banners. Windows hands a NON-TERMINAL stdout
# the ANSI codepage (cp1252), which has no byte for those characters, so the
# print raises UnicodeEncodeError and the command dies.
#
# WHY THIS GATE EARNS ITS PLACE: CI runs Linux under UTF-8, so every one of
# these crashes is invisible to it, and the failure mode is the worst shape
# available. sync_pipelines.py hit it for real and its own comment records
# where: the error landed on the plan header, AFTER the scan but BEFORE any
# put_item, so `--apply` aborted with a traceback having written nothing. A
# formatting character decided whether a deploy step ran. That file was fixed
# and the other seven were never swept, which is what this pins.
#
# The rule, not the patch list: a CLI that prints a character cp1252 cannot
# encode must declare its own output encoding inside main().

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CLI_DIR = REPO / "stacks" / "echo" / "src" / "apps" / "cli"


def printed_strings(tree):
    """Every string literal reachable from a print() argument.

    Comments and docstrings are excluded by construction: only characters that
    reach stdout can raise, and a non-ASCII character in a comment is fine
    because the source is decoded as UTF-8.
    """
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        for inner in ast.walk(node):          # walks f-string parts too
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                yield inner.value


def unencodable(text):
    """The characters in `text` that cp1252 has no byte for."""
    out = set()
    for ch in text:
        if ord(ch) < 128:
            continue
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            out.add(ch)
    return out


def declares_utf8(tree):
    """Does main() reconfigure a stream to UTF-8?

    Checks the keyword too, not just the call: `reconfigure(errors="replace")`
    alone would satisfy a bare name check while leaving the encoding inherited,
    which is the whole bug.
    """
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == "main"):
            continue
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "reconfigure"):
                continue
            for kw in call.keywords:
                if (kw.arg == "encoding" and isinstance(kw.value, ast.Constant)
                        and str(kw.value.value).lower().replace("-", "")
                        == "utf8"):
                    return True
    return False


def scan(tree):
    chars = set()
    for text in printed_strings(tree):
        chars |= unencodable(text)
    return chars


class TestCliOutputEncoding(unittest.TestCase):

    def test_cli_printing_unencodable_characters_declares_utf8(self):
        offenders = {}
        for path in sorted(CLI_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            chars = scan(tree)
            if chars and not declares_utf8(tree):
                offenders[path.name] = "".join(sorted(chars))
        self.assertEqual(
            {}, offenders,
            "these CLIs print characters cp1252 cannot encode but leave their "
            "output encoding inherited, so they crash on Windows whenever "
            "stdout is not a terminal: " + repr(offenders))

    def test_at_least_one_cli_is_actually_covered(self):
        """Guards against the gate passing because it scanned nothing -- a
        moved directory or a changed glob would otherwise read as green."""
        covered = [p.name for p in sorted(CLI_DIR.glob("*.py"))
                   if scan(ast.parse(p.read_text(encoding="utf-8")))]
        self.assertTrue(CLI_DIR.is_dir(), f"{CLI_DIR} is not a directory")
        self.assertGreaterEqual(
            len(covered), 1,
            "no CLI printed an unencodable character, which means this gate "
            "is asserting nothing -- check the glob and the directory")

    def test_the_detector_catches_the_bug_it_is_meant_to(self):
        """Known-bad control. Without this, a detector that silently found
        nothing would make the gate above pass forever."""
        bad = ast.parse('def main():\n    print("\u2550\u2550")\n')
        self.assertEqual({"\u2550"}, scan(bad))
        self.assertFalse(declares_utf8(bad))

        fixed = ast.parse(
            'def main():\n'
            '    sys.stdout.reconfigure(encoding="utf-8", errors="replace")\n'
            '    print("\u2550")\n')
        self.assertTrue(declares_utf8(fixed))

        # errors= alone must NOT satisfy the check: the encoding is still
        # inherited, so the crash remains.
        partial = ast.parse(
            'def main():\n'
            '    sys.stdout.reconfigure(errors="replace")\n'
            '    print("\u2550")\n')
        self.assertFalse(declares_utf8(partial))


if __name__ == "__main__":
    unittest.main()
