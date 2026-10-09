#!/usr/bin/env bash
# Assemble the release manifest carried in the annotated tag's message.
#
# The manifest is the release's evidence: it is what QE signs off against and
# what Ops rolls back to. So this script's real job is REFUSAL. It will not
# write a manifest recording a truncated or uppercase hash, a gate that did not
# pass, a duplicate artifact, or an unknown artifact class -- because a manifest
# that lies is worse than none at all, looking exactly like evidence.
#
# WHY A FILE AND NOT ENVIRONMENT VARIABLES. apicurio's equivalent takes eleven
# env vars because it has three artifacts. Echo has twenty-six, which would be
# seventy-eight variables and a shell quoting problem at every call site. So the
# scalar release fields arrive in the environment and the artifacts arrive as a
# TSV file the build appends to as it goes.
#
# Artifact file format -- eight tab-separated columns, one row per artifact:
#
#   class  name  file  sha256  key  version_id  origin  code_sha256
#
#   class        ui_bundle | lambdas | layers | glue_scripts
#   name         the manifest key: an underscore-safe identifier, because a
#                dotted field path cannot address `bronze-ingest.py`
#   file         the real relative path or filename; "-" when it equals name
#   sha256       64 lowercase hex, the artifact's identity AND its S3 key
#   key          the S3 key, relative to the bucket
#   version_id   the S3 object version, recorded as evidence and never read back
#   origin       built | vendored | "-"
#   code_sha256  base64 sha256 of the ARCHIVE'S OWN BYTES, or "-"
#
# WHY code_sha256 EXISTS, AND WHY IT IS NOT THE IDENTITY. `sha256` is the
# content identity: a tree hash for a multi-file artifact, a file hash for a
# single file. It is what the key is named after and what promotion proves
# unchanged. `code_sha256` is a second, narrower fact -- the hash of the exact
# bytes sitting in S3 -- and it exists for two consumers:
#
#   1. Terraform's source_code_hash on a Lambda. Without it, dropping
#      archive_file leaves a hand-run UpdateFunctionCode invisible to a plan.
#      It is stored in the base64 form Terraform expects, because that is its
#      only consumer and one canonical form beats a conversion at each call.
#   2. Verifying a copied archive. zip-artifact.sh is deterministic, so the
#      archive's bytes and its tree are one-to-one -- comparing this is exact
#      and costs no extraction.
#
# It is "-" for a single file (where it would equal sha256) and for the UI
# bundle, which is verified by re-hashing the extracted tree instead.
#
# Scalar inputs, all required, all in the environment:
#   TAG GIT_COMMIT GATE_RUN_ID GATE_CONCLUSION BUCKET ARTIFACT_FILE
# BUILT_AT is overridable so the output is reproducible under test.
#
# Usage: release-manifest.sh            (reads $ARTIFACT_FILE)
# Prints: the manifest, on stdout.

set -euo pipefail

: "${TAG:?TAG is required}"
: "${GIT_COMMIT:?GIT_COMMIT is required}"
: "${GATE_RUN_ID:?GATE_RUN_ID is required}"
: "${GATE_CONCLUSION:?GATE_CONCLUSION is required}"
: "${BUCKET:?BUCKET is required}"
: "${ARTIFACT_FILE:?ARTIFACT_FILE is required}"

die() { printf 'release-manifest.sh: %s\n' "$1" >&2; exit 1; }

[ -r "$ARTIFACT_FILE" ] || die "ARTIFACT_FILE '${ARTIFACT_FILE}' is not readable"

# A build whose gate did not pass must not produce a tag at all, so this is
# checked before anything is parsed.
[ "$GATE_CONCLUSION" = "success" ] \
  || die "the stacks-ci gate concluded '${GATE_CONCLUSION}', refusing to write a manifest claiming it passed"

printf '%s' "$GIT_COMMIT" | grep -Eq '^[0-9a-f]{40}$' \
  || die "GIT_COMMIT must be a full 40-character lowercase sha, got '${GIT_COMMIT}'"

printf '%s' "$GATE_RUN_ID" | grep -Eq '^[0-9]+$' \
  || die "GATE_RUN_ID must be digits, got '${GATE_RUN_ID}'"

