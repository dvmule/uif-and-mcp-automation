"""terraform fmt-style = alignment (extracted from the retired
generate-client-rules.py, 2026-09-17): no terraform binary exists locally,
so this reproduces fmt's contiguous-assignment alignment for the files
under infrastructure/terraform."""

from __future__ import annotations

import re

_TF_ASSIGN_RE = re.compile(r'^(\s*)("[^"]+"|[A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(\S.*)$')

def _value_opens_multiline(value: str) -> bool:
    """True when the assignment's value opens a bracket it doesn't close on
    the same line (e.g. `jsonencode({`, `[`, `{`). terraform fmt excludes
    such attributes from '=' alignment groups."""
    unquoted = re.sub(r'"[^"]*"', '""', value)
    opens = sum(unquoted.count(c) for c in "([{")
    closes = sum(unquoted.count(c) for c in ")]}")
    return opens > closes


def align_terraform(text: str) -> str:
    """terraform fmt's core layout rule: within each run of consecutive
    single-line assignments at the same indentation, pad keys so every '='
    sits one space after the longest key. Runs break on blank lines,
    comments, block open/close, an indent change, or an attribute whose
    value spans multiple lines (those are written with a single space)."""
    lines = text.split("\n")
    out: list[str] = []
    group: list[tuple[str, str, str]] = []

    def flush() -> None:
        if not group:
            return
        width = max(len(key) for _, key, _ in group)
        for indent, key, value in group:
            out.append(f"{indent}{key.ljust(width)} = {value}")
        group.clear()

    for line in lines:
        m = _TF_ASSIGN_RE.match(line)
        if m and _value_opens_multiline(m.group(3)):
            flush()
            out.append(f"{m.group(1)}{m.group(2)} = {m.group(3)}")
            continue
        if m and (not group or group[0][0] == m.group(1)):
            group.append((m.group(1), m.group(2), m.group(3)))
            continue
        flush()
        if m:
            group.append((m.group(1), m.group(2), m.group(3)))
        else:
            out.append(line)
    flush()
    return "\n".join(out)


