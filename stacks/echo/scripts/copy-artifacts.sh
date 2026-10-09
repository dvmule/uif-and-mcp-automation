#!/usr/bin/env bash
# Promote a build tag's artifacts into another environment's release bucket,
# and prove each object is the one the tag names.
#
# THE MODEL, AND WHY THIS SCRIPT IS SHORT. Application artifacts PROMOTE: the
# bytes dev built and the gate passed are copied unchanged, and the evidence is
# that the digest did not move. Infrastructure CONVERGES: one Terraform root
# applied with the target environment's tfvars. This script is the whole of the
# first half, and it is short because three other scripts already own the parts
# it would otherwise duplicate:
#
#   read-manifest.sh      what the artifacts ARE (and the only manifest parser)
#   stage-artifacts.sh    what the artifact SET is, defined once, for dev
#   restore-artifacts.sh  whether a DOWNLOADED artifact is still the release
#
# So this script does not decide which artifacts exist -- the manifest does,
# via `read-manifest.sh --dump`, which is why a class that gains an artifact
# needs no change here. And it does not re-hash anything: nothing is downloaded.
#
# ONE --dump, NOT A FIELD AT A TIME. The first version of this asked
# read-manifest.sh for each field separately: ~108 invocations, each re-reading
# the tag through git, measured at four and a half minutes on Windows. --dump
# returns every artifact fully validated in one call. The parsing still happens
# in read-manifest.sh, which is the point -- this script never reads the tag.
#
# WHY NOTHING IS DOWNLOADED. dev's and uat's release buckets are both in
# account 283556840805, so `copy-object` is server-side and no bytes cross the
# runner. Downloading all 27 artifacts to re-hash them would move 160MB -- 159MB
# of that the four layer zips, and 126MB of THAT oracle_instant_client.zip alone
# -- to re-prove what content-addressing already states: the key IS the sha256,
# checked by read-manifest.sh on every `key` read.
#
# Measured from the first real dry run against echo/v0.1.0 on 2026-09-24. An
# earlier version of this comment said ~300MB, which was a guess and nearly
# double. The argument survives the correction, but a justification resting on
# an invented number is worth no more than the number. What that does NOT prove is that the object at the key is still
# the object the tag named -- so that is what this checks, per artifact, with a
# version id.
#
# WHY copy-object AND NOT `aws s3 cp`. `cp` cannot pin a version, so it copies
# whatever is current at the key. `copy-object --copy-source '...?versionId=V'`
# copies the exact object the manifest recorded, which is the difference
# between promoting a release and promoting whatever happens to be there now.
#
# WHY A NEWER VERSION AT THE KEY IS A NOTE AND NOT A REFUSAL. An earlier draft
# refused when the key's current version differed from the manifest's. That is
# wrong here: on 2026-09-12 all four layer zips under code/lambda-layers/ were
# deleted and recovered, and a recovery writes a NEW version id for
# byte-identical content. Refusing would have blocked a promotion of artifacts
# that were provably intact. Pinning the manifest's version is already
# sufficient -- it is the bytes the gate saw -- so a newer version is worth
# saying out loud and nothing more.
#
# THE VERSION ID RECORDED IN --tsv IS THE DESTINATION'S. A uat release manifest
# has to describe uat's objects; carrying dev's version ids forward would
# produce a manifest whose keys are right and whose versions name objects in a
# bucket the environment does not read.
#
# Usage:
#   copy-artifacts.sh --tag echo/v0.1.0 --to-bucket uif-echo-uat-release-artifacts \
#                     [--tfvars artifacts.auto.tfvars.json] [--tsv rows.tsv] [--dry-run]
#
# The SOURCE bucket is not a flag: it comes from the manifest's artifacts.bucket.
# The tag already says where its artifacts live, and a flag would let somebody
# promote from the wrong bucket while every hash still checked out.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

die()  { printf 'copy-artifacts.sh: %s\n' "$1" >&2; exit 1; }
note() { printf '  %s\n' "$1" >&2; }

READ="${READ_MANIFEST:-${HERE}/read-manifest.sh}"
[ -x "$READ" ] || die "read-manifest.sh is not executable at ${READ}"

