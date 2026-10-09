#!/usr/bin/env python3
"""SDK / type generation — fetches a contract fresh from the registered
Apicurio artifact (never the local file) and invokes the language-
appropriate generator. Output is written to --out-dir; the caller (the
generate-sdks.yml workflow) uploads that directory as a CI build artifact.
Generated code is never committed into a domain (apicurio-governance.md §5).

Requires the `openapi-generator-cli` (Java, via npx or a container) on PATH
for sync (OpenAPI) contracts, and `datamodel-code-generator` (Python) /
`quicktype` (TS/Java) for async (JSON Schema) contracts — installed by the
generate-sdks.yml workflow, not by this repo's own requirements.txt, since
they are Node/JVM tools, not Python libraries.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from apicurio_client import ApicurioClient, RegistryNotConfigured

GENERATORS = {
    "typescript": ("openapi-generator-cli", ["generate", "-g", "typescript-fetch"]),
    "java": ("openapi-generator-cli", ["generate", "-g", "java"]),
    "python": ("openapi-generator-cli", ["generate", "-g", "python"]),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--language", required=True, choices=sorted(GENERATORS))
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    try:
        client = ApicurioClient()
    except RegistryNotConfigured as exc:
        print(f"SKIP: {exc}")
        return 0

    content = client.get_latest_content(args.group, args.artifact)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tmp:
        tmp.write(content)
        contract_path = tmp.name

    binary, base_args = GENERATORS[args.language]
    cmd = [binary, *base_args, "-i", contract_path, "-o", str(args.out_dir)]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
