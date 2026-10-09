#!/usr/bin/env bash
# Bootstrap everything a uif-echo environment needs BEFORE it can be provisioned
# or promoted to.
#
# Run this by hand, once per environment, before the first `terraform apply` for
# that environment and before any workflow names it. Nothing in CI calls it:
# every action here is a one-time privileged change (IAM, repository settings)
# that should be made deliberately by a person who can read the result.
#
# WHAT IT FULFILS, and why each item blocks an environment rather than merely
# improving it:
#
#   1. The deploy policy, env-scoped and DERIVED. required-actions.py builds it
#      from the Terraform itself, so it cannot drift from what an apply needs.
#      Same account does not mean same permissions: every ARN in it is pinned to
#      this stack's prefix, so a uat role cannot touch dev's resources even
#      though both live in 283556840805.
#
#   2. The GitHub Environment, with a reviewer and a tag policy. Referencing
#      `environment: uat` from a workflow before the environment exists makes
#      GitHub create it implicitly with NO protection -- no reviewer, no policy.
#      That is how "restricted release authority" becomes a sentence in a
#      document. Creating it first, deliberately, is the only ordering that
#      works.
#
#   3. The deploy role's ARN as a secret, environment-scoped. For uat that is
#      AWS_ROLE_ARN_ECHO_UAT -- stack-qualified, because the unqualified
#      AWS_ROLE_ARN_UAT is product's and scoping it to this environment shadows
#      theirs. dev keeps the shared AWS_ROLE_ARN_DEV. See the derivation of
#      SECRET_NAME for the full reasoning; it is not a style choice.
#
#   4. A report on the Terraform backend configuration, which it does NOT
#      create. See that step for why fabricating it would be worse than leaving
#      it absent.
#
# WHAT IT REFUSES TO INVENT. It will not name a reviewer, it will not guess an
# IAM role outside dev, and it will not write network or classification values.
# Each is a decision with an owner, and a bootstrap that silently picks one
# produces an environment nobody agreed to.
#
# SIX STEPS, NOT APICURIO'S SEVEN. Apicurio splits its policy into a
# hand-written delta plus a derived base, because a delta document already
# existed and is compared for exact equality against the derivation. Echo has no
# such legacy document: the derived policy IS the policy. Carrying the split
# would mean maintaining an empty delta forever to look like the stack it was
# copied from.
#
# Usage:
#   ./bootstrap-environment.sh <tfvars-env> [options]
#
#   <tfvars-env>              dev-uif | uat-uif | dev | uat | prod
#                             THE TFVARS NAME, not the GitHub environment. Echo
#                             runs two stacks per environment during the
#                             migration -- uat is echo-uat-*, uat-uif is
#                             uif-echo-uat-* -- and they need different
#                             policies, so the policy follows the tfvars file.
#                             The GitHub Environment is derived by dropping
#                             `-uif`, because GitHub has one `uat` and both
#                             stacks deploy through it.
#   --role <name>             IAM role the policy attaches to. Required outside
#                             dev, where it defaults to the shared
#                             echo-dev-github-actions.
#   --reviewer <login|org/team>
#                             Required approver for the environment. Required
#                             outside dev. Repeatable.
#   --role-arn <arn>          Value for the role-ARN secret -- AWS_ROLE_ARN_DEV
#                             on dev, AWS_ROLE_ARN_ECHO_<ENV> elsewhere. Derived
#                             from --role and the account when omitted.
#   --create-role             Create the role named by --role if absent, with an
#                             OIDC trust policy for this environment. Off by
#                             default: creating a role is a bigger step than
#                             attaching a policy to one.
#   --render-policy           Derive the policy, write it, print it, and STOP.
#                             Applies nothing.
#   --allow-self-review       Let the named reviewer approve their own dispatch.
#                             Needed when the reviewer list has exactly one
#                             person: prevent_self_review then blocks the only
#                             approver there is and every deployment queues
#                             forever. The approval click remains either way.
#                             Add a second reviewer and drop this.
#   --dry-run                 Say what would change; change nothing.
#   --verify-only             Check and report current state; change nothing.
#   --rollback                Undo what this script created, in reverse order.
#   -h, --help                This text.
#
# Typical first run for a new environment:
#   ./bootstrap-environment.sh uat-uif --render-policy               # review, commit
#   ./bootstrap-environment.sh uat-uif --role uif-echo-uat-deploy-role \
#       --reviewer <login> --dry-run
#   ./bootstrap-environment.sh uat-uif --role uif-echo-uat-deploy-role \
#       --reviewer <login>
#
# EXPECT TO RUN IT MORE THAN ONCE. Apicurio needed FIVE apply-and-fix rounds
# (UIFPE-496) because a derivation sees the resources a stack manages and is
# blind to the post-create reads, tag reads and list-shaped calls the provider
# makes around them. A green --verify-only is not evidence the apply will work.
# It is idempotent, so re-running after adding an action is safe.
#
# Exit 0 means the environment is ready for `terraform apply`. Any non-zero
# means nothing downstream should proceed.

set -euo pipefail

# ── Account map ───────────────────────────────────────────────────────────────
# dev and uat share an account; prod does not. Matches
# infrastructure/terraform/{envs,backends}/, which already carry this topology.
# Hardcoded rather than discovered, because the whole point of the preflight
# check below is to catch credentials for the WRONG account -- and a value read
# from those same credentials could not.
account_for() {
  case "$1" in
    dev|dev-uif|uat|uat-uif) echo "283556840805" ;;
    prod)                    echo "216064088366" ;;
  esac
}

REGION="${AWS_REGION:-eu-west-1}"
REPO_SLUG="${REPO_SLUG:-pearson-dnt-coe/universal-Integration-framework}"

# Build tags only. This is what makes uat a tag-based deployment enforced by
# GitHub rather than by a workflow `if:` that a dispatch can sidestep.
#
# THE uat ENVIRONMENT ADMITS ONLY apicurio/v* TODAY. Verified 2026-09-23:
#   gh api repos/<slug>/environments/uat/deployment-branch-policies
#   -> tag: apicurio/v*
# Adding echo/v* needs repository ADMIN, which this script does not assume it
# has -- it reports the gap rather than failing silently on a 403.
TAG_POLICY="echo/v*"

MODE=run
TFVARS_ENV=""
GH_ENV=""
ROLE=""
ROLE_ARN=""
CREATE_ROLE=0
ALLOW_SELF_REVIEW=0
REVIEWERS=()

step() { echo ""; echo "▶ $*"; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ! $*" >&2; }
die()  { echo "  ✗ $*" >&2; exit 1; }
would(){ echo "  … would $*"; }
note() { echo "    $*"; }

# Resolve an interpreter that can actually RUN, not merely one whose NAME is on
# PATH. Same problem qe/ci.sh solves and for the same reason: on Windows the
# Microsoft Store app-execution aliases put a `python3` on PATH that is not
# Python at all -- it prints "Python was not found" and exits 49. Hardcoding
# python3 made this script die at the first prefix lookup on a machine where
# `python` works fine.
resolve_python() {
  local c
  for c in python3 python py; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c "import json,sys" >/dev/null 2>&1 || continue
    echo "$c"; return 0
  done
  return 1
}
PY_BIN="$(resolve_python)" || { echo "  ✗ no working python on PATH (tried python3, python, py)" >&2; exit 1; }

# ── Arguments ─────────────────────────────────────────────────────────────────

[ $# -gt 0 ] || { sed -n '46,92p' "$0" | sed -E 's/^# ?//'; exit 2; }
case "${1:-}" in -h|--help) sed -n '2,92p' "$0" | sed -E 's/^# ?//'; exit 0 ;; esac

TFVARS_ENV="$1"; shift

# The closed set of tfvars files on disk. Deliberately not a wildcard glob: a
# typo like `uat-uf` would otherwise resolve to no file and fail three steps
# later, having already created a role.
case "$TFVARS_ENV" in
  dev|dev-uif|uat|uat-uif|prod) ;;
  *) die "env must be one of dev, dev-uif, uat, uat-uif or prod (the tfvars name), got '${TFVARS_ENV}'" ;;