TAG="" TO_BUCKET="" TFVARS="" TSV="" DRY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag)       [ "$#" -ge 2 ] || die "--tag needs a value";       TAG="$2";       shift 2 ;;
    --to-bucket) [ "$#" -ge 2 ] || die "--to-bucket needs a value"; TO_BUCKET="$2"; shift 2 ;;
    --tfvars)    [ "$#" -ge 2 ] || die "--tfvars needs a value";    TFVARS="$2";    shift 2 ;;
    --tsv)       [ "$#" -ge 2 ] || die "--tsv needs a value";       TSV="$2";       shift 2 ;;
    --dry-run)   DRY=1; shift ;;
    *) die "unknown flag '$1'" ;;
  esac
done

[ -n "$TAG" ]       || die "--tag is required: the build tag whose manifest names the artifacts (e.g. echo/v0.1.0)"
[ -n "$TO_BUCKET" ] || die "--to-bucket is required: the destination release bucket (e.g. uif-echo-uat-release-artifacts)"

# Every manifest read goes through here. read-manifest.sh refuses a lightweight
# tag, a manifest that does not open with this tag's name, and a gate whose
# conclusion is not success -- on EVERY read, so those refusals are inherited
# rather than restated.
mf() { "$READ" "$TAG" "$@"; }

# aws --output text is read through this. Python's CRLF cost this project 169
# falsely-denied actions once already; a version id with a trailing CR compares
# unequal to itself and the error reads exactly like a missing object.
awstext() { tr -d '\r'; }

SRC_BUCKET="$(mf bucket)"

[ "$SRC_BUCKET" != "$TO_BUCKET" ] \
  || die "$(printf '%s\n' \
    "${TAG}'s artifacts already live in ${TO_BUCKET}." \
    "  Promoting a bucket to itself would report 27 skips and look like a" \
    "  successful promotion that moved nothing.")"

# ── Destination preflight ─────────────────────────────────────────────────────

aws s3api head-bucket --bucket "$TO_BUCKET" >/dev/null 2>&1 \
  || die "$(printf '%s\n' \
    "s3://${TO_BUCKET} does not exist or is not reachable with these credentials." \
    "  Terraform declares it, and on a greenfield environment that is circular:" \
    "    terraform apply -var-file=envs/<env>.tfvars \\" \
    "      -target=aws_s3_bucket.release_artifacts \\" \
    "      -target=aws_s3_bucket_versioning.release_artifacts")"

dest_versioning="$(aws s3api get-bucket-versioning --bucket "$TO_BUCKET" \
  --query 'Status' --output text 2>/dev/null | awstext || echo None)"
[ "$dest_versioning" = "Enabled" ] \
  || die "$(printf '%s\n' \
    "versioning on s3://${TO_BUCKET} is ${dest_versioning}, not Enabled." \
    "  Every artifact's recorded version id would be null, so the manifest this" \
    "  promotion produces could not identify the objects it just wrote. The" \
    "  targeted apply above turns it on; so does put-bucket-versioning.")"

WORK="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/copy-artifacts-$$")"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

ROWS="$WORK/rows.tsv"
: > "$ROWS"

# ── Both buckets, listed once ─────────────────────────────────────────────────
#
# TWO LISTINGS INSTEAD OF EIGHTY-ONE head-objects. The first version of this
# made five aws calls per artifact -- source-at-version, source-current,
# destination, copy, destination-again -- which is 135 calls for 27 artifacts.
# Measured at three minutes against a STUB; the real CLI costs about 1.5s per
# invocation on Windows, so five minutes of process startup to move nothing.
#
# A listing answers the same questions in one call each, and makes a re-run
# nearly free: nothing to copy means nothing but the two listings.
SRC_VERSIONS="$WORK/src-versions.tsv"
DST_VERSIONS="$WORK/dst-versions.tsv"

aws s3api list-object-versions --bucket "$SRC_BUCKET" \
  --query 'Versions[].[Key,VersionId,Size,IsLatest]' --output text 2>/dev/null \
  | tr -d '\r' > "$SRC_VERSIONS" \
  || die "cannot list s3://${SRC_BUCKET}. The manifest names it as this tag's artifact bucket."

