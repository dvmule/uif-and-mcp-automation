"""shared_bridge.py - ONE shared MCP Bridge per environment: identity + MCP asset merge.

DECISION (2026-09-29): UIF domains do NOT get an MCP Bridge each. Every domain API
of an environment is served by ONE Bridge (DEV: `uif-domains-mcp` on the shared
shared gateway of the environment) whose identity lives in the shared platform
configuration (anypoint-environments.yaml, block `shared_bridge`) - never in a
domain. A domain deployment RECONCILES that domain into the shared Bridge: its own
route / upstream / connection / tools are replaced or added; every other domain's
route is preserved (update_bridge.py enforces and verifies it); nothing is ever
removed because a domain is absent from the current run's input.

What stays DOMAIN-OWNED (bridge-config.yaml + the OpenAPI contract):
  source_asset_id (its REST Exchange asset), route_label, tools/mappings (generated
  from the contract), environments.<env>.upstream_url, the REST release version,
  optionally source_instance_name (the Exchange external instance of its route;
  default: the uif-<domain>-<env> convention).
PROTECTED identities (shared_bridge.protected, optional): labels / assets / instance
  names the automation must never resolve, publish to, bind or mutate - fail closed
  before anything is sent (used for the temporary test isolation of the manually
  deployed reference Bridge). Every lookup in this tooling is EXACT-match (label ==,
  asset id in the URL path, instance name == + type external); no prefix or
  fallback matching exists, so a "-test" identity can never resolve the real one.
What is SHARED (anypoint-environments.yaml):
  Bridge instance label, the ONE MCP Exchange asset the Bridge binds, listener base
  path, transcoding path, org/env/gateway/TLS coordinates.

This module holds the pure pieces that are new with the shared model (no network;
callers do the Exchange reads/writes with the frozen helpers):
  * `load_shared`        - validate the shared_bridge block of one environment;
  * `effective_config`   - the bridge-config the frozen provisioning code receives:
                           domain inputs + shared identity (a domain may not carry a
                           per-domain MCP asset id, base path or a different
                           transcoding path any more - fail closed, never guess);
  * `merge_metadata`     - the shared MCP asset content = tools of ALL domains + one
                           connections[] entry per REST asset. A domain run rebuilds
                           it from the version the Bridge currently binds (downloaded
                           from Exchange - the only complete record of the OTHER
                           domains' tools) with this domain's tools/connection
                           replaced. Two domains may never derive the same tool name
                           (the router routes by tool name) -> fail closed;
  * `resolve_shared_version` - the shared asset's version moves INDEPENDENTLY of any
                           domain's REST release:
                             Bridge binds B and merged == published(B) -> reuse B
                             asset absent                              -> 1.0.0
                             latest L: merged == published(L) -> L, else patch bump
                             L+1 (L+1 must not exist - non-linear history refused).
"""
import copy
import json
import os
import sys

import yaml

import release_version as rv

SHARED_BLOCK = "shared_bridge"
REQUIRED_SHARED_KEYS = ("instance_label", "mcp_asset_id", "base_path")
DEFAULT_TRANSCODING_PATH = "/mcp"
MCP_CLASSIFIER = "mcp-metadata"
MCP_MAIN_FILE = "mcp-metadata.json"
EFFECTIVE_CONFIG_FILE = "effective-bridge-config.yaml"


# ── shared identity ────────────────────────────────────────────────────────────

