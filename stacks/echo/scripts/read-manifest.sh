#!/usr/bin/env bash
# Read one field out of a build tag's release manifest.
#
# The manifest is the message of the annotated tag `release-echo.yml` creates,
# so a promotion's inputs come from the tag itself rather than from a second
# copy of them in a workflow. Two copies of a hash is a drift class, not a
# convenience.
#
# WHY THIS IS A STRICT SHAPE READER AND NOT A YAML PARSER. The manifest has one
# fixed shape, written by release-manifest.sh, and a promotion that reads a
# manifest it does not fully understand is precisely the failure to avoid. A
# general parser would accept a manifest written by something else, in a shape
# this stack has never tested, and hand back plausible values. This accepts the
# shape release-manifest.sh writes and refuses everything else. It also avoids
# depending on PyYAML, which is not a declared dependency here.
#
# WHY IT REFUSES A GATE THAT DID NOT PASS. release-manifest.sh already refuses
# to WRITE such a manifest, so a tag carrying one should not exist. Checking
# again on the read side costs one comparison and covers the case that matters:
# a tag created by hand, or by an older version of the writer, being promoted
# to an environment. Evidence is only evidence if it is checked where it is
# used.
#
# WHY THE FIELD NAMES ARE DISPATCHED STRUCTURALLY, NOT ALLOW-LISTED. apicurio's
# equivalent names all ten of its fields in one `case`. Echo has twenty-six
# artifacts across three collections, which is seventy-eight field names -- a
# list nobody would keep correct. So the CLASS is allow-listed and the artifact
# name is not, and an unknown name falls through to the same "absent is an
# error" refusal a typo in apicurio's list would have hit. The loud-failure
# property is kept; the unmaintainable list is not.
#
# Usage: read-manifest.sh <tag> <field>
#        read-manifest.sh <tag> --list <lambdas|layers|glue_scripts>
#
# --list prints the artifact NAMES in a class, one per line, sorted. It exists
# because a promotion has to iterate the artifacts and this is the only thing
# here that understands manifest shape -- copy-artifacts.sh reading the message
# itself would be the second parser this script's header argues against. The
# names are whatever the manifest holds, so a class that gains an artifact
# needs no change here or in the caller.
#
# Fields:
#   git_commit  built_at  gate.run_id  gate.conclusion  bucket
#   ui_bundle.{sha256,key,version_id}
#   lambdas.<name>.{sha256,key,version_id}
#   layers.<name>.{file,sha256,key,version_id,origin}
#   glue_scripts.<name>.{file,sha256,key,version_id}
#
# Prints the value. Exits non-zero, saying which check failed, on anything else.

set -euo pipefail

tag="${1:-}"
field="${2:-}"

die() { echo "error: $*" >&2; exit 1; }

[ -n "$tag" ] && [ -n "$field" ] \
  || die "usage: read-manifest.sh <tag> <field>"

# An ANNOTATED tag or nothing. A lightweight tag is just a ref: it has no
# message, so it carries no manifest, and reading one would silently yield
# empty fields rather than saying the tag is the wrong kind of object.
object_type="$(git cat-file -t "$tag" 2>/dev/null || true)"
case "$object_type" in
  tag) ;;
  "")  die "${tag} does not exist here. Fetch tags first -- a shallow clone has none of them (release-echo.yml uses fetch-depth: 0 for this reason)." ;;
  commit) die "${tag} is a LIGHTWEIGHT tag, which has no message and therefore no manifest. Only the annotated tags release-echo.yml creates carry one. actions/checkout given a tag ref produces exactly this: re-fetch with git fetch --force origin \"+refs/tags/${tag}:refs/tags/${tag}\"." ;;
  *) die "${tag} resolves to a git ${object_type}, not a tag object." ;;
esac

manifest="$(git tag -l --format='%(contents)' "$tag")"
[ -n "$manifest" ] || die "${tag} is annotated but its message is empty, so there is no manifest to read."

# The manifest's first line is `<tag>:`, which is both a sanity check that we
# are reading what we think we are and a guard against promoting a tag whose
# message was rewritten to describe a different release.
# PARAMETER EXPANSION, NOT `printf | head -1`. That pipeline raced and lost on
# the uat converge of 2026-09-27 (run 36332201702):
#
#   read-manifest.sh: line 78: printf: write error: Broken pipe
#
# `head -1` exits the moment it has its line. bash's printf builtin writes a
# 14KB manifest in chunks, so head can close the read end between two of them,
# the next write gets EPIPE, and `set -o pipefail` turns that into a dead
# script. Nothing was wrong with the manifest -- the same tag had promoted four
# times before. Under 64KB it usually fits one write and the race is invisible,
# which is what makes it the kind of bug that surfaces on the fifth run of
# something and reads like a new problem.
#
# No subprocess, no pipe, no race. The expansion below is the text up to the
# first newline, and an empty manifest was already refused above.
first_line="${manifest%%$'\n'*}"
[ "$first_line" = "${tag}:" ] \
  || die "$(printf '%s\n' \
    "${tag}'s manifest opens with '${first_line}', not '${tag}:'." \
    "  The message does not describe this tag, so nothing in it can be trusted" \
    "  as this release's evidence.")"