esac

while [ $# -gt 0 ]; do
  case "$1" in
    --role)              ROLE="${2:?--role needs a value}"; shift 2 ;;
    --role-arn)          ROLE_ARN="${2:?--role-arn needs a value}"; shift 2 ;;
    --reviewer)          REVIEWERS+=("${2:?--reviewer needs a value}"); shift 2 ;;
    --create-role)       CREATE_ROLE=1; shift ;;
    --render-policy)     MODE=render; shift ;;
    --dry-run)           MODE=dry; shift ;;
    --verify-only)       MODE=verify; shift ;;
    --rollback)          MODE=rollback; shift ;;
    --allow-self-review) ALLOW_SELF_REVIEW=1; shift ;;
    -h|--help)           sed -n '2,92p' "$0" | sed -E 's/^# ?//'; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

# GitHub has ONE environment per real environment; the -uif suffix is a
# Terraform-state distinction, not a deployment-authority one. Both uat stacks
# deploy through the `uat` environment and answer to the same reviewer.
GH_ENV="${TFVARS_ENV%-uif}"
ACCOUNT="$(account_for "$TFVARS_ENV")"
ENV_UPPER="$(printf '%s' "$GH_ENV" | tr '[:lower:]' '[:upper:]')"

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
HERE="$(cd "$(dirname "$0")" && pwd)"
REQUIRED_ACTIONS="${HERE}/required-actions.py"
TF_DIR="${REPO_ROOT}/stacks/echo/infrastructure/terraform"
TFVARS="${TF_DIR}/envs/${TFVARS_ENV}.tfvars"
BACKEND_HCL="${TF_DIR}/backends/${TFVARS_ENV}.hcl"
# The GOLD root's backend, which is a SECOND state file in the same bucket under
# a different key (tfstate/gold-terraform.tfstate). It needs its own grant, and
# the file extension differs from the main root's: gold uses
# `<env>.s3.tfbackend` where the main root uses `<env>.hcl`.
#
# WHY THIS EXISTS. UIFPE-576 folded the gold root into the promote workflow, so
# a promote now runs `terraform init` against BOTH roots, and it changed
# required-actions.py to derive the deploy policy from both. It did not change
# this file -- and it could not have, because the backend policy is not derived
# from the Terraform: the state bucket is not a resource either root manages, so
# it is written by hand here. The result was a deploy policy that could create
# gold's resources and a backend policy that could not read gold's state, which
# fails at `init` before any of that matters.
GOLD_TF_DIR="${REPO_ROOT}/stacks/echo/infrastructure/terraform-gold"
GOLD_BACKEND="${GOLD_TF_DIR}/backends/${TFVARS_ENV}.s3.tfbackend"

# The name prefix is read from the tfvars, never derived from the env -- see
# required-actions.py's header for the five prefixes and why no formula works.
# This is the same resolution, for the policy NAME rather than its contents.
PREFIX="$(cd "$REPO_ROOT" && "$PY_BIN" -c "
import sys; sys.path.insert(0, 'stacks/echo/src/libs')
import stack_naming
print(stack_naming.load('${TFVARS_ENV}').name_prefix)
")" || die "could not resolve the name prefix for ${TFVARS_ENV} from ${TFVARS}"

# The policy name STEM. Each rendered part becomes
# ${PREFIX}-deploy-slot-NN-policy, and the slot-NN is the naming standard's own
# vocabulary rather than something invented here: 00-core.md defines `instance`
# as `slot-NN`, zero-padded to two digits, for exactly this shape -- one of
# several otherwise identical things. A bare `-1-` would read as a version or
# phase token, which that file bans outright (batch2, fix12, v2).
#
# Numbered rather than named by purpose, unlike apicurio's deploy/base/backend
# split, because the packing is ARBITRARY by design -- which scopes land in
# which part changes as the policy grows, so any purpose-name would be a lie
# after the next round. The document on disk carries the SAME name as the
# policy, so a policy in the console and a file in the repo are findable by one
# string; they disagreed in the first version of this.
POLICY_STEM="${PREFIX}-deploy"
# A SECOND policy, and the reason it is separate. Terraform's S3 backend reads
# its state file before it evaluates anything else, and the state bucket is not
# a resource this stack manages -- so required-actions.py cannot derive it from
# the Terraform, and the derived policy (which is regenerated wholesale) must
# not carry a hand-edited statement. Its own document is the honest home for
# "what this role needs in order to function" as distinct from "what it is
# allowed to create".
BACKEND_POLICY_NAME="${PREFIX}-backend-policy"
BACKEND_POLICY_ARN="arn:aws:iam::${ACCOUNT}:policy/${BACKEND_POLICY_NAME}"

# A THIRD hand-written policy, for the same reason the backend one is written by
# hand: it cannot be derived from this environment's Terraform.
#
# A promoted environment reads its artifacts out of the environment that BUILT
# them. dev is the only environment that builds (uif_echo_stage_release_artifacts
# refuses to stage into any other), so every other environment's deploy role
# needs read access to dev's release bucket -- a bucket that appears nowhere in
# uat's or prod's Terraform. required-actions.py reads the Terraform, so it
# cannot see this, and the s3_stack_buckets scope is arn:aws:s3:::{prefix}-*
# which covers this environment's own buckets and no others.
#
# That scope's own note records why this was missed: it says the grant "already
# let the dev role reach uif-echo-dev-release-artifacts without a change". True
# for dev, where {prefix} happens to BE uif-echo-dev. For uat the prefix is
# uif-echo-uat and dev's bucket falls outside it. A statement that is correct
# for one environment by coincidence is the hardest kind to notice.
#
# apicurio hit this first (UIFPE-486) and carries the same statement under the
# same Sid. Its DEPLOY-UAT.md warns that without it the promote "can log in to
# ECR but is denied on both crane copy and aws s3 cp, and the failure surfaces
# at promote's first AWS call as an opaque registry denied rather than a
# permissions error naming the resource." Echo's failure named the bucket,
# because copy-artifacts.sh says which bucket it could not list -- but it still
# cost a tag, an approval and an apply window to find.
#
# READ-ONLY, AND SEPARATE FROM THE DERIVED POLICY ON PURPOSE. The derived
# policy is regenerated wholesale on every run, so a hand-edited statement in
# it would be silently dropped the next time -- exactly what apicurio's
# bootstrap warns about at its own line 586. Keeping it in its own document
# means re-rendering cannot lose it, and a reader asking "why can uat read
# dev's bucket" finds one file that answers only that.
PROMOTION_POLICY_NAME="${PREFIX}-promotion-source-policy"
PROMOTION_POLICY_ARN="arn:aws:iam::${ACCOUNT}:policy/${PROMOTION_POLICY_NAME}"

# The environment that builds. Not derived from TFVARS_ENV: dev builds and
# every other environment promotes FROM dev, so this is a constant of the
# model rather than a property of the environment being bootstrapped.
PROMOTION_SOURCE_BUCKET="uif-echo-dev-release-artifacts"
# The GitHub secret holding the deploy role ARN. The two environments disagree
# on its shape, and the asymmetry is deliberate: it mirrors a real difference in
# who OWNS the role, and this name must match what the workflows actually read.
#
# dev is not stack-qualified because dev's role is not Echo's. It deploys
# through the shared `echo-dev-github-actions`, which eleven workflows across
# apicurio, beacon, event, price, product, program and stock assume from
# `AWS_ROLE_ARN_DEV`. Echo borrows that role; renaming a secret eight other
# stacks read is not Echo's call to make.
#
# uat IS stack-qualified because `uif-echo-uat-deploy-role` is Echo's alone --
# created by this script, attached to this stack's packed policies, read by
# nothing else.
#
# AND BECAUSE THE UNQUALIFIED NAME WAS UNSAFE. `AWS_ROLE_ARN_UAT` already
# exists at REPOSITORY level (2026-09-20) and `deploy-product-uat.yml` reads it
# under `environment: uat`. GitHub resolves an environment-scoped secret ahead
# of the repository one, so an earlier run of this script setting
# `AWS_ROLE_ARN_UAT` on the `uat` environment silently pointed product's uat
# deploy at Echo's role. Nothing broke only because that workflow is absent from
# the default branch and `workflow_dispatch` cannot reach it there -- a reprieve
# that ends the moment develop merges to main. The failure would also have been
# the worst shape to diagnose: the two roles overlap PARTIALLY, so `glue:UpdateJob`
# comes back allowed while `s3:PutObject` is an implicitDeny, and the error names
# a bucket rather than an identity.
#
# A stack-qualified name cannot collide with product's `AWS_ROLE_ARN_UAT`, cannot
# shadow or be shadowed by apicurio's unsuffixed `AWS_ROLE_ARN` (also present on
# this environment), and answers "whose role is this" from the workflow file alone.
case "$GH_ENV" in
  dev) SECRET_NAME="AWS_ROLE_ARN_DEV" ;;
  *)   SECRET_NAME="AWS_ROLE_ARN_ECHO_${ENV_UPPER}" ;;
