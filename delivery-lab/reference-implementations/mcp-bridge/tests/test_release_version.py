"""release_version.py - the ONE owner of the Exchange release version.

Exchange is faked as an in-memory store addressed exactly the way the module
reads it (latest-asset descriptor, per-version descriptor, published file);
every test asserts the decision, the resolved version and that nothing else is
touched. No network anywhere.
"""
import json
import urllib.request
from pathlib import Path

import pytest
import yaml

import deploy_bridge
import exchange_publish as xp
import release_version as rv
from generate_mcp_metadata import generate, write_artifacts

REPO = Path(__file__).resolve().parents[4]
from anypoint_fake import PRODUCT_SPEC
CONTRACT = PRODUCT_SPEC
ORG = "8c27297e-4bcf-4c53-99eb-ffd7b1069294"

DOMAINS = [("product", "product-api", "uif-product-mcp"),
           ("price", "price-api", "uif-price-mcp"),
           ("orders", "orders-catalogue-api", "uif-orders-mcp")]


# ── fake Exchange: only the three targeted read shapes the module uses ──────────

class FakeExchange:
    def __init__(self):
        self.store = {}      # (asset, version) -> {"files": {classifier: bytes}, "descriptor": {...}}
        self.latest_override = {}
        self.extra_listed = {}
        self.reads = []

    def publish(self, asset, version, content, classifier="oas"):
        self.store[(asset, version)] = {"files": {classifier: content},
                                        "descriptor": {"groupId": ORG, "assetId": asset, "version": version,
                                                       "versionGroup": "v" + version.split(".")[0]}}

    def _versions(self, asset):
        return [v for (a, v) in self.store if a == asset]

    # reader interface
    def latest_descriptor(self, group, asset):
        self.reads.append(("latest", asset))
        versions = self._versions(asset)
        if not versions:
            return None
        latest = self.latest_override.get(asset) or max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))
        d = dict(self.store[(asset, latest)]["descriptor"])
        d["otherVersions"] = [v for v in versions if v != latest] + self.extra_listed.get(asset, [])
        return d

    def descriptor(self, group, asset, version):
        self.reads.append(("descriptor", asset, version))
        entry = self.store.get((asset, version))
        return dict(entry["descriptor"], _asset=asset, _version=version) if entry else None

    def published_file(self, descriptor, classifier, main_file):
        self.reads.append(("file", descriptor["_asset"], descriptor["_version"], classifier))
        return self.store[(descriptor["_asset"], descriptor["_version"])]["files"].get(classifier)


def _changed_contract(tmp_path, name="changed.yaml"):
    """The Product contract plus one GET operation - a real content change."""
    doc = yaml.safe_load(CONTRACT.read_text(encoding="utf-8-sig"))
    doc["paths"]["/contract-change-probe"] = {"get": {"operationId": "contractChangeProbe", "summary": "s",
                                                 "responses": {"200": {"description": "ok"}}}}
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return str(p)


def _decide(ex, spec, tmp_path, rest="product-api", mcp="uif-product-mcp"):
    return rv.decide_release(str(spec), ex, ORG, rest, mcp, str(tmp_path / "work"))


# ── the decision table ─────────────────────────────────────────────────────────

def test_brand_new_domain_resolves_to_1_0_0(tmp_path):
    ex = FakeExchange()
    rel = _decide(ex, CONTRACT, tmp_path)
    assert (rel["decision"], rel["version"], rel["latest"]) == ("initial", "1.0.0", None)
    assert rel["exchange_consulted"] is True
    # targeted reads only: the latest-asset descriptor + the two 1.0.0 consistency checks
    assert ex.reads == [("latest", "product-api"), ("descriptor", "product-api", "1.0.0"), ("descriptor", "uif-product-mcp", "1.0.0")]


def test_existing_1_0_0_with_changed_contract_bumps_to_1_0_1(tmp_path):
    ex = FakeExchange()
    ex.publish("product-api", "1.0.0", rv.projection_at(str(CONTRACT), "1.0.0", str(tmp_path / "w0")))
    rel = _decide(ex, _changed_contract(tmp_path), tmp_path)
    assert (rel["decision"], rel["version"], rel["latest"]) == ("bump", "1.0.1", "1.0.0")
    assert rel["compared"] == "different" and rel["version_group"] == "v1"


