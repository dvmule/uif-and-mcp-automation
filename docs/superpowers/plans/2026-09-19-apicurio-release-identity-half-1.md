# Apicurio Release Identity (Half 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Apicurio one release identifier, `apicurio/vX.Y.Z`, that names an exact, reproducible set of artifacts — a declared version, a git tag cut on a `VERSION` bump, and a manifest in the tag binding all three artifacts to the commit and the CI gate run that passed on it.

**Architecture:** All logic that needs testing lives in small shell scripts under `stacks/apicurio/scripts/`, tested from pytest via `subprocess` (the pattern `stacks/echo/qe/gold/test_publish_sh.py` already establishes). Workflow YAML only wires those scripts together, because YAML is not testable. A new `release-apicurio.yml` validates, waits for the gate, resolves three immutable artifact references, and cuts an annotated tag whose message is the manifest. `deploy-apicurio-dev.yml` changes only in its UI job, where the API origin moves from a build-time inlined constant to a runtime-fetched `config.json`.

**Tech Stack:** Bash + pytest (scripts and their tests), Terraform 1.15.8, React 18 + Vite + vitest (UI), GitHub Actions, AWS ECR / S3 / ECS.

**Spec:** `docs/superpowers/specs/2026-09-19-apicurio-release-identity-design.md` — read it before Task 1. Every decision below is numbered against that document's Decisions section.

## Global Constraints

- **Component token is `apicurio`.** Never `apic`. Tag format `apicurio/v<MAJOR>.<MINOR>.<PATCH>`. (Spec decision 2.)
- **Semver regex:** `^[0-9]+\.[0-9]+\.[0-9]+$`. No pre-release or build suffixes.
- **Digest regex:** `^sha256:[0-9a-f]{64}$`. Bare sha256 (UI bundle) regex: `^[0-9a-f]{64}$`.
- **`VERSION` starts at `0.1.0`.** Apicurio runs in dev only and makes no stability promise.
- **The bundle hash always excludes `config.json`.** Including it makes the hash environment-specific, which defeats the entire purpose. (Spec decision 10.)
- **Archive key is `<sha256>.tar.gz`** at the bucket root — a content address, never a version token.
- **AWS region is `eu-west-1`.** ECR repo is `uif-apicurio-dev-bff-ecr-repo`.
- **Terraform is pinned to `1.15.8`**, Node to `20`. Use `npm` 10, never 11 — `npx --yes npm@10 install` when a lockfile must be regenerated, because npm 11 drops entries npm 10 keeps and `npm ci` then fails in CI.
- **GitHub Actions allow-list.** Only these `uses:` owners pass the enterprise policy and `workflow-lint`: `actions`, `github`, `hashicorp`, `aws-actions`, `aquasecurity`, `sonarsource`, `opentofu`, plus `pearson*`. **A single disallowed `uses:` fails the whole workflow at startup with no job logs.** This plan therefore uses `gh release create` rather than any third-party release action.
- **Every commit message needs a Jira key and a comment:** `UIFPE-36 #comment <subject>`. Branch names carry the key too (`jira-transition.yml` reads it from the branch name).
- **Do not touch `main`.** Whether and how `main` advances is open question 1 in the spec.

---

### Task 1: The declared version and its validator

**Files:**
- Create: `stacks/apicurio/VERSION`
- Create: `stacks/apicurio/scripts/check-version.sh`
- Create: `stacks/apicurio/qe/tests/release/test_check_version_sh.py`
- Modify: `stacks/apicurio/src/pyproject.toml:3`
- Modify: `stacks/apicurio/ui/package.json:4`

**Interfaces:**
- Consumes: nothing.
- Produces: `scripts/check-version.sh <path-to-VERSION>` — prints the implied tag (`apicurio/v0.1.0`) on stdout and exits 0, or prints a diagnostic on stderr and exits 1. Reads `COMPONENT` from the environment, defaulting to `apicurio`. Task 7 calls it.

- [ ] **Step 1: Write the failing test**

Create `stacks/apicurio/qe/tests/release/test_check_version_sh.py`:

```python
"""Unit tests for scripts/check-version.sh.

These shell out to bash and to git, both present on the ubuntu-latest runners
this script actually runs on. Each test builds a throwaway git repo in tmp_path
so tag state is controlled rather than inherited from the checkout.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "check-version.sh"

BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(
    BASH is None or GIT is None, reason="bash and git must be on PATH"
)


def _repo(tmp_path: Path, version: str, tags: list[str]) -> Path:
    subprocess.run([GIT, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "VERSION").write_text(version + "\n", newline="\n")
    subprocess.run([GIT, "add", "VERSION"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "commit", "-q", "-m", "x"], cwd=tmp_path, check=True)
    for tag in tags:
        subprocess.run([GIT, "tag", tag], cwd=tmp_path, check=True)
    return tmp_path


def _run(repo: Path):
    return subprocess.run(
        [BASH, str(SCRIPT), str(repo / "VERSION")],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_prints_the_tag_for_a_first_release(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0", []))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apicurio/v0.1.0"


def test_tolerates_trailing_whitespace_in_the_file(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0  ", []))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apicurio/v0.1.0"


@pytest.mark.parametrize("bad", ["0.1", "v0.1.0", "0.1.0-rc1", "1.0", "", "abc"])
def test_rejects_anything_that_is_not_bare_semver(tmp_path, bad):
    result = _run(_repo(tmp_path, bad, []))
    assert result.returncode == 1
    assert "MAJOR.MINOR.PATCH" in result.stderr


def test_rejects_a_version_whose_tag_already_exists(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0", ["apicurio/v0.1.0"]))
    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_rejects_a_version_not_greater_than_the_latest(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0", ["apicurio/v0.2.0"]))
    assert result.returncode == 1
    assert "not greater" in result.stderr


def test_compares_numerically_not_lexically(tmp_path):
    # 0.10.0 IS greater than 0.9.0. A lexical `sort` says the opposite, which
    # would refuse every release after the ninth minor.
    result = _run(_repo(tmp_path, "0.10.0", ["apicurio/v0.9.0"]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apicurio/v0.10.0"


def test_ignores_tags_belonging_to_other_components(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0", ["echo/v9.9.9"]))
    assert result.returncode == 0, result.stderr


def test_fails_clearly_when_the_file_is_missing(tmp_path):
    subprocess.run([GIT, "init", "-q"], cwd=tmp_path, check=True)
    result = subprocess.run(
        [BASH, str(SCRIPT), str(tmp_path / "VERSION")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio && python3 -m pytest qe/tests/release/test_check_version_sh.py -v`
Expected: every test FAILS — the script does not exist yet.

- [ ] **Step 3: Write `stacks/apicurio/VERSION`**

```
0.1.0
```

One line, trailing newline, nothing else.

- [ ] **Step 4: Write `stacks/apicurio/scripts/check-version.sh`**

```bash
#!/usr/bin/env bash
# Validate this stack's declared version and print the build tag it implies.
#
# The version is DECLARED, not derived (spec decision 1): semver on a
# capability is a claim about whether consumers break, and only a human who
# understands the change can make that claim. This script's whole job is to
# stop the three mistakes that claim invites -- a malformed number, a number
# that has already shipped, and a number that goes backwards.
#
# Usage: check-version.sh <path-to-VERSION>
# Prints: apicurio/v<version> on success. Diagnostic on stderr, exit 1 otherwise.

set -euo pipefail

version_file="${1:?usage: check-version.sh <path-to-VERSION>}"
component="${COMPONENT:-apicurio}"

if [ ! -f "$version_file" ]; then
  echo "error: ${version_file} does not exist" >&2
  exit 1
fi

# tr, not $(cat), so a stray CR from a Windows editor cannot produce a tag
# with an invisible character in it.
version="$(tr -d '[:space:]' < "$version_file")"

if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "error: VERSION must be bare MAJOR.MINOR.PATCH, got '${version}'" >&2
  exit 1
fi

tag="${component}/v${version}"

if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null 2>&1; then
  echo "error: tag ${tag} already exists -- bump VERSION to release again" >&2
  exit 1
fi

# -k1,1n -k2,2n -k3,3n sorts the three fields NUMERICALLY. A plain sort is
# lexical, which puts 0.9.0 above 0.10.0 and would refuse every release after
# the ninth minor.
latest="$(
  git tag --list "${component}/v*" \
    | sed "s|^${component}/v||" \
    | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' \
    | sort -t. -k1,1n -k2,2n -k3,3n \
    | tail -1 || true
)"

if [ -n "$latest" ]; then
  highest="$(printf '%s\n%s\n' "$latest" "$version" \
    | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
  # The equality check is separate because a tie leaves `highest` equal to
  # `version`, which the inequality below would wave through.
  if [ "$version" = "$latest" ] || [ "$highest" != "$version" ]; then
    echo "error: VERSION ${version} is not greater than the latest release ${latest}" >&2
    exit 1
  fi
fi

printf '%s\n' "$tag"
```

