"""Generate-with-AI draft service (UIFPE-102).

POST {description, domain?, table?, drivingTable?} -> {columns: [...]} —
a DRAFT of intake-contract column entries for the Transform wizard's Rules
step. Governance per ADR-0006 D6: the model DRAFTS only; the draft lands in
the same Rules step as Import, where every deterministic gate (schema
validation, compiled-SQL preview, hygiene rules) applies unchanged, a human
reviews, and the delta compiler (UIFPE-100) renders the SQL. The model's
output is never executed and never reaches an artifact directly.

Bedrock via IAM only — no API keys anywhere. Keep this module free of
import-time AWS calls so the prompt/parse functions stay unit-testable.
"""
from __future__ import annotations

import json
import os
import re

MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "eu.anthropic.claude-sonnet-4-5-20250929-v1:0")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4000"))
# CORS: when fronted by a Lambda function URL with a cors{} config, the
# URL layer injects the headers — the handler must stay silent or the
# browser sees DUPLICATE Access-Control-* headers and rejects the
# response. Set ALLOWED_ORIGIN only when serving without URL-level CORS.
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "")

SYSTEM_PROMPT = """You draft gold-attribute intake entries for Pearson's \
Unified Integration Fabric Transform wizard. You output ONLY a JSON object \
{"columns": [...]} — no prose, no markdown fences.

Each column entry follows the gold-attribute-mapping intake contract:
- scalar:      {goldColumnName, businessDescription, kind:"scalar", dataType,
                source:{table, column, filter|null, join?}, transform:null,
                channels:{event,message,api}, nullable, notes:null}
- conditional: {goldColumnName, businessDescription, kind:"conditional",
                dataType, discriminator:{table,column}, channels,
                branches:[{condition, source, transform:null, fallback,
                scope?, notes:null}], notes:null}
- nested:      {goldColumnName, businessDescription, kind:"nested", joinKey,
                filter|null, channels,
                fields:[{name,type,sortKey,source:{table,column}}], notes:null}

Rules (violations make the draft unusable):
- goldColumnName is camelCase; dataType in
  [string,integer,number,float,boolean,date,timestamp].
- Conditions are VALUES only ("10002 (Digital Catalog)" or "ELSE") — never
  prose qualifiers; row groups go in scope, transforms in transform.
- EVERY source table that is not the driving table declares a structured
  source.join {type:"left"|"inner", on:[{leftTable,leftColumn,rightColumn}]}
  — EAV tables included (entity keys stated explicitly: INVENTORY_ITEM_ID,
  plus ORGANIZATION_ID when the value is per-organization). Never prose.
- A branch whose value fans out to a row GROUP carries a scope (named
  rowGroup reference or structured predicate).
- Transforms are STRUCTURED, never prose (UIFPE-105). A transform is null,
  a reference to a named rule ({"transform":{"rule":"roundTo2dp"}} — prefer
  this when a listed rule fits), or an inline definition:
  {"steps":[{op:round|cast|trim|upper|lower|coalesceTo, precision?, to?,
  value?}]} applied in order, and/or {"cases":[{"when":{op:startsWith|like|
  equals|in|isNull, values:[...]}, "then":{op:prepend|append|replaceText|
  literal|null|keep, text?, find?, replaceWith?}}], "else":"keep"|"null"}
  evaluated first match wins. Example — round to 2 decimals:
  {"steps":[{"op":"round","precision":2},{"op":"cast","to":"DECIMAL(38,2)"}]}.
  On a conditional column, a top-level "transform" applies AFTER the branch
  pick (e.g. URL normalization of whichever branch won); a branch's own
  transform applies to that branch only.
- Exactly one nested field has sortKey:true.
- Dotted names (fulfillmentOrgDetails.itemStatus) are NAMESPACED INDIVIDUAL
  columns — one level of dot notation, kind scalar or conditional. They are
  NEVER nested arrays; discriminator/branches never appear inside a field
  source. Sourcing that varies by a discriminator = kind:"conditional", e.g.:
  {"goldColumnName":"fulfillmentOrgDetails.itemStatus","kind":"conditional",
   "dataType":"string",
   "discriminator":{"table":"EGO_MTL_SY_ITEMS_EXT_B",
                    "column":"ITEM_CATALOG_GROUP_ID"},
   "branches":[
     {"condition":"ELSE","source":{"table":"EGO_MTL_SY_ITEMS_EXT_B",
      "column":"C_EXT_ATTR7","filter":"ATTR_GROUP_ID = 1171"},
      "transform":null,"fallback":"returnNull",
      "notes":null},
     {"condition":"10002 (Digital)","scope":{"group":"ukOrgs"},
      "source":{"table":"EGO_MTL_SY_ITEMS_EXT_B","column":"C_EXT_ATTR5",
      "filter":"ATTR_GROUP_ID = 1408"},"transform":null,
      "fallback":"returnNull","notes":null}], ...}
- If the request is ambiguous or names unknown tables/columns, still draft
  your best interpretation and put the open question in that column's
  "notes" field (the ONLY place prose is allowed).
"""

