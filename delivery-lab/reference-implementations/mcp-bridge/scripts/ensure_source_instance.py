"""Ensure the SOURCE API instance the MCP Bridge association needs - the
UI-equivalent way: an Exchange EXTERNAL (unmanaged) instance of the REST asset.

WHY (decided 2026-09-11): the Anypoint API Manager UI populates a Bridge's
APIs table and 'MCP tools & mapping' panel from `upstream.connection`, whose
`apiVersionId` must reference an instance of the source REST asset. The MCP
Bridge wizard resolves that id from the Exchange asset's own `instances[]`
and, when none fits, creates an Exchange external instance itself
("Create new unmanaged upstream" -> POST .../instances/external). It never
creates an API Manager instance. UIF does the same: no API Manager object,
no gateway, no listener - just a documented Exchange record.

SURFACE: documented Exchange API v2
  GET  /exchange/api/v2/assets/{group}/{asset}/{version}
         -> descriptor: versionGroup (the product API version, from Exchange,
            never assumed) + instances[] (type managed|external|mocked)
  POST /exchange/api/v2/assets/{group}/{asset}/versionGroups/{vg}/instances/external
         body {name, endpointUri, isPublic:false} -> {id: <UUID string>, ...}
Live-verified 2026-09-11: 55 external instances in the org, ids are UUID
strings, productAPIVersion = the version group, environmentId null.

DETERMINISM / IDEMPOTENCE: name = uif-<domain>-<environment> (one per domain
per UIF environment). An existing external instance with that exact name AND
the same endpointUri is REUSED (no write). Same name + different URL -> FAIL
CLOSED (the domain's upstream changed: an explicit decision, never a silent
overwrite). More than one instance with the name -> FAIL CLOSED (ambiguous).
The captured id is passed UNCHANGED (string) to connection.apiVersionId.

SAFETY: --dry-run default (prints the exact request; sends nothing, not even
a token request). --execute requires MCP_LIVE_ENABLED=true + Connected App
credentials; at most ONE POST; a rejection prints the platform body (secrets
redacted) and saves it next to the artifacts. Output: `source_api_instance_id=
<uuid>` on stdout and <out>/source-instance.json for provision_bridge.py.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from provision_bridge import DEFAULT_BASE_URL, _get, _live_gates, _token_for, describe_http_error, load_yaml

DEFAULT_NAME = "uif-%s-%s"  # uif-<domain>-<environment>
SOURCE_INSTANCE_FILE = "source-instance.json"


def source_name(cfg, environment, override=None):
    return override or DEFAULT_NAME % (cfg["domain"], environment)


def upstream_url_for(cfg, environment):
    url = ((cfg.get("environments") or {}).get(environment) or {}).get("upstream_url")
    if not url:
        sys.exit("REFUSED: bridge-config environments.%s.upstream_url is unset - the external instance's endpointUri "
                 "is the domain's implementation URL and is never invented." % environment)
    return url


def build_external_instance_body(name, upstream_url):
    """Documented Exchange v2 external-instance body; private by design."""
    return {"name": name, "endpointUri": upstream_url, "isPublic": False}


def resolve_external_instance(descriptor, name, upstream_url):
    """Pure decision over the Exchange descriptor's instances[]:
    ('reuse', inst) | ('create', None); SystemExit on ambiguity or URL drift."""
    matches = [i for i in (descriptor.get("instances") or []) if i.get("type") == "external" and i.get("name") == name]
    if len(matches) > 1:
        sys.exit("REFUSED: %d external instances named %r on %s@%s - ambiguous; resolve manually in Exchange." % (
            len(matches), name, descriptor.get("assetId"), descriptor.get("version")))
    if matches:
        inst = matches[0]
        if (inst.get("endpointUri") or "").rstrip("/") != upstream_url.rstrip("/"):
            sys.exit("FAIL CLOSED: external instance %r (id %s) already exists with endpointUri %r but bridge-config "
                     "upstream_url is %r - the upstream changed; decide explicitly (retire/rename the old instance) "
                     "instead of silently repointing." % (name, inst.get("id"), inst.get("endpointUri"), upstream_url))
        return "reuse", inst
    return "create", None


def version_group_of(descriptor):
    vg = descriptor.get("versionGroup")
    if not vg:
        sys.exit("REFUSED: Exchange descriptor for %s@%s carries no versionGroup - cannot address the instances endpoint." % (
            descriptor.get("assetId"), descriptor.get("version")))
    return str(vg)


def capture(out_dir, inst, descriptor, name):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, SOURCE_INSTANCE_FILE)
    data = {
        "source_api_instance_id": str(inst["id"]),  # UUID string, passed unchanged to connection.apiVersionId
        "kind": "exchange-external",
        "label": inst.get("name") or name,
        "endpointUri": inst.get("endpointUri"),
        "asset": {"groupId": descriptor.get("groupId"), "assetId": descriptor.get("assetId"), "version": descriptor.get("version")},
        "productAPIVersion": version_group_of(descriptor),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path, data


def execute(args, cfg, name, upstream_url, group, asset_id, version, mode="execute"):
    cid, sec = _live_gates()
    token = _token_for({"base_url": args.base_url}, cid, sec)
    desc_path = "/exchange/api/v2/assets/%s/%s/%s" % (group, asset_id, version)
    try:
        descriptor = _get(args.base_url, token, desc_path)
    except urllib.error.HTTPError as e:
        sys.exit("REFUSED: Exchange asset %s/%s@%s not readable (HTTP %s) - publish the REST asset first." % (group, asset_id, version, e.code))
    vg = version_group_of(descriptor)
    existing = descriptor.get("instances") or []
    print("exchange asset %s@%s: versionGroup %s; instances: %s" % (
        asset_id, version, vg, ", ".join("%s:%s(%s)" % (i.get("type"), i.get("name") or i.get("id"), i.get("id")) for i in existing) or "none"))
    action, inst = resolve_external_instance(descriptor, name, upstream_url)
    if mode == "preflight":
        if action == "reuse":
            print("PREFLIGHT ONLY: execute would REUSE external instance %r (id %s) - no write would occur." % (name, inst["id"]))
        else:
            same_name_any_type = [i for i in existing if i.get("name") == name]
            print("PREFLIGHT ONLY: no external instance named %r exists (instances with that name of any type: %d); execute would "
                  "CREATE exactly ONE external instance:" % (name, len(same_name_any_type)))
            print("  POST %s/exchange/api/v2/assets/%s/%s/versionGroups/%s/instances/external" % (args.base_url, group, asset_id, vg))
            print("  " + json.dumps(build_external_instance_body(name, upstream_url)))
            print("  -> productAPIVersion %s (Exchange versionGroup), isPublic false" % vg)
        print("PREFLIGHT ONLY: nothing was created or modified.")
        return 0
    if action == "reuse":
        print("REUSE: external instance %r already exists (id %s, endpointUri %s) - no write." % (name, inst["id"], inst.get("endpointUri")))
    else:
        url = "%s/exchange/api/v2/assets/%s/%s/versionGroups/%s/instances/external" % (args.base_url, group, asset_id, vg)
        body = build_external_instance_body(name, upstream_url)
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                created = json.load(r)
        except urllib.error.HTTPError as e:
            report = describe_http_error(e, token, sec)
            os.makedirs(args.out, exist_ok=True)
            path = os.path.join(args.out, "source-instance-rejected.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"status": report["status"], "response_headers": report["headers"],
                           "response_body": report["body"], "body": body, "url": url}, f, indent=2)
            sys.exit("EXTERNAL INSTANCE CREATE REJECTED - HTTP %s\nresponse body:\n%s\nbody + response saved: %s"
                     % (report["status"], report["body"], path))
        if not created.get("id"):
            sys.exit("FAIL CLOSED: Exchange returned no id for the external instance: %s" % json.dumps(created)[:500])
        print("CREATED external instance %r id=%s" % (name, created["id"]))
        # read-back: the wizard's own source list (descriptor.instances) must now carry it
        descriptor = _get(args.base_url, token, desc_path)
        inst = next((i for i in (descriptor.get("instances") or []) if str(i.get("id")) == str(created["id"])), None)
        if not inst:
            sys.exit("FAIL CLOSED: created instance %s is not listed in the Exchange descriptor instances[] - inspect before binding." % created["id"])
    checks = {
        "type external": inst.get("type") == "external",
        "name %s" % name: inst.get("name") == name,
        "endpointUri %s" % upstream_url: (inst.get("endpointUri") or "").rstrip("/") == upstream_url.rstrip("/"),
        "private (isPublic false)": inst.get("isPublic") in (False, None),
        "productAPIVersion %s (from Exchange)" % vg: str(inst.get("productAPIVersion") or inst.get("versionGroup") or vg) == vg,
    }
    for k, ok in checks.items():
        print("read-back: %s: %s" % (k, "PASS" if ok else "FAIL"))
    if not all(checks.values()):
        sys.exit("external instance verification FAILED - nothing deleted or modified; inspect %s in Exchange." % inst.get("id"))
    path, data = capture(args.out, inst, descriptor, name)
    print("source_api_instance_id=%s" % data["source_api_instance_id"])
    print("captured: %s (provision_bridge.py reads it via --source-instance-file, or pass --source-api-instance-id %s)" % (
        path, data["source_api_instance_id"]))
    return 0


def external_delete_guards(descriptor, instance_id, name, upstream_url):
    """Pure identity guards before deleting ONE external instance. Every check
    must PASS; a managed (API Manager) instance can never pass (type guard)."""
    inst = next((i for i in (descriptor.get("instances") or []) if str(i.get("id")) == str(instance_id)), None)
    vg = str(descriptor.get("versionGroup") or "")
    checks = {
        "instance %s listed on %s@%s" % (instance_id, descriptor.get("assetId"), descriptor.get("version")): inst is not None,
        "type external (never a managed/API Manager instance)": bool(inst) and inst.get("type") == "external",
        "id is a UUID string (not an API Manager numeric id)": isinstance(instance_id, str) and len(instance_id) == 36 and instance_id.count("-") == 4 and not instance_id.isdigit(),
        "name %s" % name: bool(inst) and inst.get("name") == name,
        "endpointUri %s" % upstream_url: bool(inst) and (inst.get("endpointUri") or "").rstrip("/") == upstream_url.rstrip("/"),
        "productAPIVersion %s (descriptor versionGroup)" % vg: bool(inst) and str(inst.get("productAPIVersion") or inst.get("versionGroup") or "") == vg and bool(vg),
        "private (isPublic false)": bool(inst) and inst.get("isPublic") in (False, None),
    }
    return all(checks.values()), ["%s: %s" % (k, "PASS" if v else "FAIL") for k, v in checks.items()], inst


def delete_external_instance(args, cfg, name, upstream_url, group, asset_id, version, instance_id, confirm_id, mode):
    """Guarded delete of ONE Exchange external instance:
    DELETE /exchange/api/v2/assets/{g}/{a}/versionGroups/{vg}/instances/external/{id}
    (Exchange API v2; the route Exchange's own client library uses -
    `removeExternalInstance`). Deletes the instance record only: the REST asset,
    its versions, the MCP asset and every API Manager object are untouched.
    dry-run: print guards, no network; preflight: read + guards, no delete;
    execute: guards -> ONE DELETE -> descriptor read-back must no longer list it."""
    print("== GUARDED EXTERNAL INSTANCE DELETE (%s) == %s on %s/%s@%s" % (mode, instance_id, group, asset_id, version))
    print("expected identity: type=external name=%s endpointUri=%s isPublic=false productAPIVersion=<descriptor versionGroup>" % (name, upstream_url))
    if str(confirm_id) != str(instance_id):
        sys.exit("REFUSED: --confirm-delete must repeat the exact instance id (%s != %s)." % (confirm_id, instance_id))
    if str(instance_id).isdigit():
        sys.exit("REFUSED: %s looks like an API Manager instance id - this operation deletes Exchange EXTERNAL instances only." % instance_id)
    if mode == "dry-run":
        print("DRY-RUN: guards above would be evaluated against the live descriptor; nothing sent (no token request either).")
        return 0
    cid, sec = _live_gates()
    token = _token_for({"base_url": args.base_url}, cid, sec)
    desc_path = "/exchange/api/v2/assets/%s/%s/%s" % (group, asset_id, version)
    try:
        descriptor = _get(args.base_url, token, desc_path)
    except urllib.error.HTTPError as e:
        sys.exit("REFUSED: Exchange asset %s/%s@%s not readable (HTTP %s) - nothing deleted." % (group, asset_id, version, e.code))
    before = descriptor.get("instances") or []
    print("descriptor: %d instances: %s" % (len(before), ", ".join("%s:%s(%s)" % (i.get("type"), i.get("name") or "-", i.get("id")) for i in before)))
    ok, lines, inst = external_delete_guards(descriptor, str(instance_id), name, upstream_url)
    for line in lines:
        print("guard: " + line)
    if not ok:
        sys.exit("REFUSED (identity guard): %s does not match the expected external instance - nothing deleted." % instance_id)
    vg = version_group_of(descriptor)
    url = "%s/exchange/api/v2/assets/%s/%s/versionGroups/%s/instances/external/%s" % (args.base_url, group, asset_id, vg, instance_id)
    print("target: DELETE %s" % url)
    if mode == "preflight":
        print("PREFLIGHT ONLY: all guards PASS; no delete was sent.")
        return 0
    req = urllib.request.Request(url, method="DELETE", headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print("DELETE -> HTTP %s" % r.status)
    except urllib.error.HTTPError as e:
        report = describe_http_error(e, token, sec)
        sys.exit("DELETE REJECTED - HTTP %s\n%s" % (report["status"], report["body"]))
    after_desc = _get(args.base_url, token, desc_path)  # the asset itself must still exist
    after = after_desc.get("instances") or []
    still = [i for i in after if str(i.get("id")) == str(instance_id)]
    print("read-back: asset %s@%s still present: PASS; instances now %d (was %d)" % (asset_id, version, len(after), len(before)))
    if still:
        sys.exit("DELETE NOT CONFIRMED: %s is still listed - inspect in Exchange." % instance_id)
    if len(after) != len(before) - 1:
        sys.exit("DELETE NOT CONFIRMED: instance count changed by %d, expected exactly -1 - inspect in Exchange." % (len(after) - len(before)))
    print("VERIFIED: external instance %s removed; %s@%s and all other instances untouched." % (instance_id, asset_id, version))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="domain bridge-config.yaml")
    ap.add_argument("--environment", required=True)
    ap.add_argument("--asset-version", required=True, help="the REST asset version (= contract info.version)")
    ap.add_argument("--out", required=True, help="dir for source-instance.json (captured id) and rejection evidence")
    ap.add_argument("--name", default=None, help="override the deterministic uif-<domain>-<environment> name (controlled experiments only)")
    ap.add_argument("--upstream-url", default=None,
                    help="override bridge-config upstream_url for THIS RUN ONLY (controlled experiments; the domain config is untouched)")
    ap.add_argument("--platform-config",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "anypoint-environments.yaml"))
    ap.add_argument("--base-url", default=os.environ.get("ANYPOINT_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument("--delete-external-instance", metavar="UUID", default=None,
                    help="guarded delete of ONE Exchange external instance (identity guards: type external, name, endpointUri, "
                         "productAPIVersion, private); combine with --dry-run | --preflight | --execute and --confirm-delete <same id>")
    ap.add_argument("--confirm-delete", metavar="UUID", default=None, help="must repeat --delete-external-instance's id")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", default="dry-run")
    g.add_argument("--preflight", dest="mode", action="store_const", const="preflight",
                   help="authenticated READ-ONLY: resolve reuse/create (or evaluate delete guards) and stop - no POST/DELETE")
    g.add_argument("--execute", dest="mode", action="store_const", const="execute",
                   help="read the descriptor, reuse or create (at most ONE POST) - or, with --delete-external-instance, ONE DELETE; "
                        "requires MCP_LIVE_ENABLED=true + credentials")
    a = ap.parse_args(argv)
    a.dry_run = a.mode == "dry-run"

    cfg = load_yaml(a.config)
    platform = (load_yaml(a.platform_config) or {}).get(a.environment) or {}
    org = platform.get("org_id")
    if not org:
        sys.exit("REFUSED: anypoint-environments.yaml has no org_id for %r" % a.environment)
    group = cfg.get("source_group_id") or org
    asset_id, version = cfg["source_asset_id"], str(a.asset_version)
    name = source_name(cfg, a.environment, a.name)
    upstream_url = a.upstream_url or upstream_url_for(cfg, a.environment)
    if a.delete_external_instance:
        return delete_external_instance(a, cfg, name, upstream_url, group, asset_id, version,
                                        a.delete_external_instance, a.confirm_delete, a.mode)
    body = build_external_instance_body(name, upstream_url)

    print("== ENSURE SOURCE INSTANCE (Exchange EXTERNAL instance - documented Exchange API v2; no API Manager object, no gateway) ==")
    if a.name or a.upstream_url:
        print("RUN-SCOPED OVERRIDE: %s- the domain standard (name %s, bridge-config upstream_url) is unchanged" % (
            ("name=%r " % a.name if a.name else "") + ("upstream_url=%r " % a.upstream_url if a.upstream_url else ""),
            source_name(cfg, a.environment)))
    print("asset: %s/%s@%s  |  deterministic name: %s" % (group, asset_id, version, name))
    print("lookup: GET %s/exchange/api/v2/assets/%s/%s/%s -> instances[] (type external, name %r) -> reuse if URL identical, "
          "FAIL CLOSED if URL differs or duplicated" % (a.base_url, group, asset_id, version, name))
    print("create (only if absent): POST %s/exchange/api/v2/assets/%s/%s/versionGroups/<versionGroup from descriptor>/instances/external"
          % (a.base_url, group, asset_id))
    print(json.dumps(body, indent=2))
    if a.dry_run:
        print("\nDRY-RUN: nothing sent (no token request either).")
        return 0
    return execute(a, cfg, name, upstream_url, group, asset_id, version, a.mode)


if __name__ == "__main__":
    sys.exit(main())
