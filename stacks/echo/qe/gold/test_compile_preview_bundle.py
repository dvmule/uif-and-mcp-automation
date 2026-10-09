# test_compile_preview_bundle.py
# The compile_preview Lambda ships as a FLAT zip assembled by `make build-preview`
# in infrastructure/terraform-gold/Makefile, which copies a hand-written list of
# modules. Nothing else checks that list against what the code actually imports.
#
# WHY THIS GATE EARNS ITS PLACE: when the list falls behind an import, the
# failure is invisible from every direction at once.
#   * the deploy is GREEN -- archive_file zips the directory whatever is in it,
#     and cannot know an import is unsatisfied;
#   * no alarm fires -- Runtime.ImportModuleError returns a 200 with
#     FunctionError "Unhandled", so it never touches the Errors metric;
#   * nothing invokes the function except a human asking for a preview.
# That combination hid a dead Lambda for FIFTEEN DAYS (UIFPE-723): an
# `import gold_resources` landed on 2026-09-15 and libs/gold_resources.py was
# never added to the bundle, so every invocation died before running any code.
#
# The rule, not the instance: every module the bundle imports must resolve to a
# module IN the bundle, the standard library, or a package pip vendors into it.
# The bundle list is READ FROM THE MAKEFILE so it keeps one home -- a gate that
# restated the filenames would pass while the Makefile shipped a short list.

import ast
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
GOLD_TF = REPO / "stacks" / "echo" / "infrastructure" / "terraform-gold"
MAKEFILE = GOLD_TF / "Makefile"
SRC = REPO / "stacks" / "echo" / "src"

# Python 3.10+ knows its own stdlib; older interpreters need the fallback.
STDLIB = getattr(sys, "stdlib_module_names", None)


def makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def bundled_sources() -> list:
    """The source paths `make build-preview` copies into the bundle.

    Parsed from the recipe rather than restated, so the Makefile stays the one
    home for the list.
    """
    text = makefile_text()
    recipe = re.search(r"^build-preview:\n((?:\t.*\n)+)", text, re.M)
    assert recipe, "build-preview recipe not found in the Makefile"
    # $(PREVIEW_SRC) is the bundle's source root; $(PREVIEW_BUILD) its target.
    root = re.search(r"^PREVIEW_SRC\s*:?=\s*(\S+)", text, re.M)
    assert root, "PREVIEW_SRC not defined in the Makefile"
    rel = root.group(1)
    out = []
    for m in re.finditer(r"\$\(PREVIEW_SRC\)(/\S+\.py)", recipe.group(1)):
        out.append((GOLD_TF / (rel + m.group(1))).resolve())
    return out


def vendored_packages() -> set:
    """Distribution names pip installs into the bundle, from the Makefile's own
    `*_PIN` variables — again so the pin has one home."""
    names = set()
    for line in makefile_text().splitlines():
        if re.match(r"^\w+_PIN\s*:?=", line):
            m = re.search(r"([A-Za-z][A-Za-z0-9_.-]*)==", line)
            if m:
                names.add(m.group(1).replace("-", "_").lower())
    return names


def imported_names(path: Path) -> set:
    """Every module name this file imports, at any nesting depth.

    Lazy imports inside a function are included deliberately: the module still
    has to be in the zip when that function runs, so an unresolvable one is a
    latent failure rather than a safe one.
    """
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:      # flat zip: no relatives
                names.add(node.module.split(".")[0])
    return names


def unresolved(sources, vendored) -> dict:
    """Imports that nothing in the bundle, the stdlib or the vendored set
    satisfies, keyed by the file that imports them."""
    in_bundle = {p.stem for p in sources}
    out = {}
    for p in sources:
        bad = sorted(
            n for n in imported_names(p)
            if n not in in_bundle
            and n != "__future__"
            and n.lower() not in vendored
            and not (STDLIB and n in STDLIB)
        )
        if bad:
            out[p.name] = bad
    return out


class TestCompilePreviewBundle(unittest.TestCase):

    def test_every_bundled_import_resolves(self):
        sources = bundled_sources()
        missing = [p for p in sources if not p.is_file()]
        self.assertEqual([], missing,
                         f"the Makefile copies files that do not exist: {missing}")
        bad = unresolved(sources, vendored_packages())
        self.assertEqual(
            {}, bad,
            "the compile_preview bundle imports modules it does not ship, so "
            "the Lambda dies at Runtime.ImportModuleError before running any "
            "code. Add each to the build-preview recipe in "
            f"{MAKEFILE.relative_to(REPO)}: {bad}")

    def test_the_parse_actually_found_the_bundle(self):
        """Guards against passing because it read nothing — a renamed variable
        or a reformatted recipe would otherwise read as green."""
        sources = bundled_sources()
        self.assertGreaterEqual(len(sources), 4,
                                f"only parsed {len(sources)} bundled sources")
        stems = {p.stem for p in sources}
        self.assertIn("compile_preview", stems, "the handler itself is missing")
        self.assertIn("compile_intake_delta", stems, "the compiler is missing")
        self.assertTrue(vendored_packages(),
                        "parsed no vendored pin; sqlglot would read as missing")

    def test_the_detector_catches_a_missing_module(self):
        """Known-bad control. Without it, a detector that silently classified
        everything as stdlib would make the gate above pass forever."""
        sources = bundled_sources()
        vendored = vendored_packages()
        self.assertEqual({}, unresolved(sources, vendored))

        # Pretend the vendored pin is absent: sqlglot must then be reported.
        bad = unresolved(sources, set())
        self.assertTrue(
            any("sqlglot" in v for v in bad.values()),
            "dropping the vendored set did not surface sqlglot, so the "
            f"classifier is not discriminating: {bad}")

    def test_stdlib_classification_is_available(self):
        """If the interpreter cannot name its stdlib, every stdlib import would
        be reported and the gate would be useless noise. Fail loudly instead."""
        self.assertIsNotNone(
            STDLIB,
            "sys.stdlib_module_names is unavailable (Python < 3.10); this gate "
            "needs it to tell a stdlib import from a missing one")


if __name__ == "__main__":
    unittest.main()
