"""affected_domains.py - convention-driven discovery and selection for the ONE generic
GitHub entry point (mcp.yml). No network; the repository tree and synthetic trees only.

Proves: Product and Price are discovered from the real repository; an arbitrary new
domain (Customer) is discovered from its contract alone, nothing hardcoded; a changed
domain is selected; several changed domains are selected together (and the workflow
processes them serially - test_mcp_workflow_contract.py); unrelated files select
nothing; a selected domain without its required configuration fails clearly; a
brand-new domain is selected by the very commit that adds it."""
import json
from pathlib import Path

import pytest
import yaml

import affected_domains as ad

REPO = Path(__file__).resolve().parents[4]


def _tree(root, domains, *, without_config=()):
    """domains/<d>/src/api/openapi.yaml (+ bridge-config.yaml unless excluded) under root."""
    for d in domains:
        (root / "domains" / d / "src" / "api").mkdir(parents=True, exist_ok=True)
        (root / "domains" / d / "src" / "api" / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
        if d not in without_config:
            (root / "domains" / d / "src" / "mcp").mkdir(parents=True, exist_ok=True)
            (root / "domains" / d / "src" / "mcp" / "bridge-config.yaml").write_text(
                yaml.safe_dump({"domain": d, "environments": {"dev": {"upstream_url": "https://%s/" % d}}}), encoding="utf-8")
    (root / "domains" / "stock" / "src" / "mcp").mkdir(parents=True, exist_ok=True)  # a domain WITHOUT a contract is not an MCP domain
    (root / "domains" / "stock" / "src" / "mcp" / "README.md").write_text("stub", encoding="utf-8")
    return root


def _run(tmp_path, monkeypatch, argv, root):
    out = tmp_path / "GITHUB_OUTPUT"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    rc = ad.main(["--repo-root", str(root)] + argv)
    return rc, dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if line)


# ── discovery ──────────────────────────────────────────────────────────────────

def test_onboarded_domains_are_discovered_from_the_real_repository():
    """Discovery = the onboarding file exists; every onboarded domain resolves exactly one contract."""
    discovered = ad.discover(str(REPO))
    assert discovered == sorted(discovered) and discovered
    assert discovered == sorted(d for d in discovered if (REPO / "domains" / d / "src" / "mcp" / "bridge-config.yaml").is_file())
    assert ad.missing_configuration(str(REPO), discovered) == []  # no error-state domain in the repository
    assert ad.missing_onboarding(str(REPO)) == []  # EVERY domain folder carries its onboarding file
    assert discovered == ad.domain_folders(str(REPO))


def test_an_arbitrary_new_domain_is_discovered_from_its_onboarding_file_alone(tmp_path):
    root = _tree(tmp_path, ["product", "price", "customer"])
    assert ad.discover(str(root)) == ["customer", "price", "product"]
    for d in ("customer", "price", "product"):
        assert ad.resolve_contract(str(root), d) == ("domains/%s/src/api/openapi.yaml" % d, None)  # the single candidate auto-resolves
    src = (Path(ad.__file__)).read_text(encoding="utf-8")
    assert "customer" not in src and "product" not in src.lower().replace("domain-owned", "")  # nothing hardcoded


# ── selection ──────────────────────────────────────────────────────────────────

def test_changed_domain_is_selected_and_others_are_not():
    disc = ["customer", "price", "product"]
    assert ad.affected(["domains/price/src/api/openapi.yaml"], disc) == ["price"]
    assert ad.affected(["domains/product/src/mcp/bridge-config.yaml"], disc) == ["product"]
    assert ad.affected(["domains/product/src/api/openapi.yaml", "domains/price/src/api/openapi.yaml", "domains/customer/src/mcp/bridge-config.yaml"], disc) \
        == ["customer", "price", "product"]  # several changed domains, one run


def test_unrelated_files_select_nothing():
    disc = ["price", "product"]
    for path in ("domains/product/src/apps/pipelines/base/product.yaml", "domains/product/README.md", "domains/stock/src/mcp/README.md",
                 "capabilities/mcp/mcp-bridge.md", "stacks/echo/src/x.py", ".github/workflows/deploy-product-dev.yml", "domains/price/qe/test_x.py"):
        assert ad.affected([path], disc) == [], path