Then: `chmod +x stacks/apicurio/scripts/check-version.sh`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio && python3 -m pytest qe/tests/release/test_check_version_sh.py -v`
Expected: PASS, 13 tests (the parametrised case counts 6).

- [ ] **Step 6: Retire the two misleading package versions**

In `stacks/apicurio/src/pyproject.toml`, change line 3 from `version = "0.1.0"` to:

```toml
# Not the release identity. This package is never published to an index; the
# capability's version is declared in stacks/apicurio/VERSION and released as
# the tag apicurio/vX.Y.Z. Two independent 0.1.0s next to a real release
# identity is the ambiguity the release tag exists to remove.
version = "0.0.0"
```

In `stacks/apicurio/ui/package.json`, change `"version": "0.1.0"` to `"version": "0.0.0"`. JSON takes no comments, so the explanation goes in the commit message only.

- [ ] **Step 7: Verify nothing read those versions**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework && grep -rn "0\.1\.0" stacks/apicurio --include=*.py --include=*.ts --include=*.tsx --include=*.tf --include=*.yml | grep -v node_modules | grep -v VERSION`
Expected: no hits that read the package version at runtime. If a hit reads `importlib.metadata.version` or `package.json`'s `version`, stop and report it — it changes this task.

- [ ] **Step 8: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/VERSION stacks/apicurio/scripts/check-version.sh \
        stacks/apicurio/qe/tests/release/test_check_version_sh.py \
        stacks/apicurio/src/pyproject.toml stacks/apicurio/ui/package.json
git commit -m "UIFPE-36 #comment Declare Apicurio's version in VERSION and validate it

The capability's version is now declared in stacks/apicurio/VERSION and
validated by scripts/check-version.sh, which refuses a malformed number, one
whose tag already exists, and one that goes backwards. Version comparison is
numeric, not lexical, so 0.10.0 correctly follows 0.9.0.

src/pyproject.toml and ui/package.json drop to 0.0.0: neither is published to
an index, so neither carried information, and two independent 0.1.0s beside a
real release identity is exactly the ambiguity a release tag removes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Deterministic bundle hash

**Files:**
- Create: `stacks/apicurio/scripts/bundle-hash.sh`
- Create: `stacks/apicurio/qe/tests/release/test_bundle_hash_sh.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `scripts/bundle-hash.sh <dist-dir>` — prints a bare 64-char lowercase hex sha256 on stdout, exit 0; diagnostic on stderr, exit 1. Tasks 7 and 8 both call it, and **they must agree**, which is why it is one script rather than two inline pipelines.

- [ ] **Step 1: Write the failing test**

Create `stacks/apicurio/qe/tests/release/test_bundle_hash_sh.py`:

```python
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


def test_a_nested_config_json_DOES_change_the_hash(tmp_path):
    # Only the bundle-root config.json is environment state. A file that
    # happens to share the name deeper in the tree is real content.
    plain = _hash(_dist(tmp_path / "a" / "dist", BUNDLE))
    nested = _hash(_dist(tmp_path / "b" / "dist", {**BUNDLE, "assets/config.json": "{}"}))
    assert plain != nested


def test_changed_file_contents_change_the_hash(tmp_path):
    a = _hash(_dist(tmp_path / "a" / "dist", BUNDLE))
    b = _hash(_dist(tmp_path / "b" / "dist", {**BUNDLE, "index.html": "<html> </html>"}))
    assert a != b


def test_renaming_a_file_changes_the_hash(tmp_path):
    a = _hash(_dist(tmp_path / "a" / "dist", BUNDLE))
    b = _hash(
        _dist(tmp_path / "b" / "dist",
              {"index.html": BUNDLE["index.html"], "assets/app-def456.js": BUNDLE["assets/app-abc123.js"]})
    )
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio && python3 -m pytest qe/tests/release/test_bundle_hash_sh.py -v`
Expected: FAIL — script missing.

- [ ] **Step 3: Write `stacks/apicurio/scripts/bundle-hash.sh`**

```bash
#!/usr/bin/env bash
# Content hash of a built UI bundle -- the bundle's identity in the release
# manifest.
#
# Two properties this has to have, and one it must NOT have.
#
# Must: depend on every file's path AND contents (a renamed chunk is a
# different bundle), and be identical for identical contents regardless of
# where dist/ happens to sit on disk -- release-apicurio.yml and
# deploy-apicurio-dev.yml both call this and have to agree.
#
# Must not: depend on ./config.json. That file is environment state, written
# per environment at deploy time, and including it would make the hash
# environment-specific -- which would make one bundle unpromotable and defeat
# the reason the runtime-config change exists at all.
#
# Usage: bundle-hash.sh <dist-dir>
# Prints: 64 lowercase hex characters.

set -euo pipefail

dist="${1:?usage: bundle-hash.sh <dist-dir>}"

if [ ! -d "$dist" ]; then
  echo "error: ${dist} is not a directory" >&2
  exit 1
fi

# cd first so every path fed to sha256sum is relative (./index.html), which is
# what makes the hash independent of the directory's location.
cd "$dist"

count="$(find . -type f ! -path './config.json' | wc -l | tr -d '[:space:]')"
if [ "$count" -eq 0 ]; then
  echo "error: no files under ${dist} to hash" >&2
  exit 1
fi

# -print0 / sort -z / xargs -0 so a filename containing a space or newline
# cannot split into two entries. LC_ALL=C fixes the collation order, which
# otherwise varies by locale and would give a different hash on a different
# machine for the same bundle.
find . -type f ! -path './config.json' -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum \
  | sha256sum \
  | cut -d' ' -f1
```

Then: `chmod +x stacks/apicurio/scripts/bundle-hash.sh`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio && python3 -m pytest qe/tests/release/test_bundle_hash_sh.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Confirm it agrees with itself on the real bundle**

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio/ui
npm ci && npm run build
../scripts/bundle-hash.sh dist
echo '{"apiUrl":"https://example.invalid"}' > dist/config.json
../scripts/bundle-hash.sh dist
```
Expected: the two hashes are identical. If they differ, `config.json` is being included and the promotability property is broken — stop and fix before continuing.

- [ ] **Step 6: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/scripts/bundle-hash.sh stacks/apicurio/qe/tests/release/test_bundle_hash_sh.py
git commit -m "UIFPE-36 #comment Add a deterministic content hash for the UI bundle

The UI bundle is the one Apicurio artifact with no immutable reference -- it is
synced in place to the bucket root with --delete and its S3 version IDs are
never captured, which is Echo's Glue-script problem in the stack the delivery
guide calls the easy case.

This is the identity half of the fix: a hash over every file's path and
contents, independent of where dist/ sits, and deliberately excluding
./config.json, which is environment state rather than bundle content. Both the
release and the deploy workflow call this one script, because a hash the two
disagreed about would be worse than no hash.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Manifest assembly and tfvars reading

**Files:**
- Create: `stacks/apicurio/scripts/read-tfvar.sh`
- Create: `stacks/apicurio/scripts/release-manifest.sh`
- Create: `stacks/apicurio/qe/tests/release/test_release_manifest_sh.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `scripts/read-tfvar.sh <tfvars-file> <name>` — prints the value of a top-level `name = "value"` assignment, exit 0; exit 1 if absent.
  - `scripts/release-manifest.sh` — reads everything from the environment and writes the manifest YAML to stdout. Required: `TAG`, `GIT_COMMIT`, `GATE_RUN_ID`, `GATE_CONCLUSION`, `BFF_REPOSITORY`, `BFF_DIGEST`, `UI_SHA256`, `UI_ARCHIVE`, `UI_VERSION_ID`, `REGISTRY_REFERENCE`, `REGISTRY_DIGEST`. Optional `BUILT_AT` overrides the timestamp (tests set it). Task 7 calls both.

- [ ] **Step 1: Write the failing test**

Create `stacks/apicurio/qe/tests/release/test_release_manifest_sh.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio && python3 -m pytest qe/tests/release/test_release_manifest_sh.py -v`
Expected: FAIL — both scripts missing. If `yaml` is not importable, run `python3 -m pip install pyyaml` first.

- [ ] **Step 3: Write `stacks/apicurio/scripts/read-tfvar.sh`**

```bash
#!/usr/bin/env bash
# Read one top-level assignment out of a .tfvars file.
#
# Exists so the release workflow pins the registry image and digest to the
# values Terraform will actually apply, rather than to a second copy of them
# declared in the workflow. Two copies of a digest is a drift class, not a
# convenience.
#
# Usage: read-tfvar.sh <tfvars-file> <name>

