"""Workflow + documentation contract for the SHARED MCP Bridge lifecycle.

The reusable workflow must run the ONE orchestrated deploy step (deploy_bridge) in a
gated, per-environment SERIALIZED bridge job, keep every safety gate, never call a
delete, keep domain-scoped work (validation, generation, REST publish) out of the
serialized section, and the standard/README must describe one shared Bridge with
in-place per-domain reconciliation - not delete/recreate, not one Bridge per domain.
Pure file assertions; no network.
"""
import affected_domains as ad
import platform_config as pc
from anypoint_fake import TEST_PLATFORM_ENV
import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
TOOLING = Path(__file__).resolve().parents[1]          # delivery-lab/reference-implementations/mcp-bridge
TOOLING_REL = TOOLING.relative_to(REPO).as_posix()
WF = REPO / ".github" / "workflows" / "mcp-deploy.yml"
ENTRY = REPO / ".github" / "workflows" / "mcp.yml"  # the ONE generic entry point (discovery + serial matrix)
STANDARD = REPO / "capabilities" / "mcp" / "mcp-bridge.md"
README = REPO / "domains" / "product" / "src" / "mcp" / "README.md"
SRC = TOOLING / "scripts"
DEPLOY_STEP = "Deploy Bridge (ensure upstream -> shared asset -> CREATE | ADD | UPDATE | NO-OP)"
CONFORMANCE_STEP = "Conformance (initialize + tools/list)"
CFG_STEP = "Read bridge-config (the domain MCP input contract) + shared platform coordinates"
LIVE_GATE = "${{ !inputs.dry_run && vars.MCP_DEPLOY_ENABLED == 'true' }}"  # job-level: explicit live flag AND the shared latch


def _wf():
    text = WF.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def _steps(job):
    return {s.get("name", ""): s for s in job["steps"]}


def _runs(job):
    return "\n".join(s.get("run", "") for s in job["steps"])


def _shared():
    reg = yaml.safe_load((SRC / "anypoint-environments.yaml").read_text(encoding="utf-8"))
    return reg["dev"]["shared_bridge"]


# ── the bridge job: ONE orchestrated deploy step, serialized per environment ─────

def test_bridge_job_runs_the_orchestrated_deploy_step_only_once():
    text, wf = _wf()
    bridge = wf["jobs"]["bridge"]
    runs = _runs(bridge)
    assert "deploy_bridge.py" in runs and "--execute" in runs
    # the separate create-only stage, the standalone upstream stage and the per-domain MCP publish are gone
    for job in wf["jobs"].values():
        r = _runs(job)
        assert "provision_bridge.py" not in r and "ensure_source_instance.py" not in r and "exchange_publish.py mcp" not in r
    names = [s.get("name", "") for s in bridge["steps"]]
    assert names.index(DEPLOY_STEP) < names.index(CONFORMANCE_STEP)
    deploy = _steps(bridge)[DEPLOY_STEP]["run"]
    assert "--out build/mcp" in deploy and "--artifacts build/mcp" in deploy
    assert "--release-file build/mcp/release.json" in deploy and "--mcp-asset-version" not in deploy and "--rest-asset-version" not in deploy
    # the live job does the domain-scoped release only: REST publish -> validate -> hand over
    live_names = [s.get("name", "") for s in wf["jobs"]["live"]["steps"]]
    order = [live_names.index(n) for n in ("Publish REST asset", "Validate artifacts", "Hand the release artifacts to the bridge job")]
    assert order == sorted(order)
    assert "deploy_bridge.py" not in _runs(wf["jobs"]["live"]) and "Publish MCP asset" not in live_names


def test_shared_bridge_mutation_is_serialized_per_environment_and_nothing_else_is():
    """Concurrency protection for the shared Bridge (acceptance scenario 8): the bridge job
    - and ONLY the bridge job - runs in a per-environment concurrency group with
    cancel-in-progress off (a second domain waits, is never cancelled mid-write)."""
    text, wf = _wf()
    bridge = wf["jobs"]["bridge"]
    assert bridge["concurrency"] == {"group": "mcp-shared-bridge-${{ inputs.environment }}", "cancel-in-progress": False}
    assert "${{ inputs.domain }}" not in bridge["concurrency"]["group"]  # one group for ALL domains of the environment
    for name, job in wf["jobs"].items():
        if name != "bridge":
            assert "concurrency" not in job, name  # validation / generation / REST publish stay parallel across domains
    assert "concurrency" not in wf  # never a workflow-level group (that would serialize unrelated work)
    # the serialized section holds exactly the shared-Bridge mutation + its verification
    names = [s.get("name", "") for s in bridge["steps"]]
    assert DEPLOY_STEP in names and CONFORMANCE_STEP in names
    assert not any(n in names for n in ("Publish REST asset", "Validate artifacts", "Resolve release version (Exchange reads only)"))
    assert bridge["needs"] == ["generate", "live"] and bridge["if"].strip() == LIVE_GATE
    # second line of defence lives in the reconciliation code
    src = (SRC / "update_bridge.py").read_text(encoding="utf-8")
    assert "REFUSED (concurrent modification)" in src and "def fingerprint(" in src


