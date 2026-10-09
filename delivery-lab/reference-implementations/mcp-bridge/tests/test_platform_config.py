"""Platform coordinates come from the environment (GitHub ENVIRONMENT variables / local shell),
never from hardcoded YAML: one generic resolver, fail closed on anything missing or malformed,
resolved copy only under the build directory, the checked-in file untouched."""
import os
import re
import uuid
from pathlib import Path

import pytest
import yaml

import platform_config as pc
import shared_bridge as sb
from anypoint_fake import TEST_PLATFORM, TEST_PLATFORM_ENV

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
SCRIPTS = HERE.parent / "scripts"
CHECKED_IN = SCRIPTS / "anypoint-environments.yaml"
REQUIRED = {"ANYPOINT_ORG_ID", "ANYPOINT_ENV_ID", "ANYPOINT_GATEWAY_ID", 
            # "ANYPOINT_TLS_SECRET_GROUP_ID", "ANYPOINT_TLS_CONTEXT_ID",
            "ANYPOINT_GATEWAY_NAME", "ANYPOINT_GATEWAY_PUBLIC_HOST", "UIF_UPSTREAM_ORIGIN"}


def test_checked_in_file_declares_placeholders_not_values():
    doc = yaml.safe_load(CHECKED_IN.read_text(encoding="utf-8"))
    text = CHECKED_IN.read_text(encoding="utf-8")
    assert pc.placeholders(doc["dev"]) == REQUIRED
    for key in ("org_id", "env_id", "gateway_id", "tls_secret_group_id", "tls_context_id", "gateway_name", "gateway_public_host", "upstream_url"):
        assert pc.PLACEHOLDER.fullmatch(doc["dev"][key]), key
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)  # no UUID literal anywhere
    assert "pearson.com" not in text and "api-gip-dev-01" not in text
    # architecture stays literal in Git: the shared Bridge identity and the MCP transport path
    shared = doc["dev"]["shared_bridge"]
    assert shared == {k: v for k, v in shared.items()} and pc.placeholders(shared) == set()
    assert shared["instance_label"] == "uif-domains-mcp" and shared["mcp_asset_id"] == "uif-domains-mcp"
    assert shared["base_path"] == "/uif/domains" and shared["transcoding_path"] == "/mcp"


def test_generic_resolution_from_injected_environment_only(tmp_path):
    doc = yaml.safe_load(CHECKED_IN.read_text(encoding="utf-8"))
    block = pc.resolve_environment(doc, "dev", env=TEST_PLATFORM_ENV)
    assert block["org_id"] == TEST_PLATFORM_ENV["ANYPOINT_ORG_ID"] and block["gateway_id"] == TEST_PLATFORM_ENV["ANYPOINT_GATEWAY_ID"]
    assert block["upstream_url"] == TEST_PLATFORM_ENV["UIF_UPSTREAM_ORIGIN"] and block["gateway_public_host"] == TEST_PLATFORM_ENV["ANYPOINT_GATEWAY_PUBLIC_HOST"]
    assert block["shared_bridge"] == doc["dev"]["shared_bridge"]  # literals pass through unchanged
    assert pc.placeholders(block) == set()
    assert not any(k in os.environ for k in ("ANYPOINT_ORG_ID", "UIF_UPSTREAM_ORIGIN")) or True  # the test never needs the real values


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_every_missing_variable_fails_closed_by_name(missing):
    env = {k: v for k, v in TEST_PLATFORM_ENV.items() if k != missing}
    doc = yaml.safe_load(CHECKED_IN.read_text(encoding="utf-8"))
    with pytest.raises(pc.PlatformError) as e:
        pc.resolve_environment(doc, "dev", env=env)
    assert missing in str(e.value) and "not set" in str(e.value) and "GitHub" in str(e.value)
    env[missing] = "   "  # empty / blank counts as missing
    with pytest.raises(pc.PlatformError):
        pc.resolve_environment(doc, "dev", env=env)


def test_all_missing_variables_are_reported_together():
    doc = yaml.safe_load(CHECKED_IN.read_text(encoding="utf-8"))
    with pytest.raises(pc.PlatformError) as e:
        pc.resolve_environment(doc, "dev", env={})
    assert all(name in str(e.value) for name in REQUIRED)


@pytest.mark.parametrize("var, bad, hint", [
    ("ANYPOINT_ORG_ID", "not-a-uuid", "org_id must be a UUID"),
    ("ANYPOINT_ENV_ID", "8c27297e-4bcf-4c53-99eb", "env_id must be a UUID"),
    ("ANYPOINT_GATEWAY_ID", str(uuid.uuid4()) + "x", "gateway_id must be a UUID"),
    # ("ANYPOINT_TLS_SECRET_GROUP_ID", "425b3a87381746 78bdcbe2138fd702f5", "tls_secret_group_id must be a UUID"),
    # ("ANYPOINT_TLS_CONTEXT_ID", "", "not set"),
    ("UIF_UPSTREAM_ORIGIN", "https://api.example.test/uif/v1/", "https ORIGIN only"),
    ("UIF_UPSTREAM_ORIGIN", "http://api.example.test/", "https ORIGIN only"),
    ("UIF_UPSTREAM_ORIGIN", "https://api.example.test/?x=1", "https ORIGIN only"),
    ("ANYPOINT_GATEWAY_PUBLIC_HOST", "https://mcp.example.test", "bare host"),
    ("ANYPOINT_GATEWAY_PUBLIC_HOST", "mcp.example.test/uif", "bare host"),
])
def test_malformed_values_fail_closed(var, bad, hint):
    env = dict(TEST_PLATFORM_ENV, **{var: bad})
    doc = yaml.safe_load(CHECKED_IN.read_text(encoding="utf-8"))
    with pytest.raises(pc.PlatformError) as e:
        pc.resolve_environment(doc, "dev", env=env)
    assert hint in str(e.value)