esac

POLICY_DOC_DIR="${REPO_ROOT}/capabilities/infrastructure"
POLICY_PARTS=()
BACKEND_DOC="${REPO_ROOT}/capabilities/infrastructure/${BACKEND_POLICY_NAME}.json"
PROMOTION_DOC="${REPO_ROOT}/capabilities/infrastructure/${PROMOTION_POLICY_NAME}.json"

# dev's shared role is known; nothing else is. Guessing a role name outside dev
# would attach a policy to something, succeed, and leave the real deploy role
# unchanged -- a failure that looks exactly like success.
if [ -z "$ROLE" ]; then
  if [ "$GH_ENV" = "dev" ]; then
    ROLE="echo-dev-github-actions"
  elif [ "$MODE" != "render" ]; then
    die "--role is required for ${TFVARS_ENV}. Only dev's shared role (echo-dev-github-actions) is known to this repository; naming the wrong one attaches the policy to nothing that deploys. The rollout plan names uif-echo-uat-deploy-role for uat."
  fi
fi
[ -n "$ROLE_ARN" ] || ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE}"

echo "uif-echo environment bootstrap"
echo "  tfvars env  : ${TFVARS_ENV}   (${TFVARS})"
echo "  gh env      : ${GH_ENV}"
echo "  name prefix : ${PREFIX}"
echo "  account     : ${ACCOUNT}"
echo "  region      : ${REGION}"
echo "  iam role    : ${ROLE:-<unset>}"
echo "  policies    : ${POLICY_STEM}-slot-NN-policy (packed) + ${BACKEND_POLICY_NAME}"
echo "  repository  : ${REPO_SLUG}"
echo "  mode        : ${MODE}"

# ── Render the policy ─────────────────────────────────────────────────────────
# Rendering is a SEPARATE, explicit step that applies nothing. An IAM document
# generated and attached in one motion is a privileged change no human read.

render_policy() {
  [ -f "$REQUIRED_ACTIONS" ] || die "${REQUIRED_ACTIONS} is missing"
  mkdir -p "$POLICY_DOC_DIR"

  # PACKED INTO N DOCUMENTS, not one. Echo's derived policy is 8187 compact
  # characters against IAM's 6144 managed-policy limit -- a fact about the
  # stack, which spans Glue, Lake Formation, DynamoDB, S3, Lambda, EventBridge,
  # API Gateway, CloudFront, WAFv2, logs, IAM and EC2. apicurio's fits because
  # it spans fewer services. An inline role policy would fit (10240) and is the
  # wrong answer: inline policies have no versions, so there is nothing to roll
  # back to when a round of the apply-and-fix loop makes things worse.
  #
  # Through "$PY_BIN", NOT the shebang: required-actions.py declares
  # #!/usr/bin/env python3, which is right for running it directly and wrong
  # here on a machine whose working interpreter is `python`.
  #
  # PIPED, not written to a temp file and passed by path. mktemp yields a POSIX
  # /tmp/... path, and on Windows the interpreter is a native binary that cannot
  # resolve it -- the first version of this died with FileNotFoundError on its
  # own temp file. stdin crosses that boundary on every platform.
  local packed err rc
  err="$(mktemp)"
  packed="$("$PY_BIN" "$REQUIRED_ACTIONS" --mode policies --env "$TFVARS_ENV"               --account "$ACCOUNT" --region "$REGION" 2>"$err")" && rc=0 || rc=$?
  if [ "$rc" -ne 0 ]; then
    warn "required-actions.py failed:"
    sed 's/^/      /' "$err" >&2
    rm -f "$err"
    die "cannot render the policy for ${TFVARS_ENV}. If the message above names an unmapped resource type, add it to terraform-iam-map.json with the actions an apply of it calls -- that refusal is deliberate."
  fi
  rm -f "$err"

  POLICY_PARTS=()
  local n; n="$(printf '%s' "$packed" | "$PY_BIN" -c "import json,sys;print(len(json.load(sys.stdin)))")"
  local i doc size
  for i in $(seq 1 "$n"); do
    doc="$(printf '%s/%s-slot-%02d-policy.json' "$POLICY_DOC_DIR" "$POLICY_STEM" "$i")"
    printf '%s' "$packed" | "$PY_BIN" -c "
import json, sys
docs = json.load(sys.stdin)
with open(sys.argv[1], 'w') as f:
    json.dump(docs[int(sys.argv[2]) - 1], f, indent=2)
    print(file=f)  # trailing newline, without an escape that has to
                   # survive being nested in a double-quoted shell string
" "$doc" "$i"
    size="$(printf '%s' "$packed" | "$PY_BIN" -c "
import json, sys
d = json.load(sys.stdin)[int(sys.argv[1]) - 1]
print(len(json.dumps(d, separators=(',', ':'))))
" "$i")"
    [ "$size" -le 6144 ] || die "part ${i} is ${size} compact characters, over IAM's 6144 limit -- the packer should have prevented this"
    ok "part ${i}: ${doc##*/} (${size}/6144 compact chars)"
    POLICY_PARTS+=("$doc")
  done
  note "${n} managed policies; a role may attach ten by default"
}

# ── Preflight ─────────────────────────────────────────────────────────────────

step "Preflight"

for tool in aws gh git; do
  command -v "$tool" >/dev/null 2>&1 || die "${tool} is not on PATH"
done
ok "aws, gh, git present; python is ${PY_BIN}"

[ -f "$TFVARS" ]      || die "${TFVARS} does not exist. Phase 2 creates it; see docs/uif-echo-uat-rollout-plan.md."
[ -f "$BACKEND_HCL" ] || warn "${BACKEND_HCL#"$REPO_ROOT"/} does not exist -- terraform init will have no backend for ${TFVARS_ENV}"
ok "tfvars present"

if [ "$MODE" = "render" ]; then
  render_policy
  step "Rendered only. Nothing was applied."
  note "Review it, commit it, then re-run with --role and --reviewer."
  exit 0
fi

# WHOSE CREDENTIALS ARE THESE. The most expensive mistake available here is
# bootstrapping uat with prod credentials, or the reverse: every call would
# succeed, against the wrong account. Checked before anything is created.
CALLER_ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
[ -n "$CALLER_ACCOUNT" ] || die "no usable AWS credentials -- aws sts get-caller-identity failed"
if [ "$CALLER_ACCOUNT" != "$ACCOUNT" ]; then
  die "credentials are for account ${CALLER_ACCOUNT}, but ${TFVARS_ENV} is ${ACCOUNT}. Refusing: every call would succeed against the wrong account."
fi
ok "credentials are for ${ACCOUNT} ($(aws sts get-caller-identity --query Arn --output text))"

gh auth status >/dev/null 2>&1 || die "gh is not authenticated -- run gh auth login"
ok "gh authenticated"

