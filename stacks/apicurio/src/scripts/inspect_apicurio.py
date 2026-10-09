"""Print every artifact + its properties as Apicurio actually returns them.

Use to confirm whether properties are persisted (seed worked) or not.
"""

import os

import httpx

BASE = os.environ.get("APICURIO_BASE_URL", "http://localhost:8080")
API = f"{BASE.rstrip('/')}/apis/registry/v2"

with httpx.Client(timeout=10.0) as c:
    search = c.get(f"{API}/search/artifacts", params={"limit": 500}).json()
    artifacts = search.get("artifacts", [])

    print(f"\n{'Subject':<42} {'Type':<8} {'Search-props?':<14} {'Meta-props (owner.service)':<32}")
    print("-" * 100)

    for a in artifacts:
        gid = a.get("groupId") or "default"
        aid = a["id"]

        # 1. What does search return?
        search_props = a.get("properties")

        # 2. What does the meta endpoint return?
        meta = c.get(f"{API}/groups/{gid}/artifacts/{aid}/meta").json()
        meta_props = meta.get("properties") or {}

        print(
            f"{aid:<42} {a.get('type','?'):<8} "
            f"{'yes' if search_props else 'NO':<14} "
            f"{meta_props.get('owner.service', '—'):<32}"
        )

    print(f"\nTotal: {len(artifacts)} artifacts")