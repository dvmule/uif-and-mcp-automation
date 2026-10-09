"""In-place reconciliation of ONE domain into the SHARED MCP Bridge, mirroring the
Anypoint UI's "Apply changes" (traced from the API Manager UI bundle, 2026-09-11;
generalized from the single-upstream Bridge to the shared N-upstream Bridge on
2026-09-29; ADD path corrected 2026-09-30 after the first live ADD attempt).

Kept as a SEPARATE module so the proven CREATE / REUSE paths in
provision_bridge.py and ensure_source_instance.py are not touched: this file
only IMPORTS their helpers (read-only use).

WHAT THE UI DOES ON APPLY CHANGES (and what this module reproduces):
  1. reads the instance (v1), its upstreams (v1) and policies (v1);
  2. PATCH /apimanager/xapi/v1/organizations/{org}/environments/{env}/apis/{id}
        ?checkAutomatedPolicies=true   - SAME instance id; body (isPatch shape):
        metadata.generatedBy, spec{assetId,groupId,version=<shared MCP version>},
        assetVersion=<shared MCP version>, endpoint (no uri; proxyUri + inbound TLS
        preserved), deployment (preserved), upstreams[{id=<existing>, uri,
        connection}] for EVERY upstream of the Bridge, routing[{label,
        upstreams[{id,weight}], rules X-UPSTREAM-NAME}] for EVERY route,
        policies inline [mcp-support(1), mcp-schema-validation(2),
        mcp-transcoding-router(3) with the MERGED routes];
  3. reconciles the attached policies via the DOCUMENTED v1 policies API:
        PATCH .../apis/{id}/policies/{routerPolicyId}      {configurationData:{transcodingPath, routes}}
        PATCH .../apis/{id}/policies/{transcodingPolicyId} {configurationData:{tools}}   (THIS domain's upstream only)
  (the UI also publishes a new MCP asset version first - in UIF that is
   deploy_bridge.py's shared-asset stage: merged metadata, Exchange-resolved version.)
  No delete, no recreate, no redeploy call: label, base path, listener, TLS and
  every OTHER domain's upstream / connection / tools / policy ids stay exactly as
  they are - and are VERIFIED unchanged on read-back.

RECONCILIATION (one domain in, everything else preserved):
  current  = per-route view of the live Bridge (routes paired to upstreams by id,
             outbound policy per upstream, router routes by upstreamName)
  target   = this domain's route from the CREATE builder (provision_bridge.build_payload)
  decide   -> route absent          -> ADD    (see below)
              route present, differs -> UPDATE (its uri / connection / tools / router
                                               route; the MCP asset binding)
              identical              -> NO-OP  (nothing sent)
  A route is never removed because its domain is absent from this run's input.

ADD - adding a domain's route to an EXISTING Bridge is FOUR operations, not one
(live-verified 2026-09-30, Bridge 21206639: the xapi PATCH's routing schema is a
strict REFERENCE schema - `routing[].upstreams[]` items require `id` and reject
every other property (`uri`, `connection`, `policies` -> 400
RoutingValidationError "unevaluated properties"); the CREATE-style inline
upstream object is accepted ONLY on the create POST). Upstreams are a separate
sub-resource of the instance, exactly as the documented Anypoint CLI models them
(`api-mgr:upstream:add <apiInstanceId> <uri> --label`, `api-mgr:upstream:list`,
`api-mgr:api:edit --routing` = "routes ... with upstream references and weights"):
  A1. POST  /apimanager/api/v1/.../apis/{id}/upstreams   {label, uri}      -> {id}
        documented API Manager v1 (the collection whose GET this tooling already
        reads; the CLI's upstream:add). An unrouted upstream already carrying this
        domain's label (an earlier interrupted ADD) is REUSED, never duplicated.
  A2. PATCH xapi .../apis/{id}  (the proven isPatch shape) with the new upstream in
        `upstreams[]` BY ID (uri + connection - the same mechanism that rebinds an
        existing upstream's connection, live-proven) and the new route in
        `routing[]` as `{label, upstreams:[{id, weight}], rules}`; every existing
        route/upstream verbatim; inline router with the merged routes.
  A3. POST  xapi .../apis/{id}/policies/outbound-policies
        {configurationData:{tools}, groupId, assetId, assetVersion, upstreamIds:[id]}
        - the outbound mcp-transcoding policy bound to the new upstream (the
        surface the official Terraform provider uses for outbound policies;
        live-proven in the 2026-09 PoC on Bridges 21162919 and 21164592).
  A4. PATCH v1 .../apis/{id}/policies/{routerPolicyId}  (merged routes, as for UPDATE)
  then the read-back verifies the new route, its upstream id, connection, tools,
  outbound policy and router route - and every other route unchanged.

CONCURRENCY (second line; the first is the pipeline's per-environment
concurrency group around the Bridge job): the Bridge state is re-read right
before the first write and compared with the state the decision was made on;
any difference -> REFUSE (another deployment changed the shared Bridge in
between) - nothing is written, the run is simply repeated.

SAFETY: --dry-run default (renders the bodies from the current artifacts; no
network). --preflight: authenticated READS + guards + decision + the exact
requests execute would send, no write. --execute: the writes above + read-back;
requires MCP_LIVE_ENABLED=true + credentials. The target connection MUST come
from --source-instance-file (never connection-less). A rejected write stops the
run at once (no later write, never a delete or a recreate); the saved evidence
names the step, so a re-run can reconcile from the partial state.
"""
import argparse
import copy
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