# Reviewer is mandatory outside dev, and cannot be defaulted. An environment
# with no reviewer is an environment with no approval gate, which is the
# opposite of what creating it is for.
if [ "$GH_ENV" != "dev" ] && [ "${#REVIEWERS[@]}" -eq 0 ] && [ "$MODE" = "run" ]; then
  die "--reviewer is required for ${GH_ENV}. An environment created without one has NO approval gate, and GitHub will not add one retroactively just because a workflow names it."
fi

# ── Rollback ──────────────────────────────────────────────────────────────────

if [ "$MODE" = "rollback" ]; then
  step "Rollback — undoing what this script creates, in reverse order"
  note "The IAM ROLE is never deleted, even when --create-role made it: by the"
  note "time a rollback runs, something may already assume it."

  # BOTH names, because this script has used two. A rollback that removed only
  # the current stack-qualified one would leave the unqualified name scoped to
  # this environment, still shadowing product's repository-level secret -- a
  # rollback whose whole job is to leave no trace, leaving the one piece of
  # state that affects another team.
  #
  # --env on every delete. The repository-level AWS_ROLE_ARN_UAT is product's
  # and predates this stack; a repo-scoped delete here would break their deploy.
  rollback_secrets=("$SECRET_NAME")
  [ "$SECRET_NAME" = "AWS_ROLE_ARN_${ENV_UPPER}" ] || \
    rollback_secrets+=("AWS_ROLE_ARN_${ENV_UPPER}")

  for secret in "${rollback_secrets[@]}"; do
    if gh secret list --env "$GH_ENV" 2>/dev/null | grep -q "^${secret}\b"; then
      gh secret delete "$secret" --env "$GH_ENV" && ok "deleted ${secret} from ${GH_ENV}"
    else
      ok "${secret} not present on ${GH_ENV}"
    fi
  done

  # Every numbered part, however many there are now -- discovered rather than
  # assumed, because the count changes as the policy grows.
  mapfile -t rollback_arns < <(
    aws iam list-policies --scope Local --max-items 200       --query "Policies[?starts_with(PolicyName, '${PREFIX}-deploy-')].Arn" --output text 2>/dev/null | tr '	' '
'
  )
  for arn in "$BACKEND_POLICY_ARN" "${rollback_arns[@]}"; do
    [ -n "${arn:-}" ] || continue
    if aws iam get-policy --policy-arn "$arn" >/dev/null 2>&1; then
      # Detach before delete: IAM refuses to delete an attached policy, and the
      # error names the policy rather than the attachment.
      while read -r attached_role; do
        [ -n "${attached_role:-}" ] || continue
        aws iam detach-role-policy --role-name "$attached_role" --policy-arn "$arn" \
          && ok "detached ${arn##*/} from ${attached_role}"
      done < <(aws iam list-entities-for-policy --policy-arn "$arn" \
                 --query 'PolicyRoles[].RoleName' --output text | tr '\t' '\n')
      # Non-default versions block the delete too.
      while read -r v; do
        [ -n "${v:-}" ] || continue
        aws iam delete-policy-version --policy-arn "$arn" --version-id "$v" >/dev/null
      done < <(aws iam list-policy-versions --policy-arn "$arn" \
                 --query 'Versions[?!IsDefaultVersion].VersionId' --output text | tr '\t' '\n')
      aws iam delete-policy --policy-arn "$arn" && ok "deleted ${arn##*/}"
    else
      ok "${arn##*/} does not exist"
    fi
  done

  note "The GitHub Environment '${GH_ENV}' is NOT deleted: it is shared with the"
  note "legacy stack and, for uat, with apicurio's tag policy."
  step "Rollback complete"
  exit 0
fi

# ── 1/6  The deploy role ──────────────────────────────────────────────────────

step "1/6  Deploy role ${ROLE}"

role_exists() { aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; }

if role_exists; then
  ok "${ROLE} exists ($(aws iam get-role --role-name "$ROLE" --query 'Role.Arn' --output text))"
elif [ "$CREATE_ROLE" -eq 0 ]; then
  die "${ROLE} does not exist. Re-run with --create-role to create it with an OIDC trust policy, or name the role that already deploys this environment. Creating a role is deliberately not the default."
elif [ "$MODE" = "dry" ]; then
  would "create role ${ROLE} with an OIDC trust policy for ${REPO_SLUG} environment:${GH_ENV}"
elif [ "$MODE" = "verify" ]; then
  warn "${ROLE} does not exist"
else
  # Trust is scoped to THIS repository and THIS environment. `environment:` in
  # the subject is what makes the GitHub Environment's reviewer a real gate on
  # the AWS side: a workflow that has not been approved for this environment
  # cannot assume the role, however it is triggered.
  trust="$("$PY_BIN" -c "
import json
print(json.dumps({
  'Version': '2012-10-17',
  'Statement': [{
    'Effect': 'Allow',
    'Principal': {'Federated': 'arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com'},
    'Action': 'sts:AssumeRoleWithWebIdentity',
    'Condition': {
      'StringEquals': {
        'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        'token.actions.githubusercontent.com:sub': 'repo:${REPO_SLUG}:environment:${GH_ENV}'
      }
    }
  }]
}, indent=2))
")"
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document "$trust" \
    --description "Deploy role for ${PREFIX}-* (bootstrap-environment.sh)" \
    --query 'Role.Arn' --output text >/dev/null
  ok "created ${ROLE}, trusted only for ${REPO_SLUG} environment:${GH_ENV}"
fi

# ── 2/6  The derived deploy policy ────────────────────────────────────────────

step "2/6  Deploy policies ${POLICY_STEM}-slot-NN-policy"

render_policy

put_policy() {
  local name="$1" arn="$2" doc="$3"
  if aws iam get-policy --policy-arn "$arn" >/dev/null 2>&1; then
    if [ "$MODE" = "dry" ]; then would "update ${name} to a new default version"; return; fi
    if [ "$MODE" = "verify" ]; then ok "${name} exists"; return; fi
    # IAM keeps at most five versions. Prune the oldest non-default before
    # adding one, or the sixth apply fails with LimitExceeded -- which on a
    # fifth apply-and-fix round is exactly when it would happen.
    local n
    n="$(aws iam list-policy-versions --policy-arn "$arn" \
          --query 'length(Versions[?!IsDefaultVersion])' --output text)"
    if [ "${n:-0}" -ge 4 ]; then
      local oldest
      oldest="$(aws iam list-policy-versions --policy-arn "$arn" \
                 --query 'sort_by(Versions[?!IsDefaultVersion], &CreateDate)[0].VersionId' \
                 --output text)"
      aws iam delete-policy-version --policy-arn "$arn" --version-id "$oldest" >/dev/null
      note "pruned version ${oldest} (IAM allows five)"
    fi
    aws iam create-policy-version --policy-arn "$arn" \
      --policy-document "file://${doc}" --set-as-default >/dev/null
    ok "updated ${name}"
  else
    if [ "$MODE" = "dry" ]; then would "create ${name} from ${doc#"$REPO_ROOT"/}"; return; fi
    if [ "$MODE" = "verify" ]; then warn "${name} does not exist"; return; fi
    aws iam create-policy --policy-name "$name" \
      --policy-document "file://${doc}" \
      --description "Derived from terraform-iam-map.json by required-actions.py" >/dev/null
    ok "created ${name}"
  fi
}

attach_policy() {
  local arn="$1" name="$2"
  if ! role_exists; then
    # In dry-run the role is absent only because step 1 did not create it, so
    # "cannot attach" would read as a failure of something never attempted.
    if [ "$MODE" = "dry" ]; then would "attach ${name} to ${ROLE} (once step 1 creates it)"
    else warn "cannot attach ${name}: ${ROLE} does not exist"; fi
    return
  fi
  if aws iam list-attached-role-policies --role-name "$ROLE" \
       --query 'AttachedPolicies[].PolicyArn' --output text | tr '\t' '\n' | grep -qx "$arn"; then
    ok "${name} already attached to ${ROLE}"
  elif [ "$MODE" = "dry" ]; then
    would "attach ${name} to ${ROLE}"
  elif [ "$MODE" = "verify" ]; then
    warn "${name} is NOT attached to ${ROLE}"
  else
    aws iam attach-role-policy --role-name "$ROLE" --policy-arn "$arn"
    ok "attached ${name} to ${ROLE}"
  fi
}

