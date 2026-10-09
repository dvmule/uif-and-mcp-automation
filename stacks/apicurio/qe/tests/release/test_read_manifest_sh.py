"""Unit tests for scripts/read-manifest.sh.

A promotion's inputs come from the build tag's manifest, so this reader sits
between "what the release recorded" and "what Terraform is told to apply". Its
failure mode is the dangerous kind: hand back an empty string and
`terraform apply -var bff_image_digest=` is a variable that is set and wrong,
indistinguishable from a deliberate value. So absent must be an error, never
empty -- and a manifest whose gate did not pass must yield nothing at all.

The manifests here are built by hand rather than by release-manifest.sh, on
purpose: the reader has to be tested against shapes the writer refuses to
produce, which is exactly where a hand-made or older-writer tag would land.
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "read-manifest.sh"

BASH = shutil.which("bash")
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(
    BASH is None or GIT is None, reason="needs bash and git on PATH"
)

TAG = "apicurio/v9.9.9"

GOOD = textwrap.dedent(
    f"""\
    {TAG}:
      git_commit: "{'a' * 40}"
      built_at: "2026-09-20T18:50:53Z"
      gate:
        check: stacks-ci gate
        run_id: 35530187927
        conclusion: success
      artifacts:
        bff_image:
          repository: 283556840805.dkr.ecr.eu-west-1.amazonaws.com/uif-apicurio-dev-bff-ecr-repo
          digest: sha256:{'b' * 64}
        ui_bundle:
          sha256: "{'c' * 64}"
          archive: s3://uif-apicurio-dev-release-artifacts/{'c' * 64}.tar.gz
          version_id: "0123456789"
        registry_image:
          reference: quay.io/apicurio/apicurio-registry:3.3.2
          digest: sha256:{'d' * 64}
    """
)


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repo, so nothing here can touch the real tags."""
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
    }
    (tmp_path / "f").write_text("x", newline="\n")
    subprocess.run([GIT, "add", "f"], cwd=tmp_path, env=env, check=True)
    subprocess.run(
        [GIT, "commit", "-q", "-m", "UIFPE-1 #comment seed"],
        cwd=tmp_path,
        env=env,
        check=True,
    )
    tmp_path.joinpath(".env").write_text("", newline="\n")
    return tmp_path, env


def _annotate(repo, message: str, tag: str = TAG):
    path, env = repo
    msg = path / "msg.txt"
    msg.write_text(message, newline="\n")
    subprocess.run(
        [GIT, "tag", "-a", tag, "-F", str(msg)], cwd=path, env=env, check=True
    )


def _read(repo, field: str, tag: str = TAG):
    path, env = repo
    return subprocess.run(
        [BASH, str(SCRIPT), tag, field],
        cwd=path,
        env=env,
        capture_output=True,
        text=True,
    )


def _ok(repo, field: str) -> str:
    r = _read(repo, field)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


# ── Reading a well-formed manifest ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "field,expected",
    [
        ("git_commit", "a" * 40),
        ("gate.run_id", "35530187927"),
        ("gate.conclusion", "success"),
        (
            "bff.repository",
            "283556840805.dkr.ecr.eu-west-1.amazonaws.com/uif-apicurio-dev-bff-ecr-repo",
        ),
        ("bff.digest", "sha256:" + "b" * 64),
        ("ui.sha256", "c" * 64),
        ("ui.archive", f"s3://uif-apicurio-dev-release-artifacts/{'c' * 64}.tar.gz"),
        ("ui.version_id", "0123456789"),
        ("registry.reference", "quay.io/apicurio/apicurio-registry:3.3.2"),
        ("registry.digest", "sha256:" + "d" * 64),
    ],
)
def test_reads_each_field(repo, field, expected):
    _annotate(repo, GOOD)
    assert _ok(repo, field) == expected


def test_the_two_digests_do_not_cross_contaminate(repo):
    # `digest:` appears twice in the manifest -- once under bff_image, once
    # under registry_image. A reader matching the key alone would return
    # whichever came first for both, and the wrong one is a valid-looking
    # digest, so nothing downstream would notice.
    _annotate(repo, GOOD)
    assert _ok(repo, "bff.digest") == "sha256:" + "b" * 64
    assert _ok(repo, "registry.digest") == "sha256:" + "d" * 64


def test_a_reference_containing_a_colon_survives(repo):
    # quay.io/...:3.3.2 -- splitting on the first colon would truncate it.
    _annotate(repo, GOOD)
    assert _ok(repo, "registry.reference").endswith(":3.3.2")


