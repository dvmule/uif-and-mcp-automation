"""release_version.py - the ONE ownership point for the Exchange RELEASE version.

Design (decided 2026-09-15):
  * The GitHub/domain OpenAPI is the source of truth for contract CONTENT.
  * Exchange state decides whether a new release is needed and what its version
    is. Exchange asset versions are immutable, so the release version is NOT the
    contract's info.version: the developer never bumps the authoritative OpenAPI
    just because Exchange needs another immutable version.
  * ONE resolved release version drives the whole release:
        <rest asset>@<v>   ==   <mcp asset>@<v>   ==   Bridge connection/spec version
    Nothing downstream computes a version of its own; everything reads
    release.json (or the workflow output derived from it).

Resolution (targeted Exchange reads for the exact domain asset only - never a
search or a scan of unrelated assets):
  1. GET  /exchange/api/v2/assets/{group}/{rest}/asset          latest version of the asset
       404  -> brand-new domain: release 1.0.0
               (cross-check: <rest>@1.0.0 and <mcp>@1.0.0 must NOT exist -> else fail closed)
  2. else latest = descriptor.version (strict semver, else fail closed); any other
     versions the descriptor lists are validated too and may not exceed `latest`
  3. GET  .../{rest}/{latest}  -> download its published `oas` file (the projection
     published last time); missing -> fail closed (cannot compare)
  4. project the CURRENT GitHub contract with info.version stamped to `latest`
     (exactly what exchange_publish would publish at that version) and compare:
       identical (bytes, or same YAML document) -> NO-OP, release = latest
       different                                -> release = patch bump of latest
                                                   cross-check: <rest>@next and
                                                   <mcp>@next must NOT exist
  Reruns with the same contract therefore resolve to the same version: no new
  Exchange version is ever created because CI ran again.

THE CONTRACT IS MULTI-FILE-AWARE (contract_bundle.py): the authoritative contract is the
root openapi.yaml PLUS every recursively reachable LOCAL external $ref dependency under the
domain's src/api. Both the comparison copy and the release copy are the GENERATED
self-contained BUNDLE of that set, so a dependency-only change IS a contract change (patch
bump) while an unreferenced file under src/api never affects the decision. The source files
are never modified; the bundle is a transient build artifact.

Outputs (written to --out, consumed by every downstream stage):
  release.json            {version, decision: initial|noop|bump|preview, latest,
                           contract_files: [root + reachable dependencies], ...}
  openapi-release.yaml    the SELF-CONTAINED bundle of the GitHub contract with
                          info.version = release version (a BUILD ARTIFACT; the
                          authoritative files are never modified). exchange_publish.py
                          rest publishes its projection; the frozen publisher's own
                          version guard (info.version == asset version) therefore still holds.

Modes: --dry-run (default): no network; release = the contract's own info.version,
decision "preview" (Exchange not consulted - PR/dry-run pipelines have no
credentials). --resolve: authenticated READS only (MCP_LIVE_ENABLED=true +
Connected App credentials); writes nothing remote.

Fail closed, never guess: non-semver versions, a listed version above the reported
latest, an existing next version (non-linear history), an MCP version history that
is ahead of the REST asset, or a latest REST version whose published file cannot
be retrieved all stop the run before any downstream stage.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

import contract_bundle as cb
import exchange_publish as xp  # frozen helpers reused read-only; never redefined here

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
INITIAL_VERSION = "1.0.0"
RELEASE_FILE = "release.json"
RELEASE_SPEC = "openapi-release.yaml"


# ── pure helpers ───────────────────────────────────────────────────────────────

def parse_semver(version, what="version"):
    m = SEMVER.match(str(version or ""))
    if not m:
        sys.exit("FAIL CLOSED (release version): %s %r is not a strict semantic version X.Y.Z - "
                 "Exchange state is ambiguous; resolve it manually." % (what, version))
    return tuple(int(g) for g in m.groups())


def bump_patch(version):
    major, minor, patch = parse_semver(version)
    return "%d.%d.%d" % (major, minor, patch + 1)


def listed_versions(descriptor):
    """Every version string the latest-asset descriptor happens to list (tolerant to
    the field shapes Exchange uses: otherVersions / versions / versionGroups[].versions)."""
    out = []

    def add(item):
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("version"):
            out.append(str(item["version"]))

    for key in ("otherVersions", "versions"):
        for item in descriptor.get(key) or []:
            add(item)
    for group in descriptor.get("versionGroups") or []:
        if isinstance(group, dict):
            for item in group.get("versions") or []:
                add(item)
    return out


def bundled_contract(spec_path):
    """The self-contained bundle of the authoritative contract (root + reachable local
    dependencies) and its discovery record; fails closed on unsafe/missing references."""
    try:
        return cb.bundle(spec_path)
    except cb.ContractError as e:
        sys.exit("FAIL CLOSED (contract bundle): %s" % e)


def contract_files(spec_path):
    """Sorted repo-relative (to src/api) list of the files that make up the contract."""
    return bundled_contract(spec_path)[1]["files"]


def stamp_spec(spec_path, version, out_path):
    """Write the SELF-CONTAINED bundle of the authoritative OpenAPI (root + reachable local
    dependencies, contract_bundle.py) with info.version = version. The source files are only
    READ. Returns (out_path, original_info_version)."""
    doc, _contract = bundled_contract(spec_path)
    if not isinstance(doc, dict) or not isinstance(doc.get("info"), dict):
        sys.exit("FAIL CLOSED (release version): %s has no info block" % spec_path)
    original = str(doc["info"].get("version"))
    doc["info"]["version"] = str(version)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False))
    return out_path, original


def projection_at(spec_path, version, workdir):
    """The exact bytes exchange_publish.py rest would publish for this contract at
    `version` (same stamped copy, same frozen projection + renderer)."""
    stamped, _ = stamp_spec(spec_path, version, os.path.join(workdir, "compare-%s.yaml" % version))
    content, _stats = xp.build_rest_projection(stamped, version)
    return content


def contracts_equal(existing, ours):
    """(equal, how). Byte-identical first; else the same YAML document (Exchange may
    re-serialize the stored file) - either way the CONTENT is unchanged."""
    if existing == ours:
        return True, "byte-identical"
    try:
        if yaml.safe_load(existing.decode("utf-8-sig")) == yaml.safe_load(ours.decode("utf-8")):
            return True, "same document (serialization differs)"
    except Exception:  # undecodable / unparsable published file -> different, never identical
        pass
    return False, "different"


# ── targeted Exchange reads ────────────────────────────────────────────────────

class ExchangeReader:
    """Exactly three read shapes, all addressed by group/asset(/version); no search.
    Every read is traced (method + path only - never the token, never a signed
    query string) so a --resolve run is auditable from its own output."""

    def __init__(self, base_url, token):
        self.base_url, self.token = base_url, token
        self.trace = []

    def _traced(self, path, outcome):
        self.trace.append("GET %s -> %s" % (path, outcome))
        print("  read %d: %s" % (len(self.trace), self.trace[-1]))

    def latest_descriptor(self, group, asset):
        path = "/exchange/api/v2/assets/%s/%s/asset" % (group, asset)
        req = urllib.request.Request(self.base_url + path, headers={"Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._traced(path, "404 (asset absent)")
                return None
            self._traced(path, "HTTP %s" % e.code)
            raise
        if not isinstance(body, dict):
            self._traced(path, "200 but not a JSON object")
            sys.exit("FAIL CLOSED (release version): latest-asset endpoint returned an unexpected body (not an object); "
                     "the discovery assumption does not hold - stop and inspect.")
        self._traced(path, "200 version=%s versionGroup=%s listed=%s keys=%s" % (
            body.get("version"), body.get("versionGroup"), listed_versions(body) or "-", sorted(body)[:12]))
        return body

    def descriptor(self, group, asset, version):
        d = xp._get_descriptor(self.base_url, self.token, group, asset, version)
        self._traced("/exchange/api/v2/assets/%s/%s/%s" % (group, asset, version),
                     "404 (absent)" if d is None else "200 (exists; files: %s)" % ", ".join(
                         sorted({"%s/%s" % (f.get("classifier"), f.get("packaging")) for f in (d.get("files") or [])}) or ["none"]))
        return d

    def published_file(self, descriptor, classifier, main_file):
        content = xp._download_published_file(descriptor, classifier, main_file)
        links = [f.get("externalLink") for f in (descriptor.get("files") or []) if f.get("classifier") == classifier and f.get("externalLink")]
        host_path = "<no link>"
        if links:
            parts = urllib.parse.urlsplit(links[0])
            host_path = "%s://%s%s" % (parts.scheme, parts.netloc, parts.path)  # signed query string deliberately omitted
        self._traced(host_path + " (published %s file)" % classifier,
                     "no %s file" % classifier if content is None else "%d bytes" % len(content))
        return content


# ── the decision ───────────────────────────────────────────────────────────────

def decide_release(spec_path, reader, group, rest_asset, mcp_asset, workdir):
    """Pure orchestration over `reader` (fakeable). Returns the release record."""
    base = {"rest_asset_id": rest_asset, "mcp_asset_id": mcp_asset, "group_id": group, "exchange_consulted": True}
    latest_desc = reader.latest_descriptor(group, rest_asset)

    if latest_desc is None:
        for asset in (rest_asset,) + ((mcp_asset,) if mcp_asset else ()):
            if reader.descriptor(group, asset, INITIAL_VERSION) is not None:
                sys.exit("FAIL CLOSED (release version): %s/%s reports no latest version, yet %s@%s exists - "
                         "Exchange state is inconsistent (or the MCP history exists without its REST asset); "
                         "resolve manually." % (group, rest_asset, asset, INITIAL_VERSION))
        return dict(base, decision="initial", version=INITIAL_VERSION, latest=None, compared=None,
                    reason="REST asset absent in Exchange -> first release")

    latest = str(latest_desc.get("version") or "")
    parse_semver(latest, "latest version of %s" % rest_asset)
    for other in listed_versions(latest_desc):
        if parse_semver(other, "listed version of %s" % rest_asset) > parse_semver(latest):
            sys.exit("FAIL CLOSED (release version): Exchange reports %s@%s as latest but also lists %s - "
                     "the version history is ambiguous; resolve manually." % (rest_asset, latest, other))

    full = reader.descriptor(group, rest_asset, latest)
    if full is None:
        sys.exit("FAIL CLOSED (release version): latest %s@%s is reported but not readable." % (rest_asset, latest))
    existing = reader.published_file(full, "oas", xp.PROJECTION_FILENAME)
    if existing is None:
        sys.exit("FAIL CLOSED (release version): %s@%s has no downloadable `oas` file (%s) - the published "
                 "contract cannot be compared with the GitHub contract." % (rest_asset, latest, xp.PROJECTION_FILENAME))
    same, how = contracts_equal(existing, projection_at(spec_path, latest, workdir))
    if same:
        return dict(base, decision="noop", version=latest, latest=latest, compared=how,
                    reason="GitHub contract == %s@%s -> keep the release version" % (rest_asset, latest))

    nxt = bump_patch(latest)
    if reader.descriptor(group, rest_asset, nxt) is not None:
        sys.exit("FAIL CLOSED (release version): %s@%s already exists although %s is reported as latest - "
                 "non-linear version history; resolve manually." % (rest_asset, nxt, latest))
    if mcp_asset and reader.descriptor(group, mcp_asset, nxt) is not None:
        sys.exit("FAIL CLOSED (release version): %s@%s already exists but %s@%s does not - the MCP version "
                 "history is ahead of the REST asset; resolve manually." % (mcp_asset, nxt, rest_asset, nxt))
    return dict(base, decision="bump", version=nxt, latest=latest, compared=how,
                version_group=latest_desc.get("versionGroup"),
                reason="GitHub contract differs from %s@%s -> patch bump" % (rest_asset, latest))


def write_release(out_dir, release, spec_path):
    """release.json + the stamped contract copy; the authoritative spec is read-only."""
    os.makedirs(out_dir, exist_ok=True)
    stamped, original = stamp_spec(spec_path, release["version"], os.path.join(out_dir, RELEASE_SPEC))
    release = dict(release, source_spec=os.path.abspath(spec_path), source_info_version=original,
                   release_spec=os.path.basename(stamped), contract_files=contract_files(spec_path))
    with open(os.path.join(out_dir, RELEASE_FILE), "w", encoding="utf-8") as f:
        json.dump(release, f, indent=2)
    return release


def read_release(path_or_dir):
    path = os.path.join(path_or_dir, RELEASE_FILE) if os.path.isdir(path_or_dir) else path_or_dir
    if not os.path.exists(path):
        sys.exit("FAIL CLOSED (release version): %s not found - run release_version.py first." % path)
    rel = json.load(open(path, encoding="utf-8"))
    parse_semver(rel.get("version"), "release version in %s" % path)
    return rel


def _github_outputs(release):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for key in ("version", "decision", "latest", "release_spec"):
                f.write("%s=%s\n" % (key, release.get(key) if release.get(key) is not None else ""))


def _reads_only_gates():
    if os.environ.get("MCP_LIVE_ENABLED") != "true":
        sys.exit("REFUSED: --resolve performs authenticated Exchange READS and requires MCP_LIVE_ENABLED=true.")
    cid, sec = os.environ.get("ANYPOINT_CLIENT_ID"), os.environ.get("ANYPOINT_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("REFUSED: set ANYPOINT_CLIENT_ID / ANYPOINT_CLIENT_SECRET (Connected App).")
    return cid, sec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, help="the authoritative OpenAPI 3.1 contract (read-only)")
    ap.add_argument("--rest-asset-id", required=True)
    ap.add_argument("--mcp-asset-id", default=None,
                    help="a PER-DOMAIN MCP asset paired with the REST release (legacy single-domain Bridge). With the SHARED "
                         "Bridge the MCP asset version is resolved separately (deploy_bridge.py) - omit this flag.")
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--group-id", default=None, help="defaults to --org-id")
    ap.add_argument("--out", required=True, help="build dir for release.json + %s" % RELEASE_SPEC)
    ap.add_argument("--base-url", default=os.environ.get("ANYPOINT_BASE_URL", xp.DEFAULT_BASE_URL))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", default="dry-run",
                   help="no network: preview with the contract's own info.version (default)")
    g.add_argument("--resolve", dest="mode", action="store_const", const="resolve",
                   help="authenticated Exchange READS only; resolves the release version")
    a = ap.parse_args(argv)
    group = a.group_id or a.org_id

    print("== RELEASE VERSION (%s) == %s/%s%s" % (a.mode, group, a.rest_asset_id, " + %s" % a.mcp_asset_id if a.mcp_asset_id else
                                                  " (REST release only; the shared MCP asset version is resolved by deploy_bridge.py)"))
    if a.mode == "dry-run":
        doc = yaml.safe_load(open(a.spec, encoding="utf-8-sig"))
        preview = str(((doc or {}).get("info") or {}).get("version"))
        parse_semver(preview, "contract info.version")
        release = {"rest_asset_id": a.rest_asset_id, "mcp_asset_id": a.mcp_asset_id, "group_id": group,
                   "exchange_consulted": False, "decision": "preview", "version": preview, "latest": None,
                   "compared": None, "reason": "dry-run: Exchange not consulted; previewing with the contract's info.version"}
    else:
        cid, sec = _reads_only_gates()
        token = xp._token(a.base_url, cid, sec)
        print("token    : 1 POST %s/accounts/api/v2/oauth2/token (Connected App; not an Exchange write)" % a.base_url)
        print("exchange reads (targeted to %s only):" % (" + ".join(x for x in (a.rest_asset_id, a.mcp_asset_id) if x)))
        reader = ExchangeReader(a.base_url, token)
        release = decide_release(a.spec, reader, group, a.rest_asset_id, a.mcp_asset_id, a.out)
        release["exchange_reads"] = list(reader.trace)
        print("exchange reads: %d GET, 0 POST/PATCH/PUT/DELETE" % len(reader.trace))
    release = write_release(a.out, release, a.spec)
    _github_outputs(release)
    print("decision : %s  (%s)" % (release["decision"], release["reason"]))
    if a.mcp_asset_id:
        print("release  : %s@%s  ==  %s@%s  ==  Bridge connection/spec version" % (
            a.rest_asset_id, release["version"], a.mcp_asset_id, release["version"]))
    else:
        print("release  : %s@%s  ==  this domain's route connection.version in the shared Bridge" % (a.rest_asset_id, release["version"]))
    if release.get("latest"):
        print("exchange : latest %s@%s, compared %s" % (a.rest_asset_id, release["latest"], release["compared"]))
    print("wrote    : %s, %s (authoritative %s unchanged; its info.version %s)" % (
        os.path.join(a.out, RELEASE_FILE), os.path.join(a.out, RELEASE_SPEC), a.spec, release["source_info_version"]))
    print("contract : %d file(s) bundled into the self-contained release copy: %s" % (
        len(release["contract_files"]), ", ".join(release["contract_files"])))
    if a.mode == "dry-run":
        print("DRY-RUN: nothing sent (no token request either).")
    else:
        print("RESOLVED with Exchange READS only: nothing was published or modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
