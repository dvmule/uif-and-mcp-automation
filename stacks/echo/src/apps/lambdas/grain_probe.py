"""Grain test run for a derived column's query (UIFPE-219, ADR-0008 #5).

POST {sql, keyedBy: [{queryColumn, entityColumn}], aliasTables: {name: db.table}}
  -> {rowCount, entityCount, maxPerEntity, oneRowPerEntity,
      sample: {columns, rows}, note}

Answers the one question a static check cannot: does the author's query
return ONE ROW PER ENTITY against real dev data? The wizard's vocabulary
rule applies — the UI says "one row per <entity>", never "grain".

How it runs. The query's table names are the entity's registered runtime
ALIASES (items_ext, …) — views that exist only inside the Glue job. Here
they are resolved by prepending one CTE per name the caller maps
(`WITH items_ext AS (SELECT * FROM uif_echo_dev_silver_mdm.ego_…)`), so the
author's SQL executes VERBATIM, unrewritten, on Athena over the same Silver
data the real run reads. Two executions, concurrently: the grain summary
(row count, entity count, worst duplication) and a 10-row sample.

Honesty note the UI must carry: Athena speaks Trino, the real run speaks
Spark. Plain SQL behaves identically; a Spark-only function (collect_list)
fails here with a readable engine error while the real run would accept it
— the test is a fast approximation, and the battery + review remain the
gate. The Glue-side run would be dialect-exact but costs minutes and DPUs;
revisit if dialect misses hurt in practice.

Safety: the caller's SQL is executed BY DESIGN, so the fences are (1) the
same deterministic refusals the battery applies — one read-only SELECT, no
writes/DDL, no statement lists — over noise-stripped text, (2) strict
identifier validation on everything ELSE spliced into the wrapper (alias
names, table targets, key columns — the injection surface), and (3) an IAM
role that can read Silver AND Gold (Gold arrived with the entityStatus
mode) and write ONLY the results prefix; there is nothing a hostile query
could write to — both layers are internal catalog data behind the same
dev-MVP trust level, and the Entra revisit covers this URL like the others.

Like ai_draft/submit: no import-time AWS calls — the pure functions test
without credentials.
"""
from __future__ import annotations

import json
import os
import re
import time

ENV_NAME = os.environ.get("ENV_NAME", "dev")
WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
OUTPUT_S3 = os.environ.get(
    "OUTPUT_S3", "s3://uif-echo-dev-gold-artifacts/grain-probe-results/")
DATA_CATALOG = os.environ.get("DATA_CATALOG", "AwsDataCatalog")
GLUE_JOB_NAME = os.environ.get("GLUE_JOB_NAME", "uif-echo-dev-gold-transform")
POLL_CAP_SECONDS = int(os.environ.get("POLL_CAP_SECONDS", "45"))
SAMPLE_ROWS = 10

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$", re.ASCII)
TABLE_RE = re.compile(r"^[A-Za-z0-9_{}]+\.[A-Za-z0-9_]+$", re.ASCII)
WRITE_RE = re.compile(
    r"\b(insert|update|delete|merge|create|drop|alter|truncate|grant|unload)\b",
    re.IGNORECASE)


_NOISE_RE = re.compile(
    r"('(?:[^'\\]|\\.)*')"      # 1: single-quoted string
    r'|("(?:[^"\\]|\\.)*")'     # 2: double-quoted string
    r"|(`[^`]*`)"               # 3: backtick identifier
    r"|(--[^\n]*)"              # 4: line comment
    r"|(/\*.*?\*/)",            # 5: block comment
    re.S)


def strip_noise(sql: str) -> str:
    """Comments and string/backtick literals blanked — keyword scans must
    never match inside them (the same shape as the UI mirror). ONE
    left-to-right pass: a '--' inside a string is consumed by the string
    alternative, never mistaken for a comment — sequential substitutions
    got that wrong in both directions (found adversarially)."""
    def blank(m: re.Match) -> str:
        if m.group(1):
            return "''"
        if m.group(2):
            return '""'
        if m.group(3):
            return "``"
        return " "
    return _NOISE_RE.sub(blank, sql)


def validate_request(p: dict) -> list[str]:
    """Refuse before running — the deterministic battery subset for the SQL,
    strict identifier rules for everything spliced around it."""
    out: list[str] = []
    sql = p.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return ["sql is empty — nothing to test"]
    clean = strip_noise(sql)
    if ";" in clean:
        out.append("one statement only — remove the ';'")
    if not re.match(r"^\s*\(*\s*(select|with)\b", clean, re.IGNORECASE):
        out.append("the query must be one read-only SELECT")
    w = WRITE_RE.search(clean)
    if w:
        out.append(f"'{w.group(1).upper()}' has no place here — the test "
                   "runs read-only")

    keyed = p.get("keyedBy")
    if not isinstance(keyed, list) or not keyed:
        out.append("keyedBy is required — which output column identifies "
                   "the entity")
        keyed = []
    for k in keyed:
        qc = (k or {}).get("queryColumn") if isinstance(k, dict) else None
        if not isinstance(qc, str) or not IDENT_RE.fullmatch(qc):
            out.append(f"key column {qc!r} is not a plain identifier")

    aliases = p.get("aliasTables")
    if aliases is None:
        aliases = {}
    if not isinstance(aliases, dict):
        out.append("aliasTables must map name -> db.table")
        aliases = {}
    for name, target in aliases.items():
        if not isinstance(name, str) or not IDENT_RE.fullmatch(name):
            out.append(f"alias {name!r} is not a plain identifier")
        if not isinstance(target, str) or not TABLE_RE.fullmatch(target):
            out.append(f"alias target {target!r} is not db.table")
    return out


