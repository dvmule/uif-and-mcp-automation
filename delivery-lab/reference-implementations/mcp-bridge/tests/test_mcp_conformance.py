"""mcp_conformance.py - initialize + tools/list with a BOUNDED retry for gateway
propagation. The MCP endpoint is faked at the `call` boundary; no network."""
import json
import urllib.error

import pytest

import mcp_conformance as mc

TOOLS = ["get_prices", "get_prices_productid", "post_prices_query"]


@pytest.fixture
def metadata(tmp_path):
    p = tmp_path / "mcp-metadata.json"
    p.write_text(json.dumps({"tools": [{"name": n} for n in TOOLS]}), encoding="utf-8")
    return str(p)


def _fake_endpoint(monkeypatch, tool_sets):
    """Each tools/list answers with the next entry of tool_sets (an Exception entry
    is raised instead). initialize/notifications always succeed."""
    calls = {"list": 0, "sleeps": []}
    answers = list(tool_sets)

    def call(url, payload, session_id=None, timeout=30):
        if payload["method"] == "initialize":
            return {"result": {"serverInfo": {"name": "fake", "version": "1"}}}, "sid-1"
        if payload["method"] == "notifications/initialized":
            return None, session_id
        calls["list"] += 1
        answer = answers.pop(0) if answers else tool_sets[-1]
        if isinstance(answer, Exception):
            raise answer
        return {"result": {"tools": [{"name": n} for n in answer]}}, session_id

    monkeypatch.setattr(mc, "call", call)
    monkeypatch.setattr(mc.time, "sleep", lambda s: calls["sleeps"].append(s))
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    return calls


def test_single_attempt_conformant_without_retry_flags(metadata, monkeypatch, capsys):
    calls = _fake_endpoint(monkeypatch, [TOOLS])
    assert mc.main(["--mcp-url", "https://gw/priceTest/mcp", "--expected-metadata", metadata, "--execute"]) == 0
    assert calls["list"] == 1 and calls["sleeps"] == []
    assert "CONFORMANT: deployed tools match the contract-derived metadata exactly." in capsys.readouterr().out


def test_stale_tool_set_then_propagated_passes_within_the_bound(metadata, monkeypatch, capsys):
    """The gateway still serves the previous tool set on the first two attempts."""
    calls = _fake_endpoint(monkeypatch, [["get_prices"], ["get_prices"], TOOLS])
    rc = mc.main(["--mcp-url", "https://gw/priceTest/mcp", "--expected-metadata", metadata, "--execute",
                  "--retries", "5", "--retry-delay", "10"])
    assert rc == 0 and calls["list"] == 3 and calls["sleeps"] == [10.0, 10.0]
    out = capsys.readouterr().out
    assert "attempt 1 of 6: tool set differs (missing: get_prices_productid, post_prices_query; unexpected: -) - retrying in 10s" in out
    assert "(attempt 3 of 6)" in out


def test_transport_error_during_propagation_is_retried(metadata, monkeypatch, capsys):
    calls = _fake_endpoint(monkeypatch, [urllib.error.URLError("connection refused"), TOOLS])
    rc = mc.main(["--mcp-url", "https://gw/priceTest/mcp", "--expected-metadata", metadata, "--execute",
                  "--retries", "2", "--retry-delay", "3"])
    assert rc == 0 and calls["list"] == 2 and calls["sleeps"] == [3.0]
    assert "tools/list failed" in capsys.readouterr().out


def test_retry_budget_is_bounded_and_the_final_attempt_decides(metadata, monkeypatch, capsys):
    calls = _fake_endpoint(monkeypatch, [["get_prices"]])
    rc = mc.main(["--mcp-url", "https://gw/priceTest/mcp", "--expected-metadata", metadata, "--execute",
                  "--retries", "3", "--retry-delay", "1"])
    assert rc == 1 and calls["list"] == 4 and calls["sleeps"] == [1.0, 1.0, 1.0]  # never a 4th sleep
    out = capsys.readouterr().out
    assert "NOT CONFORMANT after 4 attempt(s): tool set differs" in out and "MISSING: get_prices_productid, post_prices_query" in out


def test_without_retries_a_mismatch_fails_immediately(metadata, monkeypatch):
    calls = _fake_endpoint(monkeypatch, [["get_prices"]])
    assert mc.main(["--mcp-url", "https://gw/priceTest/mcp", "--expected-metadata", metadata, "--execute"]) == 1
    assert calls["list"] == 1 and calls["sleeps"] == []


def test_dry_run_never_connects_even_with_retries(metadata, monkeypatch, capsys):
    monkeypatch.setattr(mc, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    assert mc.main(["--mcp-url", "https://gw/priceTest/mcp", "--expected-metadata", metadata, "--retries", "6"]) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN: no connection made." in out and "bounded retry: up to 6 extra attempt(s), 10s apart (max 60s)" in out


def test_negative_retry_values_are_refused(metadata):
    with pytest.raises(SystemExit):
        mc.main(["--mcp-url", "u", "--expected-metadata", metadata, "--retries", "-1"])
