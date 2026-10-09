#!/usr/bin/env python3
"""Derive the IAM actions an Echo apply needs, from the Terraform itself.

Ported from stacks/apicurio/infrastructure/deploy/required-actions.py, which
was written after a lesson worth restating: a per-stack deploy role was created
holding only the tracked delta policy, and the bootstrap's hand-picked simulate
checks all passed -- because those checks were drawn from that same policy. A
verification built from the thing it verifies confirms nothing. The role could
not have applied the stack at all.

So the action list is derived from every `resource` and `data` block in
../terraform/*.tf, looked up in terraform-iam-map.json.

AN UNMAPPED TYPE IS A HARD FAILURE. That is the property that matters: adding a
resource to the stack without deciding its permissions breaks this script rather
than quietly producing a policy that cannot apply.

WHAT DIFFERS FROM APICURIO'S, AND WHY IT HAD TO. Apicurio's prefix is derivable
from its environment -- every resource is uif-apicurio-{env}-something, so
substituting {env} into an ARN template is enough. Echo's is not, and cannot be:

    env        name_prefix     glue_db_prefix    legacy_name_prefix
    dev        echo-dev        echo_dev          echo-dev
    dev-uif    uif-echo-dev    uif_echo_dev      uif-echo-dev
    uat        echo-uat        echo_uat          echo-uat
    uat-uif    uif-echo-uat    uif_echo_uat      uif-echo-uat
    prod       eds-prod        eds_prod          eds-prod

Three separate prefixes, none of them a formula over the environment name, and
prod is on `eds-` because it predates the rename. A Glue database cannot contain
a hyphen, so the catalog prefix is underscore-delimited; DynamoDB tables hold
data and sit on legacy_name_prefix during a phased rename. A policy that assumed
one prefix would silently grant nothing for the other two.

src/libs/stack_naming.py is the single authority for those names -- stacks/echo/
CLAUDE.md lists it among the three choke points that must change together -- so
this reads them from there rather than re-deriving them. The alternative is a
fourth place that knows Echo's naming, which is how the CLI scripts came to
hardcode `eds-{env}-*` and be right for prod and wrong for dev and uat.

Substitution tokens available to terraform-iam-map.json:
  {prefix}        name_prefix         hyphen form: jobs, lambdas, roles, buckets
  {db_prefix}     glue_db_prefix      underscore form: Glue catalog databases
  {legacy_prefix} legacy_name_prefix  data-bearing: DynamoDB tables
  {env}           environment         dev | uat | prod, for secret paths
  {account}       account id
  {region}        region

Three outputs:
  --mode policy   the base policy document, to attach alongside the delta
  --mode actions  TAB-separated action/ARNs pairs, one line per action
  --mode batches  TAB-separated scope/actions/ARNs, one line per SCOPE -- what
                  the bootstrap uses, because simulate-principal-policy takes
                  many actions and many resources in ONE call. Per-action calls
                  meant hundreds of sequential round trips for apicurio; batched
                  it is one call per scope.

Scope tokens `_in_delta`, `_skip` and `_local` contribute nothing, each for a
recorded reason: already granted by another entry's scope, count=0 in every
environment, or evaluated by Terraform without an AWS call.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAP_FILE = HERE / "terraform-iam-map.json"
TF_DIRS = (HERE.parent / "terraform", HERE.parent / "terraform-gold")
LIBS_DIR = HERE.parent.parent / "src" / "libs"

NON_AWS_SCOPES = {"_in_delta", "_skip", "_local"}


def tf_types(tf_dirs: tuple[Path, ...]) -> tuple[set[str], set[str]]:
    """Every resource and data type the stack declares, across BOTH roots.

    BOTH, not just the top-level one. This read "Only the top-level root.
    infrastructure/terraform-gold is a separate release unit with its own
    deploy path and would need its own map -- the same boundary qe/ci.sh
    draws." That premise was reversed on 2026-09-24 (UIFPE-576): gold folds
    into echo/v* as ONE release unit, and qe/ci.sh no longer draws that
    boundary either.

    It mattered more here than anywhere else it was encoded. A deploy policy
    derived from one root grants nothing for the other root's resources, and
    a policy that grants nothing reads exactly like a policy that works --
    until the apply fails on it. 11 resource types and 1 data type exist only
    in the gold root.
    """
    resources: set[str] = set()
    data: set[str] = set()
    for tf_dir in tf_dirs:
        files = sorted(tf_dir.glob("*.tf"))
        if not files:
            sys.exit(f"error: no .tf files under {tf_dir}")
        for f in files:
            src = f.read_text(encoding="utf-8")
            resources |= set(re.findall(r'^resource\s+"([a-z0-9_]+)"', src, re.M))
            data |= set(re.findall(r'^data\s+"([a-z0-9_]+)"', src, re.M))
    return resources, data


def prefixes(env: str) -> dict[str, str]:
    """Resolve Echo's three name prefixes for an environment, via stack_naming.

    Imported lazily and by path, because this script lives under
    infrastructure/deploy while the library lives under src/libs, and nothing
    puts the latter on sys.path outside the test suite's conftest.
    """
    sys.path.insert(0, str(LIBS_DIR))
    try:
        import stack_naming
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        sys.exit(f"error: cannot import stack_naming from {LIBS_DIR}: {exc}")

    try:
        names = stack_naming.load(env)
    except ValueError as exc:
        sys.exit(f"error: {exc}")

    return {
        "prefix": names.name_prefix,
        "db_prefix": names.glue_db_prefix,
        "legacy_prefix": names.legacy_name_prefix,
        "env": names.environment,
    }


def resolve(arns: list[str], subs: dict[str, str]) -> list[str]:
    out = []
    for arn in arns:
        rendered = arn
        for token, value in subs.items():
            rendered = rendered.replace("{" + token + "}", value)
        # A template with an unresolved token would produce an ARN matching
        # nothing, and a policy that grants nothing reads exactly like a policy
        # that works until the apply fails on it.
        left = re.findall(r"\{[a-z_]+\}", rendered)
        if left:
            sys.exit(
                f"error: ARN template '{arn}' still contains {left} after "
                f"substitution. Known tokens: {sorted(subs)}."
            )
        out.append(rendered)
    return out


def collect(env: str, account: str, region: str):
    spec = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    scopes = spec["_scopes"]
    resources, data = tf_types(TF_DIRS)

    # THE STALENESS GUARD. Both directions are checked: an unmapped type means
    # the stack grew without a permissions decision, and a mapped type that no
    # longer exists means the map is carrying a grant for nothing -- quieter,
    # but still a wrong policy.
    missing_r = sorted(resources - set(spec["resources"]))
    missing_d = sorted(data - set(spec["data"]))
    if missing_r or missing_d:
        lines = ["error: terraform declares types that terraform-iam-map.json does not map."]
        lines += [f"  resource {t}" for t in missing_r]
        lines += [f"  data     {t}" for t in missing_d]
        lines += [
            "",
            "  Add each with the actions an apply of it calls and the scope token they",
            "  apply to. Refusing to emit a policy that would be missing them: that is",
            "  exactly how a role gets created that cannot apply the stack.",
        ]
        sys.exit("\n".join(lines))

    # aws_caller_identity is mapped without being declared in the root, on
    # purpose -- the deploy scripts and the bootstrap call GetCallerIdentity.
    # The map says so at the entry; anything else stale is reported.
    for t in sorted(set(spec["resources"]) - resources):
        print(f"warning: map entry 'resource {t}' matches nothing in the terraform", file=sys.stderr)
    for t in sorted(set(spec["data"]) - data - {"aws_caller_identity"}):
        print(f"warning: map entry 'data {t}' matches nothing in the terraform", file=sys.stderr)

    subs = prefixes(env)
    subs["account"] = account
    subs["region"] = region

    by_scope: dict[str, set[str]] = {}
    for section in ("resources", "data"):
        universe = resources if section == "resources" else data
        for name, entry in spec[section].items():
            if name not in universe and name != "aws_caller_identity":
                continue
            scope = entry["scope"]
            if scope in NON_AWS_SCOPES:
                continue
            if scope not in scopes:
                sys.exit(f"error: {name} names scope '{scope}', which _scopes does not define")
            by_scope.setdefault(scope, set()).update(entry.get("actions", []))

            # extra_scope receives TWO kinds of action, and the distinction is
            # why this is not just one list.
            #
            #   unscopable_actions  calls this resource type makes that take no
            #                       resource at all -- list-shaped APIs, creates
            #                       whose id does not exist yet. They go to an
            #                       unscopable scope, which is ["*"].
            #   extra_actions       CROSS-SERVICE calls that are perfectly
            #                       scopable, just to a different ARN than this
            #                       resource's own. iam:PassRole belongs on the
            #                       ROLE being passed, not on the Lambda passing
            #                       it; a layer published from S3 needs
            #                       s3:GetObject on the OBJECT, not the layer.
            #
            # Added because the ported format had only the first, so every
            # cross-service action had to sit in `actions` and was emitted
            # against the wrong ARNs. That produced a policy granting
            # iam:PassRole on arn:...:function:uif-echo-uat-* -- which matches no
            # role, so CreateFunction would fail with an error naming the role
            # and nothing naming the real cause. Four entries were wrong this
            # way before the split existed.
            # `extra_actions` ALSO TAKES A MAP, {scope: [actions]}, for the entry
            # that needs TWO destinations. Eighteen entries use an extra_scope
            # and for a long time none needed more than one, so a single slot was
            # enough. aws_lakeformation_resource is the first that is not:
            # RegisterResource passes a role (iam_stack_roles) AND reads Lake
            # Formation's service-linked role, which lives under
            # role/aws-service-role/ and is outside role/{prefix}-*.
            #
            # The alternative was adding that ARN to iam_stack_roles, whose own
            # `why` promises it "cannot touch any other role in the account" --
            # so the one-line fix would have made the map's documentation false
            # and granted PassRole on a service-linked role for good measure.
            #
            # The list form is unchanged and still uses extra_scope. Only the map
            # form names its own scopes, and then extra_scope governs
            # unscopable_actions alone.
            extra = entry.get("extra_scope")
            if extra is not None and extra not in scopes:
                sys.exit(
                    f"error: {name} names extra_scope '{extra}', which _scopes does not define"
                )

            routed: list[tuple[str, list[str], str]] = []
            unscopable = entry.get("unscopable_actions", [])
            if unscopable:
                if not extra:
                    sys.exit(
                        f"error: {name} lists unscopable_actions but no extra_scope to put them in"
                    )
                routed.append((extra, unscopable, "unscopable_actions"))

            cross = entry.get("extra_actions", [])
            if isinstance(cross, dict):
                for token, actions in cross.items():
                    if not actions:
                        continue
                    if token not in scopes:
                        sys.exit(
                            f"error: {name} routes extra_actions to '{token}', which "
                            f"_scopes does not define"
                        )
                    routed.append((token, actions, "extra_actions"))
            elif cross:
                if not extra:
                    sys.exit(
                        f"error: {name} lists extra_actions but no extra_scope to put them in"
                    )
                routed.append((extra, cross, "extra_actions"))

            for token, actions, field in routed:
                if field == "unscopable_actions" and scopes[token].get("arns") != ["*"]:
                    sys.exit(
                        f"error: {name} routes unscopable_actions to '{token}', which is a "
                        f"SCOPED token. An action that takes no resource cannot be granted on "
                        f"specific ARNs -- either it is scopable and belongs in extra_actions, "
                        f"or extra_scope should name an unscopable token."
                    )
                by_scope.setdefault(token, set()).update(actions)

    return scopes, by_scope, subs


def main() -> None:
    # LF ONLY, on every platform. Python's stdout is a text stream with
    # newline=None, so it translates its line terminator to os.linesep --
    # which on Windows means every line this emits ends CR LF.
    #
    # The bootstrap reads --mode batches with a tab-separated `read scope
    # actions arns`, and the carriage return then rides along on the LAST
    # field. arns became the wildcard followed by CR, which AWS rejected as
    # "ResourceNames are not in a valid ARN format", and the same CR on a
    # decision made the equality test against "allowed" false for 169
    # actions that were every one of them allowed. One character, two
    # symptoms, and the second looked exactly like a permissions problem.
    #
    # Fixed here rather than only stripped in the consumer, so any other
    # caller of this script gets clean output too.
    sys.stdout.reconfigure(newline=chr(10))


    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=["policy", "policies", "batches", "actions"], required=True)
    ap.add_argument(
        "--limit",
        type=int,
        default=6144,
        help="IAM's managed-policy character limit, counted on the COMPACT document. "
        "Only --mode policies uses it.",
    )
    ap.add_argument(
        "--env",
        required=True,
        help="tfvars environment name: dev, dev-uif, uat, uat-uif or prod. NOT the "
        "bare environment -- uat and uat-uif are different stacks with different "
        "prefixes and therefore different policies.",
    )
    ap.add_argument("--account", required=True)
    ap.add_argument("--region", default="eu-west-1")
    args = ap.parse_args()

    scopes, by_scope, subs = collect(args.env, args.account, args.region)

    def statement(scope: str) -> dict:
        return {
            # Sid from the scope token, so a denial in CloudTrail points back at
            # the map entry that granted it.
            "Sid": "".join(p.capitalize() for p in scope.split("_")),
            "Effect": "Allow",
            "Action": sorted(by_scope[scope]),
            "Resource": resolve(scopes[scope]["arns"], subs),
        }

    def compact(doc: dict) -> int:
        """IAM counts the document with whitespace removed."""
        return len(json.dumps(doc, separators=(",", ":")))

    if args.mode == "policy":
        statements = [statement(s) for s in sorted(by_scope) if by_scope[s]]
        print(json.dumps({"Version": "2012-10-17", "Statement": statements}, indent=2))

    elif args.mode == "policies":
        # ECHO'S POLICY DOES NOT FIT IN ONE MANAGED POLICY, and that is a fact
        # about the stack rather than a problem with the derivation: 23 scopes,
        # 183 actions, 8187 compact characters against IAM's 6144 limit. Echo
        # spans Glue, Lake Formation, DynamoDB, S3, Lambda, EventBridge, API
        # Gateway, CloudFront, WAFv2, logs, IAM and EC2, where apicurio's fits
        # because it spans fewer services.
        #
        # An inline role policy would fit (10240), and is the wrong answer: an
        # inline policy has no versions, so there is nothing to roll back to
        # when a round of the apply-and-fix loop makes it worse.
        #
        # So the statements are PACKED into as few managed policies as fit.
        # Greedy, largest-first, which keeps the count minimal without needing
        # to be optimal -- a role may attach ten managed policies by default.
        # Packed rather than split by service on purpose: a hand-drawn boundary
        # would have to be redrawn every time the policy grows, and this does
        # not.
        ordered = sorted(
            (s for s in by_scope if by_scope[s]),
            key=lambda s: -compact(statement(s)),
        )
        empty = compact({"Version": "2012-10-17", "Statement": []})
        parts: list[list[dict]] = []
        sizes: list[int] = []
        for scope in ordered:
            st = statement(scope)
            size = compact(st) + 1  # the comma joining it to its neighbour
            if size + empty > args.limit:
                sys.exit(
                    f"error: the statement for scope '{scope}' is {size} compact "
                    f"characters and cannot fit in a {args.limit}-character policy "
                    f"on its own. Narrow its action list or split the scope."
                )
            for i, used in enumerate(sizes):
                if used + size <= args.limit:
                    parts[i].append(st)
                    sizes[i] = used + size
                    break
            else:
                parts.append([st])
                sizes.append(empty + size)

        # Sid order within a document is cosmetic, but a stable one keeps the
        # committed documents diffable across runs.
        docs = [
            {"Version": "2012-10-17", "Statement": sorted(p, key=lambda s: s["Sid"])}
            for p in parts
        ]
        print(json.dumps(docs, indent=2))

    elif args.mode == "actions":
        for scope in sorted(by_scope):
            arns = ",".join(resolve(scopes[scope]["arns"], subs))
            for action in sorted(by_scope[scope]):
                print(f"{action}\t{arns}")

    else:  # batches
        # One line per scope: every action, every ARN, for a single
        # simulate-principal-policy call.
        for scope in sorted(by_scope):
            actions = sorted(by_scope[scope])
            if not actions:
                continue
            arns = resolve(scopes[scope]["arns"], subs)
            print(f"{scope}\t{','.join(actions)}\t{','.join(arns)}")


if __name__ == "__main__":
    main()
