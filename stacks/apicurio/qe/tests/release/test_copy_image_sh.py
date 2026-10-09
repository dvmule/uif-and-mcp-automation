"""Unit tests for scripts/copy-image.sh.

The script's job is refusal, not copying -- crane already copies. Promotion
puts the BFF image into the target environment's own ECR repository, and the
digest recorded in the build tag's manifest is the artifact's identity, so a
copy that changes it has deployed an unrecorded image under the release's
name. The failure is silent (see the script's header for the measurement), so
the guard is the only thing standing between a bad copy and a release that
claims to be something it is not.

Most tests here drive a FAKE crane, because the behaviour that matters is what
the script does when the digests disagree -- and a real registry cannot be
asked to disagree on demand. The end-to-end test against a local registry:2 is
skipped unless docker and a real crane are both present, which is the honest
arrangement: CI has neither today.
"""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "copy-image.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available on PATH")

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _fake_crane(tmp_path: Path, *, digests: dict[str, str], copy_rc: int = 0) -> Path:
    """A stand-in crane that answers `digest` from a table and logs its calls.

    Keyed by reference so a single fake can return one digest for the source
    and a different one for the destination -- which is the mismatch the real
    thing will not produce to order.
    """
    table = "\n".join(f'  "{ref}") echo "{d}" ;;' for ref, d in digests.items())
    script = tmp_path / "crane"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "${CRANE_CALLS}"\n'
        "# Drop flags so the fake sees the same argv shape regardless of --insecure.\n"
        "args=(); for a in \"$@\"; do case \"$a\" in --*) ;; *) args+=(\"$a\") ;; esac; done\n"
        'case "${args[0]}" in\n'
        "  digest)\n"
        '    case "${args[1]}" in\n'
        f"{table}\n"
        '      *) echo "fake: no such reference ${args[1]}" >&2; exit 1 ;;\n'
        "    esac\n"
        "    ;;\n"
        f"  copy) exit {copy_rc} ;;\n"
        '  *) echo "fake: unexpected subcommand ${args[0]}" >&2; exit 2 ;;\n'
        "esac\n",
        newline="\n",
    )
    script.chmod(0o755)
    return script


def _run(tmp_path: Path, *args: str, crane: Path | None = None):
    env = dict(os.environ)
    calls = tmp_path / "calls.log"
    calls.touch()
    env["CRANE_CALLS"] = str(calls)
    if crane is not None:
        env["CRANE"] = str(crane)
    result = subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env
    )
    result.calls = calls.read_text()  # type: ignore[attr-defined]
    return result


# ── Refusal: the behaviour the script exists for ───────────────────────────────


def test_refuses_when_the_copy_changed_the_digest(tmp_path):
    crane = _fake_crane(tmp_path, digests={"src:v1": DIGEST_A, "dst:v1": DIGEST_B})
    r = _run(tmp_path, "src:v1", "dst:v1", crane=crane)
    assert r.returncode != 0
    assert "did not preserve the digest" in r.stderr
    # Both digests named, so the reader does not have to go and look them up.
    assert DIGEST_A in r.stderr and DIGEST_B in r.stderr
    # And told that the destination is now polluted -- the copy did happen.
    assert "DIFFERENT artifact" in r.stderr


def test_accepts_when_the_digest_survived(tmp_path):
    crane = _fake_crane(tmp_path, digests={"src:v1": DIGEST_A, "dst:v1": DIGEST_A})
    r = _run(tmp_path, "src:v1", "dst:v1", crane=crane)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == DIGEST_A


def test_refuses_a_source_that_is_not_the_expected_digest(tmp_path):
    crane = _fake_crane(tmp_path, digests={"src:v1": DIGEST_A, "dst:v1": DIGEST_A})
    r = _run(tmp_path, "src:v1", "dst:v1", DIGEST_B, crane=crane)
    assert r.returncode != 0
    assert "but the release expects" in r.stderr


def test_does_not_copy_when_the_source_is_not_the_expected_digest(tmp_path):
    # The ordering matters as much as the refusal: a tag that moved must be
    # caught BEFORE the wrong image lands in the target repository.
    crane = _fake_crane(tmp_path, digests={"src:v1": DIGEST_A, "dst:v1": DIGEST_A})
    r = _run(tmp_path, "src:v1", "dst:v1", DIGEST_B, crane=crane)
    assert r.returncode != 0
    assert "copy" not in r.calls, f"a copy was attempted anyway: {r.calls!r}"
    assert "Nothing was copied" in r.stderr


def test_accepts_a_source_that_matches_the_expected_digest(tmp_path):
    crane = _fake_crane(tmp_path, digests={"src:v1": DIGEST_A, "dst:v1": DIGEST_A})
    r = _run(tmp_path, "src:v1", "dst:v1", DIGEST_A, crane=crane)
    assert r.returncode == 0, r.stderr


