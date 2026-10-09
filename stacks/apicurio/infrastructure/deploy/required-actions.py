#!/usr/bin/env python3
"""Derive the IAM actions an apicurio apply needs, from the Terraform itself.

Written after a per-stack deploy role was created holding only
uif-apicurio-<env>-deploy.policy.json, and the bootstrap's six hand-picked
simulate checks all passed -- because those six were drawn from that same
policy. A verification built from the thing it verifies confirms nothing. The
role could not have applied the stack: the tracked policy is a DELTA on the
shared echo role's base, and a new role has no base.

So the action list is derived here from every `resource` and `data` block in
../terraform/*.tf, looked up in terraform-iam-map.json.

AN UNMAPPED TYPE IS A HARD FAILURE. That is the property that matters: adding a
resource to the stack without deciding its permissions breaks this script
rather than quietly producing a policy that cannot apply.

Three outputs:
  --mode policy   the base policy document, to attach alongside the delta
  --mode actions  TAB-separated action/ARNs pairs, one line per action
  --mode batches  TAB-separated scope/actions/ARNs, one line per SCOPE -- what
                  the bootstrap uses, because simulate-principal-policy takes
                  many actions and many resources in ONE call. Per-action calls
                  meant ~500 sequential round trips and a run that took over ten
                  minutes; batched it is one call per scope.

Scope tokens `_in_delta`, `_skip` and `_local` contribute nothing, each for a
recorded reason: already granted by the delta policy, count=0 in every
environment, or evaluated by Terraform without an AWS call.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAP_FILE = HERE / "terraform-iam-map.json"
TF_DIR = HERE.parent / "terraform"

NON_AWS_SCOPES = {"_in_delta", "_skip", "_local"}


def tf_types(tf_dir: Path) -> tuple[set[str], set[str]]:
    """Every resource and data type the stack declares."""
    resources: set[str] = set()
    data: set[str] = set()
    files = sorted(tf_dir.glob("*.tf"))
    if not files:
        sys.exit(f"error: no .tf files under {tf_dir}")
    for f in files:
        src = f.read_text()
        resources |= set(re.findall(r'^resource\s+"([a-z0-9_]+)"', src, re.M))
        data |= set(re.findall(r'^data\s+"([a-z0-9_]+)"', src, re.M))
    return resources, data


def resolve(arns: list[str], env: str, account: str, region: str) -> list[str]:
    return [
        a.replace("{env}", env).replace("{account}", account).replace("{region}", region)
        for a in arns
    ]


def collect(env: str, account: str, region: str):
    spec = json.loads(MAP_FILE.read_text())
    scopes = spec["_scopes"]
    resources, data = tf_types(TF_DIR)

    # THE STALENESS GUARD. Both directions are checked: an unmapped type means
    # the stack grew without a permissions decision, and a mapped type that no
    # longer exists means the map is carrying a grant for nothing -- which is a
    # quieter problem but still a wrong policy.
    missing_r = sorted(resources - set(spec["resources"]))
    missing_d = sorted(data - set(spec["data"]))
    if missing_r or missing_d:
        lines = ["error: terraform declares types that terraform-iam-map.json does not map."]
        for t in missing_r:
            lines.append(f"  resource {t}")
        for t in missing_d:
            lines.append(f"  data     {t}")
        lines.append("")
        lines.append("  Add each with the actions an apply of it calls and the scope token they")
        lines.append("  apply to. Refusing to emit a policy that would be missing them: that is")
        lines.append("  exactly how a role gets created that cannot apply the stack.")
        sys.exit("\n".join(lines))

    stale_r = sorted(set(spec["resources"]) - resources)
    stale_d = sorted(set(spec["data"]) - data)
    if stale_r or stale_d:
        for t in stale_r:
            print(f"warning: map entry 'resource {t}' matches nothing in the terraform", file=sys.stderr)
        for t in stale_d:
            print(f"warning: map entry 'data {t}' matches nothing in the terraform", file=sys.stderr)

    # scope token -> set of actions
    by_scope: dict[str, set[str]] = {}
    for section in ("resources", "data"):
        for name, entry in spec[section].items():
            if name not in (resources if section == "resources" else data):
                continue
            scope = entry["scope"]
            if scope in NON_AWS_SCOPES:
                continue
            if scope not in scopes:
                sys.exit(f"error: {name} names scope '{scope}', which _scopes does not define")
            by_scope.setdefault(scope, set()).update(entry.get("actions", []))
            extra = entry.get("extra_scope")
            unscoped = entry.get("unscopable_actions", [])
            if unscoped:
                if not extra:
                    sys.exit(
                        f"error: {name} lists unscopable_actions but no extra_scope to put them in"
                    )
                if extra not in scopes:
                    sys.exit(f"error: {name} names extra_scope '{extra}', which _scopes does not define")
                by_scope.setdefault(extra, set()).update(unscoped)

    return spec, scopes, by_scope


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["policy", "actions", "batches"], required=True)
    ap.add_argument("--env", required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--region", default="eu-west-1")
    args = ap.parse_args()

    _, scopes, by_scope = collect(args.env, args.account, args.region)

    if args.mode == "policy":
        statements = []
        for scope in sorted(by_scope):
            actions = sorted(by_scope[scope])
            if not actions:
                continue
            statements.append(
                {
                    # Sid from the scope token, so a denial in CloudTrail points
                    # back at the map entry that granted it.
                    "Sid": "".join(p.capitalize() for p in scope.split("_")),
                    "Effect": "Allow",
                    "Action": actions,
                    "Resource": resolve(scopes[scope]["arns"], args.env, args.account, args.region),
                }
            )
        print(json.dumps({"Version": "2012-10-17", "Statement": statements}, indent=2))
    elif args.mode == "batches":
        # One line per scope: every action, every ARN, for a single API call.
        for scope in sorted(by_scope):
            actions = sorted(by_scope[scope])
            if not actions:
                continue
            arns = resolve(scopes[scope]["arns"], args.env, args.account, args.region)
            print(f"{scope}\t{','.join(actions)}\t{','.join(arns)}")
    else:
        # One line per action, carrying EVERY ARN in its scope, comma-separated.
        #
        # It used to emit only the first, on the reasoning that a scope's ARNs
        # "differ only in resource type, not in whether the grant applies".
        # That is backwards: resource type is precisely what IAM matches an
        # action against. ecs:CreateService evaluated against a cluster ARN is
        # an implicitDeny however fully it is granted on service/*, and the
        # first uat run reported 11 such denials -- every one of them the
        # simulation being wrong rather than the policy.
        #
        # The caller tries each ARN and counts the action allowed if any one
        # is. Within a statement every action is granted on every resource, so
        # "allowed on at least one shape" is exactly the question "is the grant
        # present".
        for scope in sorted(by_scope):
            arns = ",".join(resolve(scopes[scope]["arns"], args.env, args.account, args.region))
            for action in sorted(by_scope[scope]):
                print(f"{action}\t{arns}")


if __name__ == "__main__":
    main()
