#!/usr/bin/env bash
# Stage every release artifact: hash it, upload it at its content-addressed
# key, and emit both of the files the rest of the pipeline consumes.
#
# WHY THIS IS ONE SCRIPT AND NOT TWO COPIES. Both halves of the model need the
# identical artifact set, the identical hashes and the identical keys:
#
#   release-echo.yml                       cutting a build tag for uat
#   infrastructure/deploy/_uif-echo-...sh  a dev apply
#
# Written twice, they drift, and the failure is invisible: a function whose
# source list is stale in one of them ships without a module it imports, which
# fails at RUNTIME, not at apply. Same reason `qe/ci.sh` is one script that CI
# and a laptop both run, and the same reason `.githooks/lint-commits.sh` exists.
# The artifact set is defined HERE, once.
#
# Emits two files:
#   --tsv <path>     one row per artifact, for release-manifest.sh
#   --tfvars <path>  artifact_shas + lambda_code_hashes, for terraform
# Either may be omitted; at least one is required, or this does nothing useful.
#
# Requires: aws, and a python that can import zipfile (for zip-artifact.sh).
#
# Usage:
#   stage-artifacts.sh --bucket <release-bucket> --layer-source-bucket <bucket> \
#                      [--tsv out.tsv] [--tfvars out.auto.tfvars.json]

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STACK_ROOT="$(cd "$HERE/.." && pwd)"

die() { printf 'stage-artifacts.sh: %s\n' "$1" >&2; exit 1; }
note() { printf '  %s\n' "$1" >&2; }

BUCKET="" LAYER_SRC="" TSV="" TFVARS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bucket)              [ "$#" -ge 2 ] || die "--bucket needs a value";              BUCKET="$2";    shift 2 ;;
    --layer-source-bucket) [ "$#" -ge 2 ] || die "--layer-source-bucket needs a value"; LAYER_SRC="$2"; shift 2 ;;
    --tsv)                 [ "$#" -ge 2 ] || die "--tsv needs a value";                 TSV="$2";       shift 2 ;;
    --tfvars)              [ "$#" -ge 2 ] || die "--tfvars needs a value";              TFVARS="$2";    shift 2 ;;
    *) die "unknown flag '$1'" ;;
  esac
done

[ -n "$BUCKET" ]    || die "--bucket is required (the release-artifacts bucket)"
[ -n "$LAYER_SRC" ] || die "--layer-source-bucket is required (where the vendored layer zips already live)"
[ -n "$TSV" ] || [ -n "$TFVARS" ] || die "give --tsv, --tfvars, or both; otherwise this uploads and reports nothing"

HASH="${HERE}/artifact-hash.sh"
ZIP="${HERE}/zip-artifact.sh"
[ -x "$HASH" ] || die "artifact-hash.sh not executable at ${HASH}"
[ -x "$ZIP" ]  || die "zip-artifact.sh not executable at ${ZIP}"

WORK="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/stage-artifacts-$$")"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

ROWS="$WORK/rows.tsv"
: > "$ROWS"

