"""The fail-closed `shared_bridge.protected` guard and the exact-match lookups that make a
run unable to resolve, publish to, bind or mutate identities it was told to leave alone.

Test-owned configuration only (a platform config whose `protected` block fences off a
hypothetical set of "reference" identities). The repository's committed configuration
carries an EMPTY protected block; this file proves the mechanism stays available and
fail-closed. Domain names here (alpha/beta) are synthetic - nothing is Product/Price."""
import copy
import json
import urllib.request
from pathlib import Path

import pytest
import yaml

import deploy_bridge
import shared_bridge as sb
from anypoint_fake import (NEW_ID, NEW_UUID, ORG, TEST_PLATFORM, AnypointFake, domain_payload, merged_asset, shared_state, synthetic_domain)
from generate_mcp_metadata import generate, write_artifacts

UP = "https://upstream.example.test/service/"
REF_LABEL, REF_MCP, REF_REST, REF_INSTANCE = "uif-domains-reference", "uif-domains-reference", "uif-alpha-reference-api", "reference-instance"
REF_BRIDGE = {"id": 21168012, "instanceLabel": REF_LABEL, "endpoint": {"proxyUri": "https://0.0.0.0:443/reference"},
              "deployment": {"targetId": "2936f2eb-716f-47f1-a41f-fcfd09518867", "gatewayVersion": "1.13.1"}}
REF_EXTERNAL = {"id": "cccccccc-1111-4222-8333-444444444444", "type": "external", "name": REF_INSTANCE, "endpointUri": UP, "isPublic": False, "productAPIVersion": "v1"}


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    """Test-owned platform: the canonical shared identity + a protected block naming the reference resources."""
    doc = copy.deepcopy(TEST_PLATFORM)
    doc["dev"]["shared_bridge"]["protected"] = {"instance_labels": [REF_LABEL], "mcp_asset_ids": [REF_MCP],
                                                 "rest_asset_ids": [REF_REST], "source_instance_names": [REF_INSTANCE]}
    p = tmp_path_factory.mktemp("platform") / "anypoint-environments.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return {"path": str(p), "shared": sb.load_shared(doc["dev"], "dev")}


@pytest.fixture(scope="module")
def alpha(tmp_path_factory):
    spec, cfg = synthetic_domain(tmp_path_factory.mktemp("alpha"), "alpha", "alphas", UP)
    doc = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    doc.pop("source_asset_id")  # rely on the convention default: uif-<domain>-api
    cfg.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    art = tmp_path_factory.mktemp("alpha-art")
    write_artifacts(generate(str(spec), source_ref={"assetId": "uif-alpha-api", "version": "1.0.0", "groupId": ORG},
                             mcp_asset={"assetId": TEST_PLATFORM["dev"]["shared_bridge"]["mcp_asset_id"], "version": "1.0.0"}, org_id=ORG), str(art))
    return {"cfg": str(cfg), "art": str(art), "doc": yaml.safe_load(cfg.read_text(encoding="utf-8"))}


def _live(monkeypatch):
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")


def _args(cfg, art, out, platform, mode):
    return ["--config", cfg, "--artifacts", art, "--environment", "dev", "--rest-asset-version", "1.0.0", "--out", str(out),
            "--platform-config", platform["path"], mode]


@pytest.mark.parametrize("mutate, message", [
    (lambda s, c: s.__setitem__("instance_label", REF_LABEL), "Bridge instance label '%s'" % REF_LABEL),
    (lambda s, c: s.__setitem__("mcp_asset_id", REF_MCP), "MCP asset '%s'" % REF_MCP),
    (lambda s, c: c.__setitem__("source_asset_id", REF_REST), "REST asset '%s'" % REF_REST),
    (lambda s, c: c.__setitem__("route_label", REF_REST), "route label '%s'" % REF_REST),
    (lambda s, c: c.__setitem__("source_instance_name", REF_INSTANCE), "source instance '%s'" % REF_INSTANCE),
])
def test_every_protected_kind_is_refused_before_anything_is_sent(platform, alpha, mutate, message):
    shared, cfg = copy.deepcopy(platform["shared"]), copy.deepcopy(alpha["doc"])
    mutate(shared, cfg)
    with pytest.raises(SystemExit) as e:
        eff = sb.effective_config(cfg, shared)
        sb.guard_protected(shared, eff, source_instance_name=cfg.get("source_instance_name"))
    assert "REFUSED (protected identity)" in str(e.value) and message in str(e.value)