# Split one tab-separated line, PRESERVING EMPTY FIELDS, into TSV_F.
#
# `IFS=$'\t' read -r a b c` cannot be used here. Tab is an IFS *whitespace*
# character, so bash collapses a run of tabs into one delimiter: a row with an
# empty version_id column reads as one field short, every later column shifts
# left by one, and the empty value this script is meant to refuse arrives
# looking like a populated one. That was observed, not theorised -- the
# "empty version_id" case passed validation and emitted a manifest with
# origin's value sitting in version_id's field.
split_tsv() {
  local s="${1}"$'\t' f
  TSV_F=()
  while [ -n "$s" ]; do
    f="${s%%$'\t'*}"
    TSV_F+=("$f")
    s="${s#*$'\t'}"
  done
}

# Read every row first and validate the whole set before emitting a line. A
# manifest that is half-written and then fails is a file somebody might use.
declare -a rows=()
declare -a TSV_F=()
seen=""
line_no=0

while IFS= read -r raw_line || [ -n "${raw_line:-}" ]; do
  line_no=$((line_no + 1))
  # Skip blanks and comments so the build can annotate the file it appends to.
  case "${raw_line:-}" in ''|'#'*) continue ;; esac

  split_tsv "$raw_line"
  [ "${#TSV_F[@]}" -eq 8 ] \
    || die "row ${line_no}: expected 8 tab-separated columns (class name file sha256 key version_id origin code_sha256), got ${#TSV_F[@]}"
  class="${TSV_F[0]}"
  name="${TSV_F[1]}"
  file="${TSV_F[2]}"
  sha="${TSV_F[3]}"
  key="${TSV_F[4]}"
  version_id="${TSV_F[5]}"
  origin="${TSV_F[6]}"
  code_sha="${TSV_F[7]}"

  [ -n "${name:-}" ]       || die "row ${line_no}: name is empty"
  [ -n "${sha:-}" ]        || die "row ${line_no} (${class}/${name}): sha256 is empty"
  [ -n "${key:-}" ]        || die "row ${line_no} (${class}/${name}): key is empty"
  [ -n "${version_id:-}" ] || die "row ${line_no} (${class}/${name}): version_id is empty. S3 returns one on every put to a versioned bucket; an empty value means versioning is off or the put was not read back."

  case "$class" in
    ui_bundle|lambdas|layers|glue_scripts) ;;
    *) die "row ${line_no}: unknown artifact class '${class}'. Known: ui_bundle lambdas layers glue_scripts" ;;
  esac

  # Underscore-safe, because a dotted field path addresses this key.
  printf '%s' "$name" | grep -Eq '^[a-z0-9_]+$' \
    || die "row ${line_no}: name '${name}' must match ^[a-z0-9_]+$ -- a dotted field path cannot address a key containing a dot or a slash. Put the real filename in the file column."

  # Lowercase hex only: accepting uppercase would let two spellings of one
  # artifact into the record, and the promotion claim is an equality check.
  printf '%s' "$sha" | grep -Eq '^[0-9a-f]{64}$' \
    || die "row ${line_no} (${class}/${name}): sha256 must be 64 lowercase hex characters with no prefix, got '${sha}'"

  # The key IS the hash. If they disagree, either the upload went somewhere
  # else or the hash was computed over different bytes -- both make every
  # other field in this row untrustworthy.
  case "$key" in
    *"$sha"*) ;;
    *) die "row ${line_no} (${class}/${name}): key '${key}' does not contain its own sha256 '${sha}'. Content-addressed artifacts store at their hash; a key that disagrees is not the artifact this row describes." ;;
  esac

  case "${origin:-}" in
    built|vendored|-|'') ;;
    *) die "row ${line_no} (${class}/${name}): origin must be built, vendored or -, got '${origin}'" ;;
  esac

  # base64 of a 32-byte digest is always 43 characters plus one '=' pad. A
  # hex digest slipped into this column would be 64 characters and is caught
  # here rather than by Terraform reporting a permanent diff it cannot explain.
  if [ "$code_sha" != "-" ] && [ -n "$code_sha" ]; then
    printf '%s' "$code_sha" | grep -Eq '^[A-Za-z0-9+/]{43}=$' \
      || die "row ${line_no} (${class}/${name}): code_sha256 must be base64 of a sha256 digest (43 chars + '='), got '${code_sha}'. Terraform's source_code_hash takes base64, not hex."
  fi

  # An archive without one cannot be verified on copy, and a Lambda without
  # one loses drift detection. Both are the reason the column exists.
  case "$class" in
    lambdas|layers)
      [ "$code_sha" != "-" ] && [ -n "$code_sha" ] \
        || die "row ${line_no} (${class}/${name}): code_sha256 is required for ${class} -- it is Terraform's source_code_hash and the only way a copied archive is verified without extracting it." ;;
  esac

  # One row per artifact. A duplicate means the build emitted an artifact twice,
  # and the second would silently win in the YAML.
  case " $seen " in
    *" ${class}/${name} "*) die "row ${line_no}: ${class}/${name} appears twice" ;;
  esac
  seen="${seen} ${class}/${name}"

  rows+=("${class}"$'\t'"${name}"$'\t'"${file:--}"$'\t'"${sha}"$'\t'"${key}"$'\t'"${version_id}"$'\t'"${origin:--}"$'\t'"${code_sha:--}")
