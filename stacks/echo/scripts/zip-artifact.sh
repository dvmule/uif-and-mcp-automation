#!/usr/bin/env bash
# Write a DETERMINISTIC zip of a staged artifact tree, and print the zip's
# sha256 in the base64 form Terraform's source_code_hash expects.
#
# WHY NOT `zip`. A zip stores each entry's mtime, its external attributes and
# whatever order the archiver walked the tree in. All three vary between a
# runner and a laptop, and between two runs on the same machine, so `zip` gives
# a different archive for identical source. `zip -X` drops some extra fields
# and still keeps mtimes.
#
# That matters for two reasons, and only the second is obvious:
#
#   1. source_code_hash would change on every build, so two build tags of
#      identical source would each show a Lambda update -- a diff with no
#      change behind it, on a plan a human is meant to read.
#   2. The promotion model requires four consecutive build tags of unchanged
#      source to produce identical artifacts. A non-reproducible archive makes
#      that check fail for a reason that has nothing to do with the code.
#
# So: fixed timestamp (1980-01-01, the zip epoch -- the earliest a zip can
# represent), fixed permissions, entries added in LC_ALL=C sorted order,
# deflate level 9. The result depends on file contents and paths only.
#
# The ARTIFACT IDENTITY is still artifact-hash.sh's tree hash, not this value.
# This exists so Terraform can detect out-of-band drift: without
# source_code_hash, a hand-run UpdateFunctionCode is invisible to a plan.
#
# Usage:
#   zip-artifact.sh <staged-dir> <output.zip>
#
# Prints: the zip's sha256, base64-encoded (Terraform's source_code_hash form).
# Exit: 0 on success, 1 on any refusal.

set -euo pipefail

die() { printf '%s: %s\n' "zip-artifact.sh" "$1" >&2; exit 1; }

src="${1:?usage: zip-artifact.sh <staged-dir> <output.zip>}"
out="${2:?usage: zip-artifact.sh <staged-dir> <output.zip>}"

[ -d "$src" ] || die "${src} is not a directory"

# `python3` on Linux and macOS, `py` on Windows -- the same split the repo's
# own docs state. Resolve to one that can actually import zipfile rather than
# the first name on PATH.
PY=""
for candidate in python3 py python; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c "import zipfile" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
[ -n "$PY" ] || die "no python on PATH that can import zipfile (tried python3, py, python)"

"$PY" - "$src" "$out" <<'PY'
import hashlib
import os
import sys
import zipfile

src, out = sys.argv[1], sys.argv[2]

# The zip epoch. Any fixed value works; this is the earliest a zip can store,
# so it is unambiguously "not a real timestamp" to anyone who looks.
FIXED = (1980, 1, 1, 0, 0, 0)

paths = []
for root, dirs, files in os.walk(src):
    dirs.sort()
    for name in files:
        full = os.path.join(root, name)
        rel = os.path.relpath(full, src).replace(os.sep, "/")
        paths.append((rel, full))

if not paths:
    sys.exit("zip-artifact.sh: no files under %s -- an empty artifact is a "
             "build failure, not an empty zip" % src)

# Byte-wise sort on the archive path, matching LC_ALL=C so this agrees with
# artifact-hash.sh's ordering on every machine.
paths.sort(key=lambda p: p[0].encode("utf-8"))

os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for rel, full in paths:
        info = zipfile.ZipInfo(filename=rel, date_time=FIXED)
        # 0o644 in the high 16 bits, the form a zip stores a unix mode in.
        # Lambda needs the file readable; it does not need it executable.
        info.external_attr = (0o644 & 0xFFFF) << 16
        info.create_system = 3  # unix, so the mode above is honoured
        with open(full, "rb") as fh:
            zf.writestr(info, fh.read())

digest = hashlib.sha256()
with open(out, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        digest.update(chunk)

import base64
print(base64.b64encode(digest.digest()).decode("ascii"))
PY
