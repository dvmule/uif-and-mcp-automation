"""Listener (base) path handling of the shared Bridge with MULTI-SEGMENT paths.

Regression for the 2026-09-30 finding: three frozen-module sites derived the Bridge base
path from `endpoint.proxyUri` with a last-segment split (`rsplit("/", 1)[-1]`), so a
listener path like `/uif/domains` collapsed to `domains`: the CREATE-path collision scan
looked for the wrong path, the post-CREATE read-back used a suffix match, and the
standalone update_bridge CLI built an identity the real Bridge could never satisfy.
The path is now parsed as a URI (`provision_bridge._path_component` / `base_path_of`)
and every segment is preserved; nothing here assumes a path depth.
"""
import json
from pathlib import Path

import pytest

import provision_bridge as pb
import shared_bridge as sb
import update_bridge as ub
from anypoint_fake import (ENV, ORG, PLATFORM, PROD_URL, PROD_UUID, PRODUCT_CFG, SHARED_LABEL, domain_payload, effective, shared_state)
from anypoint_fake import PRODUCT_SPEC
from generate_mcp_metadata import generate, write_artifacts
from test_mcp_provisioning import _inst, _payload, _readback, _run_preflight, artifacts_dir, cfg  # noqa: F401  (fixtures re-exported)

REPO = Path(__file__).resolve().parents[4]
SPEC = PRODUCT_SPEC  # test-owned fixture contract
SINGLE, MULTI, DEEP = "foo", "uif/domains", "uif/domains/v1"


# ── the parser ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("/foo", "/foo"), ("https://0.0.0.0:443/foo", "/foo"), ("https://0.0.0.0:443/foo/", "/foo"),
    ("/uif/domains", "/uif/domains"), ("https://0.0.0.0:443/uif/domains", "/uif/domains"), ("https://0.0.0.0:443/uif/domains/", "/uif/domains"),
    ("https://mcp-dev.pearson.com/foo/bar/baz", "/foo/bar/baz"), ("/foo/bar/baz/", "/foo/bar/baz"),
    ("https://0.0.0.0:443/foo/bar/baz?x=1", "/foo/bar/baz"), ("https://0.0.0.0:443/foo/bar/baz#frag", "/foo/bar/baz"),
    ("/foo/bar?x=1#f", "/foo/bar"), ("https://h/uif/domains/?q=1", "/uif/domains"),
    ("https://host", "/"), ("https://host/", "/"),
    ("plain", None), ("", None), (None, None), (42, None),
])
def test_path_component_preserves_every_segment_and_drops_query_and_fragment(value, expected):
    assert pb._path_component(value) == expected


@pytest.mark.parametrize("uri, expected", [
    ("https://0.0.0.0:443/foo", SINGLE), ("https://0.0.0.0:443/uif/domains", MULTI), ("https://0.0.0.0:443/uif/domains/", MULTI),
    ("https://0.0.0.0:443/uif/domains/v1", DEEP), ("https://h/uif/domains/v1/?q=1#f", DEEP), ("/uif/domains", MULTI),
])
def test_base_path_of_matches_the_shared_bridge_base_path_convention(uri, expected):
    """One convention everywhere: shared_bridge.base_path is stored without leading/trailing slash,
    the leading slash is put back only for the comparison against the parsed proxyUri."""
    assert pb.base_path_of(uri) == expected
    doc = {"shared_bridge": {"instance_label": SHARED_LABEL, "mcp_asset_id": SHARED_LABEL, "base_path": "/%s/" % expected}}
    assert sb.load_shared(doc, "dev")["base_path"] == pb.base_path_of(uri)
    assert pb._path_component(uri) == "/" + pb.base_path_of(uri)


@pytest.mark.parametrize("bad", ["https://host", "https://host/", "plain", "", None])
def test_base_path_of_fails_closed_without_a_listener_path(bad):
    with pytest.raises(SystemExit) as e:
        pb.base_path_of(bad)
    assert "REFUSED" in str(e.value) and "no listener path" in str(e.value)


def test_no_last_segment_derivation_remains_in_the_frozen_modules():
    for name in ("provision_bridge.py", "update_bridge.py"):
        src = (Path(pb.__file__).parent / name).read_text(encoding="utf-8")
        assert 'rsplit("/", 1)' not in src.replace("rsplit(\"/apis/\", 1)", "") and 'split("/")[-1]' not in src, name
        assert "ends with /" not in src, name  # the suffix-match read-back check is gone