[ -s "$SRC_VERSIONS" ] \
  || die "s3://${SRC_BUCKET} has no object versions at all, so it cannot hold ${TAG}'s artifacts."

# VERSIONS on the destination too, not just keys. A plain key listing would
# answer "is it already there", but the TSV has to record the DESTINATION's
# version id for every artifact -- including the ones already present on a
# re-run -- or a uat release manifest built from it would name objects that
# only exist in dev's bucket. An earlier version used list-objects-v2 and wrote
# the literal "PRESENT" into that column, which is not a version id.
#
# Empty is fine and expected: the destination is a bucket Terraform has just
# created on a first promote.
aws s3api list-object-versions --bucket "$TO_BUCKET" \
  --query 'Versions[?IsLatest].[Key,VersionId]' --output text 2>/dev/null \
  | tr -d '\r' > "$DST_VERSIONS" || : > "$DST_VERSIONS"

# ── One artifact ──────────────────────────────────────────────────────────────

# THE PROOF, answered from the source listing. The manifest's version must
# still exist at that key: a deleted or expired version cannot be promoted,
# because nothing left can show the bytes the gate saw.
#
# SEPARATE FROM THE COPY, AND RUN OVER EVERY ARTIFACT FIRST. When this was one
# pass, a missing version on artifact 20 refused correctly -- after copying 19.
# Those 19 were valid (content-addressed keys cannot be wrong, only absent) and
# a re-run resumed cleanly, so nothing was broken. But it left a half-populated
# bucket behind a refusal, which reads like a failed promotion that did
# something, and someone would have to reason about whether it mattered.
# Verifying costs no API calls at all -- the listing is already in hand -- so
# there is no reason to touch the destination before every artifact has been
# accounted for.
verify_source() {
  local class="$1" row_name="$2" key="$3" vid="$4"

  local hit
  hit="$(awk -F'\t' -v k="$key" -v v="$vid" '$1==k && $2==v {print; exit}' "$SRC_VERSIONS")"
  [ -n "$hit" ] \
    || die "$(printf '%s\n' \
      "${TAG} names ${class}/${row_name} at version ${vid} of s3://${SRC_BUCKET}/${key}," \
      "  and no such version is listed. The tag's evidence covers those bytes and" \
      "  no others, so this promotion cannot proceed -- and nothing has been" \
      "  copied. If the version was deleted, recover it: a byte-identical" \
      "  recovery is fine, but it takes a new tag to record the new version id.")"

  # A newer version at a content-addressed key is unusual, not fatal -- see the
  # header. Free now that the listing carries IsLatest, and said out loud
  # because nothing else would show it.
  local is_latest
  is_latest="$(printf '%s' "$hit" | cut -f4)"
  if [ "$is_latest" != "True" ]; then
    note "! ${key} has a newer version; promoting the manifest's ${vid}"
  fi
}

promote() {
  # One --dump row, already validated by read-manifest.sh: class, name, file,
  # sha256, key, version_id, origin, code_sha256.
  local class="$1" row_name="$2" file="$3" sha="$4" key="$5" vid="$6" origin="$7" code="$8"

  # Size, for the dry-run note only. verify_source has already established
  # that this key and version are in the listing.
  local src_len
  src_len="$(awk -F'\t' -v k="$key" -v v="$vid" '$1==k && $2==v {print $3; exit}' "$SRC_VERSIONS")"

  # Content-addressed, so an object already at this key IS this artifact. That
  # is what makes re-running free and the whole script idempotent -- the same
  # reasoning as stage-artifacts.sh's put().
  local dest_vid=""
  dest_vid="$(awk -F'\t' -v k="$key" '$1==k {print $2; exit}' "$DST_VERSIONS")"

  if [ -n "$dest_vid" ]; then
    note "= ${key}"
  elif [ "$DRY" -eq 1 ]; then
    note "+ ${key} (dry run, ${src_len} bytes)"
    dest_vid="DRY-RUN"
  else
    # NO LENGTH RE-CHECK AFTER THE COPY, and that is a correction rather than
    # an omission. An earlier version read the destination back to compare
    # bytes, reasoning from the 132MB upload that failed inside
    # stage-artifacts.sh while printing a success line. But that was a CLIENT
    # upload -- a single PUT with no retry. CopyObject is server-side and has
    # no partial-success mode: it returns a result or it errors. So the
    # re-check spent 27 calls guarding a failure that cannot occur here, and
    # the copy's own non-zero exit is the real guard.
    dest_vid="$(aws s3api copy-object --bucket "$TO_BUCKET" --key "$key" \
      --copy-source "${SRC_BUCKET}/${key}?versionId=${vid}" \
      --query 'VersionId' --output text | awstext)"
    [ -n "$dest_vid" ] && [ "$dest_vid" != "None" ] \
      || die "copying ${key} returned no version id, so the object it wrote cannot be identified."
    note "+ ${key}"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$class" "$row_name" "$file" "$sha" "$key" "$dest_vid" "$origin" "$code" >> "$ROWS"
}