def test_existing_1_0_8_with_changed_contract_bumps_to_1_0_9(tmp_path):
    ex = FakeExchange()
    for v in ("1.0.6", "1.0.7", "1.0.8"):
        ex.publish("product-api", v, rv.projection_at(str(CONTRACT), v, str(tmp_path / ("w" + v))))
        ex.publish("uif-product-mcp", v, b'{"tools": []}', classifier="mcp-metadata")
    rel = _decide(ex, _changed_contract(tmp_path), tmp_path)
    assert (rel["decision"], rel["version"], rel["latest"]) == ("bump", "1.0.9", "1.0.8")
    # only the domain's two assets were ever addressed
    assert {r[1] for r in ex.reads} == {"product-api", "uif-product-mcp"}
    assert ("descriptor", "product-api", "1.0.9") in ex.reads and ("descriptor", "uif-product-mcp", "1.0.9") in ex.reads


def test_existing_latest_with_identical_contract_is_noop_same_version(tmp_path):
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w")))
    rel = _decide(ex, CONTRACT, tmp_path)
    assert (rel["decision"], rel["version"], rel["latest"], rel["compared"]) == ("noop", "1.0.8", "1.0.8", "byte-identical")
    assert not any(r[0] == "descriptor" and r[2] == "1.0.9" for r in ex.reads)  # no next-version probing on NO-OP


def test_reserialized_published_file_is_still_identical(tmp_path):
    """Exchange may store/return the YAML re-serialized; the CONTENT is unchanged -> NO-OP."""
    ex = FakeExchange()
    ours = rv.projection_at(str(CONTRACT), "1.0.3", str(tmp_path / "w"))
    reserialized = yaml.safe_dump(yaml.safe_load(ours), sort_keys=True, indent=4).encode()
    assert reserialized != ours
    ex.publish("product-api", "1.0.3", reserialized)
    rel = _decide(ex, CONTRACT, tmp_path)
    assert (rel["decision"], rel["version"], rel["compared"]) == ("noop", "1.0.3", "same document (serialization differs)")


def test_repeated_execution_never_creates_another_version(tmp_path):
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w8")))
    changed = _changed_contract(tmp_path)
    first = _decide(ex, changed, tmp_path)
    assert first["version"] == "1.0.9"
    # the release is published (what exchange_publish would upload from the stamped copy)
    ex.publish("product-api", "1.0.9", rv.projection_at(changed, "1.0.9", str(tmp_path / "w9")))
    for _ in range(3):  # CI reruns with the exact same contract
        again = _decide(ex, changed, tmp_path)
        assert (again["decision"], again["version"]) == ("noop", "1.0.9")
    assert ("descriptor", "product-api", "1.0.10") not in ex.reads
    # and the unchanged ORIGINAL contract, resolved against the same Exchange, is a real change again
    assert _decide(ex, CONTRACT, tmp_path)["version"] == "1.0.10"


@pytest.mark.parametrize("domain,rest,mcp", DOMAINS)
def test_same_shared_logic_for_every_domain(tmp_path, domain, rest, mcp):
    """Product, Price and an arbitrary domain: identical rules, only the asset ids differ."""
    ex = FakeExchange()
    assert _decide(ex, CONTRACT, tmp_path, rest, mcp)["version"] == "1.0.0"
    ex.publish(rest, "1.0.0", rv.projection_at(str(CONTRACT), "1.0.0", str(tmp_path / "w0")))
    noop = _decide(ex, CONTRACT, tmp_path, rest, mcp)
    assert (noop["decision"], noop["version"]) == ("noop", "1.0.0")
    bump = _decide(ex, _changed_contract(tmp_path), tmp_path, rest, mcp)
    assert (bump["decision"], bump["version"]) == ("bump", "1.0.1")
    assert bump["rest_asset_id"] == rest and bump["mcp_asset_id"] == mcp
    assert {r[1] for r in ex.reads} == {rest, mcp}  # never another domain's asset


# ── one version, stamped into artifacts; the authoritative contract untouched ─────

def test_write_release_stamps_a_build_copy_and_never_touches_the_source(tmp_path):
    before = CONTRACT.read_bytes()
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w")))
    rel = rv.write_release(str(tmp_path / "build"), _decide(ex, _changed_contract(tmp_path), tmp_path), str(CONTRACT))
    assert CONTRACT.read_bytes() == before
    stamped = yaml.safe_load((tmp_path / "build" / rv.RELEASE_SPEC).read_text(encoding="utf-8"))
    original = yaml.safe_load(CONTRACT.read_text(encoding="utf-8-sig"))
    assert stamped["info"]["version"] == "1.0.9" and original["info"]["version"] != "1.0.9"  # git version is NOT the release
    assert rel["source_info_version"] == str(original["info"]["version"]) and rel["release_spec"] == rv.RELEASE_SPEC
    stamped["info"]["version"] = original["info"]["version"]
    assert stamped == original  # nothing but the version differs
    on_disk = json.loads((tmp_path / "build" / rv.RELEASE_FILE).read_text(encoding="utf-8"))
    assert on_disk["version"] == "1.0.9" and on_disk["decision"] == "bump"


