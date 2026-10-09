"""ONE deployment command for a domain's MCP surface: its source instance + its route
in the SHARED MCP Bridge of the environment.

From the operator's point of view this is a single step; internally it is the
proven steward stages orchestrated in order, in-process, fail-closed at every
boundary (their implementations are NOT duplicated here - this module only
calls them):

  1. ensure_source_instance  - Exchange EXTERNAL instance of THIS domain's REST
                                asset (uif-<domain>-<env>): reuse if the
                                deterministic name exists with the expected URL,
                                else create it; read back; capture its UUID
                                (unchanged behaviour).
  2. inspect the SHARED Bridge - documented v1 list: how many instances carry the
                                shared label (anypoint-environments.yaml
                                shared_bridge.instance_label, DEV uif-domains-mcp)?
        >1 -> REFUSE (ambiguous identity)
        1  -> identity guards (update_bridge) BEFORE any Exchange write; read the
              MCP asset version it binds and that version's published metadata
              (the only complete record of the OTHER domains' tools)
  3. shared MCP asset        - merge THIS domain's generated metadata into the
                                bound content (its tools/connection replaced, every
                                other domain's preserved); resolve the asset version
                                from Exchange (shared_bridge.resolve_shared_version:
                                identical -> reuse | absent -> 1.0.0 | else patch
                                bump); publish it (frozen exchange_publish guard:
                                absent -> publish, identical -> no-op, different ->
                                FAIL CLOSED) only when the Bridge will change
  4. Bridge                  - absent    -> CREATE (provision_bridge, the proven
                                            full-payload create with the shared
                                            identity, --require-connection)
                                present   -> update_bridge: reconcile ONE route
                                            route absent   -> ADD
                                            route differs  -> UPDATE in place
                                            identical      -> NO-OP (no write)
                                            every other route preserved + verified
  5. writes <out>/shared-mcp-metadata.json (what the Bridge serves after this run)
     for the conformance stage, and <out>/shared-release.json (the asset decision).

A domain deployment therefore NEVER deletes, recreates or re-labels the shared
Bridge, and never touches another domain's upstream, connection, tools or
policies. Delete-then-create remains a separate, explicit operator action
(provision_bridge.py --delete-instance) for listener/path/TLS changes only.

Modes: --dry-run (default; renders the requests, no network), --preflight
(authenticated READ-ONLY: resolves reuse/create, inspects the Bridge, reports
CREATE / ADD / UPDATE / NO-OP and stops), --execute (the writes above),
--check-existing (read-only: which instance carries the shared label/path).

Versions: the domain's REST release (release_version.py -> --release-file, or
--rest-asset-version) binds the REST asset and this route's connection; the
shared MCP asset version is resolved here from Exchange (or pinned with
--mcp-asset-version for controlled runs). They are independent by design.

Run-scoped overrides (--source-name, --upstream-url, --instance-label,
--base-path) exist for controlled experiments; they never modify the domain or
platform configuration, and the standard identities are printed alongside so a
PoC can never be mistaken for the shared Bridge.
"""
import argparse
import json
import os
import sys
from types import SimpleNamespace

import ensure_source_instance as esi
import exchange_publish as xp
import platform_config as pc
import provision_bridge as pb
import release_version as rv
import shared_bridge as sb
import update_bridge as ub

SHARED_METADATA_FILE = "shared-mcp-metadata.json"
SHARED_RELEASE_FILE = "shared-release.json"
MCP_ASSET_DESCRIPTION = "UIF shared MCP asset: tools of every onboarded domain API, generated from the domain OpenAPI contracts."


def rest_version_for(a):
    """The domain's REST release version (release_version.py) binds the REST asset and this
    route's connection. --release-file supplies it; an explicit --rest-asset-version must
    agree; the generated metadata (connection.version) must carry the same version."""
    if not a.release_file and not a.rest_asset_version:
        sys.exit("REFUSED: pass --release-file <release.json> (from release_version.py) or --rest-asset-version.")
    version = a.rest_asset_version
    if a.release_file:
        rel = rv.read_release(a.release_file)
        if version and version != rel["version"]:
            sys.exit("FAIL CLOSED: --rest-asset-version %s != release version %s in %s - one release version drives "
                     "the REST asset and this route's connection; nothing sent." % (version, rel["version"], a.release_file))
        version = rel["version"]
        print("release   : %s (decision %s, %s)" % (version, rel.get("decision"), a.release_file))
    meta_path = os.path.join(a.artifacts, "mcp-metadata.json")
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path, encoding="utf-8-sig"))
        conn_version = str(((meta.get("connections") or [{}])[0].get("ref") or {}).get("version"))
        if conn_version != str(version):
            sys.exit("FAIL CLOSED: generated metadata binds the REST asset at %s but the release version is %s - "
                     "regenerate the artifacts from the resolved release; nothing sent." % (conn_version, version))
    return version