def resolve_env(table: str) -> str:
    """uif_echo_{env}_silver_mdm.x -> uif_echo_dev_silver_mdm.x — the
    catalog and manifests carry the placeholder; only the runtime knows
    which environment it is."""
    return table.replace("{env}", ENV_NAME)


def build_prelude(alias_tables: dict) -> str:
    """One CTE per mapped name. Unreferenced ones are legal and unscanned —
    prepending all of them beats parsing the query to see which it reads."""
    parts = [f"{name} AS (SELECT * FROM {resolve_env(target)})"
             for name, target in sorted(alias_tables.items())]
    return ", ".join(parts)


def grain_query(sql: str, keyed: list, alias_tables: dict) -> str:
    """row count vs entity count vs worst duplication, in ONE execution.
    The author's query is embedded verbatim as __q — never rewritten."""
    keys = ", ".join(k["queryColumn"] for k in keyed)
    prelude = build_prelude(alias_tables)
    with_list = (prelude + ", " if prelude else "") + (
        f"__q AS (\n{sql}\n), "
        f"__g AS (SELECT {keys}, count(*) AS __c FROM __q GROUP BY {keys})")
    return (f"WITH {with_list}\n"
            "SELECT (SELECT count(*) FROM __q) AS row_count,\n"
            "       (SELECT count(*) FROM __g) AS entity_count,\n"
            "       (SELECT max(__c) FROM __g) AS max_per_entity")


def sample_query(sql: str, alias_tables: dict) -> str:
    prelude = build_prelude(alias_tables)
    with_list = (prelude + ", " if prelude else "") + f"__q AS (\n{sql}\n)"
    return f"WITH {with_list}\nSELECT * FROM __q LIMIT {SAMPLE_ROWS}"


def verdict(row_count: int, entity_count: int, max_per: int | None) -> dict:
    ok = row_count == entity_count
    if ok:
        note = (f"one row per entity — {entity_count:,} entities, "
                f"{row_count:,} rows")
    else:
        note = (f"{row_count:,} rows across {entity_count:,} entities — "
                f"some entities get more than one row"
                + (f" (worst: {max_per})" if max_per else "")
                + "; aggregate or window the query so each entity "
                  "resolves to one row")
    return {"rowCount": row_count, "entityCount": entity_count,
            "maxPerEntity": max_per, "oneRowPerEntity": ok, "note": note}


# ------------------------------------------------------------------- Athena

class ProbeError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _start(athena, query: str) -> str:
    return athena.start_query_execution(
        QueryString=query,
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": OUTPUT_S3},
    )["QueryExecutionId"]


def _await_all(athena, qids: list) -> None:
    """Poll until every execution finishes; cancel everything on the cap.
    A failed query surfaces Athena's own message — Trino errors are
    readable, and a dialect miss should say exactly what it is."""
    deadline = time.monotonic() + POLL_CAP_SECONDS
    pending = set(qids)
    while pending:
        if time.monotonic() > deadline:
            for q in pending:
                try:
                    athena.stop_query_execution(QueryExecutionId=q)
                except Exception:
                    pass
            raise ProbeError(504, "the test took longer than "
                             f"{POLL_CAP_SECONDS}s and was cancelled — "
                             "narrow the query (or test on a smaller source)")
        for q in list(pending):
            state = athena.get_query_execution(QueryExecutionId=q)
            status = state["QueryExecution"]["Status"]
            if status["State"] in ("SUCCEEDED",):
                pending.discard(q)
            elif status["State"] in ("FAILED", "CANCELLED"):
                pending.discard(q)
                for other in pending:  # cancel-everything, on EVERY exit
                    try:
                        athena.stop_query_execution(QueryExecutionId=other)
                    except Exception:
                        pass
                reason = status.get("StateChangeReason", "query failed")
                if "Schema must be specified" in reason:
                    # a bare name the prelude did not map — usually a comma
                    # join, which the table scan documents as its blind spot
                    reason += (" — a table in the query was not recognized; "
                               "write joins as explicit JOINs")
                raise ProbeError(400, f"the test engine refused the query: "
                                 f"{reason}")
        if pending:
            time.sleep(1)