# ── CREATE path: collision detection ───────────────────────────────────────────

def test_collision_hits_recognize_the_exact_multi_segment_path_only():
    resolved = [(_inst(1, "a", MULTI), "https://0.0.0.0:443/uif/domains"), (_inst(2, "b", "domains"), "https://0.0.0.0:443/domains"),
                (_inst(3, "c", "uif"), "https://0.0.0.0:443/uif"), (_inst(4, "d", DEEP), "https://0.0.0.0:443/uif/domains/v1"),
                (_inst(5, "e", "other/domains"), "https://0.0.0.0:443/other/domains"), (_inst(6, "f", MULTI + "/"), "https://0.0.0.0:443/uif/domains/")]
    assert pb.collision_hits("new-label", MULTI, resolved, {}) == [
        "base path /uif/domains collision with instance 1 (proxyUri https://0.0.0.0:443/uif/domains)",
        "base path /uif/domains collision with instance 6 (proxyUri https://0.0.0.0:443/uif/domains/)"]
    assert pb.collision_hits("new-label", DEEP, resolved, {}) == [
        "base path /uif/domains/v1 collision with instance 4 (proxyUri https://0.0.0.0:443/uif/domains/v1)"]
    assert pb.collision_hits("new-label", "uif/other", resolved, {}) == []
    inv = {"gateway-target ports (xapi)": {"status": "ok", "paths": {"/uif/domains", "/other"}}}
    assert pb.collision_hits("new-label", MULTI, [], inv) == ["base path /uif/domains already in use on the target [gateway-target ports (xapi)]"]
    assert pb.collision_hits("new-label", "domains", [], inv) == [] and pb.collision_hits("new-label", "uif", [], inv) == []


def test_create_preflight_derives_the_complete_path_from_the_payload_and_refuses_the_exact_collision(artifacts_dir, monkeypatch):
    """Another instance already serving /uif/domains: the old derivation scanned for /domains and PASSED."""
    apis = [_inst(21164696, "poc-uifull", "poc-uifull"), _inst(555, "someone-elses-bridge", MULTI)]
    with pytest.raises(SystemExit) as e:
        _run_preflight(monkeypatch, [], artifacts_dir, extra_args=["--base-path", MULTI], env_apis=apis)
    assert "REFUSED (collision guard)" in str(e.value) and "base path /uif/domains collision with instance 555" in str(e.value)
    ports = [{"port": 443, "paths": [{"path": "/uif/domains"}]}]
    with pytest.raises(SystemExit) as e:
        _run_preflight(monkeypatch, [], artifacts_dir, extra_args=["--base-path", MULTI], target_ports=ports)
    assert "base path /uif/domains already in use on the target" in str(e.value)


def test_create_preflight_does_not_falsely_collide_on_a_parent_child_or_sibling_path(artifacts_dir, monkeypatch, capsys):
    apis = [_inst(21164696, "poc-uifull", "poc-uifull"), _inst(556, "x", "domains"), _inst(557, "y", "uif"),
            _inst(558, "z", DEEP), _inst(559, "w", "other/domains")]
    ports = [{"port": 443, "paths": [{"path": "/domains"}, {"path": "/uif"}, {"path": "/uif/domains/v1"}]}]
    assert _run_preflight(monkeypatch, [], artifacts_dir, extra_args=["--base-path", MULTI], env_apis=apis, target_ports=ports) == 0
    out = capsys.readouterr().out
    assert "collision scan: PASS" in out and "/uif/domains not served by any instance proxyUri" in out
    assert '"proxyUri": "https://0.0.0.0:443/uif/domains"' in out and "/domains not served" not in out.replace("/uif/domains not served", "")


# ── CREATE path: post-create verification ──────────────────────────────────────

