#!/usr/bin/env python3
# validate_silver_sources.py
# Read-only preflight for a Gold pipeline's FIRST load (and for every run after
# a Silver source changes). Answers one question: if the transform started now,
# would it succeed?
#
# It makes NO writes of any kind — no DynamoDB puts, no S3 uploads, no job
# starts. Every check is a get/head/describe.
#
# Checks, in the order the transform itself would hit them:
#   A  config      merged base+overlay config is valid (gold_config.validate)
#   B  contract    columns the mapping SQL requires, per source alias (static)
#   C  silver      each Silver table exists in the Glue catalog and carries
#                  every required column + its watermark column
#   D  artifacts   master.json / manifest.yaml / product.sql are in S3
#   E  job         the Glue transform job exists, its script objects exist, and
#                  the deployed script bytes match this working tree
#   F  control     the DynamoDB config item, its enabled flag and watermarks
#   G  target      whether the Gold table already exists (first load vs delta)
#
# Usage (from the monorepo root):
#   py stacks/echo/src/apps/cli/validate_silver_sources.py --env dev
#   py stacks/echo/src/apps/cli/validate_silver_sources.py --env dev --static
#   py stacks/echo/src/apps/cli/validate_silver_sources.py --env dev --profile pearson-esb-dev
#
# Exit codes: 0 = all checks passed (warnings allowed), 1 = at least one FAIL.

import argparse
import hashlib
import re
import sys
from pathlib import Path

from env_config import ENVIRONMENTS, DEFAULT_ENV
import gold_config
import gold_resources  # on sys.path via gold_config
from sync_pipelines import REGION, load_desired

# The domain whose pipelines and mapping SQL to check (product by default,
# like sync_pipelines); the gold runtime itself lives beside this script.
REPO_ROOT = Path(__file__).resolve().parents[5] / "domains" / "product" / "src" / "apps"
DOMAIN_ROOT = REPO_ROOT.parents[1]
RUNTIME_DIR = Path(__file__).resolve().parents[1] / "pipeline"

# Reserved words and type names that the identifier regex in required_columns()
# would otherwise mistake for Oracle column names. Kept explicit rather than
# clever: a wrong entry here produces a false PASS, so it should be reviewable.
_SQL_WORDS = {
    "WITH", "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "HAVING",
    "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "ON", "AS", "AND",
    "OR", "NOT", "NULL", "IS", "IN", "CASE", "WHEN", "THEN", "ELSE", "END",
    "CAST", "COALESCE", "DISTINCT", "UNION", "ALL", "LIMIT", "BETWEEN",
    "LIKE", "EXISTS", "ASC", "DESC", "OVER", "PARTITION",
    "BIGINT", "INT", "INTEGER", "STRING", "DATE", "TIMESTAMP", "DECIMAL",
    "DOUBLE", "FLOAT", "BOOLEAN", "ARRAY", "STRUCT", "MAP", "VARCHAR",
}

_STATUS_ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}


class Report:
    """Accumulates check results and prints them grouped by section."""

    def __init__(self):
        self.rows = []

    def add(self, section: str, status: str, check: str, detail: str = ""):
        self.rows.append((section, status, check, detail))

    def has_failures(self) -> bool:
        return any(r[1] == "FAIL" for r in self.rows)

    def print(self):
        icons = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "INFO": "  ->"}
        current = None
        for section, status, check, detail in self.rows:
            if section != current:
                print(f"\n--- {section} " + "-" * max(0, 66 - len(section)))
                current = section
            print(f"  [{icons[status]}] {check}")
            for line in (detail.splitlines() if detail else []):
                print(f"         {line}")
        counts = {s: 0 for s in ("PASS", "FAIL", "WARN")}
        for _, status, _, _ in self.rows:
            if status in counts:
                counts[status] += 1
        print("\n" + "=" * 72)
        print(f"  {counts['PASS']} passed | {counts['WARN']} warning(s) | "
              f"{counts['FAIL']} failure(s)")


# ── B: what the mapping SQL requires ────────────────────────────────────────

