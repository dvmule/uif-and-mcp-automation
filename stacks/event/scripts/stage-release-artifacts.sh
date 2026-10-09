#!/usr/bin/env bash
# Stage every event-stack release artifact: hash it, upload it at its
# content-addressed key, and emit the files the rest of the pipeline consumes.
#
# Ported from stacks/echo/scripts/stage-release-artifacts.sh and cut down to event's
# set: SIX artifacts, all built from src/, none vendored.
#
#   lambdas       pipeline_controller  dlq_recorder
#                 subscription_provisioner  event_redrive
#   glue_scripts  event_fabric_processor  event_fabric_recovery
#
# WHY THIS IS ONE SCRIPT AND NOT TWO COPIES. release-event.yml (cutting a build
# tag) and deploy-event-dev.yml (a dev apply) both need the identical artifact
# set, hashes and keys. Written twice they drift, and the failure is invisible:
# a Lambda whose source list is stale in one copy ships without a module it
# imports and fails at RUNTIME, not at apply. The set is defined HERE, once, and
# qe/test_release_artifacts.py asserts it matches the Terraform that reads it.
#
# Emits:
#   --tsv <path>     one row per artifact, for release-manifest.sh
#   --tfvars <path>  artifact_shas + lambda_code_hashes, for terraform
# At least one is required.
#
# --hash-only computes every hash and key and writes --tfvars WITHOUT touching
# S3. It exists for ONE case: a dev `plan` before the release bucket has been
# created, so the plan can be read before anything is applied. It refuses
# --tsv, because a manifest row without an uploaded object and its version id
# would be evidence of nothing.
#
# Requires: aws (unless --hash-only), and a python that can import zipfile.
#
# Usage:
#   stage-release-artifacts.sh --bucket <release-bucket> [--tsv out.tsv] [--tfvars out.json]
#   stage-release-artifacts.sh --hash-only --tfvars out.json

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STACK_ROOT="$(cd "$HERE/.." && pwd)"

die() { printf 'stage-release-artifacts.sh: %s\n' "$1" >&2; exit 1; }
note() { printf '  %s\n' "$1" >&2; }

# The number of rows this script must produce. qe/test_release_artifacts.py
# derives it from the calls below, so the two cannot disagree silently.
EXPECTED_ROWS=6

BUCKET="" TSV="" TFVARS="" HASH_ONLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bucket)    [ "$#" -ge 2 ] || die "--bucket needs a value"; BUCKET="$2"; shift 2 ;;
    --tsv)       [ "$#" -ge 2 ] || die "--tsv needs a value";    TSV="$2";    shift 2 ;;
    --tfvars)    [ "$#" -ge 2 ] || die "--tfvars needs a value"; TFVARS="$2"; shift 2 ;;
    --hash-only) HASH_ONLY=1; shift ;;
    *) die "unknown flag '$1'" ;;
  esac
done

[ -n "$TSV" ] || [ -n "$TFVARS" ] || die "give --tsv, --tfvars, or both; otherwise this reports nothing"
if [ "$HASH_ONLY" -eq 1 ]; then
  [ -z "$TSV" ] || die "--hash-only cannot write --tsv: a manifest row needs an uploaded object and its version id"
  [ -z "$BUCKET" ] || die "--hash-only touches no bucket; drop --bucket"
else
  [ -n "$BUCKET" ] || die "--bucket is required (the release-artifacts bucket), or pass --hash-only"
fi

HASH="${HERE}/artifact-hash.sh"
ZIP="${HERE}/zip-artifact.sh"
[ -f "$HASH" ] || die "artifact-hash.sh not found at ${HASH}"
[ -f "$ZIP" ]  || die "zip-artifact.sh not found at ${ZIP}"

WORK="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/stage-artifacts-$$")"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

ROWS="$WORK/rows.tsv"
: > "$ROWS"