# Upload only if the key is absent. Content-addressed, so an object already at
# this key IS this artifact -- which is what makes "only changed artifacts are
# written" true without tracking what changed. The version id is read back
# either way, so what is recorded is the object that is actually there.
put() {
  local file="$1" key="$2" vid
  if aws s3api head-object --bucket "$BUCKET" --key "$key" >/dev/null 2>&1; then
    note "= ${key}"
  else
    # `aws s3 cp`, NOT `s3api put-object`. put-object sends the whole body as a
    # single PUT with no retry and no multipart, which does not survive a large
    # artifact: oracle_instant_client.zip is 132 MB and this failed on it twice
    # in a row with "Connection was closed before we received a valid response
    # from endpoint URL". `s3 cp` switches to multipart above its threshold and
    # retries individual parts, which is the whole reason it exists.
    #
    # The exit status is CHECKED. It was not before, and `note "+ ${key}"` ran
    # unconditionally underneath it, so a failed upload printed the same "+"
    # line as a successful one and the script carried on to fail confusingly
    # two lines later. A staging step that reports an upload it did not make is
    # worse than one that stops.
    aws s3 cp "$file" "s3://${BUCKET}/${key}" --only-show-errors \
      || die "upload of ${key} failed (${file}). Nothing was recorded for it; re-run, the staged artifacts already present are skipped."
    note "+ ${key}"
  fi
  # TWO failures, two messages. Both of these used to say "versioning is off",
  # which is wrong for the common case and actively misleading: when the 132 MB
  # layer above failed to upload, this reported versioning as the cause on a
  # bucket whose versioning had been verified Enabled minutes earlier, sending
  # a reader to the bucket's configuration instead of to the upload.
  vid="$(aws s3api head-object --bucket "$BUCKET" --key "$key" --query VersionId --output text 2>/dev/null)" \
    || die "${key} is not in s3://${BUCKET} after the upload reported success. The object did not land."
  [ -n "$vid" ] && [ "$vid" != "None" ] \
    || die "${key} is present in s3://${BUCKET} but carries no version id, so versioning is off on that bucket and a release cannot record which object it shipped."
  printf '%s\n' "$vid"
}

# ── Glue scripts ─────────────────────────────────────────────────────────────
#
# An EXPLICIT LIST, never a directory scan -- but NOT because gold is excluded.
# It is not: UIFPE-576 folds the gold root into this release unit, and gold's
# four job scripts and five libraries are staged further down. The list is
# explicit because a scan yields paths and no ARTIFACT NAMES, and the name is
# what the manifest and var.artifact_shas are keyed by. src/apps/pipeline/ also
# holds files read by two different Terraform roots, and a scan cannot say
# which root reads which file or what to call it.
#
# The key uses the BASENAME because Glue flattens every --extra-py-files entry
# into the job's working directory; that is why _module_loader.load_dq_kind_module
# tries dq-kinds/<stem>.py and then <stem>.py.
glue_script() {
  local name="$1" src="$2" base sha key vid
  base="$(basename "$src")"
  [ -f "$STACK_ROOT/$src" ] || die "glue script ${src} does not exist"
  sha="$("$HASH" "$STACK_ROOT/$src")"
  key="glue/${sha}/${base}"
  vid="$(put "$STACK_ROOT/$src" "$key")"
  printf 'glue_scripts\t%s\t%s\t%s\t%s\t%s\t-\t-\n' "$name" "$src" "$sha" "$key" "$vid" >> "$ROWS"
}

P=src/apps/pipeline
glue_script bronze_ingest                "$P/bronze-ingest.py"
glue_script silver_routing               "$P/silver_routing.py"
glue_script bronze_extract_sql           "$P/bronze_extract_sql.py"
glue_script silver_transform             "$P/silver-transform.py"
glue_script silver_transform_gcs_iceberg "$P/silver-transform-gcs-iceberg.py"
glue_script silver_transform_bq          "$P/silver-transform-bq.py"
glue_script silver_transform_bq_native   "$P/silver-transform-bq-native.py"
glue_script silver_reconcile             "$P/silver-reconcile.py"
glue_script silver_reconcile_gcs         "$P/silver-reconcile-gcs.py"
glue_script silver_reconcile_bq          "$P/silver-reconcile-bq.py"
glue_script silver_quality_check         "$P/silver-quality-check.py"
glue_script analyze_table                "$P/analyze-table.py"
glue_script iceberg_gcs_probe            "$P/iceberg-gcs-probe.py"
glue_script module_loader                "$P/_module_loader.py"
glue_script dq_kinds_init                "$P/dq-kinds/__init__.py"
glue_script dq_kinds_reconcile           "$P/dq-kinds/reconcile.py"
glue_script dq_kinds_null_rate           "$P/dq-kinds/null-rate.py"
glue_script bq_partition_ddl             src/libs/bq_partition_ddl.py