# ── Every artifact the manifest names ─────────────────────────────────────────

note "promoting ${TAG}"
note "  from s3://${SRC_BUCKET}"
note "  to   s3://${TO_BUCKET}"

# The manifest, once. Written to a file rather than piped into the loop: a
# pipeline puts the loop in a subshell, and `die` inside a subshell kills only
# the subshell -- so a refusal on artifact 9 would be printed and then the
# remaining 18 would promote anyway, exiting 0.
MANIFEST_ROWS="$WORK/manifest.tsv"
mf --dump > "$MANIFEST_ROWS"

expected="$(grep -c . "$MANIFEST_ROWS" || true)"
[ "$expected" -gt 0 ] || die "${TAG}'s manifest named no artifacts."

# Pass one: prove every artifact, copy nothing.
while IFS=$'\t' read -r class name file sha key vid origin code; do
  [ -n "$class" ] || continue
  verify_source "$class" "$name" "$key" "$vid"
done < "$MANIFEST_ROWS"
note "all ${expected} artifacts verified present at the versions ${TAG} names"

# Pass two: copy what is missing.
while IFS=$'\t' read -r class name file sha key vid origin code; do
  [ -n "$class" ] || continue
  promote "$class" "$name" "$file" "$sha" "$key" "$vid" "$origin" "$code"
done < "$MANIFEST_ROWS"

# Derived, not hardcoded. stage-artifacts.sh can assert 45 because it DEFINES
# the set; here the manifest defines it, so a constant would defeat --dump and
# need editing every time an artifact is added. This still catches a row lost
# mid-loop.
rows="$(grep -c . "$ROWS" || true)"
[ "$rows" -eq "$expected" ] \
  || die "the manifest named ${expected} artifacts but ${rows} rows were written."
note "${rows} artifacts promoted or already present"

# ── Emit ──────────────────────────────────────────────────────────────────────

if [ -n "$TSV" ]; then
  cp "$ROWS" "$TSV"
  note "wrote ${rows} rows to ${TSV}"
fi

if [ -n "$TFVARS" ]; then
  # Byte-for-byte the shape stage-artifacts.sh writes, because Terraform cannot
  # tell which script produced its inputs and the two must not disagree.
  # artifact_shas takes every row's hash; lambda_code_hashes takes only the
  # functions', since source_code_hash exists on aws_lambda_function alone.
  {
    printf '{\n  "artifact_shas": {\n'
    awk -F'\t' '{printf "%s    \"%s\": \"%s\"", (NR>1 ? ",\n" : ""), $2, $4} END {print ""}' "$ROWS"
    printf '  },\n  "lambda_code_hashes": {\n'
    awk -F'\t' '$1 == "lambdas" {printf "%s    \"%s\": \"%s\"", (n++ ? ",\n" : ""), $2, $8} END {print ""}' "$ROWS"
    printf '  }\n}\n'
  } > "$TFVARS"
  note "wrote tfvars to ${TFVARS}"
fi

if [ "$DRY" -eq 1 ]; then
  note "DRY RUN — nothing was copied. Version ids in any output are placeholders."
fi
