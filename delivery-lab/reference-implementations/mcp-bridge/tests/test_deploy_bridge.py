"""deploy_bridge.py - ONE command: reuse-or-create the Exchange external source
instance, publish/reuse the shared MCP asset, then create the SHARED Bridge bound to
the captured UUID (or reconcile the route into it). Exchange + API Manager are faked
together (anypoint_fake); every test asserts exactly which writes happened (if any).
Run-scoped PoC overrides (label/path/source name) are exercised here so a PoC can
never be mistaken for the shared Bridge.
"""
import json
import urllib.request
from pathlib import Path

import pytest
import yaml

import deploy_bridge
from anypoint_fake import (NEW_ID, NEW_UUID, ORG, PLATFORM, PROD_UUID, PRODUCT_CFG, PRODUCT_SPEC, SHARED_LABEL, SHARED_MCP, SHARED_PATH,
                           AnypointFake, effective)
from generate_mcp_metadata import generate, write_artifacts

REPO = Path(__file__).resolve().parents[4]
DOMAIN = REPO / "domains" / "product"  # fixture domain (first MCP realization): its CONTRACT is the real one
CONFIG = Path(PRODUCT_CFG)  # test-owned canonical Product bridge-config (anypoint_fake); the repo's own is asserted in test_isolation.py
REPO_CONFIG = DOMAIN / "src" / "mcp" / "bridge-config.yaml"
POC_URL = "https://test-sementic-search-ic2gqt.rajrd4-1.usa-e1.cloudhub.io/"
PROD_URL = "https://upstream.example.test/service/"
PROD_EXTERNAL = {"id": PROD_UUID, "type": "external", "name": "uif-product-dev", "endpointUri": PROD_URL, "isPublic": False, "productAPIVersion": "v1"}
PROD_BRIDGE = {"id": 21170001, "instanceLabel": SHARED_LABEL, "endpoint": {"proxyUri": "https://0.0.0.0:443/domainsTest"},
               "deployment": {"targetId": "2936f2eb-716f-47f1-a41f-fcfd09518867", "gatewayVersion": "1.13.1"}}


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory, cfg):
    out = tmp_path_factory.mktemp("deploy-artifacts")
    write_artifacts(generate(str(PRODUCT_SPEC),
                             source_ref={"assetId": cfg["source_asset_id"], "version": "1.0.0", "groupId": ORG},
                             mcp_asset={"assetId": SHARED_MCP, "version": "1.0.0"}, org_id=ORG,
                             route_label=cfg.get("route_label"), transcoding_path=cfg.get("transcoding_path", "/mcp")), str(out))
    return str(out)


def _poc_args(artifacts, out, mode):
    return ["--config", str(CONFIG), "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--rest-asset-version", "1.0.0",
            "--out", str(out), "--source-name", "test-unmanaged", "--upstream-url", POC_URL,
            "--instance-label", "uif-domains-mcp-poc", "--base-path", "domainsPoc", mode]


def _live(monkeypatch):
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")


def _no_network(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))