def test_shared_tooling_or_pipeline_change_selects_every_discovered_domain():
    disc = ["customer", "price", "product"]
    for path in ("delivery-lab/reference-implementations/mcp-bridge/scripts/update_bridge.py",
                 "delivery-lab/reference-implementations/mcp-bridge/scripts/anypoint-environments.yaml",
                 ".github/workflows/mcp-deploy.yml", ".github/workflows/mcp.yml"):
        assert ad.affected([path], disc) == disc, path


def test_a_brand_new_domain_is_selected_by_the_commit_that_adds_it():
    # the contract appears for the first time: not in the previous discovery, still selected
    assert ad.affected(["domains/customer/src/api/openapi.yaml", "domains/customer/src/mcp/bridge-config.yaml"], ["price", "product"]) == ["customer"]
    # but a stray file under a non-MCP domain (no contract, no config) selects nothing
    assert ad.affected(["domains/stock/src/api/README.md"], ["price", "product"]) == []


# ── the CLI as the workflow runs it ────────────────────────────────────────────

def test_cli_changed_files_writes_the_matrix_outputs(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path / "repo", ["product", "price", "customer"])
    changed = tmp_path / "changed.txt"
    changed.write_text("domains/price/src/api/openapi.yaml\nREADME.md\ndomains/customer/src/api/openapi.yaml\n", encoding="utf-8")
    rc, out = _run(tmp_path, monkeypatch, ["--changed-files", str(changed)], root)
    assert rc == 0 and json.loads(out["domains"]) == ["customer", "price"] and out["count"] == "2"
    assert "onboarded domains" in capsys.readouterr().out


def test_cli_all_and_only(tmp_path, monkeypatch):
    root = _tree(tmp_path / "repo", ["product", "price", "customer"])
    rc, out = _run(tmp_path, monkeypatch, ["--all"], root)
    assert rc == 0 and json.loads(out["domains"]) == ["customer", "price", "product"]
    rc, out = _run(tmp_path, monkeypatch, ["--only", "price, product"], root)
    assert rc == 0 and json.loads(out["domains"]) == ["price", "product"]
    with pytest.raises(SystemExit) as e:
        _run(tmp_path, monkeypatch, ["--only", "orders"], root)
    assert "orders not onboarded" in str(e.value)


def test_cli_nothing_selected_ends_with_count_zero(tmp_path, monkeypatch):
    root = _tree(tmp_path / "repo", ["product", "price"])
    changed = tmp_path / "changed.txt"
    changed.write_text("domains/product/src/apps/x.py\n", encoding="utf-8")
    rc, out = _run(tmp_path, monkeypatch, ["--changed-files", str(changed)], root)
    assert rc == 0 and out["domains"] == "[]" and out["count"] == "0"


