"""platform_config.py - resolve the per-environment Anypoint platform coordinates.

anypoint-environments.yaml declares, per UIF environment, WHICH coordinates the MCP automation
needs and the architecture values that are design decisions. The environment-specific
coordinates are NOT hardcoded there: they are `${VAR}` placeholders resolved from the process
environment - in GitHub Actions from the selected GitHub ENVIRONMENT's variables (dev today;
uat/prod later without touching this code), locally from the shell. Credentials are never
part of this file; they stay GitHub Environment SECRETS read by the live scripts themselves.

    dev:
      org_id: ${ANYPOINT_ORG_ID}                 # resolved from the environment
      ...
      shared_bridge:                             # architecture: stays literal in Git
        instance_label: uif-domains-mcp

ONE generic resolver: every `${NAME}` anywhere in the environment block is substituted from the
environment; nothing is special-cased per variable. Fail closed, clearly, when a variable is
missing or empty. Validation by KEY (not by variable): keys ending in `_id` must be UUIDs,
`upstream_url` must be an https ORIGIN with no path, `gateway_public_host` a bare host.
Values are never printed by this module (only the variable NAMES); the resolved document is
written ONLY under the build directory (build/mcp/...) as a transient artifact, and every
downstream script receives THAT file through its existing --platform-config argument - the
frozen provisioning code has no environment-variable handling of its own.
"""
import argparse
import os
import re
import sys
from urllib.parse import urlsplit

import yaml

RESOLVED_FILE = "anypoint-environments.resolved.yaml"
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DEFAULT_PLATFORM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anypoint-environments.yaml")


class PlatformError(ValueError):
    """The platform configuration cannot be resolved or is invalid; the message says why."""


def placeholders(node, acc=None):
    """Every variable NAME referenced by a ${NAME} placeholder anywhere under node."""
    acc = set() if acc is None else acc
    if isinstance(node, str):
        acc.update(PLACEHOLDER.findall(node))
    elif isinstance(node, dict):
        for v in node.values():
            placeholders(v, acc)
    elif isinstance(node, list):
        for v in node:
            placeholders(v, acc)
    return acc


def substitute(node, env, missing):
    """node with every ${NAME} replaced from env; names absent or empty are collected in `missing`."""
    if isinstance(node, str):
        def repl(m):
            value = env.get(m.group(1))
            if value is None or str(value).strip() == "":
                missing.add(m.group(1))
                return m.group(0)
            return str(value).strip()
        return PLACEHOLDER.sub(repl, node)
    if isinstance(node, dict):
        return {k: substitute(v, env, missing) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, env, missing) for v in node]
    return node


def validate_environment(block, environment):
    """Shape checks by KEY on a resolved environment block (never assumes every value is a UUID)."""
    problems = []
    for key, value in block.items():
        if isinstance(value, str) and PLACEHOLDER.search(value):
            problems.append("%s still carries an unresolved placeholder" % key)
        if key.endswith("_id") and isinstance(value, str) and not UUID.match(value):
            problems.append("%s must be a UUID (got a %d-character value)" % (key, len(value)))
    if isinstance(block.get("upstream_url"), str):
        parts = urlsplit(block["upstream_url"])
        if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/") or parts.query or parts.fragment:
            problems.append("upstream_url must be an https ORIGIN only (scheme + host, no base path) - the contract's servers[0] owns the base path")
    host = block.get("gateway_public_host")
    if isinstance(host, str) and ("/" in host or ":" in host or not host.strip()):
        problems.append("gateway_public_host must be a bare host name (no scheme, path or port)")
    if problems:
        raise PlatformError("%s: %s" % (environment, "; ".join(problems)))


def resolve_environment(platform_doc, environment, env=None):
    """The resolved environment block (a NEW dict). Fails closed naming every missing variable."""
    env = os.environ if env is None else env
    block = (platform_doc or {}).get(environment)
    if not isinstance(block, dict):
        raise PlatformError("anypoint-environments.yaml has no %r block (DEV-only automation for now)" % environment)
    missing = set()
    resolved = substitute(block, env, missing)
    if missing:
        raise PlatformError("%s: environment variable(s) not set: %s - in GitHub Actions they are the selected GitHub "
                            "ENVIRONMENT's variables (Settings > Environments > %s > Variables); locally export them before a "
                            "dry run. Nothing is invented." % (environment, ", ".join(sorted(missing)), environment))
    validate_environment(resolved, environment)
    return resolved


def write_resolved(platform_path, environment, out_dir, env=None):
    """Resolve `platform_path` for `environment` and write {environment: resolved} to
    <out_dir>/anypoint-environments.resolved.yaml (a build artifact; the checked-in file is only
    READ). Returns the resolved file's path. Prints the variable NAMES used, never values."""
    doc = yaml.safe_load(open(platform_path, encoding="utf-8-sig")) or {}
    names = sorted(placeholders((doc or {}).get(environment) or {}))
    resolved = resolve_environment(doc, environment, env)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, RESOLVED_FILE)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# GENERATED by platform_config.py - %s with its ${VAR} placeholders resolved from the environment\n"
                "# (%s). Build artifact under build/mcp: never committed; the checked-in file is unchanged.\n"
                % (os.path.basename(platform_path), ", ".join(names) or "no placeholders"))
        f.write(yaml.safe_dump({environment: resolved}, sort_keys=False, allow_unicode=True, default_flow_style=False))
    print("platform: %s resolved for %r from %d environment variable(s)%s -> %s"
          % (os.path.basename(platform_path), environment, len(names), (": " + ", ".join(names)) if names else "", path))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--environment", required=True)
    ap.add_argument("--platform-config", default=DEFAULT_PLATFORM, help="the checked-in file with ${VAR} placeholders (read-only)")
    ap.add_argument("--out", required=True, help="build directory for the resolved copy (build/mcp/...)")
    ap.add_argument("--github-output", action="store_true", help="append platform_config, org_id, gateway_public_host to $GITHUB_OUTPUT")
    a = ap.parse_args(argv)
    try:
        path = write_resolved(a.platform_config, a.environment, a.out)
    except PlatformError as e:
        sys.exit("REFUSED (platform config): %s" % e)
    if a.github_output:
        gh = os.environ.get("GITHUB_OUTPUT")
        if not gh:
            sys.exit("REFUSED: --github-output needs $GITHUB_OUTPUT")
        block = (yaml.safe_load(open(path, encoding="utf-8")) or {})[a.environment]
        with open(gh, "a", encoding="utf-8") as f:
            f.write("platform_config=%s\n" % path)
            f.write("org_id=%s\n" % block["org_id"])
            f.write("gateway_public_host=%s\n" % block.get("gateway_public_host", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
