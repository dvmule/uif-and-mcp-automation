"""Unit tests for scripts/release-manifest.sh and scripts/read-tfvar.sh.

The manifest is the release's evidence -- what QE signed off and what Ops rolls
back to. So the tests that matter are the refusals: a manifest must not be
writable with a mutable tag where a digest belongs, with a truncated digest, or
with a gate that did not pass. A manifest that lies is worse than no manifest,
because it looks like evidence.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

STACK_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = STACK_ROOT / "scripts" / "release-manifest.sh"
READ_TFVAR = STACK_ROOT / "scripts" / "read-tfvar.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available on PATH")

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64

GOOD = {
    "TAG": "apicurio/v0.1.0",
    "GIT_COMMIT": "8f31a2c9e4b7d1a3f5c8e2b6d9a4f7c1e3b5d8a2",
    "BUILT_AT": "2026-09-19T08:32:11Z",
    "GATE_RUN_ID": "18472910334",
    "GATE_CONCLUSION": "success",
    "BFF_REPOSITORY": "1234.dkr.ecr.eu-west-1.amazonaws.com/uif-apicurio-dev-bff-ecr-repo",
    "BFF_DIGEST": DIGEST_A,
    "UI_SHA256": "9" * 64,
    "UI_ARCHIVE": "s3://uif-apicurio-dev-release-artifacts/" + "9" * 64 + ".tar.gz",
    "UI_VERSION_ID": "3HL4kqtJ8pXo",
    "REGISTRY_REFERENCE": "quay.io/apicurio/apicurio-registry:3.3.2",
    "REGISTRY_DIGEST": DIGEST_B,
}


def _run(env_overrides=None, drop=None):
    env = dict(GOOD)
    env.update(env_overrides or {})
    for key in drop or []:
        env.pop(key, None)
    return subprocess.run(
        [BASH, str(MANIFEST)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env}
    )


def test_produces_parseable_yaml_keyed_by_the_tag():
    result = _run()
    assert result.returncode == 0, result.stderr
    doc = yaml.safe_load(result.stdout)
    assert list(doc.keys()) == ["apicurio/v0.1.0"]


def test_records_the_commit_the_gate_and_all_three_artifacts():
    doc = yaml.safe_load(_run().stdout)["apicurio/v0.1.0"]
    assert doc["git_commit"] == GOOD["GIT_COMMIT"]
    assert doc["built_at"] == "2026-09-19T08:32:11Z"
    assert doc["gate"] == {
        "check": "stacks-ci gate",
        "run_id": 18472910334,
        "conclusion": "success",
    }
    assert set(doc["artifacts"]) == {"bff_image", "ui_bundle", "registry_image"}
    assert doc["artifacts"]["bff_image"]["digest"] == DIGEST_A
    assert doc["artifacts"]["registry_image"]["digest"] == DIGEST_B
    assert doc["artifacts"]["ui_bundle"]["sha256"] == "9" * 64


def test_all_digit_identifiers_stay_strings():
    # A commit sha or an S3 version id can legitimately contain no hex
    # letters. Unquoted, YAML's type inference would silently reinterpret
    # such a value as an int -- the manifest changing type under us is
    # exactly the "manifest that lies" failure this script exists to prevent.
    doc = yaml.safe_load(
        _run(
            {
                "GIT_COMMIT": "1" * 40,
                "UI_VERSION_ID": "12345678901234",
            }
        ).stdout
    )["apicurio/v0.1.0"]
    assert isinstance(doc["git_commit"], str)
    assert doc["git_commit"] == "1" * 40
    assert isinstance(doc["artifacts"]["ui_bundle"]["version_id"], str)
    assert doc["artifacts"]["ui_bundle"]["version_id"] == "12345678901234"


@pytest.mark.parametrize("bad", ["latest", "3.3.2", "sha256:abc", "sha256:" + "A" * 64, ""])
def test_refuses_a_bff_digest_that_is_not_a_full_lowercase_digest(bad):
    result = _run({"BFF_DIGEST": bad})
    assert result.returncode == 1
    assert "digest" in result.stderr.lower()


@pytest.mark.parametrize("bad", ["latest", "sha256:abc", ""])
def test_refuses_a_registry_digest_that_is_not_a_digest(bad):
    result = _run({"REGISTRY_DIGEST": bad})
    assert result.returncode == 1
    assert "digest" in result.stderr.lower()


@pytest.mark.parametrize("bad", ["sha256:" + "9" * 64, "abc", ""])
def test_refuses_a_ui_hash_that_is_not_a_bare_sha256(bad):
    result = _run({"UI_SHA256": bad})
    assert result.returncode == 1


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "neutral", ""])
def test_refuses_to_write_a_manifest_when_the_gate_did_not_pass(conclusion):
    result = _run({"GATE_CONCLUSION": conclusion})
    assert result.returncode == 1
    assert "gate" in result.stderr.lower()


@pytest.mark.parametrize("missing", ["TAG", "GIT_COMMIT", "BFF_REPOSITORY", "UI_ARCHIVE"])
def test_fails_when_a_required_input_is_unset(missing):
    result = _run(drop=[missing])
    assert result.returncode != 0
    assert missing in result.stderr


# -- read-tfvar.sh --------------------------------------------------------------

TFVARS = '''
# a comment mentioning registry_image = "decoy"
env = "dev"
registry_image        = "quay.io/apicurio/apicurio-registry:3.3.2"
registry_image_digest = "sha256:cafebabe"
registry_desired_count = 2
'''


def _read(tmp_path, name):
    path = tmp_path / "dev.tfvars"
    path.write_text(TFVARS, newline="\n")
    return subprocess.run(
        [BASH, str(READ_TFVAR), str(path), name], capture_output=True, text=True
    )


def test_read_tfvar_returns_a_quoted_string_value(tmp_path):
    result = _read(tmp_path, "registry_image")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "quay.io/apicurio/apicurio-registry:3.3.2"


def test_read_tfvar_does_not_match_a_commented_line(tmp_path):
    # The decoy lives in a comment. Matching it would pin the release to a
    # value nothing deploys.
    assert _read(tmp_path, "registry_image").stdout.strip() != "decoy"


def test_read_tfvar_does_not_prefix_match_a_longer_name(tmp_path):
    # registry_image must not return registry_image_digest's value.
    assert _read(tmp_path, "registry_image").stdout.strip().startswith("quay.io/")


def test_read_tfvar_fails_when_the_name_is_absent(tmp_path):
    result = _read(tmp_path, "not_there")
    assert result.returncode == 1
    assert "not_there" in result.stderr