def test_an_empty_or_absent_protected_block_guards_nothing():
    shared = sb.load_shared(TEST_PLATFORM["dev"], "dev")
    assert shared["protected"] == {}
    sb.guard_protected(shared, {"domain": "x", "source_asset_id": "uif-x-api"}, instance_label=shared["instance_label"], source_instance_name="uif-x-dev")
    repo = yaml.safe_load((Path(__file__).resolve().parents[1] / "scripts" / "anypoint-environments.yaml").read_text(encoding="utf-8"))
    assert sb.load_shared(repo["dev"], "dev")["protected"] == {}  # the committed configuration fences nothing off


def test_a_run_whose_platform_points_at_a_protected_bridge_is_refused_in_dry_run(platform, alpha, tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    doc = yaml.safe_load(Path(platform["path"]).read_text(encoding="utf-8"))
    doc["dev"]["shared_bridge"]["instance_label"] = REF_LABEL
    p = tmp_path / "platform.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(["--config", alpha["cfg"], "--artifacts", alpha["art"], "--environment", "dev", "--rest-asset-version", "1.0.0",
                            "--out", str(tmp_path), "--platform-config", str(p), "--dry-run"])
    assert "REFUSED (protected identity)" in str(e.value) and REF_LABEL in str(e.value)
    with pytest.raises(SystemExit) as e:  # a run-scoped label override cannot reach it either
        deploy_bridge.main(_args(alpha["cfg"], alpha["art"], tmp_path, platform, "--dry-run") + ["--instance-label", REF_LABEL])
    assert "REFUSED (protected identity)" in str(e.value)


def test_protected_bridge_in_the_environment_is_ignored_and_never_written(platform, alpha, tmp_path, monkeypatch, capsys):
    """The reference Bridge sits in the same environment with a reference REST asset whose
    instance shares the upstream URL: the run CREATEs its own Bridge, reads nothing of the
    reference asset, binds only its own instance, and never writes to the reference Bridge."""
    _live(monkeypatch)
    fake = AnypointFake(externals=[], extra_bridges=[REF_BRIDGE], rest_assets={REF_REST: {"1.0.0": [REF_EXTERNAL]}, "uif-alpha-api": {"1.0.0": []}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(alpha["cfg"], alpha["art"], tmp_path, platform, "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["external", "mcp-asset", "create"]
    assert fake.writes[0][1]["name"] == "uif-alpha-dev" and fake.writes[0][1]["_asset"] == "uif-alpha-api"
    create = fake.writes[2][1]
    assert create["instanceLabel"] == platform["shared"]["instance_label"] and REF_EXTERNAL["id"] not in json.dumps(create)
    assert "protected instance(s) present in the environment and IGNORED (never touched): %s" % REF_LABEL in out
    assert not any(m in ("POST", "PATCH", "DELETE") and "/apis/%s" % REF_BRIDGE["id"] in p for m, p in fake.record)
    assert not any(m == "GET" and "/%s/" % REF_REST in p for m, p in fake.record)  # the reference asset is never even read


def test_lookups_are_exact_never_prefix(platform, alpha, tmp_path, monkeypatch, capsys):
    """A Bridge whose label merely starts with ours, or an instance whose name merely starts
    with ours, is neither resolved nor reused."""
    _live(monkeypatch)
    near_label = platform["shared"]["instance_label"] + "-legacy"
    near = {"id": 555, "instanceLabel": near_label, "endpoint": {"proxyUri": "https://0.0.0.0:443/legacy"}, "deployment": {}}
    near_ext = {"id": "dddddddd-1111-4222-8333-444444444444", "type": "external", "name": "uif-alpha-dev-legacy", "endpointUri": UP, "isPublic": False, "productAPIVersion": "v1"}
    fake = AnypointFake(externals=[], extra_bridges=[near], rest_assets={"uif-alpha-api": {"1.0.0": [near_ext]}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert deploy_bridge.main(_args(alpha["cfg"], alpha["art"], tmp_path, platform, "--execute")) == 0
    out = capsys.readouterr().out
    assert fake.kinds() == ["external", "mcp-asset", "create"] and fake.writes[0][1]["name"] == "uif-alpha-dev"  # not reused
    assert "bridge lookup: none labeled %r -> CREATE path" % platform["shared"]["instance_label"] in out
    assert fake.writes[2][1]["routing"][0]["upstreams"][0]["connection"]["apiVersionId"] == NEW_UUID