# Upload only if the key is absent. Content-addressed, so an object already at
# this key IS this artifact. The version id is read back either way, so what is
# recorded is the object actually there. Echo's put(), unchanged.
put() {
  local file="$1" key="$2" vid
  if [ "$HASH_ONLY" -eq 1 ]; then
    printf -- '-\n'
    return 0
  fi
  if aws s3api head-object --bucket "$BUCKET" --key "$key" >/dev/null 2>&1; then
    note "= ${key}"
  else
    aws s3 cp "$file" "s3://${BUCKET}/${key}" --only-show-errors \
      || die "upload of ${key} failed (${file}). Nothing was recorded for it; re-run, artifacts already present are skipped."
    note "+ ${key}"
  fi
  vid="$(aws s3api head-object --bucket "$BUCKET" --key "$key" --query VersionId --output text 2>/dev/null)" \
    || die "${key} is not in s3://${BUCKET} after the upload reported success. The object did not land."
  [ -n "$vid" ] && [ "$vid" != "None" ] \
    || die "${key} is present in s3://${BUCKET} but carries no version id, so versioning is off on that bucket and a release cannot record which object it shipped."
  printf '%s\n' "$vid"
}

# ── Glue scripts ─────────────────────────────────────────────────────────────
# Keyed glue/<sha>/<basename>: the job's script_location names the file, and
# the directory is the content hash, so a new script is a new key.
glue_script() {
  local name="$1" src="$2" base sha key vid
  base="$(basename "$src")"
  [ -f "$STACK_ROOT/$src" ] || die "glue script ${src} does not exist"
  sha="$(bash "$HASH" "$STACK_ROOT/$src")"
  key="glue/${sha}/${base}"
  vid="$(put "$STACK_ROOT/$src" "$key")"
  printf 'glue_scripts\t%s\t%s\t%s\t%s\t%s\t-\t-\n' "$name" "$src" "$sha" "$key" "$vid" >> "$ROWS"
}

glue_script event_fabric_processor src/glue/event_fabric_processor.py
glue_script event_fabric_recovery  src/glue/event_fabric_recovery.py

# ── Lambda zips ──────────────────────────────────────────────────────────────
# Each function is a single file at the zip root today. The file LIST form is
# kept (echo's) so a function that gains a helper module adds one argument here
# rather than a new mechanism; the handler names in the .tf files depend on the
# root-level filenames staying as they are.
lambda_zip() {
  local name="$1"; shift
  local d="$WORK/stage-$name" sha code key vid src
  rm -rf "$d"; mkdir -p "$d"
  for src in "$@"; do
    [ -f "$STACK_ROOT/$src" ] || die "lambda source ${src} does not exist (${name})"
    cp "$STACK_ROOT/$src" "$d/$(basename "$src")"
  done
  sha="$(bash "$HASH" "$d")"
  code="$(bash "$ZIP" "$d" "$WORK/${name}.zip")"
  key="lambdas/${sha}.zip"
  vid="$(put "$WORK/${name}.zip" "$key")"
  printf 'lambdas\t%s\t-\t%s\t%s\t%s\t-\t%s\n' "$name" "$sha" "$key" "$vid" "$code" >> "$ROWS"
}

lambda_zip pipeline_controller      src/handler.py
lambda_zip dlq_recorder             src/dlq_recorder.py
lambda_zip subscription_provisioner src/subscription_provisioner.py
lambda_zip event_redrive            src/event_redrive.py

# ── Emit ─────────────────────────────────────────────────────────────────────
rows="$(grep -c . "$ROWS" || true)"
[ "$rows" -eq "$EXPECTED_ROWS" ] \
  || die "expected ${EXPECTED_ROWS} rows (2 Glue + 4 Lambda), got ${rows}. A missing artifact would produce a manifest that under-describes the release."

if [ -n "$TSV" ]; then
  cp "$ROWS" "$TSV"
  note "wrote ${rows} rows to ${TSV}"
fi

if [ -n "$TFVARS" ]; then
  # JSON so no shell quoting reaches HCL. Read automatically by terraform when
  # named *.auto.tfvars.json in the root (gitignored: it holds one build's
  # hashes, and a stale copy would pin the next plan to them).
  {
    printf '{\n  "artifact_shas": {\n'
    awk -F'\t' '{printf "%s    \"%s\": \"%s\"", (NR>1 ? ",\n" : ""), $2, $4} END {print ""}' "$ROWS"
    printf '  },\n  "lambda_code_hashes": {\n'
    awk -F'\t' '$1 == "lambdas" {printf "%s    \"%s\": \"%s\"", (n++ ? ",\n" : ""), $2, $8} END {print ""}' "$ROWS"
    printf '  }\n}\n'
  } > "$TFVARS"
  note "wrote tfvars to ${TFVARS}"
fi