def test_an_all_digit_version_id_comes_back_as_written(repo):
    # The writer quotes it precisely because YAML would retype a bare
    # all-digit scalar as an int. The reader must strip the quotes and not the
    # leading zero.
    _annotate(repo, GOOD)
    assert _ok(repo, "ui.version_id") == "0123456789"


# ── Refusal ────────────────────────────────────────────────────────────────────


def test_refuses_a_gate_that_did_not_pass(repo):
    _annotate(repo, GOOD.replace("conclusion: success", "conclusion: failure"))
    r = _read(repo, "bff.digest")
    assert r.returncode != 0
    assert "not 'success'" in r.stderr
    assert "must not be promoted" in r.stderr


def test_refuses_every_field_when_the_gate_did_not_pass(repo):
    # Not only the gate field: a promotion that reads the digest and never
    # looks at the gate would deploy a build whose own evidence says it failed.
    _annotate(repo, GOOD.replace("conclusion: success", "conclusion: cancelled"))
    for field in ("git_commit", "bff.digest", "ui.sha256", "registry.reference"):
        assert _read(repo, field).returncode != 0, f"{field} was handed out anyway"


def test_refuses_a_manifest_with_no_gate_block(repo):
    without = "\n".join(
        line
        for line in GOOD.splitlines()
        if not line.startswith(("  gate:", "    check:", "    run_id:", "    conclusion:"))
    )
    _annotate(repo, without + "\n")
    r = _read(repo, "bff.digest")
    assert r.returncode != 0
    assert "<absent>" in r.stderr


def test_refuses_a_lightweight_tag(repo):
    path, env = repo
    subprocess.run([GIT, "tag", TAG], cwd=path, env=env, check=True)
    r = _read(repo, "bff.digest")
    assert r.returncode != 0
    assert "LIGHTWEIGHT" in r.stderr


def test_refuses_a_tag_that_does_not_exist(repo):
    r = _read(repo, "bff.digest")
    assert r.returncode != 0
    assert "does not exist here" in r.stderr
    # And says why a clone might not have it, rather than only that it is absent.
    assert "shallow clone" in r.stderr


def test_refuses_a_manifest_describing_a_different_tag(repo):
    # Guards a rewritten message: the body must name the tag it is attached to.
    _annotate(repo, GOOD.replace(f"{TAG}:", "apicurio/v0.0.1:", 1))
    r = _read(repo, "bff.digest")
    assert r.returncode != 0
    assert "does not describe this tag" in r.stderr


def test_absent_field_is_an_error_not_an_empty_string(repo):
    _annotate(repo, GOOD.replace(f"      digest: sha256:{'b' * 64}\n", ""))
    r = _read(repo, "bff.digest")
    assert r.returncode != 0
    assert r.stdout.strip() == "", "an absent field must print nothing at all"
    assert "is absent from" in r.stderr


@pytest.mark.parametrize(
    "bad",
    [
        "sha256:" + "B" * 64,  # uppercase: two spellings of one digest
        "sha256:" + "b" * 63,  # truncated
        "b" * 64,  # no algorithm prefix
        "latest",  # a mutable tag where a digest belongs
    ],
)
def test_refuses_a_bff_digest_that_is_not_a_full_lowercase_digest(repo, bad):
    _annotate(repo, GOOD.replace("sha256:" + "b" * 64, bad))
    r = _read(repo, "bff.digest")
    assert r.returncode != 0
    assert "not a full sha256" in r.stderr


def test_refuses_a_ui_sha256_carrying_the_algorithm_prefix(repo):
    _annotate(repo, GOOD.replace(f'sha256: "{"c" * 64}"', f'sha256: "sha256:{"c" * 64}"'))
    r = _read(repo, "ui.sha256")
    assert r.returncode != 0
    assert "no sha256: prefix" in r.stderr


def test_refuses_a_short_git_commit(repo):
    _annotate(repo, GOOD.replace('"' + "a" * 40 + '"', '"aaaaaaa"'))
    r = _read(repo, "git_commit")
    assert r.returncode != 0
    assert "not a full 40-character sha" in r.stderr


def test_refuses_an_unknown_field(repo):
    _annotate(repo, GOOD)
    r = _read(repo, "bff.tag")
    assert r.returncode != 0
    assert "unknown field" in r.stderr
    assert "bff.digest" in r.stderr, "the error should list what is available"


@pytest.mark.parametrize("args", [(), ("apicurio/v9.9.9",)])
def test_requires_both_arguments(repo, args):
    path, env = repo
    r = subprocess.run(
        [BASH, str(SCRIPT), *args], cwd=path, env=env, capture_output=True, text=True
    )
    assert r.returncode != 0
    assert "usage:" in r.stderr