import provision_bridge as pb

MARKERS = ("<UNRESOLVED:", "<CAPTURED-AT-DEPLOY:", "<DISCOVERED-AT-DEPLOY:")
ROUTE_UPSTREAM_REF_KEYS = {"id", "weight"}  # the xapi PATCH routing schema: a REFERENCE, nothing else (live 400 otherwise)


# ── pure pieces ────────────────────────────────────────────────────────────────

def _tools_key(tools):
    return sorted((t.get("name"), (t.get("method") or "").upper(), t.get("path")) for t in tools or [])


def _routes_key(routes):
    return sorted((r.get("upstreamName"), tuple(sorted(r.get("tools") or []))) for r in routes or [])


def _pid(p):
    return p.get("policyId") or p.get("id")


def routes_view(inst, ups, pol):
    """Per-route view of the live Bridge: {route label: {upstream_id, uri, upstream_label,
    connection, tools (key), tool_names, transcoding_policy_id, rules}}. Routes are paired
    to upstreams by id (inst.routing[].upstreams[].id == upstreams[].id); a single-route
    Bridge whose routing read omits the ids pairs with its only upstream."""
    plist = pb._policies_list(pol)
    upstreams = {str(u.get("id")): u for u in (ups.get("upstreams") or [])}
    transcoding = {str(p.get("upstreamId")): p for p in pb._policy_by_template(plist, "mcp-transcoding")}
    routing = inst.get("routing") or []
    view = {}
    for r in routing:
        ids = [str(u.get("id")) for u in (r.get("upstreams") or []) if isinstance(u, dict) and u.get("id") is not None]
        if not ids and len(routing) == 1 and len(upstreams) == 1:
            ids = list(upstreams)
        uid = ids[0] if len(ids) == 1 else None
        up = upstreams.get(uid) or {}
        tp = transcoding.get(uid) or {}
        tools = (tp.get("configuration") or {}).get("tools") or []
        view[r.get("label")] = {
            "label": r.get("label"),
            "rules": copy.deepcopy(r.get("rules") or {}),
            "upstream_ids": ids,
            "upstream_id": uid,
            "uri": up.get("uri"),
            "upstream_label": up.get("label"),
            "connection": copy.deepcopy(up.get("connection") or {}),
            "tools": _tools_key(tools),
            "tool_names": [t.get("name") for t in tools],
            "transcoding_policy_id": _pid(tp) if tp else None,
        }
    return view


def unrouted_upstreams(inst, ups):
    """Upstreams no route references (an earlier ADD interrupted between A1 and A2)."""
    routed = {str(u.get("id")) for r in (inst.get("routing") or []) for u in (r.get("upstreams") or []) if isinstance(u, dict)}
    return [u for u in (ups.get("upstreams") or []) if str(u.get("id")) not in routed]


def _is_own_orphan(u, expected):
    """An unrouted upstream is THIS domain's leftover only when it carries this domain's
    source-instance label (A1 sets it) or a connection to this domain's REST asset."""
    return (u.get("label") and u.get("label") == expected.get("source_instance_label")) or \
        (u.get("connection") or {}).get("assetId") == expected["source_asset_id"]


def identity_guards(inst, ups, pol, expected):
    """expected = {label, base_path, mcp_asset_id, env_id, source_asset_id, route_label,
    source_instance_label}. Identity of the SHARED Bridge + structural sanity of every
    route + this domain's route (if present) must belong to this domain's REST asset."""
    plist = pb._policies_list(pol)
    upstreams = ups.get("upstreams") or []
    upstream_ids = sorted(str(u.get("id")) for u in upstreams)
    routing = inst.get("routing") or []
    proxy = ((inst.get("endpoint") or {}).get("proxyUri")) or ""
    router = pb._policy_by_template(plist, "mcp-transcoding-router")
    transcoding = pb._policy_by_template(plist, "mcp-transcoding")
    view = routes_view(inst, ups, pol)
    mine = view.get(expected["route_label"])
    orphans = unrouted_upstreams(inst, ups)
    routed_ids = sorted(str(v["upstream_id"]) for v in view.values() if v["upstream_id"] is not None)
    bound_ids = sorted(str(p.get("upstreamId")) for p in transcoding)
    checks = {
        "instanceLabel %s" % expected["label"]: inst.get("instanceLabel") == expected["label"],
        "environment %s" % expected["env_id"]: inst.get("environmentId") == expected["env_id"],
        "generatedBy mcp_bridge": (inst.get("metadata") or {}).get("generatedBy") == "mcp_bridge",
        "endpoint.type mcp": (inst.get("endpoint") or {}).get("type") == "mcp",
        "proxyUri path /%s" % expected["base_path"]: pb._path_component(proxy) == "/" + expected["base_path"],
        "bound MCP asset %s" % expected["mcp_asset_id"]: inst.get("assetId") == expected["mcp_asset_id"],
        "deployment present": bool(inst.get("deployment")),
        "at least one route with an upstream": bool(routing) and bool(upstreams),
        "route labels unique and named": len(view) == len(routing) and None not in view,
        "every route pairs with exactly one upstream": bool(view) and all(
            v["upstream_id"] is not None and v["upstream_id"] in upstream_ids for v in view.values()) and len(routed_ids) == len(set(routed_ids)),
        "every unrouted upstream is this domain's leftover of an interrupted ADD (never another domain's)":
            all(_is_own_orphan(u, expected) for u in orphans),
        "every routed upstream carries a connection (a shared Bridge is never connection-less)":
            all(u.get("connection") for u in upstreams if str(u.get("id")) in routed_ids),
        "exactly one mcp-transcoding-router policy": len(router) == 1,
        "one mcp-transcoding policy per routed upstream, each bound to its upstream": all(i in bound_ids for i in routed_ids)
            and all(i in upstream_ids for i in bound_ids) and len(bound_ids) == len(set(bound_ids)),
        "policy ids readable": all(_pid(p) for p in router + transcoding),
    }
    if mine is not None:
        checks["route %s -> REST asset %s (never another domain's asset)" % (expected["route_label"], expected["source_asset_id"])] = \
            mine["connection"].get("assetId") == expected["source_asset_id"]
    else:
        checks["route %s absent (would be ADDED) and no other route serves REST asset %s" % (expected["route_label"], expected["source_asset_id"])] = \
            not any(v["connection"].get("assetId") == expected["source_asset_id"] for v in view.values())
    return all(checks.values()), ["%s: %s" % (k, "PASS" if v else "FAIL") for k, v in checks.items()]