def load_shared(platform_env, environment):
    """The validated `shared_bridge` block of one environment (SystemExit when absent/incomplete)."""
    block = (platform_env or {}).get(SHARED_BLOCK)
    if not isinstance(block, dict):
        sys.exit("REFUSED: anypoint-environments.yaml has no `%s` block for %r - the shared MCP Bridge identity "
                 "(instance_label, mcp_asset_id, base_path) is platform configuration and is never invented." % (SHARED_BLOCK, environment))
    missing = [k for k in REQUIRED_SHARED_KEYS if not block.get(k)]
    if missing:
        sys.exit("REFUSED: anypoint-environments.yaml %s.%s lacks %s." % (environment, SHARED_BLOCK, ", ".join(missing)))
    shared = {k: str(block[k]) for k in REQUIRED_SHARED_KEYS}
    shared["base_path"] = shared["base_path"].strip("/")
    shared["transcoding_path"] = str(block.get("transcoding_path") or DEFAULT_TRANSCODING_PATH)
    protected = block.get("protected") or {}
    if not isinstance(protected, dict) or any(not isinstance(v, list) for v in protected.values()):
        sys.exit("REFUSED: anypoint-environments.yaml %s.%s.protected must be a mapping of lists." % (environment, SHARED_BLOCK))
    shared["protected"] = {k: [str(x) for x in v] for k, v in protected.items()}
    shared["environment"] = environment
    # the COMMON UIF API upstream of this environment (anypoint-environments.yaml <env>.upstream_url):
    # shared by every domain; a domain's bridge-config.yaml may override it intentionally
    common = (platform_env or {}).get("upstream_url")
    shared["upstream_url"] = str(common).strip() if common else None
    return shared


def guard_protected(shared, eff=None, *, instance_label=None, source_instance_name=None):
    """Fail closed when any identity this run would resolve, publish to, bind or mutate is
    listed under shared_bridge.protected (the manually deployed reference resources during
    the temporary test isolation). Configuration-driven: an empty/absent block guards nothing."""
    prot = shared.get("protected") or {}
    hits = []
    if (instance_label or shared["instance_label"]) in prot.get("instance_labels", ()):
        hits.append("Bridge instance label %r" % (instance_label or shared["instance_label"]))
    if shared["mcp_asset_id"] in prot.get("mcp_asset_ids", ()):
        hits.append("MCP asset %r" % shared["mcp_asset_id"])
    if eff is not None:
        if eff.get("source_asset_id") in prot.get("rest_asset_ids", ()):
            hits.append("REST asset %r" % eff.get("source_asset_id"))
        if eff.get("route_label", eff.get("source_asset_id")) in prot.get("rest_asset_ids", ()):
            hits.append("route label %r" % eff.get("route_label", eff.get("source_asset_id")))
    if source_instance_name and source_instance_name in prot.get("source_instance_names", ()):
        hits.append("source instance %r" % source_instance_name)
    if hits:
        sys.exit("REFUSED (protected identity): %s is listed under shared_bridge.protected in anypoint-environments.yaml - "
                 "the automation must not resolve, publish to, bind or mutate it during the test isolation; nothing sent."
                 % "; ".join(hits))


