"""MCP runtime conformance — initialize + tools/list against a deployed Bridge.

Speaks MCP streamable HTTP (the transport in the generated metadata):
POST <mcp-url> with Accept: application/json, text/event-stream;
`initialize` -> `notifications/initialized` -> `tools/list`, replaying the
Mcp-Session-Id header; handles both plain-JSON and SSE response bodies.
The returned tool list is diffed against the GENERATED mcp-metadata.json —
proving the deployed Bridge serves exactly the contract-derived tools.

DELIBERATELY ABSENT: tools/call — there is no code path that can invoke a
tool. Listing is the maximum this client can do. (A tools/call canary is a
separate, explicitly approved step.)

Read-only protocol traffic, but it does contact the given URL — so it too
defaults to --dry-run (prints what it would call) and requires --execute plus
MCP_LIVE_ENABLED=true to actually connect. stdlib only.

BOUNDED RETRY (2026-09-15): Flex Gateway pulls a Bridge's configuration from
API Manager asynchronously, so right after a CREATE/UPDATE the endpoint may
still answer with the previous tool set (or not yet at all). --retries N
--retry-delay S re-runs the whole initialize + tools/list check up to N more
times, S seconds apart, ONLY for that propagation window: a transport error,
a JSON-RPC error, or a tool-set mismatch. Total wait is bounded by N*S; the
final attempt's result is the verdict. Default: no retry (single attempt).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def parse_body(text):
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    datas = [ln[5:].strip() for ln in text.splitlines() if ln.strip().startswith("data:")]
    if not datas:
        raise ValueError("unrecognized response body (not JSON, no SSE data lines)")
    return json.loads(datas[-1])


def call(url, payload, session_id=None, timeout=30):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id") or session_id
        raw = r.read().decode(errors="replace")
    body = parse_body(raw) if raw.strip() else None
    if body and body.get("error"):
        raise RuntimeError("JSON-RPC error: %s" % json.dumps(body["error"]))
    return body, sid


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mcp-url", required=True, help="https://<gateway host>/<bridge base path>/mcp")
    ap.add_argument("--expected-metadata", required=True, help="generated mcp-metadata.json to diff against")
    ap.add_argument("--retries", type=int, default=0,
                    help="additional attempts when the gateway has not yet propagated the new configuration (default 0)")
    ap.add_argument("--retry-delay", type=float, default=10.0, help="seconds between attempts (default 10)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    g.add_argument("--execute", dest="dry_run", action="store_false")
    a = ap.parse_args(argv)
    if a.retries < 0 or a.retry_delay < 0:
        sys.exit("REFUSED: --retries and --retry-delay must not be negative.")

    expected = json.load(open(a.expected_metadata, encoding="utf-8-sig"))
    expected_names = sorted(t["name"] for t in expected.get("tools") or [])
    print("== MCP conformance: initialize + tools/list (tools/call impossible by construction) ==")
    print("target: POST %s" % a.mcp_url)
    print("expected tools (from the generated metadata): %s" % ", ".join(expected_names))
    if a.retries:
        print("bounded retry: up to %d extra attempt(s), %.0fs apart (max %.0fs) for gateway propagation only"
              % (a.retries, a.retry_delay, a.retries * a.retry_delay))
    if a.dry_run:
        print("DRY-RUN: no connection made.")
        return 0
    if os.environ.get("MCP_LIVE_ENABLED") != "true":
        sys.exit("REFUSED: --execute requires MCP_LIVE_ENABLED=true.")

    attempts = a.retries + 1
    for attempt in range(1, attempts + 1):
        ok, reason = check_once(a.mcp_url, expected_names)
        if ok:
            print("CONFORMANT: deployed tools match the contract-derived metadata exactly%s."
                  % ("" if attempt == 1 else " (attempt %d of %d)" % (attempt, attempts)))
            return 0
        if attempt < attempts:
            print("attempt %d of %d: %s - retrying in %.0fs (gateway configuration propagation)"
                  % (attempt, attempts, reason, a.retry_delay))
            time.sleep(a.retry_delay)
    print("NOT CONFORMANT after %d attempt(s): %s" % (attempts, reason))
    return 1


def check_once(mcp_url, expected_names):
    """One full initialize -> notifications/initialized -> tools/list pass.
    Returns (True, None) or (False, reason) - never raises for transport / protocol
    conditions the bounded retry is meant to absorb."""
    try:
        init, sid = call(mcp_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": "uif-mcp-conformance", "version": "1.0"},
            "capabilities": {}}})
    except (urllib.error.URLError, RuntimeError, ValueError, OSError) as e:
        return False, "initialize failed: %s" % e
    info = (init or {}).get("result", {}).get("serverInfo", {})
    print("initialize OK: server=%s %s session=%s" % (info.get("name"), info.get("version"), sid or "(none)"))
    try:
        call(mcp_url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    except Exception as e:  # some gateways skip the notification requirement
        print("notifications/initialized rejected (%s) — continuing" % e)
    try:
        listed, _ = call(mcp_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
    except (urllib.error.URLError, RuntimeError, ValueError, OSError) as e:
        return False, "tools/list failed: %s" % e
    got = sorted(t["name"] for t in (listed or {}).get("result", {}).get("tools") or [])
    print("tools/list returned: %s" % ", ".join(got))
    missing = [n for n in expected_names if n not in got]
    extra = [n for n in got if n not in expected_names]
    if missing or extra:
        if missing:
            print("MISSING: %s" % ", ".join(missing))
        if extra:
            print("UNEXPECTED: %s" % ", ".join(extra))
        return False, "tool set differs (missing: %s; unexpected: %s)" % (", ".join(missing) or "-", ", ".join(extra) or "-")
    return True, None


if __name__ == "__main__":
    sys.exit(main())