def current_state(inst, ups, pol):
    plist = pb._policies_list(pol)
    router = pb._policy_by_template(plist, "mcp-transcoding-router")[0]
    cfg = router.get("configuration") or {}
    return {
        "mcp_version": str(inst.get("assetVersion")),
        "transcoding_path": cfg.get("transcodingPath"),
        "router_policy_id": _pid(router),
        "router_config": copy.deepcopy(cfg),
        "router_routes": {r.get("upstreamName"): sorted(r.get("tools") or []) for r in (cfg.get("routes") or [])},
        "routes": routes_view(inst, ups, pol),
        "unrouted": [{"id": str(u.get("id")), "label": u.get("label"), "uri": u.get("uri"),
                      "connection": copy.deepcopy(u.get("connection") or {})} for u in unrouted_upstreams(inst, ups)],
    }


def target_state(payload):
    """This domain's route as the CREATE builder renders it (ONE route in the payload)."""
    route = payload["routing"][0]
    up = route["upstreams"][0]
    if not up.get("connection"):
        sys.exit("REFUSED: the target route carries no upstream.connection - a shared Bridge is never connection-less; "
                 "capture the source instance first (ensure_source_instance.py).")
    router = next(p for p in payload["policies"] if p["assetId"] == "mcp-transcoding-router")["configurationData"]
    tools = up["policies"][0]["configurationData"]["tools"]
    return {
        "mcp_version": str(payload["spec"]["version"]),
        "route_label": route["label"],
        "rules": copy.deepcopy(route["rules"]),
        "uri": up["uri"],
        "connection": copy.deepcopy(up["connection"]),
        "tools_raw": copy.deepcopy(tools),
        "tools": _tools_key(tools),
        "route_tools": sorted(t for r in router["routes"] if r.get("upstreamName") == route["label"] for t in (r.get("tools") or [])),
        "route_tools_ordered": [t for r in router["routes"] if r.get("upstreamName") == route["label"] for t in (r.get("tools") or [])],
        "transcoding_path": router["transcodingPath"],
    }


def decide(current, target):
    """('noop' | 'update' | 'add', [what differs]). Only THIS domain's route and the
    Bridge-level binding are compared; other routes are preserved by construction."""
    label = target["route_label"]
    mine = current["routes"].get(label)
    diff = []
    if current["mcp_version"] != target["mcp_version"]:
        diff.append("mcp_version")
    if mine is None:
        return "add", ["route %s absent" % label] + diff
    if str(mine["connection"].get("version")) != str(target["connection"]["version"]):
        diff.append("connection_version")
    if str(mine["connection"].get("apiVersionId")) != str(target["connection"]["apiVersionId"]):
        diff.append("source_instance")
    if mine["uri"] != target["uri"]:
        diff.append("upstream_uri")
    if mine["tools"] != target["tools"]:
        diff.append("tools")
    if current["router_routes"].get(label) != target["route_tools"]:
        diff.append("routes")
    if current["transcoding_path"] != target["transcoding_path"]:
        diff.append("transcoding_path")
    return ("noop" if not diff else "update"), diff


def merged_router_routes(current, target):
    """The router's routes[] with THIS domain's entry replaced in place (or appended);
    every other domain's entry verbatim, in the platform's order."""
    label, out, seen = target["route_label"], [], False
    for r in current["router_config"].get("routes") or []:
        if r.get("upstreamName") == label:
            out.append({"upstreamName": label, "tools": list(target["route_tools_ordered"])})
            seen = True
        else:
            out.append(copy.deepcopy(r))
    if not seen:
        out.append({"upstreamName": label, "tools": list(target["route_tools_ordered"])})
    return out


def upstream_create_body(target):
    """A1 - documented API Manager v1 upstream create (the CLI's api-mgr:upstream:add
    <apiInstanceId> <uri> --label): the upstream's label is the selected source
    instance's name (what the UI shows in the Upstream table), its uri this route's."""
    return {"label": target["connection"]["label"], "uri": target["uri"]}


