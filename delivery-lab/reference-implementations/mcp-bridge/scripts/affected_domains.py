"""affected_domains.py - discover MCP domains from the repository tree, classify them, and
select the ones a change touches. The ONE generic GitHub entry point (.github/workflows/mcp.yml)
runs this first, then reconciles the PROCESSED domains SERIALLY through the unchanged reusable
pipeline (mcp-deploy.yml) - no per-domain workflow, no hardcoded domain list, no per-domain
latch variable.

ONBOARDING (convention, never a registry): EVERY folder directly under domains/ is a UIF
domain and must carry its MCP onboarding file
    domains/<domain>/src/mcp/bridge-config.yaml
from the moment the domain is onboarded - whether or not it ships an OpenAPI contract yet.
The file is DATA only:
    domain: <domain>
    mcp_enabled: true|false      # default true; false = onboarded to UIF, not (yet) an MCP surface
    contract: src/api/<api>/openapi.yaml   # only when the domain ships SEVERAL contracts (never guessed)
A folder WITHOUT the file is reported as "missing onboarding configuration" and excluded; it
never crashes the discovery of the other domains, and the repository test suite fails on it.

CLASSIFICATION of an onboarded domain (one status, always reported, never silent):
    ready               a contract is available: `contract:` (must exist) or exactly one candidate
                        among src/api/openapi.yaml and src/api/*/openapi.yaml -> PROCESSED
    skip:disabled       mcp_enabled: false -> SKIPPED, regardless of contract availability
    skip:no-contract    mcp_enabled and zero candidates -> SKIPPED cleanly ("contract not available");
                        the day src/api/<api>/openapi.yaml appears, the SAME file makes it ready
    error               several candidates without `contract:` (ambiguous), an explicit `contract:`
                        that does not exist or escapes the domain, an unparsable file, a non-boolean
                        mcp_enabled, a `domain:` that does not match the folder -> FAIL CLOSED
Everything else defaults by convention - source_asset_id -> uif-<domain>-api, route label ->
source_asset_id, upstream -> the COMMON UIF API upstream of the environment
(anypoint-environments.yaml <env>.upstream_url). The authoritative contract = the selected
OpenAPI root + every LOCAL $ref dependency reachable from it under domains/<domain>/src/
(contract_bundle.py); this module never parses the graph.

SELECTION (which onboarded domains one workflow run looks at):
  domain-owned change   domains/<d>/src/api/**, src/schemas/** or src/mcp/**           -> <d>
  shared change         the MCP tooling home, mcp-deploy.yml, mcp.yml                  -> EVERY onboarded domain
  --all                 every onboarded domain (first push of a branch, no diff base, manual run)
  --only a,b            exactly these domains (workflow_dispatch; refused unless onboarded)
A newly ADDED contract or schema under an onboarded domain is a domain-owned change, so the
existing path triggers wake the run and the domain turns from skip:no-contract into ready with
no configuration or code change. Whether a PROCESSED domain actually changed semantically is not
decided here: release_version.py compares the whole bundled contract against Exchange and the
Bridge reconciliation compares the live route, so an unchanged domain ends as NO-OP.

Output: a sorted JSON list of the PROCESSED domains on stdout and, when $GITHUB_OUTPUT is set,
`domains`, `count`, `skipped_no_contract`, `skipped_disabled`, `missing_config` (JSON lists);
when $GITHUB_STEP_SUMMARY is set, a per-domain status table so a skip can never be mistaken
for a successful processing. `--contract-path <domain>` prints the resolved repo-relative
contract path of ONE ready domain (the workflows use it instead of a literal). Read-only; no network.
"""
import argparse
import glob
import json
import os
import re
import sys

import yaml

CONFIG_REL = "src/mcp/bridge-config.yaml"
CONTRACT_CANDIDATES = ("src/api/openapi.yaml", "src/api/*/openapi.yaml")
SHARED_PREFIXES = ("delivery-lab/reference-implementations/mcp-bridge/",)
SHARED_FILES = (".github/workflows/mcp-deploy.yml", ".github/workflows/mcp.yml")
_DOMAIN_OWNED = re.compile(r"^domains/([A-Za-z0-9_-]+)/src/(api|mcp|schemas)/")

