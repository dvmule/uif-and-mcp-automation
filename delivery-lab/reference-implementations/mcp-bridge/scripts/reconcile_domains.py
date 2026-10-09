"""reconcile_domains.py - ONE local command that behaves like the generic GitHub entry
point (mcp.yml + mcp-deploy.yml): discover the domains, reconcile them SERIALLY, stop at
the first failure. It orchestrates the EXISTING modules exactly as the workflow does and
contains no release, Exchange, generation, Bridge or conformance logic of its own.

    py .../reconcile_domains.py --domains all --environment dev --dry-run    # zero network
    py .../reconcile_domains.py --domains all --environment dev --preflight  # live READS only
    py .../reconcile_domains.py --domains all --environment dev --execute    # the reconciliation

DISCOVERY = affected_domains.py (every domains/<d>/src/mcp/bridge-config.yaml is an onboarded
domain; nothing is hardcoded). `--domains all` takes every onboarded domain in discovery order
(alphabetical); `--domains a,b` exactly those; `--changed-files FILE` the ones a change touches.
Each selected domain is CLASSIFIED before ANY domain runs: ready -> reconciled; skip:no-contract
/ skip:disabled -> reported and skipped; error (ambiguous or missing explicit contract, bad
file) -> the whole run is REFUSED up front. Folders without the onboarding file are reported.

PER DOMAIN, IN THIS ORDER (= the validate job's gate, then the live job, then the bridge
job of mcp-deploy.yml - test_reconcile_domains.py pins the alignment):
  gate           generate at 0.0.0 + validate_mcp_metadata + contract_gate   (validate job)
  release        release_version.py   --resolve | --dry-run                   (live: Resolve release version)
  generate       generate_mcp_metadata.py from openapi-release.yaml            (live: Generate release artifacts)
  publish-rest   exchange_publish.py rest  --execute | --dry-run               (live: Publish REST asset)
  validate       validate_mcp_metadata.py + contract_gate.py                   (live: Validate artifacts)
  deploy-bridge  deploy_bridge.py  --execute | --preflight | --dry-run         (bridge: Deploy Bridge)
                 = ensure/reuse source instance -> read the SHARED Bridge (identity guards,
                   protected identities) -> merge + resolve/publish the shared MCP asset ->
                   CREATE | ADD | UPDATE | NO-OP -> read-back
  conformance    mcp_conformance.py  --execute | --dry-run                     (bridge: Conformance)
Each domain re-reads the live shared state inside deploy_bridge, so the order of domains
never matters for correctness (the first CREATEs the Bridge, every later one ADDs to it).

MODES: --dry-run = every step in its own dry-run (no network, not even a token request);
--preflight = release --resolve (Exchange READS), REST publish preview (local), deploy_bridge
--preflight (authenticated READS + the exact requests it would send), conformance preview -
NO Anypoint write; --execute = the reconciliation. --preflight/--execute require
MCP_LIVE_ENABLED=true + ANYPOINT_CLIENT_ID/SECRET in the environment (checked up front; the
scripts enforce it again). Locally there is no MCP_DEPLOY_ENABLED latch - that is GitHub's.

FAIL CLOSED: a missing config, a refused identity, a rejected write, a failed read-back or a
non-zero step ends the run at once; later domains never start. Outputs per domain under
<out>/<domain>/ (release.json, artifacts, source-instance.json, shared-mcp-metadata.json ...).
"""
import argparse
import os
import sys

import affected_domains as ad
import contract_gate
import deploy_bridge
import exchange_publish
import generate_mcp_metadata
import mcp_conformance
import platform_config as pc
import provision_bridge as pb
import release_version
import shared_bridge as sb
import validate_mcp_metadata

# (orchestrator step, the workflow step it mirrors) - pinned by test_reconcile_domains.py
STEPS = (
    ("gate", "Contract <-> MCP generation gate (shared, contract-derived)"),
    ("release", "Resolve release version (Exchange reads only)"),
    ("generate", "Generate release artifacts"),
    ("publish-rest", "Publish REST asset"),
    ("validate", "Validate artifacts"),
    ("deploy-bridge", "Deploy Bridge (ensure upstream -> shared asset -> CREATE | ADD | UPDATE | NO-OP)"),
    ("conformance", "Conformance (initialize + tools/list)"),
)
CONFORMANCE_RETRIES = ("--retries", "6", "--retry-delay", "10")  # the workflow's bounded propagation window


def _paths(repo_root, domain):
    """The domain's MCP inputs: bridge-config.yaml (the onboarding file) and the contract it
    selects (`contract:` or the single src/api candidate - affected_domains.classify)."""
    base = os.path.join(repo_root, "domains", domain)
    rec = ad.classify(repo_root, domain)
    if rec["status"] != ad.READY:
        sys.exit("REFUSED: %s is not processable (%s): %s" % (domain, rec["status"], rec["reason"]))
    rel = rec["contract"]
    return {"spec": os.path.join(repo_root, *rel.split("/")),
            "config": os.path.join(base, *ad.CONFIG_REL.split("/"))}


