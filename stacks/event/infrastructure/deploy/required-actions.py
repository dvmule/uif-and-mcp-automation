#!/usr/bin/env python3
"""Derive the IAM policy an event-stack deploy needs, from the Terraform itself.

Same principle as stacks/echo/infrastructure/deploy/required-actions.py, and
for the same reason: a deploy policy written by hand and then "verified" with
checks drawn from that same policy confirms nothing. Here the action list is
derived from every `resource` and `data` block in ../terraform/*.tf AND in
../terraform/modules/event-domain/*.tf, looked up in terraform-iam-map.json.

AN UNMAPPED TYPE IS A HARD FAILURE. Adding a resource to the stack (or to the
domain module) without deciding its permissions breaks this script rather than
quietly producing a policy that cannot apply.

Simpler than echo's in one respect: the event stack has ONE name prefix,
uif-event-{env}, in every environment (locals.tf event_stack_prefix). No
legacy stack, no underscore catalog prefix, no eds- prod.

Four statements are NOT derivable from the Terraform, because the resources
they reach are not declared in it. They are added here, explicitly:

  backend           the state bucket uif-event-{env}-data (backends/*.s3.tfbackend),
                    created by bootstrap-environment.sh, never by Terraform
  release_copy      object writes into this environment's release bucket, done
                    by copy-artifacts.sh (a CopyObject), not by Terraform
  promotion_source  READ of uif-event-dev-release-artifacts -- the bucket a
                    promoted environment copies FROM. Echo's plan records this
                    as "the one grant the derivation cannot predict": its first
                    uat converge passed 225/225 simulated actions and then died
                    at the copy. Skipped for dev, which is the source.
  config_seeds      ITEM writes into the two config tables. This root declares
                    those tables, so the derivation grants table-level actions
                    only -- the rows are aws_dynamodb_table_item in a DOMAIN's
                    Terraform, which this root cannot see (UIFPE-774).

Usage:
  required-actions.py --env uat --account 283556840805 --mode policies --out-dir DIR
  required-actions.py --env uat --account 283556840805 --mode batches

  policies  writes policy-1.json [, policy-2.json ...], each within IAM's
            6,144-character managed-policy limit, and prints their paths
  batches   one TAB-separated line per statement: sid, actions (comma), ARNs
            (comma) -- what bootstrap-environment.sh feeds to
            simulate-principal-policy, one call per statement
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAP_FILE = HERE / "terraform-iam-map.json"
TF_DIRS = (HERE.parent / "terraform", HERE.parent / "terraform" / "modules" / "event-domain")
NON_AWS_SCOPES = {"_local"}
# IAM counts a managed policy's size without whitespace; stay under with margin.
MAX_POLICY_CHARS = 6000

BLOCK = re.compile(r'^(resource|data)\s+"([A-Za-z0-9_]+)"\s+"[A-Za-z0-9_]+"\s*\{', re.M)


def tf_types(dirs=TF_DIRS):
    resources, data = set(), set()
    for d in dirs:
        for tf in sorted(d.glob("*.tf")):
            for kind, typ in BLOCK.findall(tf.read_text(encoding="utf-8")):
                (resources if kind == "resource" else data).add(typ)
    return resources, data


def subst(template, env, account, region):
    return (template.replace("{prefix}", f"uif-event-{env}")
                    .replace("{env}", env)
                    .replace("{account}", account)
                    .replace("{region}", region))


def statements(env, account, region):
    m = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    scopes = m["_scopes"]
    resources, data = tf_types()

    unmapped = sorted({f"resource {t}" for t in resources if t not in m["resources"]}
                      | {f"data {t}" for t in data if t not in m["data"]})
    if unmapped:
        sys.exit("required-actions.py: UNMAPPED Terraform types -- decide their "
                 "permissions in terraform-iam-map.json:\n  " + "\n  ".join(unmapped))

    by_scope = {}

    def add(scope, actions):
        if scope in NON_AWS_SCOPES or not actions:
            return
        if scope not in scopes:
            sys.exit(f"required-actions.py: scope '{scope}' is not defined in _scopes")
        by_scope.setdefault(scope, set()).update(actions)

    for table, used in (("resources", resources), ("data", data)):
        for typ in sorted(used):
            e = m[table][typ]
            add(e["scope"], e.get("actions", []))
            if e.get("extra_scope"):
                add(e["extra_scope"], e.get("extra_actions", []))

    out = []
    for scope in sorted(by_scope):
        arns = [subst(a, env, account, region) for a in scopes[scope]["arns"]]
        out.append({"Sid": re.sub(r"[^A-Za-z0-9]", "", scope.title()),
                    "Effect": "Allow", "Action": sorted(by_scope[scope]), "Resource": arns})

    # ── The four that no Terraform declares (see the module docstring) ───────
    # dev has no state bucket of its own: backends/dev.s3.tfbackend points at
    # pearson-ingestion-framework-tfstate-283556840805, the bucket the product
    # domain and beacon share. The shared dev role already reaches it -- every
    # deploy-event-dev.yml run inits against it -- so claiming it from EVENT's
    # policy would duplicate another owner's grant and assert that this stack
    # owns estate infrastructure it does not. uat and prod do own theirs.
    if env != "dev":
        data_bucket = f"uif-event-{env}-data"
        out.append({"Sid": "Backend", "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketVersioning"],
                    "Resource": [f"arn:aws:s3:::{data_bucket}"]})
        out.append({"Sid": "BackendState", "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    "Resource": [f"arn:aws:s3:::{data_bucket}/tfstate/*"]})
    release = f"uif-event-{env}-release-artifacts"
    out.append({"Sid": "ReleaseCopy", "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion",
                           "s3:PutObjectTagging", "s3:ListBucket", "s3:ListBucketVersions",
                           "s3:GetBucketVersioning"],
                "Resource": [f"arn:aws:s3:::{release}", f"arn:aws:s3:::{release}/*"]})
    if env != "dev":
        src = "uif-event-dev-release-artifacts"
        # GetObject*Tagging: CopyObject copies tags by default and reads them
        # from the source version. Inferred, not yet observed failing -- the
        # first real copy will say if it is superfluous.
        out.append({"Sid": "PromotionSource", "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:ListBucketVersions",
                               "s3:GetObject", "s3:GetObjectVersion",
                               "s3:GetObjectTagging", "s3:GetObjectVersionTagging"],
                    "Resource": [f"arn:aws:s3:::{src}", f"arn:aws:s3:::{src}/*"]})

    # A domain seeds its own pipeline and subscription rows into the event
    # stack's two config tables, as aws_dynamodb_table_item in that domain's
    # Terraform. This root declares the TABLES, so the loop above grants
    # table-level actions and nothing item-level. Scoped to those two tables
    # rather than the whole prefix: a deploy role has no business writing rows
    # into a metrics table. Every environment -- dev's rows predate this
    # policy, so a dev apply that had to recreate one would fail as uat did.
    seeded = [f"arn:aws:dynamodb:{region}:{account}:table/uif-event-{env}-{name}"
              for name in ("pipeline-configs", "subscription-configs")]
    out.append({"Sid": "ConfigSeeds", "Effect": "Allow",
                "Action": ["dynamodb:DeleteItem", "dynamodb:GetItem",
                           "dynamodb:PutItem", "dynamodb:UpdateItem"],
                "Resource": seeded})
    return out


def pack(stmts):
    """Greedy-pack statements into as few documents as fit the size limit."""
    docs, cur = [], []
    for s in stmts:
        trial = cur + [s]
        if len(json.dumps({"Version": "2012-10-17", "Statement": trial},
                          separators=(",", ":"))) > MAX_POLICY_CHARS and cur:
            docs.append(cur)
            cur = [s]
        else:
            cur = trial
    if cur:
        docs.append(cur)
    return [{"Version": "2012-10-17", "Statement": d} for d in docs]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, choices=["dev", "uat", "prod"])
    ap.add_argument("--account", required=True)
    ap.add_argument("--region", default="eu-west-1")
    ap.add_argument("--mode", required=True, choices=["policies", "batches"])
    ap.add_argument("--out-dir")
    a = ap.parse_args()

    if not re.fullmatch(r"\d{12}", a.account):
        sys.exit("required-actions.py: --account must be a 12-digit AWS account id")

    stmts = statements(a.env, a.account, a.region)
    if a.mode == "batches":
        for s in stmts:
            print(f"{s['Sid']}\t{','.join(s['Action'])}\t{','.join(s['Resource'])}")
        return

    if not a.out_dir:
        sys.exit("required-actions.py: --mode policies needs --out-dir")
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for i, doc in enumerate(pack(stmts), start=1):
        p = out / f"policy-{i}.json"
        p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(p)


if __name__ == "__main__":
    main()
