"""Unit tests for scripts/bundle-hash.sh.

The hash is the UI bundle's identity in the release manifest, so the
properties that matter are: it depends on file contents AND paths, it does NOT
depend on where the directory lives, and it does NOT depend on config.json --
which is environment state written per environment at deploy time. If it did
depend on config.json, one bundle could not be promoted across environments,
which is the whole point of the runtime-config change.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "bundle-hash.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available on PATH")

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _dist(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, newline="\n")
    return root


def _hash(dist: Path) -> str:
    result = subprocess.run(
        [BASH, str(SCRIPT), str(dist)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    assert SHA256.match(out), f"not a bare sha256: {out!r}"
    return out


BUNDLE = {"index.html": "<html></html>", "assets/app-abc123.js": "console.log(1)"}


def test_is_a_bare_lowercase_sha256(tmp_path):
    _hash(_dist(tmp_path / "dist", BUNDLE))


def test_same_contents_in_a_different_location_hash_the_same(tmp_path):
    a = _hash(_dist(tmp_path / "one" / "dist", BUNDLE))
    b = _hash(_dist(tmp_path / "two" / "dist", BUNDLE))
    assert a == b, "the hash must not depend on where dist/ lives"


def test_config_json_does_not_change_the_hash(tmp_path):
    plain = _hash(_dist(tmp_path / "a" / "dist", BUNDLE))
    withcfg = _hash(
        _dist(tmp_path / "b" / "dist", {**BUNDLE, "config.json": '{"apiUrl":"https://x"}'})
    )
    assert plain == withcfg, "config.json is environment state, not bundle identity"


def test_a_nested_config_json_does_change_the_hash(tmp_path):
    # DOES, emphatically: only the bundle-root config.json is environment
    # state. A file that happens to share the name deeper in the tree is
    # real content.
    plain = _hash(_dist(tmp_path / "a" / "dist", BUNDLE))
    nested = _hash(_dist(tmp_path / "b" / "dist", {**BUNDLE, "assets/config.json": "{}"}))
    assert plain != nested


def test_changed_file_contents_change_the_hash(tmp_path):
    a = _hash(_dist(tmp_path / "a" / "dist", BUNDLE))
    b = _hash(_dist(tmp_path / "b" / "dist", {**BUNDLE, "index.html": "<html> </html>"}))
    assert a != b


def test_renaming_a_file_changes_the_hash(tmp_path):
    a = _hash(_dist(tmp_path / "a" / "dist", BUNDLE))
    renamed = {
        "index.html": BUNDLE["index.html"],
        "assets/app-def456.js": BUNDLE["assets/app-abc123.js"],
    }
    b = _hash(_dist(tmp_path / "b" / "dist", renamed))
    assert a != b, "paths are part of identity -- a renamed chunk is a different bundle"


def test_is_stable_across_repeated_runs(tmp_path):
    dist = _dist(tmp_path / "dist", BUNDLE)
    assert _hash(dist) == _hash(dist)


def test_rejects_an_empty_directory(tmp_path):
    empty = tmp_path / "dist"
    empty.mkdir()
    result = subprocess.run([BASH, str(SCRIPT), str(empty)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "no files" in result.stderr


def test_rejects_a_missing_directory(tmp_path):
    result = subprocess.run(
        [BASH, str(SCRIPT), str(tmp_path / "nope")], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "not a directory" in result.stderr