def test_bridge_job_consumes_exactly_what_this_run_derived():
    text, wf = _wf()
    live, bridge = wf["jobs"]["live"], wf["jobs"]["bridge"]
    up = next(s for s in live["steps"] if s.get("uses", "").startswith("actions/upload-artifact"))
    down = next(s for s in bridge["steps"] if s.get("uses", "").startswith("actions/download-artifact"))
    assert up["with"]["name"] == down["with"]["name"] == "mcp-release-${{ inputs.domain }}-${{ inputs.environment }}"
    assert up["with"]["path"].rstrip("/") == down["with"]["path"].rstrip("/") == "build/mcp"
    assert "download-artifact" not in yaml.safe_dump(live)  # the live job publishes only what it derived itself
    conf = _steps(bridge)[CONFORMANCE_STEP]["run"]
    assert "--expected-metadata build/mcp/shared-mcp-metadata.json" in conf  # the MERGED tool set, not this domain's alone
    assert "/${{ needs.generate.outputs.base_path }}/mcp" in conf and "needs.live.outputs.gateway_public_host" in conf
    assert "--retries 6 --retry-delay 10" in conf  # bounded propagation retry, never unbounded


# ── ONE REST release version, resolved from Exchange, drives the domain-scoped stages ──

def test_live_job_resolves_the_release_version_first_and_everything_consumes_it():
    text, wf = _wf()
    live = wf["jobs"]["live"]
    names = [s.get("name", "") for s in live["steps"]]
    resolve = names.index("Resolve release version (Exchange reads only)")
    assert resolve < names.index("Generate release artifacts") < names.index("Publish REST asset")
    steps = _steps(live)
    assert steps["Resolve release version (Exchange reads only)"]["id"] == "release"
    resolve_run = steps["Resolve release version (Exchange reads only)"]["run"]
    assert "release_version.py" in resolve_run and "--resolve" in resolve_run and "--execute" not in resolve_run
    assert '--spec "${{ steps.contract.outputs.path }}"' in resolve_run  # the contract is RESOLVED (bridge-config `contract:`), never a literal; only READ
    assert "--mcp-asset-id" not in text  # the shared MCP asset version is not paired with any domain's REST release
    live_runs = _runs(live)
    for flag in ("--asset-version", "--source-version"):
        vals = re.findall(r'%s\s+("[^"]*"|\S+)' % flag, live_runs)
        assert vals and set(vals) == {'"${{ steps.release.outputs.version }}"'}, (flag, vals)
    assert "needs.generate.outputs.source_version" not in text and "preview_release_version" not in live_runs
    # publishers project/publish the release-stamped BUILD COPY, never the git file
    assert "--spec build/mcp/openapi-release.yaml" in steps["Publish REST asset"]["run"]
    assert "--spec build/mcp/openapi-release.yaml" in steps["Generate release artifacts"]["run"]


def test_generate_job_previews_the_release_and_stamps_from_the_same_owner():
    text, wf = _wf()
    gen = _steps(wf["jobs"]["generate"])
    preview = gen["Resolve release version (preview — Exchange not consulted)"]
    assert preview["id"] == "release" and "--dry-run" in preview["run"] and "--resolve" not in preview["run"]
    gen_runs = _runs(wf["jobs"]["generate"])
    for flag in ("--asset-version", "--source-version"):
        vals = re.findall(r'%s\s+("[^"]*"|\S+)' % flag, gen_runs)
        assert vals and set(vals) == {'"${{ steps.release.outputs.version }}"'}, (flag, vals)
    assert "--spec build/mcp/openapi-release.yaml" in gen["Generate artifacts from the release-stamped contract"]["run"]
    assert "--release-file build/mcp/release.json" in gen["Deploy preview (deploy_bridge lifecycle, dry-run)"]["run"]
    # the ONLY version derivation from info.version left in the workflow is the display-only contract_version
    assert "spec['info']['version']" in gen[CFG_STEP]["run"] and "contract_version=" in gen[CFG_STEP]["run"]
    assert text.count("['info']['version']") == 1


def test_documentation_states_the_exchange_release_version_model():
    std = STANDARD.read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", std)
    assert "release_version.py" in std and "1.0.8 → 1.0.9" in std
    assert "the authoritative OpenAPI is never modified" in flat
    assert "a CI rerun never creates a version" in flat
    assert "Exchange state" in std and "never bumps `info.version`" in flat
    readme = README.read_text(encoding="utf-8")
    assert "release_version.py" in readme and "product-api@<release>" in readme
    assert "uif-product-mcp@" not in readme and "uif-product-mcp@" not in std  # no per-domain MCP asset any more