done < "$ARTIFACT_FILE"

[ "${#rows[@]}" -gt 0 ] \
  || die "no artifact rows in ${ARTIFACT_FILE}. A release with no artifacts is a build failure, not an empty manifest."

# Emit one class's block. Quoting below is deliberate: an unquoted all-digit or
# ISO-8601-shaped scalar is implicitly retyped by a YAML parser (int or
# timestamp), and every one of these fields is an opaque identifier that can
# legitimately be all-digit -- a commit sha, an S3 version id, a bare hash.
# Silently changing type is exactly the "manifest that lies" failure this
# script exists to prevent. run_id is deliberately left unquoted: it is meant
# to be read back as an integer, not an opaque string.
emit_class() {
  local want="$1" indent_name="$2" indent_field="$3" printed=0 r
  for r in "${rows[@]}"; do
    split_tsv "$r"
    class="${TSV_F[0]}"; name="${TSV_F[1]}"; file="${TSV_F[2]}"
    sha="${TSV_F[3]}"; key="${TSV_F[4]}"; version_id="${TSV_F[5]}"
    origin="${TSV_F[6]}"; code_sha="${TSV_F[7]}"
    [ "$class" = "$want" ] || continue
    printed=1
    printf '%s%s:\n' "$indent_name" "$name"
    [ "$file" = "-" ] || printf '%sfile: %s\n' "$indent_field" "$file"
    printf '%ssha256: "%s"\n' "$indent_field" "$sha"
    printf '%skey: %s\n' "$indent_field" "$key"
    printf '%sversion_id: "%s"\n' "$indent_field" "$version_id"
    [ "$code_sha" = "-" ] || printf '%scode_sha256: "%s"\n' "$indent_field" "$code_sha"
    [ "$origin" = "-" ] || printf '%sorigin: %s\n' "$indent_field" "$origin"
  done
  return $((1 - printed))
}

printf '%s:\n' "$TAG"
printf '  git_commit: "%s"\n' "$GIT_COMMIT"
printf '  built_at: "%s"\n' "${BUILT_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
printf '  gate:\n'
printf '    check: stacks-ci gate\n'
printf '    run_id: %s\n' "$GATE_RUN_ID"
printf '    conclusion: %s\n' "$GATE_CONCLUSION"
printf '  artifacts:\n'
printf '    bucket: %s\n' "$BUCKET"

# ui_bundle is a single artifact, not a map of them, so it sits one level
# shallower than the three collections.
for r in "${rows[@]}"; do
  split_tsv "$r"
  class="${TSV_F[0]}"; sha="${TSV_F[3]}"; key="${TSV_F[4]}"; version_id="${TSV_F[5]}"
  [ "$class" = "ui_bundle" ] || continue
  printf '    ui_bundle:\n'
  printf '      sha256: "%s"\n' "$sha"
  printf '      key: %s\n' "$key"
  printf '      version_id: "%s"\n' "$version_id"
done

for class in lambdas layers glue_scripts; do
  if printf '%s\n' "${rows[@]}" | grep -q "^${class}"$'\t'; then
    printf '    %s:\n' "$class"
    emit_class "$class" '      ' '        ' || true
  fi
done
