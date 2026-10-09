"""ensure_source_instance.py - the Exchange EXTERNAL (unmanaged) instance the MCP
Bridge association needs, the way the Anypoint wizard does it (decided
2026-09-11; live-verified: 55 external instances in the org, UUID ids).
No network anywhere: Exchange is faked; every test asserts the HTTP methods issued.
"""
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

import ensure_source_instance as esi
import provision_bridge

REPO = Path(__file__).resolve().parents[4]
DOMAIN = REPO / "domains" / "product"  # fixture domain (first MCP realization)
from anypoint_fake import PLATFORM, PRODUCT_CFG
from anypoint_fake import PRODUCT_SPEC
CONFIG = Path(PRODUCT_CFG)  # test-owned canonical Product bridge-config (the repo's own may carry temporary test identities)
ORG = "8c27297e-4bcf-4c53-99eb-ffd7b1069294"
UUID = "ef2c6179-2ad8-4eab-8854-02f832b970e3"
UPSTREAM = "https://upstream.example.test/service/"


@pytest.fixture(scope="module")
def cfg():
    """The EFFECTIVE config (domain inputs + the shared Bridge identity) - what the frozen
    provisioning code consumes; the domain file itself carries no MCP asset id."""
    from anypoint_fake import shared
    from shared_bridge import effective_config
    return effective_config(yaml.safe_load(CONFIG.read_text(encoding="utf-8")), shared())


class _Resp:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode()
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _external(name, url=UPSTREAM, iid=UUID):
    return {"id": iid, "type": "external", "name": name, "endpointUri": url, "isPublic": False,
            "productAPIVersion": "v1", "versionGroup": "v1", "environmentId": None, "assetId": "product-api", "version": "1.0.0"}


MANAGED_21166244 = {"id": "21166244", "type": "managed", "name": "uif-product-api", "endpointUri": UPSTREAM,
                    "productAPIVersion": "v1", "environmentId": "a8c24dc0-02fc-4eb5-bf20-6c5b2627182f"}


def _fake(record, *, instances=None, create_status=201, created_id=UUID, descriptor_status=200, list_after_create=True):
    state = {"instances": list(instances or [])}

    def urlopen(req, timeout=None):
        method, path = req.get_method(), req.full_url.split("anypoint.mulesoft.com", 1)[1]
        record.append((method, path))
        if path.endswith("/oauth2/token"):
            return _Resp({"access_token": "tok"})
        if method == "GET" and path == "/exchange/api/v2/assets/%s/product-api/1.0.0" % ORG:
            if descriptor_status != 200:
                raise urllib.error.HTTPError(req.full_url, descriptor_status, "Not Found", None, None)
            return _Resp({"groupId": ORG, "assetId": "product-api", "version": "1.0.0", "versionGroup": "v1",
                          "instances": state["instances"]})
        if method == "POST" and path == "/exchange/api/v2/assets/%s/product-api/versionGroups/v1/instances/external" % ORG:
            if create_status != 201:
                raise urllib.error.HTTPError(req.full_url, create_status, "Bad Request", None,
                                             io.BytesIO(json.dumps({"message": "endpointUri must be a valid URL"}).encode()))
            body = json.loads(req.data)
            assert body == {"name": body["name"], "endpointUri": UPSTREAM, "isPublic": False}
            if list_after_create:
                state["instances"].append(_external(body["name"], body["endpointUri"], created_id))
            return _Resp({"id": created_id, "name": body["name"], "endpointUri": body["endpointUri"], "type": "external"})
        raise AssertionError("unexpected call %s %s" % (method, path))
    return urlopen


def _args(tmp_path, *extra):
    return ["--config", str(CONFIG), "--environment", "dev", "--platform-config", PLATFORM, "--asset-version", "1.0.0", "--out", str(tmp_path), *extra]


def _live(monkeypatch):
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")


# ── pure pieces ────────────────────────────────────────────────────────────────

def test_deterministic_name_and_private_body(cfg):
    assert esi.source_name(cfg, "dev") == "uif-product-dev"
    assert esi.source_name(cfg, "uat") == "uif-product-uat"
    body = esi.build_external_instance_body("uif-product-dev", UPSTREAM)
    assert body == {"name": "uif-product-dev", "endpointUri": UPSTREAM, "isPublic": False}
    assert json.dumps(body).count("true") == 0  # never public