def test_reads_the_destination_digest_back_rather_than_trusting_the_copy(tmp_path):
    crane = _fake_crane(tmp_path, digests={"src:v1": DIGEST_A, "dst:v1": DIGEST_A})
    r = _run(tmp_path, "src:v1", "dst:v1", crane=crane)
    assert r.returncode == 0, r.stderr
    assert "digest dst:v1" in r.calls, (
        "the destination digest must come from the registry after the copy, not "
        f"from the copy's own report. Calls were: {r.calls!r}"
    )


def test_fails_when_the_copy_itself_fails(tmp_path):
    crane = _fake_crane(
        tmp_path, digests={"src:v1": DIGEST_A, "dst:v1": DIGEST_A}, copy_rc=1
    )
    r = _run(tmp_path, "src:v1", "dst:v1", crane=crane)
    assert r.returncode != 0
    assert "crane copy" in r.stderr


def test_fails_when_the_source_cannot_be_read(tmp_path):
    # Distinct from "it is not there": an unreadable source means whether it
    # exists is unknown, and the script must not proceed on a guess.
    crane = _fake_crane(tmp_path, digests={"dst:v1": DIGEST_A})
    r = _run(tmp_path, "missing:v1", "dst:v1", crane=crane)
    assert r.returncode != 0
    assert "could not read a digest for the source" in r.stderr
    assert "copy" not in r.calls


# ── Argument validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [(), ("src:v1",)])
def test_requires_both_references(tmp_path, args):
    r = _run(tmp_path, *args)
    assert r.returncode != 0
    assert "usage:" in r.stderr


def test_refuses_to_copy_a_reference_onto_itself(tmp_path):
    crane = _fake_crane(tmp_path, digests={"src:v1": DIGEST_A})
    r = _run(tmp_path, "src:v1", "src:v1", crane=crane)
    assert r.returncode != 0
    assert "same reference" in r.stderr


@pytest.mark.parametrize(
    "bad",
    [
        "sha256:" + "A" * 64,  # uppercase -- two spellings of one digest
        "sha256:" + "a" * 63,  # truncated
        "a" * 64,  # no algorithm prefix
        "sha256:nothex" + "0" * 58,
    ],
)
def test_rejects_a_malformed_expected_digest(tmp_path, bad):
    crane = _fake_crane(tmp_path, digests={"src:v1": DIGEST_A, "dst:v1": DIGEST_A})
    r = _run(tmp_path, "src:v1", "dst:v1", bad, crane=crane)
    assert r.returncode != 0
    assert "must be sha256:<64 lowercase hex>" in r.stderr
    assert "copy" not in r.calls, "a malformed pin must fail before any bytes move"


def test_reports_a_missing_crane_without_pretending_it_copied(tmp_path):
    r = _run(tmp_path, "src:v1", "dst:v1", crane=tmp_path / "definitely-not-here")
    assert r.returncode != 0
    assert "crane not found" in r.stderr


# ── End to end, against a real registry ────────────────────────────────────────

DOCKER = shutil.which("docker")
CRANE = shutil.which("crane")


def _docker_up() -> bool:
    if DOCKER is None:
        return False
    return subprocess.run([DOCKER, "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(
    CRANE is None or not _docker_up(),
    reason="needs a real crane on PATH and a running Docker daemon",
)
def test_end_to_end_preserves_the_digest_through_a_real_registry(tmp_path):
    """The same check the 2026-09-20 spike ran, as a test rather than a session.

    Uses a throwaway registry:2 on an ephemeral port so it cannot collide with
    anything the developer is running.
    """
    name = f"apicurio-copy-image-test-{uuid.uuid4().hex[:8]}"
    up = subprocess.run(
        [DOCKER, "run", "-d", "--name", name, "-p", "0:5000", "registry:2"],
        capture_output=True,
        text=True,
    )
    if up.returncode != 0:
        pytest.skip(f"could not start registry:2: {up.stderr.strip()}")
    try:
        port = subprocess.run(
            [DOCKER, "port", name, "5000/tcp"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip().rsplit(":", 1)[-1]
        host = f"localhost:{port}"

        # FROM scratch, so the image needs no upstream pull and the test does
        # not depend on Docker Hub being reachable.
        (tmp_path / "payload.txt").write_text("promotion identity\n", newline="\n")
        (tmp_path / "Dockerfile").write_text(
            "FROM scratch\nCOPY payload.txt /payload.txt\n", newline="\n"
        )
        src = f"{host}/spike-src:v1"
        dst = f"{host}/spike-dst:v1"
        for cmd in (
            [DOCKER, "build", "--platform=linux/amd64", "-q", "-t", src, str(tmp_path)],
            [DOCKER, "push", "-q", src],
        ):
            subprocess.run(cmd, capture_output=True, text=True, check=True)

        env = dict(os.environ, CRANE_INSECURE="1", CRANE_CALLS=str(tmp_path / "c.log"))
        expected = subprocess.run(
            [CRANE, "--insecure", "digest", src],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        r = subprocess.run(
            [BASH, str(SCRIPT), src, dst, expected],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == expected
    finally:
        subprocess.run([DOCKER, "rm", "-f", name], capture_output=True)
