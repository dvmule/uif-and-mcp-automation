# xml_paths.py (UIFPE-557)
# Target-path grammar for partner-shaped XML output. A mapping's target may
# be a PATH instead of a plain name: "/" nests elements, a final "@" names an
# attribute — `Identifiers/ISBN13` and `Title@lang`. This module is the whole
# grammar in one pure place: parsing, the validation issues the wizard's
# Check/Validate surfaces, and the nesting plan the Glue job turns into
# nested structs for spark-xml. No pyspark here (the engine imports without
# Spark — API Lambda and test-suite invariant); the Spark half lives in
# beacon_export_job.py, quarantined like every other pyspark use.
#
# A plain name (no "/" or "@") is NOT a path and behaves exactly as today —
# a config of plain names never triggers any of this, which is what keeps
# every published version byte-identical through this feature.

from __future__ import annotations

import re

# Pragmatic XML name subset (NCName without the exotic unicode ranges): the
# names partners actually use. No ":" — namespaces are out of Phase 1 scope.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")

# spark-xml's conventions, mirrored by the job's struct assembly.
ATTRIBUTE_PREFIX = "_"
VALUE_TAG = "_VALUE"


def is_path(name: str) -> bool:
    """True when the target uses path syntax at all."""
    return isinstance(name, str) and ("/" in name or "@" in name)


def parse_target_path(name: str) -> tuple[tuple[str, ...], str | None]:
    """(element segments, attribute or None) for a path target.

    Raises ValueError with a human-readable reason — the caller turns it
    into a validation issue naming the offending target."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("target is empty")
    if name.count("@") > 1:
        raise ValueError(f"{name!r}: at most one '@' (one attribute) per target")
    body, _, attr = name.partition("@")
    attribute = attr if attr or "@" in name else None
    if "@" in name and not attr:
        raise ValueError(f"{name!r}: '@' must be followed by an attribute name")
    if "@" in name and "/" in attr:
        raise ValueError(f"{name!r}: the attribute must be the last part — "
                         "'@' after the final '/'")
    if not body:
        raise ValueError(f"{name!r}: an attribute needs an element to sit on "
                         "(write Element@attribute)")
    segments = body.split("/")
    for seg in segments:
        if not seg:
            raise ValueError(f"{name!r}: empty path segment (check for '//' "
                             "or a leading/trailing '/')")
        if not _NAME_RE.match(seg):
            raise ValueError(f"{name!r}: {seg!r} is not a valid XML element "
                             "name (letters, digits, '_', '-', '.'; must not "
                             "start with a digit)")
    if attribute is not None and not _NAME_RE.match(attribute):
        raise ValueError(f"{name!r}: {attribute!r} is not a valid XML "
                         "attribute name")
    return tuple(segments), attribute


def path_issues(names) -> list[str]:
    """Every problem in a set of target names, each as one plain sentence.

    Syntax first, then structural conflicts. Only names using path syntax
    are parsed; plain names participate only in conflict checks (a plain
    `Title` next to `Title/Sub` is still mixed content). A set with no path
    targets returns [] without further work — the legacy fast path."""
    if not any(is_path(n) for n in names):
        return []
    issues: list[str] = []
    parsed: dict[str, tuple[tuple[str, ...], str | None]] = {}
    for n in names:
        if not isinstance(n, str):
            continue
        try:
            parsed[n] = parse_target_path(n) if is_path(n) else ((n,), None)
        except ValueError as e:
            issues.append(str(e))
    # element paths that carry a VALUE (a text leaf), and every path that
    # wraps DEEPER targets (strict ancestors only — an element holding an
    # attribute is not a wrapper, so `Title` + `Title@lang` is legal text
    # plus attribute, while `Title` + `Title/Sub` is mixed content)
    value_paths = {segs for (segs, attr) in parsed.values() if attr is None}
    wrapper_paths: set[tuple[str, ...]] = set()
    for segs, _attr in parsed.values():
        for i in range(1, len(segs)):
            wrapper_paths.add(segs[:i])
    for segs in sorted(value_paths & wrapper_paths):
        issues.append(
            f"{'/'.join(segs)!r} is used both as a value and as a wrapper "
            "for deeper targets — mixed content is not supported; rename "
            "one of them (an attribute on it is fine)")
    return issues


def any_path(names) -> bool:
    return any(is_path(n) for n in names if isinstance(n, str))


def build_plan(names) -> dict:
    """The nesting tree the job assembles into Spark structs.

    Node shape: {"children": {segment: node}, "attrs": {attr: column},
    "value": column | None} — `column` is the ORIGINAL target string, which
    is the canonical parquet column's exact name. Call only on a set that
    path_issues() said is clean."""
    root = {"children": {}, "attrs": {}, "value": None}
    for n in names:
        segs, attr = parse_target_path(n) if is_path(n) else ((n,), None)
        node = root
        walk = segs if attr is not None else segs[:-1]
        for seg in walk:
            node = node["children"].setdefault(
                seg, {"children": {}, "attrs": {}, "value": None})
        if attr is not None:
            node["attrs"][attr] = n
        else:
            leaf = node["children"].setdefault(
                segs[-1], {"children": {}, "attrs": {}, "value": None})
            leaf["value"] = n
    return root
