"""The onboarding lifecycle (scenarios A-O): every UIF domain folder carries bridge-config.yaml
from day one; contracts are discovered when they appear; skips are visible, never silent;
ambiguity and broken explicit configuration fail closed; mcp_enabled gates MCP exposure.
Synthetic domains throughout - nothing here depends on a real domain name."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

import affected_domains as ad
import reconcile_domains as rd
import release_version as rv
from anypoint_fake import PLATFORM, synthetic_domain

REPO = Path(__file__).resolve().parents[4]


def onboard(root, domain, **cfg):
    p = root / "domains" / domain / "src" / "mcp" / "bridge-config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"domain": domain, **cfg}, sort_keys=False), encoding="utf-8")
    return p


def contract(root, domain, api=None, doc=None):
    rel = ("src/api/%s/openapi.yaml" % api) if api else "src/api/openapi.yaml"
    p = root / "domains" / domain / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(doc or {"openapi": "3.1.0", "info": {"title": domain, "version": "1.0.0"},
                                         "servers": [{"url": "https://h/uif/v1"}],
                                         "paths": {"/%s/{id}" % domain: {"get": {"description": "d", "parameters": [
                                             {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                                             "responses": {"200": {"description": "ok"}}}}}}, sort_keys=False), encoding="utf-8")
    return p


def run_detect(root, tmp_path, monkeypatch, *argv):
    out = tmp_path / "GITHUB_OUTPUT"
    out.write_text("", encoding="utf-8")
    summary = tmp_path / "GITHUB_STEP_SUMMARY"
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    rc = ad.main(["--repo-root", str(root), *argv])
    outputs = dict(l.split("=", 1) for l in out.read_text(encoding="utf-8").splitlines() if l)
    return rc, outputs, summary.read_text(encoding="utf-8")


# ── A / H: onboarded, no contract -> clean visible SKIP ────────────────────────

def test_A_H_onboarded_domain_without_contract_is_skipped_cleanly_and_visibly(tmp_path, monkeypatch, capsys):
    onboard(tmp_path, "zeta")
    rec = ad.classify(str(tmp_path), "zeta")
    assert rec["status"] == ad.SKIP_NO_CONTRACT and "contract not available" in rec["reason"] and rec["contract"] is None
    assert ad.discover(str(tmp_path)) == ["zeta"] and ad.processable(str(tmp_path)) == [] and ad.missing_configuration(str(tmp_path), ["zeta"]) == []
    rc, out, summary = run_detect(tmp_path, tmp_path, monkeypatch, "--all")
    assert rc == 0 and out["domains"] == "[]" and out["count"] == "0" and json.loads(out["skipped_no_contract"]) == ["zeta"]
    printed = capsys.readouterr().out
    assert "SKIP    zeta" in printed and "contract not available" in printed
    assert "| zeta | skip:no-contract |" in summary and "skipped domains ran NOTHING" in summary


# ── B / I: the contract appears later -> discovered and processed, no config change ──

def test_B_I_contract_added_later_turns_the_same_onboarding_file_into_ready(tmp_path, monkeypatch):
    cfg = onboard(tmp_path, "zeta")
    before = cfg.read_bytes()
    assert ad.classify(str(tmp_path), "zeta")["status"] == ad.SKIP_NO_CONTRACT
    contract(tmp_path, "zeta", api="zeta")
    rec = ad.classify(str(tmp_path), "zeta")
    assert rec["status"] == ad.READY and rec["contract"] == "domains/zeta/src/api/zeta/openapi.yaml" and rec["reason"] == "single contract candidate"
    assert cfg.read_bytes() == before  # nothing in the onboarding file changed
    # the commit that adds the contract is a domain-owned change under src/api/** -> selected
    assert ad.affected(["domains/zeta/src/api/zeta/openapi.yaml"], ad.discover(str(tmp_path))) == ["zeta"]
    changed = tmp_path / "changed.txt"
    changed.write_text("domains/zeta/src/api/zeta/openapi.yaml\n", encoding="utf-8")
    rc, out, _ = run_detect(tmp_path, tmp_path, monkeypatch, "--changed-files", str(changed))
    assert rc == 0 and json.loads(out["domains"]) == ["zeta"] and out["count"] == "1"


def test_push_triggers_cover_newly_added_contracts_and_schemas():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "mcp.yml").read_text(encoding="utf-8"))
    paths = (wf.get(True) or wf.get("on"))["push"]["paths"]
    assert "domains/*/src/api/**" in paths and "domains/*/src/schemas/**" in paths and "domains/*/src/mcp/**" in paths


# ── C / D / E: unchanged -> NO-OP, changed root or schema -> processed ─────────────

def test_C_D_E_release_comparison_sees_root_and_schema_changes_but_not_noise(tmp_path):
    onboard(tmp_path, "zeta")
    (tmp_path / "domains" / "zeta" / "src" / "schemas" / "channels" / "zeta").mkdir(parents=True)
    (tmp_path / "domains" / "zeta" / "src" / "schemas" / "channels" / "zeta" / "zeta-data-api-created.json").write_text(
        json.dumps({"type": "object", "properties": {"qty": {"type": "integer"}}}), encoding="utf-8")
    doc = {"openapi": "3.1.0", "info": {"title": "zeta", "version": "1.0.0"}, "servers": [{"url": "https://h/uif/v1"}],
           "paths": {"/zeta/{id}": {"get": {"description": "d", "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                                             "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "../../schemas/channels/zeta/zeta-data-api-created.json"}}}}}}}}}
    spec = contract(tmp_path, "zeta", api="zeta", doc=doc)
    base = rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w0"))
    assert rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w1")) == base  # C: unchanged -> identical -> NO-OP
    schema = tmp_path / "domains" / "zeta" / "src" / "schemas" / "channels" / "zeta" / "zeta-data-api-created.json"
    schema.write_text(json.dumps({"type": "object", "properties": {"qty": {"type": "number"}}}), encoding="utf-8")
    after_schema = rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w2"))
    assert after_schema != base  # E: referenced schema changed -> processed
    doc["paths"]["/zeta/{id}"]["get"]["description"] = "changed"
    contract(tmp_path, "zeta", api="zeta", doc=doc)
    assert rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w3")) != after_schema  # D: root changed -> processed
    (tmp_path / "domains" / "zeta" / "src" / "api" / "zeta" / "notes.json").write_text("{}", encoding="utf-8")
    assert rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w4")) == rv.projection_at(str(spec), "1.0.0", str(tmp_path / "w5"))  # noise -> NO-OP
    assert ad.affected(["domains/zeta/src/schemas/channels/zeta/zeta-data-api-created.json"], ["zeta"]) == ["zeta"]  # E wakes the run


# ── F / G: ambiguity fails closed; explicit selection wins ─────────────────────────

def test_F_G_multiple_contracts_fail_closed_unless_one_is_selected_explicitly(tmp_path, monkeypatch):
    onboard(tmp_path, "zeta")
    contract(tmp_path, "zeta", api="zeta")
    contract(tmp_path, "zeta", api="marketing-copy")
    rec = ad.classify(str(tmp_path), "zeta")
    assert rec["status"] == ad.ERROR and "ships 2 OpenAPI contracts" in rec["reason"] and "never guesses" in rec["reason"]
    with pytest.raises(SystemExit) as e:
        run_detect(tmp_path, tmp_path, monkeypatch, "--all")
    assert "REFUSED: domain configuration error" in str(e.value) and "zeta: domains/zeta ships 2 OpenAPI contracts" in str(e.value)
    onboard(tmp_path, "zeta", contract="src/api/zeta/openapi.yaml")
    rec = ad.classify(str(tmp_path), "zeta")
    assert rec["status"] == ad.READY and rec["contract"] == "domains/zeta/src/api/zeta/openapi.yaml" and rec["reason"] == "explicit contract"
    rc, out, _ = run_detect(tmp_path, tmp_path, monkeypatch, "--all")
    assert rc == 0 and json.loads(out["domains"]) == ["zeta"]
    # G against the real repository: Product selects explicitly; marketing-copy is never the contract
    rec = ad.classify(str(REPO), "product")
    assert rec["status"] == ad.READY and rec["contract"] == "domains/product/src/api/product/openapi.yaml"
    assert "marketing-copy" in " ".join(ad.contract_candidates(str(REPO), "product")) and "marketing-copy" not in rec["contract"]


# ── J / N: a folder without the onboarding file ─────────────────────────────────────

def test_J_N_folder_without_onboarding_file_is_reported_excluded_and_fails_repository_validation(tmp_path, monkeypatch, capsys):
    onboard(tmp_path, "alpha")
    contract(tmp_path, "alpha", api="alpha")
    (tmp_path / "domains" / "newdomain" / "src" / "api" / "newdomain").mkdir(parents=True)
    contract(tmp_path, "newdomain", api="newdomain")  # a contract, but no bridge-config.yaml
    assert ad.domain_folders(str(tmp_path)) == ["alpha", "newdomain"] and ad.discover(str(tmp_path)) == ["alpha"]
    assert ad.missing_onboarding(str(tmp_path)) == ["newdomain"] and ad.classify(str(tmp_path), "newdomain")["status"] == ad.MISSING_CONFIG
    rc, out, summary = run_detect(tmp_path, tmp_path, monkeypatch, "--all")  # J: reported, excluded, the others still run
    assert rc == 0 and json.loads(out["domains"]) == ["alpha"] and json.loads(out["missing_config"]) == ["newdomain"]
    assert "::warning::MISSING ONBOARDING CONFIGURATION" in capsys.readouterr().out and "| newdomain | missing-config |" in summary
    assert ad.affected(["domains/newdomain/src/api/newdomain/openapi.yaml"], ad.discover(str(tmp_path))) == []  # never processed
    with pytest.raises(SystemExit) as e:  # an explicit request for it is refused
        run_detect(tmp_path, tmp_path, monkeypatch, "--only", "newdomain")
    assert "newdomain not onboarded" in str(e.value)
    # N: repository validation = every domain folder must carry the onboarding file
    assert ad.missing_onboarding(str(tmp_path)) != []  # this synthetic repo FAILS that rule ...
    assert ad.missing_onboarding(str(REPO)) == []  # ... the real repository passes it (test_repository_contracts enforces it)


# ── K / L: mcp_enabled ───────────────────────────────────────────────────────────────

def test_K_L_mcp_disabled_skips_an_existing_contract_and_enabling_makes_it_processable(tmp_path, monkeypatch, capsys):
    onboard(tmp_path, "zeta", mcp_enabled=False)
    contract(tmp_path, "zeta", api="zeta")
    rec = ad.classify(str(tmp_path), "zeta")
    assert rec["status"] == ad.SKIP_DISABLED and "MCP disabled" in rec["reason"] and rec["contract"] is None
    rc, out, summary = run_detect(tmp_path, tmp_path, monkeypatch, "--all")
    assert rc == 0 and out["domains"] == "[]" and json.loads(out["skipped_disabled"]) == ["zeta"]
    assert "SKIP    zeta" in capsys.readouterr().out and "| zeta | skip:disabled |" in summary
    with pytest.raises(SystemExit) as e:  # a per-domain job could never run for it
        ad.main(["--repo-root", str(tmp_path), "--contract-path", "zeta"])
    assert "not processable (skip:disabled)" in str(e.value)
    onboard(tmp_path, "zeta", mcp_enabled=True)  # L: false -> true, nothing else
    rec = ad.classify(str(tmp_path), "zeta")
    assert rec["status"] == ad.READY and rec["contract"] == "domains/zeta/src/api/zeta/openapi.yaml"
    rc, out, _ = run_detect(tmp_path, tmp_path, monkeypatch, "--all")
    assert json.loads(out["domains"]) == ["zeta"]
    onboard(tmp_path, "zeta", mcp_enabled="yes")  # a non-boolean is a configuration error, never a guess
    assert ad.classify(str(tmp_path), "zeta")["status"] == ad.ERROR


def test_K_disabled_domain_is_never_published_by_the_local_orchestrator(tmp_path, monkeypatch, capsys):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("NETWORK")))
    root = tmp_path / "repo"
    spec, cfg = synthetic_domain(root / "domains" / "alpha", "alpha", "alphas", "https://a/")
    synthetic_domain(root / "domains" / "beta", "beta", "betas", "https://b/")
    doc = yaml.safe_load((root / "domains" / "beta" / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8"))
    doc["mcp_enabled"] = False
    (root / "domains" / "beta" / "src" / "mcp" / "bridge-config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    onboard(root, "gamma")  # onboarded, no contract
    assert rd.main(["--environment", "dev", "--domains", "all", "--repo-root", str(root), "--out", str(tmp_path / "out"),
                    "--platform-config", PLATFORM, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "SKIP beta" in out and "MCP disabled" in out and "SKIP gamma" in out and "contract not available" in out
    assert "order (serial, stop at the first failure): alpha" in out and "beta         SKIPPED" in out and "gamma        SKIPPED" in out
    assert (tmp_path / "out" / "alpha" / "release.json").exists() and not (tmp_path / "out" / "beta").exists() and not (tmp_path / "out" / "gamma").exists()


# ── M: the real repository ─────────────────────────────────────────────────────────

def test_M_every_current_domain_folder_carries_its_onboarding_file_and_reports_one_status():
    folders = ad.domain_folders(str(REPO))
    assert folders and ad.missing_onboarding(str(REPO)) == [] and ad.discover(str(REPO)) == folders
    rep = ad.report(str(REPO), folders)
    assert rep["errors"] == []
    assert sorted(rep["processed"] + rep["skipped_no_contract"] + rep["skipped_disabled"]) == folders
    for d in folders:
        cfg = yaml.safe_load((REPO / "domains" / d / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8"))
        assert cfg["domain"] == d and set(cfg) <= {"domain", "contract", "mcp_enabled", "source_asset_id", "route_label", "environments"}
    import re
    src = Path(ad.__file__).read_text(encoding="utf-8")
    code = chr(10).join(l for l in src.split('"""', 2)[-1].splitlines() if l.strip() and not l.strip().startswith('#'))  # code lines, not prose
    assert not any(re.search(r"%s" % re.escape(d), code) for d in folders)  # the discovery code names no domain


# ── O: explicit contract that does not exist -> FAIL, not SKIP ──────────────────────

def test_O_explicit_missing_contract_fails_closed(tmp_path, monkeypatch):
    onboard(tmp_path, "zeta", contract="src/api/zeta/openapi.yaml")
    rec = ad.classify(str(tmp_path), "zeta")
    assert rec["status"] == ad.ERROR and "does not exist" in rec["reason"] and "fail, not skip" in rec["reason"]
    with pytest.raises(SystemExit) as e:
        run_detect(tmp_path, tmp_path, monkeypatch, "--all")
    assert "REFUSED: domain configuration error" in str(e.value)
    onboard(tmp_path, "zeta", contract="../alpha/src/api/openapi.yaml")
    assert "inside the domain" in ad.classify(str(tmp_path), "zeta")["reason"]
