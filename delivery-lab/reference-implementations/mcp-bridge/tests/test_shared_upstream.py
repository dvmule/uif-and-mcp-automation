"""The COMMON UIF API upstream: configured ONCE per environment in anypoint-environments.yaml
(<env>.upstream_url) and injected into every domain's effective config by
shared_bridge.effective_config - no domain list, map or special case; a domain's own
environments.<env>.upstream_url is an intentional override only."""
import copy
from pathlib import Path

import yaml

import affected_domains as ad
import shared_bridge as sb
import platform_config as pc
from anypoint_fake import TEST_PLATFORM, TEST_PLATFORM_ENV

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
SCRIPTS = HERE.parent / "scripts"
COMMON = TEST_PLATFORM_ENV["UIF_UPSTREAM_ORIGIN"]  # the injected LOCAL TEST origin (the real one is a GitHub Environment variable)


def platform_with_common(url=COMMON):
    doc = copy.deepcopy(TEST_PLATFORM)
    doc["dev"]["upstream_url"] = url
    return doc


def test_repository_platform_config_declares_the_common_upstream_once_and_no_domain_repeats_it():
    repo = yaml.safe_load((SCRIPTS / "anypoint-environments.yaml").read_text(encoding="utf-8"))
    assert repo["dev"]["upstream_url"] == "${UIF_UPSTREAM_ORIGIN}"  # a placeholder in Git, the value in the GitHub Environment
    shared = sb.load_shared(pc.resolve_environment(repo, "dev", env=TEST_PLATFORM_ENV), "dev")
    assert shared["upstream_url"] == COMMON and shared["environment"] == "dev"
    for cfg_path in (REPO / "domains").glob("*/src/mcp/bridge-config.yaml"):
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert "environments" not in cfg, "%s repeats an upstream the shared configuration already provides" % cfg_path
        assert "api-gip-dev-01" not in cfg_path.read_text(encoding="utf-8")


def test_a_brand_new_domain_with_only_a_domain_key_resolves_the_common_upstream():
    shared = sb.load_shared(platform_with_common()["dev"], "dev")
    eff = sb.effective_config({"domain": "zeta"}, shared)
    assert eff["environments"]["dev"]["upstream_url"] == COMMON
    assert eff["environments"]["dev"]["upstream_source"].startswith("shared anypoint-environments.yaml dev")
    assert eff["source_asset_id"] == "uif-zeta-api"  # every other convention still derives from `domain`


def test_every_discovered_domain_resolves_the_same_upstream_without_any_domain_specific_code():
    shared = sb.load_shared(platform_with_common()["dev"], "dev")
    urls = {d: sb.effective_config({"domain": d}, shared)["environments"]["dev"]["upstream_url"] for d in ("alpha", "beta", "gamma", "omega")}
    assert set(urls.values()) == {COMMON}
    src = (SCRIPTS / "shared_bridge.py").read_text(encoding="utf-8")
    assert "product" not in src.split("def effective_config")[1].split("def write_effective_config")[0].lower()


def test_a_null_or_empty_domain_value_falls_back_to_the_common_upstream():
    shared = sb.load_shared(platform_with_common()["dev"], "dev")
    for cfg in ({"domain": "x", "environments": {"dev": {"upstream_url": None}}}, {"domain": "x", "environments": {"dev": {}}},
                {"domain": "x", "environments": {}}, {"domain": "x", "environments": None}):
        assert sb.effective_config(cfg, shared)["environments"]["dev"]["upstream_url"] == COMMON


def test_an_intentional_domain_override_is_kept_and_labelled():
    shared = sb.load_shared(platform_with_common()["dev"], "dev")
    eff = sb.effective_config({"domain": "x", "environments": {"dev": {"upstream_url": "https://other.example.test/"}}}, shared)
    assert eff["environments"]["dev"]["upstream_url"] == "https://other.example.test/"
    assert eff["environments"]["dev"]["upstream_source"] == "domain override (bridge-config.yaml)"


def test_other_environments_in_the_domain_config_are_left_alone():
    shared = sb.load_shared(platform_with_common()["dev"], "dev")
    eff = sb.effective_config({"domain": "x", "environments": {"uat": {"upstream_url": "https://uat.example.test/"}}}, shared)
    assert eff["environments"]["uat"] == {"upstream_url": "https://uat.example.test/"}
    assert eff["environments"]["dev"]["upstream_url"] == COMMON


def test_without_a_common_upstream_the_behaviour_is_unchanged():
    shared = sb.load_shared(TEST_PLATFORM["dev"], "dev")  # the test platform declares none
    assert shared["upstream_url"] is None
    eff = sb.effective_config({"domain": "x", "environments": {"dev": {"upstream_url": "https://own.example.test/"}}}, shared)
    assert eff["environments"]["dev"] == {"upstream_url": "https://own.example.test/"}
    assert "environments" not in sb.effective_config({"domain": "x"}, shared)


def test_workflow_reads_the_upstream_from_the_effective_config():
    wf = (REPO / ".github" / "workflows" / "mcp-deploy.yml").read_text(encoding="utf-8")
    assert 'upstream = ((eff.get("environments") or {}).get(env) or {}).get("upstream_url") or ""' in wf
    assert "has no environments.{env} block" not in wf
    assert "api-gip-dev-01" not in wf


def test_missing_configuration_message_points_at_the_shared_upstream(tmp_path):
    root = tmp_path
    (root / "domains" / "d" / "src" / "mcp").mkdir(parents=True)
    (root / "domains" / "d" / "src" / "mcp" / "bridge-config.yaml").write_text("domain: d\ncontract: src/api/d/openapi.yaml\n", encoding="utf-8")  # explicit contract that does not exist -> error
    import pytest
    with pytest.raises(SystemExit) as e:
        ad.main(["--repo-root", str(root), "--all"])
    assert "anypoint-environments.yaml <env>.upstream_url" in str(e.value) and "domain:" in str(e.value)


def test_the_common_upstream_is_an_origin_only_and_base_paths_stay_in_the_contracts():
    """Design rule: final backend URL = shared upstream ORIGIN + contract servers[0] path + operation path.
    The shared value must therefore carry no path of its own - the contract owns its base path."""
    from urllib.parse import urlsplit
    import generate_mcp_metadata as gm
    repo = yaml.safe_load((SCRIPTS / "anypoint-environments.yaml").read_text(encoding="utf-8"))
    parts = urlsplit(pc.resolve_environment(repo, "dev", env=TEST_PLATFORM_ENV)["upstream_url"])
    assert parts.scheme == "https" and parts.netloc and parts.path in ("", "/") and not parts.query and not parts.fragment
    for domain in ad.processable(str(REPO)):  # every ready contract carries its own base path in every mapping
        loader = gm.SpecLoader(str(REPO / ad.resolve_contract(str(REPO), domain)[0]))
        base = gm.server_base_path(loader.entry)
        assert base and all(m["path"].startswith(base + "/") for m in gm.build_mappings(loader, "x")["tools"])
    src = (SCRIPTS / "shared_bridge.py").read_text(encoding="utf-8") + (SCRIPTS / "generate_mcp_metadata.py").read_text(encoding="utf-8")
    assert "/uif/v1" not in src.replace("(e.g. /uif/v1", "")  # no hardcoded UIF base path in Python