def effective_config(cfg, shared):
    """bridge-config as the frozen provisioning code consumes it: the domain's inputs plus
    the shared identity. Fails closed on any per-domain value that would contradict the
    shared Bridge (a leftover per-domain MCP asset id, a base path, another transcoding path)."""
    eff = copy.deepcopy(cfg)
    domain = eff.get("domain")
    if not domain:
        sys.exit("REFUSED: bridge-config.yaml has no `domain:` - every convention (route label, source instance name, "
                 "source_asset_id default) derives from it.")
    # convention (decided 2026-09-30): the REST Exchange asset is uif-<domain>-api unless the domain says otherwise
    eff.setdefault("source_asset_id", "uif-%s-api" % domain)
    legacy_mcp = eff.get("mcp_asset_id")
    if legacy_mcp and legacy_mcp != shared["mcp_asset_id"]:
        sys.exit("REFUSED: bridge-config.yaml of %r carries mcp_asset_id %r but the Bridge is shared and binds the ONE MCP asset %r "
                 "(anypoint-environments.yaml shared_bridge.mcp_asset_id) - remove the per-domain value." % (domain, legacy_mcp, shared["mcp_asset_id"]))
    if eff.get("base_path"):
        sys.exit("REFUSED: bridge-config.yaml of %r sets base_path %r - the listener path belongs to the shared Bridge "
                 "(anypoint-environments.yaml shared_bridge.base_path), a domain cannot choose one." % (domain, eff["base_path"]))
    own_tp = eff.get("transcoding_path")
    if own_tp and str(own_tp) != shared["transcoding_path"]:
        sys.exit("REFUSED: bridge-config.yaml of %r sets transcoding_path %r but the shared Bridge serves %r - one transport path per Bridge."
                 % (domain, own_tp, shared["transcoding_path"]))
    eff["mcp_asset_id"] = shared["mcp_asset_id"]
    eff["transcoding_path"] = shared["transcoding_path"]
    # upstream: the COMMON UIF API upstream ORIGIN of the environment (anypoint-environments.yaml
    # <env>.upstream_url, scheme + host only) unless the domain sets environments.<env>.upstream_url
    # itself - an intentional per-domain override, never the default. The domain's base path is
    # NOT part of it: the gateway composes origin + the contract's servers[0] path + the operation
    # path (generate_mcp_metadata.build_mappings). No domain names, lists or maps here: every
    # discovered domain, present or future, resolves to the same shared origin.
    if shared.get("upstream_url") and "${" in shared["upstream_url"]:
        sys.exit("REFUSED: anypoint-environments.yaml %s.upstream_url is an unresolved placeholder %r - resolve the platform "
                 "configuration first (platform_config.py; deploy_bridge.py and reconcile_domains.py do this automatically)."
                 % (shared.get("environment"), shared["upstream_url"]))
    if shared.get("upstream_url") and shared.get("environment"):
        envs = eff.get("environments")
        if not isinstance(envs, dict):
            envs = eff["environments"] = {}
        env_block = envs.get(shared["environment"])
        if not isinstance(env_block, dict):
            env_block = envs[shared["environment"]] = {}
        if not env_block.get("upstream_url"):
            env_block["upstream_url"] = shared["upstream_url"]
            env_block["upstream_source"] = "shared anypoint-environments.yaml %s.upstream_url" % shared["environment"]
        else:
            env_block["upstream_source"] = "domain override (bridge-config.yaml)"
    guard_protected(shared, eff, source_instance_name=eff.get("source_instance_name"))
    return eff


