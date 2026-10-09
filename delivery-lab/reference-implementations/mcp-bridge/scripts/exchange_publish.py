"""Exchange asset publication — DOCUMENTED Anypoint Exchange API v2 only.

Surface (capabilities/mcp/mcp-bridge.md §Automation surfaces), both assets:
  POST /exchange/api/v2/organizations/{org}/assets/{group}/{asset}/{version}
  headers: Authorization bearer, x-sync-publication: true;  multipart form-data
  - REST source asset (OpenAPI):  name, description, properties.mainFile,
        properties.apiVersion, files.oas.yaml=@<OAS 3.0.3 PROJECTION>
        (classifier `oas` identifies a REST API asset; no `type` field)
  - MCP asset:                    name, description, type=mcp,
        files.mcp-metadata.json=@<generated metadata>

OPENAPI 3.1 CONSTRAINT (verified 2026-09-10): Exchange supports OAS 2.0/3.0
only and parses specs server-side; the retired API Catalog CLI failed on the
same limit (AMF 'OAS 3.0' parser fed a 3.1 document). The git contract stays
OpenAPI 3.1 and authoritative; the REST asset is its deterministic OAS 3.0.3
PROJECTION (oas_projection.py) — a build artifact, never committed. The
projection fails closed on any construct it cannot translate safely.

VERSIONING: REST asset version == MCP asset version == contract info.version.
The REST publish REFUSES if the contract's info.version differs from
--asset-version (determinism guard).

PUBLISH SAFETY GUARD (both assets):
  version absent -> publish;  exists + published content byte-identical to the
  generated artifact -> no-op;  exists + DIFFERENT content -> FAIL CLOSED
  ("conflicts with newly generated content and requires an explicit
  versioning decision" — never instructs the domain to bump info.version).
  After a publish, existence is re-verified (fail closed if absent).

SAFETY MODEL: --dry-run default (writes the artifact locally, prints the exact
request, sends NOTHING — no token request); --execute requires
MCP_LIVE_ENABLED=true + ANYPOINT_CLIENT_ID/SECRET in the environment.
stdlib only (urllib) + PyYAML.
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
import zipfile

from oas_projection import ProjectionError, project_oas31_to_30, render_yaml

DEFAULT_BASE_URL = "https://anypoint.mulesoft.com"
PROJECTION_FILENAME = "openapi.yaml"  # the uploaded file name == properties.mainFile

CONFLICT_MESSAGE = ("FAIL CLOSED: the immutable %s asset version %s conflicts with newly "
                    "generated content and requires an explicit versioning decision "
                    "(Exchange asset versions cannot be changed in place).")


# ── shared plumbing ────────────────────────────────────────────────────────────

def _require_live_gates():
    if os.environ.get("MCP_LIVE_ENABLED") != "true":
        sys.exit("REFUSED: --execute requires MCP_LIVE_ENABLED=true in the environment "
                 "(live MCP operations are latched off; see capabilities/mcp/mcp-bridge.md).")
    cid = os.environ.get("ANYPOINT_CLIENT_ID")
    sec = os.environ.get("ANYPOINT_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("REFUSED: set ANYPOINT_CLIENT_ID / ANYPOINT_CLIENT_SECRET (Connected App).")
    return cid, sec


def _token(base_url, cid, sec):
    body = json.dumps({"grant_type": "client_credentials",
                       "client_id": cid, "client_secret": sec}).encode()
    req = urllib.request.Request(base_url + "/accounts/api/v2/oauth2/token", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def _get_descriptor(base_url, token, group, asset, version):
    """Exchange v2 asset descriptor, or None when the version does not exist."""
    req = urllib.request.Request("%s/exchange/api/v2/assets/%s/%s/%s" % (base_url, group, asset, version),
                                 headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _asset_exists(base_url, token, group, asset, version):
    return _get_descriptor(base_url, token, group, asset, version) is not None


def decide_publish(existing_content, generated_content):
    """The publish guard, as a pure decision:
    existing None -> 'publish'; identical bytes -> 'noop'; different -> 'conflict'."""
    if existing_content is None:
        return "publish"
    return "noop" if existing_content == generated_content else "conflict"


def _download_published_file(descriptor, classifier, main_file=None):
    """Bytes of the published file for `classifier` via the descriptor's
    pre-signed link. Prefers a single-file packaging; falls back to reading
    `main_file` out of a zip packaging. None when not found."""
    files = descriptor.get("files") or []
    for f in files:
        if f.get("classifier") == classifier and f.get("packaging") in ("json", "yaml") and f.get("externalLink"):
            with urllib.request.urlopen(f["externalLink"], timeout=60) as r:
                return r.read()
    if main_file:
        for f in files:
            if f.get("classifier") == classifier and f.get("packaging") == "zip" and f.get("externalLink"):
                with urllib.request.urlopen(f["externalLink"], timeout=60) as r:
                    with zipfile.ZipFile(io.BytesIO(r.read())) as z:
                        names = z.namelist()
                        hit = next((n for n in names if n == main_file or n.endswith("/" + main_file)), None)
                        return z.read(hit) if hit else None
    return None


def _multipart(fields, files):
    """Encode multipart/form-data with stdlib. files: {field: (filename, bytes, ctype)}."""
    boundary = "----uifmcp" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                % (boundary, k, v)).encode()
    for k, (fname, data, ctype) in files.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                "Content-Type: %s\r\n\r\n" % (boundary, k, fname, ctype)).encode()
        out += data + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary


def _post_asset(base_url, token, url, fields, files):
    body, ctype = _multipart(fields, files)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "bearer " + token, "x-sync-publication": "true", "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.status, r.read().decode()[:2000]


def _print_request(url, fields, file_field, file_path):
    print("POST %s" % url)
    print("  headers: Authorization bearer <token>, x-sync-publication: true")
    for k, v in fields.items():
        print("  -F %s=%s" % (k, v))
    print("  -F %s=@%s" % (file_field, file_path))


def _run_guarded_publish(args, kind, group, asset_id, version, fields, file_field, file_name,
                         content, classifier, main_file):
    """Shared execute path: gates -> token -> existence/content guard -> POST -> verify."""
    cid, sec = _require_live_gates()
    token = _token(args.base_url, cid, sec)
    descriptor = _get_descriptor(args.base_url, token, group, asset_id, version)
    existing = _download_published_file(descriptor, classifier, main_file) if descriptor else None
    verdict = decide_publish(existing, content) if descriptor is None or existing is not None else "conflict"
    if verdict == "noop":
        print("Version already exists with IDENTICAL content — idempotent no-op.")
        return 0
    if verdict == "conflict":
        sys.exit(CONFLICT_MESSAGE % (kind, version))
    url = "%s/exchange/api/v2/organizations/%s/assets/%s/%s/%s" % (args.base_url, args.org_id, group, asset_id, version)
    ctype = "application/json" if file_name.endswith(".json") else "application/x-yaml"
    status, text = _post_asset(args.base_url, token, url, fields, {file_field: (file_name, content, ctype)})
    print("Exchange response %s: %s" % (status, text))
    if not _asset_exists(args.base_url, token, group, asset_id, version):
        sys.exit("FAIL CLOSED: %s/%s@%s still does NOT exist in Exchange after the publish attempt." % (group, asset_id, version))
    print("VERIFIED: %s/%s@%s now exists in Exchange — publish succeeded." % (group, asset_id, version))
    return 0


# ── REST source asset: OAS 3.0.3 projection of the 3.1 contract ───────────────

def rest_publish_fields(asset_id, description, api_version, main_file=PROJECTION_FILENAME):
    """Documented REST/OAS creation form fields (classifier `oas`, no `type`)."""
    return {
        "name": asset_id,
        "description": description,
        "properties.mainFile": main_file,
        "properties.apiVersion": api_version,
    }


def build_rest_projection(spec_path, asset_version):
    """Load the authoritative 3.1 contract, enforce version pairing, project to
    OAS 3.0.3 (fail closed), and render deterministically. Returns (bytes, stats)."""
    import yaml
    doc = yaml.safe_load(open(spec_path, encoding="utf-8-sig"))
    info_version = str(((doc or {}).get("info") or {}).get("version"))
    if info_version != str(asset_version):
        sys.exit("FAIL CLOSED (version guard): contract info.version %r != --asset-version %r "
                 "(REST asset version must equal the contract version)." % (info_version, asset_version))
    try:
        projected, stats = project_oas31_to_30(doc)
    except ProjectionError as e:
        sys.exit("FAIL CLOSED (OAS 3.0 projection): " + str(e))
    return render_yaml(projected).encode("utf-8"), stats


def publish_rest_asset(args):
    group = args.group_id or args.org_id
    content, stats = build_rest_projection(args.spec, args.asset_version)
    os.makedirs(args.out, exist_ok=True)
    artifact = os.path.join(args.out, "openapi-3.0-projection.yaml")
    with open(artifact, "wb") as f:
        f.write(content)
    api_version = "v" + str(args.asset_version).split(".")[0]
    fields = rest_publish_fields(
        args.asset_id,
        args.description or ("OAS 3.0.3 projection of the %s v%s OpenAPI 3.1 contract "
                             "(git contract is authoritative; Exchange supports OAS 2.0/3.0)."
                             % (args.asset_id, args.asset_version)),
        api_version)
    url = "%s/exchange/api/v2/organizations/%s/assets/%s/%s/%s" % (args.base_url, args.org_id, group, args.asset_id, args.asset_version)
    print("== publish source REST asset (DOCUMENTED Exchange API v2, classifier oas) ==")
    print("projection: openapi 3.1 -> 3.0.3 | info.version %s | operations %d | nullable type arrays converted %d"
          % (stats["info_version"], stats["operations"], stats["type_arrays_converted"]))
    print("artifact: %s (%d bytes)" % (artifact, len(content)))
    _print_request(url, fields, "files.oas.yaml", artifact)
    if args.dry_run:
        print("DRY-RUN: nothing sent (no token request either).")
        return 0
    return _run_guarded_publish(args, "REST", group, args.asset_id, args.asset_version, fields,
                                "files.oas.yaml", PROJECTION_FILENAME, content, "oas", PROJECTION_FILENAME)


# ── MCP asset ──────────────────────────────────────────────────────────────────

def publish_mcp_asset(args):
    group = args.group_id or args.org_id
    meta_path = os.path.join(args.artifacts, "mcp-metadata.json")
    if not os.path.exists(meta_path):
        sys.exit("missing %s — run generate_mcp_metadata.py first" % meta_path)
    content = open(meta_path, "rb").read()
    fields = {"name": args.asset_id, "description": args.description, "type": "mcp"}
    url = "%s/exchange/api/v2/organizations/%s/assets/%s/%s/%s" % (args.base_url, args.org_id, group, args.asset_id, args.asset_version)
    print("== publish MCP asset (DOCUMENTED Exchange API v2, type=mcp) ==")
    _print_request(url, fields, "files.mcp-metadata.json", meta_path)
    if args.dry_run:
        print("DRY-RUN: nothing sent (no token request either).")
        return 0
    return _run_guarded_publish(args, "MCP", group, args.asset_id, args.asset_version, fields,
                                "files.mcp-metadata.json", "mcp-metadata.json", content, "mcp-metadata", None)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="what", required=True)

    m = sub.add_parser("mcp", help="publish the generated MCP asset (Exchange v2, type=mcp)")
    m.add_argument("--artifacts", required=True, help="dir holding generated mcp-metadata.json")
    m.add_argument("--description", default="UIF-generated MCP asset (source of truth: the domain OpenAPI contract).")

    r = sub.add_parser("rest", help="publish the source REST asset as an OAS 3.0.3 projection (Exchange v2)")
    r.add_argument("--spec", required=True, help="the authoritative OpenAPI 3.1 contract (read-only)")
    r.add_argument("--out", required=True, help="build dir for the projection artifact")
    r.add_argument("--description", default=None)

    for p in (m, r):
        p.add_argument("--org-id", required=True)
        p.add_argument("--group-id", default=None)
        p.add_argument("--asset-id", required=True)
        p.add_argument("--asset-version", required=True)
        p.add_argument("--base-url", default=os.environ.get("ANYPOINT_BASE_URL", DEFAULT_BASE_URL))
        g = p.add_mutually_exclusive_group()
        g.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
        g.add_argument("--execute", dest="dry_run", action="store_false",
                       help="perform the publish (requires MCP_LIVE_ENABLED=true + credentials)")
    a = ap.parse_args(argv)
    return publish_mcp_asset(a) if a.what == "mcp" else publish_rest_asset(a)


if __name__ == "__main__":
    sys.exit(main())