def test_dry_run_renders_both_requests_and_sends_nothing(artifacts, tmp_path, monkeypatch, capsys):
    _no_network(monkeypatch)
    assert deploy_bridge.main(_poc_args(artifacts, tmp_path, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "RUN-SCOPED OVERRIDES (domain/platform config unchanged)" in out
    assert '"name": "test-unmanaged"' in out and POC_URL in out and '"isPublic": false' in out
    assert '"instanceLabel": "uif-domains-mcp-poc"' in out and '"proxyUri": "https://0.0.0.0:443/domainsPoc"' in out
    assert '"uri": "%s"' % POC_URL in out  # the route's upstream follows the override too
    assert '"assetId": "uif-domains-mcp"' in out  # the ONE shared MCP asset, never a per-domain one
    assert "<CAPTURED-AT-DEPLOY:source-instance-uuid" in out and "DRY-RUN: nothing sent" in out
    assert "(standard name: uif-product-dev)" in out and "(standard: uif-domains-mcp, /domainsTest)" in out


# ── shared identity: ONE Bridge label / asset / path for every domain ─────────────

@pytest.mark.parametrize("domain", ["product", "price", "stock"])
def test_dry_run_renders_the_shared_identity_for_any_domain(artifacts, tmp_path, monkeypatch, capsys, cfg, domain):
    """Whatever the domain, the rendered payload carries the SHARED Bridge identity from
    anypoint-environments.yaml: label uif-domains-mcp, MCP asset uif-domains-mcp, path
    /domainsTest - and the domain's own route label / REST asset."""
    _no_network(monkeypatch)
    config = tmp_path / ("%s-bridge-config.yaml" % domain)
    config.write_text(yaml.safe_dump(dict(cfg, domain=domain, source_asset_id="%s-api" % domain)), encoding="utf-8")
    art = tmp_path / "art"
    write_artifacts(generate(str(PRODUCT_SPEC), source_ref={"assetId": "%s-api" % domain, "version": "1.0.0", "groupId": ORG},
                             mcp_asset={"assetId": SHARED_MCP, "version": "1.0.0"}, org_id=ORG), str(art))
    assert deploy_bridge.main(["--config", str(config), "--artifacts", str(art), "--environment", "dev", "--platform-config", PLATFORM,
                               "--rest-asset-version", "1.0.0", "--out", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert '"instanceLabel": "%s"' % SHARED_LABEL in out and '"proxyUri": "https://0.0.0.0:443/%s"' % SHARED_PATH in out
    assert '"assetId": "%s"' % SHARED_MCP in out and '"label": "%s-api"' % domain in out
    assert "uif-%s-mcp" % domain not in out and "/%sTest" % domain not in out  # no per-domain Bridge identity anywhere


def test_per_domain_bridge_identity_in_the_domain_config_is_refused(artifacts, tmp_path, monkeypatch, cfg):
    _no_network(monkeypatch)
    for extra, message in (({"base_path": "custom-path"}, "a domain cannot choose one"),
                           ({"mcp_asset_id": "uif-product-mcp"}, "remove the per-domain value")):
        config = tmp_path / "bridge-config.yaml"
        config.write_text(yaml.safe_dump(dict(cfg, **extra)), encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            deploy_bridge.main(["--config", str(config), "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM,
                                "--rest-asset-version", "1.0.0", "--out", str(tmp_path), "--dry-run"])
        assert message in str(e.value)


def test_product_config_owns_no_bridge_identity(cfg):
    for c in (cfg, yaml.safe_load(REPO_CONFIG.read_text(encoding="utf-8"))):  # the test-owned AND the repository's Product config
        assert c["domain"] == "product" and c["source_asset_id"]
        assert "mcp_asset_id" not in c and "base_path" not in c and "instance_label" not in c


def test_preflight_is_read_only_end_to_end(artifacts, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(externals=[PROD_EXTERNAL], extra_bridges=[PROD_BRIDGE])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_poc_args(artifacts, tmp_path, "--preflight")) == 0
    out = capsys.readouterr().out
    assert "no external instance named 'test-unmanaged' exists" in out and "execute would CREATE exactly ONE external instance" in out
    assert "shared MCP asset: uif-domains-mcp@1.0.0 (decision initial" in out
    assert "collision scan: PASS" in out and "'uif-domains-mcp-poc'" in out and "/domainsPoc" in out
    assert "PREFLIGHT ONLY (end to end): nothing was created, published or modified" in out
    assert fake.writes == [] and fake.methods() == {"GET"}
    assert not (tmp_path / "source-instance.json").exists()


def test_execute_creates_external_then_asset_then_bridge_bound_to_its_uuid(artifacts, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(externals=[PROD_EXTERNAL], extra_bridges=[PROD_BRIDGE])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_poc_args(artifacts, tmp_path, "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["external", "mcp-asset", "create"]  # exactly three writes, in this order
    assert fake.writes[0][1] == {"name": "test-unmanaged", "endpointUri": POC_URL, "isPublic": False, "_asset": "product-api"}  # _asset: the fake records which REST asset received the instance
    assert fake.writes[1][1]["version"] == "1.0.0" and fake.writes[1][1]["metadata"]["connections"][0]["ref"]["assetId"] == "product-api"
    bridge = fake.writes[2][1]
    conn = bridge["routing"][0]["upstreams"][0]["connection"]
    assert conn["apiVersionId"] == NEW_UUID and conn["label"] == "test-unmanaged" and conn["productAPIVersion"] == "v1"
    assert bridge["instanceLabel"] == "uif-domains-mcp-poc" and bridge["endpoint"]["proxyUri"].endswith("/domainsPoc")
    assert bridge["spec"] == {"assetId": SHARED_MCP, "groupId": ORG, "version": "1.0.0"}
    assert bridge["routing"][0]["upstreams"][0]["uri"] == POC_URL
    assert "CREATED external instance 'test-unmanaged' id=%s" % NEW_UUID in out
    assert "CREATED instance %s (label uif-domains-mcp-poc)" % NEW_ID in out
    assert "connection persisted as sent (apiVersionId %s, label test-unmanaged" % NEW_UUID in out and "read-back verification passed." in out
    # the shared Bridge and the production upstream were only READ, never written
    assert not any(m in ("POST", "PATCH", "DELETE") and ("21170001" in p or PROD_UUID in p) for m, p in fake.record)
    captured = json.loads((tmp_path / "source-instance.json").read_text(encoding="utf-8"))
    assert captured["source_api_instance_id"] == NEW_UUID


def test_execute_reuses_existing_external_and_asset_and_creates_only_the_bridge(artifacts, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    poc_external = {"id": NEW_UUID, "type": "external", "name": "test-unmanaged", "endpointUri": POC_URL, "isPublic": False, "productAPIVersion": "v1"}
    meta = (Path(artifacts) / "mcp-metadata.json").read_bytes()
    fake = AnypointFake(externals=[PROD_EXTERNAL, poc_external], mcp_assets={"1.0.0": meta})  # identical content already published
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_poc_args(artifacts, tmp_path, "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["create"]
    assert fake.writes[0][1]["routing"][0]["upstreams"][0]["connection"]["apiVersionId"] == NEW_UUID
    assert "REUSE: external instance 'test-unmanaged' already exists" in out
    assert "shared MCP asset: uif-domains-mcp@1.0.0 (decision reuse" in out


def test_execute_stops_before_anything_when_source_step_fails_closed(artifacts, tmp_path, monkeypatch):
    _live(monkeypatch)
    drifted = {"id": NEW_UUID, "type": "external", "name": "test-unmanaged", "endpointUri": "https://somewhere-else/", "isPublic": False}
    fake = AnypointFake(externals=[PROD_EXTERNAL, drifted])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(_poc_args(artifacts, tmp_path, "--execute"))
    assert "FAIL CLOSED" in str(e.value) and fake.writes == []


def test_poc_source_name_with_standard_bridge_label_is_refused(artifacts, tmp_path):
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--rest-asset-version", "1.0.0",
                            "--out", str(tmp_path), "--source-name", "test-unmanaged", "--upstream-url", POC_URL, "--dry-run"])
    assert "would rebind the shared Bridge's route" in str(e.value)


def test_config_is_never_modified(artifacts, tmp_path, monkeypatch):
    before = CONFIG.read_bytes()
    _no_network(monkeypatch)
    deploy_bridge.main(_poc_args(artifacts, tmp_path, "--dry-run"))
    assert CONFIG.read_bytes() == before
    assert (tmp_path / "effective-bridge-config.yaml").exists()  # the effective config is a build artifact in --out


def test_never_binds_a_foreign_source_instance_file_from_the_artifacts_dir(artifacts, tmp_path, monkeypatch, capsys):
    """Regression: the artifacts dir may hold the PRODUCTION source-instance.json
    (uif-product-dev). A PoC run with its own --out must never pick it up - in
    dry-run, preflight or execute."""
    prod_file = Path(artifacts) / "source-instance.json"
    prod_file.write_text(json.dumps({"source_api_instance_id": PROD_UUID, "kind": "exchange-external", "label": "uif-product-dev",
                                     "productAPIVersion": "v1"}), encoding="utf-8")
    try:
        _no_network(monkeypatch)
        assert deploy_bridge.main(_poc_args(artifacts, tmp_path, "--dry-run")) == 0
        out = capsys.readouterr().out
        assert PROD_UUID not in out and "uif-product-dev" not in out.split("-- step 2")[1]
        _live(monkeypatch)
        fake = AnypointFake(externals=[PROD_EXTERNAL])
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        assert deploy_bridge.main(_poc_args(artifacts, tmp_path, "--execute")) == 0
        conn = fake.writes[-1][1]["routing"][0]["upstreams"][0]["connection"]
        assert conn["apiVersionId"] == NEW_UUID and conn["label"] == "test-unmanaged"
        # and a stale PoC capture naming another upstream is refused before the Bridge create
        (tmp_path / "source-instance.json").write_text(json.dumps({"source_api_instance_id": PROD_UUID, "label": "uif-product-dev",
                                                                   "productAPIVersion": "v1"}), encoding="utf-8")
        fake = AnypointFake(externals=[PROD_EXTERNAL, {"id": NEW_UUID, "type": "external", "name": "test-unmanaged", "endpointUri": POC_URL,
                                                       "isPublic": False, "productAPIVersion": "v1"}], mcp_assets=dict(fake.mcp_assets))
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        # reuse path rewrites the capture with the right instance, so this succeeds and binds NEW_UUID
        assert deploy_bridge.main(_poc_args(artifacts, tmp_path, "--execute")) == 0
        assert fake.writes[-1][1]["routing"][0]["upstreams"][0]["connection"]["apiVersionId"] == NEW_UUID
    finally:
        prod_file.unlink()


def test_require_connection_refuses_connectionless_before_any_network(artifacts, tmp_path, monkeypatch):
    """Guarantee: with --require-connection the Bridge create is refused BEFORE a
    token request when no source instance is bound or the id is still a marker."""
    import provision_bridge
    _live(monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call")))
    eff = effective(CONFIG, tmp_path)
    base = ["--config", eff, "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--mcp-asset-version", "1.0.0",
            "--instance-label", "uif-domains-mcp-poc", "--base-path", "domainsPoc", "--require-connection"]
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(base + ["--source-instance-file", "none", "--execute"])
    assert "REFUSED (--require-connection)" in str(e.value)
    with pytest.raises(SystemExit) as e:
        provision_bridge.main(base + ["--source-instance-file", "none", "--source-api-instance-id",
                                      "<CAPTURED-AT-DEPLOY:source-instance-uuid (step 1)>", "--execute"])
    assert "REFUSED (--require-connection)" in str(e.value)


def test_execute_passes_require_connection_and_payload_is_never_connectionless(artifacts, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(externals=[PROD_EXTERNAL])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_poc_args(artifacts, tmp_path, "--execute")) == 0
    out = capsys.readouterr().out
    assert "connection guard: PASS - every upstream carries connection.apiVersionId=%s label=test-unmanaged productAPIVersion=v1" % NEW_UUID in out
    assert "source association: connection block present" in out
    bridge_payload = [w[1] for w in fake.writes if w[0] == "create"][0]
    assert all("connection" in u and u["connection"]["apiVersionId"] == NEW_UUID
               for r in bridge_payload["routing"] for u in r["upstreams"])


def test_check_existing_is_read_only_and_uses_the_shared_identity(artifacts, tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    fake = AnypointFake(externals=[PROD_EXTERNAL], extra_bridges=[PROD_BRIDGE])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--out", str(tmp_path), "--check-existing"]) == 0
    out = capsys.readouterr().out
    assert "== CHECK EXISTING (read-only) == label 'uif-domains-mcp', base path /domainsTest" in out
    assert "FOUND [label+base path]: id=21170001 label=uif-domains-mcp" in out
    assert fake.writes == [] and fake.methods() == {"GET"}


def test_rest_version_comes_from_the_release_file_and_refuses_drift(artifacts, tmp_path, monkeypatch, capsys):
    _no_network(monkeypatch)
    rel = tmp_path / "release.json"
    rel.write_text(json.dumps({"version": "1.0.0", "decision": "noop"}), encoding="utf-8")
    assert deploy_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--release-file", str(rel),
                               "--out", str(tmp_path), "--dry-run"]) == 0
    assert "release   : 1.0.0 (decision noop" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--release-file", str(rel),
                            "--rest-asset-version", "1.0.1", "--out", str(tmp_path), "--dry-run"])
    assert "--rest-asset-version 1.0.1 != release version 1.0.0" in str(e.value)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(["--config", str(CONFIG), "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM, "--out", str(tmp_path), "--dry-run"])
    assert "--release-file" in str(e.value)