def write_effective_config(eff, out_dir):
    """<out>/effective-bridge-config.yaml - a BUILD artifact; the domain's bridge-config.yaml is never modified."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, EFFECTIVE_CONFIG_FILE)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# GENERATED by deploy_bridge.py - domain inputs + the shared Bridge identity (anypoint-environments.yaml).\n"
                "# Build artifact for the frozen provisioning code; the domain's bridge-config.yaml is authoritative and unchanged.\n")
        f.write(yaml.safe_dump(eff, sort_keys=False, allow_unicode=True, default_flow_style=False))
    return path


# ── the shared MCP asset content ───────────────────────────────────────────────

def render_metadata(meta):
    """Exactly the bytes generate_mcp_metadata.write_artifacts writes (indent 2, unicode kept)."""
    return json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8")


def parse_metadata(content):
    """bytes -> metadata dict; SystemExit when the published file is not a metadata object."""
    try:
        doc = json.loads(content.decode("utf-8-sig"))
    except (ValueError, AttributeError, UnicodeDecodeError) as e:
        sys.exit("FAIL CLOSED (shared MCP asset): the published mcp-metadata.json is not valid JSON (%s) - the other domains' "
                 "tools cannot be preserved from it; inspect the asset in Exchange." % e)
    if not isinstance(doc, dict) or not isinstance(doc.get("tools"), list):
        sys.exit("FAIL CLOSED (shared MCP asset): the published mcp-metadata.json has no tools[] - not an MCP metadata document.")
    return doc


def _connection_asset(conn):
    return (conn.get("ref") or {}).get("assetId") or conn.get("name")


def merge_metadata(existing, domain_meta, replaced_tool_names, protected_tool_names=None):
    """The shared asset content after reconciling ONE domain.

    existing             the metadata the Bridge currently binds (None when the Bridge is new)
    domain_meta          this domain's generated metadata (ONE connection, its tools)
    replaced_tool_names  the tool names the Bridge currently serves for THIS domain's route
                         (its outbound mcp-transcoding policy + its router route) - these leave
    protected_tool_names the tool names served by EVERY OTHER route of the Bridge (the
                         Bridge's routing is the ownership record - the asset has none);
                         preserved verbatim, and a collision with this domain's new tools
                         fails closed (the router routes by tool name). None -> every
                         existing tool not in `replaced` is treated as another domain's.
    A tool that no route serves any more (orphaned by an earlier drift) is not another
    domain's: it is replaced when this domain now derives that name, kept otherwise.
    ORDER is preserved (this domain's block stays where its previous tools were; a new
    domain appends), so an unchanged domain re-merges byte-identical -> NO-OP."""
    conns = domain_meta.get("connections") or []
    if len(conns) != 1:
        sys.exit("FAIL CLOSED (shared MCP asset): a domain's generated metadata must carry exactly ONE connection, found %d." % len(conns))
    source_asset = _connection_asset(conns[0])
    new_tools = list(domain_meta.get("tools") or [])
    new_names = [t.get("name") for t in new_tools]
    if len(set(new_names)) != len(new_names):
        sys.exit("FAIL CLOSED (shared MCP asset): duplicate tool names inside %s's own metadata: %s" % (
            source_asset, ", ".join(sorted(n for n in new_names if new_names.count(n) > 1))))
    if existing is None:
        return {"transport": copy.deepcopy(domain_meta.get("transport")), "tools": copy.deepcopy(new_tools),
                "connections": copy.deepcopy(conns)}

    existing_tools = existing.get("tools") or []
    replaced = set(replaced_tool_names or ())
    if protected_tool_names is None:
        protected = {t.get("name") for t in existing_tools} - replaced
    else:
        protected = set(protected_tool_names) - replaced
    collisions = sorted(protected & set(new_names))
    if collisions:
        sys.exit("FAIL CLOSED (shared MCP asset): tool name(s) %s are already served by ANOTHER domain of the shared Bridge - "
                 "the router routes by tool name, so %s cannot publish them; rename the operation path in the contract "
                 "or agree a route-specific naming decision." % (", ".join(collisions), source_asset))
    transport = existing.get("transport") or domain_meta.get("transport")
    own_transport = domain_meta.get("transport") or {}
    if transport and own_transport and transport.get("path") and own_transport.get("path") and transport.get("path") != own_transport.get("path"):
        sys.exit("FAIL CLOSED (shared MCP asset): the Bridge transport path is %r but %s was generated for %r - one transport per Bridge."
                 % (transport.get("path"), source_asset, own_transport.get("path")))

    leaving = {n for n in replaced} | ({t.get("name") for t in existing_tools} - protected) & set(new_names)
    tools, placed = [], False
    for t in existing_tools:
        if t.get("name") in leaving:
            if not placed:
                tools += copy.deepcopy(new_tools)
                placed = True
            continue
        tools.append(copy.deepcopy(t))
    if not placed:
        tools += copy.deepcopy(new_tools)

    connections, placed = [], False
    for c in existing.get("connections") or []:
        if _connection_asset(c) == source_asset:
            if not placed:
                connections += copy.deepcopy(conns)
                placed = True
            continue
        connections.append(copy.deepcopy(c))
    if not placed:
        connections += copy.deepcopy(conns)
    return {"transport": copy.deepcopy(transport), "tools": tools, "connections": connections}


def metadata_equal(published, merged):
    """(equal, how): byte-identical, or the same JSON document (Exchange/UI may re-serialize)."""
    if published == merged:
        return True, "byte-identical"
    try:
        if json.loads(published.decode("utf-8-sig")) == json.loads(merged.decode("utf-8")):
            return True, "same document (serialization differs)"
    except Exception:
        pass
    return False, "different"


def tool_names(meta):
    return sorted(t.get("name") for t in (meta or {}).get("tools") or [])


def connection_versions(meta):
    """{rest asset id: version} of a metadata document - what the shared asset says each domain is at."""
    return {_connection_asset(c): str((c.get("ref") or {}).get("version")) for c in (meta or {}).get("connections") or []}


# ── the shared MCP asset version ───────────────────────────────────────────────

def resolve_shared_version(reader, group, asset_id, bound_version, merged):
    """Pure orchestration over a release_version.ExchangeReader-like `reader` (fakeable).

    bound_version  the version the live Bridge binds (None when the Bridge is new)
    merged         the merged metadata bytes this run wants the Bridge to serve
    Returns {version, decision: reuse|initial|bump, bound, latest, compared, reason}."""
    base = {"mcp_asset_id": asset_id, "group_id": group, "bound": bound_version, "exchange_consulted": True}

    def published(version):
        d = reader.descriptor(group, asset_id, version)
        if d is None:
            return None, None
        content = reader.published_file(d, MCP_CLASSIFIER, MCP_MAIN_FILE)
        if content is None:
            sys.exit("FAIL CLOSED (shared MCP asset): %s@%s has no downloadable %s file - the content the Bridge binds cannot "
                     "be compared; inspect the asset in Exchange." % (asset_id, version, MCP_MAIN_FILE))
        return d, content

    if bound_version:
        rv.parse_semver(bound_version, "MCP asset version bound by the shared Bridge")
        d, content = published(bound_version)
        if d is None:
            sys.exit("FAIL CLOSED (shared MCP asset): the Bridge binds %s@%s but Exchange has no such version - inconsistent state; "
                     "resolve manually." % (asset_id, bound_version))
        same, how = metadata_equal(content, merged)
        if same:
            return dict(base, decision="reuse", version=bound_version, latest=None, compared=how,
                        reason="merged metadata == %s@%s (bound by the Bridge) -> keep the version" % (asset_id, bound_version))

    latest_desc = reader.latest_descriptor(group, asset_id)
    if latest_desc is None:
        if bound_version:
            sys.exit("FAIL CLOSED (shared MCP asset): %s@%s is readable but the asset reports no latest version - inconsistent "
                     "Exchange state; resolve manually." % (asset_id, bound_version))
        if reader.descriptor(group, asset_id, rv.INITIAL_VERSION) is not None:
            sys.exit("FAIL CLOSED (shared MCP asset): %s reports no latest version, yet %s@%s exists - resolve manually."
                     % (asset_id, asset_id, rv.INITIAL_VERSION))
        return dict(base, decision="initial", version=rv.INITIAL_VERSION, latest=None, compared=None,
                    reason="shared MCP asset absent in Exchange -> first version")

    latest = str(latest_desc.get("version") or "")
    rv.parse_semver(latest, "latest version of %s" % asset_id)
    for other in rv.listed_versions(latest_desc):
        if rv.parse_semver(other, "listed version of %s" % asset_id) > rv.parse_semver(latest):
            sys.exit("FAIL CLOSED (shared MCP asset): Exchange reports %s@%s as latest but also lists %s - ambiguous history; "
                     "resolve manually." % (asset_id, latest, other))
    if latest != bound_version:
        _, content = published(latest)
        if content is None:
            sys.exit("FAIL CLOSED (shared MCP asset): latest %s@%s is reported but not readable." % (asset_id, latest))
        same, how = metadata_equal(content, merged)
        if same:
            return dict(base, decision="reuse", version=latest, latest=latest, compared=how,
                        reason="merged metadata == %s@%s (latest, not yet bound) -> bind it" % (asset_id, latest))
    nxt = rv.bump_patch(latest)
    if reader.descriptor(group, asset_id, nxt) is not None:
        sys.exit("FAIL CLOSED (shared MCP asset): %s@%s already exists although %s is reported as latest - non-linear history; "
                 "resolve manually." % (asset_id, nxt, latest))
    return dict(base, decision="bump", version=nxt, latest=latest, compared="different",
                reason="merged metadata differs from %s@%s -> patch bump" % (asset_id, latest))