def required_columns(sql_text: str, aliases: list) -> dict:
    """{alias: {COLUMN, …}} — the Silver columns the mapping SQL reads.

    Attribution rules, which hold for this SQL shape and are asserted rather
    than inferred:
      * `alias.COLUMN` → that alias, wherever it appears.
      * inside a block that reads exactly ONE source alias, every unqualified
        Oracle-style identifier → that alias.
    A block reading two source aliases is reported ONLY when it also leaves an
    identifier the qualified pass could not attribute — a fully qualified
    multi-source block is not ambiguous, and refusing to answer for it would
    hide the very columns the caller needs (UIFPE-476: product's
    instructorResourceFiles joins the base and translations EAV tables, and its
    EXTENSION_ID join key was required on neither alias while this returned a
    bare warning).
    """
    # Strip comments and string literals first: 'US' and 'MDM' are literals,
    # not columns, and a commented-out column is not a requirement.
    text = re.sub(r"--[^\n]*", "", sql_text)
    text = re.sub(r"'[^']*'", "''", text)

    out = {a: set() for a in aliases}
    ambiguous = []

    ident = r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*"

    # Qualified references anywhere in the SQL.
    for alias in aliases:
        for col in re.findall(rf"\b{re.escape(alias)}\.({ident})\b", text):
            if col not in _SQL_WORDS:
                out[alias].add(col)

    # Split into CTE bodies + the final SELECT, then attribute unqualified
    # identifiers per block.
    blocks = _split_blocks(text)
    for name, body in blocks:
        read = [a for a in aliases
                if re.search(rf"\b(?:FROM|JOIN)\s+{re.escape(a)}\b", body)]
        if len(read) != 1:
            if len(read) > 1:
                # Strip every `alias.COLUMN` the qualified pass already took and
                # see whether a bare column-shaped identifier survives. If none
                # does, the block is fully qualified and nothing is unattributed.
                stripped = body
                for a in read:
                    stripped = re.sub(
                        rf"\b{re.escape(a)}\.{ident}\b", " ", stripped)
                bare = sorted({c for c in re.findall(rf"\b({ident})\b", stripped)
                               if c not in _SQL_WORDS and c not in aliases})
                if bare:
                    ambiguous.append(
                        f"{name} reads {read} and leaves {bare} unqualified")
            continue
        alias = read[0]
        for col in re.findall(rf"\b({ident})\b", body):
            if col in _SQL_WORDS or col in aliases:
                continue
            out[alias].add(col)

    return out, ambiguous


