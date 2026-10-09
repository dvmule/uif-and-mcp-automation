#!/usr/bin/env bash
# Put the two vendor JARs on disk where Terraform's aws_s3_object expects them.
#
# WHY THIS EXISTS. glue.tf declares a null_resource that curls each JAR into
# infrastructure/terraform/.cache/jars/, and an aws_s3_object whose `source`
# reads that path. The provider reads the local file at apply time; the
# null_resource only runs when its triggers change.
#
# Those two facts are incompatible with an ephemeral runner. Once the
# null_resource is in state, a later run on a FRESH runner sees unchanged
# triggers, skips the download, and the provider then fails:
#
#   opening S3 object source (./.cache/jars/iceberg-gcp-bundle-1.6.1.jar):
#   no such file or directory
#
# It broke the uif-echo-uat converge on run 36327029234 -- round 2 created the
# null_resources, round 3 got a new runner -- and it would have broken every
# subsequent run, because re-applying cannot make Terraform re-run a resource
# whose triggers have not moved.
#
# THE FIX IS TO PUT THE FILE THERE BEFORE TERRAFORM LOOKS. The null_resource's
# local-exec is already guarded by `if [ ! -f "$dest" ]`, so a pre-downloaded
# file makes it a no-op that stays in state unchanged -- no replacement, no
# state churn, and no change to glue.tf.
#
# NOT GATED PER ENVIRONMENT, and this is the trap worth naming: the obvious fix
# was to skip the JARs outside dev, since they are "the GCP ones". They are not.
# generic_bronze_ingest -- the core Bronze job every pipeline runs -- passes
# local.gcp_glue_extra_jars as --extra-jars at glue.tf:51. Gating them would
# leave that job pointing at S3 keys that do not exist, and a Glue job accepts
# any string for --extra-jars, so it would apply clean and fail at run time.
#
# Idempotent: an existing file of non-zero length is left alone. Safe to call
# before every plan and every apply.
set -euo pipefail

# KEPT IN STEP WITH glue.tf's local.gcp_glue_jars BY A TEST, not by care --
# qe/pipeline/test_gcp_jar_fetch.py fails if the two lists disagree on a
# filename, a version or a URL. Two hand-maintained copies of one fact need
# something checking them; a comment asking nicely is not that.
JARS=(
  "gcs-connector-hadoop3-2.2.22-shaded.jar|https://repo1.maven.org/maven2/com/google/cloud/bigdataoss/gcs-connector/hadoop3-2.2.22/gcs-connector-hadoop3-2.2.22-shaded.jar"
  "iceberg-gcp-bundle-1.6.1.jar|https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-gcp-bundle/1.6.1/iceberg-gcp-bundle-1.6.1.jar"
)

# Resolved from this script's own location so it does not care where it is
# called from -- the workflow runs it from the repo root, the deploy script from
# wherever the operator happens to be.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cache="${here}/../infrastructure/terraform/.cache/jars"
mkdir -p "$cache"

for entry in "${JARS[@]}"; do
  name="${entry%%|*}"
  url="${entry#*|}"
  dest="${cache}/${name}"

  if [ -s "$dest" ]; then
    echo "  cached   ${name}"
    continue
  fi

  echo "  fetching ${name}"
  # To a temp file, then moved. A curl interrupted part-way through would
  # otherwise leave a short file that `-s` accepts and Glue rejects at run
  # time, which is the failure mode this whole script exists to avoid.
  tmp="${dest}.partial"
  curl -fsSL --retry 3 --retry-delay 2 "$url" -o "$tmp"
  [ -s "$tmp" ] || { echo "  ERROR: ${name} downloaded empty from ${url}" >&2; rm -f "$tmp"; exit 1; }
  mv "$tmp" "$dest"
done

echo "  jars ready in ${cache}"