# One managed policy per rendered part, numbered. The packing is dynamic, so
# which scopes land in which part can change as the policy grows -- that is
# safe because each part is versioned and updated in place, and the union is
# what matters to the role.
part_name() { printf '%s-slot-%02d-policy
' "$POLICY_STEM" "$1"; }
part_arn()  { echo "arn:aws:iam::${ACCOUNT}:policy/$(part_name "$1")"; }

idx=0
for doc in "${POLICY_PARTS[@]}"; do
  idx=$((idx + 1))
  put_policy    "$(part_name "$idx")" "$(part_arn "$idx")" "$doc"
  attach_policy "$(part_arn "$idx")"  "$(part_name "$idx")"
done

# ── 3/6  The Terraform state backend: the bucket, then its policy ─────────────────────────────────────────

step "3/6  Terraform state backend: s3 bucket + ${BACKEND_POLICY_NAME}"

# The state bucket is read before Terraform evaluates anything, and it is not a
# resource this stack manages -- so it cannot be derived. Read from the backend
# config rather than assumed, because dev-uif and uat-uif put state in their own
# data bucket while the legacy envs use the shared tfstate bucket.
if [ -f "$BACKEND_HCL" ]; then
  STATE_BUCKET="$(sed -nE 's/^[[:space:]]*bucket[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$BACKEND_HCL" | head -1)"
  STATE_KEY="$(sed -nE 's/^[[:space:]]*key[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$BACKEND_HCL" | head -1)"
else
  STATE_BUCKET=""; STATE_KEY=""
fi

# Gold's key, when this environment is described by the gold root too. Its
# bucket is read and compared rather than assumed: the two roots SHOULD share
# one bucket (gold's own backend comment says "beside the main root's own uat
# state, in the same bucket"), and if they ever diverge this policy would grant
# the wrong one while reporting success.
GOLD_STATE_KEY=""
if [ -f "$GOLD_BACKEND" ]; then
  GOLD_STATE_BUCKET="$(sed -nE 's/^[[:space:]]*bucket[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$GOLD_BACKEND" | head -1)"
  GOLD_STATE_KEY="$(sed -nE 's/^[[:space:]]*key[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$GOLD_BACKEND" | head -1)"
  if [ -n "$GOLD_STATE_BUCKET" ] && [ "$GOLD_STATE_BUCKET" != "$STATE_BUCKET" ]; then
    die "$(printf '%s\n' \
      "the two roots name different state buckets:" \
      "  main: ${STATE_BUCKET}" \
      "  gold: ${GOLD_STATE_BUCKET}" \
      "  This policy grants object access inside ONE bucket, so it cannot cover" \
      "  both. Reconcile the backend files, or this script needs a second" \
      "  statement -- do not widen the resource to a wildcard to make it pass.")"
  fi
fi

if [ -z "$STATE_BUCKET" ]; then
  warn "no bucket in ${BACKEND_HCL#"$REPO_ROOT"/}; skipping the backend policy"
  note "Terraform cannot init without it, so this is a real gap, not a warning to"
  note "pass over. Phase 2 writes backends/${TFVARS_ENV}.hcl."
else
  # EVERY key, not just the main root's. The first version of this printed
  # STATE_KEY alone, which made a dry run unverifiable for the thing that
  # mattered most about it: whether the gold root's state was granted. The
  # document was correct and the report was silent, so the run looked identical
  # to the one before the fix. A dry run you cannot check is worth very little.
  note "state s3://${STATE_BUCKET}/${STATE_KEY}   (main root)"
  if [ -n "$GOLD_STATE_KEY" ]; then
    note "      s3://${STATE_BUCKET}/${GOLD_STATE_KEY}   (gold root)"
    note "both roots' state is granted, each key named explicitly. The deploy"
    note "policy is derived from both roots; this one is not derivable and is"
    note "written here, so it is the half that can fall behind."
  else
    note "the gold root does not describe ${TFVARS_ENV} (no"
    note "terraform-gold/backends/${TFVARS_ENV}.s3.tfbackend), so only the main"
    note "root's state is granted. A promote that inits the gold root would"
    note "fail on this environment."
  fi

  # ── The state bucket itself ────────────────────────────────────────────────
  #
  # CREATED HERE, and the circularity is the reason. `terraform init` reads the
  # state before it evaluates anything, so the bucket must exist first -- but
  # Terraform also DECLARES it, as aws_s3_bucket.artifacts from this
  # environment's artifacts_bucket. Something has to break that loop, and a
  # bootstrap run by a person before the first init is the right place.
  #
  # Dev broke it the same way and paid for it twice over: uif-echo-dev-data was
  # created by hand, and so was uif-echo-dev-release-artifacts on 2026-09-23,
  # each time as an unrecorded manual step someone had to be told about. This
  # makes it a step with a name.
  #
  # VERSIONING IS NOT OPTIONAL. Without it a corrupted or truncated state has no
  # previous copy, and state is the one file whose loss cannot be recovered by
  # re-running anything. The release bucket had to be created by hand with
  # versioning on for a related reason -- stage-artifacts.sh fails closed
  # without a version id -- so this is the second time the same omission would
  # have bitten.
  #
  # CREATING IT IS ONLY HALF. Terraform still declares it, so the first apply
  # would fail with BucketAlreadyOwnedByYou unless the bucket is IMPORTED into
  # state. uif_echo_import_phase5_artifacts() in _uif-echo-deploy-common.sh
  # does exactly that -- and is hardcoded to dev-uif, so it is a no-op for uat
  # until it is parameterised. That is a Phase 4 prerequisite, not something
  # this script can do: it has no state to import into yet.
  if aws s3api head-bucket --bucket "$STATE_BUCKET" --region "$REGION" 2>/dev/null; then
    ok "s3://${STATE_BUCKET} exists"
    sb_versioning="$(aws s3api get-bucket-versioning --bucket "$STATE_BUCKET" \
      --region "$REGION" --query 'Status' --output text 2>/dev/null || echo None)"
    if [ "$sb_versioning" = "Enabled" ]; then
      ok "versioning Enabled"
    else
      warn "versioning is ${sb_versioning} on s3://${STATE_BUCKET} -- a corrupted"
      warn "state would have no previous copy. Enable it before the first apply."
    fi
  elif [ "$MODE" = "dry" ]; then
    would "create s3://${STATE_BUCKET} with versioning, encryption and public access blocked"
  elif [ "$MODE" = "verify" ]; then
    warn "s3://${STATE_BUCKET} does not exist -- terraform init cannot run"
  else
    # eu-west-1 and every region except us-east-1 require LocationConstraint;
    # us-east-1 REJECTS it. Both branches, because prod is a different account
    # and nothing here should assume it stays in eu-west-1.
    if [ "$REGION" = "us-east-1" ]; then
      aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$REGION" >/dev/null
    else
      aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$REGION" \
        --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
    fi
    ok "created s3://${STATE_BUCKET}"

    aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" --region "$REGION" \
      --versioning-configuration Status=Enabled >/dev/null
    ok "versioning Enabled"

    # Both of these are what trivy reports on a bucket that lacks them, and a
    # state bucket is the last place to leave them off: it holds every resource
    # identifier and every non-sensitive attribute of the stack.
    aws s3api put-public-access-block --bucket "$STATE_BUCKET" --region "$REGION" \
      --public-access-block-configuration \
      "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >/dev/null
    ok "public access blocked"

    # SSE-S3, matching every other bucket in this stack. Not a CMK, for the
    # reason recorded against AVD-AWS-0132 in terraform-iam-map.json -- and
    # here there is a second reason: a key policy on the state bucket sits on
    # the one path that cannot depend on anything this Terraform creates.
    aws s3api put-bucket-encryption --bucket "$STATE_BUCKET" --region "$REGION" \
      --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
    ok "SSE-S3 enabled"

    note "IMPORT IT before the first apply, or Terraform will fail with"
    note "BucketAlreadyOwnedByYou -- see the comment above this step."
  fi

  mkdir -p "$(dirname "$BACKEND_DOC")"
  # cannot encode the ✓ the rest of this script uses. bash reports instead.
  "$PY_BIN" - "$BACKEND_DOC" "$STATE_BUCKET" "$STATE_KEY" "$GOLD_STATE_KEY" <<'PY'