# ── The GOLD root's Glue scripts and libraries (UIFPE-576 phase 1b) ──────────
#
# Four job scripts and five libraries, read by infrastructure/terraform-gold's
# four Glue jobs. Until this, the gold root uploaded them itself from the repo
# at apply time, which is a BUILD -- and a promoted environment must not build
# (stack-promotion-model.md: "uat receives artifacts, it never makes them").
# Now they are staged here once, and each job resolves its own content key.
#
# The artifact NAME is the manifest key, so it must be unique across every
# class. gold_telemetry, gold_stats and gold_frequencies also travel inside
# Lambda zips, but as ARTIFACT names these nine are new; the uniqueness check
# lives in qe/pipeline/test_glue_artifact_sets.py because a duplicate name
# silently overwrites its twin in the artifact_shas map.
glue_script gold_transform     "$P/generic_gold_transform.py"
glue_script gold_reconcile     "$P/generic_gold_reconcile.py"
glue_script gold_ops_export    "$P/gold_ops_export.py"
glue_script gold_ops_summarize "$P/gold_ops_summarize.py"
glue_script gold_cdc           "$P/gold_cdc.py"
glue_script gold_telemetry     "src/libs/gold_telemetry.py"
glue_script gold_report        "src/libs/gold_report.py"
glue_script gold_stats         "src/libs/gold_stats.py"
glue_script gold_frequencies   "src/libs/gold_frequencies.py"

# ── Lambda zips ──────────────────────────────────────────────────────────────
#
# A Lambda's artifact is a file SET drawn from several directories, so it is
# staged into a clean tree first, then hashed, then zipped. These lists mirror
# what lambda.tf's archive_file blocks used to assemble; they live here because
# a promotable artifact cannot be built at plan time from whatever tree the
# apply happens to run against.
lambda_zip() {
  local name="$1"; shift
  local d="$WORK/stage-$name" sha code key vid spec src dest
  rm -rf "$d"; mkdir -p "$d"
  for spec in "$@"; do
    # Either "src" (flat at the zip root, the usual case) or "src:dest". A dest
    # is needed when a file must sit in a PACKAGE DIRECTORY: gold_alert_notify
    # imports pearson_notifications as a package, so flattening its two files
    # to the root breaks every alert mail at import.
    src="${spec%%:*}"; dest="${spec#*:}"
    [ "$dest" = "$spec" ] && dest="$(basename "$src")"
    [ -f "$STACK_ROOT/$src" ] || die "lambda source ${src} does not exist (${name})"
    mkdir -p "$d/$(dirname "$dest")"
    cp "$STACK_ROOT/$src" "$d/$dest"
  done
  sha="$("$HASH" "$d")"
  code="$("$ZIP" "$d" "$WORK/${name}.zip")"
  key="lambdas/${sha}.zip"
  vid="$(put "$WORK/${name}.zip" "$key")"
  printf 'lambdas\t%s\t-\t%s\t%s\t%s\t-\t%s\n' "$name" "$sha" "$key" "$vid" "$code" >> "$ROWS"
}

L=src/apps/lambdas; C=src/apps/control_panel; B=src/libs
# gold_frequencies.py was MISSING from this list and the omission reached dev:
# the function's INIT failed with
#   Runtime.ImportModuleError: No module named 'gold_frequencies'
# and every control-plane call returned 500 until it was added back.
# pipeline_controller.py:48 does `from gold_frequencies import
# is_transform_frequency, parse_time_of_day`, and lambda.tf's archive_file for
# this function has always listed all nine files. This list claims to mirror
# that one; it did not, which is the exact drift the comment above warns about.
# qe/pipeline/test_lambda_artifact_sets.py now asserts the two agree.
lambda_zip pipeline_controller \
  "$C/pipeline_controller.py" "$B/pipeline_profiles.py" "$B/echo_resources.py" \
  "$B/gold_resources.py" "$B/gold_frequencies.py" "$B/sizing_policy.py" \
  "$B/daily_schedule.py" "$B/schedule_vocabulary.py" "$B/bq_partition_ddl.py" \
  "$B/gold_stats.py" "$B/gold_telemetry.py"
