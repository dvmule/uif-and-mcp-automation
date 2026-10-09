"""reconcile_domains.py - the local one-shot equivalent of mcp.yml + mcp-deploy.yml.

Offline: a synthetic repository with two domains that are NOT Product/Price (so nothing
can be hardcoded), the test-owned platform config, and the Anypoint fake serving the
whole clean-slate lifecycle (REST assets, external instances, shared MCP asset, Bridge
CREATE then ADD, the MCP endpoint for conformance). Proves discovery from the filesystem,
serial processing, order independence, idempotent re-run, stop-at-first-failure, fail-closed
configuration, zero-network dry-run, write-free preflight, and the alignment of the step
sequence with the workflow.
"""
import json
import re
import urllib.request
from pathlib import Path

import pytest
import yaml

import affected_domains as ad
import reconcile_domains as rd
import shared_bridge as sb
from anypoint_fake import NEW_ID, PLATFORM, SHARED_LABEL, SHARED_MCP, AnypointFake, synthetic_domain

REPO = Path(__file__).resolve().parents[4]
WF = REPO / ".github" / "workflows" / "mcp-deploy.yml"
UP = {"orders": "https://upstream.example.test/orders/", "customer": "https://upstream.example.test/customer/"}


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    """A repository tree with two contract-bearing domains (alphabetical: customer, orders)."""
    root = tmp_path_factory.mktemp("repo")
    synthetic_domain(root / "domains" / "customer", "customer", "customers", UP["customer"])
    synthetic_domain(root / "domains" / "orders", "orders", "orders", UP["orders"])
    (root / "domains" / "stock" / "src" / "mcp").mkdir(parents=True)  # no contract -> never discovered
    return root


def _args(repo, out, mode, domains="all", extra=()):
    return ["--environment", "dev", "--domains", domains, "--repo-root", str(repo), "--out", str(out), "--platform-config", PLATFORM, mode, *extra]


def _live(monkeypatch):
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")


def _routes(fake):
    return [r["label"] for r in fake.bridge["inst"]["routing"]]


def _tools(fake, label):
    uid = next(r for r in fake.bridge["inst"]["routing"] if r["label"] == label)["upstreams"][0]["id"]
    pol = next(p for p in fake.bridge["pols"]["policies"] if p.get("upstreamId") == uid)
    return sorted(t["name"] for t in pol["configuration"]["tools"])


# ── discovery ─────────────────────────────────────────────────────────────────────

def test_domains_all_discovers_from_the_filesystem_and_nothing_is_hardcoded(repo):
    assert ad.discover(str(repo)) == ["customer", "orders"]
    src = Path(rd.__file__).read_text(encoding="utf-8")
    for name in ("product", "price", "customer", "orders", "stock"):
        assert name not in src.lower(), name
    real = ad.discover(str(REPO))
    assert "product" in real and "price" in real  # the real repository's two domains are found the same way


# ── clean slate: Bridge created by the first domain, second domain ADDed, order-independent ──