def test_missing_required_configuration_fails_clearly_and_is_never_skipped(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path / "repo", ["product", "customer"], without_config=("customer",))
    changed = tmp_path / "changed.txt"
    # a contract WITHOUT an onboarding file is not an MCP domain: nothing is selected, and the run says so
    changed.write_text("domains/customer/src/api/openapi.yaml\n", encoding="utf-8")
    rc, out = _run(tmp_path, monkeypatch, ["--changed-files", str(changed)], root)
    printed = capsys.readouterr().out
    assert rc == 0 and out["domains"] == "[]" and "MISSING ONBOARDING CONFIGURATION" in printed and "customer" in printed
    # an ONBOARDED domain whose contract cannot be resolved fails closed, clearly
    (root / "domains" / "customer" / "src" / "mcp").mkdir(parents=True, exist_ok=True)
    (root / "domains" / "customer" / "src" / "mcp" / "bridge-config.yaml").write_text("domain: customer\n", encoding="utf-8")
    (root / "domains" / "customer" / "src" / "api" / "v2").mkdir(parents=True)
    (root / "domains" / "customer" / "src" / "api" / "v2" / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")  # a SECOND candidate
    with pytest.raises(SystemExit) as e:
        _run(tmp_path, monkeypatch, ["--changed-files", str(changed)], root)
    msg = str(e.value)
    assert "REFUSED: domain configuration error" in msg
    assert "customer: domains/customer ships 2 OpenAPI contracts" in msg and "contract:" in msg and "never guesses" in msg
    # an unaffected, correctly configured Product run is not blocked by Customer's gap
    changed.write_text("domains/product/src/api/openapi.yaml\n", encoding="utf-8")
    rc, out = _run(tmp_path, monkeypatch, ["--changed-files", str(changed)], root)
    assert rc == 0 and json.loads(out["domains"]) == ["product"]


# ── contract selection (the onboarding file picks the contract) ─────────────────

def _domain(root, d, contracts, cfg):
    for rel in contracts:
        p = root / "domains" / d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("openapi: 3.1.0\n", encoding="utf-8")
    c = root / "domains" / d / "src" / "mcp" / "bridge-config.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_contract_selection_rules(tmp_path):
    root = tmp_path
    _domain(root, "one", ["src/api/one/openapi.yaml"], {"domain": "one"})
    assert ad.resolve_contract(str(root), "one") == ("domains/one/src/api/one/openapi.yaml", None)  # the single nested candidate
    _domain(root, "flat", ["src/api/openapi.yaml"], {"domain": "flat"})
    assert ad.resolve_contract(str(root), "flat") == ("domains/flat/src/api/openapi.yaml", None)  # the single flat candidate
    _domain(root, "two", ["src/api/two/openapi.yaml", "src/api/extra/openapi.yaml"], {"domain": "two"})
    rel, problem = ad.resolve_contract(str(root), "two")
    assert rel is None and "ships 2 OpenAPI contracts" in problem and "api/extra/openapi.yaml" in problem and "api/two/openapi.yaml" in problem
    _domain(root, "two", [], {"domain": "two", "contract": "src/api/two/openapi.yaml"})
    assert ad.resolve_contract(str(root), "two") == ("domains/two/src/api/two/openapi.yaml", None)  # the domain chose
    _domain(root, "mixed", ["src/api/openapi.yaml", "src/api/mixed/openapi.yaml"], {"domain": "mixed"})
    assert "ships 2 OpenAPI contracts" in ad.resolve_contract(str(root), "mixed")[1]
    _domain(root, "none", [], {"domain": "none"})
    assert "contract not available" in ad.resolve_contract(str(root), "none")[1]  # a clean SKIP, not an error
    assert ad.classify(str(root), "none")["status"] == ad.SKIP_NO_CONTRACT
    _domain(root, "gone", [], {"domain": "gone", "contract": "src/api/gone/openapi.yaml"})
    assert "does not exist" in ad.resolve_contract(str(root), "gone")[1]
    _domain(root, "esc", ["src/api/esc/openapi.yaml"], {"domain": "esc", "contract": "../one/src/api/one/openapi.yaml"})
    assert "inside the domain" in ad.resolve_contract(str(root), "esc")[1]
    assert ad.classify(str(root), "ghost")["status"] == ad.MISSING_CONFIG


def test_contract_path_cli_prints_the_resolved_path_or_refuses(tmp_path, capsys):
    _domain(tmp_path, "one", ["src/api/one/openapi.yaml"], {"domain": "one"})
    assert ad.main(["--repo-root", str(tmp_path), "--contract-path", "one"]) == 0
    assert capsys.readouterr().out.strip() == "domains/one/src/api/one/openapi.yaml"
    _domain(tmp_path, "two", ["src/api/a/openapi.yaml", "src/api/b/openapi.yaml"], {"domain": "two"})
    with pytest.raises(SystemExit) as e:
        ad.main(["--repo-root", str(tmp_path), "--contract-path", "two"])
    assert "REFUSED" in str(e.value) and "contract:" in str(e.value)


def test_a_schema_dependency_change_selects_the_domain_and_the_source_is_never_hardcoded():
    assert ad.affected(["domains/zeta/src/schemas/channels/zeta/zeta-data-api-created.json"], ["zeta"]) == ["zeta"]
    assert ad.affected(["domains/zeta/src/api/zeta/openapi.yaml"], ["zeta"]) == ["zeta"]
    assert ad.affected(["domains/zeta/src/mcp/bridge-config.yaml"], []) == ["zeta"]  # the onboarding commit itself
    assert ad.affected(["domains/zeta/src/apps/main.py", "domains/zeta/README.md"], ["zeta"]) == []
    src = Path(ad.__file__).read_text(encoding="utf-8")
    assert "stock" not in src.lower() and "price" not in src.lower() and "/uif/v1" not in src