set -euo pipefail

file="${1:?usage: read-tfvar.sh <tfvars-file> <name>}"
name="${2:?usage: read-tfvar.sh <tfvars-file> <name>}"

if [ ! -f "$file" ]; then
  echo "error: ${file} does not exist" >&2
  exit 1
fi

# Anchored on start-of-line so a name appearing inside a comment or a nested
# block cannot match, and the name is followed by whitespace-then-= so
# `registry_image` cannot return `registry_image_digest`'s value.
value="$(
  grep -E "^[[:space:]]*${name}[[:space:]]*=" "$file" \
    | grep -v '^[[:space:]]*#' \
    | head -1 \
    | sed -E "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*//" \
    | sed -E 's/^"(.*)"[[:space:]]*$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
)"

if [ -z "$value" ]; then
  echo "error: ${name} is not set in ${file}" >&2
  exit 1
fi

printf '%s\n' "$value"
```

Then: `chmod +x stacks/apicurio/scripts/read-tfvar.sh`

- [ ] **Step 4: Write `stacks/apicurio/scripts/release-manifest.sh`**

```bash
#!/usr/bin/env bash
# Assemble the release manifest carried in the annotated tag's message.
#
# The manifest is the release's evidence: it is what QE signs off against and
# what Ops rolls back to. So this script's real job is refusal. It will not
# write a manifest that records a mutable tag where a digest belongs, a
# truncated digest, or a gate that did not pass -- because a manifest that
# lies is worse than none at all, looking exactly like evidence.
#
# Every input arrives in the environment. BUILT_AT is overridable so the
# output is reproducible under test.

set -euo pipefail

: "${TAG:?TAG is required}"
: "${GIT_COMMIT:?GIT_COMMIT is required}"
: "${GATE_RUN_ID:?GATE_RUN_ID is required}"
: "${GATE_CONCLUSION:?GATE_CONCLUSION is required}"
: "${BFF_REPOSITORY:?BFF_REPOSITORY is required}"
: "${BFF_DIGEST:?BFF_DIGEST is required}"
: "${UI_SHA256:?UI_SHA256 is required}"
: "${UI_ARCHIVE:?UI_ARCHIVE is required}"
: "${UI_VERSION_ID:?UI_VERSION_ID is required}"
: "${REGISTRY_REFERENCE:?REGISTRY_REFERENCE is required}"
: "${REGISTRY_DIGEST:?REGISTRY_DIGEST is required}"

die() { echo "error: $*" >&2; exit 1; }

# Lowercase hex only: ECR and quay both return lowercase, and accepting
# uppercase would let two spellings of one digest into the record.
for pair in "BFF_DIGEST=${BFF_DIGEST}" "REGISTRY_DIGEST=${REGISTRY_DIGEST}"; do
  name="${pair%%=*}"; value="${pair#*=}"
  printf '%s' "$value" | grep -Eq '^sha256:[0-9a-f]{64}$' \
    || die "${name} must be a full digest of the form sha256:<64 lowercase hex>, got '${value}'"
done

printf '%s' "$UI_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
  || die "UI_SHA256 must be 64 lowercase hex characters with no sha256: prefix, got '${UI_SHA256}'"

[ "$GATE_CONCLUSION" = "success" ] \
  || die "the stacks-ci gate concluded '${GATE_CONCLUSION}', refusing to write a manifest claiming it passed"

cat <<YAML
${TAG}:
  git_commit: ${GIT_COMMIT}
  built_at: ${BUILT_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}
  gate:
    check: stacks-ci gate
    run_id: ${GATE_RUN_ID}
    conclusion: ${GATE_CONCLUSION}
  artifacts:
    bff_image:
      repository: ${BFF_REPOSITORY}
      digest: ${BFF_DIGEST}
    ui_bundle:
      sha256: ${UI_SHA256}
      archive: ${UI_ARCHIVE}
      version_id: ${UI_VERSION_ID}
    registry_image:
      reference: ${REGISTRY_REFERENCE}
      digest: ${REGISTRY_DIGEST}
YAML
```

Then: `chmod +x stacks/apicurio/scripts/release-manifest.sh`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio && python3 -m pytest qe/tests/release/ -v`
Expected: PASS — all three test files, no failures.

- [ ] **Step 6: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/scripts/read-tfvar.sh stacks/apicurio/scripts/release-manifest.sh \
        stacks/apicurio/qe/tests/release/test_release_manifest_sh.py
git commit -m "UIFPE-36 #comment Assemble the release manifest, and refuse to write a false one

release-manifest.sh builds the YAML carried in the annotated tag. Its real job
is refusal: it will not record a mutable tag where a digest belongs, a
truncated or uppercase digest, or a gate that did not pass. A manifest that
lies is worse than no manifest, because it looks like evidence.

read-tfvar.sh pins the release to the registry image and digest Terraform will
actually apply, rather than to a second copy declared in the workflow. It
refuses to match a commented line or to prefix-match a longer variable name --
registry_image must never return registry_image_digest's value.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Runtime configuration for the UI

**Files:**
- Create: `stacks/apicurio/ui/public/config.json`
- Create: `stacks/apicurio/ui/src/services/config.ts`
- Create: `stacks/apicurio/ui/src/services/config.test.ts`
- Modify: `stacks/apicurio/ui/src/services/api-client.ts:3`
- Modify: `stacks/apicurio/ui/src/main.tsx:18-26`
- Modify: `stacks/apicurio/ui/src/test-setup.ts:1`
- Modify: `stacks/apicurio/ui/vite.config.ts:28-30`

**Interfaces:**
- Consumes: nothing.
- Produces: `src/services/config.ts` exporting `interface AppConfig { apiUrl: string }`, `loadConfig(): Promise<AppConfig>`, `getConfig(): AppConfig`, and `setConfigForTesting(config: AppConfig | null): void`. `api-client.ts` keeps exporting `apiClient` with an unchanged public shape, so no feature hook changes.

**Why:** `deploy-apicurio-dev.yml:711` compiles `VITE_API_URL` into the bundle from `terraform output -raw bff_api_url`. That makes the bundle environment-bound, so it cannot be promoted — colliding with the rule the delivery guide marks settled. (Spec decision 10.)

- [ ] **Step 1: Write the failing test**

Create `stacks/apicurio/ui/src/services/config.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getConfig, loadConfig, setConfigForTesting } from '@/services/config';

function mockFetch(response: Partial<Response> & { jsonBody?: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    json: async () => response.jsonBody,
  } as Response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('loadConfig', () => {
  beforeEach(() => {
    setConfigForTesting(null);
    vi.unstubAllGlobals();
  });

  it('fetches /config.json and returns the API origin', async () => {
    const fetchMock = mockFetch({ jsonBody: { apiUrl: 'https://api.example.com' } });
    const config = await loadConfig();
    expect(config.apiUrl).toBe('https://api.example.com');
    expect(fetchMock).toHaveBeenCalledWith('/config.json', { cache: 'no-store' });
  });

  it('requests config.json uncached, so a promotion is picked up', async () => {
    const fetchMock = mockFetch({ jsonBody: { apiUrl: 'https://api.example.com' } });
    await loadConfig();
    expect(fetchMock.mock.calls[0][1]).toEqual({ cache: 'no-store' });
  });

  it('strips a trailing slash, because api-client appends /api/v1', async () => {
    mockFetch({ jsonBody: { apiUrl: 'https://api.example.com/' } });
    expect((await loadConfig()).apiUrl).toBe('https://api.example.com');
  });

  it('throws when config.json is missing', async () => {
    mockFetch({ ok: false, status: 404, jsonBody: undefined });
    await expect(loadConfig()).rejects.toThrow(/config\.json/);
  });

  it('throws when apiUrl is absent, rather than defaulting silently', async () => {
    mockFetch({ jsonBody: {} });
    await expect(loadConfig()).rejects.toThrow(/apiUrl/);
  });

  it('throws when apiUrl is the empty string', async () => {
    // The old failure mode: an empty origin built clean and issued same-origin
    // requests that CloudFront answered with index.html.
    mockFetch({ jsonBody: { apiUrl: '' } });
    await expect(loadConfig()).rejects.toThrow(/apiUrl/);
  });
});

describe('getConfig', () => {
  beforeEach(() => setConfigForTesting(null));

  it('throws if called before loadConfig resolved', () => {
    expect(() => getConfig()).toThrow(/loadConfig/);
  });

  it('returns the loaded config afterwards', async () => {
    mockFetch({ jsonBody: { apiUrl: 'https://api.example.com' } });
    await loadConfig();
    expect(getConfig().apiUrl).toBe('https://api.example.com');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio/ui && npx vitest run src/services/config.test.ts`