def _step(name, fn, argv):
    """Run one existing module's main() exactly as the workflow invokes the script; any
    non-zero return stops the run (a SystemExit inside the module propagates unchanged)."""
    print("\n##### %s: %s" % (name, " ".join(argv)))
    rc = fn(argv)
    if rc:
        sys.exit("STOP: step %r returned %s - later steps and later domains do not run." % (name, rc))


def reconcile_domain(domain, environment, mode, repo_root, out_root, platform_config, base_url):
    paths = _paths(repo_root, domain)
    out = os.path.join(out_root, domain)
    os.makedirs(out, exist_ok=True)
    cfg = pb.load_yaml(paths["config"])
    try:  # ${VAR} placeholders -> resolved build artifact; every step below receives the RESOLVED file
        platform_config = pc.write_resolved(platform_config, environment, out)
    except pc.PlatformError as e:
        sys.exit("REFUSED (platform config): %s" % e)
    platform_env = (pb.load_yaml(platform_config) or {}).get(environment) or {}
    if not platform_env.get("org_id"):
        sys.exit("REFUSED: %s has no org_id for %r" % (platform_config, environment))
    shared = sb.load_shared(platform_env, environment)
    eff = sb.effective_config(cfg, shared)  # shared identity + protected-identity guard, before anything runs
    upstream = ((eff.get("environments") or {}).get(environment) or {}).get("upstream_url")
    if not upstream:
        sys.exit("REFUSED: %s has no environments.%s.upstream_url (neither the common UIF API upstream in anypoint-environments.yaml <env>.upstream_url nor a domain override) - the deployed API's service origin is never invented." % (paths["config"], environment))
    org = platform_env["org_id"]
    rest_asset = eff["source_asset_id"]
    route_label = eff.get("route_label", rest_asset)
    live = mode != "dry-run"
    print("\n================ %s / %s (%s) ================" % (domain, environment, mode))
    print("contract %s | REST asset %s | route %s | shared Bridge %s (%s) | out %s" % (
        paths["spec"], rest_asset, route_label, shared["instance_label"], shared["mcp_asset_id"], out))

    gate = os.path.join(out, "gate")
    _step("gate", generate_mcp_metadata.main, ["--spec", paths["spec"], "--source-asset-id", "gate", "--source-version", "0.0.0",
                                                "--asset-id", "gate", "--asset-version", "0.0.0", "--org-id", "gate", "--out", gate])
    _step("gate", validate_mcp_metadata.main, ["--metadata", os.path.join(gate, "mcp-metadata.json"), "--mappings", os.path.join(gate, "tool-mappings.json")])
    _step("gate", contract_gate.main, ["--spec", paths["spec"], "--metadata", os.path.join(gate, "mcp-metadata.json"),
                                       "--mappings", os.path.join(gate, "tool-mappings.json")])

    _step("release", release_version.main, ["--spec", paths["spec"], "--rest-asset-id", rest_asset, "--org-id", org, "--out", out,
                                            "--base-url", base_url, "--resolve" if live else "--dry-run"])
    release = release_version.read_release(out)
    version = release["version"]
    stamped = os.path.join(out, release_version.RELEASE_SPEC)

    _step("generate", generate_mcp_metadata.main, ["--spec", stamped, "--source-asset-id", rest_asset, "--source-version", version,
                                                    "--asset-id", shared["mcp_asset_id"], "--asset-version", version, "--org-id", org,
                                                    "--route-label", route_label, "--transcoding-path", shared["transcoding_path"], "--out", out])

    _step("publish-rest", exchange_publish.main, ["rest", "--spec", stamped, "--out", out, "--org-id", org, "--asset-id", rest_asset,
                                                  "--asset-version", version, "--base-url", base_url,
                                                  "--execute" if mode == "execute" else "--dry-run"])

    _step("validate", validate_mcp_metadata.main, ["--metadata", os.path.join(out, "mcp-metadata.json"), "--mappings", os.path.join(out, "tool-mappings.json")])
    _step("validate", contract_gate.main, ["--spec", stamped, "--metadata", os.path.join(out, "mcp-metadata.json"),
                                           "--mappings", os.path.join(out, "tool-mappings.json")])

    if mode == "preflight" and release.get("decision") == "initial":
        # a read-only rehearsal cannot inspect a Bridge route whose REST asset does not exist yet
        # (deploy_bridge --preflight reads the asset's Exchange descriptor and fails closed);
        # execute publishes the asset first and then CREATEs the Bridge / ADDs the route.
        print("\n##### deploy-bridge: SKIPPED in preflight - REST asset %s is absent (release decision 'initial'); --execute would publish "
              "%s@%s first, then deploy_bridge would CREATE the shared Bridge or ADD route %s to it." % (rest_asset, rest_asset, version, route_label))
    else:
        _step("deploy-bridge", deploy_bridge.main, ["--config", paths["config"], "--artifacts", out, "--environment", environment,
                                                    "--release-file", os.path.join(out, release_version.RELEASE_FILE), "--out", out,
                                                    "--platform-config", platform_config, "--base-url", base_url, "--" + mode])

    expected = os.path.join(out, deploy_bridge.SHARED_METADATA_FILE if mode == "execute" else "mcp-metadata.json")
    _step("conformance", mcp_conformance.main, ["--mcp-url", "https://%s/%s/mcp" % (platform_env.get("gateway_public_host"), shared["base_path"]),
                                                "--expected-metadata", expected, *CONFORMANCE_RETRIES,
                                                "--execute" if mode == "execute" else "--dry-run"])
    return {"domain": domain, "rest": "%s@%s (%s)" % (rest_asset, version, release.get("decision")), "mode": mode}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--environment", required=True)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--domains", help="'all' (every discovered domain, discovery order) or a comma-separated list")
    sel.add_argument("--changed-files", metavar="FILE", help="newline-separated repo-relative paths -> the domains they affect")
    ap.add_argument("--repo-root", default=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *[".."] * 4)))
    ap.add_argument("--out", default=None, help="build root (default <repo>/build/mcp); each domain writes to <out>/<domain>/")
    ap.add_argument("--platform-config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "anypoint-environments.yaml"))
    ap.add_argument("--base-url", default=os.environ.get("ANYPOINT_BASE_URL", pb.DEFAULT_BASE_URL))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", default="dry-run", help="zero network calls (default)")
    g.add_argument("--preflight", dest="mode", action="store_const", const="preflight", help="authenticated READS only; no Anypoint write")
    g.add_argument("--execute", dest="mode", action="store_const", const="execute", help="the reconciliation (MCP_LIVE_ENABLED=true + credentials)")
    a = ap.parse_args(argv)
    repo_root = os.path.abspath(a.repo_root)
    out_root = a.out or os.path.join(repo_root, "build", "mcp")

    discovered = ad.discover(repo_root)
    if a.changed_files:
        changed = [ln.strip() for ln in open(a.changed_files, encoding="utf-8") if ln.strip()]
        selected = ad.affected(changed, discovered)
    elif a.domains.strip().lower() == "all":
        selected = list(discovered)
    else:
        selected = sorted({d.strip() for d in a.domains.split(",") if d.strip()})
        unknown = [d for d in selected if d not in discovered]
        if unknown:
            sys.exit("REFUSED: %s not onboarded (no domains/<d>/%s)." % (", ".join(unknown), ad.CONFIG_REL))
    missing_cfg = ad.missing_onboarding(repo_root)
    print("== RECONCILE DOMAINS (%s) == onboarded: %s | selected: %s%s" % (
        a.mode, ", ".join(discovered) or "none", ", ".join(selected) or "none",
        " | MISSING ONBOARDING CONFIGURATION (excluded): %s" % ", ".join(missing_cfg) if missing_cfg else ""))
    rep = ad.report(repo_root, selected)  # classified for EVERY selected domain before ANY domain runs
    if rep["errors"]:
        sys.exit("REFUSED: domain configuration error(s) (checked for every selected domain before any domain runs):\n  "
                 + "\n  ".join("%s: %s" % e for e in rep["errors"]))
    skipped = [(d, rep["records"][d]["reason"]) for d in rep["skipped_no_contract"] + rep["skipped_disabled"]]
    for d, why in skipped:
        print("SKIP %-12s %s" % (d, why))
    selected = rep["processed"]
    if not selected:
        print("nothing to reconcile (processed: none%s)." % ("; skipped: %s" % ", ".join(d for d, _ in skipped) if skipped else ""))
        return 0
    if a.mode != "dry-run":
        if os.environ.get("MCP_LIVE_ENABLED") != "true":
            sys.exit("REFUSED: --%s performs authenticated Anypoint %s and requires MCP_LIVE_ENABLED=true." % (
                a.mode, "reads" if a.mode == "preflight" else "reads and writes"))
        if not os.environ.get("ANYPOINT_CLIENT_ID") or not os.environ.get("ANYPOINT_CLIENT_SECRET"):
            sys.exit("REFUSED: set ANYPOINT_CLIENT_ID / ANYPOINT_CLIENT_SECRET (Connected App).")
    print("order (serial, stop at the first failure): %s" % " -> ".join(selected))

    results = []
    for domain in selected:
        results.append(reconcile_domain(domain, a.environment, a.mode, repo_root, out_root, a.platform_config, a.base_url))
    print("\n== DONE (%s) ==" % a.mode)
    for r in results:
        print("  %-12s %s" % (r["domain"], r["rest"]))
    for d, why in skipped:
        print("  %-12s SKIPPED - %s" % (d, why))
    if a.mode == "dry-run":
        print("DRY-RUN: nothing sent (no token request either).")
    elif a.mode == "preflight":
        print("PREFLIGHT ONLY: Anypoint was READ, nothing was published or modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
