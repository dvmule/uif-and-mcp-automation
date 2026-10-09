# platform/python — shared runtime code for every stack

`platform/` holds what more than one stack needs and no stack owns. `terraform/`
carries the infrastructure primitives (OIDC federation, IRSA); this folder carries
the **runtime** ones: small, stdlib-only Python packages that a stack copies into
its Lambda zip unchanged.

| Package or module | What it does | Used by |
|---|---|---|
| `pearson_notifications/` | Pearson Notifications API client: mints and caches a PI token, sends a `FileShare.StandardEmail` event, re-mints once on 401 | beacon's export mail (`stacks/beacon/.../email_delivery.py`), echo's gold alert mail (`stacks/echo/.../gold_alert_notify.py`) |
| `jwt_verify.py` | Bearer-token verification: RS256/384/512 signatures against a cached JWKS, claim checks, and a subject allow-list. Verification needs no crypto library — `pow()` and `hashlib` are enough for a public key | the gold retrieval API (`stacks/echo/.../gold_serving_api.py`), the marketing-copy API (`domains/product/.../marketing_copy_api.py`) |

## Rules

- **Stdlib only.** Consumers are pure-code Lambda zips with nothing vendored, and
  the same file has to run under Glue. A boto3 or requests import here breaks one
  of them at run time, not at plan time; `qe/` pins the import set.
- **A domain may consume one too.** `jwt_verify` is shared by a stack's Lambda
  and a domain's, which is exactly why it lives here: a domain takes no runtime
  dependency on a stack's code (ADR-0004), and `platform/` is owned by neither.
- **No stack knowledge.** A package here does not know what an export or a gold
  alarm is. Templates, subjects and links belong to the stack that sends them.
- **Copy, don't import across the tree at deploy time.** Each consumer's packaging
  step (`deploy-beacon-dev.yml` zip staging, `terraform-gold/alerts.tf`
  `archive_file`) copies the package directory into the zip root. Tests reach it
  through each stack's `qe/conftest.py`, which puts `platform/python` on `sys.path`.

## Tests

The packages' tests live in `stacks/echo/qe/gold/` (`test_pearson_notifications.py`,
`test_platform_consumers_wired.py`), NOT here: the repo runs pytest in importlib
mode, which would register a test under `platform/` as a module named `platform`
and shadow the standard library's. The gold deploy workflow runs that suite on
every change to `platform/python/**`; beacon's own `test_email_delivery.py` covers
its use of the client.