# Indentation is the structure, so each accessor is anchored to its own depth.
# `sha256` appears twenty-six times, which is why nothing is addressed by key
# alone -- every lookup is scoped to its parent block first.

# `  <key>: <value>` -- a top-level scalar.
top() {
  printf '%s\n' "$manifest" \
    | grep -E "^  ${1}:[[:space:]]" \
    | head -1 \
    | sed -E "s/^  ${1}:[[:space:]]*//" \
    | sed -E 's/^"(.*)"$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
}

# `  <block>:` then `    <key>: <value>` -- gate, and artifacts.bucket.
nested() {
  printf '%s\n' "$manifest" \
    | sed -n "/^  ${1}:\$/,/^  [a-z_]*:\$/p" \
    | grep -E "^    ${2}:[[:space:]]" \
    | head -1 \
    | sed -E "s/^    ${2}:[[:space:]]*//" \
    | sed -E 's/^"(.*)"$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
}

# `    ui_bundle:` then `      <key>: <value>` -- the one single artifact.
single() {
  printf '%s\n' "$manifest" \
    | sed -n "/^    ${1}:\$/,/^    [a-z_]*:\$/p" \
    | grep -E "^      ${2}:[[:space:]]" \
    | head -1 \
    | sed -E "s/^      ${2}:[[:space:]]*//" \
    | sed -E 's/^"(.*)"$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
}

# `    <class>:` then `      <name>:` then `        <key>: <value>`.
# The inner range terminator is another six-space key OR a four-space key, so
# the scan stops at the next artifact AND at the end of the class -- without
# the second alternative, a lookup for the last artifact of a class would run
# on into the next class and could match its first entry's field.
deep() {
  printf '%s\n' "$manifest" \
    | sed -n "/^    ${1}:\$/,\$p" \
    | sed -n "/^      ${2}:\$/,/^\(      [a-z0-9_]*\|    [a-z_]*\):\$/p" \
    | grep -E "^        ${3}:[[:space:]]" \
    | head -1 \
    | sed -E "s/^        ${3}:[[:space:]]*//" \
    | sed -E 's/^"(.*)"$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
}

# The artifact NAMES in a class: six-space keys with no value, inside the
# four-space class block. Shared by --list and --dump so the two cannot
# disagree about what a class contains.
class_names() {
  printf '%s\n' "$manifest" \
    | sed -n "/^    ${1}:\$/,/^    [a-z_]*:\$/p" \
    | grep -E '^      [a-z0-9_]+:$' \
    | sed -E 's/^      ([a-z0-9_]+):$/\1/' \
    | sort
}

# The shape rules the writer enforces, checked again here for the same reason
# the gate is: a manifest is only evidence where it is used.
#
# Extracted from the single-field path so --dump applies the IDENTICAL checks.
# Written inline in both places they would drift, and a --dump that validated
# less than a field read would be the more dangerous of the two, because it is
# the one a promotion uses for all 27 artifacts at once.
#
# $1 field label (for messages)  $2 leaf key  $3 value  $4 sibling sha256
check_leaf() {
  local label="$1" leaf="$2" value="$3" sibling="${4:-}"

  # Absent is an ERROR, never an empty string. An empty hash passed to
  # `terraform apply -var artifact_shas=...` is a variable that is set and
  # wrong, and Terraform cannot tell it from a deliberate value.
  [ -n "$value" ] \
    || die "${label} is absent from ${tag}'s manifest, or the manifest is not in the shape release-manifest.sh writes."

  case "$leaf" in
    sha256)
      printf '%s' "$value" | grep -Eq '^[0-9a-f]{64}$' \
        || die "${label} is '${value}', not 64 lowercase hex characters with no prefix. A truncated or differently-cased hash is not something an environment can be pinned to." ;;

    # base64 of a 32-byte digest: 43 characters plus one pad. A hex digest here
    # would be silently accepted by Terraform and then never match, producing a
    # permanent diff with no explanation.
    code_sha256)
      printf '%s' "$value" | grep -Eq '^[A-Za-z0-9+/]{43}=$' \
        || die "${label} is '${value}', not base64 of a sha256 digest. Terraform's source_code_hash takes base64, not hex." ;;

    # The key IS the hash, so a key that does not contain one is either the
    # wrong object or a hand-edited manifest.
    key)
      [ -n "$sibling" ] \
        || die "${label} has no sibling sha256 in ${tag}'s manifest, so the key cannot be checked against its own hash."
      case "$value" in
        *"$sibling"*) ;;
        *) die "${label} is '${value}', which does not contain its own sha256 '${sibling}'. Content-addressed artifacts store at their hash; a key that disagrees is not the artifact this entry describes." ;;
      esac ;;
  esac
}

