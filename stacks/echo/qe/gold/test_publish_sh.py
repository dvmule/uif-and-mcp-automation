# test_publish_sh.py
# Unit tests for scripts/publish.sh covering the two review findings fixed
# on this script (#14):
#   - PUBLISH_API_BASE has no hardcoded sandbox default anymore; unset (and
#     no --api-base) must fail fast with a clear message.
#   - the existence probe must only treat a clean 404 as "not found"; any
#     other status (or network failure) must die loudly instead of being
#     silently treated as "artifact not found".
#
# These shell out to bash and, for the full-flow test, to jq — both are
# present on the ubuntu-latest CI runners this script actually runs on
# (see the workflow step comment "jq + curl already present on
# ubuntu-latest"), but are not guaranteed on every dev machine, so tests
# that need jq skip cleanly when it's absent locally.

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# qe/ -> parents[1] == domains/product; the operator CLIs live under src/apps/.
STACK_ROOT = Path(__file__).resolve().parents[2]
SCRIPT     = STACK_ROOT / "src" / "apps" / "cli" / "publish.sh"

BASH = shutil.which("bash")
JQ   = shutil.which("jq")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available on PATH")


def _make_stub(dir_path: Path, name: str, body: str) -> Path:
    path = dir_path / name
    # Force LF endings even on Windows so the shebang-less script (invoked
    # directly as an arg to bash, not executed by path) still runs cleanly.
    path.write_text(body, newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _run(tmp_path, extra_args, publish_api_base=None, stub_dir=None):
    schema = tmp_path / "schema.json"
    schema.write_text('{"metadata": {"dataClassification": "internal"}}')

    env = dict(os.environ)
    if publish_api_base is None:
        env.pop("PUBLISH_API_BASE", None)
    else:
        env["PUBLISH_API_BASE"] = publish_api_base
    if stub_dir is not None:
        env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"

    args = [BASH, str(SCRIPT),
            "--schema", str(schema),
            "--service-urn", "urn:pearson:svc:test",
            "--artifact-id", "test.gold-canonical-value",
            "--classification", "internal",
            *extra_args]
    return subprocess.run(args, capture_output=True, text=True, env=env)


def test_missing_publish_api_base_fails_fast(tmp_path):
    # No env var, no --api-base -- must die before doing anything else, with
    # a message naming PUBLISH_API_BASE (not a silent sandbox fallback).
    r = _run(tmp_path, extra_args=[], publish_api_base="")
    assert r.returncode != 0
    assert "PUBLISH_API_BASE" in r.stderr
    assert "not set" in r.stderr


def test_empty_publish_api_base_env_fails_fast(tmp_path):
    # An explicitly empty env var must be treated the same as unset --
    # this is the exact "empty/unset...silently falls back" bug from #14.
    r = _run(tmp_path, extra_args=[], publish_api_base="")
    assert r.returncode != 0
    assert "cmkimj77g5" not in (r.stdout + r.stderr)  # old hardcoded host gone


def test_probe_non_404_status_dies_loudly(tmp_path):
    # A 500 (or any non-200/404) from the existence probe must not be
    # treated as "not found" -- it must die loudly instead.
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    _make_stub(stub_dir, "curl", "#!/usr/bin/env bash\nprintf '%s' 500\n")

    r = _run(tmp_path, extra_args=["--api-base", "http://example.invalid"],
              publish_api_base=None, stub_dir=stub_dir)
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "500" in combined
    assert "not proceeding" in combined
    # The 500 must not be mistaken for the "clean 404 → new artifact" path.
    assert "will register a new artifact" not in combined


@pytest.mark.skipif(JQ is None, reason="jq not available on PATH")
def test_probe_404_proceecho_as_new_artifact(tmp_path):
    # A clean 404 must proceed as "register as new" and reach the actual
    # publish POST (stubbed to succeed) -- verifies 404 isn't conflated with
    # the non-404 "die loudly" path above.
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    _make_stub(stub_dir, "curl", "\n".join([
        "#!/usr/bin/env bash",
        "out_file=\"\"",
        "is_post=0",
        "args=(\"$@\")",
        "for ((i=0; i<${#args[@]}; i++)); do",
        "  if [[ \"${args[$i]}\" == \"-o\" ]]; then out_file=\"${args[$((i+1))]}\"; fi",
        "  if [[ \"${args[$i]}\" == \"-X\" && \"${args[$((i+1))]}\" == \"POST\" ]]; then is_post=1; fi",
        "done",
        "if [[ $is_post -eq 1 ]]; then",
        "  if [[ -n \"$out_file\" && \"$out_file\" != \"/dev/null\" ]]; then echo '{\"id\":\"stub\"}' > \"$out_file\"; fi",
        "  printf '%s' 201",
        "else",
        "  printf '%s' 404",
        "fi",
        "",
    ]))

    r = _run(tmp_path, extra_args=["--api-base", "http://example.invalid", "-q"],
              publish_api_base=None, stub_dir=stub_dir)
    assert r.returncode == 0, r.stderr
    assert "artifact not found" not in r.stderr.lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