def test_no_delete_or_recreate_anywhere_in_the_workflows():
    for path in (WF, ENTRY):
        text = path.read_text(encoding="utf-8")
        assert "--delete-instance" not in text and "--delete-external-instance" not in text
        assert "--allow-connectionless-ui-gap" not in text
        assert "--instance-label" not in text  # standard identity only, no run-scoped label overrides in CI
        # the listener path is the SHARED Bridge's, read from anypoint-environments.yaml - never typed into a workflow
        assert "--base-path" not in text


def test_safety_gates_are_intact():
    text, wf = _wf()
    live, bridge = wf["jobs"]["live"], wf["jobs"]["bridge"]
    for job in (live, bridge):
        assert job["if"].strip() == LIVE_GATE                                   # gates 1 + 2: explicit live flag AND the latch
        assert job["environment"] == "${{ inputs.environment }}"                 # gate 4: GitHub Environment secrets
        assert job["env"]["MCP_LIVE_ENABLED"] == "true"                          # gate 3: script latch only in the live jobs
    for job_name, job in wf["jobs"].items():
        if job_name not in ("live", "bridge"):
            assert "MCP_LIVE_ENABLED" not in yaml.safe_dump(job)
    names = [s.get("name", "") for s in live["steps"]]
    assert names[:3] == ["Latch check (fail closed)", "Credentials present (fail closed)",
                         "Upstream present (fail closed — MCP automation never creates a URL)"]  # gates 2, 4, 5 first
    latch = _steps(live)["Latch check (fail closed)"]
    assert latch["env"] == {"MCP_DEPLOY_ENABLED": "${{ vars.MCP_DEPLOY_ENABLED }}"}  # ONE shared latch ...
    assert "_MCP_DEPLOY_ENABLED" not in latch["run"].replace("MCP_DEPLOY_ENABLED", "")   # ... never a per-domain <DOMAIN>_ one
    assert "inputs.domain" not in latch["run"] and "toJson(vars)" not in yaml.safe_dump(latch)
    assert "live" in bridge["needs"]  # the bridge job cannot run unless every gate of the live job held
    assert "actions/setup-node" not in text                                      # API Catalog CLI stays retired


def test_require_connection_is_enforced_by_the_orchestrator_create_path():
    src = (SRC / "deploy_bridge.py").read_text(encoding="utf-8")
    assert '"--require-connection", "--execute"' in src
    assert "ub.main(" in src and "pb.main(" in src                              # reuses both proven implementations
    assert "sb.merge_metadata(" in src and "sb.resolve_shared_version(" in src  # shared asset: merge + Exchange-resolved version
    # the orchestrator can never delete: no argv literal for the guarded delete modes (docstring may mention them)
    assert '"--delete-instance"' not in src and '"--delete-external-instance"' not in src
    assert 'method="DELETE"' not in src and "urlopen(" not in src


def test_generate_job_previews_the_lifecycle_dry_run():
    text, wf = _wf()
    runs = _runs(wf["jobs"]["generate"])
    assert "deploy_bridge.py" in runs and "--dry-run" in runs
    assert "--execute" not in runs


# ── Exchange coordinates: the REAL platform org id everywhere, no placeholder ──────

def _platform_org(env="dev"):
    reg = yaml.safe_load((SRC / "anypoint-environments.yaml").read_text(encoding="utf-8"))
    return pc.resolve_environment(reg, env, env=TEST_PLATFORM_ENV)["org_id"]  # injected LOCAL TEST values, never the real ones


def test_generation_uses_the_real_platform_org_id_and_ci_preview_is_gone():
    text, wf = _wf()
    assert "ci-preview" not in text and "ci-preview" not in ENTRY.read_text(encoding="utf-8")
    gen = _steps(wf["jobs"]["generate"])
    cfg_run = gen[CFG_STEP]["run"]
    assert 'os.environ["PLATFORM"]' in cfg_run  # the RESOLVED platform file, produced by platform_config.py from the Environment's variables
    assert "org_id={platform['org_id']}" in cfg_run
    assert wf["jobs"]["generate"]["outputs"]["org_id"] == "${{ steps.cfg.outputs.org_id }}"
    # every --org-id in the generate job is the cfg output; every --org-id in the live job is the platform step
    org_re = re.compile(r'--org-id\s+("[^"]*"|\S+)')
    gen_hits = [h for s in wf["jobs"]["generate"]["steps"] for h in org_re.findall(s.get("run", ""))]
    live_hits = [h for s in wf["jobs"]["live"]["steps"] for h in org_re.findall(s.get("run", ""))]
    # generate: release preview, generator, REST preview; live: resolve, generator, REST publish
    assert len(gen_hits) == 3 and set(gen_hits) == {'"${{ steps.cfg.outputs.org_id }}"'}, gen_hits
    assert len(live_hits) == 3 and set(live_hits) == {'"${{ steps.platform.outputs.org_id }}"'}, live_hits
    assert re.search(r'--org-id\s+"\$\{\{ steps\.cfg\.outputs\.org_id \}\}"', gen["Generate artifacts from the release-stamped contract"]["run"])
    assert re.search(r'--org-id\s+"\$\{\{ steps\.cfg\.outputs\.org_id \}\}"', gen["REST publish preview (OAS 3.0.3 projection, dry-run)"]["run"])