import json, sys
doc, bucket = sys.argv[1], sys.argv[2]
# Every state key this environment has, main root first. The gold key is empty
# for an environment the gold root does not describe, so it is filtered rather
# than assumed present.
keys = [k for k in sys.argv[3:] if k]

# use_lockfile (Terraform >= 1.10, which providers.tf now requires) locks with
# an S3 object beside the state rather than a DynamoDB table, so the lock needs
# object write on <key>.tflock and NOT dynamodb on a lock table. A policy copied
# from a pre-1.10 stack grants the wrong thing and the apply runs UNLOCKED.
#
# ONE RESOURCE PER KEY, NOT tfstate/*. A wildcard would cover both roots in one
# line and also cover any state a future root put in this bucket, granted
# silently. Naming each key means adding a root is a visible change here.
resources = []
for k in keys:
    resources.append(f"arn:aws:s3:::{bucket}/{k}")
    resources.append(f"arn:aws:s3:::{bucket}/{k}.tflock")

json.dump({
    "Version": "2012-10-17",
    "Statement": [
        {"Sid": "StateBucketList", "Effect": "Allow",
         "Action": ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketVersioning"],
         "Resource": f"arn:aws:s3:::{bucket}"},
        {"Sid": "StateObject", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectVersion"],
         "Resource": resources},
    ],
}, open(doc, "w"), indent=2)
PY
  put_policy    "$BACKEND_POLICY_NAME" "$BACKEND_POLICY_ARN" "$BACKEND_DOC"
  attach_policy "$BACKEND_POLICY_ARN"  "$BACKEND_POLICY_NAME"
fi

# ── 3b/6  Promotion source: read-only on the bucket dev built into ────────────
#
# See the PROMOTION_POLICY_NAME comment for why this cannot be derived and why
# it is its own document. dev is skipped: dev IS the source, and its own
# s3_stack_buckets scope already covers uif-echo-dev-* because for dev the
# prefix and the source bucket coincide. Granting dev read access to its own
# bucket a second time would add a statement that explains nothing.
if [ "$TFVARS_ENV" = "dev" ] || [ "$TFVARS_ENV" = "dev-uif" ]; then
  note "${TFVARS_ENV} IS the promotion source, so it needs no cross-environment"
  note "read. Its own bucket scope already covers ${PROMOTION_SOURCE_BUCKET}."
else
  step "3b/6  Promotion source read: ${PROMOTION_POLICY_NAME}"
  note "reads s3://${PROMOTION_SOURCE_BUCKET} -- the bucket dev built into"

  mkdir -p "$(dirname "$PROMOTION_DOC")"
  "$PY_BIN" - "$PROMOTION_DOC" "$PROMOTION_SOURCE_BUCKET" <<'PY'
import json, sys
doc, bucket = sys.argv[1], sys.argv[2]
# Sid matches apicurio's (UIFPE-486) so the two stacks name the same thing the
# same way and a reader who has seen one recognises the other.
#
# FOUR ACTIONS, AND TWO OF THEM ARE ECHO-ONLY. apicurio's equivalent grants
# ListBucket, GetObject and GetObjectVersion; Echo also needs
# ListBucketVersions, because copy-artifacts.sh reads the source's object
# VERSIONS -- the version id in the manifest is what proves the promoted bytes
# are the ones the gate saw, and a version cannot be pinned without listing it.
# apicurio copies with crane and aws s3 cp, which never version-list.
#
# Bucket-level actions take the bucket ARN and object-level actions take /*. A
# policy carrying only one of them fails halfway: the listing succeeds and the
# copy is denied, or the reverse.
json.dump({
    "Version": "2012-10-17",
    "Statement": [
        {"Sid": "PromotionSourceRead", "Effect": "Allow",
         "Action": ["s3:ListBucket", "s3:ListBucketVersions"],
         "Resource": f"arn:aws:s3:::{bucket}"},
        {"Sid": "PromotionSourceObjectRead", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:GetObjectVersion"],
         "Resource": f"arn:aws:s3:::{bucket}/*"},
    ],
}, open(doc, "w"), indent=2)
PY
  put_policy    "$PROMOTION_POLICY_NAME" "$PROMOTION_POLICY_ARN" "$PROMOTION_DOC"
  attach_policy "$PROMOTION_POLICY_ARN"  "$PROMOTION_POLICY_NAME"

  # No write actions, deliberately. A promote READS the source and writes only
  # to this environment's own bucket, which the derived policy already covers.
  # A role that could write into dev's release bucket could overwrite a
  # content-addressed key that a dev release tag still attests to.
  note "read-only: no PutObject, no DeleteObject on the source"
fi

# ── 4/6  Verify the role can act on this environment's resources ──────────────

step "4/6  Verify ${ROLE} can act on ${PREFIX}-* resources"

if [ "$MODE" = "dry" ]; then
  would "simulate every derived action against ${ROLE}, one call per scope"
else
  # A per-run stderr file, not a fixed shared path: /tmp/echo-sim.err
  # persisted between runs holding 4779 bytes from an earlier one, and a
  # message could be read back as though it belonged to this invocation.
  SIM_ERR="$(mktemp)"
  failed=0
  allowed=0

  # ONE API CALL PER SCOPE, not one per action-and-ARN.
  # simulate-principal-policy accepts many --action-names and many
  # --resource-arns together, and an action counts as allowed if ANY resource
  # allows it. Per-action calls meant hundreds of sequential round trips for
  # apicurio and a run over ten minutes; this is ~23 calls.
  #
  # `set -f` IS LOAD-BEARING. The unscopable scopes carry a single ARN of "*",
  # and an unquoted expansion of that under IFS=, GLOB-EXPANDS to the working
  # directory listing. Apicurio's first run reported denials for "VERSION" --
  # a file in its own directory, not an action at all.
  # Allowed if ANY resource allowed it -- the API returns one result per
  # action-and-resource pair, and an action evaluated against the wrong
  # resource TYPE is an implicitDeny however fully it is granted. Echo has
  # more of that than apicurio: the glue scope alone carries five ARN shapes,
  # and a job action will deny against the database ARN every time.
  # Carriage returns stripped, for the reason the scope list above needed it:
  # this Python is a text stream too, so on Windows it terminates each line
  # with CR LF, and a decision read as allowed-plus-CR does not equal allowed.
  # That one character reported 169 permitted actions as denied, and the
  # message read exactly like a permissions problem.
  best_verdicts() {
    "$PY_BIN" -c '
import json, sys, collections
sys.stdout.reconfigure(newline=chr(10))
best = collections.defaultdict(str)
for r in json.load(sys.stdin)["EvaluationResults"]:
    a, d = r["EvalActionName"], r["EvalDecision"]
    if d == "allowed" or not best[a]:
        best[a] = d
for a in sorted(best):
    print(a + chr(9) + best[a])
' | tr -d '\r'
  }

  # Count one scope's verdicts. Reads TAB-separated action/decision on stdin.
  tally() {
    local scope="$1" action decision
    while IFS="$(printf '\t')" read -r action decision; do
      [ -n "${action:-}" ] || continue
      if [ "$decision" = "allowed" ]; then
        allowed=$((allowed + 1))
      else
        warn "${action} -> ${decision} (scope ${scope})"
        failed=$((failed + 1))
      fi
    done
  }

  set -f
  while IFS="$(printf '\t')" read -r scope actions arns; do
    [ -n "${scope:-}" ] || continue
    IFS=, read -r -a action_list <<<"$actions"
    IFS=, read -r -a arn_list <<<"$arns"

    if ! result="$(aws iam simulate-principal-policy \
        --policy-source-arn "$ROLE_ARN" \
        --action-names "${action_list[@]}" \
        --resource-arns "${arn_list[@]}" \
        --output json </dev/null 2>"$SIM_ERR")"; then
      # A REFUSED BATCH IS NOT A DENIAL, and counting it as one is worse than
      # not checking: it reports a policy problem that does not exist and
      # blocks the apply on it. Retry the scope one action at a time.
      #
      # The case that forced this: API Gateway has two action vocabularies --
      # the HTTP-verb form (apigateway:GET, POST, ...) and the standard form
      # (apigateway:TagResource) -- and the simulator refuses to evaluate both
      # in one request:
      #
      #   InvalidInput: Invalid Input Actions: [apigateway:DELETE,...] and
      #   [apigateway:TagResource,...] require different authorization
      #   information.
      #
      # Both sit legally in one IAM statement and AWS evaluates them fine at
      # runtime; only the simulator objects. Adding the tag actions therefore
      # turned 5 previously-verified actions into 7 reported failures and the
      # step died telling the operator to add actions to the map -- the one
      # thing that could not have helped.
      #
      # Per-action rather than per-vocabulary because the split is a property of
      # the simulator, not of this map, and guessing which actions clash would
      # be another thing to keep in step with AWS. A scope this touches costs
      # one call per action; nothing else changes.
      warn "batched simulate refused scope ${scope}, retrying per action: $(tr -d '\n' <"$SIM_ERR" | head -c 200)"
      for one_action in "${action_list[@]}"; do
        if ! result="$(aws iam simulate-principal-policy \
            --policy-source-arn "$ROLE_ARN" \
            --action-names "$one_action" \
            --resource-arns "${arn_list[@]}" \
            --output json </dev/null 2>"$SIM_ERR")"; then
          warn "${one_action} -> simulate failed (scope ${scope}): $(tr -d '\n' <"$SIM_ERR" | head -c 200)"
          failed=$((failed + 1))
          continue
        fi
        tally "$scope" < <(printf '%s' "$result" | best_verdicts)
      done
      continue
    fi

    tally "$scope" < <(printf '%s' "$result" | best_verdicts)
  done <<DERIVED