READY, SKIP_NO_CONTRACT, SKIP_DISABLED, MISSING_CONFIG, ERROR = "ready", "skip:no-contract", "skip:disabled", "missing-config", "error"
SKIPS = (SKIP_NO_CONTRACT, SKIP_DISABLED)


def _norm(path):
    """Repo-relative POSIX path: backslashes -> '/', ONE leading './' removed (never a bare
    leading '.', which belongs to '.github/...')."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def domain_dir(repo_root, domain):
    return os.path.join(repo_root, "domains", domain)


def domain_folders(repo_root):
    """Every folder directly under domains/ - every one of them is an onboarded UIF domain."""
    domains_dir = os.path.join(repo_root, "domains")
    if not os.path.isdir(domains_dir):
        return []
    return sorted(d for d in os.listdir(domains_dir) if os.path.isdir(os.path.join(domains_dir, d)) and not d.startswith((".", "_")))


def has_config(repo_root, domain):
    return os.path.isfile(os.path.join(domain_dir(repo_root, domain), *CONFIG_REL.split("/")))


def discover(repo_root):
    """Every domain carrying its MCP onboarding file (bridge-config.yaml), sorted."""
    return [d for d in domain_folders(repo_root) if has_config(repo_root, d)]


def missing_onboarding(repo_root):
    """Domain folders WITHOUT the onboarding file - an onboarding defect, reported and excluded."""
    return [d for d in domain_folders(repo_root) if not has_config(repo_root, d)]


def contract_candidates(repo_root, domain):
    """Repo-relative candidates in deterministic order: src/api/openapi.yaml, then src/api/*/openapi.yaml."""
    base = domain_dir(repo_root, domain)
    found = []
    for pattern in CONTRACT_CANDIDATES:
        for hit in sorted(glob.glob(os.path.join(base, *pattern.split("/")))):
            if os.path.isfile(hit):
                found.append(_norm(os.path.relpath(hit, repo_root)))
    return found


def classify(repo_root, domain):
    """{'domain', 'status', 'contract', 'reason'} - see the module docstring for the statuses."""
    rec = {"domain": domain, "status": None, "contract": None, "reason": ""}
    cfg_rel = "domains/%s/%s" % (domain, CONFIG_REL)
    if not has_config(repo_root, domain):
        return dict(rec, status=MISSING_CONFIG, reason="%s is missing - every UIF domain folder must carry its MCP onboarding file "
                                                      "(domain: <domain>, optional mcp_enabled / contract)" % cfg_rel)
    try:
        cfg = yaml.safe_load(open(os.path.join(repo_root, *cfg_rel.split("/")), encoding="utf-8-sig")) or {}
    except Exception as e:  # noqa: BLE001
        return dict(rec, status=ERROR, reason="%s cannot be parsed: %s" % (cfg_rel, str(e).splitlines()[0]))
    if not isinstance(cfg, dict) or not cfg.get("domain"):
        return dict(rec, status=ERROR, reason="%s must declare `domain: %s`" % (cfg_rel, domain))
    if str(cfg["domain"]) != domain:
        return dict(rec, status=ERROR, reason="%s declares domain %r but lives in domains/%s" % (cfg_rel, cfg["domain"], domain))
    enabled = cfg.get("mcp_enabled", True)
    if not isinstance(enabled, bool):
        return dict(rec, status=ERROR, reason="%s mcp_enabled must be true or false, got %r" % (cfg_rel, enabled))
    if not enabled:
        return dict(rec, status=SKIP_DISABLED, reason="MCP disabled (mcp_enabled: false) - onboarded to UIF, intentionally not an MCP surface yet")
    declared = cfg.get("contract")
    if declared:
        rel = _norm(str(declared)).lstrip("/")
        if rel.startswith("..") or "/../" in rel or os.path.isabs(str(declared)):
            return dict(rec, status=ERROR, reason="%s `contract: %s` must be a path inside the domain" % (cfg_rel, declared))
        if not os.path.isfile(os.path.join(domain_dir(repo_root, domain), *rel.split("/"))):
            return dict(rec, status=ERROR, reason="%s `contract: %s` does not exist - an explicit contract must exist (fail, not skip)" % (cfg_rel, declared))
        return dict(rec, status=READY, contract="domains/%s/%s" % (domain, rel), reason="explicit contract")
    candidates = contract_candidates(repo_root, domain)
    if len(candidates) == 1:
        return dict(rec, status=READY, contract=candidates[0], reason="single contract candidate")
    if not candidates:
        return dict(rec, status=SKIP_NO_CONTRACT, reason="contract not available (no %s yet) - processed automatically once one appears"
                                                           % " or ".join(CONTRACT_CANDIDATES))
    return dict(rec, status=ERROR, reason="%s ships %d OpenAPI contracts (%s) - set `contract: <one of them>` in %s; the MCP automation never guesses"
                % ("domains/%s" % domain, len(candidates), ", ".join(c.split("/", 2)[2] for c in candidates), CONFIG_REL))


def resolve_contract(repo_root, domain):
    """(repo-relative contract path or None, problem/skip reason or None) - the READY contract of a domain."""
    rec = classify(repo_root, domain)
    return (rec["contract"], None) if rec["status"] == READY else (None, rec["reason"])


def processable(repo_root):
    """Onboarded domains that are READY (contract available, MCP enabled)."""
    return [d for d in discover(repo_root) if classify(repo_root, d)["status"] == READY]


def report(repo_root, domains):
    """Statuses of the given domains grouped: {'processed': [...], 'skipped_no_contract': [...],
    'skipped_disabled': [...], 'errors': [(domain, reason)], 'records': {domain: record}}."""
    out = {"processed": [], "skipped_no_contract": [], "skipped_disabled": [], "errors": [], "records": {}}
    for d in domains:
        rec = classify(repo_root, d)
        out["records"][d] = rec
        if rec["status"] == READY:
            out["processed"].append(d)
        elif rec["status"] == SKIP_NO_CONTRACT:
            out["skipped_no_contract"].append(d)
        elif rec["status"] == SKIP_DISABLED:
            out["skipped_disabled"].append(d)
        else:
            out["errors"].append((d, rec["reason"]))
    return out


def missing_configuration(repo_root, domains):
    """(domain, problem) for every given domain whose onboarding file is missing or in ERROR (skips are not problems)."""
    return [(d, rec["reason"]) for d in domains for rec in (classify(repo_root, d),) if rec["status"] in (MISSING_CONFIG, ERROR)]


def domain_of(path):
    """'domains/<d>/src/api/...', '.../src/schemas/...' or '.../src/mcp/...' -> <d>; else None."""
    m = _DOMAIN_OWNED.match(_norm(path))
    return m.group(1) if m else None


def is_shared(path):
    p = _norm(path)
    return p in SHARED_FILES or any(p.startswith(prefix) for prefix in SHARED_PREFIXES)


def affected(changed, discovered):
    """Pure decision over repo-relative paths. A domain-owned change selects its domain when
    the domain is onboarded (or when the change IS the domain's onboarding file, so a brand
    new domain is selected by the very commit that onboards it)."""
    selected = set()
    for path in changed:
        p = _norm(path)
        if is_shared(p):
            return sorted(discovered)
        d = domain_of(p)
        if d and (d in discovered or p.endswith("/" + CONFIG_REL)):
            selected.add(d)
    return sorted(selected)


def _summary_table(records, missing):
    rows = ["| Domain | Status | Contract / reason |", "|---|---|---|"]
    for d, rec in sorted(records.items()):
        rows.append("| %s | %s | %s |" % (d, rec["status"], rec["contract"] or rec["reason"]))
    for d in missing:
        rows.append("| %s | %s | %s |" % (d, MISSING_CONFIG, "domains/%s/%s is missing" % (d, CONFIG_REL)))
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--changed-files", metavar="FILE", help="newline-separated repo-relative paths (git diff --name-only)")
    g.add_argument("--all", action="store_true", help="every onboarded domain")
    g.add_argument("--only", metavar="DOMAINS", help="comma-separated domains (workflow_dispatch); refused unless onboarded")
    g.add_argument("--contract-path", metavar="DOMAIN", help="print the resolved repo-relative contract path of ONE ready domain")
    a = ap.parse_args(argv)

    if a.contract_path:
        rec = classify(a.repo_root, a.contract_path)
        if rec["status"] != READY:
            sys.exit("REFUSED: %s is not processable (%s): %s" % (a.contract_path, rec["status"], rec["reason"]))
        print(rec["contract"])
        return 0

    discovered = discover(a.repo_root)
    missing = missing_onboarding(a.repo_root)
    print("onboarded domains (domains/<d>/%s): %s" % (CONFIG_REL, ", ".join(discovered) or "none"))
    if missing:
        print("::warning::MISSING ONBOARDING CONFIGURATION - excluded, never processed: %s (add domains/<d>/%s)" % (", ".join(missing), CONFIG_REL))
    if a.all:
        selected = list(discovered)
        how = "--all"
    elif a.only:
        selected = sorted({d.strip() for d in a.only.split(",") if d.strip()})
        unknown = [d for d in selected if d not in discovered]
        if unknown:
            sys.exit("REFUSED: %s not onboarded (no domains/<d>/%s) - nothing to reconcile." % (", ".join(unknown), CONFIG_REL))
        how = "--only"
    else:
        changed = [ln.strip() for ln in open(a.changed_files, encoding="utf-8") if ln.strip()]
        selected = affected(changed, discovered)
        how = "%d changed file(s)" % len(changed)

    rep = report(a.repo_root, selected)
    if rep["errors"]:
        sys.exit("REFUSED: domain configuration error(s) - the run would fail later or, worse, guess:\n  "
                 + "\n  ".join("%s: %s" % e for e in rep["errors"])
                 + "\n  bridge-config.yaml must declare `domain:`; set `contract:` when the domain ships several OpenAPI contracts, "
                   "`mcp_enabled: false` to keep an onboarded domain out of MCP; source_asset_id defaults to uif-<domain>-api and the "
                   "upstream_url defaults to the common UIF API upstream (anypoint-environments.yaml <env>.upstream_url) unless overridden.")
    for d in rep["processed"]:
        print("  PROCESS %-12s contract %s" % (d, rep["records"][d]["contract"]))
    for d in rep["skipped_no_contract"]:
        print("  SKIP    %-12s %s" % (d, rep["records"][d]["reason"]))
    for d in rep["skipped_disabled"]:
        print("  SKIP    %-12s %s" % (d, rep["records"][d]["reason"]))
    print("selected (%s): %s | processed: %s | skipped (no contract): %s | skipped (mcp disabled): %s | missing onboarding config: %s" % (
        how, ", ".join(selected) or "none", ", ".join(rep["processed"]) or "none", ", ".join(rep["skipped_no_contract"]) or "none",
        ", ".join(rep["skipped_disabled"]) or "none", ", ".join(missing) or "none"))
    print(json.dumps(rep["processed"]))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("domains=%s\n" % json.dumps(rep["processed"]))
            f.write("count=%d\n" % len(rep["processed"]))
            f.write("skipped_no_contract=%s\n" % json.dumps(rep["skipped_no_contract"]))
            f.write("skipped_disabled=%s\n" % json.dumps(rep["skipped_disabled"]))
            f.write("missing_config=%s\n" % json.dumps(missing))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## MCP domains - %s\n\n%s\n\n" % (how, _summary_table(rep["records"], missing)))
            f.write("Processed domains run the reusable pipeline serially; skipped domains ran NOTHING.\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