def test_rest_and_mcp_artifacts_carry_exactly_the_same_release_version(tmp_path):
    """The stamped copy feeds BOTH publishers: the REST projection (frozen version guard
    passes because the copy's info.version == the release) and the MCP metadata."""
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w")))
    build = tmp_path / "build"
    rel = rv.write_release(str(build), _decide(ex, _changed_contract(tmp_path), tmp_path), str(CONTRACT))
    stamped = str(build / rv.RELEASE_SPEC)
    content, stats = xp.build_rest_projection(stamped, rel["version"])       # REST @ release
    assert stats["info_version"] == "1.0.9" and b"version: 1.0.9" in content
    with pytest.raises(SystemExit):                                           # and NOT at any other version
        xp.build_rest_projection(stamped, "9.9.9")
    write_artifacts(generate(stamped, source_ref={"assetId": "product-api", "version": rel["version"], "groupId": ORG},
                             mcp_asset={"assetId": "uif-product-mcp", "version": rel["version"]}, org_id=ORG), str(build))
    meta = json.loads((build / "mcp-metadata.json").read_text(encoding="utf-8"))
    assert meta["connections"][0]["ref"]["version"] == rel["version"] == "1.0.9"  # MCP @ the same release


# ── fail closed on ambiguous / inconsistent Exchange state ───────────────────────

def test_non_semver_latest_fails_closed(tmp_path):
    ex = FakeExchange()
    ex.store[("product-api", "v1")] = {"files": {}, "descriptor": {"version": "v1"}}
    ex.latest_override["product-api"] = "v1"
    with pytest.raises(SystemExit) as e:
        _decide(ex, CONTRACT, tmp_path)
    assert "not a strict semantic version" in str(e.value)


def test_listed_snapshot_or_prerelease_version_fails_closed(tmp_path):
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w")))
    ex.extra_listed["product-api"] = ["1.0.9-SNAPSHOT"]
    with pytest.raises(SystemExit) as e:
        _decide(ex, CONTRACT, tmp_path)
    assert "1.0.9-SNAPSHOT" in str(e.value) and "not a strict semantic version" in str(e.value)


def test_listed_version_above_reported_latest_fails_closed(tmp_path):
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w8")))
    ex.publish("product-api", "1.0.9", rv.projection_at(str(CONTRACT), "1.0.9", str(tmp_path / "w9")))
    ex.latest_override["product-api"] = "1.0.8"  # Exchange's "latest" disagrees with its own list
    with pytest.raises(SystemExit) as e:
        _decide(ex, CONTRACT, tmp_path)
    assert "also lists 1.0.9" in str(e.value) and "ambiguous" in str(e.value)


def test_next_rest_version_already_existing_fails_closed(tmp_path):
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w8")))
    ex.publish("product-api", "1.0.9", b"openapi: 3.0.3\n")
    ex.latest_override["product-api"] = "1.0.8"
    ex.extra_listed["product-api"] = []
    # hide 1.0.9 from the listing to simulate a latest lookup that lags behind
    ex.latest_descriptor = lambda group, asset: dict(ex.store[(asset, "1.0.8")]["descriptor"], otherVersions=[])
    with pytest.raises(SystemExit) as e:
        _decide(ex, _changed_contract(tmp_path), tmp_path)
    assert "product-api@1.0.9 already exists" in str(e.value) and "non-linear" in str(e.value)


def test_mcp_history_ahead_of_rest_fails_closed(tmp_path):
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w")))
    ex.publish("uif-product-mcp", "1.0.9", b"{}", classifier="mcp-metadata")
    with pytest.raises(SystemExit) as e:
        _decide(ex, _changed_contract(tmp_path), tmp_path)
    assert "uif-product-mcp@1.0.9 already exists" in str(e.value) and "ahead of the REST asset" in str(e.value)


def test_brand_new_rest_but_existing_mcp_history_fails_closed(tmp_path):
    ex = FakeExchange()
    ex.publish("uif-product-mcp", "1.0.0", b"{}", classifier="mcp-metadata")
    with pytest.raises(SystemExit) as e:
        _decide(ex, CONTRACT, tmp_path)
    assert "uif-product-mcp@1.0.0 exists" in str(e.value) and "inconsistent" in str(e.value)