$("$PY_BIN" "$REQUIRED_ACTIONS" --mode batches --env "$TFVARS_ENV" --account "$ACCOUNT" --region "$REGION")
DERIVED
  set +f

  # A handful of concrete, named resources rather than a prefix wildcard. These
  # exercise the paths a promotion actually takes, and each has bitten
  # something: s3:GetObjectVersion is the rollback path, and denied, a release
  # cannot be restored when it matters most.
  simulate_one() {
    local decision
    decision="$(aws iam simulate-principal-policy \
      --policy-source-arn "$ROLE_ARN" \
      --action-names "$1" --resource-arns "$2" \
      --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null || echo error)"
    if [ "$decision" = "allowed" ]; then
      allowed=$((allowed + 1))
    else
      warn "$1 -> ${decision} on $2"
      failed=$((failed + 1))
    fi
  }
  simulate_one s3:PutObject        "arn:aws:s3:::${PREFIX}-release-artifacts/lambdas/x.zip"
  simulate_one s3:GetObjectVersion "arn:aws:s3:::${PREFIX}-release-artifacts/lambdas/x.zip"
  simulate_one glue:UpdateJob      "arn:aws:glue:${REGION}:${ACCOUNT}:job/${PREFIX}-bronze-ingest"
  simulate_one lambda:UpdateFunctionCode "arn:aws:lambda:${REGION}:${ACCOUNT}:function:${PREFIX}-pipeline-controller"
  simulate_one dynamodb:DescribeTable "arn:aws:dynamodb:${REGION}:${ACCOUNT}:table/${PREFIX}-pipeline-config"

  # THIS ENVIRONMENT'S OWN version listing, which no derived scope covered
  # until 2026-09-26. copy-artifacts.sh lists the DESTINATION's versions so the
  # TSV records this environment's version ids rather than the source's, which
  # is what release-manifest.sh reads as ARTIFACT_FILE.
  simulate_one s3:ListBucketVersions "arn:aws:s3:::${PREFIX}-release-artifacts"

  # THE PROMOTION SOURCE, and the reason these probes exist at all.
  #
  # The first uat converge passed this step with 225 of 225 actions allowed and
  # then failed at the artifact copy, because every action the derivation
  # produced is scoped to arn:aws:s3:::{prefix}-* and the source bucket is not
  # in this environment's prefix. A verification built only from the derivation
  # can only confirm the derivation -- so the one grant that cannot be derived
  # was the one it could not check. These four close that hole.
  #
  # Skipped for dev, which IS the source.
  if [ "$TFVARS_ENV" != "dev" ] && [ "$TFVARS_ENV" != "dev-uif" ]; then
    simulate_one s3:ListBucket         "arn:aws:s3:::${PROMOTION_SOURCE_BUCKET}"
    simulate_one s3:ListBucketVersions "arn:aws:s3:::${PROMOTION_SOURCE_BUCKET}"
    simulate_one s3:GetObject          "arn:aws:s3:::${PROMOTION_SOURCE_BUCKET}/lambdas/x.zip"
    simulate_one s3:GetObjectVersion   "arn:aws:s3:::${PROMOTION_SOURCE_BUCKET}/lambdas/x.zip"
  fi

  if [ "$failed" -gt 0 ]; then
    if [ "$MODE" = "verify" ]; then
      warn "${failed} action(s) not allowed; re-run without --verify-only to apply the policy"
    else
      die "${failed} of $((allowed + failed)) action(s) are still not allowed after attaching both policies. The environment is NOT ready; do not run terraform apply -- it would fail part-way and leave ${TFVARS_ENV} half-built. Add the missing actions to terraform-iam-map.json and re-run; this is the apply-and-fix loop UIFPE-496 describes, and apicurio needed five rounds."
    fi
  else
    ok "all ${allowed} derived actions allowed"
  fi
  note "simulate-principal-policy evaluates IDENTITY policies only -- an SCP or a"
  note "permissions boundary can still deny at runtime, and neither is visible here."
fi

# ── 5/6  The GitHub Environment ───────────────────────────────────────────────

step "5/6  GitHub Environment '${GH_ENV}'"

env_exists() { gh api "repos/${REPO_SLUG}/environments/${GH_ENV}" >/dev/null 2>&1; }

if env_exists; then
  ok "environment '${GH_ENV}' exists"
else
  if [ "$MODE" = "dry" ]; then
    would "create environment '${GH_ENV}' with reviewer(s) ${REVIEWERS[*]:-<none>}"
  elif [ "$MODE" = "verify" ]; then
    warn "environment '${GH_ENV}' does not exist -- a workflow naming it would create it UNPROTECTED"
  else
    gh api --method PUT "repos/${REPO_SLUG}/environments/${GH_ENV}" >/dev/null
    ok "created environment '${GH_ENV}'"
  fi
fi