lambda_zip scheduler_dispatcher \
  "$L/scheduler_dispatcher.py" "$B/pipeline_profiles.py" "$B/echo_resources.py" \
  "$B/daily_schedule.py" "$B/sizing_policy.py" "$B/schedule_vocabulary.py"
lambda_zip gcs_replicator       "$L/gcs_replicator.py" "$B/echo_resources.py"
lambda_zip silver_bq_replicator "$L/silver_bq_replicator.py" "$B/echo_resources.py"
# The fifth function, declared in data_quality.tf rather than lambda.tf --
# which is why lambda.tf's header says "five" while four resources live in it.
lambda_zip dq_check_controller  "$C/dq_check_controller.py" "$B/echo_resources.py"

# ── The GOLD root's functions (UIFPE-576) ────────────────────────────────────
#
# Gold is part of THIS release unit, not a separate one: `gold` is a {layer} in
# the naming vocabulary and not a {stack}, the promotion model is stack-scoped,
# and the two halves already share source - pipeline_controller above vendors
# gold_resources, gold_frequencies, gold_stats and gold_telemetry. Two manifests
# could disagree about the same file; one cannot.
#
# Each list mirrors that function's archive_file block in
# infrastructure/terraform-gold/, and qe/pipeline/test_lambda_artifact_sets.py
# asserts the two agree - the guard that exists because gold_frequencies.py was
# once missing from the list above and reached dev as ImportModuleError.
M=../../platform/python; N=../../platform/python/pearson_notifications
lambda_zip ai_draft          "$L/ai_draft.py"
lambda_zip submit            "$L/submit.py"
lambda_zip grain_probe       "$L/grain_probe.py"
lambda_zip gold_announce     "$L/gold_announce.py" "$B/gold_resources.py"
lambda_zip gold_dispatcher   "$L/gold_dispatcher.py" "$B/gold_resources.py" \
  "$B/gold_frequencies.py" "$B/gold_telemetry.py"
# pearson_notifications keeps its PACKAGE path inside the zip; flattened, the
# import fails and no alert mail is ever sent.
lambda_zip gold_alert_notify "$L/gold_alert_notify.py" "$B/gold_resources.py" \
  "$N/__init__.py:pearson_notifications/__init__.py" \
  "$N/client.py:pearson_notifications/client.py"
lambda_zip gold_serving_sync "$L/gold_serving_sync.py" "$B/gold_serving.py" \
  "$P/gold_cdc.py"
lambda_zip gold_serving_api  "$L/gold_serving_api.py" "$B/gold_serving.py" \
  "$P/gold_cdc.py" "$M/jwt_verify.py"
# compile_preview is the one function whose artifact is a BUILT TREE rather than
# a file list: its zip carries a pip-installed sqlglot beside the handler, from
# the `build-preview` target in infrastructure/terraform-gold/Makefile. The tree
# is content-reproducible (measured: identical across two installs), and
# zip-artifact.sh normalises mtimes, permissions and order, so the zip hash is
# stable for unchanged pins.
lambda_zip_tree() {
  local name="$1" d="$2" sha code key vid
  [ -d "$d" ] || die "lambda tree ${d} does not exist (${name}); run 'make build-preview' in stacks/echo/infrastructure/terraform-gold first"
  sha="$("$HASH" "$d")"
  code="$("$ZIP" "$d" "$WORK/${name}.zip")"
  key="lambdas/${sha}.zip"
  vid="$(put "$WORK/${name}.zip" "$key")"
  printf 'lambdas\t%s\t-\t%s\t%s\t%s\t-\t%s\n' "$name" "$sha" "$key" "$vid" "$code" >> "$ROWS"
}

lambda_zip_tree compile_preview \
  "$STACK_ROOT/infrastructure/terraform-gold/.build/compile_preview_src"

