"""Smoke test — try body-shaped references against Apicurio v2."""

import json
from pathlib import Path

import httpx

API_URL = "http://localhost:8080/apis/registry/v2"
GROUP = "default"
ARTEFACT = "product.product.created-value"
SOURCE = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "events"
    / "product.product.created-value.avsc"
)

content = SOURCE.read_text()

body = {
    "type": "AVRO",
    "content": content,
    "references": [
        {
            "name": "com.pearson.product.canonical.ProductSummary",
            "groupId": GROUP,
            "artifactId": "product.summary-value",
            "version": "1",
        }
    ],
}

with httpx.Client(timeout=30.0) as c:
    # Delete first so we're starting clean
    print("→ DELETE existing artefact (if any)")
    d = c.delete(f"{API_URL}/groups/{GROUP}/artifacts/{ARTEFACT}")
    print(f"  status: {d.status_code}\n")

    # Try body-shaped registration with extended content type
    print("→ POST with body-shaped references, application/create.extended+json")
    r = c.post(
        f"{API_URL}/groups/{GROUP}/artifacts",
        params={"ifExists": "FAIL"},
        content=json.dumps(body),
        headers={
            "Content-Type": "application/create.extended+json",
            "X-Registry-ArtifactId": ARTEFACT,
            "X-Registry-ArtifactType": "AVRO",
        },
    )
    print(f"  status: {r.status_code}")
    print(f"  body:   {r.text[:300]}\n")

    # Inspect what landed
    print("→ Check artefact meta")
    m = c.get(f"{API_URL}/groups/{GROUP}/artifacts/{ARTEFACT}/meta")
    if m.status_code == 200:
        meta = m.json()
        refs = meta.get("references", "<no references key>")
        print(f"  references field: {json.dumps(refs)}")
    else:
        print(f"  status: {m.status_code}")