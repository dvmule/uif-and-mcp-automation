"""Unit tests for scripts/restore-bundle.sh.

Promotion never rebuilds the UI: it publishes the exact bytes the build tag's
archive holds. So the only question this script answers is whether what came
out of the archive is still what the release recorded -- and the failure it
guards is one a later `aws s3 sync` would happily publish.

The destination is therefore load-bearing as well as the exit code. A tree
left on disk after a rejected hash is a rejected bundle sitting where the
publish step expects a good one, so several tests below assert the directory
is gone rather than only that the command failed.
"""

import gzip
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "restore-bundle.sh"

BASH = shutil.which("bash")
TAR = shutil.which("tar")
pytestmark = pytest.mark.skipif(
    BASH is None or TAR is None, reason="needs bash and tar on PATH"
)

BUNDLE = {
    "index.html": "<html><body>x</body></html>",
    "assets/index-abc123.js": "console.log(1)",
    "assets/index-abc123.css": "body{}",
}

ZERO_HASH = "0" * 64


def _dist(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, newline="\n")
    return root


def _hash(dist: Path) -> str:
    r = subprocess.run(
        [BASH, str(STACK_ROOT / "scripts" / "bundle-hash.sh"), str(dist)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _archive(dist: Path, out: Path, *, exclude_config: bool = True) -> Path:
    """Archive exactly as release-apicurio.yml does: -C dist, excluding config.json."""
    args = [TAR]
    if exclude_config:
        args += ["--exclude=./config.json"]
    args += ["-czf", str(out), "-C", str(dist), "."]
    subprocess.run(args, check=True, capture_output=True)
    return out


def _run(archive: Path, dest: Path, expected: str):
    return subprocess.run(
        [BASH, str(SCRIPT), str(archive), str(dest), expected],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def good(tmp_path):
    dist = _dist(tmp_path / "dist", BUNDLE)
    digest = _hash(dist)
    archive = _archive(dist, tmp_path / f"{digest}.tar.gz")
    return archive, digest


# ── The round trip ─────────────────────────────────────────────────────────────


def test_restores_and_verifies(tmp_path, good):
    archive, digest = good
    dest = tmp_path / "out"
    r = _run(archive, dest, digest)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == digest
    assert (dest / "index.html").is_file()
    assert (dest / "assets" / "index-abc123.js").is_file()


def test_the_hash_of_the_restored_tree_equals_the_hash_of_the_source(tmp_path, good):
    # The property that makes one bundle promotable: archive then restore is
    # identity as far as bundle-hash.sh is concerned.
    archive, digest = good
    dest = tmp_path / "out"
    assert _run(archive, dest, digest).returncode == 0
    assert _hash(dest) == digest


def test_a_config_json_written_after_restore_does_not_change_the_hash(tmp_path, good):
    # The promotion writes the target environment's config.json into the
    # restored tree. If that changed the bundle's identity, the deployed bundle
    # could never match the released one.
    archive, digest = good
    dest = tmp_path / "out"
    assert _run(archive, dest, digest).returncode == 0
    (dest / "config.json").write_text('{"apiUrl":"https://uat"}', newline="\n")
    assert _hash(dest) == digest


# ── Refusal, and the destination that must not survive it ──────────────────────


def test_refuses_a_hash_that_does_not_match(tmp_path, good):
    archive, _ = good
    dest = tmp_path / "out"
    r = _run(archive, dest, ZERO_HASH)
    assert r.returncode != 0
    assert "is not the one the release recorded" in r.stderr
    assert ZERO_HASH in r.stderr, "the error should name both hashes"


def test_removes_the_destination_when_the_hash_does_not_match(tmp_path, good):
    # The important half. A rejected tree left on disk is a rejected bundle
    # sitting exactly where the publish step looks for a good one.
    archive, _ = good
    dest = tmp_path / "out"
    assert _run(archive, dest, ZERO_HASH).returncode != 0
    assert not dest.exists(), "a rejected bundle was left where a sync would publish it"


def test_refuses_an_archive_whose_contents_changed(tmp_path):
    # The realistic corruption: the archive is readable and well-formed, but
    # its contents are not what the build tag measured.
    dist = _dist(tmp_path / "dist", BUNDLE)
    digest = _hash(dist)
    tampered = _dist(tmp_path / "tampered", {**BUNDLE, "index.html": "<html>evil</html>"})
    archive = _archive(tampered, tmp_path / "t.tar.gz")
    dest = tmp_path / "out"
    r = _run(archive, dest, digest)
    assert r.returncode != 0
    assert "is not the one the release recorded" in r.stderr
    assert not dest.exists()


def test_refuses_an_archive_containing_config_json(tmp_path):
    # config.json is environment state. Its presence means the archive was cut
    # without --exclude, and if the later per-environment write were skipped the
    # target would serve another environment's API origin -- surfacing as a CORS
    # error far from this cause. The hash ignores it, so only this check catches
    # it.
    dist = _dist(
        tmp_path / "dist", {**BUNDLE, "config.json": '{"apiUrl":"https://dev"}'}
    )
    digest = _hash(dist)
    archive = _archive(dist, tmp_path / "with-config.tar.gz", exclude_config=False)
    dest = tmp_path / "out"
    r = _run(archive, dest, digest)
    assert r.returncode != 0
    assert "contains config.json" in r.stderr
    assert not dest.exists()


def test_refuses_to_extract_over_an_existing_directory(tmp_path, good):
    # A stale tree from an earlier attempt could contribute files to the hash,
    # which is how a wrong bundle would verify.
    archive, digest = good
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "leftover.js").write_text("stale", newline="\n")
    r = _run(archive, dest, digest)
    assert r.returncode != 0
    assert "refusing to extract over it" in r.stderr
    assert (dest / "leftover.js").exists(), "an existing tree must not be touched"


def test_refuses_a_missing_archive(tmp_path):
    r = _run(tmp_path / "nope.tar.gz", tmp_path / "out", ZERO_HASH)
    assert r.returncode != 0
    assert "does not exist" in r.stderr


def test_refuses_something_that_is_not_a_gzipped_tar(tmp_path):
    # What a truncated download looks like.
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a tarball at all")
    r = _run(bad, tmp_path / "out", ZERO_HASH)
    assert r.returncode != 0
    assert "not a readable gzipped tar" in r.stderr
    assert "truncated download" in r.stderr


def test_refuses_an_empty_archive(tmp_path):
    empty = tmp_path / "empty.tar.gz"
    with gzip.open(empty, "wb") as fh:
        with tarfile.open(fileobj=fh, mode="w"):
            pass
    r = _run(empty, tmp_path / "out", ZERO_HASH)
    assert r.returncode != 0
    assert "empty archive" in r.stderr


@pytest.mark.parametrize(
    "bad",
    [
        "A" * 64,  # uppercase
        "a" * 63,  # truncated
        "sha256:" + "a" * 64,  # carries the algorithm prefix
        "zz" + "a" * 62,  # not hex
    ],
)
def test_rejects_a_malformed_expected_hash(tmp_path, good, bad):
    archive, _ = good
    dest = tmp_path / "out"
    r = _run(archive, dest, bad)
    assert r.returncode != 0
    assert "64 lowercase hex" in r.stderr
    assert not dest.exists(), "a malformed pin must fail before anything is extracted"


@pytest.mark.parametrize("args", [(), ("a",), ("a", "b")])
def test_requires_all_three_arguments(tmp_path, args):
    r = subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True
    )
    assert r.returncode != 0
    assert "usage:" in r.stderr


# ── Extraction safety ──────────────────────────────────────────────────────────


def _tar_with_names(path: Path, names: list[str]) -> Path:
    """Build a tarball carrying literal member names, bypassing tar's own checks."""
    with gzip.open(path, "wb") as gz:
        with tarfile.open(fileobj=gz, mode="w") as tf:
            for name in names:
                data = b"x"
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.parametrize(
    "name",
    ["/etc/passwd", "../outside.js", "./../outside.js", "assets/../../outside.js", "a/.."],
)
def test_refuses_an_archive_that_would_write_outside_the_destination(tmp_path, name):
    archive = _tar_with_names(tmp_path / "eek.tar.gz", ["index.html", name])
    dest = tmp_path / "out"
    r = _run(archive, dest, ZERO_HASH)
    assert r.returncode != 0
    assert "absolute path" in r.stderr or "parent-directory reference" in r.stderr
    assert not dest.exists()


@pytest.mark.parametrize("name", ["assets/chunk..map", "a..b.js", "..leading.js"])
def test_allows_innocent_names_containing_dots(tmp_path, name):
    # The traversal check must not be `*..*`, which would reject these.
    archive = _tar_with_names(tmp_path / "ok.tar.gz", [name])
    dest = tmp_path / "out"
    r = _run(archive, dest, ZERO_HASH)
    # Rejected for the hash, which is the point -- but NOT for its name.
    assert "parent-directory reference" not in r.stderr
    assert "absolute path" not in r.stderr