def outbound_policy_body(target):
    """A3 - the outbound mcp-transcoding policy bound to ONE upstream (upstreamIds is
    required by the platform); shape = the PoC's live-proven attach."""
    return {"configurationData": {"tools": copy.deepcopy(target["tools_raw"])},
            "groupId": pb.MULESOFT_POLICY_ORG, "assetId": "mcp-transcoding", "assetVersion": pb.POLICY_PINS["mcp-transcoding"]}


def route_upstream_ref(upstream_id):
    return {"id": upstream_id, "weight": 100}


def build_patch_body(payload, inst, current, target, new_upstream_id=None):
    """The UI's isPatch shape for the WHOLE shared Bridge: every existing upstream/route
    preserved (ids, uri, connection, label); this domain's upstream with its new uri /
    connection; when ADDING, the upstream created in A1 (by id, with uri + connection)
    and its route as a REFERENCE {id, weight} - never an inline upstream object."""
    label = target["route_label"]
    adding = label not in current["routes"]
    if adding and not new_upstream_id:
        sys.exit("REFUSED: adding route %s needs the id of an upstream created first (A1: POST .../upstreams) - the xapi routing "
                 "schema accepts upstream REFERENCES only (live 400 RoutingValidationError on inline upstreams); nothing sent." % label)
    endpoint = copy.deepcopy(payload["endpoint"])
    endpoint.pop("uri", None)
    endpoint["proxyUri"] = (inst.get("endpoint") or {}).get("proxyUri") or endpoint["proxyUri"]  # preserve listener
    existing_tls = ((inst.get("endpoint") or {}).get("tlsContexts") or {}).get("inbound")
    if existing_tls:
        endpoint["tlsContexts"] = {"inbound": {"secretGroupId": existing_tls.get("secretGroupId"),
                                               "tlsContextId": existing_tls.get("tlsContextId")}}
    dep = inst.get("deployment") or {}
    deployment = {
        "environmentId": dep.get("environmentId") or payload["deployment"]["environmentId"],
        "type": dep.get("type") or "HY",
        "expectedStatus": "deployed",
        "overwrite": False,
        "targetId": dep.get("targetId"),
        "targetName": dep.get("targetName"),
        "gatewayVersion": dep.get("gatewayVersion"),
    }
    upstreams, routing = [], []
    for r in inst.get("routing") or []:
        v = current["routes"][r.get("label")]
        entry = {"id": v["upstream_id"], "uri": v["uri"], "connection": copy.deepcopy(v["connection"])}
        if v.get("upstream_label"):
            entry["label"] = v["upstream_label"]
        if r.get("label") == label:
            entry["uri"] = target["uri"]
            entry["connection"] = copy.deepcopy(target["connection"])
        upstreams.append(entry)
        routing.append({"label": r.get("label"), "upstreams": [route_upstream_ref(v["upstream_id"])],
                        "rules": copy.deepcopy(v["rules"])})
    if adding:
        upstreams.append({"id": str(new_upstream_id), "uri": target["uri"], "connection": copy.deepcopy(target["connection"]),
                          "label": target["connection"]["label"]})
        routing.append({"label": label, "upstreams": [route_upstream_ref(str(new_upstream_id))], "rules": copy.deepcopy(target["rules"])})
    policies = copy.deepcopy(payload["policies"])
    router = next(p for p in policies if p["assetId"] == "mcp-transcoding-router")
    router_cfg = dict(current["router_config"])
    router_cfg["routes"] = merged_router_routes(current, target)
    router_cfg["transcodingPath"] = target["transcoding_path"]
    router["configurationData"] = router_cfg
    body = {
        "technology": "flexGateway",
        "instanceLabel": payload["instanceLabel"],
        "metadata": {"generatedBy": "mcp_bridge"},
        "spec": copy.deepcopy(payload["spec"]),
        "assetVersion": payload["spec"]["version"],
        "endpoint": endpoint,
        "deployment": deployment,
        "upstreams": upstreams,
        "routing": routing,
        "policies": policies,
    }
    assert_patch_routing_is_references_only(body)
    return body


def assert_patch_routing_is_references_only(body):
    """Guard against the 2026-09-30 failure class: in an xapi PATCH every routing upstream
    is {id, weight} and every top-level upstream carries an id. Pure; SystemExit otherwise."""
    for i, r in enumerate(body.get("routing") or []):
        for j, u in enumerate(r.get("upstreams") or []):
            extra = set(u) - ROUTE_UPSTREAM_REF_KEYS
            if not u.get("id") or extra:
                sys.exit("REFUSED (patch shape): routing[%d].upstreams[%d] of route %r must be an upstream REFERENCE {id, weight}; "
                         "found %s%s - the xapi PATCH rejects inline upstream objects (RoutingValidationError). Nothing sent."
                         % (i, j, r.get("label"), "no id" if not u.get("id") else "", (", extra keys %s" % sorted(extra)) if extra else ""))
    for k, u in enumerate(body.get("upstreams") or []):
        if not u.get("id"):
            sys.exit("REFUSED (patch shape): upstreams[%d] (%s) has no id - upstreams are created first (POST .../upstreams) and then "
                     "referenced; nothing sent." % (k, u.get("uri")))


def policy_patch_bodies(current, target):
    """Documented v1 policy PATCH bodies: the router keeps its other keys and gets the
    MERGED routes; THIS domain's transcoding policy gets its new tools."""
    router_cfg = dict(current["router_config"])
    router_cfg["routes"] = merged_router_routes(current, target)
    router_cfg["transcodingPath"] = target["transcoding_path"]
    return {"configurationData": router_cfg}, {"configurationData": {"tools": copy.deepcopy(target["tools_raw"])}}