def test_upstream_url_is_never_invented(cfg):
    c = dict(cfg)
    c["environments"] = {"dev": {"upstream_url": None}}
    with pytest.raises(SystemExit) as e:
        esi.upstream_url_for(c, "dev")
    assert "upstream_url" in str(e.value)


def test_resolve_reuses_exact_match_and_ignores_managed_and_other_names():
    desc = {"assetId": "product-api", "version": "1.0.0", "instances": [
        MANAGED_21166244, _external("someone-else", "https://other/"), _external("uif-product-dev")]}
    action, inst = esi.resolve_external_instance(desc, "uif-product-dev", UPSTREAM)
    assert action == "reuse" and inst["id"] == UUID
    # trailing-slash differences are not drift
    assert esi.resolve_external_instance(desc, "uif-product-dev", UPSTREAM.rstrip("/"))[0] == "reuse"
    # a managed instance with the same name is NOT a match for the external path
    desc2 = {"assetId": "product-api", "version": "1.0.0", "instances": [dict(MANAGED_21166244, name="uif-product-dev")]}
    assert esi.resolve_external_instance(desc2, "uif-product-dev", UPSTREAM) == ("create", None)


def test_resolve_fails_closed_on_url_drift_and_duplicates():
    drift = {"assetId": "product-api", "version": "1.0.0", "instances": [_external("uif-product-dev", "https://old-upstream/")]}
    with pytest.raises(SystemExit) as e:
        esi.resolve_external_instance(drift, "uif-product-dev", UPSTREAM)
    assert "FAIL CLOSED" in str(e.value) and "upstream changed" in str(e.value)
    dup = {"assetId": "product-api", "version": "1.0.0", "instances": [_external("uif-product-dev"), _external("uif-product-dev", iid="other")]}
    with pytest.raises(SystemExit) as e:
        esi.resolve_external_instance(dup, "uif-product-dev", UPSTREAM)
    assert "ambiguous" in str(e.value)


def test_version_group_comes_from_exchange_not_assumed():
    assert esi.version_group_of({"versionGroup": "v2"}) == "v2"
    with pytest.raises(SystemExit):
        esi.version_group_of({"assetId": "x", "version": "1.0.0"})


# ── CLI: dry-run / execute ─────────────────────────────────────────────────────

def test_dry_run_prints_request_and_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call in dry-run")))
    assert esi.main(_args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Exchange EXTERNAL instance" in out and "uif-product-dev" in out
    assert "/exchange/api/v2/assets/%s/product-api/versionGroups/<versionGroup from descriptor>/instances/external" % ORG in out
    assert '"isPublic": false' in out and "DRY-RUN: nothing sent" in out
    assert "apimanager" not in out  # no API Manager involvement anywhere


def test_execute_creates_when_absent_and_captures_uuid_unchanged(tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    record = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake(record, instances=[MANAGED_21166244]))
    assert esi.main(_args(tmp_path, "--execute")) == 0
    out = capsys.readouterr().out
    posts = [p for m, p in record if m == "POST" and not p.endswith("/oauth2/token")]
    assert posts == ["/exchange/api/v2/assets/%s/product-api/versionGroups/v1/instances/external" % ORG]
    assert "CREATED external instance 'uif-product-dev' id=%s" % UUID in out
    assert "read-back: type external: PASS" in out and "read-back: productAPIVersion v1 (from Exchange): PASS" in out
    assert "source_api_instance_id=%s" % UUID in out
    captured = json.loads((tmp_path / "source-instance.json").read_text(encoding="utf-8"))
    assert captured["source_api_instance_id"] == UUID and isinstance(captured["source_api_instance_id"], str)
    assert captured["kind"] == "exchange-external" and captured["label"] == "uif-product-dev"
    assert captured["productAPIVersion"] == "v1" and captured["asset"]["assetId"] == "product-api"
    assert "s3cr3t-VALUE-never-printed" not in out
    assert not any("/apimanager/" in p for m, p in record)  # 21166244-style API Manager objects never touched


def test_execute_reuses_existing_external_without_writing(tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    record = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake(record, instances=[MANAGED_21166244, _external("uif-product-dev")]))
    assert esi.main(_args(tmp_path, "--execute")) == 0
    out = capsys.readouterr().out
    assert "REUSE: external instance 'uif-product-dev' already exists (id %s" % UUID in out
    assert not any(m == "POST" and not p.endswith("/oauth2/token") for m, p in record)
    assert "source_api_instance_id=%s" % UUID in out


def test_execute_fails_closed_on_url_drift_without_writing(tmp_path, monkeypatch):
    _live(monkeypatch)
    record = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake(record, instances=[_external("uif-product-dev", "https://old/")]))
    with pytest.raises(SystemExit) as e:
        esi.main(_args(tmp_path, "--execute"))
    assert "FAIL CLOSED" in str(e.value)
    assert not any(m == "POST" and not p.endswith("/oauth2/token") for m, p in record)