def test_clean_slate_execute_builds_the_shared_platform_serially(repo, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(rest_assets={}, mcp_assets={})  # nothing exists in Anypoint
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert rd.main(_args(repo, tmp_path / "out", "--execute")) == 0
    out = capsys.readouterr().out
    assert "order (serial, stop at the first failure): customer -> orders" in out
    kinds = fake.kinds()
    first = kinds.index("create")
    # customer (first): REST 1.0.0 published, external instance, shared asset 1.0.0, Bridge CREATE
    assert kinds[:first + 1] == ["rest-asset", "external", "mcp-asset", "create"]
    assert fake.writes[0][1]["asset"] == "customer-api" and fake.writes[0][1]["version"] == "1.0.0"
    assert fake.writes[1][1]["name"] == "uif-customer-dev" and fake.writes[2][1]["version"] == "1.0.0"
    assert fake.writes[3][1]["instanceLabel"] == SHARED_LABEL and fake.writes[3][1]["spec"]["assetId"] == SHARED_MCP
    # orders (second): REST 1.0.0, external instance, shared asset 1.0.1 (merged), ADD via A1..A4 - the SAME Bridge
    assert kinds[first + 1:] == ["rest-asset", "external", "mcp-asset", "post-upstream", "patch-instance", "post-outbound-policy", "patch-policy"]
    assert fake.writes[first + 1][1]["asset"] == "orders-api" and fake.writes[first + 3][1]["version"] == "1.0.1"
    assert sb.connection_versions(fake.writes[first + 3][1]["metadata"]) == {"customer-api": "1.0.0", "orders-api": "1.0.0"}
    assert fake.bridge["inst"]["id"] == NEW_ID and _routes(fake) == ["customer-api", "orders-api"]
    assert _tools(fake, "customer-api") == ["get_customers", "get_customers_sku"] and _tools(fake, "orders-api") == ["get_orders", "get_orders_sku"]
    assert fake.bridge["inst"]["assetVersion"] == "1.0.1"
    # conformance ran against the fake gateway twice (once per domain) and saw the merged tool set the second time
    assert fake.mcp_calls.count("tools/list") == 2 and "CONFORMANT" in out
    assert "route customer-api PRESERVED" in out and "ADDED in place" in out and "CREATED instance %s" % NEW_ID in out
    for line in ("  customer     customer-api@1.0.0 (initial)", "  orders       orders-api@1.0.0 (initial)"):
        assert line in out
    assert "s3cr3t-VALUE-never-printed" not in out


def test_reverse_order_yields_the_same_final_state(repo, tmp_path, monkeypatch):
    _live(monkeypatch)
    fake = AnypointFake(rest_assets={}, mcp_assets={})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert rd.main(_args(repo, tmp_path / "out", "--execute", domains="orders,customer")) == 0
    # explicit lists are still processed in a deterministic (sorted) order; whichever domain is first CREATEs, the other ADDs
    assert fake.kinds().count("create") == 1 and fake.kinds().count("post-upstream") == 1
    assert sorted(_routes(fake)) == ["customer-api", "orders-api"]
    assert _tools(fake, "customer-api") == ["get_customers", "get_customers_sku"] and _tools(fake, "orders-api") == ["get_orders", "get_orders_sku"]
    assert sb.connection_versions(json.loads(fake.mcp_assets["1.0.1"].decode())) == {"customer-api": "1.0.0", "orders-api": "1.0.0"}


def test_second_run_is_a_noop_with_reads_only(repo, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(rest_assets={}, mcp_assets={})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert rd.main(_args(repo, tmp_path / "run1", "--execute")) == 0
    fake.record.clear(); fake.writes.clear(); fake.mcp_calls.clear()
    assert rd.main(_args(repo, tmp_path / "run2", "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.writes == [] and fake.methods() == {"GET", "POST"}  # POST only to the MCP endpoint / token
    assert all(p.endswith("/oauth2/token") or p.endswith("/mcp") for m, p in fake.record if m == "POST")
    assert out.count("decision : noop") == 2 and out.count("NO-OP") >= 2 and out.count("Version already exists with IDENTICAL content") == 2
    assert _routes(fake) == ["customer-api", "orders-api"] and len(fake.bridge["ups"]["upstreams"]) == 2  # no duplicates
    assert len([p for p in fake.bridge["pols"]["policies"] if p["template"]["assetId"] == "mcp-transcoding"]) == 2
    assert sorted(fake.mcp_assets) == ["1.0.0", "1.0.1"] and sorted(fake.rest_assets["customer-api"]) == ["1.0.0"]


# ── failure handling ────────────────────────────────────────────────────────────────

def test_failure_in_the_first_domain_prevents_the_second(repo, tmp_path, monkeypatch):
    _live(monkeypatch)
    fake = AnypointFake(rest_assets={}, mcp_assets={}, reject="create")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        rd.main(_args(repo, tmp_path / "out", "--execute"))
    assert "CREATE REJECTED" in str(e.value)
    assert fake.kinds() == ["rest-asset", "external", "mcp-asset"]  # customer's steps up to the rejected create ...
    assert all(w[1].get("asset", w[1].get("name", "")).startswith(("customer", "uif-customer")) or w[0] == "mcp-asset" for w in fake.writes)
    assert "orders-api" not in json.dumps([w[1] for w in fake.writes], default=str)  # ... and NOTHING of orders
    assert not (tmp_path / "out" / "orders").exists()


def test_missing_configuration_fails_closed_before_any_domain_runs(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    synthetic_domain(root / "domains" / "alpha", "alpha", "alphas", "https://a/")
    (root / "domains" / "beta" / "src" / "mcp").mkdir(parents=True)
    (root / "domains" / "beta" / "src" / "mcp" / "bridge-config.yaml").write_text(
        "domain: beta\ncontract: src/api/beta/openapi.yaml\n", encoding="utf-8")  # onboarded, explicit contract that does NOT exist
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    with pytest.raises(SystemExit) as e:
        rd.main(_args(root, tmp_path / "out", "--dry-run"))
    assert "domain configuration error" in str(e.value) and "`contract: src/api/beta/openapi.yaml` does not exist" in str(e.value)
    assert not (tmp_path / "out" / "alpha").exists()  # alpha (ready) did not start either: errors are checked for ALL domains first
    # a configured domain whose upstream_url is unset is refused too
    cfg = root / "domains" / "alpha" / "src" / "mcp" / "bridge-config.yaml"
    cfg.write_text(yaml.safe_dump({"domain": "alpha", "environments": {"dev": {"upstream_url": None}}}), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        rd.main(_args(root, tmp_path / "out", "--dry-run", domains="alpha"))
    assert "no environments.dev.upstream_url" in str(e.value)


def test_protected_identity_is_refused_before_any_network(repo, tmp_path, monkeypatch):
    platform = yaml.safe_load(Path(PLATFORM).read_text(encoding="utf-8"))
    platform["dev"]["shared_bridge"]["protected"] = {"rest_asset_ids": ["orders-api"]}
    p = tmp_path / "platform.yaml"
    p.write_text(yaml.safe_dump(platform), encoding="utf-8")
    _live(monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    with pytest.raises(SystemExit) as e:
        rd.main(["--environment", "dev", "--domains", "orders", "--repo-root", str(repo), "--out", str(tmp_path / "out"), "--platform-config", str(p), "--execute"])
    assert "REFUSED (protected identity)" in str(e.value) and "REST asset 'orders-api'" in str(e.value)


# ── modes ───────────────────────────────────────────────────────────────────────────

def test_dry_run_makes_zero_network_calls(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MCP_LIVE_ENABLED", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    assert rd.main(_args(repo, tmp_path / "out", "--dry-run")) == 0
    out = capsys.readouterr().out
    assert out.count("DRY-RUN: nothing sent") >= 4 and "== DONE (dry-run) ==" in out
    for d in ("customer", "orders"):
        assert (tmp_path / "out" / d / "release.json").exists() and (tmp_path / "out" / d / "tool-mappings.json").exists()


def test_preflight_on_a_clean_slate_reads_but_never_writes(repo, tmp_path, monkeypatch, capsys):
    """Nothing exists yet: the release resolves 'initial' for both domains, the REST publish is
    previewed, and the Bridge rehearsal is SKIPPED (its REST asset does not exist to read) -
    reads only, both domains reported, nothing written."""
    _live(monkeypatch)
    fake = AnypointFake(rest_assets={}, mcp_assets={})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert rd.main(_args(repo, tmp_path / "out", "--preflight")) == 0
    out = capsys.readouterr().out
    assert fake.writes == [] and fake.methods() == {"GET"} and fake.mcp_calls == []
    assert out.count("decision : initial") == 2 and out.count("deploy-bridge: SKIPPED in preflight") == 2
    assert "PREFLIGHT ONLY: Anypoint was READ, nothing was published or modified." in out


def test_preflight_on_an_existing_platform_rehearses_the_bridge_step_read_only(repo, tmp_path, monkeypatch, capsys):
    """After a real run the same preflight inspects the live Bridge for every domain: release
    NO-OP, Bridge NO-OP - still reads only."""
    _live(monkeypatch)
    fake = AnypointFake(rest_assets={}, mcp_assets={})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert rd.main(_args(repo, tmp_path / "run", "--execute")) == 0
    fake.record.clear(); fake.writes.clear(); fake.mcp_calls.clear()
    assert rd.main(_args(repo, tmp_path / "pre", "--preflight")) == 0
    out = capsys.readouterr().out.split("== RECONCILE DOMAINS (preflight)")[1]
    assert fake.writes == [] and fake.methods() == {"GET"} and fake.mcp_calls == []
    assert out.count("decision : noop") == 2 and out.count("NO-OP") >= 2 and "PREFLIGHT ONLY" in out


def test_live_modes_require_the_latch_and_credentials_up_front(repo, tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_LIVE_ENABLED", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    for mode in ("--preflight", "--execute"):
        with pytest.raises(SystemExit) as e:
            rd.main(_args(repo, tmp_path / "out", mode))
        assert "MCP_LIVE_ENABLED=true" in str(e.value)


# ── alignment with the workflow ─────────────────────────────────────────────────────

def test_step_sequence_matches_mcp_deploy_yml():
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    names = [s.get("name", "") for job in ("validate", "live", "bridge") for s in wf["jobs"][job]["steps"]]
    mirrored = [w for _, w in rd.STEPS]
    assert all(n in names for n in mirrored), [n for n in mirrored if n not in names]
    assert [n for n in names if n in mirrored] == mirrored  # same relative order as the workflow
    # the same scripts, the same mode flags
    src = Path(rd.__file__).read_text(encoding="utf-8")
    runs = "\n".join(s.get("run", "") for job in ("live", "bridge") for s in wf["jobs"][job]["steps"])
    for tool in ("release_version", "generate_mcp_metadata", "exchange_publish", "validate_mcp_metadata", "contract_gate", "deploy_bridge", "mcp_conformance"):
        assert "%s.py" % tool in runs and ("import %s" % tool) in src, tool
    assert re.search(r'--retries 6 --retry-delay 10', runs) and '("--retries", "6", "--retry-delay", "10")' in src
    assert "--release-file" in runs and '"--release-file"' in src