Expected: FAIL — cannot resolve `@/services/config`.

- [ ] **Step 3: Write `stacks/apicurio/ui/src/services/config.ts`**

```ts
/**
 * Runtime configuration, fetched at boot rather than compiled in.
 *
 * The API origin used to arrive as VITE_API_URL, inlined by Vite at build time
 * from `terraform output -raw bff_api_url`. That made the bundle
 * environment-bound: it could not be promoted from dev to UAT, because dev's
 * API Gateway URL was baked into it. Fetching it instead makes the bundle
 * byte-identical everywhere, which is what gives it one content hash and makes
 * it a promotable artifact.
 *
 * config.json is written per environment at deploy time and is deliberately
 * excluded from the bundle hash -- see scripts/bundle-hash.sh.
 */

export interface AppConfig {
  apiUrl: string;
}

let config: AppConfig | null = null;

export async function loadConfig(): Promise<AppConfig> {
  // no-store, not the default: a promoted release writes a new config.json to
  // the same key, and a cached copy would point the UI at the previous
  // environment's API for as long as the browser held it.
  const response = await fetch('/config.json', { cache: 'no-store' });

  if (!response.ok) {
    throw new Error(
      `Could not load /config.json (HTTP ${response.status}). The deploy writes this ` +
        `file from terraform output bff_api_url; if it is missing, the UI deploy did not complete.`,
    );
  }

  const parsed = (await response.json()) as Partial<AppConfig>;

  // Explicitly reject the empty string as well as absence. An empty origin is
  // the old silent failure: it built clean, issued same-origin requests, and
  // CloudFront answered them with index.html.
  if (!parsed.apiUrl) {
    throw new Error('/config.json is missing apiUrl, so there is no API origin to call.');
  }

  config = { apiUrl: parsed.apiUrl.replace(/\/+$/, '') };
  return config;
}

export function getConfig(): AppConfig {
  if (!config) {
    throw new Error('getConfig() was called before loadConfig() resolved.');
  }
  return config;
}

/** Seed or clear the module state. Tests only — never called by app code. */
export function setConfigForTesting(next: AppConfig | null): void {
  config = next;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio/ui && npx vitest run src/services/config.test.ts`
Expected: PASS, 9 tests.

- [ ] **Step 5: Write `stacks/apicurio/ui/public/config.json`**

```json
{
  "apiUrl": "http://localhost:8000"
}
```

This is the local-development default. It ships into `dist/` on every build and the deploy overwrites it with the environment's real origin — which is why the bundle hash excludes it.

- [ ] **Step 6: Point `api-client.ts` at the runtime config**

Replace lines 1-11 of `stacks/apicurio/ui/src/services/api-client.ts` with:

```ts
import axios from 'axios';

import { getConfig } from '@/services/config';

export const apiClient = axios.create({
  timeout: 15_000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// baseURL is resolved per request rather than at module load, because the API
// origin now comes from /config.json (fetched at boot) instead of a
// VITE_API_URL compiled into the bundle. Setting it here keeps apiClient's
// public shape unchanged, so no feature hook has to know any of this happened.
apiClient.interceptors.request.use((request) => {
  request.baseURL = `${getConfig().apiUrl}/api/v1`;
  return request;
});
```

Leave the existing response interceptor and its comment exactly as they are.

- [ ] **Step 7: Bootstrap the config before rendering, in `main.tsx`**

Replace the `ReactDOM.createRoot(...)` call at the end of `stacks/apicurio/ui/src/main.tsx` with:

```tsx
const rootElement = document.getElementById('root')!;

function renderApp() {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </React.StrictMode>,
  );
}

// The config has to be in hand before the first request, so it is awaited
// before the first render rather than raced with it. Failing visibly here is
// deliberate: the previous behaviour was an empty API origin that rendered a
// working-looking dashboard and then showed an empty list, which reads as "no
// schemas" rather than "misconfigured deploy".
loadConfig()
  .then(renderApp)
  .catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    rootElement.textContent = `Configuration error: ${message}`;
  });
```

Add to the imports at the top of the file:

```tsx
import { loadConfig } from '@/services/config';
```

- [ ] **Step 8: Seed the config for the existing test suite**

`api-client.ts`'s request interceptor now calls `getConfig()`, which throws until `loadConfig()` resolves. Every existing test that issues a request through `apiClient` would fail. Seed it once, globally, in `stacks/apicurio/ui/src/test-setup.ts`:

```ts
import '@testing-library/jest-dom';

import { setConfigForTesting } from '@/services/config';

// The suite used to get its API origin from vitest's env.VITE_API_URL. That
// variable is gone, so the same value is seeded here instead -- one place,
// before any test module imports apiClient.
setConfigForTesting({ apiUrl: 'http://localhost:8000' });
```

- [ ] **Step 9: Remove the stale vitest env var**

In `stacks/apicurio/ui/vite.config.ts`, delete the `env` block from the `test` section (lines 28-30):

```ts
    env: {
      VITE_API_URL: 'http://localhost:8000',
    },
```

It configured a variable nothing reads any more; leaving it implies the build still consumes one.

- [ ] **Step 10: Verify no VITE_API_URL reference survives**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework && grep -rn "VITE_API_URL" stacks/apicurio --include=*.ts --include=*.tsx --include=*.json --include=*.yml | grep -v node_modules`
Expected: hits only in `.github/workflows/deploy-apicurio-dev.yml` (removed in Task 8) and nowhere under `ui/src`.

- [ ] **Step 11: Run the whole UI suite and the type-check**

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio/ui
npx vitest run
npx tsc -b && npx vite build
```
Expected: all tests pass (the pre-existing suite plus 9 new ones), type-check clean, build clean. If a component test fails because it asserted on `apiClient.defaults.baseURL`, update that assertion to read `getConfig().apiUrl` — the baseURL now lives on the request, not the instance.

- [ ] **Step 12: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/ui/public/config.json stacks/apicurio/ui/src/services/config.ts \
        stacks/apicurio/ui/src/services/config.test.ts \
        stacks/apicurio/ui/src/services/api-client.ts stacks/apicurio/ui/src/main.tsx \
        stacks/apicurio/ui/src/test-setup.ts stacks/apicurio/ui/vite.config.ts
git commit -m "UIFPE-36 #comment Fetch the API origin at runtime so the UI bundle is promotable

VITE_API_URL was compiled into the bundle from terraform output bff_api_url,
which made the bundle environment-bound: it could not be promoted from dev to
UAT because dev's API Gateway URL was baked in. That collides directly with the
rule the delivery guide marks settled -- build once, promote the artifact.

The origin now arrives in /config.json, fetched before the first render and
written per environment at deploy time. The bundle becomes byte-identical
across environments, which is what gives it one content hash. As a side effect
the UI build no longer needs AWS credentials or an already-deployed stack.

loadConfig() fails visibly rather than defaulting. The old behaviour was an
empty origin that built clean and issued same-origin requests CloudFront
answered with index.html -- a dashboard that looked fine and showed an empty
list, reading as 'no schemas' rather than 'misconfigured deploy'.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Pin the registry image by digest

**Files:**
- Modify: `stacks/apicurio/infrastructure/terraform/variables.tf` (add after the `registry_image` block, which ends around line 107)
- Modify: `stacks/apicurio/infrastructure/terraform/locals.tf` (add a local)
- Modify: `stacks/apicurio/infrastructure/terraform/ecs.tf:126`
- Modify: `stacks/apicurio/infrastructure/terraform/envs/dev.tfvars`

**Interfaces:**
- Consumes: nothing.
- Produces: variable `registry_image_digest` (no default, digest-validated) and `local.registry_image_ref`. Task 7 reads `registry_image` and `registry_image_digest` out of `envs/dev.tfvars` with `read-tfvar.sh`.

**Why:** `variables.tf:105` pins the registry to `quay.io/apicurio/apicurio-registry:3.3.2` — a **mutable** tag. Upstream can repoint it, so the image QE proved is not necessarily the image the next task pulls, and "what is this environment running" is unanswerable. (Spec, current-state table.)

- [ ] **Step 1: Add the variable to `variables.tf`**

Insert immediately after the closing brace of the existing `variable "registry_image"` block. Do **not** modify that block — it records what we mean, and why quay.io rather than Docker Hub.