# Checked on EVERY read, not only when the caller asks for it. A promotion that
# reads a hash and never looks at the gate would deploy a build whose evidence
# says it failed.
conclusion="$(nested gate conclusion)"
[ "$conclusion" = "success" ] \
  || die "$(printf '%s\n' \
    "${tag}'s manifest records the stacks-ci gate as '${conclusion:-<absent>}', not 'success'." \
    "  Refusing to hand out any field from it: a build whose gate did not pass" \
    "  must not be promoted, and release-manifest.sh should never have written" \
    "  this. Re-cut the tag from a commit with a green gate.")"

# --list, handled before the field dispatch because it answers with a set
# rather than a value and so shares none of the leaf checks below. It DOES sit
# after the gate check, deliberately: enumerating a failed build's artifacts is
# the first step of promoting one.
if [ "$field" = "--list" ]; then
  class="${3:-}"
  case "$class" in
    lambdas|layers|glue_scripts) ;;
    "") die "--list needs a class. Known: lambdas layers glue_scripts" ;;
    ui_bundle) die "ui_bundle is a single artifact, not a class -- read ui_bundle.sha256 directly." ;;
    *) die "unknown class '${class}'. Known: lambdas layers glue_scripts" ;;
  esac

  names="$(class_names "$class")"

  # Absent is an error here for the same reason it is below: an empty list
  # makes a promotion loop over nothing and report success having copied
  # nothing at all.
  [ -n "$names" ] \
    || die "${tag}'s manifest lists no artifacts under '${class}'. Either the class is empty, which release-manifest.sh does not write, or the manifest is not in the shape it writes."

  printf '%s\n' "$names"
  exit 0
fi

# --dump: every artifact, one row each, fully validated, in ONE invocation.
#
# WHY THIS EXISTS. copy-artifacts.sh needs four to six fields for each of 27
# artifacts. Asking for them one at a time is ~108 invocations of this script,
# each re-reading the tag through git: measured at 2.5s on Windows, so four and
# a half minutes to promote one release. A step that slow gets skipped, and the
# promotion model depends on it not being skipped.
#
# The alternative was for copy-artifacts.sh to parse the manifest itself, which
# is the second parser this script's header exists to prevent. So the bulk read
# lives here, beside the shape knowledge, and every field goes through the same
# check_leaf the single-field path uses.
#
# MEASURED, NOT ASSUMED: --dump is ~84s on Windows, against 4.5 minutes for the
# per-field equivalent. The remaining cost is process spawns -- the accessors
# below run four or five sed/grep pipelines per field, so 27 artifacts is ~600
# processes, which Git Bash makes expensive and Linux does not. It could be one
# awk pass over the manifest instead, and that is deliberately NOT done: it
# would be a second shape reader inside the one file whose correctness the whole
# promotion rests on, to save a minute on a step that runs a handful of times.
# Revisit only if the artifact count grows by an order of magnitude.
#
# Format is stage-artifacts.sh's TSV exactly -- class, name, file, sha256, key,
# version_id, origin, code_sha256, with `-` where a class has no such field --
# so the promote and the dev stage emit one format and the same awk reads both.
if [ "$field" = "--dump" ]; then
  dump_row() {
    local class="$1" name="$2"
    local sha key vid file origin code label

    # NOT `label="${class}$([ -n "$name" ] && printf '.%s' "$name")"`. With an
    # empty name the test fails, the command substitution exits 1, and under
    # set -e the assignment takes the whole script down -- which it did, after
    # 26 good rows, with an exit status and no message. ui_bundle is the only
    # artifact with an empty name, so it was the only one affected.
    if [ -n "$name" ]; then label="${class}.${name}"; else label="$class"; fi

    if [ "$class" = "ui_bundle" ]; then
      sha="$(single ui_bundle sha256)"
      key="$(single ui_bundle key)"
      vid="$(single ui_bundle version_id)"
    else
      sha="$(deep "$class" "$name" sha256)"
      key="$(deep "$class" "$name" key)"
      vid="$(deep "$class" "$name" version_id)"
    fi

    check_leaf "${label}.sha256"     sha256     "$sha"
    check_leaf "${label}.key"        key        "$key" "$sha"
    check_leaf "${label}.version_id" version_id "$vid"

    file="-"; origin="-"; code="-"
    case "$class" in
      lambdas)
        code="$(deep "$class" "$name" code_sha256)"
        check_leaf "${label}.code_sha256" code_sha256 "$code" ;;
      layers)
        file="$(deep "$class" "$name" file)"
        origin="$(deep "$class" "$name" origin)"
        code="$(deep "$class" "$name" code_sha256)"
        check_leaf "${label}.file"        file        "$file"
        check_leaf "${label}.origin"      origin      "$origin"
        check_leaf "${label}.code_sha256" code_sha256 "$code" ;;
      glue_scripts)
        file="$(deep "$class" "$name" file)"
        check_leaf "${label}.file" file "$file" ;;
    esac

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$class" "${name:-ui_bundle}" "$file" "$sha" "$key" "$vid" "$origin" "$code"
  }

  for dump_class in glue_scripts lambdas layers; do
    dump_names="$(class_names "$dump_class")"
    [ -n "$dump_names" ] \
      || die "${tag}'s manifest lists no artifacts under '${dump_class}'."
    while IFS= read -r dump_name; do
      [ -n "$dump_name" ] || continue
      dump_row "$dump_class" "$dump_name"
    done <<< "$dump_names"
  done

  # The one single artifact, easy to forget because it is not in a class and
  # the dev deploy publishes it directly rather than promoting it.
  dump_row ui_bundle ""
  exit 0