# ── Lambda layers ────────────────────────────────────────────────────────────
#
# NOT rebuilt. Three have `make build-*-layer` targets that end in `zip -qr`,
# which embeds mtimes, and a pip tree is not reliably byte-stable (RECORD,
# __pycache__) -- so a rebuild produces a new hash for unchanged pins and
# breaks the reproducibility the promotion claim rests on. The fourth,
# oracle_instant_client, is a licensed ~127 MB vendor download with no build
# target in this repo at all (stacks/echo/CLAUDE.md records it as
# unreproducible). So the layer already in S3 IS the artifact, and this records
# which one -- the same treatment apicurio gives its registry image.
layer() {
  local name="$1" zipname="$2" src_key sha code key vid
  src_key="code/lambda-layers/${zipname}"
  if ! aws s3api head-object --bucket "$LAYER_SRC" --key "$src_key" >/dev/null 2>&1; then
    local hint
    case "$name" in
      oracle_instant_client) hint="it is a vendored Oracle Instant Client zip with no build target in this repo; see infrastructure/deploy/layers/README.md" ;;
      gcs_client)            hint="make -C stacks/echo/infrastructure/terraform build-gcs-layer" ;;
      silver_bq_client)      hint="make -C stacks/echo/infrastructure/terraform build-silver-bq-layer" ;;
      controller_bq_client)  hint="make -C stacks/echo/infrastructure/terraform build-controller-bq-layer" ;;
    esac
    die "s3://${LAYER_SRC}/${src_key} is missing, so layer '${name}' cannot be staged. Produce and upload it first (${hint}). This script RECORDS layers; it does not create them."
  fi
  aws s3 cp "s3://${LAYER_SRC}/${src_key}" "$WORK/${zipname}" --quiet
  sha="$("$HASH" "$WORK/${zipname}")"
  code="$(openssl dgst -sha256 -binary "$WORK/${zipname}" | openssl base64 -A)"
  key="layers/${sha}.zip"
  vid="$(put "$WORK/${zipname}" "$key")"
  printf 'layers\t%s\t%s\t%s\t%s\t%s\tvendored\t%s\n' "$name" "$zipname" "$sha" "$key" "$vid" "$code" >> "$ROWS"
}

layer oracle_instant_client oracle_instant_client.zip
layer gcs_client            gcs_layer.zip
layer silver_bq_client      silver_bq_layer.zip
layer controller_bq_client  controller_bq_layer.zip

# ── Emit ─────────────────────────────────────────────────────────────────────
#
# The UI bundle is NOT staged here. It needs `npm ci && npm run build`, which
# is a different toolchain and a different failure mode from hashing a file
# that already exists -- and the dev UI deploy publishes it directly rather
# than promoting it. release-echo.yml appends its row.
rows="$(grep -c . "$ROWS" || true)"
[ "$rows" -eq 45 ] \
  || die "expected 45 rows (27 Glue + 14 Lambda + 4 layers), got ${rows}. A missing artifact would produce a manifest that under-describes the release."

if [ -n "$TSV" ]; then
  cp "$ROWS" "$TSV"
  note "wrote ${rows} rows to ${TSV}"
fi

if [ -n "$TFVARS" ]; then
  # Terraform reads artifact_shas for every artifact and lambda_code_hashes for
  # all fourteen functions -- five in the main root, nine in the gold root.
  # Written as JSON so no shell quoting reaches HCL.
  {
    printf '{\n  "artifact_shas": {\n'
    awk -F'\t' '{printf "%s    \"%s\": \"%s\"", (NR>1 ? ",\n" : ""), $2, $4} END {print ""}' "$ROWS"
    printf '  },\n  "lambda_code_hashes": {\n'
    awk -F'\t' '$1 == "lambdas" {printf "%s    \"%s\": \"%s\"", (n++ ? ",\n" : ""), $2, $8} END {print ""}' "$ROWS"
    printf '  }\n}\n'
  } > "$TFVARS"
  note "wrote tfvars to ${TFVARS}"
fi