```hcl
variable "registry_image_digest" {
  description = <<-DESC
    Digest of the Apicurio Registry image to run, as sha256:<64 hex>.

    `registry_image` above records WHAT WE MEAN -- 3.3.2, from quay.io, and the
    reasons for both. This records what ECS actually pulls. The two are not
    redundant: a tag is mutable, so upstream can repoint 3.3.2 at any time, and
    an environment pinned only to the tag cannot answer "which image is
    running" -- nor guarantee that the image QE proved is the image the next
    task pulls.

    NO DEFAULT, for the same reason bff_image_digest has none: an environment
    should have to say which artifact it runs, and be unable to answer
    "whatever upstream is serving today".

    Resolve it with:
      curl -sSI \
        -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
        https://quay.io/v2/apicurio/apicurio-registry/manifests/3.3.2 \
        | grep -i docker-content-digest
  DESC
  type        = string

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.registry_image_digest))
    error_message = "registry_image_digest must be a full digest of the form sha256:<64 lowercase hex chars>, not a tag."
  }
}
```

- [ ] **Step 2: Add the reference local to `locals.tf`**

Add inside the existing `locals` block, after the `registry_deletion_enabled` local:

```hcl
  # ECS must pull by digest, so registry_image's tag is stripped and the digest
  # appended: quay.io/apicurio/apicurio-registry:3.3.2 becomes
  # quay.io/apicurio/apicurio-registry@sha256:...
  #
  # The regex removes a trailing :<tag> only when that segment contains no "/",
  # so a registry host carrying a port (host:5000/image:tag) keeps its port and
  # loses only the tag.
  registry_image_ref = "${replace(var.registry_image, "/:[^:/]+$/", "")}@${var.registry_image_digest}"
```

- [ ] **Step 3: Point the task definition at it**

In `stacks/apicurio/infrastructure/terraform/ecs.tf`, change line 126 from:

```hcl
      image        = var.registry_image
```

to:

```hcl
      # Digest, not tag -- see local.registry_image_ref and
      # variable "registry_image_digest". var.registry_image keeps the
      # human-readable version for anyone reading this file.
      image        = local.registry_image_ref
```

- [ ] **Step 4: Set the value in `envs/dev.tfvars`**

Add:

```hcl
# quay.io/apicurio/apicurio-registry:3.3.2 as resolved on 2026-09-19. Bump this
# deliberately, together with the tag in registry_image, never on its own: the
# two disagreeing means the task runs an image the file does not name.
registry_image_digest = "sha256:e9476584866f9cbfd6b11c1fbdd0df64976277e069f54d537572032751a4b6fc"
```

If `registry_image` is not already set explicitly in this file, add it too, so `read-tfvar.sh` in Task 7 finds both in one place:

```hcl
registry_image = "quay.io/apicurio/apicurio-registry:3.3.2"
```

- [ ] **Step 5: Validate the configuration**

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio/infrastructure/terraform
terraform init -backend=false -input=false
terraform validate
tflint
```
Expected: `Success! The configuration is valid.` and no tflint findings. If `tflint` is not installed locally, skip it — `stacks-ci` runs it with the pinned `v0.64.0`.

- [ ] **Step 6: Confirm the digest resolves and the reference composes correctly**

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio/infrastructure/terraform
echo 'replace("quay.io/apicurio/apicurio-registry:3.3.2", "/:[^:/]+$/", "")' | terraform console
echo 'replace("host:5000/apicurio-registry:3.3.2", "/:[^:/]+$/", "")' | terraform console
```
Expected: `"quay.io/apicurio/apicurio-registry"` and `"host:5000/apicurio-registry"` — the second proves a registry port survives.