fi

# Split the field on dots without a subshell, so an unknown shape is named
# rather than silently yielding empty.
f1="${field%%.*}"
rest="${field#*.}"
if [ "$rest" = "$field" ]; then rest=""; fi
f2="${rest%%.*}"
f3="${rest#*.}"
if [ "$f3" = "$rest" ]; then f3=""; fi

case "$field" in
  git_commit)      value="$(top git_commit)" ;;
  built_at)        value="$(top built_at)" ;;
  gate.run_id)     value="$(nested gate run_id)" ;;
  gate.conclusion) value="$conclusion" ;;
  bucket)          value="$(nested artifacts bucket)" ;;

  ui_bundle.*)
    case "$f2" in
      sha256|key|version_id) value="$(single ui_bundle "$f2")" ;;
      *) die "unknown ui_bundle field '${f2}'. Known: sha256 key version_id" ;;
    esac ;;

  lambdas.*.*|layers.*.*|glue_scripts.*.*)
    [ -n "$f2" ] && [ -n "$f3" ] \
      || die "field '${field}' is missing an artifact name or a key. Shape: <class>.<name>.<key>"
    printf '%s' "$f2" | grep -Eq '^[a-z0-9_]+$' \
      || die "artifact name '${f2}' is not an underscore-safe identifier. The manifest keys artifacts by identifier and carries the real filename in the 'file' field."
    case "$f3" in
      file|sha256|key|version_id|origin|code_sha256) ;;
      *) die "unknown field '${f3}' for ${f1}. Known: file sha256 key version_id origin code_sha256" ;;
    esac
    value="$(deep "$f1" "$f2" "$f3")" ;;

  lambdas|layers|glue_scripts|artifacts|gate|ui_bundle)
    die "'${field}' is a block, not a field. Ask for a leaf: e.g. ${field}.<name>.sha256" ;;

  *) die "unknown field '${field}'. Known: git_commit built_at gate.run_id gate.conclusion bucket ui_bundle.<key> and <class>.<name>.<key> for class in lambdas layers glue_scripts" ;;
esac

# The leaf key is the last dotted component -- f3 for <class>.<name>.<key>,
# f2 for ui_bundle.<key>, and empty for a top-level field.
leaf_key="${f3:-$f2}"

sibling=""
if [ "$leaf_key" = "key" ]; then
  sibling="$(
    case "$f1" in
      ui_bundle) single ui_bundle sha256 ;;
      *)         deep "$f1" "$f2" sha256 ;;
    esac
  )"
fi

check_leaf "$field" "$leaf_key" "$value" "$sibling"

if [ "$field" = "git_commit" ]; then
  printf '%s' "$value" | grep -Eq '^[0-9a-f]{40}$' \
    || die "git_commit is '${value}', not a full 40-character sha."
fi

printf '%s\n' "$value"
