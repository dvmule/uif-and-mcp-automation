"""The REAL contracts of this repository through the generic MCP path - data-driven over whatever
domains are onboarded (domains/<d>/src/mcp/bridge-config.yaml); no domain name is special here.
Layout under test: domains/<d>/src/api/<api>/openapi.yaml referencing ../../schemas/channels/<d>/*.json."""
import json
from pathlib import Path

import pytest
import yaml

import affected_domains as ad
import contract_bundle as cb
import contract_gate as cg
import generate_mcp_metadata as gm
import oas_projection as op
import release_version as rv
import shared_bridge as sb
import validate_mcp_metadata as vm

REPO = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ONBOARDED = ad.discover(str(REPO))          # every domain folder (all carry the onboarding file)
READY = ad.processable(str(REPO))            # the ones with an available contract and MCP enabled


def test_every_domain_folder_is_onboarded_and_ready_domains_resolve_exactly_one_contract():
    assert ONBOARDED == ad.domain_folders(str(REPO)) and ad.missing_onboarding(str(REPO)) == [], "a domains/ folder lacks src/mcp/bridge-config.yaml"
    assert ad.missing_configuration(str(REPO), ONBOARDED) == []  # no ambiguous / broken onboarding file
    assert READY, "no processable domain in this repository"
    for d in READY:
        rel, problem = ad.resolve_contract(str(REPO), d)
        assert problem is None and (REPO / rel).is_file() and rel.startswith("domains/%s/src/api/" % d)
        cfg = yaml.safe_load((REPO / "domains" / d / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8"))
        if len(ad.contract_candidates(str(REPO), d)) > 1:
            assert cfg.get("contract"), "%s ships several contracts and must choose one explicitly" % d


def test_skipped_domains_are_visible_and_other_contracts_never_enter_a_bundle():
    rep = ad.report(str(REPO), ONBOARDED)
    assert rep["errors"] == [] and rep["processed"] == READY
    for d in rep["skipped_no_contract"]:
        assert ad.contract_candidates(str(REPO), d) == []  # skipped only because no contract exists yet
    for d in rep["skipped_disabled"]:
        cfg = yaml.safe_load((REPO / "domains" / d / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8"))
        assert cfg["mcp_enabled"] is False  # skipped only because the domain says so
    # a domain's OTHER contracts (not the selected one) are never part of its bundle
    for d in READY:
        chosen, _ = ad.resolve_contract(str(REPO), d)
        _, c = cb.bundle(str(REPO / chosen))
        for other in ad.contract_candidates(str(REPO), d):
            if other != chosen:
                assert other.split("/src/", 1)[1] not in c["files"]


@pytest.mark.parametrize("domain", READY)
def test_real_contract_bundles_projects_generates_and_passes_the_gate(domain, tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("NETWORK")))
    rel, _ = ad.resolve_contract(str(REPO), domain)
    spec = REPO / rel
    doc, c = cb.bundle(str(spec))
    assert c["api_root"] == cb.contract_root(str(spec)) and Path(c["api_root"]).name == "src"
    assert all(not f.startswith("..") for f in c["files"]) and cb.external_refs(doc) == []
    projected, stats = op.project_oas31_to_30(doc)
    assert projected["openapi"] == "3.0.3" and stats["operations"] >= 1
    out = tmp_path / "build"
    assert rv.main(["--spec", str(spec), "--rest-asset-id", "uif-%s-api" % domain, "--org-id", "o", "--out", str(out), "--dry-run"]) == 0
    rel_files = json.loads((out / rv.RELEASE_FILE).read_text(encoding="utf-8"))["contract_files"]
    assert rel_files == c["files"] and rel.split("/src/", 1)[1] in rel_files
    assert gm.main(["--spec", str(out / rv.RELEASE_SPEC), "--source-asset-id", "uif-%s-api" % domain, "--source-version", "1.0.0",
                    "--asset-id", "uif-domains-mcp", "--asset-version", "1.0.0", "--org-id", "o", "--out", str(out)]) == 0
    assert vm.main(["--metadata", str(out / "mcp-metadata.json"), "--mappings", str(out / "tool-mappings.json")]) == 0
    assert cg.main(["--spec", str(spec), "--metadata", str(out / "mcp-metadata.json"), "--mappings", str(out / "tool-mappings.json")]) == 0
    # the design rule: every mapping path = the contract's servers[0] base path + operation path
    loader = gm.SpecLoader(str(spec))
    base = gm.server_base_path(loader.entry)
    mappings = json.loads((out / "tool-mappings.json").read_text(encoding="utf-8"))["tools"]
    assert base and all(m["path"].startswith(base + "/") for m in mappings)


@pytest.mark.parametrize("domain", READY)
def test_real_contract_dependency_change_is_a_contract_change_and_unreferenced_files_are_not(domain, tmp_path):
    import shutil
    rel, _ = ad.resolve_contract(str(REPO), domain)
    src_root = REPO / "domains" / domain / "src"
    work = tmp_path / "domains" / domain / "src"
    shutil.copytree(src_root, work)  # copy of the real domain source, never the repository itself
    spec = work / rel.split("/src/", 1)[1]
    _, c = cb.bundle(str(spec))
    deps = [f for f in c["files"] if f != rel.split("/src/", 1)[1]]
    before = rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w0"))
    (work / "unreferenced-note.json").write_text('{"type": "string"}', encoding="utf-8")
    assert rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w1")) == before  # unreferenced file: no change
    if deps:
        dep = work / deps[0]
        text = dep.read_text(encoding="utf-8")
        dep.write_text(text.replace('"description": "', '"description": "CHANGED ', 1), encoding="utf-8")
        assert rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w2")) != before  # dependency-only change IS a change


def test_effective_upstream_is_the_shared_origin_for_every_onboarded_domain():
    import platform_config as pc
    from anypoint_fake import TEST_PLATFORM_ENV
    repo = yaml.safe_load((SCRIPTS / "anypoint-environments.yaml").read_text(encoding="utf-8"))
    shared = sb.load_shared(pc.resolve_environment(repo, "dev", env=TEST_PLATFORM_ENV), "dev")
    for d in ONBOARDED:  # every onboarded domain, ready or skipped, resolves the same shared upstream and conventions
        cfg = yaml.safe_load((REPO / "domains" / d / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8"))
        eff = sb.effective_config(cfg, shared)
        assert eff["environments"]["dev"]["upstream_url"] == TEST_PLATFORM_ENV["UIF_UPSTREAM_ORIGIN"] and eff["source_asset_id"] == "uif-%s-api" % d
