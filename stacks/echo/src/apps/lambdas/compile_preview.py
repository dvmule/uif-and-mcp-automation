"""Query preview service (UIFPE-219) — the Review step's pre-submit diff.

POST { envelope, artifacts } -> the recompiled artifact texts + the
compiler's change log, WITHOUT writing anything anywhere. The envelope is
exactly what Submit would send (submission.ts builds it); the artifacts are
the CURRENT texts of the entity's four registered files (plus the domain's
pipeline config), sent by the UI from its own build — the UI and the
deployed artifacts ship from the same merge, so the "before" the user diffs
against is the "before" the compiler would see.

Why the CLIENT sends the artifacts instead of this service bundling a repo
snapshot: the Lambda stays a pure function of its request — nothing to go
stale between deploys, no S3/git read at runtime, no lambda change when a
new domain registers (the UI's build-time globs discover new entities on
their own), and the diff base is byte-identical to what the browser holds.

The compiler here is the REAL one — compile_intake_delta.py and its
derivation battery, bundled verbatim into this zip by the Terraform
Makefile's build-preview step (with sqlglot, pinned from
qe/requirements-dev.txt). Nothing is mirrored, so the preview can never
drift from what the merge would actually do (ADR-0006 D3: the compiler
renders, never interprets — this service just runs it a step earlier and
throws the result away).

Import-time AWS-free and boto3-free: the handler makes no AWS call at all
(the IAM role carries logs only). Input is hostile by assumption — the
identifiers that become paths are fenced before any path is built, sizes
are capped, and the working tree is a per-invocation tmp dir removed on
every exit.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

# Bare import — flat Lambda zip convention (conftest.py puts scripts/ on
# sys.path for the test suite, the build step flattens both into one dir).
import compile_intake_delta as cid

MAX_BODY = 5_000_000        # bytes; the Function URL itself caps at 6 MB
MAX_ARTIFACT = 2_000_000    # per artifact text
# domain and entity become PATH SEGMENTS — plain lowercase identifiers only,
# so ".." or a separator can never reach Path arithmetic. re.ASCII so \w-ish
# classes can't be widened by locale.
IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
REQUIRED = ("mapping", "master", "manifest", "sql")


class Refusal(Exception):
    """A 400 the fences raise before the compiler is ever invoked."""


def _response(status: int, body: dict) -> dict:
    # No CORS headers here — the Function URL's cors config owns that
    # (duplicating them doubles the header and breaks the browser check).
    return {"statusCode": status,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(body)}


def _fence(payload) -> tuple[dict, dict, str, str]:
    """Validate the request shape; returns (envelope, artifacts, domain,
    entity). Everything that becomes a filesystem path is identifier-locked
    here, and every artifact must be a bounded string."""
    if not isinstance(payload, dict):
        raise Refusal("body must be a JSON object")
    env, arts = payload.get("envelope"), payload.get("artifacts")
    if not isinstance(env, dict):
        raise Refusal("envelope must be an object")
    intake = env["intake"] if isinstance(env.get("intake"), dict) else env
    domain, table = intake.get("domain"), intake.get("table")
    if not isinstance(domain, str) or not IDENT_RE.fullmatch(domain):
        raise Refusal(f"domain {domain!r} is not a plain lowercase identifier")
    if not isinstance(table, str) or not table.strip():
        raise Refusal("intake.table missing")
    entity = table.split(".")[-1]
    if not IDENT_RE.fullmatch(entity):
        raise Refusal(f"entity {entity!r} is not a plain lowercase identifier")
    cols = intake.get("columns")
    if not isinstance(cols, list) or not cols \
            or not all(isinstance(c, dict) for c in cols):
        raise Refusal("intake.columns must be a non-empty list of objects")
    if not isinstance(arts, dict):
        raise Refusal("artifacts must be an object")
    # UIFPE-260: a NEW entity has no artifacts — the compiler scaffolds them
    # from the intake alone. The new-table identity fields gate the empty
    # tree, so a typo'd entity name still gets the registered-entity error.
    scaffold = (not arts and intake.get("drivingTable")
                and intake.get("entityKey")
                and intake.get("drivingSilverTable"))
    if not arts and not scaffold:
        absent = [k for k in ("drivingTable", "entityKey",
                              "drivingSilverTable") if not intake.get(k)]
        raise Refusal(
            f"no artifacts were sent, but the intake is missing "
            f"{', '.join(absent)} — a new-entity scaffold preview needs all "
            f"three (a table picked from a catalog without an environment "
            f"marker leaves drivingSilverTable unset)")
    for k in REQUIRED:
        if scaffold:
            break
        if not isinstance(arts.get(k), str) or not arts[k].strip():
            raise Refusal(f"artifacts.{k} missing — the entity's current "
                          f"{k} text is required (send no artifacts at all "
                          f"to preview a new entity's scaffold)")
    for k, v in arts.items():
        if k not in REQUIRED and k != "pipeline":
            raise Refusal(f"unexpected artifact {k!r}")
        if not isinstance(v, str):
            raise Refusal(f"artifacts.{k} must be a string")
        if len(v) > MAX_ARTIFACT:
            raise Refusal(f"artifacts.{k} exceeds {MAX_ARTIFACT} bytes")
    return env, arts, domain, entity


def _layout(root: Path, domain: str, entity: str, arts: dict) -> dict:
    """Write the artifact texts into the exact tree compile_delta reads —
    the same relative paths it builds for itself, so its Path keys equal
    ours and the results map straight back to logical names."""
    base = root / "domains" / domain / "src" / "schemas"
    paths = {
        "mapping": base / "mapping" / entity / "v1" / f"{domain}.mapping.json",
        "master": base / "gold" / entity / "v1" / "master.json",
        "manifest": base / "mapping" / entity / "v1" / "manifest.yaml",
        "sql": base / "mapping" / entity / "v1" / "sql" / f"{entity}.sql",
    }
    if isinstance(arts.get("pipeline"), str):
        # keyed by gold_id (= entity = filename stem), matching the compiler's
        # own path build — a domain can carry several pipelines (UIFPE-252)
        paths["pipeline"] = (root / "domains" / domain / "src" / "apps"
                             / "pipelines" / "base" / f"{entity}.yaml")
    for k, p in paths.items():
        if k not in arts:       # scaffold preview: an empty tree is the point
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(arts[k], encoding="utf-8", newline="")
    return paths


def handler(event: dict, _context=None) -> dict:
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "POST")
    if method == "OPTIONS":
        return _response(200, {})
    if method != "POST":
        return _response(405, {"error": "POST only"})
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    if len(raw) > MAX_BODY:
        return _response(400, {"error": f"request exceeds {MAX_BODY} bytes",
                               "refusal": False})
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _response(400, {"error": "body is not valid JSON",
                               "refusal": False})
    try:
        env, arts, domain, entity = _fence(payload)
    except Refusal as e:
        return _response(400, {"error": str(e), "refusal": False})

    # load_envelope's contract, minus the file read: intake may be nested
    # under "intake" or be the whole envelope.
    intake = env["intake"] if isinstance(env.get("intake"), dict) else env
    intent = env.get("intent") or "add"
    refresh = env.get("refresh") if isinstance(env.get("refresh"), dict) else None
    job_props = (env.get("jobProperties")
                 if isinstance(env.get("jobProperties"), dict) else None)

    tmp = Path(tempfile.mkdtemp(prefix="preview-"))
    try:
        paths = _layout(tmp, domain, entity, arts)
        try:
            edits, log = cid.compile_delta(tmp, intake, intent, refresh,
                                           job_props)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            # the compiler's own refusals (battery errors, anchors missing,
            # unregistered entity, prose transforms …) plus malformed intake
            # shapes (a string where a list belongs) — real answers the
            # author needs to see, never Lambda 500s
            msg = e.args[0] if e.args else str(e)
            return _response(400, {"error": str(msg), "refusal": True})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    by_path = {p: k for k, p in paths.items()}
    # every artifact the caller sent gets an answer — the compiler omits the
    # pipeline config from its edits when the schedule stands still
    files = {k: {"changed": False} for k in paths if k in arts}
    for p, text in edits.items():
        k = by_path.get(p)
        if k is None:
            # scaffold mode (UIFPE-260) emits files the caller never sent —
            # a new entity's artifacts plus its pipeline base and dev overlay
            k = ("overlay" if p.parent.name == "dev"
                 else "pipeline" if p.parent.name == "base"
                 else "sql" if p.suffix == ".sql"
                 else "manifest" if p.name == "manifest.yaml"
                 else "master" if p.name == "master.json"
                 else "mapping")
            files[k] = {"changed": True, "after": text}
        elif text != arts.get(k, ""):
            files[k] = {"changed": True, "after": text}
    return _response(200, {"intent": intent, "log": log, "files": files})