def test_execute_surfaces_rejection_and_requires_descriptor(tmp_path, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", _fake([], create_status=400))
    with pytest.raises(SystemExit) as e:
        esi.main(_args(tmp_path, "--execute"))
    assert "EXTERNAL INSTANCE CREATE REJECTED - HTTP 400" in str(e.value) and "endpointUri must be a valid URL" in str(e.value)
    assert (tmp_path / "source-instance-rejected.json").exists()
    monkeypatch.setattr(urllib.request, "urlopen", _fake([], descriptor_status=404))
    with pytest.raises(SystemExit) as e:
        esi.main(_args(tmp_path, "--execute"))
    assert "publish the REST asset first" in str(e.value)


def test_execute_fails_when_created_instance_is_not_listed(tmp_path, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", _fake([], list_after_create=False))
    with pytest.raises(SystemExit) as e:
        esi.main(_args(tmp_path, "--execute"))
    assert "not listed in the Exchange descriptor" in str(e.value)


def test_execute_requires_live_latch(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_LIVE_ENABLED", raising=False)
    with pytest.raises(SystemExit) as e:
        esi.main(_args(tmp_path, "--execute"))
    assert "MCP_LIVE_ENABLED" in str(e.value)


# ── hand-off to the Bridge payload ─────────────────────────────────────────────

def _artifacts(cfg, tmp_path):
    from anypoint_fake import SHARED_MCP
    from generate_mcp_metadata import generate, write_artifacts
    artifacts = generate(str(PRODUCT_SPEC),
                         source_ref={"assetId": cfg["source_asset_id"], "version": "1.0.0", "groupId": ORG},
                         mcp_asset={"assetId": SHARED_MCP, "version": "1.0.0"}, org_id=ORG,  # the SHARED Bridge's MCP asset
                         route_label=cfg.get("route_label"), transcoding_path=cfg.get("transcoding_path", "/mcp"))
    write_artifacts(artifacts, str(tmp_path))
    return str(tmp_path)


def _effective(tmp_path):
    """provision_bridge consumes the EFFECTIVE config (domain inputs + shared identity)."""
    from anypoint_fake import effective
    return effective(CONFIG, tmp_path / "eff")


def test_captured_uuid_flows_unchanged_into_connection(cfg, tmp_path, monkeypatch, capsys):
    """provision_bridge reads <artifacts>/source-instance.json: apiVersionId = the
    UUID string verbatim, label = the instance name, productAPIVersion = the
    Exchange version group - no numeric assumption anywhere."""
    art = _artifacts(cfg, tmp_path)
    (tmp_path / "source-instance.json").write_text(json.dumps({
        "source_api_instance_id": UUID, "kind": "exchange-external", "label": "uif-product-dev",
        "endpointUri": UPSTREAM, "asset": {"groupId": ORG, "assetId": "product-api", "version": "1.0.0"},
        "productAPIVersion": "v1"}), encoding="utf-8")
    rc = provision_bridge.main(["--config", _effective(tmp_path), "--artifacts", art, "--environment", "dev", "--platform-config", PLATFORM,
                                "--mcp-asset-version", "1.0.0", "--base-path", "productTest", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "source instance from" in out and "id=%s label=uif-product-dev productAPIVersion=v1 (exchange-external)" % UUID in out
    payload = json.loads(out[out.index("{"):out.rindex("}") + 1])
    conn = payload["routing"][0]["upstreams"][0]["connection"]
    assert conn == {"label": "uif-product-dev", "assetId": "product-api", "groupId": ORG, "version": "1.0.0",
                    "assetName": "product-api", "assetType": "rest", "apiVersionId": UUID, "productAPIVersion": "v1"}


def test_explicit_cli_id_overrides_captured_file(cfg, tmp_path):
    art = _artifacts(cfg, tmp_path)
    (tmp_path / "source-instance.json").write_text(json.dumps({"source_api_instance_id": UUID, "label": "uif-product-dev",
                                                               "productAPIVersion": "v1"}), encoding="utf-8")
    opts = dict(org_id=ORG, anypoint_env_id="e", gateway_id="g", gateway_name="n", gateway_version="1",
                tls_secret_group_id="s", tls_context_id="t", mcp_asset_version="1.0.0", instance_label=None,
                base_path="productTest", base_url="https://example.invalid", source_api_instance_id="21166244",
                source_instance_label=None, source_product_api_version=None)
    conn = provision_bridge.build_payload(cfg, "dev", art, opts)["routing"][0]["upstreams"][0]["connection"]
    assert conn["apiVersionId"] == "21166244" and conn["label"] == "v1:21166244" and conn["productAPIVersion"] == "v1"


def test_readback_requires_connection_persisted_exactly_as_sent(cfg, tmp_path):
    art = _artifacts(cfg, tmp_path)
    opts = dict(org_id=ORG, anypoint_env_id="e", gateway_id="g", gateway_name="n", gateway_version="1",
                tls_secret_group_id="s", tls_context_id="t", mcp_asset_version="1.0.0", instance_label=None,
                base_path="productTest", base_url="https://example.invalid", source_api_instance_id=UUID,
                source_instance_label="uif-product-dev", source_product_api_version="v1")
    payload = provision_bridge.build_payload(cfg, "dev", art, opts)
    sent = payload["routing"][0]["upstreams"][0]
    inst = {"id": 1, "metadata": {"generatedBy": "mcp_bridge"}, "assetId": "uif-domains-mcp", "assetVersion": "1.0.0",
            "endpoint": {"proxyUri": payload["endpoint"]["proxyUri"]},
            "routing": [{"label": "product-api", "rules": payload["routing"][0]["rules"], "upstreams": [{"id": "u1"}]}]}
    pol = {"policies": [{"template": {"assetId": a}, "upstreamId": "u1",
                         "configuration": ({"tools": sent["policies"][0]["configurationData"]["tools"]} if a == "mcp-transcoding"
                                           else payload["policies"][2]["configurationData"] if a == "mcp-transcoding-router" else {})}
                        for a in provision_bridge.EXPECTED_POLICIES]}
    good = {"upstreams": [{"id": "u1", "uri": sent["uri"], "connection": dict(sent["connection"])}]}
    ok, lines = provision_bridge.verify_readback(payload, inst, good, pol)
    assert ok, lines
    assert any(l.startswith("connection persisted as sent (apiVersionId %s, label uif-product-dev" % UUID) and l.endswith("PASS") for l in lines)
    drifted = {"upstreams": [{"id": "u1", "uri": sent["uri"], "connection": dict(sent["connection"], apiVersionId="21166244")}]}
    ok, lines = provision_bridge.verify_readback(payload, inst, drifted, pol)
    assert not ok and any("connection persisted as sent" in l and l.endswith("FAIL") for l in lines)


# ── guarded delete of ONE external instance (clean-from-scratch reset) ─────────

TARGET_UUID = "07577d9c-ae1c-4826-b0d1-1ecc2546c75d"


def _delete_fake(record, instances, deleted, *, delete_status=204):
    state = {"instances": list(instances)}

    def urlopen(req, timeout=None):
        method, path = req.get_method(), req.full_url.split("anypoint.mulesoft.com", 1)[1]
        record.append((method, path))
        if path.endswith("/oauth2/token"):
            return _Resp({"access_token": "tok"})
        if method == "GET" and path == "/exchange/api/v2/assets/%s/product-api/1.0.0" % ORG:
            return _Resp({"groupId": ORG, "assetId": "product-api", "version": "1.0.0", "versionGroup": "v1",
                          "instances": state["instances"]})
        if method == "DELETE":
            if delete_status != 204:
                raise urllib.error.HTTPError(req.full_url, delete_status, "Forbidden", None, io.BytesIO(b'{"message":"no"}'))
            deleted.append(path)
            iid = path.rsplit("/", 1)[1]
            state["instances"] = [i for i in state["instances"] if str(i.get("id")) != iid]
            class R:
                status = 204
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()
        raise AssertionError("unexpected call %s %s" % (method, path))
    return urlopen


def _del_args(tmp_path, iid, mode, confirm=None):
    return _args(tmp_path, "--delete-external-instance", iid, "--confirm-delete", confirm or iid, mode)


def test_delete_guards_pass_only_for_the_exact_external_instance():
    desc = {"assetId": "product-api", "version": "1.0.0", "versionGroup": "v1",
            "instances": [_external("uif-product-dev", iid=TARGET_UUID), MANAGED_21166244, _external("other", "https://x/", iid="aaaaaaaa-0000-0000-0000-000000000000")]}
    ok, lines, inst = esi.external_delete_guards(desc, TARGET_UUID, "uif-product-dev", UPSTREAM)
    assert ok, lines
    assert inst["id"] == TARGET_UUID
    # managed instance id: type guard + UUID guard fail
    ok, lines, _ = esi.external_delete_guards(desc, "21166244", "uif-product-dev", UPSTREAM)
    assert not ok and "type external (never a managed/API Manager instance): FAIL" in lines
    # wrong name / wrong URL / unknown id all fail
    assert not esi.external_delete_guards(desc, TARGET_UUID, "uif-product-uat", UPSTREAM)[0]
    assert not esi.external_delete_guards(desc, TARGET_UUID, "uif-product-dev", "https://elsewhere/")[0]
    assert not esi.external_delete_guards(desc, "ffffffff-ffff-ffff-ffff-ffffffffffff", "uif-product-dev", UPSTREAM)[0]


def test_delete_dry_run_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    assert esi.main(_del_args(tmp_path, TARGET_UUID, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "GUARDED EXTERNAL INSTANCE DELETE (dry-run)" in out and "DRY-RUN: guards above would be evaluated" in out


def test_delete_preflight_reads_guards_and_never_deletes(tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    record, deleted = [], []
    instances = [_external("uif-product-dev", iid=TARGET_UUID), MANAGED_21166244]
    monkeypatch.setattr(urllib.request, "urlopen", _delete_fake(record, instances, deleted))
    assert esi.main(_del_args(tmp_path, TARGET_UUID, "--preflight")) == 0
    out = capsys.readouterr().out
    for g in ("type external (never a managed/API Manager instance): PASS", "name uif-product-dev: PASS",
              "endpointUri %s: PASS" % UPSTREAM, "productAPIVersion v1 (descriptor versionGroup): PASS", "private (isPublic false): PASS"):
        assert "guard: " + g in out
    assert "target: DELETE https://anypoint.mulesoft.com/exchange/api/v2/assets/%s/product-api/versionGroups/v1/instances/external/%s" % (ORG, TARGET_UUID) in out
    assert "PREFLIGHT ONLY: all guards PASS; no delete was sent." in out
    assert deleted == [] and {m for m, p in record if not p.endswith("/oauth2/token")} == {"GET"}


def test_delete_execute_deletes_exactly_one_and_verifies(tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    record, deleted = [], []
    instances = [_external("uif-product-dev", iid=TARGET_UUID), MANAGED_21166244, _external("keep", "https://k/", iid="bbbbbbbb-0000-0000-0000-000000000000")]
    monkeypatch.setattr(urllib.request, "urlopen", _delete_fake(record, instances, deleted))
    assert esi.main(_del_args(tmp_path, TARGET_UUID, "--execute")) == 0
    out = capsys.readouterr().out
    assert deleted == ["/exchange/api/v2/assets/%s/product-api/versionGroups/v1/instances/external/%s" % (ORG, TARGET_UUID)]
    assert "instances now 2 (was 3)" in out and "VERIFIED: external instance %s removed" % TARGET_UUID in out
    # never a DELETE on the asset itself or on API Manager
    assert not any(m == "DELETE" and ("/product-api/1.0.0" in p or "/apimanager/" in p) for m, p in record)


def test_delete_refuses_managed_id_wrong_confirm_and_guard_failures(tmp_path, monkeypatch):
    _live(monkeypatch)
    record, deleted = [], []
    monkeypatch.setattr(urllib.request, "urlopen", _delete_fake(record, [MANAGED_21166244], deleted))
    with pytest.raises(SystemExit) as e:
        esi.main(_del_args(tmp_path, "21166244", "--execute"))
    assert "API Manager instance id" in str(e.value) and deleted == []
    with pytest.raises(SystemExit) as e:
        esi.main(_del_args(tmp_path, TARGET_UUID, "--execute", confirm="ffffffff-ffff-ffff-ffff-ffffffffffff"))
    assert "--confirm-delete must repeat" in str(e.value) and deleted == []
    # right id but the descriptor no longer lists it -> identity guard refuses
    with pytest.raises(SystemExit) as e:
        esi.main(_del_args(tmp_path, TARGET_UUID, "--execute"))
    assert "identity guard" in str(e.value) and deleted == []


def test_delete_rejection_is_reported_without_secrets(tmp_path, monkeypatch):
    _live(monkeypatch)
    record, deleted = [], []
    monkeypatch.setattr(urllib.request, "urlopen", _delete_fake(record, [_external("uif-product-dev", iid=TARGET_UUID)], deleted, delete_status=403))
    with pytest.raises(SystemExit) as e:
        esi.main(_del_args(tmp_path, TARGET_UUID, "--execute"))
    assert "DELETE REJECTED - HTTP 403" in str(e.value) and "s3cr3t-VALUE-never-printed" not in str(e.value)


# ── run-scoped overrides + read-only preflight for the ensure path ─────────────

POC_URL = "https://test-sementic-search-ic2gqt.rajrd4-1.usa-e1.cloudhub.io/"


def test_preflight_reports_would_create_without_writing(tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    record = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake(record, instances=[_external("uif-product-dev", iid=TARGET_UUID)]))
    rc = esi.main(_args(tmp_path, "--name", "test-unmanaged", "--upstream-url", POC_URL, "--preflight"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "RUN-SCOPED OVERRIDE: name='test-unmanaged' upstream_url=%r" % POC_URL in out
    assert "no external instance named 'test-unmanaged' exists" in out
    assert "execute would CREATE exactly ONE external instance" in out
    assert json.dumps({"name": "test-unmanaged", "endpointUri": POC_URL, "isPublic": False}) in out
    assert "productAPIVersion v1 (Exchange versionGroup), isPublic false" in out
    assert "PREFLIGHT ONLY: nothing was created or modified." in out
    assert {m for m, p in record if not p.endswith("/oauth2/token")} == {"GET"}
    assert not (tmp_path / "source-instance.json").exists()  # nothing captured on preflight


def test_preflight_reports_would_reuse(tmp_path, monkeypatch, capsys):
    _live(monkeypatch)
    record = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake(record, instances=[_external("uif-product-dev", iid=TARGET_UUID)]))
    assert esi.main(_args(tmp_path, "--preflight")) == 0
    out = capsys.readouterr().out
    assert "execute would REUSE external instance 'uif-product-dev' (id %s)" % TARGET_UUID in out
    assert not any(m == "POST" and not p.endswith("/oauth2/token") for m, p in record)


def test_overrides_are_run_scoped_and_config_untouched(cfg, tmp_path, monkeypatch, capsys):
    before = CONFIG.read_bytes()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    assert esi.main(_args(tmp_path, "--name", "test-unmanaged", "--upstream-url", POC_URL, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert '"name": "test-unmanaged"' in out and '"endpointUri": "%s"' % POC_URL in out
    assert "the domain standard (name uif-product-dev, bridge-config upstream_url) is unchanged" in out
    assert CONFIG.read_bytes() == before
    assert cfg["environments"]["dev"]["upstream_url"] != POC_URL