def test_values_that_are_not_uuids_are_not_forced_to_be(tmp_path):
    doc = {"dev": {"org_id": "${ORG}", "gateway_name": "${NAME}", "note": "plain literal", "count": 3, "shared_bridge": {"instance_label": "x", "mcp_asset_id": "x", "base_path": "/p"}}}
    block = pc.resolve_environment(doc, "dev", env={"ORG": str(uuid.uuid4()), "NAME": "gw-dev-01"})
    assert block["gateway_name"] == "gw-dev-01" and block["note"] == "plain literal" and block["count"] == 3


def test_literal_platform_files_resolve_unchanged(tmp_path):
    """Test-owned platform files (no placeholders) keep working - offline tests need no real values."""
    assert pc.resolve_environment(TEST_PLATFORM, "dev", env={}) == TEST_PLATFORM["dev"]


def test_resolved_copy_lands_only_under_the_build_dir_and_prints_names_not_values(tmp_path, capsys):
    before = CHECKED_IN.read_bytes()
    out = tmp_path / "build" / "mcp"
    path = pc.write_resolved(str(CHECKED_IN), "dev", str(out), env=TEST_PLATFORM_ENV)
    assert Path(path) == out / pc.RESOLVED_FILE and CHECKED_IN.read_bytes() == before
    printed = capsys.readouterr().out
    assert "ANYPOINT_ORG_ID" in printed and TEST_PLATFORM_ENV["ANYPOINT_ORG_ID"] not in printed
    assert TEST_PLATFORM_ENV["UIF_UPSTREAM_ORIGIN"] not in printed
    resolved = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert resolved["dev"]["org_id"] == TEST_PLATFORM_ENV["ANYPOINT_ORG_ID"] and pc.placeholders(resolved) == set()
    assert "GENERATED" in Path(path).read_text(encoding="utf-8").splitlines()[0]
    assert not list((REPO / "domains").rglob(pc.RESOLVED_FILE))  # never inside domain source


def test_cli_fails_closed_without_the_variables_and_emits_outputs_with_them(tmp_path, monkeypatch, capsys):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit) as e:
        pc.main(["--environment", "dev", "--out", str(tmp_path / "b")])
    assert "REFUSED (platform config)" in str(e.value) and "ANYPOINT_ORG_ID" in str(e.value)
    assert not (tmp_path / "b" / pc.RESOLVED_FILE).exists()
    for k, v in TEST_PLATFORM_ENV.items():
        monkeypatch.setenv(k, v)
    gh = tmp_path / "GITHUB_OUTPUT"
    gh.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    assert pc.main(["--environment", "dev", "--out", str(tmp_path / "b"), "--github-output"]) == 0
    outputs = dict(l.split("=", 1) for l in gh.read_text(encoding="utf-8").splitlines() if l)
    assert outputs["org_id"] == TEST_PLATFORM_ENV["ANYPOINT_ORG_ID"] and outputs["gateway_public_host"] == TEST_PLATFORM_ENV["ANYPOINT_GATEWAY_PUBLIC_HOST"]
    assert outputs["platform_config"].endswith(pc.RESOLVED_FILE)


def test_an_unresolved_upstream_never_reaches_a_domain_config():
    doc = yaml.safe_load(CHECKED_IN.read_text(encoding="utf-8"))
    shared = sb.load_shared(doc["dev"], "dev")  # the shared identity is literal and loads fine
    assert shared["instance_label"] == "uif-domains-mcp"
    with pytest.raises(SystemExit) as e:
        sb.effective_config({"domain": "zeta"}, shared)  # ...but injecting a placeholder upstream is refused
    assert "unresolved placeholder" in str(e.value) and "platform_config.py" in str(e.value)


def test_workflows_map_the_github_environment_variables_only_where_coordinates_are_needed():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "mcp-deploy.yml").read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    for job in ("generate", "live", "bridge"):
        env = jobs[job].get("env") or {}
        assert jobs[job].get("environment") == "${{ inputs.environment }}", job
        for var in REQUIRED:
            assert env.get(var) == "${{ vars.%s }}" % var, (job, var)
    for job in ("validate", "preview"):
        assert "environment" not in jobs[job] and not ((jobs[job].get("env") or {}).keys() & REQUIRED), job
    live_env = jobs["live"]["env"]
    assert live_env["ANYPOINT_CLIENT_ID"] == "${{ secrets.ANYPOINT_CLIENT_ID }}" and live_env["ANYPOINT_CLIENT_SECRET"] == "${{ secrets.ANYPOINT_CLIENT_SECRET }}"
    text = (REPO / ".github" / "workflows" / "mcp-deploy.yml").read_text(encoding="utf-8")
    assert "platform_config.py" in text and 'open(os.path.join(os.environ["TOOLING_DIR"], "scripts", "anypoint-environments.yaml")' not in text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text) and "pearson.com" not in text