def test_latest_without_downloadable_oas_file_fails_closed(tmp_path):
    ex = FakeExchange()
    ex.publish("product-api", "1.0.8", b"", classifier="fat-oas")  # no `oas` file to compare against
    with pytest.raises(SystemExit) as e:
        _decide(ex, CONTRACT, tmp_path)
    assert "no downloadable `oas` file" in str(e.value)


def test_existing_immutable_version_with_different_content_fails_closed_at_publish(tmp_path, monkeypatch):
    """The per-asset publish guard is unchanged: at the resolved version, different
    content on an existing immutable version is a conflict, never an overwrite."""
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")
    monkeypatch.setattr(xp, "_token", lambda *a: "tok")
    monkeypatch.setattr(xp, "_get_descriptor", lambda *a: {"files": [{"classifier": "mcp-metadata"}]})
    monkeypatch.setattr(xp, "_download_published_file", lambda *a: b'{"tools": ["something-else"]}')
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no POST may happen")))
    (tmp_path / "mcp-metadata.json").write_text('{"tools": []}', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        xp.main(["mcp", "--artifacts", str(tmp_path), "--org-id", ORG, "--asset-id", "uif-product-mcp",
                 "--asset-version", "1.0.9", "--execute"])
    assert "FAIL CLOSED" in str(e.value) and "immutable MCP asset version 1.0.9" in str(e.value)


# ── CLI modes ──────────────────────────────────────────────────────────────────

def test_dry_run_previews_with_the_contract_version_and_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    gh = tmp_path / "gh_output"
    gh.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    before = CONTRACT.read_bytes()
    assert rv.main(["--spec", str(CONTRACT), "--rest-asset-id", "product-api", "--mcp-asset-id", "uif-product-mcp",
                    "--org-id", ORG, "--out", str(tmp_path / "build")]) == 0
    git_version = str(yaml.safe_load(CONTRACT.read_text(encoding="utf-8-sig"))["info"]["version"])  # whatever the contract says today
    rel = json.loads((tmp_path / "build" / rv.RELEASE_FILE).read_text(encoding="utf-8"))
    assert (rel["decision"], rel["version"], rel["exchange_consulted"]) == ("preview", git_version, False)
    assert (tmp_path / "build" / rv.RELEASE_SPEC).exists() and CONTRACT.read_bytes() == before
    outputs = dict(l.split("=", 1) for l in gh.read_text(encoding="utf-8").splitlines() if l)
    assert outputs["version"] == git_version and outputs["decision"] == "preview" and outputs["release_spec"] == rv.RELEASE_SPEC
    out = capsys.readouterr().out
    assert "DRY-RUN: nothing sent" in out and "product-api@%s  ==  uif-product-mcp@%s" % (git_version, git_version) in out


def test_resolve_requires_the_live_latch_and_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_LIVE_ENABLED", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    with pytest.raises(SystemExit) as e:
        rv.main(["--spec", str(CONTRACT), "--rest-asset-id", "product-api", "--mcp-asset-id", "uif-product-mcp",
                 "--org-id", ORG, "--out", str(tmp_path), "--resolve"])
    assert "MCP_LIVE_ENABLED" in str(e.value) and "READS" in str(e.value)


def test_resolve_uses_targeted_reads_only_and_writes_nothing_remote(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCP_LIVE_ENABLED", "true")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "s3cr3t-VALUE-never-printed")
    record = []
    ours = rv.projection_at(str(CONTRACT), "1.0.8", str(tmp_path / "w"))

    class _Resp:
        def __init__(self, body, status=200):
            self._b = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.status = status
        def read(self):
            return self._b
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        record.append((method, url))
        if url.endswith("/oauth2/token"):
            return _Resp({"access_token": "tok"})
        if url.endswith("/assets/%s/product-api/asset" % ORG):
            return _Resp({"version": "1.0.8", "versionGroup": "v1", "otherVersions": ["1.0.7"]})
        if url.endswith("/assets/%s/product-api/1.0.8" % ORG):
            return _Resp({"version": "1.0.8", "files": [{"classifier": "oas", "packaging": "yaml", "externalLink": "https://files.example/oas.yaml"}]})
        if url == "https://files.example/oas.yaml":
            return _Resp(ours)
        raise AssertionError("unexpected %s %s" % (method, url))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert rv.main(["--spec", str(CONTRACT), "--rest-asset-id", "product-api", "--mcp-asset-id", "uif-product-mcp",
                    "--org-id", ORG, "--out", str(tmp_path / "build"), "--resolve"]) == 0
    assert all(m == "GET" for m, u in record if not u.endswith("/oauth2/token"))
    assert not any("search" in u or "assets?" in u for m, u in record)  # no scan
    rel = json.loads((tmp_path / "build" / rv.RELEASE_FILE).read_text(encoding="utf-8"))
    assert (rel["decision"], rel["version"]) == ("noop", "1.0.8")
    out = capsys.readouterr().out
    assert "RESOLVED with Exchange READS only" in out
    # the run is auditable from its own output: every read traced, method + path only
    assert "exchange reads: 3 GET, 0 POST/PATCH/PUT/DELETE" in out
    assert rel["exchange_reads"] == [
        "GET /exchange/api/v2/assets/%s/product-api/asset -> 200 version=1.0.8 versionGroup=v1 listed=['1.0.7'] keys=['otherVersions', 'version', 'versionGroup']" % ORG,
        "GET /exchange/api/v2/assets/%s/product-api/1.0.8 -> 200 (exists; files: oas/yaml)" % ORG,
        "GET https://files.example/oas.yaml (published oas file) -> %d bytes" % len(ours)]
    assert "Bearer tok" not in out and "Bearer" not in out and "s3cr3t" not in out  # token value / secret never printed


def test_read_release_rejects_a_non_semver_release_file(tmp_path):
    (tmp_path / rv.RELEASE_FILE).write_text(json.dumps({"version": "latest"}), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        rv.read_release(str(tmp_path))
    assert "not a strict semantic version" in str(e.value)


# ── deploy_bridge consumes the ONE version and refuses drift ──────────────────────

def _artifacts_at(tmp_path, version):
    out = tmp_path / ("art-" + version)
    write_artifacts(generate(str(CONTRACT), source_ref={"assetId": "product-api", "version": version, "groupId": ORG},
                             mcp_asset={"assetId": "uif-product-mcp", "version": version}, org_id=ORG), str(out))
    return str(out)


def _release_file(tmp_path, version):
    p = tmp_path / ("rel-" + version) / rv.RELEASE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": version, "decision": "bump"}), encoding="utf-8")
    return str(p)


from anypoint_fake import PLATFORM, PRODUCT_CFG as CONFIG  # test-owned canonical Product config + platform


def test_deploy_bridge_takes_the_version_from_the_release_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in dry-run")))
    rc = deploy_bridge.main(["--config", str(CONFIG), "--artifacts", _artifacts_at(tmp_path, "1.0.9"), "--environment", "dev", "--platform-config", PLATFORM,
                             "--release-file", _release_file(tmp_path, "1.0.9"), "--out", str(tmp_path / "out"), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "release   : 1.0.9 (decision bump" in out and "domain route    : product-api -> product-api@1.0.9" in out
    assert '"version": "1.0.9"' in out  # this route's connection.version; the shared MCP asset version is resolved separately
    assert "uif-product-mcp" not in out  # no per-domain MCP asset any more


def test_deploy_bridge_refuses_artifacts_generated_at_another_version(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(["--config", str(CONFIG), "--artifacts", _artifacts_at(tmp_path, "1.0.8"), "--environment", "dev", "--platform-config", PLATFORM,
                            "--release-file", _release_file(tmp_path, "1.0.9"), "--out", str(tmp_path / "out"), "--dry-run"])
    assert "binds the REST asset at 1.0.8 but the release version is 1.0.9" in str(e.value)


def test_deploy_bridge_refuses_a_disagreeing_explicit_version_and_requires_one_source(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    art = _artifacts_at(tmp_path, "1.0.9")
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(["--config", str(CONFIG), "--artifacts", art, "--environment", "dev", "--platform-config", PLATFORM, "--rest-asset-version", "1.0.8",
                            "--release-file", _release_file(tmp_path, "1.0.9"), "--out", str(tmp_path / "out"), "--dry-run"])
    assert "--rest-asset-version 1.0.8 != release version 1.0.9" in str(e.value)
    with pytest.raises(SystemExit) as e:
        deploy_bridge.main(["--config", str(CONFIG), "--artifacts", art, "--environment", "dev", "--platform-config", PLATFORM, "--out", str(tmp_path / "out"), "--dry-run"])
    assert "--release-file" in str(e.value)