def test_post_create_verification_compares_the_complete_path(cfg, artifacts_dir):
    payload = _payload(cfg, artifacts_dir, mcp_asset_version="1.0.0", base_path=MULTI)
    assert payload["endpoint"]["proxyUri"] == "https://0.0.0.0:443/uif/domains"
    ok, lines = pb.verify_readback(payload, *_readback(payload), allow_ui_gap=True)
    assert ok and "proxyUri path /uif/domains: PASS" in lines
    # the old suffix check accepted ANY proxyUri ending in /domains
    for wrong in ("https://0.0.0.0:443/other/domains", "https://0.0.0.0:443/domains", "https://0.0.0.0:443/uif/domains/v1", "https://0.0.0.0:443/uif"):
        ok, lines = pb.verify_readback(payload, *_readback(payload, inst_extra={"endpoint": {"proxyUri": wrong}}), allow_ui_gap=True)
        assert not ok and "proxyUri path /uif/domains: FAIL" in lines, wrong
    # a trailing slash on the read-back is normalization, not drift
    assert pb.verify_readback(payload, *_readback(payload, inst_extra={"endpoint": {"proxyUri": "https://0.0.0.0:443/uif/domains/"}}), allow_ui_gap=True)[0]
    for path in (SINGLE, DEEP):  # one and three segments, preserved end to end
        p = _payload(cfg, artifacts_dir, mcp_asset_version="1.0.0", base_path=path)
        ok, lines = pb.verify_readback(p, *_readback(p), allow_ui_gap=True)
        assert ok and "proxyUri path /%s: PASS" % path in lines


# ── standalone update_bridge: identity from the payload ────────────────────────

@pytest.fixture(scope="module")
def product_eff(tmp_path_factory):
    return effective(PRODUCT_CFG, tmp_path_factory.mktemp("eff-product"))


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory, product_eff):
    c = pb.load_yaml(product_eff)
    out = tmp_path_factory.mktemp("art")
    write_artifacts(generate(str(SPEC), source_ref={"assetId": c["source_asset_id"], "version": "1.0.0", "groupId": ORG},
                             mcp_asset={"assetId": c["mcp_asset_id"], "version": "1.0.1"}, org_id=ORG,
                             route_label=c.get("route_label"), transcoding_path=c.get("transcoding_path", "/mcp")), str(out))
    return str(out)


def _source_file(tmp_path):
    p = tmp_path / "source-instance.json"
    p.write_text(json.dumps({"source_api_instance_id": PROD_UUID, "kind": "exchange-external", "label": "uif-product-dev",
                             "endpointUri": PROD_URL, "productAPIVersion": "v1",
                             "asset": {"groupId": ORG, "assetId": "product-api", "version": "1.0.0"}}), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("path", [SINGLE, MULTI, DEEP])
def test_standalone_update_bridge_identity_accepts_the_exact_path_and_refuses_any_other(product_eff, artifacts, tmp_path, path):
    _, _, payload, expected = ub.prepare(product_eff, artifacts, "dev", "1.0.1", _source_file(tmp_path), SHARED_LABEL, path, PROD_URL, PLATFORM)
    assert payload["endpoint"]["proxyUri"] == "https://0.0.0.0:443/" + path and expected["base_path"] == path
    assert expected["base_path"] == sb.load_shared({"shared_bridge": {"instance_label": SHARED_LABEL, "mcp_asset_id": SHARED_LABEL,
                                                                        "base_path": "/" + path}}, "dev")["base_path"]  # same value deploy_bridge uses

    def bridge_at(p):
        return shared_state([domain_payload(product_eff, artifacts, "1.0.1", PROD_UUID, "uif-product-dev", PROD_URL, base_path=p)], "1.0.1", base_path=p)

    st = bridge_at(path)
    ok, lines = ub.identity_guards(st["inst"], st["ups"], st["pols"], expected)
    assert ok and "proxyUri path /%s: PASS" % path in lines
    for wrong in {path.rsplit("/", 1)[-1], path.split("/", 1)[0], path + "/v9", "other/" + path} - {path}:
        st = bridge_at(wrong)
        ok, lines = ub.identity_guards(st["inst"], st["ups"], st["pols"], expected)
        assert not ok and "proxyUri path /%s: FAIL" % path in lines, wrong


def test_standalone_update_bridge_dry_run_renders_the_complete_path_without_network(product_eff, artifacts, tmp_path, monkeypatch, capsys):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert ub.main(["--config", product_eff, "--artifacts", artifacts, "--environment", "dev", "--platform-config", PLATFORM,
                    "--mcp-asset-version", "1.0.1", "--source-instance-file", _source_file(tmp_path), "--instance-label", SHARED_LABEL,
                    "--base-path", MULTI, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "for Bridge %r at /uif/domains;" % SHARED_LABEL in out and '"proxyUri": "https://0.0.0.0:443/uif/domains"' in out
    assert "at /domains;" not in out and "DRY-RUN: nothing sent" in out