def _split_blocks(text: str) -> list:
    """[(block_name, body)] for each `name AS ( … )` CTE plus the final SELECT.

    Paren-depth scan rather than a regex: the CTE bodies contain nested
    parentheses (CAST, COALESCE, IN-lists) that a non-greedy regex splits in
    the wrong place.
    """
    blocks, last_end = [], 0
    for m in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(", text):
        if m.start() < last_end:      # nested `x AS (` inside a CTE body
            continue
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        blocks.append((m.group(1), text[start:i - 1]))
        last_end = i
    # The final SELECT is whatever follows the last CTE's closing paren.
    blocks.append(("<final SELECT>", text[last_end:]))
    return blocks


# ── AWS checks ──────────────────────────────────────────────────────────────

def check_silver(rep, glue, sources, required):
    for src in sources:
        alias, table = src["alias"], src["silver_table"]
        db, tbl = table.split(".", 1)
        try:
            meta = glue.get_table(DatabaseName=db, Name=tbl)["Table"]
        except Exception as e:
            rep.add("C. Silver sources", "FAIL", f"{alias} → {table}",
                    f"{type(e).__name__}: {e}\n"
                    f"The transform calls spark.table() on every source before "
                    f"it reads any data, so a missing table fails the run "
                    f"immediately — not partially.")
            continue

        sd = meta.get("StorageDescriptor", {})
        cols = {c["Name"].upper(): c.get("Type", "?")
                for c in sd.get("Columns", [])}
        cols.update({c["Name"].upper(): c.get("Type", "?")
                     for c in meta.get("PartitionKeys", [])})
        params = meta.get("Parameters", {})
        fmt = params.get("table_type", params.get("classification", "unknown"))

        rep.add("C. Silver sources", "PASS", f"{alias} → {table} exists",
                f"format={fmt}  columns={len(cols)}  "
                f"location={sd.get('Location', 'n/a')}")

        missing = sorted(c for c in required.get(alias, set())
                         if c.upper() not in cols)
        if missing:
            rep.add("C. Silver sources", "FAIL",
                    f"{alias}: {len(missing)} column(s) the mapping SQL needs "
                    f"are absent",
                    "missing: " + ", ".join(missing))
        else:
            rep.add("C. Silver sources", "PASS",
                    f"{alias}: all {len(required.get(alias, set()))} mapped "
                    f"columns present")

        wm = src.get("watermark_col", "_silver_updated_at")
        if wm.upper() in cols:
            rep.add("C. Silver sources", "PASS",
                    f"{alias}: watermark column {wm} present "
                    f"({cols[wm.upper()]})")
        else:
            rep.add("C. Silver sources", "FAIL",
                    f"{alias}: watermark column {wm} is absent",
                    "Phase 1 filters on this column and the initial "
                    "materialization takes max() of it. Without it the run "
                    "fails and no watermark can be recorded.")

        rows = params.get("recordCount") or params.get("numRows")
        if rows:
            rep.add("C. Silver sources", "INFO" if int(rows) else "WARN",
                    f"{alias}: catalog recordCount={rows}")
        else:
            rep.add("C. Silver sources", "WARN",
                    f"{alias}: row count unknown (no recordCount parameter)",
                    f"Catalog statistics are absent, which is normal for "
                    f"Iceberg tables written by Spark. Confirm with Athena:\n"
                    f"  SELECT count(*) FROM \"{db}\".\"{tbl}\";")


def check_artifacts(rep, s3, cfg):
    paths = {
        "schema.master_s3_path": cfg["schema"]["master_s3_path"],
        "mapping.manifest_s3_path": cfg["mapping"]["manifest_s3_path"],
        "mapping.sql_s3_path": cfg["mapping"]["sql_s3_path"],
    }
    for label, uri in paths.items():
        bucket, key = uri[5:].split("/", 1)
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            rep.add("D. Mapping + schema artifacts", "PASS", f"{label}",
                    f"{uri}\nsize={head['ContentLength']}B  "
                    f"modified={head['LastModified'].isoformat()}")
        except Exception as e:
            rep.add("D. Mapping + schema artifacts", "FAIL", f"{label}",
                    f"{uri}\n{type(e).__name__}: {e}\n"
                    f"The transform reads this from S3 at startup. Upload with "
                    f"the two `aws s3 sync` commands from "
                    f"deploy-product-dev.yml before starting a run.")


def _s3_etag(s3, bucket, key):
    try:
        return s3.head_object(Bucket=bucket, Key=key)["ETag"].strip('"'), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def check_job(rep, glue, s3, env, job_name):
    try:
        job = glue.get_job(JobName=job_name)["Job"]
    except Exception as e:
        rep.add("E. Glue job + scripts", "FAIL", f"job {job_name}",
                f"{type(e).__name__}: {e}\n"
                f"The job is created by Terraform (glue_jobs.tf). If it is "
                f"absent in this account, the gold stack has not been applied "
                f"here and there is nothing to start.")
        return

    cmd = job.get("Command", {})
    script = cmd.get("ScriptLocation", "")
    dargs = job.get("DefaultArguments", {})
    rep.add("E. Glue job + scripts", "PASS", f"job {job_name} exists",
            f"glue_version={job.get('GlueVersion')}  "
            f"worker={job.get('WorkerType')}x{job.get('NumberOfWorkers')}  "
            f"timeout={job.get('Timeout')}m\nscript={script}")

    # Required: getResolvedOptions() demands the first two, and the transform
    # raises on an empty --env (note the lowercase spelling — Terraform sets
    # "--env", not "--ENV").
    for key_name in ("--CONFIG_TABLE", "--METRICS_TABLE", "--env"):
        if dargs.get(key_name):
            rep.add("E. Glue job + scripts", "PASS",
                    f"default arg {key_name}={dargs[key_name]}")
        else:
            rep.add("E. Glue job + scripts", "FAIL",
                    f"default arg {key_name} is not set",
                    "The job fails at startup without it "
                    "(getResolvedOptions / the explicit --env guard).")

    # Runtime dependencies that fail late and confusingly when absent.
    expected = {
        "--datalake-formats": ("iceberg",
                               "Iceberg DDL/MERGE is unavailable without it"),
        "--additional-python-modules": ("pyyaml",
                                        "the transform does `import yaml` to "
                                        "read the mapping manifest"),
        "--extra-py-files": ("gold_cdc.py",
                             "gold_cdc is imported by the job"),
    }
    for key_name, (needle, why) in expected.items():
        val = dargs.get(key_name, "")
        if needle in val:
            rep.add("E. Glue job + scripts", "PASS",
                    f"default arg {key_name} includes {needle}")
        else:
            rep.add("E. Glue job + scripts", "FAIL",
                    f"default arg {key_name} does not include {needle}",
                    f"value={val or '(unset)'} — {why}.")

    # Script + library objects must exist, and should match this working tree.
    local = {
        script: RUNTIME_DIR / "generic_gold_transform.py",
    }
    for extra in (dargs.get("--extra-py-files") or "").split(","):
        if extra.strip():
            local[extra.strip()] = RUNTIME_DIR / Path(extra.strip()).name

    for uri, local_path in local.items():
        if not uri.startswith("s3://"):
            continue
        bucket, key = uri[5:].split("/", 1)
        etag, err = _s3_etag(s3, bucket, key)
        if err:
            rep.add("E. Glue job + scripts", "FAIL", f"script object {uri}",
                    f"{err}\nUpload with:\n"
                    f"  aws s3 sync stacks/echo/src/apps/pipeline/ "
                    f"s3://uif-echo-{env}-data/code/gold/ "
                    f"--exclude \"*.pyc\" --exclude \"__pycache__/*\"")
            continue
        if not local_path.exists():
            rep.add("E. Glue job + scripts", "WARN", f"script object {uri}",
                    f"present in S3; no local counterpart at {local_path} "
                    f"to compare against")
            continue
        md5 = hashlib.md5(local_path.read_bytes()).hexdigest()
        if md5 == etag:
            rep.add("E. Glue job + scripts", "PASS",
                    f"{Path(key).name} in S3 matches the working tree")
        else:
            rep.add("E. Glue job + scripts", "WARN",
                    f"{Path(key).name} in S3 DIFFERS from the working tree",
                    f"s3_etag={etag} local_md5={md5}\n"
                    f"Terraform tracks filemd5() but does not re-upload "
                    f"(domain CLAUDE.md). If the working tree is the intended "
                    f"version, re-upload before starting the run. "
                    f"(ETag is only an md5 for single-part uploads — a "
                    f"multipart upload mismatches spuriously.)")


def check_control_plane(rep, dynamo, env, cfg, table_name):
    gold_id = cfg["gold_id"]
    try:
        table = dynamo.Table(table_name)
        item = table.get_item(Key={"gold_id": gold_id,
                                   "version": "latest"}).get("Item")
    except Exception as e:
        rep.add("F. Control plane", "FAIL", f"config table {table_name}",
                f"{type(e).__name__}: {e}")
        return

    if item is None:
        rep.add("F. Control plane", "FAIL",
                f"no config item for gold_id={gold_id} in {table_name}",
                "The dispatcher scans this table; the transform reads its "
                "config from it. Write it with:\n"
                f"  py stacks/echo/src/apps/cli/sync_pipelines.py "
                f"--env {env} --apply")
        return

    rep.add("F. Control plane", "PASS",
            f"config item gold_id={gold_id} present in {table_name}")

    if item.get("enabled") is True:
        rep.add("F. Control plane", "WARN", "enabled=true",
                "The deployed dispatcher scans for enabled==true and starts "
                "the transform on its own cadence (schedule.frequency="
                f"{item.get('schedule', {}).get('frequency', 'gold')}). "
                "The load will therefore begin at the next EventBridge tick "
                "whether or not it is started by hand.")
    else:
        rep.add("F. Control plane", "WARN", "enabled is not true",
                "The dispatcher will skip this pipeline; only a manual "
                "start-job-run will move data.")

    wm = item.get("watermark") or {}
    rep.add("F. Control plane", "INFO", "bookmark (ADR-0020)",
            wm.get("bookmark") or "unset — the next run seeds it from the "
            "last successful run's start (UIFPE-522)")
    if "last_processed_silver_updated_at" in wm:
        rep.add("F. Control plane", "INFO",
                "retired per-source watermark map still on the row",
                "the next transform run REMOVEs it (UIFPE-522)")

    rep.add("F. Control plane", "INFO", "run history",
            f"last_run_id={(item.get('metadata') or {}).get('last_run_id')}  "
            f"last_run_at={(item.get('metadata') or {}).get('last_run_at')}")


def check_lake_formation(rep, session, env, sources, job_name):
    """Best-effort: can the shared Glue role actually read Silver?

    A newly registered Silver table is the classic first-run failure — the job
    starts, Spark resolves the table, and the read is denied. This section is
    WARN-only by design: an account using IAM-only access on these databases
    legitimately has no Lake Formation grants at all, so an absent grant is not
    proof of a problem.
    """
    section = "H. Lake Formation (best effort)"
    # Read the executor off the job rather than spelling a prefix here. The
    # gold jobs moved from the legacy echo-{env}-glue-role onto the standard
    # estate's, and a hand-spelled name silently checked the wrong identity.
    try:
        role_arn = session.client("glue").get_job(
            JobName=job_name)["Job"]["Role"]
        if not role_arn.startswith("arn:"):
            role_arn = session.client("iam").get_role(
                RoleName=role_arn)["Role"]["Arn"]
        role_name = role_arn.rsplit("/", 1)[-1]
        rep.add(section, "INFO", f"Glue execution role {role_name}",
                f"{role_arn}\n(the identity {job_name} actually runs as)")
    except Exception as e:
        rep.add(section, "WARN", f"could not resolve the executor of {job_name}",
                f"{type(e).__name__}: {e}\nSkipping the grant check — verify "
                f"Silver read access by other means before the first run.")
        return

    # ListPermissions rejects a Principal without a Resource, so the grants
    # have to be fetched per table rather than listed once and filtered.
    lf = session.client("lakeformation")

    for src in sources:
        db, tbl = src["silver_table"].split(".", 1)
        try:
            grants, kwargs = [], {
                "Principal": {"DataLakePrincipalIdentifier": role_arn},
                "Resource": {"Table": {"DatabaseName": db, "Name": tbl}},
            }
            while True:
                page = lf.list_permissions(**kwargs)
                grants.extend(page.get("PrincipalResourcePermissions", []))
                token = page.get("NextToken")
                if not token:
                    break
                kwargs["NextToken"] = token
        except Exception as e:
            rep.add(section, "WARN",
                    f"{src['alias']}: could not list grants for {db}.{tbl}",
                    f"{type(e).__name__}: {e}\nThe caller may lack "
                    f"lakeformation:ListPermissions. Not a blocker by itself.")
            continue

        matched = []
        for g in grants:
            matched.extend(g.get("Permissions", []))
        if any(p in matched for p in ("SELECT", "ALL")):
            rep.add(section, "PASS",
                    f"{src['alias']}: role holds {sorted(set(matched))} on "
                    f"{db}.{tbl}")
        else:
            rep.add(section, "WARN",
                    f"{src['alias']}: no SELECT grant found for {db}.{tbl}",
                    f"Either the databases are IAM-only (fine), or the grant "
                    f"is missing and the run will fail on read. Grant with:\n"
                    f"  aws lakeformation grant-permissions "
                    f"--principal DataLakePrincipalIdentifier={role_arn} "
                    f"--resource '{{\"Table\":{{\"DatabaseName\":\"{db}\","
                    f"\"Name\":\"{tbl}\"}}}}' "
                    f"--permissions SELECT DESCRIBE")


def check_target(rep, glue, cfg, env):
    db = cfg["target"]["database"].replace("{env}", env)
    tbl = cfg["target"]["table"].replace("{env}", env)
    try:
        glue.get_table(DatabaseName=db, Name=tbl)
        rep.add("G. Gold target", "INFO", f"{db}.{tbl} already exists",
                "This run takes the INCREMENTAL path (Phase 1 watermark scan "
                "→ Phase 2 rebuild of affected keys → MERGE). Use --rebuild "
                "only to deliberately replace the table.")
    except Exception:
        rep.add("G. Gold target", "INFO", f"{db}.{tbl} does not exist yet",
                "This run takes the INITIAL MATERIALIZATION path: CREATE "
                "TABLE from the full mapping SQL, then the watermark is set "
                "from the registered views. No change-log rows are written "
                "for the initial load.")


def main():
    ap = argparse.ArgumentParser(
        description="Read-only preflight for a Gold pipeline load")
    ap.add_argument("--env", default=DEFAULT_ENV, choices=ENVIRONMENTS)
    ap.add_argument("--gold-id", default="product")
    ap.add_argument("--profile", default=None,
                    help="AWS profile to use (e.g. pearson-esb-dev)")
    ap.add_argument("--config-table", default=None)
    ap.add_argument("--job-name", default=None,
                    help="Glue transform job name "
                         "(default: gold_resources.gold_transform_job(env))")
    ap.add_argument("--static", action="store_true",
                    help="Skip every AWS call; print the config + the columns "
                         "the mapping SQL requires, and stop")
    args = ap.parse_args()

    # The Windows console defaults to cp1252, which cannot encode the arrows
    # and rules used below. Never let formatting crash a validation run.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    rep = Report()

    # ── A: config ──────────────────────────────────────────────────────────
    desired = load_desired(args.env, REPO_ROOT)
    if args.gold_id not in desired:
        sys.exit(f"[FAIL] no pipeline '{args.gold_id}' for env={args.env} "
                 f"under {REPO_ROOT / 'pipelines'}")
    cfg = desired[args.gold_id]
    err = gold_config.validate(cfg)
    if err:
        rep.add("A. Config", "FAIL", "merged config is invalid", err)
    else:
        rep.add("A. Config", "PASS",
                f"merged base+{args.env} config is valid",
                f"target={cfg['target']['database'].replace('{env}', args.env)}"
                f".{cfg['target']['table']}  "
                f"pk={cfg['target']['primary_key_cols']}  "
                f"frequency={cfg['schedule']['frequency']}")

    sources = []
    for src in cfg["sources"]:
        s = dict(src)
        s["silver_table"] = s["silver_table"].replace("{env}", args.env)
        sources.append(s)
    for s in sources:
        rep.add("A. Config", "INFO",
                f"source {s['alias']} → {s['silver_table']}"
                + ("  [source of truth]" if s.get("is_source_of_truth") else ""))

    # ── B: contract ────────────────────────────────────────────────────────
    sql_path = (DOMAIN_ROOT / "src/schemas/mapping" / args.gold_id
                / cfg["schema"]["version"] / "sql" / f"{args.gold_id}.sql")
    if not sql_path.exists():
        rep.add("B. SQL contract", "FAIL", f"mapping SQL not found",
                str(sql_path))
        required, ambiguous = {}, []
    else:
        required, ambiguous = required_columns(
            sql_path.read_text(encoding="utf-8"),
            [s["alias"] for s in sources])
        for alias, cols in required.items():
            rep.add("B. SQL contract", "INFO",
                    f"{alias}: {len(cols)} column(s) required",
                    ", ".join(sorted(cols)) or
                    "none — this source is declared but never read by the SQL")
        for a in ambiguous:
            rep.add("B. SQL contract", "WARN",
                    "block reads more than one source; its unqualified "
                    "columns were not attributed", a)
        empty = [a for a, c in required.items() if not c]
        if empty:
            rep.add("B. SQL contract", "WARN",
                    f"declared but unread source(s): {empty}",
                    "A source declared in the pipeline is still read by "
                    "spark.table() and still gates the run, even when the "
                    "mapping SQL never references its alias.")

    if args.static:
        rep.print()
        print("\nStatic mode — no AWS calls were made.")
        return 1 if rep.has_failures() else 0

    # ── C–G: AWS ───────────────────────────────────────────────────────────
    import boto3
    session = (boto3.Session(profile_name=args.profile, region_name=REGION)
               if args.profile else boto3.Session(region_name=REGION))
    try:
        ident = session.client("sts").get_caller_identity()
        rep.add("A. Config", "PASS", "AWS credentials resolved",
                f"account={ident['Account']}  region={REGION}\n"
                f"arn={ident['Arn']}")
    except Exception as e:
        print(f"\n[FAIL] AWS credentials are not usable: "
              f"{type(e).__name__}: {e}")
        print("Refresh them, then re-run. Static checks only: --static")
        return 1

    glue = session.client("glue")
    s3 = session.client("s3")
    dynamo = session.resource("dynamodb")

    check_silver(rep, glue, sources, required)
    check_artifacts(rep, s3, cfg)
    check_job(rep, glue, s3, args.env,
              args.job_name or gold_resources.gold_transform_job(args.env))
    check_control_plane(rep, dynamo, args.env, cfg,
                        args.config_table
                        or gold_config.default_gold_config_table(args.env))
    check_lake_formation(rep, session, args.env, sources,
                         args.job_name
                         or gold_resources.gold_transform_job(args.env))
    check_target(rep, glue, cfg, args.env)

    rep.print()
    if rep.has_failures():
        print("\nAt least one check FAILED — do not start the load until the "
              "failures above are resolved.")
        return 1
    print("\nAll checks passed. Review the warnings, then start the load.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