def _rows(athena, qid: str) -> tuple:
    """(columns, rows) with the header row Athena repeats stripped."""
    r = athena.get_query_results(QueryExecutionId=qid,
                                 MaxResults=SAMPLE_ROWS + 1)
    cols = [c["Name"] for c in
            r["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
    data = []
    for i, row in enumerate(r["ResultSet"]["Rows"]):
        vals = [c.get("VarCharValue") for c in row.get("Data", [])]
        if i == 0 and vals == cols:
            continue  # first row echoes the header for non-CTAS results
        data.append(vals)
    return cols, data


def run_probe(p: dict, athena=None) -> dict:
    if athena is None:  # pragma: no cover — real client only on Lambda
        import boto3
        athena = boto3.client("athena")
    sql = p["sql"].strip()
    keyed = p["keyedBy"]
    alias_tables = p.get("aliasTables") or {}
    g_id = _start(athena, grain_query(sql, keyed, alias_tables))
    s_id = _start(athena, sample_query(sql, alias_tables))
    _await_all(athena, [g_id, s_id])
    _, g_rows = _rows(athena, g_id)
    cols, rows = _rows(athena, s_id)
    if not g_rows or len(g_rows[0]) < 3:
        raise ProbeError(502, "the grain summary came back empty")
    row_count = int(g_rows[0][0] or 0)
    entity_count = int(g_rows[0][1] or 0)
    max_per = int(g_rows[0][2]) if g_rows[0][2] is not None else None
    return {**verdict(row_count, entity_count, max_per),
            "sample": {"columns": cols, "rows": rows},
            "engine": "athena"}


# ── entity status (UIFPE-219): the step-1 cards' live half ──────────────────
# mode=entityStatus: {goldId, database, table} -> last transform run for the
# entity + the gold table's row count. Same identifier fences as the probe —
# these three strings are spliced into a query and an API filter.

# {env} placeholder allowed — the catalog and manifests carry it, and
# only the runtime knows which environment it is
DB_RE = re.compile(r"^[a-z0-9_{}]+$", re.ASCII)


def validate_status_request(p: dict) -> list[str]:
    out = []
    for field, rx in (("goldId", IDENT_RE), ("database", DB_RE),
                      ("table", DB_RE)):
        v = p.get(field)
        if not isinstance(v, str) or not rx.fullmatch(v):
            out.append(f"{field} must be a plain identifier")
    return out


def entity_status(p: dict, athena=None, glue=None) -> dict:
    if athena is None:  # pragma: no cover — real clients only on Lambda
        import boto3
        athena = boto3.client("athena")
        glue = boto3.client("glue")
    last = None
    try:
        # the transform job is SHARED across domains — a fine-cadence
        # pipeline floods the newest page, so walk a few pages before
        # concluding an entity has no run
        token = None
        for _ in range(5):
            kw = {"JobName": GLUE_JOB_NAME, "MaxResults": 60}
            if token:
                kw["NextToken"] = token
            resp = glue.get_job_runs(**kw)
            for r in resp["JobRuns"]:  # newest first
                if r.get("Arguments", {}).get("--gold_id") == p["goldId"]:
                    last = {"state": r.get("JobRunState") or "unknown",
                            "startedOn": r["StartedOn"].isoformat(),
                            "seconds": r.get("ExecutionTime")}
                    break
            if last or not resp.get("NextToken"):
                break
            token = resp["NextToken"]
    except Exception:
        pass  # a Glue hiccup degrades the card, never errors it
    try:
        db = resolve_env(p["database"])
        table = resolve_env(p["table"])
        qid = _start(athena, f"SELECT count(*) FROM {db}.{table}")
        _await_all(athena, [qid])
        _, rows = _rows(athena, qid)
        count = int(rows[0][0]) if rows and rows[0] and rows[0][0] else 0
    except ProbeError as e:
        # _await_all speaks the TEST RUN's language — rephrase for a lookup
        raise ProbeError(e.code, "the status lookup failed: "
                         + e.message.split(":", 1)[-1].strip())
    return {"rowCount": count, "lastRun": last}


# ------------------------------------------------------------------ handler

def _response(code: int, body: dict) -> dict:
    # no CORS headers — the function URL's cors{} config injects them
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def handler(event: dict, _context=None, athena=None) -> dict:
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "POST")
    if method == "OPTIONS":
        return _response(200, {})
    try:
        import base64
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return _response(400, {"problems": ["body must be a JSON object"]})
        if payload.get("mode") == "entityStatus":
            problems = validate_status_request(payload)
            if problems:
                return _response(400, {"problems": problems})
            return _response(200, entity_status(payload, athena=athena))
        problems = validate_request(payload)
        if problems:
            return _response(400, {"problems": problems})
        return _response(200, run_probe(payload, athena=athena))
    except ProbeError as e:
        return _response(e.code, {"problems": [e.message]})
    except json.JSONDecodeError:
        return _response(400, {"problems": ["request body is not JSON"]})
    except Exception as e:  # pragma: no cover — last resort, never a stack
        return _response(500, {"problems": [f"test service error: {e}"]})