def fingerprint(inst, ups, pol):
    """Stable digest of everything the decision and the PATCH bodies depend on."""
    return hashlib.sha256(json.dumps(current_state(inst, ups, pol), sort_keys=True, default=str).encode()).hexdigest()


def verify_update(inst, ups, pol, before, target):
    """Pure read-back check: this domain's route persisted as sent, the Bridge identity
    and the MCP binding right, and EVERY other route unchanged (upstream id, uri,
    connection, tools, outbound policy id, router route). No route removed. When adding,
    the route must be served by the upstream created/reused in A1."""
    after = current_state(inst, ups, pol)
    label, adding = target["route_label"], before["adding"]
    mine = after["routes"].get(label) or {}
    checks = {
        "same instance id %s" % before["id"]: str(inst.get("id")) == str(before["id"]),
        "label unchanged %s" % before["label"]: inst.get("instanceLabel") == before["label"],
        "proxyUri unchanged %s" % before["proxyUri"]: ((inst.get("endpoint") or {}).get("proxyUri")) == before["proxyUri"],
        "MCP asset bound %s" % target["mcp_version"]: after["mcp_version"] == target["mcp_version"],
        "router policy id unchanged (no policy churn)": str(after["router_policy_id"]) == str(before["router_policy_id"]),
        "transcodingPath %s" % target["transcoding_path"]: after["transcoding_path"] == target["transcoding_path"],
        "route %s present" % label: bool(mine),
        "route %s connection.version %s" % (label, target["connection"]["version"]):
            str(mine.get("connection", {}).get("version")) == str(target["connection"]["version"]),
        "route %s connection.apiVersionId %s" % (label, target["connection"]["apiVersionId"]):
            str(mine.get("connection", {}).get("apiVersionId")) == str(target["connection"]["apiVersionId"]),
        "route %s connection -> REST asset %s" % (label, target["connection"]["assetId"]):
            mine.get("connection", {}).get("assetId") == target["connection"]["assetId"],
        "route %s upstream uri %s" % (label, target["uri"]): mine.get("uri") == target["uri"],
        "route %s tools persisted as sent" % label: mine.get("tools") == target["tools"],
        "route %s outbound mcp-transcoding bound to its upstream" % label: mine.get("transcoding_policy_id") is not None,
        "route %s router route persisted as sent" % label: after["router_routes"].get(label) == target["route_tools"],
        "no route removed (%d before -> %d after)" % (len(before["routes"]), len(before["routes"]) + (1 if adding else 0)):
            len(after["routes"]) == len(before["routes"]) + (1 if adding else 0),
        "no unrouted upstream left behind": not after["unrouted"],
    }
    if adding:
        checks["route %s served by the upstream created/reused in A1 (%s)" % (label, before.get("new_upstream_id"))] = \
            str(mine.get("upstream_id")) == str(before.get("new_upstream_id"))
    else:
        o = before["routes"][label]
        checks["route %s upstream id unchanged" % label] = mine.get("upstream_id") == o["upstream_id"]
        checks["route %s transcoding policy id unchanged (no policy churn)" % label] = str(mine.get("transcoding_policy_id")) == str(o["transcoding_policy_id"])
    for other, o in before["routes"].items():
        if other == label:
            continue
        a = after["routes"].get(other)
        checks["route %s PRESERVED (upstream id, uri, connection, tools, outbound policy id)" % other] = bool(a) and (
            a["upstream_id"] == o["upstream_id"] and a["uri"] == o["uri"] and a["connection"] == o["connection"]
            and a["tools"] == o["tools"] and str(a["transcoding_policy_id"]) == str(o["transcoding_policy_id"]))
        checks["route %s router route PRESERVED" % other] = after["router_routes"].get(other) == before["router_routes"].get(other)
    return all(checks.values()), ["%s: %s" % (k, "PASS" if v else "FAIL") for k, v in checks.items()]


def describe_routes(current):
    return ", ".join("%s -> %s@%s (%d tools, instance %s)" % (
        lbl, v["connection"].get("assetId"), v["connection"].get("version"), len(v["tools"]), v["connection"].get("apiVersionId"))
        for lbl, v in current["routes"].items()) or "none"


# ── live flow ─────────────────────────────────────────────────────────────────

def _read(base_url, token, org, env_id, iid):
    p = "/apimanager/api/v1/organizations/%s/environments/%s/apis/%s" % (org, env_id, iid)
    inst = pb._get(base_url, token, p + "?includeProxyConfiguration=true&includeTlsContexts=true")
    ups = pb._get(base_url, token, p + "/upstreams")
    pol = pb._get(base_url, token, p + "/policies")
    return inst, ups, pol


def _find_instance_id(base_url, token, org, env_id, label):
    hits = [a for a in pb._list_env_apis(base_url, token, org, env_id) if a.get("instanceLabel") == label]
    if len(hits) != 1:
        sys.exit("REFUSED: expected exactly one instance labeled %r, found %d - nothing changed." % (label, len(hits)))
    return hits[0]["id"]