# mode=derivedSql (UIFPE-219): draft ONE derived column's query — ADR-0008,
# "the query is the contract". Same governance: the draft lands in the
# wizard's authoring panel where the advisory checks, the wrap preview,
# human review and the Python battery all apply; nothing is executed here.
DERIVED_SQL_PROMPT = """You draft the QUERY for one DERIVED gold column in \
Pearson's Unified Integration Fabric Transform wizard. You output ONLY a \
JSON object {"sql": "...", "keyedBy": [{"queryColumn": "...", \
"entityColumn": "..."}], "valueColumn": "..."} — no prose, no fences.

The contract (violations make the draft unusable):
- sql is ONE read-only Spark SELECT (WITH … SELECT allowed). No writes, no
  DDL, no trailing semicolon — it is embedded verbatim inside the gold query
  as a CTE and LEFT-joined back to the entity.
- The query returns ONE ROW PER ENTITY: every keyedBy.queryColumn is an
  output column of the query; entityColumn is the matching driving-table
  column. valueColumn is the output column that becomes the gold value.
- Read ONLY the tables listed as known — by their BARE names, never
  database-qualified.
- Deterministic only: no clock or randomness functions (rand, uuid, now,
  current_timestamp, current_date, current_time, unix_timestamp(),
  localtimestamp, current_timezone, monotonically_increasing_id, shuffle,
  reflect, java_method); any collect_list/collect_set is wrapped in
  array_sort()/sort_array().
- Identifiers UNQUOTED — no backticks; name output columns exactly as
  declared in keyedBy and valueColumn.
- Prefer aggregation (min/max/group by) or window functions to guarantee
  the one-row-per-entity grain.
"""


def build_prompt(body: dict) -> str:
    ctx = [f"Entity: {body.get('table') or 'gold_product.product'} "
           f"(domain {body.get('domain') or 'product'})",
           f"Driving table: {body.get('drivingTable') or 'MTL_SYSTEM_ITEMS_B'}"]
    if body.get("knownTables"):
        ctx.append("Known Silver tables: " + ", ".join(body["knownTables"][:40]))
    if body.get("mode") == "derivedSql":
        if body.get("columnName"):
            ctx.append(f"Gold column being derived: {body['columnName']} "
                       f"({body.get('dataType') or 'string'})")
        if body.get("entityKey"):
            ctx.append("Entity key (driving-table columns): "
                       + ", ".join(body["entityKey"]))
    if body.get("rowGroups"):
        ctx.append("Named row groups: " + json.dumps(body["rowGroups"]))
    if body.get("transforms"):
        ctx.append("Named transform rules: " + json.dumps(body["transforms"]))
    ctx.append("Request:\n" + (body.get("description") or "").strip())
    return "\n".join(ctx)


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:])


def parse_draft(text: str) -> dict:
    """Model output -> {"columns": [...]}; tolerate accidental fences."""
    doc = _extract_json(text)
    cols = doc.get("columns")
    if not isinstance(cols, list) or not cols:
        raise ValueError("draft has no columns")
    for c in cols:
        if not c.get("goldColumnName") or c.get("kind") not in (
                "scalar", "conditional", "nested"):
            raise ValueError(f"malformed column entry: {c}")
    return {"columns": cols}


def parse_derived_draft(text: str) -> dict:
    """Model output -> {sql, keyedBy, valueColumn?}. The contract shape only
    — the WIZARD's advisory checks and the Python battery judge the SQL;
    this parse just refuses drafts the panel could not even display."""
    doc = _extract_json(text)
    sql = doc.get("sql")
    keyed = doc.get("keyedBy")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("draft has no sql")
    # the halves must be non-empty STRINGS — a numeric column index passes a
    # truthiness check and then breaks every consumer expecting .trim()
    if not isinstance(keyed, list) or not keyed or not all(
            isinstance(k, dict)
            and isinstance(k.get("queryColumn"), str) and k["queryColumn"].strip()
            and isinstance(k.get("entityColumn"), str) and k["entityColumn"].strip()
            for k in keyed):
        raise ValueError("draft has no usable keyedBy contract")
    out = {"sql": sql.strip(), "keyedBy": keyed}
    if isinstance(doc.get("valueColumn"), str) and doc["valueColumn"].strip():
        out["valueColumn"] = doc["valueColumn"].strip()
    return out


def _resp(status: int, payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if ALLOWED_ORIGIN:
        headers.update({"Access-Control-Allow-Origin": ALLOWED_ORIGIN,
                        "Access-Control-Allow-Methods": "POST,OPTIONS",
                        "Access-Control-Allow-Headers": "content-type"})
    return {"statusCode": status, "headers": headers,
            "body": json.dumps(payload)}


def handler(event, _context=None, bedrock=None):
    method = (event.get("requestContext", {}).get("http", {})
              .get("method") or event.get("httpMethod") or "POST")
    if method == "OPTIONS":
        return _resp(200, {})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "body is not JSON"})
    if not isinstance(body, dict):  # '"x"' and '[1]' are JSON too
        return _resp(400, {"error": "body must be a JSON object"})
    if not (body.get("description") or "").strip():
        return _resp(400, {"error": "description is required"})

    if bedrock is None:  # pragma: no cover — real client only on Lambda
        import boto3
        bedrock = boto3.client("bedrock-runtime")
    derived = body.get("mode") == "derivedSql"
    try:
        r = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": DERIVED_SQL_PROMPT if derived else SYSTEM_PROMPT}],
            messages=[{"role": "user",
                       "content": [{"text": build_prompt(body)}]}],
            inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0.2})
        text = r["output"]["message"]["content"][0]["text"]
        draft = parse_derived_draft(text) if derived else parse_draft(text)
    except ValueError as e:
        return _resp(502, {"error": f"model draft unusable: {e}"})
    except Exception as e:  # noqa: BLE001 — surface Bedrock faults as 502
        return _resp(502, {"error": f"bedrock call failed: {type(e).__name__}"})
    return _resp(200, draft)