def _run_cfg_step(tmp_path, monkeypatch, domain="product", env="dev"):
    """Execute the cfg step's Python exactly as the runner would (inputs substituted,
    GITHUB_OUTPUT + TOOLING_DIR set, cwd = repo root) and return its outputs."""
    text, wf = _wf()
    code = _steps(wf["jobs"]["generate"])[CFG_STEP]["run"]
    code = code.replace("${{ inputs.domain }}", domain).replace("${{ inputs.environment }}", env)
    assert "${{" not in code
    gh_out = tmp_path / "GITHUB_OUTPUT"
    gh_out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    monkeypatch.setenv("TOOLING_DIR", TOOLING_REL)
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("CONTRACT", ad.resolve_contract(str(REPO), domain)[0] or "")  # what the Resolve step outputs
    monkeypatch.setenv("PLATFORM", pc.write_resolved(str(SRC / "anypoint-environments.yaml"), env, str(tmp_path / "build"), env=TEST_PLATFORM_ENV))
    exec(compile(code, "<cfg step>", "exec"), {"__name__": "cfg_step"})
    return dict(line.split("=", 1) for line in gh_out.read_text(encoding="utf-8").splitlines() if line)


def test_cfg_step_emits_real_org_id_and_the_shared_bridge_identity_for_product(tmp_path, monkeypatch):
    out = _run_cfg_step(tmp_path, monkeypatch)
    shared = _shared()
    assert out["org_id"] == _platform_org() and len(out["org_id"]) == 36
    assert "ci-preview" not in out.values()
    product_cfg = yaml.safe_load((REPO / "domains" / "product" / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8"))
    assert out["base_path"] == shared["base_path"].strip("/") and out["bridge_label"] == shared["instance_label"]
    assert out["mcp_asset_id"] == shared["mcp_asset_id"] and out["source_asset_id"] == product_cfg["source_asset_id"]
    assert out["route_label"] == product_cfg.get("route_label", product_cfg["source_asset_id"])
    assert out["transcoding_path"] == shared.get("transcoding_path", "/mcp")
    spec = yaml.safe_load((REPO / ad.resolve_contract(str(REPO), "product")[0]).read_text(encoding="utf-8-sig"))
    assert out["contract_version"] == str(spec["info"]["version"])
    assert "source_version" not in out  # the contract's info.version is display-only; releases are resolved from Exchange


def test_generated_artifact_carries_the_same_exchange_coordinates_as_the_live_publish(tmp_path, monkeypatch):
    """What the generate job writes (and the bridge job merges into the shared asset) must
    reference the real org as the connection groupId - the live-proven content."""
    import generate_mcp_metadata as gen
    out = _run_cfg_step(tmp_path, monkeypatch)
    version = out["contract_version"]  # what the dry-run preview stamps; the live job stamps the Exchange-resolved release
    gen.main(["--spec", ad.resolve_contract(str(REPO), "product")[0], "--source-asset-id", out["source_asset_id"],
              "--source-version", version, "--asset-id", out["mcp_asset_id"], "--asset-version", version,
              "--org-id", out["org_id"], "--route-label", out["route_label"], "--transcoding-path", out["transcoding_path"],
              "--out", str(tmp_path / "build")])
    meta = json.loads((tmp_path / "build" / "mcp-metadata.json").read_text(encoding="utf-8"))
    assert meta["connections"][0]["ref"]["groupId"] == _platform_org()
    assert "ci-preview" not in (tmp_path / "build" / "mcp-metadata.json").read_text(encoding="utf-8")
    # the live job resolves org_id from the same file -> identical coordinates
    live = _steps(_wf()[1]["jobs"]["live"])["Resolve shared platform coordinates"]["run"]
    assert "platform_config.py" in live and "--github-output" in live  # the same resolver, the same Environment variables


# ── the shared Bridge identity: ONE definition (anypoint-environments.yaml), consumed everywhere ──

def test_workflow_consumes_the_shared_bridge_identity_from_the_platform_config():
    text, wf = _wf()
    gen = _steps(wf["jobs"]["generate"])
    assert "from shared_bridge import effective_config, load_shared" in gen[CFG_STEP]["run"]
    assert "base_path={shared['base_path']}" in gen[CFG_STEP]["run"] and "mcp_asset_id={shared['mcp_asset_id']}" in gen[CFG_STEP]["run"]
    assert "resolve_base_path" not in text and "cfg['mcp_asset_id']" not in text  # no per-domain derivation left
    for literal in ("productTest", "uif-product-mcp", "domains/product"):
        assert literal not in text  # generic: never a literal domain identity or path


def _fake_repo(tmp_path, domain, cfg_mutator=None):
    fake_repo = tmp_path / "repo"
    for src in (REPO / "domains" / "product" / "src" / "mcp" / "bridge-config.yaml", REPO / ad.resolve_contract(str(REPO), "product")[0]):
        rel = src.relative_to(REPO / "domains" / "product").as_posix()
        dst = fake_repo / "domains" / domain / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        content = src.read_text(encoding="utf-8-sig")
        if rel.endswith("bridge-config.yaml"):
            content = content.replace("domain: product", "domain: %s" % domain)
            if cfg_mutator:
                content = cfg_mutator(content)
        dst.write_text(content, encoding="utf-8")
    tooling_dst = fake_repo / TOOLING_REL / "scripts"
    tooling_dst.mkdir(parents=True)
    for f in SRC.glob("*.py"):
        (tooling_dst / f.name).write_bytes(f.read_bytes())
    (tooling_dst / "anypoint-environments.yaml").write_bytes((SRC / "anypoint-environments.yaml").read_bytes())
    return fake_repo


@pytest.mark.parametrize("domain", ["product", "price", "stock"])
def test_cfg_step_yields_the_same_shared_bridge_for_any_domain(tmp_path, monkeypatch, domain):
    """price/stock have no MCP realization yet: stamp a minimal domain tree from the
    Product fixture and prove the same workflow step yields the SHARED Bridge identity."""
    if domain != "product":
        monkeypatch.setitem(globals(), "REPO", _fake_repo(tmp_path, domain))
    out = _run_cfg_step(tmp_path, monkeypatch, domain=domain)
    shared = _shared()
    product_cfg = yaml.safe_load((Path(__file__).resolve().parents[4] / "domains" / "product" / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8"))
    assert out["base_path"] == shared["base_path"].strip("/") and out["mcp_asset_id"] == shared["mcp_asset_id"]
    assert out["bridge_label"] == shared["instance_label"] and out["route_label"] == product_cfg.get("route_label", product_cfg["source_asset_id"])
    assert out["org_id"] == _platform_org()


def test_per_domain_base_path_in_bridge_config_is_refused_by_the_workflow_step(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "REPO", _fake_repo(tmp_path, "product", lambda c: c + "\nbase_path: intentional-override\n"))
    with pytest.raises(SystemExit) as e:
        _run_cfg_step(tmp_path, monkeypatch)
    assert "a domain cannot choose one" in str(e.value)


def test_documentation_states_the_shared_bridge_model():
    std = STANDARD.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    shared = _shared()
    for doc in (std, readme):
        assert shared["instance_label"] in doc and "shared_bridge" in doc
        assert "shared_bridge.base_path" in doc  # the listener path is documented as the shared configuration value, never a literal
    assert "one Bridge per domain" in re.sub(r"\s+", " ", std).lower() or "ONE shared MCP Bridge" in std
    assert "uif-<domain>-api" in std and "uif-<domain>-<env>" in std  # the naming convention is documented generically
    assert "https://<gateway host>/productTest/mcp" not in readme and "test-1" not in std and "test-1" not in readme
    cfg_text = (REPO / "domains" / "product" / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8")
    assert "connection-less Bridges are not an accepted deployment pattern" in cfg_text
    assert "mcp_asset_id:" not in cfg_text and "base_path:" not in cfg_text and "shared_bridge" in cfg_text
    assert "instances are immutable" not in cfg_text and "created connection-less" not in cfg_text


def test_documentation_states_in_place_update_not_delete_recreate():
    std = STANDARD.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for doc in (std, readme):
        flat = re.sub(r"\s+", " ", doc.replace("**", ""))  # markdown emphasis + line wraps must not hide the statement
        assert "UPDATE IN PLACE" in flat and "NO-OP" in flat and "REFUSE" in flat and "ADD" in flat
        assert "never deletes or recreates a Bridge" in flat or "No delete/recreate for version changes" in flat
        assert "preserved" in flat  # every other domain's route
    assert "delete-then-recreate window" not in std
    assert "Instances are immutable" not in std
    assert "## Bridge lifecycle (live-proven 2026-09-11)" in std
    assert re.search(r"deploy-bridge\s+ONE step \(deploy_bridge\.py\)", std)
    assert "concurrency" in std.lower() and "mcp-shared-bridge-" in std  # the serialization mechanism is documented


# ── domain parameterisation vs. the shared tooling location ──────────────────────

DOMAIN_OWNED = ("src/api/", "src/mcp/bridge-config.yaml", "qe/")


def test_reusable_workflow_never_hardcodes_a_domain_for_domain_owned_paths():
    """Every domains/<x>/... reference must be domains/${{ inputs.domain }}/...;
    generic tooling is reached only through TOOLING_DIR (a non-domain path)."""
    text, wf = _wf()
    fixed = [m.group(0) for m in re.finditer(r"domains/(?!\$\{\{ inputs\.domain \}\}/)[A-Za-z0-9_-]+/[^\s\"'\)]*", text)]
    assert fixed == [], fixed  # NO fixed-domain path anywhere - tooling is not domain-hosted any more
    assert wf["env"]["TOOLING_DIR"] == TOOLING_REL == "delivery-lab/reference-implementations/mcp-bridge"
    for suffix in DOMAIN_OWNED:
        assert "domains/${{ inputs.domain }}/" + suffix in text, suffix
    # no step may reach into any domain for tooling - only through $TOOLING_DIR
    steps_text = yaml.safe_dump(wf["jobs"])
    assert "domains/product" not in steps_text and "reference-implementations" not in steps_text
    assert steps_text.count("$TOOLING_DIR") + steps_text.count('os.environ["TOOLING_DIR"]') >= 10


def test_every_generic_tool_invocation_goes_through_tooling_dir():
    text, wf = _wf()
    runs = "\n".join(s.get("run", "") for job in wf["jobs"].values() for s in job["steps"])
    for tool in ("release_version.py", "generate_mcp_metadata.py", "validate_mcp_metadata.py", "contract_gate.py",
                 "exchange_publish.py", "deploy_bridge.py", "mcp_conformance.py"):
        assert re.search(r'python "\$TOOLING_DIR"/scripts/%s' % re.escape(tool), runs), tool
        assert "domains/product/src/mcp/%s" % tool not in runs
        assert (SRC / tool).exists(), tool  # the invoked script really lives in the shared tooling
    assert re.search(r'python "\$TOOLING_DIR"/scripts/platform_config\.py', runs)  # the platform file is read by the shared resolver only
    assert (SRC / "anypoint-environments.yaml").exists() and (SRC / "shared_bridge.py").exists()
    # the shared tooling's own tests run in the validate job, before the domain gate
    validate_runs = [s.get("run", "") for s in wf["jobs"]["validate"]["steps"]]
    assert any('pytest "$TOOLING_DIR"/tests' in r for r in validate_runs)
    assert any("domains/${{ inputs.domain }}/qe/test_mcp_generation.py" in r for r in validate_runs)


def test_domain_input_drives_every_domain_owned_reference(tmp_path):
    """Substituting inputs.domain must yield price/stock trees with no product residue."""
    text, _ = _wf()
    for domain in ("product", "price", "stock"):
        rendered = text.replace("${{ inputs.domain }}", domain)
        for suffix in DOMAIN_OWNED:
            assert "domains/%s/%s" % (domain, suffix) in rendered
        residue = [m.group(0) for m in re.finditer(r"domains/product/[^\s\"'\)]*", rendered)]
        assert residue == [] or domain == "product", (domain, residue)
        assert "%s_MCP_DEPLOY_ENABLED" % domain.upper() not in rendered  # no per-domain latch for any domain


# ── the ONE generic entry point: discovery + SERIAL reconciliation ──────────────────

def _entry():
    text = ENTRY.read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    return text, wf, (wf[True] if True in wf else wf["on"])


def test_there_is_exactly_one_entry_workflow_and_no_per_domain_caller():
    workflows = sorted(p.name for p in (REPO / ".github" / "workflows").glob("*.yml"))
    assert "mcp.yml" in workflows and "mcp-deploy.yml" in workflows
    assert not [w for w in workflows if w.endswith("-mcp.yml")]  # product-mcp.yml / price-mcp.yml are gone
    others = [w for w in workflows if w not in ("mcp.yml", "mcp-deploy.yml")]
    for w in others:
        assert "mcp-deploy.yml" not in (REPO / ".github" / "workflows" / w).read_text(encoding="utf-8"), w  # nobody else calls the pipeline


def test_entry_discovers_domains_by_convention_with_no_hardcoded_domain():
    text, wf, on = _entry()
    for domain in ("product", "price", "stock", "customer"):
        assert domain not in text.lower().replace("product, price and any future domain", "").replace("e.g. product,price", ""), domain
    detect = wf["jobs"]["detect"]
    run = detect["steps"][-1]["run"]
    assert 'python "$TOOLING_DIR"/scripts/affected_domains.py' in run
    assert "--changed-files changed.txt" in run and "--all" in run and '--only "$ONLY"' in run
    assert "git diff --name-only" in run and "fetch-depth: 0" in text
    assert detect["outputs"]["domains"] == "${{ steps.affected.outputs.domains }}" and detect["outputs"]["count"] == "${{ steps.affected.outputs.count }}"
    assert set(detect["outputs"]) >= {"skipped_no_contract", "skipped_disabled", "missing_config"}  # skips are visible, never silent
    # the trigger paths are the convention paths - any domain's contract (root OR a $ref-erenced dependency under src/api/**)
    # or MCP inputs, the shared tooling, the two workflows
    expected = {"domains/*/src/api/**", "domains/*/src/schemas/**", "domains/*/src/mcp/**", "delivery-lab/reference-implementations/mcp-bridge/**",
                ".github/workflows/mcp-deploy.yml", ".github/workflows/mcp.yml"}
    assert set(on["push"]["paths"]) == expected
    assert set(on["workflow_dispatch"]["inputs"]) == {"domains", "dry_run"} and on["workflow_dispatch"]["inputs"]["domains"]["default"] == "all"


def test_entry_runs_only_on_develop_pushes_or_manual_dispatch_never_on_feature_branches_or_prs():
    """Lifecycle: work on any branch -> PR -> merge into develop -> the merge push runs the
    workflow once. Feature/bugfix/hotfix pushes and the PR itself never trigger it."""
    text, wf, on = _entry()
    assert set(on) == {"push", "workflow_dispatch"}          # no pull_request trigger by design
    assert on["push"]["branches"] == ["develop"]              # ... and no feature/* branch
    assert "feature/" not in yaml.safe_dump(on) and "pull_request" not in yaml.safe_dump(on)
    # the affected domains of a merge = the diff of the push (previous develop tip .. merge commit)
    run = wf["jobs"]["detect"]["steps"][-1]["run"]
    env = wf["jobs"]["detect"]["steps"][-1]["env"]
    assert env["BASE"] == "${{ github.event.before }}" and env["HEAD_SHA"] == "${{ github.sha }}"
    assert 'git diff --name-only "$BASE" "$HEAD_SHA" > changed.txt' in run
    assert "pull_request" not in run and "merge-base" not in run
    # a push on develop is live; only a dispatch can ask for a dry run
    assert wf["jobs"]["domain"]["with"]["dry_run"] == "${{ github.event_name == 'workflow_dispatch' && inputs.dry_run }}"


def test_entry_reconciles_selected_domains_serially_through_the_reusable_pipeline_and_fails_loudly():
    text, wf, _ = _entry()
    job = wf["jobs"]["domain"]
    assert job["uses"] == "./.github/workflows/mcp-deploy.yml" and job["secrets"] == "inherit"
    assert job["needs"] == "detect" and job["if"].strip() == "${{ needs.detect.outputs.count != '0' }}"
    assert job["strategy"]["max-parallel"] == 1        # SERIAL: one domain completely before the next
    assert job["strategy"]["fail-fast"] is True        # a failing domain fails the run; the rest are cancelled, never skipped
    assert job["strategy"]["matrix"] == {"domain": "${{ fromJson(needs.detect.outputs.domains) }}"}
    assert job["with"] == {"domain": "${{ matrix.domain }}", "environment": "dev",
                           "dry_run": "${{ github.event_name == 'workflow_dispatch' && inputs.dry_run }}"}
    assert job["name"] == "${{ matrix.domain }}"
    assert "steps" not in job and "python " not in yaml.safe_dump(job)  # orchestration only - the Python automation is not duplicated
    assert "concurrency" not in wf and "concurrency" not in job  # serialization = max-parallel here + the bridge job's group across runs


def test_reusable_pipeline_requires_only_the_contract_and_bridge_config_from_a_domain():
    """A brand-new domain provides domains/<d>/src/api/openapi.yaml + src/mcp/bridge-config.yaml;
    a QE suite is optional (the shared contract-derived gate always runs)."""
    text, wf = _wf()
    validate = _steps(wf["jobs"]["validate"])
    assert "Domain inputs present (fail closed)" in validate
    present = validate["Domain inputs present (fail closed)"]["run"]
    assert "domains/${{ inputs.domain }}/src/mcp/bridge-config.yaml" in present and "--contract-path" in validate["Resolve the domain's MCP contract (bridge-config.yaml `contract:` or the single src/api candidate)"]["run"] and "domains/${{ inputs.domain }}/src/mcp/bridge-config.yaml" in present
    gate = validate["Contract <-> MCP generation gate (shared, contract-derived)"]["run"]
    assert 'python "$TOOLING_DIR"/scripts/contract_gate.py' in gate and 'python "$TOOLING_DIR"/scripts/validate_mcp_metadata.py' in gate
    assert "if [ -f domains/${{ inputs.domain }}/qe/test_mcp_generation.py ]" in gate   # optional, not required
    assert "if [ -f domains/${{ inputs.domain }}/qe/requirements-dev.txt ]" in validate["Install test dependencies"]["run"]
    for domain in ad.processable(str(REPO)):  # every ready domain satisfies the convention
        cfg = yaml.safe_load((REPO / "domains" / domain / "src" / "mcp" / "bridge-config.yaml").read_text(encoding="utf-8"))
        assert cfg["domain"] == domain and "mcp_asset_id" not in cfg and "base_path" not in cfg
        assert (REPO / ad.resolve_contract(str(REPO), domain)[0]).exists()
        assert not list((REPO / "domains" / domain / "src" / "mcp").glob("*.py"))


# ── the job-level latch: off -> live/bridge SKIPPED (green run), on -> they run; scripts still gate ──

def _evaluate(expr, dry_run, latch):
    """Evaluate a GitHub `if` expression of the shape used here with concrete values (no guessing:
    only the two tokens the gate uses are substituted; anything else fails the test)."""
    inner = expr.strip()
    assert inner.startswith("${{") and inner.endswith("}}"), expr
    inner = inner[3:-2].strip()
    inner = inner.replace("inputs.dry_run", str(bool(dry_run))).replace("vars.MCP_DEPLOY_ENABLED == 'true'", str(latch == "true"))
    inner = inner.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    assert set(inner.replace("(", " ").replace(")", " ").split()) <= {"True", "False", "and", "or", "not"}, inner
    return eval(inner)  # noqa: S307 - a boolean expression over literals only, asserted above


@pytest.mark.parametrize("latch", ["", "false", "TRUE", "yes", None])
def test_latch_off_skips_live_and_bridge_on_a_push_and_the_run_stays_green(latch):
    """A normal push to develop (dry_run=false) with MCP_DEPLOY_ENABLED unset or anything but 'true'."""
    text, wf = _wf()
    for job in ("live", "bridge"):
        assert _evaluate(wf["jobs"][job]["if"], dry_run=False, latch=latch) is False, job
    # nothing above the gate is conditional on the latch: validate/generate/preview always run
    for job in ("validate", "generate", "preview"):
        assert "if" not in wf["jobs"][job] and "MCP_DEPLOY_ENABLED" not in str(wf["jobs"][job])
    # and nothing in those jobs can write: every network-capable script runs in --dry-run there
    runs = "\n".join(s.get("run", "") for j in ("validate", "generate", "preview") for s in wf["jobs"][j]["steps"])
    for tool in ("release_version.py", "exchange_publish.py", "deploy_bridge.py"):
        for m in re.finditer(r'python "\$TOOLING_DIR"/scripts/%s[^\n]*(\n[^\n]*\\)*[^\n]*' % re.escape(tool), runs):
            assert "--execute" not in m.group(0) and "--resolve" not in m.group(0), (tool, m.group(0))
    assert "MCP_LIVE_ENABLED" not in str(wf["jobs"]["generate"].get("env")) and "secrets." not in str(wf["jobs"]["generate"].get("env"))


def test_latch_on_runs_live_and_bridge_only_for_a_live_request():
    text, wf = _wf()
    for job in ("live", "bridge"):
        assert _evaluate(wf["jobs"][job]["if"], dry_run=False, latch="true") is True, job     # push / dispatch dry_run=false + latch on
        assert _evaluate(wf["jobs"][job]["if"], dry_run=True, latch="true") is False, job     # dispatch dry_run=true never goes live
    # the latch is still re-checked INSIDE the live job (defense in depth) and the scripts' own gates stay
    live = _steps(wf["jobs"]["live"])
    assert '[ "$MCP_DEPLOY_ENABLED" = "true" ]' in live["Latch check (fail closed)"]["run"]
    assert wf["jobs"]["live"]["env"]["MCP_LIVE_ENABLED"] == "true" and wf["jobs"]["bridge"]["env"]["MCP_LIVE_ENABLED"] == "true"
    for name in ("exchange_publish.py", "provision_bridge.py", "release_version.py", "mcp_conformance.py"):
        assert "MCP_LIVE_ENABLED" in (SRC / name).read_text(encoding="utf-8"), name
    assert "_MCP_DEPLOY_ENABLED" not in text.replace("MCP_DEPLOY_ENABLED", "")  # still ONE shared latch, never per domain
