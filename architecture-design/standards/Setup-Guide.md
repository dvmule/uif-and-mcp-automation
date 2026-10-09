# UIF naming rules — Claude Code setup

Claude Code rule files for the **UIF Resource Naming Convention**, plus the installer and
`PreToolUse` hook that activate them. The convention itself is the standard governed by
this directory; the files here are the machine-readable derivation of it.

The rules give Claude the naming vocabulary as context when you open a Terraform file, a
draft config, or a Glue job. They shape what Claude generates; they do not block
anything. The hook in `hooks/validate_naming.py` is the only part that can refuse an
edit.

**Nothing here is live until you install it.** Claude Code loads rule files from
`~/.claude/rules/` (per machine) or a repository's `.claude/rules/` (per project); markdown
sitting in this directory is inert either way.

## Contents

| Path | Loads when you open |
|---|---|
| `rules/naming/00-core.md` | any UIF file — stem, vocabularies, banned tokens, delimiters, length limits, legacy freeze |
| `rules/naming/compute-etl.md` | Terraform — Glue, Lambda, ECS, ECR, Step Functions |
| `rules/naming/storage.md` | Terraform, `*.tfvars`, drafts — S3 buckets, key prefixes, DynamoDB tables |
| `rules/naming/catalog.md` | Glue jobs, `schemas/**`, registration scripts — catalog databases and tables |
| `rules/naming/eventing.md` | Terraform — EventBridge, SQS, SNS |
| `rules/naming/networking.md` | Terraform — ALB, target group, API Gateway, CloudFront, security groups, VPC endpoints |
| `rules/naming/identity-secrets.md` | any Terraform under an `infrastructure/` tree, `iam*.tf`, secret scripts, Lambda — IAM roles and policies, Secrets Manager |
| `rules/naming/observability.md` | Terraform — log groups, dashboards, alarms |
| `rules/naming/dynamodb-attributes.md` | drafts, `schemas/**` — item attribute naming (guidance only) |
| `hooks/validate_naming.py` | `PreToolUse` guard on `Edit`/`Write`/`MultiEdit` |

## Install

Two scopes. Pick by who the rules are for.

### Per machine — `~/.claude/`

```bash
./installers/install.sh          # macOS / Linux
```
```powershell
.\installers\install.ps1         # Windows
```

Both resolve this directory from their own location, so they work from any working
directory. Copies `rules/naming/` to `~/.claude/rules/naming/` and the hook to `~/.claude/hooks/`,
then writes `~/.claude/rules/naming/.installed-version` (version, commit, date). Plain
copies, no symlinks or junctions — no Administrator or Developer Mode required.

### Per project — the repository's `.claude/`

```bash
./installers/install-project.sh --wire-hook --verify
```

Installs the same package into the repo's own `.claude/`, so the rules that govern a
repository travel with it: one `git pull` and every contributor has them, and a change to
them is a reviewable diff rather than a README instruction some people ran and others did
not. Commit the result — an uncommitted `.claude/` is a per-machine install that merely
happens to live in the project directory.

| Flag | |
|---|---|
| `--wire-hook` | merges the `PreToolUse` guard into `.claude/settings.json`, creating it if absent, using `$CLAUDE_PROJECT_DIR` so the command resolves on every contributor's machine |
| `--verify` | runs two real hook payloads and asserts one is allowed and one denied — the hook always exits 0 by design, so silence is not evidence it works |
| `--check` | reports installed rules, hook and wiring; writes nothing |
| `--from-ref <ref>` | installs the package as it exists on another git ref, via `git show`, leaving your worktree untouched — for working on a branch that predates the current standard (see "Upgrading from 1.x") |

After a first install, `.claude/settings.json` is new, and Claude Code's settings watcher
only watches directories that already had a settings file when the session started. Open
`/hooks` once or restart, or the guard sits on disk inert.

`install-project.sh` also checks three lists against each other: the `stack` tokens in
`00-core.md`, the hook's `STACKS` set, and the `stacks/` directories. `standards-ci.yml`
already guards the first two in the package — but it checks what a given repo *installed*,
which is a different question and the one an operator has. Note that CI only caught this
pair in one direction until 2026-09-14, which is how `apicurio` spent six days sanctioned in
the rule file and denied by the hook. There is no Windows counterpart to this script yet.

## Update

```bash
git pull && ./installers/install.sh
```

Re-running is the update path. `rules/naming/` is **replaced**, not merged, so files
deleted upstream disappear locally.

**Do not edit the installed rules under `~/.claude/` or a repo's `.claude/`.** The next
update overwrites them. Changes go through a PR on this package. This holds even when the
installed copy is demonstrably wrong and the fix is one line — a local patch survives until
the next install, then vanishes without telling anyone, and the defect returns for everybody
who never had the patch.

