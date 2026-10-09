"""Every file in a Lambda zip triggers the deploy that rebuilds it.

These zips are built by `archive_file` at PLAN time, so a file that is in a
zip but not in the deploy workflow's `paths:` never redeploys: the change
merges, the workflow does not run, and the deployed zip keeps a stale copy of
a module whose source has moved on. The gold workflow says so in a comment,
and the repo has hand-written one assertion per library — which is why each
new library gets forgotten.

This is that assertion, DERIVED. It reads the source list out of the Terraform
and the patterns out of the workflow, so a new module is covered the moment it
joins a zip. It spans BOTH roots that ship a Lambda from a shared module,
because `platform/python/` is outside either one's own path filter.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]

#: (terraform root, the workflow that applies it).
ROOTS = [
    ("stacks/echo/infrastructure/terraform-gold",
     ".github/workflows/deploy-echo-gold-dev.yml"),
    ("domains/product/infrastructure/terraform",
     ".github/workflows/deploy-product-dev.yml"),
]

_ARCHIVE = re.compile(r'data\s+"archive_file"\s+"([^"]+)"\s*\{(.*?)\n\}',
                      re.DOTALL)
_FILE_REF = re.compile(r'file\(\s*"\$\{path\.module\}/([^"]+)"\s*\)')
#: A source_dir zip has no file list, so the DIRECTORY is what must be watched.
_DIR_REF = re.compile(r'source_dir\s*=\s*"\$\{path\.module\}/([^"]+)"')


#: A zip built from a GENERATED tree has no source to watch — compile_preview's
#: source_dir is `.build/compile_preview_src`, produced by the Makefile's
#: build-preview target and absent from a fresh checkout. Its real input,
#: src/apps/lambdas/compile_preview.py, is watched on its own, and
#: test_lambda_artifact_sets.py holds both halves to staging it as a tree.
GENERATED = ".build/"


def zip_sources() -> list[tuple[str, str, str]]:
    """(workflow, zip name, repo-relative path) for every archive_file input."""
    found = []
    for root, workflow in ROOTS:
        tf_dir = REPO / root
        for tf in sorted(tf_dir.glob("*.tf")):
            body = tf.read_text(encoding="utf-8")
            for name, block in _ARCHIVE.findall(body):
                for pattern in (_FILE_REF, _DIR_REF):
                    for ref in pattern.findall(block):
                        if GENERATED in ref:
                            continue
                        resolved = (tf_dir / ref).resolve()
                        found.append((workflow, name,
                                      resolved.relative_to(REPO).as_posix()))
    return found


def workflow_paths(workflow: str) -> list[str]:
    doc = yaml.safe_load((REPO / workflow).read_text(encoding="utf-8"))
    # `on` is parsed as the boolean True by YAML 1.1, which is why this reads
    # both spellings rather than just the obvious one.
    triggers = doc.get("on") or doc.get(True)
    return list(triggers["push"]["paths"])


def covered(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        # fnmatch has no path semantics, so `**` and `*` behave the same here.
        # Deliberately permissive: this guard is for a MISSING entry.
        if fnmatch.fnmatch(path, pattern.replace("**", "*")):
            return True
        # A watched directory covers the files beneath it.
        if fnmatch.fnmatch(path, pattern.replace("**", "*").rstrip("/") + "/*"):
            return True
    return False


SOURCES = zip_sources()


def test_the_terraform_actually_declares_zips():
    """A regex that silently matched nothing would make every test below pass
    without checking anything — and so would a GENERATED filter that swallowed
    every path."""
    assert len(SOURCES) >= 8, SOURCES
    assert not any(GENERATED in s for _, _, s in SOURCES)
    names = {Path(s).name for _, _, s in SOURCES}
    assert {"gold_serving_api.py", "marketing_copy_api.py",
            "jwt_verify.py"} <= names, sorted(names)


def test_BOTH_roots_contribute(request):
    """Product's root is here because it ships a Lambda from platform/python,
    which its own `domains/product/**` filter does not match."""
    assert len({w for w, _, _ in SOURCES}) == len(ROOTS)


@pytest.mark.parametrize("workflow,zip_name,source", SOURCES,
                         ids=[f"{z}:{Path(s).name}" for _, z, s in SOURCES])
def test_a_zipped_source_TRIGGERS_the_deploy(workflow, zip_name, source):
    assert (REPO / source).exists(), f"{source} is referenced but absent"
    assert covered(source, workflow_paths(workflow)), (
        f"{source} is in the {zip_name} zip but matches no path in "
        f"{Path(workflow).name}. A change to it alone would merge without "
        f"redeploying, leaving a stale copy in the deployed zip.")


def test_the_shared_module_is_watched_by_EVERY_root_that_ships_it():
    """The specific failure this guard exists for: platform/python sits
    outside both roots' natural path filters, so each has to name it."""
    shippers = {w for w, _, s in SOURCES if s.startswith("platform/python/")}
    assert len(shippers) >= 2, shippers
    for workflow in shippers:
        assert covered("platform/python/jwt_verify.py",
                       workflow_paths(workflow)), workflow