- [ ] **Step 7: Verify the live plan is a task-definition change only** (requires AWS credentials; if unavailable, record that this is unverified and let the deploy workflow's plan prove it)

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio/infrastructure/terraform
terraform init -reconfigure -input=false -backend-config=backends/dev.hcl
terraform plan -var-file=envs/dev.tfvars -out=tf.plan
terraform show -json tf.plan | jq -r '.resource_changes[] | select(.change.actions != ["no-op"]) | .address'
```
Expected: only `aws_ecs_task_definition.registry` and `aws_ecs_service.registry`. Anything else means the digest does not match the image the service is already running — investigate before applying.

- [ ] **Step 8: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/infrastructure/terraform/variables.tf \
        stacks/apicurio/infrastructure/terraform/locals.tf \
        stacks/apicurio/infrastructure/terraform/ecs.tf \
        stacks/apicurio/infrastructure/terraform/envs/dev.tfvars
git commit -m "UIFPE-36 #comment Pin the Apicurio Registry image by digest, not by a mutable tag

ecs.tf pulled quay.io/apicurio/apicurio-registry:3.3.2 -- a tag upstream can
repoint at any time. So the image QE proved was not necessarily the image the
next task pulled, and 'what is this environment running' had no answer even for
the off-the-shelf half of the stack.

registry_image keeps the tag and its reasoning, because that records what we
mean. The new registry_image_digest records what ECS actually pulls, with no
default, for the reason bff_image_digest has none: an environment should have
to say which artifact it runs.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The release-artifacts bucket

**Files:**
- Create: `stacks/apicurio/infrastructure/terraform/release-artifacts.tf`
- Modify: `stacks/apicurio/infrastructure/terraform/outputs.tf` (append)

**Interfaces:**
- Consumes: `local.name` (`uif-apicurio-${var.env}`) from `locals.tf`.
- Produces: bucket `uif-apicurio-<env>-release-artifacts` and output `release_artifacts_bucket`. Task 7 writes `<sha256>.tar.gz` into it.

**No IAM change is required.** Verified 2026-09-19: `capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json` statement `UiBucket` already covers `arn:aws:s3:::uif-apicurio-dev-*` and `UiBucketObjects` covers `arn:aws:s3:::uif-apicurio-dev-*/*`, including `CreateBucket`, versioning, encryption, tagging, public-access-block, `PutObject` and `GetObject`. Do not widen the policy. **Note for Half 2:** `s3:GetObjectVersion` is *not* granted, and rollback-by-version will need it.

- [ ] **Step 1: Create `release-artifacts.tf`**

```hcl
# Release artifacts — the immutable, retrievable copy of each release's UI
# bundle, keyed by its content hash.
#
# WHY THIS IS NOT THE UI BUCKET (frontend.tf). Reusing that bucket has two
# bugs, not one preference:
#
#   1. deploy-apicurio-dev.yml syncs dist/ to that bucket's ROOT with --delete,
#      so anything stored alongside the bundle is removed on the next UI
#      deploy. An archive kept there would survive exactly until someone
#      shipped a UI change.
#   2. That bucket is a public CloudFront origin, so release evidence would be
#      downloadable by anyone who guessed the key.
#
# WHY THE KEYS ARE CONTENT HASHES. <sha256>.tar.gz is a content address, not a
# version token, so .claude/rules/naming/00-core.md's ban on version tokens is
# not engaged -- and neither is ADR-0019's open question about whether a
# versioned S3 prefix (jobs/v1.4.3/) is compatible with that rule. Echo has to
# resolve that question; Apicurio does not.
#
# No lifecycle rule, deliberately. These objects are what a rollback restores,
# so an expiry would quietly set a horizon on how far back a release can be
# rolled. Add one when there is a retention requirement to encode, not before.

resource "aws_s3_bucket" "release_artifacts" {
  # uif-{stack}-{env}-{purpose}, same as every other name in this stack.
  # 38 characters at the longest env, well inside the 63-char limit, and
  # globally unique because the stack token is.
  bucket = "${local.name}-release-artifacts"

  # NOT force_destroy. Unlike the UI bucket, whose contents are a rebuildable
  # bundle, these objects are release evidence -- a destroy that silently took
  # them with it would remove the thing a rollback needs.
  force_destroy = false

  tags = { component = "release" }
}

resource "aws_s3_bucket_public_access_block" "release_artifacts" {
  bucket                  = aws_s3_bucket.release_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning on, so an accidental overwrite of a content-addressed key is
# recoverable and the manifest can record a version_id alongside the hash.
resource "aws_s3_bucket_versioning" "release_artifacts" {
  bucket = aws_s3_bucket.release_artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 rather than a CMK. These archives contain exactly the bytes CloudFront
# already serves to every browser, so there is no confidentiality requirement a
# customer-managed key would protect -- it would add a key policy and a
# rotation obligation for no gain. The bucket is private because release
# evidence should not be enumerable, not because the bytes are secret.
# trivy:ignore:AVD-AWS-0132 See the comment above.
resource "aws_s3_bucket_server_side_encryption_configuration" "release_artifacts" {
  bucket = aws_s3_bucket.release_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
```

- [ ] **Step 2: Add the output**

Append to `stacks/apicurio/infrastructure/terraform/outputs.tf`:

```hcl
output "release_artifacts_bucket" {
  description = "Bucket holding each release's UI bundle archive, keyed by content hash."
  value       = aws_s3_bucket.release_artifacts.id
}
```

- [ ] **Step 3: Validate**

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework/stacks/apicurio/infrastructure/terraform
terraform init -backend=false -input=false
terraform validate
tflint
```
Expected: valid, no findings.

- [ ] **Step 4: Confirm the checkov/trivy gate is satisfied**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework && bash stacks/apicurio/qe/ci.sh`
Expected: the Terraform security checks pass. If a finding appears for this bucket that the `trivy:ignore` does not cover (for example a logging or lifecycle rule), **do not add a blanket suppression** — report the finding and the justification for review.

- [ ] **Step 5: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/infrastructure/terraform/release-artifacts.tf \
        stacks/apicurio/infrastructure/terraform/outputs.tf
git commit -m "UIFPE-36 #comment Add a private bucket for release artifacts

Rollback under the release model means repointing a manifest entry, which only
works if the artifact that entry names can still be fetched. This is where the
UI bundle archive lives, keyed by its content hash.

Deliberately not the UI bucket: that one is synced at its root with --delete,
so an archive beside the bundle would be deleted by the next UI deploy, and it
is a public CloudFront origin, so release evidence would be downloadable by
anyone who guessed the key.

No lifecycle rule and no force_destroy: an expiry would quietly set a horizon
on how far back a release can be rolled back. No IAM change needed -- the
deploy role's S3 grant is already scoped to uif-apicurio-dev-*.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `release-apicurio.yml`

**Files:**
- Create: `.github/workflows/release-apicurio.yml`

**Interfaces:**
- Consumes: `stacks/apicurio/scripts/check-version.sh` (Task 1), `bundle-hash.sh` (Task 2), `read-tfvar.sh` and `release-manifest.sh` (Task 3), `output release_artifacts_bucket`'s bucket (Task 6).
- Produces: the annotated tag `apicurio/vX.Y.Z` and a GitHub Release of the same name whose body is the manifest.

**Prerequisite:** Tasks 5 and 6 must have been **applied** to dev before the first release run, because the release writes the archive into a bucket Terraform creates.

- [ ] **Step 1: Create the workflow**

```yaml
name: Release apicurio

# Cuts the build tag apicurio/vX.Y.Z when stacks/apicurio/VERSION changes on
# develop. Design: docs/superpowers/specs/2026-09-19-apicurio-release-identity-design.md
#
# WHY THIS POLLS FOR THE GATE INSTEAD OF USING workflow_run. A workflow_run
# trigger only fires for workflow files that live on the DEFAULT branch, and
# main is 956 commits behind develop with 2 of 28 workflows on it. That is not
# hypothetical: domain-doc-sync.yml uses workflow_run, is absent from main, and
# GitHub answers `gh run list` for it with "workflow not found on the default
# branch" -- it has never run. Polling is correct here for as long as main
# stays behind.
#
# WHY A BUILD TAG'S EVIDENCE IS THE CI GATE AND NOT SMOKE. The two tags in the
# delivery guide answer two different questions. A build tag answers "what code
# went into this artifact", so its evidence is the four CI checks. A release
# tag answers "what was deployed, where, when", so its evidence is smoke and
# SLO. Deploying is Half 2's job, not this workflow's.

on:
  push:
    branches: [develop]
    paths:
      - "stacks/apicurio/VERSION"
      # This file itself, so a change to the release path is exercised by it.
      - ".github/workflows/release-apicurio.yml"
  workflow_dispatch:

concurrency:
  group: release-apicurio
  cancel-in-progress: false

permissions:
  contents: read

env:
  ENV: dev
  AWS_REGION: eu-west-1
  COMPONENT: apicurio
  # Hardcoded for the same reason deploy-apicurio-dev.yml hardcodes it: the
  # name is built from local.name in bff.tf, and reading it back from
  # `terraform output` would mean an init against remote state purely to learn
  # a string this file can state. If it changes in bff.tf, change it here.
  ECR_REPO: uif-apicurio-dev-bff-ecr-repo
  TFVARS: stacks/apicurio/infrastructure/terraform/envs/dev.tfvars

jobs:
  validate:
    name: Validate the declared version
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.check.outputs.tag }}
      version: ${{ steps.check.outputs.version }}
    steps:
      - uses: actions/checkout@v4
        with:
          # Full history and tags: check-version.sh compares against every
          # existing apicurio/v* tag, and a shallow clone has none of them.
          fetch-depth: 0

      - name: Check VERSION
        id: check
        run: |
          set -euo pipefail
          tag="$(stacks/apicurio/scripts/check-version.sh stacks/apicurio/VERSION)"
          echo "tag=${tag}" >> "$GITHUB_OUTPUT"
          echo "version=${tag#"${COMPONENT}/v"}" >> "$GITHUB_OUTPUT"
          echo "::notice::Releasing ${tag}"

  gate:
    name: Require a green stacks-ci gate on this commit
    needs: [validate]
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read
    outputs:
      run_id: ${{ steps.poll.outputs.run_id }}
      conclusion: ${{ steps.poll.outputs.conclusion }}
    steps:
      - name: Wait for the stacks-ci gate to conclude
        id: poll
        env:
          GH_TOKEN: ${{ github.token }}
          SHA: ${{ github.sha }}
        run: |
          set -euo pipefail
          deadline=$(( $(date +%s) + 1800 ))
          run_id=""

          while :; do
            if [ -z "$run_id" ]; then
              run_id="$(gh api \
                "repos/${GITHUB_REPOSITORY}/actions/workflows/stacks-ci.yml/runs?head_sha=${SHA}" \
                --jq '[.workflow_runs[]] | sort_by(.created_at) | last | .id // empty' || true)"
            fi

            if [ -n "$run_id" ]; then
              # The gate JOB, not the run's overall conclusion: validate-sif is
              # continue-on-error, so a run can conclude success while the
              # aggregating gate job is what actually decides.
              row="$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}/jobs" \
                --jq '[.jobs[] | select(.name == "stacks-ci gate")] | last // {}
                      | [(.status // ""), (.conclusion // "")] | @tsv' || true)"
              status="$(printf '%s' "$row" | cut -f1)"
              conclusion="$(printf '%s' "$row" | cut -f2)"

              if [ "$status" = "completed" ]; then
                if [ "$conclusion" != "success" ]; then
                  echo "::error::stacks-ci gate concluded '${conclusion}' on ${SHA} (run ${run_id}). A build tag is evidence the gate passed, so no tag will be created."
                  exit 1
                fi
                echo "run_id=${run_id}"         >> "$GITHUB_OUTPUT"
                echo "conclusion=${conclusion}" >> "$GITHUB_OUTPUT"
                echo "::notice::stacks-ci gate passed on ${SHA} (run ${run_id})."
                exit 0
              fi
              echo "gate is '${status:-queued}' in run ${run_id}; waiting."
            else
              echo "no stacks-ci run for ${SHA} yet; waiting."
            fi

            if [ "$(date +%s)" -ge "$deadline" ]; then
              echo "::error::Timed out after 30 minutes waiting for the stacks-ci gate on ${SHA}. If stacks-ci did not run for this commit, check its path filters cover the files this commit touched."
              exit 1
            fi
            sleep 20
          done

  artifacts:
    name: Resolve the three immutable artifact references
    needs: [validate, gate]
    runs-on: ubuntu-latest
    environment: dev
    permissions:
      id-token: write
      contents: read
    outputs:
      bff_repository: ${{ steps.bff.outputs.repository }}
      bff_digest: ${{ steps.bff.outputs.digest }}
      ui_sha256: ${{ steps.ui.outputs.sha256 }}
      ui_archive: ${{ steps.ui.outputs.archive }}
      ui_version_id: ${{ steps.ui.outputs.version_id }}
      registry_reference: ${{ steps.registry.outputs.reference }}
      registry_digest: ${{ steps.registry.outputs.digest }}
    steps:
      - uses: actions/checkout@v4
        with:
          # rev-list walks back to the last commit that touched src/, so the
          # full history is required.
          fetch-depth: 0

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN_DEV }}
          aws-region: ${{ env.AWS_REGION }}

      # Nothing is built here. The image this release runs is the one built
      # from the most recent commit that changed src/ -- which is this commit
      # when the release rides its own change, and an earlier one when the
      # release is a UI or Terraform change. Recording that digest is correct
      # either way: it is the image the release actually runs.
      - name: Resolve the BFF image digest
        id: bff
        run: |
          set -euo pipefail
          account="$(aws sts get-caller-identity --query Account --output text)"
          repository="${account}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

          digest=""
          found=""
          for sha in $(git rev-list -n 50 HEAD -- stacks/apicurio/src); do
            if digest="$(aws ecr describe-images \
                  --repository-name "$ECR_REPO" \
                  --image-ids "imageTag=${sha}" \
                  --query 'imageDetails[0].imageDigest' \
                  --output text 2>/dev/null)"; then
              if [ -n "$digest" ] && [ "$digest" != "None" ]; then
                found="$sha"
                break
              fi
            fi
          done

          if [ -z "$found" ]; then
            echo "::error::No ECR image found for any of the last 50 commits that touched stacks/apicurio/src. Run deploy-apicurio-dev.yml with bff_image=build first, then re-run this release."
            exit 1
          fi

          echo "repository=${repository}" >> "$GITHUB_OUTPUT"
          echo "digest=${digest}"         >> "$GITHUB_OUTPUT"
          echo "::notice::BFF image ${digest} (built from ${found})"

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: stacks/apicurio/ui/package-lock.json

      # No VITE_API_URL and no Terraform read: since the API origin arrives at
      # runtime in config.json, this build needs neither AWS nor an existing
      # deployment. That is what makes the bundle one promotable artifact
      # rather than one per environment.
      - name: Build, hash and archive the UI bundle
        id: ui
        working-directory: stacks/apicurio/ui
        run: |
          set -euo pipefail
          npm ci
          npm run build

          sha256="$(../scripts/bundle-hash.sh dist)"

          # config.json is excluded from the archive as well as from the hash:
          # it is environment state, and a promotion writes the target
          # environment's own copy.
          tar --exclude=./config.json -czf "/tmp/${sha256}.tar.gz" -C dist .

          bucket="uif-apicurio-${ENV}-release-artifacts"
          version_id="$(aws s3api put-object \
            --bucket "$bucket" \
            --key "${sha256}.tar.gz" \
            --body "/tmp/${sha256}.tar.gz" \
            --query VersionId --output text)"

          echo "sha256=${sha256}"                              >> "$GITHUB_OUTPUT"
          echo "archive=s3://${bucket}/${sha256}.tar.gz"        >> "$GITHUB_OUTPUT"
          echo "version_id=${version_id}"                       >> "$GITHUB_OUTPUT"
          echo "::notice::UI bundle ${sha256} archived to s3://${bucket}/${sha256}.tar.gz"

      - name: Resolve the registry image digest and check for upstream drift
        id: registry
        run: |
          set -euo pipefail
          reference="$(stacks/apicurio/scripts/read-tfvar.sh "$TFVARS" registry_image)"
          pinned="$(stacks/apicurio/scripts/read-tfvar.sh "$TFVARS" registry_image_digest)"

          # Re-resolve the tag upstream. A mismatch means whoever publishes
          # this image repointed the tag after we pinned it -- worth saying
          # loudly, because the image we tested is then not the image the tag
          # names. NOT a failure: upstream moving a tag should make a human
          # decide to bump, not wedge our releases.
          repo="${reference%:*}"
          version="${reference##*:}"
          path="${repo#*/}"
          live="$(curl -sSI \
            -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
            "https://quay.io/v2/${path}/manifests/${version}" \
            | tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" { print $2 }')"

          if [ -n "$live" ] && [ "$live" != "$pinned" ]; then
            echo "::warning::${reference} now resolves to ${live} upstream, but this stack is pinned to ${pinned}. The pin is what runs; bump registry_image_digest in ${TFVARS} deliberately if the new image is wanted."
          fi

          echo "reference=${reference}" >> "$GITHUB_OUTPUT"
          echo "digest=${pinned}"       >> "$GITHUB_OUTPUT"

  tag:
    name: Cut the build tag
    needs: [validate, gate, artifacts]
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Assemble the release manifest
        env:
          TAG: ${{ needs.validate.outputs.tag }}
          GIT_COMMIT: ${{ github.sha }}
          GATE_RUN_ID: ${{ needs.gate.outputs.run_id }}
          GATE_CONCLUSION: ${{ needs.gate.outputs.conclusion }}
          BFF_REPOSITORY: ${{ needs.artifacts.outputs.bff_repository }}
          BFF_DIGEST: ${{ needs.artifacts.outputs.bff_digest }}
          UI_SHA256: ${{ needs.artifacts.outputs.ui_sha256 }}
          UI_ARCHIVE: ${{ needs.artifacts.outputs.ui_archive }}
          UI_VERSION_ID: ${{ needs.artifacts.outputs.ui_version_id }}
          REGISTRY_REFERENCE: ${{ needs.artifacts.outputs.registry_reference }}
          REGISTRY_DIGEST: ${{ needs.artifacts.outputs.registry_digest }}
        run: |
          set -euo pipefail
          stacks/apicurio/scripts/release-manifest.sh > /tmp/manifest.yaml
          cat /tmp/manifest.yaml

      - name: Create and push the annotated tag
        env:
          TAG: ${{ needs.validate.outputs.tag }}
        run: |
          set -euo pipefail
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          # Annotated, with the manifest as the message: the tag and its
          # evidence are then one object, readable offline with
          # `git show apicurio/vX.Y.Z` and impossible to separate.
          git tag -a "$TAG" -F /tmp/manifest.yaml
          git push origin "refs/tags/${TAG}"
          echo "::notice::Tagged ${TAG}"

      - name: Mirror it to a GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ needs.validate.outputs.tag }}
        run: |
          set -euo pipefail
          # gh, not a third-party release action: only actions, github,
          # hashicorp, aws-actions, aquasecurity, sonarsource, opentofu and
          # pearson* pass the enterprise allow-list, and one disallowed uses:
          # fails the whole workflow at startup with no job logs.
          gh release create "$TAG" \
            --title "$TAG" \
            --notes-file /tmp/manifest.yaml \
            --target "${GITHUB_SHA}"
          echo "::notice::Release ${TAG} published."