def preflight_source_capture(args, cfg, name, upstream_url, group, asset_id, version, pre_dir):
    """READ-ONLY twin of ensure_source_instance's capture for --preflight: resolves
    the external instance from the Exchange descriptor without writing anything
    remote. The capture goes to its own <out>/preflight/ directory so the run's
    real <out>/source-instance.json is never touched. Returns (path, data) or
    (None, None) when execute would CREATE the instance."""
    cid, sec = pb._live_gates()
    token = pb._token_for({"base_url": args.base_url}, cid, sec)
    descriptor = pb._get(args.base_url, token, "/exchange/api/v2/assets/%s/%s/%s" % (group, asset_id, version))
    action, inst = esi.resolve_external_instance(descriptor, name, upstream_url)
    if action != "reuse":
        return None, None
    return esi.capture(pre_dir, inst, descriptor, name)


def read_bound_metadata(base_url, token, group, asset_id, version):
    """The published mcp-metadata.json of <asset>@<version> (documented Exchange v2 reads)."""
    descriptor = xp._get_descriptor(base_url, token, group, asset_id, version)
    if descriptor is None:
        sys.exit("FAIL CLOSED (shared MCP asset): the Bridge binds %s@%s but Exchange has no such version - the other domains' "
                 "tools cannot be preserved from it; inspect the asset in Exchange." % (asset_id, version))
    content = xp._download_published_file(descriptor, sb.MCP_CLASSIFIER, sb.MCP_MAIN_FILE)
    if content is None:
        sys.exit("FAIL CLOSED (shared MCP asset): %s@%s has no downloadable %s - the other domains' tools cannot be preserved."
                 % (asset_id, version, sb.MCP_MAIN_FILE))
    return content


def write_shared_outputs(out, merged_bytes, release):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, SHARED_METADATA_FILE), "wb") as f:
        f.write(merged_bytes)
    with open(os.path.join(out, SHARED_RELEASE_FILE), "w", encoding="utf-8") as f:
        json.dump(release, f, indent=2)