def run(args, cfg, opts, payload, expected, mode):
    if pb.find_unresolved(payload, markers=MARKERS):
        sys.exit("REFUSED: target payload still carries markers:\n  " + "\n  ".join(pb.find_unresolved(payload, markers=MARKERS)))
    cid, sec = pb._live_gates()
    token = pb._token_for(opts, cid, sec)
    base_url, org, env_id = opts["base_url"], opts["org_id"], opts["anypoint_env_id"]
    iid = args.instance_id or _find_instance_id(base_url, token, org, env_id, expected["label"])
    inst, ups, pol = _read(base_url, token, org, env_id, iid)
    print("== RECONCILE DOMAIN ROUTE INTO THE SHARED BRIDGE (%s) == instance %s label=%s proxyUri=%s bound=%s@%s" % (
        mode, iid, inst.get("instanceLabel"), (inst.get("endpoint") or {}).get("proxyUri"), inst.get("assetId"), inst.get("assetVersion")))
    ok, lines = identity_guards(inst, ups, pol, expected)
    for line in lines:
        print("guard: " + line)
    if not ok:
        sys.exit("REFUSED (identity guard): instance %s is not the expected shared UIF Bridge, or its route for %s is not this "
                 "domain's - nothing changed." % (iid, expected["route_label"]))
    cur, tgt = current_state(inst, ups, pol), target_state(payload)
    action, diff = decide(cur, tgt)
    print("current: mcp %s; routes: %s%s" % (cur["mcp_version"], describe_routes(cur),
          ("; unrouted upstream(s): %s" % ", ".join("%s (%s)" % (u["id"], u["label"]) for u in cur["unrouted"])) if cur["unrouted"] else ""))
    print("target : mcp %s; route %s -> %s@%s (%d tools, instance %s); every other route preserved" % (
        tgt["mcp_version"], tgt["route_label"], tgt["connection"]["assetId"], tgt["connection"]["version"], len(tgt["tools"]),
        tgt["connection"]["apiVersionId"]))
    if action == "noop":
        print("NO-OP: the shared Bridge already binds the target MCP version and serves route %s with identical upstream, "
              "connection and mappings - nothing sent." % tgt["route_label"])
        return 0
    print("decision: %s (differs in: %s)" % (action.upper(), ", ".join(diff)))

    v1_api = "%s/apimanager/api/v1/organizations/%s/environments/%s/apis/%s" % (base_url, org, env_id, iid)
    xapi_api = "%s/apimanager/xapi/v1/organizations/%s/environments/%s/apis/%s" % (base_url, org, env_id, iid)
    xapi = xapi_api + "?checkAutomatedPolicies=true"
    v1_policy = v1_api + "/policies/%s"
    mine = cur["routes"].get(tgt["route_label"])
    reuse_orphan = next((u for u in cur["unrouted"] if _is_own_orphan(u, expected)), None) if action == "add" else None
    # the id the ADD will route to: reused leftover, or a placeholder until A1 answers
    new_id = reuse_orphan["id"] if reuse_orphan else ("<id returned by A1>" if action == "add" else None)
    body = build_patch_body(payload, inst, cur, tgt, new_upstream_id=new_id)
    router_body, transcoding_body = policy_patch_bodies(cur, tgt)
    if action == "add":
        if reuse_orphan:
            print("would send: (A1 skipped) REUSE unrouted upstream %s (label %r) left by an interrupted ADD - no duplicate created" % (
                reuse_orphan["id"], reuse_orphan["label"]))
        else:
            print("would send: A1 POST  %s/upstreams  %s   (documented v1; CLI api-mgr:upstream:add) -> {id}" % (v1_api, json.dumps(upstream_create_body(tgt))))
        print("            A2 PATCH %s  (%d upstreams by id incl. the new one with its connection, %d routes as {id, weight} references)" % (
            xapi, len(body["upstreams"]), len(body["routing"])))
        print("            A3 POST  %s/policies/outbound-policies  {mcp-transcoding@%s, %d tools, upstreamIds:[<A1 id>]}  (live-proven PoC surface)" % (
            xapi_api, pb.POLICY_PINS["mcp-transcoding"], len(tgt["tools_raw"])))
        print("            A4 PATCH %s  (router: merged routes %s)" % (v1_policy % cur["router_policy_id"], [r["upstreamName"] for r in router_body["configurationData"]["routes"]]))
    else:
        print("would send: PATCH %s  (%d upstreams, %d routes)" % (xapi, len(body["upstreams"]), len(body["routing"])))
        print("            PATCH %s  (router: merged routes %s)" % (v1_policy % cur["router_policy_id"], [r["upstreamName"] for r in router_body["configurationData"]["routes"]]))
        print("            PATCH %s  (transcoding tools of route %s)" % (v1_policy % mine["transcoding_policy_id"], tgt["route_label"]))
    if mode == "preflight":
        print(json.dumps(body, indent=2))
        if action == "add":
            print("A3 body: " + json.dumps(dict(outbound_policy_body(tgt), upstreamIds=[new_id]))[:600] + " ...")
        print("PREFLIGHT ONLY: nothing was modified.")
        return 0

    before = {"id": iid, "label": inst.get("instanceLabel"), "proxyUri": (inst.get("endpoint") or {}).get("proxyUri"),
              "router_policy_id": cur["router_policy_id"], "routes": copy.deepcopy(cur["routes"]),
              "router_routes": copy.deepcopy(cur["router_routes"]), "adding": action == "add"}
    os.makedirs(opts["artifacts_dir"], exist_ok=True)

    # second-line concurrency guard: the Bridge must still be exactly what the decision saw
    inst_b, ups_b, pol_b = _read(base_url, token, org, env_id, iid)
    if fingerprint(inst_b, ups_b, pol_b) != fingerprint(inst, ups, pol):
        sys.exit("REFUSED (concurrent modification): the shared Bridge %s changed between the decision read and the write "
                 "(another domain's deployment?) - nothing written; re-run this deployment." % iid)

    def send(method, url, doc, what, extra_headers=None):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        headers.update(extra_headers or {})
        req = urllib.request.Request(url, data=json.dumps(doc).encode(), method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode() or "{}"
                print("%s %s -> HTTP %s" % (method, what, r.status))
                try:
                    return json.loads(raw)
                except ValueError:
                    return {}
        except urllib.error.HTTPError as e:
            report = pb.describe_http_error(e, token, sec)
            path = os.path.join(opts["artifacts_dir"], "update-rejected.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"step": what, "status": report["status"], "response_headers": report["headers"],
                           "response_body": report["body"], "body": doc}, f, indent=2)
            sys.exit("UPDATE REJECTED at %s - HTTP %s\n%s\nbody + response saved: %s\nInstance %s may be partially updated - "
                     "run --preflight to see its current state (an upstream created by A1 but not yet routed is reused by the "
                     "next run, never duplicated); nothing is deleted." % (what, report["status"], report["body"], path, iid))

    xapi_headers = {"X-ANYPNT-ORG-ID": org, "X-ANYPNT-ENV-ID": env_id}
    if action == "add":
        if reuse_orphan:
            new_id = reuse_orphan["id"]
            print("A1 REUSE: unrouted upstream %s (label %r) - no POST" % (new_id, reuse_orphan["label"]))
        else:
            created = send("POST", v1_api + "/upstreams", upstream_create_body(tgt), "A1 upstream create (v1 POST .../upstreams)")
            new_id = str(created.get("id") or "")
            if not new_id:
                ups_now = pb._get(base_url, token, v1_api[len(base_url):] + "/upstreams")
                hits = [u for u in unrouted_upstreams(inst, ups_now) if u.get("label") == tgt["connection"]["label"]]
                if len(hits) != 1:
                    sys.exit("FAIL CLOSED: A1 returned no upstream id and the upstream list shows %d unrouted upstream(s) labeled %r - "
                             "inspect instance %s (provision_bridge.py --inspect %s); nothing else written." % (len(hits), tgt["connection"]["label"], iid, iid))
                new_id = str(hits[0]["id"])
            print("A1 CREATED upstream %s (label %r, uri %s)" % (new_id, tgt["connection"]["label"], tgt["uri"]))
        before["new_upstream_id"] = new_id
        body = build_patch_body(payload, inst, cur, tgt, new_upstream_id=new_id)
        send("PATCH", xapi, body, "A2 xapi instance (routes by upstream id)", xapi_headers)
        send("POST", xapi_api + "/policies/outbound-policies", dict(outbound_policy_body(tgt), upstreamIds=[new_id]),
             "A3 outbound mcp-transcoding policy for upstream %s" % new_id, xapi_headers)
        send("PATCH", v1_policy % cur["router_policy_id"], router_body, "A4 router policy")
    else:
        send("PATCH", xapi, body, "xapi instance", xapi_headers)
        send("PATCH", v1_policy % cur["router_policy_id"], router_body, "router policy")
        send("PATCH", v1_policy % mine["transcoding_policy_id"], transcoding_body, "transcoding policy (route %s)" % tgt["route_label"])
    inst2, ups2, pol2 = _read(base_url, token, org, env_id, iid)
    ok, lines = verify_update(inst2, ups2, pol2, before, tgt)
    for line in lines:
        print("read-back: " + line)
    if not ok:
        sys.exit("update read-back verification FAILED - inspect instance %s (provision_bridge.py --inspect %s); nothing deleted." % (iid, iid))
    others = [l for l in before["routes"] if l != tgt["route_label"]]
    print("%s in place: shared Bridge %s now binds %s@%s; route %s -> %s@%s with %d tools; %d other route(s) preserved (%s); "
          "label/path/TLS unchanged." % ("ADDED" if action == "add" else "UPDATED", iid, payload["spec"]["assetId"], payload["spec"]["version"],
                                           tgt["route_label"], tgt["connection"]["assetId"], tgt["connection"]["version"], len(tgt["tools"]),
                                           len(others), ", ".join(others) or "-"))
    return 0


