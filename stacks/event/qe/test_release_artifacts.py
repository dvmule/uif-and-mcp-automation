"""The release-artifact contract (docs/uif-event-uat-rollout-plan.md, Phase 1).

Two copies of one fact must agree, and nothing else would notice if they did
not:

  scripts/stage-release-artifacts.sh   the artifact SET that is hashed and uploaded
  infrastructure/terraform/    the keys every Lambda and Glue job READS

A name staged but not read ships dead weight; a name read but not staged
resolves to ARTIFACT-SHA-NOT-SUPPLIED and artifact_guard.tf refuses the plan --
but only on the day someone deploys. These tests move that failure to the pull
request. The script tests exercise the refusals the manifest's trust rests on,
each one proven failing rather than assumed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parent.parent
SCRIPTS = STACK / "scripts"
TF = STACK / "infrastructure" / "terraform"

BASH = shutil.which("bash")
GIT = shutil.which("git")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not on PATH")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


STAGE = _read(SCRIPTS / "stage-release-artifacts.sh")
LOCALS = _read(TF / "locals.tf")

GLUE_CALLS = re.findall(r"^glue_script\s+(\w+)\s+(\S+)$", STAGE, re.M)
LAMBDA_CALLS = re.findall(r"^lambda_zip\s+(\w+)\s+(.+)$", STAGE, re.M)


def _tf_glue_scripts() -> dict[str, str]:
    block = re.search(r"glue_scripts = \{(.*?)\n  \}", LOCALS, re.S).group(1)
    return dict(re.findall(r'(\w+)\s*=\s*"([^"]+)"', block))


def _tf_lambda_artifacts() -> list[str]:
    block = re.search(r"lambda_artifacts = \[(.*?)\]", LOCALS, re.S).group(1)
    return re.findall(r'"(\w+)"', block)


# ── The set: staged == read ─────────────────────────────────────────────────

def test_glue_scripts_staged_are_the_ones_terraform_reads():
    staged = {name: Path(src).name for name, src in GLUE_CALLS}
    assert staged == _tf_glue_scripts()


def test_lambdas_staged_are_the_ones_terraform_reads():
    assert sorted(n for n, _ in LAMBDA_CALLS) == sorted(_tf_lambda_artifacts())


def test_expected_row_count_is_derived_from_the_calls():
    declared = int(re.search(r"^EXPECTED_ROWS=(\d+)$", STAGE, re.M).group(1))
    assert declared == len(GLUE_CALLS) + len(LAMBDA_CALLS)


def test_artifact_names_are_unique_across_classes():
    names = [n for n, _ in GLUE_CALLS] + [n for n, _ in LAMBDA_CALLS]
    assert len(names) == len(set(names)), "a duplicate silently overwrites its twin in artifact_shas"


def test_every_staged_source_exists():
    for _, src in GLUE_CALLS:
        assert (STACK / src).is_file(), src
    for _, srcs in LAMBDA_CALLS:
        for src in srcs.split():
            assert (STACK / src).is_file(), src


@pytest.mark.parametrize("tf_file,name", [
    ("controller.tf", "pipeline_controller"),
    ("dlq-recorder.tf", "dlq_recorder"),
    ("provisioner.tf", "subscription_provisioner"),
    ("redrive.tf", "event_redrive"),
])
def test_each_lambda_reads_its_release_key_and_its_handler_is_in_the_zip(tf_file, name):
    body = _read(TF / tf_file)
    assert f'local.lambda_s3_key["{name}"]' in body
    assert f'lookup(var.lambda_code_hashes, "{name}", null)' in body
    assert "aws_s3_bucket.release_artifacts.id" in body
    # The handler module must sit at the zip root under the same filename.
    module = re.search(r'handler\s*=\s*"(\w+)\.lambda_handler"', body).group(1)
    staged = dict(LAMBDA_CALLS)[name].split()
    assert f"{module}.py" in {Path(s).name for s in staged}


@pytest.mark.parametrize("tf_file,name", [
    ("glue.tf", "event_fabric_processor"),
    ("recovery.tf", "event_fabric_recovery"),
])
def test_each_glue_job_reads_its_release_uri_and_may_only_read_it(tf_file, name):
    body = _read(TF / tf_file)
    assert f'script_location = local.glue_script_uri["{name}"]' in body
    grant = re.search(
        r'Action\s*=\s*\["s3:GetObject"\]\s*\n\s*Resource\s*=\s*"arn:aws:s3:::\$\{local\.release_artifacts_bucket\}/glue/\*"',
        body)
    assert grant, "the job needs read on its script, and read ONLY"


def test_nothing_in_the_root_builds_from_the_repo():
    for tf in TF.glob("*.tf"):
        body = _read(tf)
        assert 'data "archive_file"' not in body, tf.name
        assert not re.search(r'resource "aws_s3_object" "event_fabric_\w+_script"', body), tf.name
        assert not re.search(r"^\s*filename\s*=", body, re.M), tf.name


def test_the_guard_covers_both_classes():
    assert "local.glue_script_uri" in re.search(r"artifacts_missing_sha = .*?\)\)", LOCALS, re.S).group(0)
    assert "local.lambda_s3_key" in re.search(r"artifacts_missing_sha = .*?\)\)", LOCALS, re.S).group(0)
    guard = _read(TF / "artifact_guard.tf")
    assert "length(local.artifacts_missing_sha) == 0" in guard


def test_release_bucket_is_versioned_and_never_expires():
    body = _read(TF / "release.tf")
    assert 'status = "Enabled"' in body
    assert "aws_s3_bucket_lifecycle_configuration" not in body


def test_the_hashes_file_is_never_tracked():
    assert "artifacts.auto.tfvars.json" in _read(TF / ".gitignore").splitlines()


# ── The scripts ─────────────────────────────────────────────────────────────

def _run(args, env=None, cwd=None):
    return subprocess.run([BASH, *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, cwd=cwd)


@needs_bash
def test_hash_only_staging_is_reproducible(tmp_path):
    outs = []
    for i in range(2):
        out = tmp_path / f"a{i}.json"
        r = _run([str(SCRIPTS / "stage-release-artifacts.sh"), "--hash-only", "--tfvars", str(out)])
        assert r.returncode == 0, r.stderr
        outs.append(json.loads(out.read_text()))
    assert outs[0] == outs[1]
    assert set(outs[0]["artifact_shas"]) == set(_tf_glue_scripts()) | set(_tf_lambda_artifacts())
    assert set(outs[0]["lambda_code_hashes"]) == set(_tf_lambda_artifacts())
    for v in outs[0]["artifact_shas"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", v)
    for v in outs[0]["lambda_code_hashes"].values():
        assert re.fullmatch(r"[A-Za-z0-9+/]{43}=", v)


@needs_bash
def test_hash_only_refuses_to_write_manifest_rows(tmp_path):
    r = _run([str(SCRIPTS / "stage-release-artifacts.sh"), "--hash-only", "--tsv", str(tmp_path / "x.tsv")])
    assert r.returncode != 0 and "cannot write --tsv" in r.stderr


H = "a" * 64
G = "b" * 64
CODE = "LQiCVhifvjpdDBNwNJ0uhsUH/hr/g5ATzZgKZK+gBBU="
GOOD_ROWS = [
    ["glue_scripts", "event_fabric_processor", "src/glue/event_fabric_processor.py", H,
     f"glue/{H}/event_fabric_processor.py", "v1", "-", "-"],
    ["lambdas", "dlq_recorder", "-", G, f"lambdas/{G}.zip", "v2", "-", CODE],
]
ENV = {"TAG": "event/v9.9.9", "GIT_COMMIT": "c" * 40, "GATE_RUN_ID": "123",
       "GATE_CONCLUSION": "success", "BUCKET": "uif-event-dev-release-artifacts",
       "BUILT_AT": "2026-01-01T00:00:00Z"}


def _manifest(tmp_path, rows, **env):
    tsv = tmp_path / "a.tsv"
    # Bytes, not text: on Windows, text mode writes CRLF and bash reads "-\r".
    tsv.write_bytes("".join("\t".join(r) + "\n" for r in rows).encode("utf-8"))
    return _run([str(SCRIPTS / "release-manifest.sh")],
                env={**ENV, "ARTIFACT_FILE": str(tsv), **env})


@needs_bash
@pytest.mark.parametrize("mutate,expect", [
    (lambda r: r[0].__setitem__(3, H.upper()), "64 lowercase hex"),
    (lambda r: r[0].__setitem__(3, H[:40]), "64 lowercase hex"),
    (lambda r: r[1].__setitem__(7, "-"), "code_sha256 is required"),
    (lambda r: r[0].__setitem__(0, "layers"), "unknown artifact class"),
    (lambda r: r[0].__setitem__(0, "ui_bundle"), "unknown artifact class"),
    (lambda r: r[0].__setitem__(4, f"glue/{G}/x.py"), "does not contain its own sha256"),
    (lambda r: r.append(list(r[0])), "appears twice"),
])
def test_release_manifest_refuses(tmp_path, mutate, expect):
    rows = [list(r) for r in GOOD_ROWS]
    mutate(rows)
    r = _manifest(tmp_path, rows)
    assert r.returncode != 0 and expect in r.stderr, r.stderr


@needs_bash
def test_release_manifest_refuses_a_gate_that_did_not_pass(tmp_path):
    r = _manifest(tmp_path, GOOD_ROWS, GATE_CONCLUSION="failure")
    assert r.returncode != 0 and "refusing" in r.stderr


@needs_bash
@pytest.mark.skipif(GIT is None, reason="git not on PATH")
def test_manifest_round_trips_through_an_annotated_tag(tmp_path):
    m = _manifest(tmp_path, GOOD_ROWS)
    assert m.returncode == 0, m.stderr
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run([GIT, *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    (tmp_path / "m.yaml").write_bytes(m.stdout.encode("utf-8"))
    git("-c", "user.name=t", "-c", "user.email=t@t", "tag", "-a", "event/v9.9.9", "-F", str(tmp_path / "m.yaml"))

    read = lambda *a: _run([str(SCRIPTS / "read-manifest.sh"), "event/v9.9.9", *a], cwd=repo)  # noqa: E731
    dump = read("--dump")
    assert dump.returncode == 0, dump.stderr
    assert sorted(dump.stdout.splitlines()) == sorted("\t".join(r) for r in GOOD_ROWS)
    assert read("lambdas.dlq_recorder.code_sha256").stdout.strip() == CODE
    assert read("bucket").stdout.strip() == "uif-event-dev-release-artifacts"
    assert read("--list", "glue_scripts").stdout.split() == ["event_fabric_processor"]
    bad = read("--list", "layers")
    assert bad.returncode != 0 and "unknown class" in bad.stderr

    git("tag", "light")  # lightweight: no message, no manifest
    lw = _run([str(SCRIPTS / "read-manifest.sh"), "light", "bucket"], cwd=repo)
    assert lw.returncode != 0 and "LIGHTWEIGHT" in lw.stderr