```

- [ ] **Step 2: Lint the workflow the way CI will**

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
actionlint -shellcheck= -pyflakes= .github/workflows/release-apicurio.yml
```
Expected: no output. If `actionlint` is not installed locally, push the branch and let `workflow-lint` run — but read its result before merging, because a disallowed `uses:` or a YAML error fails the whole workflow at startup with no job logs.

- [ ] **Step 3: Confirm every `uses:` is on the allow-list**

Run: `grep -n "uses:" .github/workflows/release-apicurio.yml`
Expected: only `actions/checkout@v4`, `actions/setup-node@v4`, `aws-actions/configure-aws-credentials@v4`. Any other owner will fail the whole workflow at startup.

- [ ] **Step 4: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add .github/workflows/release-apicurio.yml
git commit -m "UIFPE-36 #comment Cut apicurio/vX.Y.Z with a manifest when VERSION changes

Four jobs: validate the declared version, require a green stacks-ci gate on
this exact commit, resolve the three immutable artifact references, then create
an annotated tag whose message IS the manifest and mirror it to a GitHub
Release.

It polls for the gate rather than using workflow_run, because that trigger only
fires for workflows on the default branch and main is 956 commits behind with 2
of 28 workflows. domain-doc-sync.yml is the proof: it uses workflow_run, is
absent from main, and has never run.

Nothing is built here. The BFF digest is resolved by walking back to the most
recent commit that touched src/, which is correct whether the release rides its
own BFF change or is a UI-only one -- either way it records the image the
release actually runs. The UI bundle is built without AWS credentials and
without an existing deployment, which is only possible now the API origin
arrives at runtime.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Write `config.json` at deploy time, and report the deployed tag

**Files:**
- Modify: `.github/workflows/deploy-apicurio-dev.yml:761-772` (the stale VITE_API_URL guard comment)
- Modify: `.github/workflows/deploy-apicurio-dev.yml:794-801` (the Build step)
- Modify: `.github/workflows/deploy-apicurio-dev.yml:803-818` (the sync step)
- Modify: `.github/workflows/deploy-apicurio-dev.yml:820-846` (the invalidation step)
- Modify: `.github/workflows/deploy-apicurio-dev.yml` smoke job (from line 873)
- Modify: `stacks/apicurio/infrastructure/terraform/frontend.tf:6-8` (stale header comment)

**Interfaces:**
- Consumes: `bundle-hash.sh` is *not* needed here — the deploy does not re-hash. It only writes `config.json`.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Replace the Build step's inlined variable**

Change lines 794-801 from the `env:`-carrying version to:

```yaml
      - name: Build
        working-directory: stacks/apicurio/ui
        # No VITE_API_URL. The API origin is no longer compiled in -- it is
        # written to dist/config.json below and fetched by the browser at boot,
        # which is what makes one bundle promotable across environments.
        run: npm run build
```

- [ ] **Step 2: Write `config.json` into `dist/` before the sync**

In the "Sync to the UI bucket" step, insert immediately after `set -euo pipefail` and before the existing comment block:

```bash
          # The API origin, written per environment rather than compiled in.
          # It goes INTO dist/ before the sync rather than being copied
          # afterwards, so there is no window in which the bucket serves the
          # localhost default that ui/public/config.json carries for local
          # development.
          #
          # This is also the guard that used to sit in "Read the stack's
          # outputs": an empty origin used to build clean, issue same-origin
          # requests, and have CloudFront answer them with index.html.
          if [ -z "${UI_API_URL:-}" ]; then
            echo "::error::bff_api_url is empty, so the UI has no API origin to call. Run this workflow with infra=apply first."
            exit 1
          fi
          printf '{\n  "apiUrl": "%s"\n}\n' "$UI_API_URL" > dist/config.json
```

Then, after the existing `aws s3 cp dist/index.html ...` lines, add:

```bash
          # config.json is unhashed and its name never changes, so it is the
          # other document a browser can hold across a deploy -- and a stale
          # copy points the UI at the previous environment's API.
          aws s3 cp dist/config.json "s3://$UI_BUCKET/config.json" \
            --cache-control 'no-cache, must-revalidate'
```

- [ ] **Step 3: Add `/config.json` to the CloudFront invalidation**

In the "Invalidate the entry document on CloudFront" step, change the command to:

```bash
          aws cloudfront create-invalidation \
            --distribution-id "$UI_DISTRIBUTION_ID" \
            --paths '/' '/index.html' '/config.json'
```

And update that step's "HONEST GAP" paragraph, which currently says unhashed files from `ui/public/` are not invalidated. `config.json` is now exactly such a file and **is** invalidated, so the paragraph must say so:

```bash
          # HONEST GAP: unhashed files copied from ui/public/ are NOT
          # invalidated and will serve stale until their TTL expires — today
          # that is just pearson-logo.png. config.json is the one exception: it
          # is unhashed, environment-specific, and invalidated above, because a
          # stale copy points the UI at the previous environment's API rather
          # than merely showing an old logo.
```

- [ ] **Step 4: Fix the now-stale guard comment**

Lines 761-772 explain a `VITE_API_URL` failure mode that no longer exists, and the `missing` check still requires `bff_api_url`. Keep the check — the deploy genuinely needs the origin to write `config.json` — but replace the comment with:

```bash
          # All three outputs are still required, but for a different reason
          # than before: bff_api_url is no longer compiled into the bundle, it
          # is written to dist/config.json at publish time. Catching an empty
          # value here keeps the failure at the step that can explain it.
```

- [ ] **Step 5: Report the deployed build tag from the smoke job**

Add as the final step of the `smoke` job:

```yaml
      - name: Report which build tag this deployed
        if: always()
        run: |
          set -euo pipefail
          # Seed for Half 2: when releases/<env>.yaml exists it can be
          # generated from data already emitted here. "+N commits" meanwhile is
          # an honest signal that dev is ahead of its last release.
          described="$(git describe --tags --match 'apicurio/v*' --always 2>/dev/null || echo "")"
          if [ -z "$described" ]; then
            echo "::notice::No apicurio/v* tag is reachable from ${GITHUB_SHA}. Nothing has been released yet -- bump stacks/apicurio/VERSION to cut the first one."
          else
            echo "::notice::Deployed ${described}"
          fi
```

The `smoke` job's `actions/checkout@v4` needs `fetch-depth: 0` for `git describe` to see tags. Add it if it is not already there.

- [ ] **Step 6: Fix the stale Terraform header comment**

In `stacks/apicurio/infrastructure/terraform/frontend.tf`, replace lines 6-8:

```hcl
# The bundle no longer carries the API origin: it is written to config.json at
# publish time and fetched by the browser at boot, so one bundle is valid in
# every environment. CloudFront never proxies the API — the browser calls API
# Gateway directly, which is why the authorizer's IP-allowlist path is what
# credentials the UI.
```

- [ ] **Step 7: Lint both workflows**

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
actionlint -shellcheck= -pyflakes= .github/workflows/deploy-apicurio-dev.yml .github/workflows/release-apicurio.yml
cd stacks/apicurio/infrastructure/terraform && terraform init -backend=false -input=false && terraform validate
```
Expected: no actionlint output; Terraform valid.

- [ ] **Step 8: Verify no reference to the removed variable survives**

Run: `cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework && grep -rn "VITE_API_URL" stacks/apicurio .github/workflows | grep -v node_modules`
Expected: **no hits at all.**

- [ ] **Step 9: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add .github/workflows/deploy-apicurio-dev.yml \
        stacks/apicurio/infrastructure/terraform/frontend.tf
git commit -m "UIFPE-36 #comment Publish the API origin as config.json instead of compiling it in

The UI job no longer passes VITE_API_URL to the build. It writes
dist/config.json from terraform output bff_api_url just before the sync, so
there is no window in which the bucket serves the localhost default that
ui/public/config.json carries for local development.

config.json joins index.html in getting no-cache and in the CloudFront
invalidation. That matters more than it does for index.html: the invalidation
step's own HONEST GAP note says unhashed files from ui/public/ serve stale
until TTL, which is tolerable for a logo and not for a file that names which
API the UI calls.

The smoke job now reports the build tag reachable from the deployed commit, so
'deployed apicurio/v0.1.0 (+3 commits)' says both what was released and that
dev has moved since. frontend.tf's header no longer claims the API URL is
compiled into the bundle.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification before the pull request

- [ ] `cd stacks/apicurio && python3 -m pytest qe/tests/release/ -v` — all script tests pass
- [ ] `cd stacks/apicurio/ui && npx vitest run && npx tsc -b && npx vite build` — UI suite, types and build clean
- [ ] `bash stacks/apicurio/qe/ci.sh` — the stack's own gate passes locally
- [ ] `actionlint -shellcheck= -pyflakes= .github/workflows/*.yml` — clean
- [ ] `grep -rn "VITE_API_URL" stacks/apicurio .github/workflows | grep -v node_modules` — no hits
- [ ] Open the PR against `develop`, and **wait for `stacks-ci gate` and `workflow-lint` to be green before merging.** Branch protection does not enforce this until 2026-09-28, so a red gate will not block the merge — that is exactly how a 39-run outage survived three days.

## First release, after merge

1. Run `deploy-apicurio-dev.yml` with `infra=apply` so the release-artifacts bucket and the digest-pinned registry task definition exist.
2. Run it again with `ui=deploy` to publish the first `config.json`, then load the UI and confirm the dashboard populates — this is the check that the runtime-config change works end to end.
3. Open a PR bumping `stacks/apicurio/VERSION`, merge it, and confirm `release-apicurio.yml` creates `apicurio/v0.1.0`.
4. Read the manifest back with `git show apicurio/v0.1.0` and confirm all three artifact references are present and digest-shaped.

---

## Self-Review

**Spec coverage.** Every Half 1 element of the spec maps to a task: decision 1 and the `VERSION` file → Task 1; decisions 2 and 3 (component name, bump-triggered tags) → Tasks 1 and 7; decision 4 (bump with the change) → documented in the plan's Global Constraints and Task 7's trigger; decision 5 (manifest in the tag) → Tasks 3 and 7; decision 6 (no Terraform row) → Task 3's schema; decision 8 (gate evidence) → Task 7's `gate` job and `release-manifest.sh`'s refusal; decision 9 (fix both soft references) → Tasks 5 and 6; decision 10 (runtime config) → Task 4. The spec's "repository settings" item (tag protection on `apicurio/*`) is deliberately **not** a task — it is a settings change for the owner, grouped with the 2026-09-28 branch-protection work, and noted below.

**Known gaps, stated rather than hidden.**
- **Tag protection is not in this plan.** Until it exists, anyone with write access can move `apicurio/v0.1.0`, so the manifest's immutability is convention. Raise it with the branch-protection change.
- **Half 2 is not in this plan** and is blocked on four things outside the stack — see the spec.
- **`s3:GetObjectVersion` is not granted** to the deploy role. Not needed by Half 1; needed by Half 2's rollback.

**Type and name consistency.** `check-version.sh` prints `apicurio/v0.1.0`, which Task 7's `validate` job stores as `tag` and `release-manifest.sh` consumes as `TAG` — one spelling throughout. `bundle-hash.sh` prints a **bare** 64-hex string with no `sha256:` prefix, which is what `release-manifest.sh` validates with `^[0-9a-f]{64}$` and what Task 7 uses as both the archive key and the manifest's `ui_sha256`. The two image digests carry the `sha256:` prefix and are validated with `^sha256:[0-9a-f]{64}$`. `setConfigForTesting` is the same name in `config.ts`, `config.test.ts` and `test-setup.ts`.