def prepare(config, artifacts, environment, mcp_asset_version, source_instance_file, instance_label=None, base_path=None,
            upstream_url=None, platform_config=None, base_url=pb.DEFAULT_BASE_URL):
    """(cfg, opts, payload, expected) exactly as the CLI builds them - shared with deploy_bridge.py
    so the orchestrator decides on the same target the PATCH would send."""
    cfg = pb.load_yaml(config)
    platform_config = platform_config or os.path.join(os.path.dirname(os.path.abspath(__file__)), "anypoint-environments.yaml")
    platform = (pb.load_yaml(platform_config) or {}).get(environment) or {}
    captured = json.load(open(source_instance_file, encoding="utf-8"))
    opts = {"org_id": platform.get("org_id"), "anypoint_env_id": platform.get("env_id"), "gateway_id": platform.get("gateway_id"),
            "gateway_name": platform.get("gateway_name"), "gateway_version": "<preserved from the existing instance>",
            "tls_secret_group_id": platform.get("tls_secret_group_id"), "tls_context_id": platform.get("tls_context_id"),
            "mcp_asset_version": mcp_asset_version, "instance_label": instance_label, "base_path": base_path,
            "base_url": base_url, "upstream_url": upstream_url, "artifacts_dir": artifacts,
            "source_api_instance_id": str(captured["source_api_instance_id"]),
            "source_instance_label": captured.get("label"), "source_product_api_version": captured.get("productAPIVersion")}
    payload = pb.build_payload(cfg, environment, artifacts, opts)
    expected = {"label": payload["instanceLabel"], "base_path": pb.base_path_of(payload["endpoint"]["proxyUri"]),
                "mcp_asset_id": cfg["mcp_asset_id"], "env_id": opts["anypoint_env_id"],
                "source_asset_id": cfg["source_asset_id"], "route_label": payload["routing"][0]["label"],
                "source_instance_label": payload["routing"][0]["upstreams"][0].get("connection", {}).get("label")}
    return cfg, opts, payload, expected


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="EFFECTIVE bridge-config (domain inputs + shared Bridge identity; deploy_bridge.py writes it)")
    ap.add_argument("--artifacts", required=True, help="generated artifacts for the TARGET contract version")
    ap.add_argument("--environment", required=True)
    ap.add_argument("--mcp-asset-version", required=True, help="TARGET shared MCP asset version (already published)")
    ap.add_argument("--source-instance-file", required=True, help="source-instance.json (this domain's external upstream UUID)")
    ap.add_argument("--instance-id", default=None, help="the shared Bridge to reconcile into (default: the single instance carrying the label)")
    ap.add_argument("--instance-label", default=None)
    ap.add_argument("--base-path", default=None)
    ap.add_argument("--upstream-url", default=None)
    ap.add_argument("--platform-config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "anypoint-environments.yaml"))
    ap.add_argument("--base-url", default=os.environ.get("ANYPOINT_BASE_URL", pb.DEFAULT_BASE_URL))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", default="dry-run")
    g.add_argument("--preflight", dest="mode", action="store_const", const="preflight")
    g.add_argument("--execute", dest="mode", action="store_const", const="execute")
    a = ap.parse_args(argv)

    cfg, opts, payload, expected = prepare(a.config, a.artifacts, a.environment, a.mcp_asset_version, a.source_instance_file,
                                           a.instance_label, a.base_path, a.upstream_url, a.platform_config, a.base_url)
    if a.mode == "dry-run":
        tgt = target_state(payload)
        print("== RECONCILE DOMAIN ROUTE INTO THE SHARED BRIDGE (dry-run) == target %s@%s for Bridge %r at /%s; route %s -> %s@%s, "
              "upstream UUID %s" % (cfg["mcp_asset_id"], a.mcp_asset_version, expected["label"], expected["base_path"], tgt["route_label"],
                                    tgt["connection"]["assetId"], tgt["connection"]["version"], tgt["connection"]["apiVersionId"]))
        other = "<other domain route(s) - preserved verbatim>"
        fake_inst = {"endpoint": {"proxyUri": payload["endpoint"]["proxyUri"]},
                     "deployment": {"targetId": "<existing>", "targetName": "<existing>", "gatewayVersion": "<existing>", "environmentId": expected["env_id"]},
                     "routing": [{"label": other, "rules": {"headers": {"X-UPSTREAM-NAME": "<other>"}}}]}
        fake_cur = {"mcp_version": "<existing>", "transcoding_path": tgt["transcoding_path"], "router_policy_id": "<router policy id>",
                    "router_config": {"transcodingPath": tgt["transcoding_path"],
                                      "routes": [{"upstreamName": other, "tools": ["<their tools>"]}]},
                    "router_routes": {}, "unrouted": [], "routes": {
                        other: {"upstream_id": "<their upstream id>", "uri": "<their uri>", "upstream_label": None,
                                "connection": {"assetId": "<their REST asset>"}, "rules": {"headers": {"X-UPSTREAM-NAME": "<other>"}},
                                "tools": [], "tool_names": [], "transcoding_policy_id": "<their policy id>"}}}
        print("If route %s is ABSENT (ADD): A1 POST v1 .../apis/<id>/upstreams %s -> {id}" % (tgt["route_label"], json.dumps(upstream_create_body(tgt))))
        print("A2 PATCH xapi .../apis/<existing id>?checkAutomatedPolicies=true")
        print(json.dumps(build_patch_body(payload, fake_inst, fake_cur, tgt, new_upstream_id="<id from A1>"), indent=2))
        print("A3 POST xapi .../apis/<id>/policies/outbound-policies: %s ..." % json.dumps(dict(outbound_policy_body(tgt), upstreamIds=["<id from A1>"]))[:400])
        r, t = policy_patch_bodies(fake_cur, tgt)
        print("A4 PATCH v1 .../policies/<router policy id>: %s" % json.dumps(r))
        print("If route %s is PRESENT (UPDATE): PATCH xapi (routes by id) + PATCH router + PATCH v1 .../policies/<transcoding policy id>: %s ..."
              % (tgt["route_label"], json.dumps(t)[:300]))
        print("DRY-RUN: nothing sent (no token request either).")
        return 0
    return run(a, cfg, opts, payload, expected, a.mode)


if __name__ == "__main__":
    sys.exit(main())