# Reviewers. Reported rather than silently replaced: an environment that already
# has approvers belongs to whoever set them.
# Checked in EVERY mode, not just run. The reviewer is the one argument a
# person can get wrong -- a login typo, or a team where a user was meant -- and
# the first version only looked at it in run mode, so a dry run reported nothing
# and the mistake surfaced on the real one. Resolving a login is a read.
if [ "${#REVIEWERS[@]}" -gt 0 ] && env_exists; then
  existing="$(gh api "repos/${REPO_SLUG}/environments/${GH_ENV}" \
      --jq '[.protection_rules[]? | select(.type=="required_reviewers") | .reviewers[]?.reviewer.login // .reviewers[]?.reviewer.slug] | join(",")' 2>/dev/null || echo "")"
  if [ -n "$existing" ]; then
    ok "reviewers already set: ${existing}"
    note "not replacing them. Change them in Settings, or --rollback and re-run."
    # Say plainly whether --reviewer changed anything. Otherwise the argument
    # looks accepted when it was a no-op, and someone believes they granted
    # approval rights they did not.
    for r in "${REVIEWERS[@]}"; do
      if printf '%s' "$existing" | grep -qi -- "$r"; then
        note "  ${r} is already among them, so --reviewer ${r} changed nothing"
      else
        warn "  ${r} is NOT among them, and this script will not add it to an"
        warn "  environment that already has approvers. Add it in Settings."
      fi
    done
  elif [ "$MODE" != "run" ]; then
    # Resolve the logins read-only so a typo fails HERE rather than on the run
    # that actually changes something.
    for r in "${REVIEWERS[@]}"; do
      if [ "${r#*/}" != "$r" ]; then
        if gh api "orgs/${r%%/*}/teams/${r#*/}" --jq .id >/dev/null 2>&1; then
          ok "team ${r} resolves"
        else
          warn "team ${r} does NOT resolve -- the real run would fail here"
        fi
      elif gh api "users/${r}" --jq .id >/dev/null 2>&1; then
        ok "user ${r} resolves"
      else
        warn "user ${r} does NOT resolve -- the real run would fail here"
      fi
    done
    would "set reviewer(s) ${REVIEWERS[*]} on '${GH_ENV}'"
  else
    # A team is org/team; anything else is a user. Resolving to ids is required:
    # the API takes numeric ids, not logins.
    payload="$("$PY_BIN" - "$ALLOW_SELF_REVIEW" "${REVIEWERS[@]}" <<'PY'
import json, subprocess, sys
allow_self = sys.argv[1] == "1"
out = []
for r in sys.argv[2:]:
    if "/" in r:
        org, team = r.split("/", 1)
        q = subprocess.run(["gh", "api", f"orgs/{org}/teams/{team}", "--jq", ".id"],
                           capture_output=True, text=True)
        kind = "Team"
    else:
        q = subprocess.run(["gh", "api", f"users/{r}", "--jq", ".id"],
                           capture_output=True, text=True)
        kind = "User"
    if q.returncode != 0:
        # ASCII only. Python's stdout and stderr are cp1252 on Windows and
        # cannot encode the marks bash uses, so a non-ASCII message here would
        # replace "cannot resolve reviewer" with a UnicodeEncodeError -- on the
        # error path, where the real message matters most.
        sys.exit(f"cannot resolve reviewer '{r}': {q.stderr.strip()[:200]}")
    out.append({"type": kind, "id": int(q.stdout.strip())})
print(json.dumps({
    "reviewers": out,
    # prevent_self_review blocks the only approver there is when the list has
    # one person, and every deployment then queues forever. --allow-self-review
    # is the escape hatch; adding a second reviewer is the fix.
    "prevent_self_review": not allow_self,
    "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}))
PY
)"
    printf '%s' "$payload" | gh api --method PUT "repos/${REPO_SLUG}/environments/${GH_ENV}" --input - >/dev/null
    ok "set reviewer(s) ${REVIEWERS[*]}$([ "$ALLOW_SELF_REVIEW" -eq 1 ] && echo ' (self-review allowed)')"
  fi
fi

# The tag policy. THE ONE THING THIS SCRIPT MOST LIKELY CANNOT DO, because
# adding a deployment branch policy needs repository admin.
if env_exists; then
  policies="$(gh api "repos/${REPO_SLUG}/environments/${GH_ENV}/deployment-branch-policies" \
      --jq '[.branch_policies[]? | "\(.type):\(.name)"] | join(" ")' 2>/dev/null || echo "")"
  note "current ref policies: ${policies:-<none>}"
  if printf '%s' "$policies" | grep -q "tag:${TAG_POLICY}"; then
    ok "${TAG_POLICY} is admitted"
  elif [ "$MODE" != "run" ]; then
    would "add tag policy ${TAG_POLICY} to '${GH_ENV}'"
  else
    if gh api --method POST \
         "repos/${REPO_SLUG}/environments/${GH_ENV}/deployment-branch-policies" \
         -f "name=${TAG_POLICY}" -f "type=tag" >/dev/null 2>&1; then
      ok "added tag policy ${TAG_POLICY}"
    else
      warn "could not add the tag policy ${TAG_POLICY} -- this needs repository ADMIN"
      note "Until it is added, NO echo release can deploy to '${GH_ENV}', however"
      note "correct everything else is: the environment admits only ${policies:-nothing}."
      note "Ask an admin for: Settings > Environments > ${GH_ENV} > deployment"
      note "branch and tag rules > add tag rule '${TAG_POLICY}'."
    fi
  fi
fi

# ── 6/6  The deploy-role secret ───────────────────────────────────────────────

step "6/6  ${SECRET_NAME}, scoped to '${GH_ENV}'"

# ENVIRONMENT-scoped, not repository-scoped, and that distinction is the point.
# A repo-wide secret is readable by any workflow on any ref, which undoes the
# environment's reviewer -- the reviewer guards the environment, so an ARN that
# does not live behind it is not guarded at all.
#
# Scoping cuts both ways, though, and that is what the stack-qualified name is
# for. An environment-scoped secret also SHADOWS a repository-level one of the
# same name for every job entering this environment, so the scoping that
# protects Echo's ARN is the same mechanism that would hijack product's.
#
# An earlier run of this script set the unqualified name here and did exactly
# that. Warn if that secret is still present, because it is invisible from the
# workflow that suffers for it.
if [ "$GH_ENV" != "dev" ] && \
   gh secret list --env "$GH_ENV" 2>/dev/null | grep -q "^AWS_ROLE_ARN_${ENV_UPPER}\b"; then
  warn "AWS_ROLE_ARN_${ENV_UPPER} is scoped to '${GH_ENV}' and is NOT Echo's."
  warn "It shadows the repository-level secret of that name for every job that"
  warn "enters this environment -- deploy-product-uat.yml reads it. Left over"
  warn "from before this script used a stack-qualified name. Remove it with:"
  warn "  gh secret delete AWS_ROLE_ARN_${ENV_UPPER} --env ${GH_ENV}"
  warn "(--env, never repo-wide: the repository-level secret is product's.)"
fi

if gh secret list --env "$GH_ENV" 2>/dev/null | grep -q "^${SECRET_NAME}\b"; then
  ok "${SECRET_NAME} already set on '${GH_ENV}'"
  note "value not readable; if the role changed, --rollback and re-run"
elif [ "$MODE" = "dry" ]; then
  would "set ${SECRET_NAME}=${ROLE_ARN} on '${GH_ENV}'"
elif [ "$MODE" = "verify" ]; then
  warn "${SECRET_NAME} is not set on '${GH_ENV}' -- every OIDC step reading it would fail"
else
  printf '%s' "$ROLE_ARN" | gh secret set "$SECRET_NAME" --env "$GH_ENV" >/dev/null
  ok "set ${SECRET_NAME} on '${GH_ENV}'"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

step "Summary"
echo "  tfvars env  : ${TFVARS_ENV}  ->  ${PREFIX}-*"
echo "  gh env      : ${GH_ENV}"
echo "  role        : ${ROLE_ARN}"
echo "  policies    : ${POLICY_STEM}-slot-NN-policy, ${BACKEND_POLICY_NAME}"
echo "  documents   : capabilities/infrastructure/${POLICY_STEM}-slot-NN-policy.json"
echo "                ${BACKEND_DOC#"$REPO_ROOT"/}"
echo ""
case "$MODE" in
  dry)    note "DRY RUN — nothing was changed." ;;
  verify) note "VERIFY ONLY — nothing was changed. A green verify is NOT evidence the"
          note "apply will work: it sees only the resources this stack manages, and is"
          note "blind to post-create reads and tag reads. See UIFPE-496." ;;
  *)      note "Next: commit the rendered policy documents, then"
          note "  make -C stacks/echo/infrastructure/terraform init ENV=${TFVARS_ENV}"
          note "and run INFRA=plan before INFRA=apply. Expect to come back here when"
          note "the apply names an action the derivation did not predict." ;;
esac