### Upgrading from 1.x

1.x rules assert two forms the current standard has replaced: the account-id-prefixed
S3 bucket (`283556840805-s3-uif-echo-dev-ui`) and the `gip` segment in secret paths
(`uif/echo/dev/gip/db/tep`). They also name DynamoDB tables after the engine
(`glue-job-metrics`) rather than the concept (`pipeline-metrics`). Until you re-run the
installer you are being given guidance that is wrong, not merely incomplete. Check
`~/.claude/rules/naming/.installed-version`, or `install-project.sh --check` for a
project-scope install.

A long-lived feature branch is the common way to end up here: the branch carries the copy of
this package that existed when it was cut, so `install.sh` from that worktree installs 1.x
however current `develop` is. `install-project.sh --from-ref origin/develop` installs the
current standard without touching the branch.

## Enable the hook

The installer never writes `~/.claude/settings.json` — you may already have hooks there.
Paste the contents of `settings/hooks.snippet.json` into `~/.claude/settings.json`,
merging into any existing `"hooks"` block. Then restart Claude Code.

If `python` is not on your `PATH` under that name, change the `command` in the snippet
(`python3`, or an absolute interpreter path).

Prefer automation: `./installers/install.sh --wire-hook` / `.\installers\install.ps1 -WireHook`
merges the snippet in place and backs up the previous file as `settings.json.bak`.

What the hook refuses:

- renaming a deployed resource whose rename destroys data or forces replacement —
  25 types, including S3 buckets, DynamoDB tables, SQS queues, ECR repositories,
  Secrets Manager secrets, Glue catalog databases and RDS instances. Each denial names
  what would be lost. `00-core.md` allows a rename "through an explicit, planned
  migration", and this is how one declares itself: a `# uif:rename-approved <TICKET>`
  comment on its own line above the resource block, or inside its body. The 8 types whose
  rename destroys data also need the data question answered in the same marker, using
  exactly one of `data:copied`, `data:empty` or `data:accepted-loss` — a closed set, like
  `cadence`, so that prose cannot satisfy it. A safe replacement and a data loss
  therefore cannot be authorised by the same words. The marker authorises one resource,
  is not read inside a `/* */` comment or a heredoc, and stays in the diff for the
  reviewer to see.
- newly introduced names that break the stem, a required suffix, a closed
  vocabulary, or an AWS length limit — 34 resource types
- draft key prefixes that break the layer-specific shapes in `storage.md`

Names already deployed are treated as pre-existing and never judged. That is scoped to
the Terraform **module**, not the file: moving a resource block into a sibling `.tf`
changes no AWS name, so it is not a new name either.

**Names built from a local are judged too** (UIFPE-358). A value like
`"${local.uif_prefix}-gold-transform"` is resolved against the `locals {}` blocks in the
same directory and checked once per environment, so a name that fits in `dev` but breaks
the length limit in `prod` is still caught. The denial names both the literal you wrote
and the value it resolves to.

Resolution is deliberately all-or-nothing: if any part of a name cannot be resolved, the
whole name goes unjudged rather than being checked against tokens you never wrote. That
covers `${each.key}`, a local declared in another module, and — the common case — a local
whose value is an expression rather than a string, as in the echo stack's
`name_prefix = var.name_prefix_override != "" ? ... : "echo-${var.environment}"`, whose
real value lives in `envs/*.tfvars` and differs per environment. The rule files therefore
remain the primary mechanism and the hook a backstop. It no-ops outside a UIF-shaped
layout and fails open on any internal error.

## Troubleshooting

- `/context` lists the rule files currently loaded under **Memory files**. If a rule is
  absent it was never loaded — check the file's `paths:` globs against the file you have
  open, not the rule's contents.
- Path-scoped rules that load everywhere are also a failure. `compute-etl.md` should
  load for `infra/glue_jobs.tf` and *not* for `lambda/pipeline_controller.py`;
  `catalog.md` should do the opposite.
- No naming rules should load in a non-UIF repo.
- Hook not firing: confirm the `command` path resolves, that `~/.claude/hooks/validate_naming.py`
  exists, and that Claude Code was restarted after editing `settings.json`. On Windows,
  if `$HOME` is not expanded by the shell that runs hooks, replace it with an absolute
  path: `python "C:/Users/<you>/.claude/hooks/validate_naming.py"`.
- The hook checks only values an edit *introduces*. Editing a file that already contains
  non-compliant legacy names is expected to pass — that is the legacy rule, not a bug.