def publish_shared_asset(a, org, group, asset_id, version, content):
    """Frozen exchange_publish guard, in-process: absent -> publish; identical -> no-op; different -> FAIL CLOSED."""
    fields = {"name": asset_id, "description": MCP_ASSET_DESCRIPTION, "type": "mcp"}
    print("shared MCP asset: publish %s/%s@%s (guarded: absent -> publish | identical -> no-op | different -> FAIL CLOSED)" % (group, asset_id, version))
    return xp._run_guarded_publish(SimpleNamespace(base_url=a.base_url, org_id=org), "MCP", group, asset_id, version, fields,
                                   "files.mcp-metadata.json", sb.MCP_MAIN_FILE, content, sb.MCP_CLASSIFIER, None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="domain bridge-config.yaml")
    ap.add_argument("--artifacts", required=True, help="generated artifacts dir (mcp-metadata.json, tool-mappings.json)")
    ap.add_argument("--environment", required=True)
    ap.add_argument("--release-file", default=None,
                    help="release.json written by release_version.py - the domain's REST release version (REST asset + this "
                         "route's connection); the generated metadata must carry the same version (fail closed otherwise)")
    ap.add_argument("--rest-asset-version", default=None, help="the REST release version when no --release-file is given (must agree with it otherwise)")
    ap.add_argument("--mcp-asset-version", default=None,
                    help="PIN the shared MCP asset version instead of resolving it from Exchange (controlled runs / tests)")
    ap.add_argument("--out", default=None, help="dir for source-instance.json, effective config, shared outputs + evidence (default: --artifacts)")
    ap.add_argument("--source-name", default=None, help="override the external instance name uif-<domain>-<env> (run-scoped)")
    ap.add_argument("--upstream-url", default=None, help="override bridge-config upstream_url for BOTH the external instance and this route's upstream (run-scoped)")
    ap.add_argument("--instance-label", default=None, help="override the shared Bridge label (run-scoped PoC only)")
    ap.add_argument("--base-path", default=None, help="override the shared Bridge base path (run-scoped PoC only)")
    ap.add_argument("--platform-config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "anypoint-environments.yaml"))
    ap.add_argument("--base-url", default=os.environ.get("ANYPOINT_BASE_URL", pb.DEFAULT_BASE_URL))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", default="dry-run")
    g.add_argument("--preflight", dest="mode", action="store_const", const="preflight")
    g.add_argument("--execute", dest="mode", action="store_const", const="execute")
    g.add_argument("--check-existing", dest="mode", action="store_const", const="check-existing",
                   help="READ-ONLY: list any instance carrying the shared Bridge label or base path")
    a = ap.parse_args(argv)
    out = a.out or a.artifacts
    # the platform file carries ${VAR} placeholders (GitHub Environment variables): resolve ONCE into a build
    # artifact and hand the RESOLVED file to every downstream/frozen script via --platform-config
    try:
        a.platform_config = pc.write_resolved(a.platform_config, a.environment, out)
    except pc.PlatformError as e:
        sys.exit("REFUSED (platform config): %s" % e)

    cfg = pb.load_yaml(a.config)
    platform_env = (pb.load_yaml(a.platform_config) or {}).get(a.environment) or {}
    org = platform_env.get("org_id") or sys.exit("REFUSED: anypoint-environments.yaml has no org_id for %r" % a.environment)
    env_id = platform_env.get("env_id") or sys.exit("REFUSED: anypoint-environments.yaml has no env_id for %r" % a.environment)
    shared = sb.load_shared(platform_env, a.environment)
    eff = sb.effective_config(cfg, shared)
    label = a.instance_label or shared["instance_label"]
    base_path = (a.base_path or shared["base_path"]).strip("/")
    eff_path = sb.write_effective_config(eff, out)
    sb.guard_protected(shared, eff, instance_label=label)

    if a.mode == "check-existing":
        return pb.main(["--config", eff_path, "--artifacts", a.artifacts, "--environment", a.environment, "--platform-config", a.platform_config,
                        "--base-url", a.base_url, "--instance-label", label, "--base-path", base_path, "--check-existing"])

    rest_version = rest_version_for(a)
    group = eff.get("source_group_id") or org
    # the route's external instance name: run-scoped --source-name > bridge-config
    # source_instance_name (domain-owned, e.g. test isolation) > uif-<domain>-<env>
    standard_name = esi.source_name(eff, a.environment, eff.get("source_instance_name"))
    name = esi.source_name(eff, a.environment, a.source_name or eff.get("source_instance_name"))
    sb.guard_protected(shared, eff, instance_label=label, source_instance_name=name)
    upstream_url = a.upstream_url or esi.upstream_url_for(eff, a.environment)
    route_label = eff.get("route_label", eff["source_asset_id"])
    domain_meta = json.load(open(os.path.join(a.artifacts, "mcp-metadata.json"), encoding="utf-8-sig"))

    print("== DEPLOY DOMAIN INTO THE SHARED MCP BRIDGE (%s) == %s / %s" % (a.mode, eff["domain"], a.environment))
    print("shared bridge           : label %r  path /%s  MCP asset %s@<resolved from Exchange%s>  gateway %s  (standard: %s, /%s)" % (
        label, base_path, shared["mcp_asset_id"], " | pinned %s" % a.mcp_asset_version if a.mcp_asset_version else "",
        platform_env.get("gateway_name"), shared["instance_label"], shared["base_path"]))
    print("step 1  source instance : Exchange external %r on %s/%s@%s  endpointUri=%s  (standard name: %s)" % (
        name, group, eff["source_asset_id"], rest_version, upstream_url, standard_name))
    if shared.get("protected"):
        print("protected identities    : %s  (never resolved, published, bound or mutated - fail closed)" % json.dumps(shared["protected"]))
    print("step 2  domain route    : %s -> %s@%s  upstream %s  (%d tools from the contract)" % (
        route_label, eff["source_asset_id"], rest_version, upstream_url, len(domain_meta.get("tools") or [])))
    print("lifecycle               : Bridge absent -> CREATE | present -> identity guards -> route absent: ADD | differs: UPDATE in place | "
          "identical: NO-OP | every other domain's route preserved | never delete/recreate")
    print("effective config        : %s (domain inputs + shared identity; bridge-config.yaml unchanged)" % eff_path)
    overrides = {k: v for k, v in (("source-name", a.source_name), ("upstream-url", a.upstream_url),
                                   ("instance-label", a.instance_label), ("base-path", a.base_path)) if v}
    if overrides:
        print("RUN-SCOPED OVERRIDES (domain/platform config unchanged): %s" % json.dumps(overrides))
    if label == shared["instance_label"] and overrides.get("source-name"):
        sys.exit("REFUSED: a PoC source name with the STANDARD shared Bridge label would rebind the shared Bridge's route; "
                 "pass --instance-label too.")

    esi_args = SimpleNamespace(base_url=a.base_url, out=out)
    source_file = os.path.join(out, esi.SOURCE_INSTANCE_FILE)
    # The Bridge stage may ONLY bind the source instance captured by THIS run
    # (<out>/source-instance.json) - never a file left in the artifacts dir by
    # another deployment.
    pb_common = ["--config", eff_path, "--artifacts", a.artifacts, "--environment", a.environment,
                 "--platform-config", a.platform_config, "--base-url", a.base_url,
                 "--instance-label", label, "--base-path", base_path, "--upstream-url", upstream_url,
                 "--source-instance-file", "none"]
    ub_common = ["--config", eff_path, "--artifacts", a.artifacts, "--environment", a.environment,
                 "--platform-config", a.platform_config, "--base-url", a.base_url,
                 "--instance-label", label, "--base-path", base_path, "--upstream-url", upstream_url]

    if a.mode == "dry-run":
        print("\n-- step 1 (dry-run) --")
        print(json.dumps({"POST": "%s/exchange/api/v2/assets/%s/%s/versionGroups/<versionGroup from descriptor>/instances/external"
                          % (a.base_url, group, eff["source_asset_id"]),
                          "body": esi.build_external_instance_body(name, upstream_url), "unless": "an external instance named %r already exists with that URL" % name}, indent=2))
        print("\n-- shared MCP asset (dry-run) -- %s: merged at deploy time from the version the Bridge binds (Exchange read) + this domain's "
              "%d tool(s); version reuse | 1.0.0 | patch bump resolved from Exchange%s" % (
                  shared["mcp_asset_id"], len(domain_meta.get("tools") or []), " (pinned %s)" % a.mcp_asset_version if a.mcp_asset_version else ""))
        print("\n-- step 2 (dry-run; CREATE shape of this domain's route shown - if the shared Bridge exists, execute reconciles the route "
              "into it IN PLACE instead; connection.apiVersionId = UUID captured in step 1) --")
        rc = pb.main(pb_common + ["--source-api-instance-id", "<CAPTURED-AT-DEPLOY:source-instance-uuid (step 1)>"]
                     + (["--mcp-asset-version", a.mcp_asset_version] if a.mcp_asset_version else []) + ["--dry-run"])
        print("\nDRY-RUN: nothing sent (no token request either).")
        return rc

    # ── step 1: source instance (unchanged proven behaviour) ───────────────────
    print("\n-- step 1: source instance (%s) --" % a.mode)
    rc = esi.execute(esi_args, eff, name, upstream_url, group, eff["source_asset_id"], rest_version, a.mode)
    if rc:
        return rc

    # ── step 2: inspect the shared Bridge (read-only) ───────────────────────────
    print("\n-- step 2: shared bridge (%s) --" % a.mode)
    cid, sec = pb._live_gates()
    token = pb._token_for({"base_url": a.base_url}, cid, sec)
    env_apis = pb._list_env_apis(a.base_url, token, org, env_id)
    hits = [x for x in env_apis if x.get("instanceLabel") == label]  # EXACT label match - never a prefix
    protected_labels = (shared.get("protected") or {}).get("instance_labels", [])
    seen_protected = sorted({x.get("instanceLabel") for x in env_apis if x.get("instanceLabel") in protected_labels})
    if seen_protected:
        print("bridge lookup: protected instance(s) present in the environment and IGNORED (never touched): %s" % ", ".join(seen_protected))
    if len(hits) > 1:
        sys.exit("REFUSED: %d instances carry label %r - ambiguous shared Bridge identity; nothing changed." % (len(hits), label))
    existing = hits[0] if hits else None
    if existing is not None and existing.get("instanceLabel") != label:
        sys.exit("REFUSED: resolved instance %s carries label %r, not %r - nothing changed." % (existing.get("id"), existing.get("instanceLabel"), label))
    if existing is not None and existing.get("instanceLabel") in protected_labels:
        sys.exit("REFUSED (protected identity): instance %s (%r) is a protected Bridge - nothing changed." % (existing.get("id"), existing.get("instanceLabel")))
    print("bridge lookup: %s" % ("none labeled %r -> CREATE path (this domain's route is the first)" % label if not existing
                                  else "instance %s labeled %r -> reconcile path (identity guards next)" % (existing["id"], label)))

    expected = {"label": label, "base_path": base_path, "mcp_asset_id": shared["mcp_asset_id"], "env_id": env_id,
                "source_asset_id": eff["source_asset_id"], "route_label": route_label, "source_instance_label": name}
    live = None
    bound_version, replaced_names, protected_names, existing_meta = None, [], None, None
    if existing is not None:
        live = ub._read(a.base_url, token, org, env_id, existing["id"])
        ok, lines = ub.identity_guards(*live, expected)
        for line in lines:
            print("guard: " + line)
        if not ok:
            sys.exit("REFUSED (identity guard): instance %s is not the expected shared UIF Bridge, or its route %s is not this "
                     "domain's - nothing changed (no Exchange write either)." % (existing["id"], route_label))
        view = ub.routes_view(*live)
        router_routes = ub.current_state(*live)["router_routes"]
        bound_version = str(live[0].get("assetVersion"))
        # the Bridge's routing is the ownership record of tool names: mine = my route's
        # outbound policy + router route; protected = every OTHER route's
        replaced_names = sorted(set(view[route_label]["tool_names"]) | set(router_routes.get(route_label) or ())) if route_label in view else []
        protected_names = sorted({n for l, v in view.items() if l != route_label for n in v["tool_names"]}
                                 | {n for l, names in router_routes.items() if l != route_label for n in names})
        print("bridge state: binds %s@%s; routes: %s" % (live[0].get("assetId"), bound_version,
              ", ".join("%s (%d tools)" % (l, len(v["tool_names"])) for l, v in view.items())))
        existing_meta = sb.parse_metadata(read_bound_metadata(a.base_url, token, group, shared["mcp_asset_id"], bound_version))
        print("bound metadata: %d tool(s), connections %s" % (len(existing_meta.get("tools") or []), sb.connection_versions(existing_meta)))

    # ── step 3: the shared MCP asset (merge + Exchange-resolved version) ────────
    merged = sb.merge_metadata(existing_meta, domain_meta, replaced_names, protected_names)
    merged_bytes = sb.render_metadata(merged)
    if a.mcp_asset_version:
        release = {"mcp_asset_id": shared["mcp_asset_id"], "group_id": group, "version": a.mcp_asset_version, "decision": "pinned",
                   "bound": bound_version, "latest": None, "compared": None, "reason": "--mcp-asset-version pinned by the operator"}
    else:
        reader = rv.ExchangeReader(a.base_url, token)
        print("shared MCP asset reads (targeted to %s only):" % shared["mcp_asset_id"])
        release = sb.resolve_shared_version(reader, group, shared["mcp_asset_id"], bound_version, merged_bytes)
        release["exchange_reads"] = list(reader.trace)
    write_shared_outputs(out, merged_bytes, release)
    print("shared MCP asset: %s@%s (decision %s: %s); merged content = %d tool(s) %s, connections %s -> %s" % (
        shared["mcp_asset_id"], release["version"], release["decision"], release["reason"], len(merged["tools"]),
        sb.tool_names(merged), sb.connection_versions(merged), os.path.join(out, SHARED_METADATA_FILE)))
    mcp_version = release["version"]
    needs_publish = release["decision"] != "reuse"

    # ── step 4: the Bridge ──────────────────────────────────────────────────────
    print("\n-- step 4: bridge (%s) --" % a.mode)
    if existing is None:
        # ── CREATE (the proven path, verbatim, with the shared identity) ─────
        if a.mode == "preflight":
            print("connection at execute: {label: %r, assetId: %r, version: %r, assetType: rest, productAPIVersion: <descriptor versionGroup>, "
                  "apiVersionId: <UUID from step 1>} - the Bridge preflight below runs without it (gateway, TLS, markers, collision scan)" % (
                      name, eff["source_asset_id"], rest_version))
            rc = pb.main(pb_common + ["--mcp-asset-version", mcp_version, "--preflight"])
            print("\nPREFLIGHT ONLY (end to end): nothing was created, published or modified. Decision: CREATE. Execute would: (1) reuse-or-create "
                  "the external instance %r, (2) %s %s@%s, (3) create the shared Bridge %r at /%s with route %s bound to it, (4) verify the read-backs." % (
                      name, "publish" if needs_publish else "reuse", shared["mcp_asset_id"], mcp_version, label, base_path, route_label))
            return rc
        if not os.path.exists(source_file):
            sys.exit("FAIL CLOSED: step 1 reported success but %s is missing - not creating a Bridge without a verified source instance." % source_file)
        captured = json.load(open(source_file, encoding="utf-8"))
        if captured.get("label") != name:
            sys.exit("FAIL CLOSED: %s holds source instance %r, not %r - refusing to bind the wrong upstream." % (source_file, captured.get("label"), name))
        if needs_publish:
            rc = publish_shared_asset(a, org, group, shared["mcp_asset_id"], mcp_version, merged_bytes)
            if rc:
                return rc
        # --require-connection: provision_bridge refuses BEFORE any network call if the
        # rendered payload lacks upstream.connection or carries a marker id, so the
        # create can never be connection-less; the read-back then requires the
        # connection persisted exactly as sent.
        return pb.main(pb_common[:-2] + ["--source-instance-file", source_file, "--mcp-asset-version", mcp_version,
                                         "--require-connection", "--execute"])

    # ── ADD / UPDATE / NO-OP (update_bridge: the in-place reconciliation) ───────
    if a.mode == "preflight":
        pre_file, data = preflight_source_capture(a, eff, name, upstream_url, group, eff["source_asset_id"], rest_version,
                                                  os.path.join(out, "preflight"))
        if data is None:
            sys.exit("REFUSED: shared Bridge %s exists but no external instance %r with URL %s exists for %s@%s - execute would create the "
                     "upstream first; run --execute (or resolve the upstream) to see the route decision." % (
                         existing["id"], name, upstream_url, eff["source_asset_id"], rest_version))
        rc = ub.main(ub_common + ["--mcp-asset-version", mcp_version, "--source-instance-file", pre_file, "--instance-id", str(existing["id"]), "--preflight"])
        print("\nPREFLIGHT ONLY (end to end): nothing was created, published or modified. Shared Bridge %s would get route %s ADDED or UPDATED "
              "IN PLACE, or be left as NO-OP (see decision above); every other route is preserved; the Bridge is never deleted or recreated."
              % (existing["id"], route_label))
        return rc
    if not os.path.exists(source_file):
        sys.exit("FAIL CLOSED: step 1 reported success but %s is missing - not updating a Bridge without a verified source instance." % source_file)
    captured = json.load(open(source_file, encoding="utf-8"))
    if captured.get("label") != name:
        sys.exit("FAIL CLOSED: %s holds source instance %r, not %r - refusing to bind the wrong upstream." % (source_file, captured.get("label"), name))
    # decide on the SAME target update_bridge would send; publish the shared asset only if the Bridge changes
    _, _, payload, _ = ub.prepare(eff_path, a.artifacts, a.environment, mcp_version, source_file, label, base_path, upstream_url,
                                  a.platform_config, a.base_url)
    action, diff = ub.decide(ub.current_state(*live), ub.target_state(payload))
    if action == "noop":
        print("NO-OP: the shared Bridge already binds %s@%s and serves route %s with identical upstream, connection and mappings - "
              "nothing sent (no Exchange write either)." % (shared["mcp_asset_id"], mcp_version, route_label))
        return 0
    print("decision: %s (differs in: %s)" % (action.upper(), ", ".join(diff)))
    if needs_publish:
        rc = publish_shared_asset(a, org, group, shared["mcp_asset_id"], mcp_version, merged_bytes)
        if rc:
            return rc
    # update_bridge: guards -> decision -> concurrency re-read -> PATCH instance + policy PATCHes -> read-back.
    # Any failure exits immediately; this orchestrator never falls back to delete/recreate.
    return ub.main(ub_common + ["--mcp-asset-version", mcp_version, "--source-instance-file", source_file, "--instance-id", str(existing["id"]), "--execute"])


if __name__ == "__main__":
    sys.exit(main())
