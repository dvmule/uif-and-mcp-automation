"""Publish safety guard (UIF versioning decision, 2026-09) and the documented
Exchange API v2 request shape for both assets — pure tests, no network anywhere.

  absent    -> publish
  identical -> no-op
  different -> conflict (fail closed; message demands an explicit versioning
               decision and never tells the domain to bump info.version)
"""
import os

import pytest

import exchange_publish
from exchange_publish import CONFLICT_MESSAGE, decide_publish

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
from anypoint_fake import PRODUCT_SPEC
CONTRACT = str(PRODUCT_SPEC)


def test_absent_version_publishes():
    assert decide_publish(None, b'{"tools": []}') == "publish"


def test_identical_content_is_noop():
    content = b'{"tools": ["get_products"]}'
    assert decide_publish(content, content) == "noop"


def test_different_content_is_conflict():
    assert decide_publish(b'{"tools": ["a"]}', b'{"tools": ["b"]}') == "conflict"


def test_conflict_message_demands_explicit_decision_only():
    for kind in ("REST", "MCP"):
        msg = CONFLICT_MESSAGE % (kind, "1.0.0")
        assert "explicit versioning decision" in msg
        assert "conflicts with newly generated content" in msg
        assert kind in msg and "1.0.0" in msg
        # the guard must NOT prescribe a contract bump — that is the domain's call
        assert "info.version" not in msg and "bump" not in msg.lower()


def test_rest_publish_fields_are_the_documented_oas_form():
    """Documented Exchange v2 REST asset creation: name, description,
    properties.mainFile, properties.apiVersion; classifier `oas` on the file
    part; NO `type` field (type identifies the MCP asset only)."""
    fields = exchange_publish.rest_publish_fields("product-api", "desc", "v1")
    assert fields == {"name": "product-api", "description": "desc",
                      "properties.mainFile": "openapi.yaml", "properties.apiVersion": "v1"}
    assert "type" not in fields
    assert exchange_publish.PROJECTION_FILENAME == fields["properties.mainFile"]


def test_multipart_encoder_emits_fields_and_file_parts():
    body, ctype = exchange_publish._multipart({"name": "a", "type": "mcp"},
                                              {"files.oas.yaml": ("openapi.yaml", b"openapi: 3.0.3\n", "application/x-yaml")})
    boundary = ctype.split("boundary=")[1]
    assert ctype.startswith("multipart/form-data; boundary=")
    assert body.count(("--" + boundary).encode()) == 4  # 3 parts + terminator
    assert b'name="files.oas.yaml"; filename="openapi.yaml"' in body
    assert b"openapi: 3.0.3\n" in body
    assert body.endswith(("--%s--\r\n" % boundary).encode())


def _contract_version():
    """The fixture contract's own info.version - read, never hardcoded: the Product
    contract evolves independently of this shared suite."""
    import yaml
    return str(yaml.safe_load(open(CONTRACT, encoding="utf-8-sig"))["info"]["version"])


def test_build_rest_projection_pairs_asset_version_with_contract(tmp_path):
    """The REST asset version must equal the info.version of the file projected; a
    mismatch fails closed BEFORE any artifact is produced."""
    from generate_mcp_metadata import SpecLoader, iter_operations
    version = _contract_version()
    content, stats = exchange_publish.build_rest_projection(CONTRACT, version)
    assert content.startswith(b"openapi: 3.0.3")
    operations = len(list(iter_operations(SpecLoader(CONTRACT).entry)))  # derived from the contract, never a literal
    assert stats["info_version"] == version and stats["operations"] == operations >= 1
    with pytest.raises(SystemExit) as e:
        exchange_publish.build_rest_projection(CONTRACT, "9.9.9")
    assert "version guard" in str(e.value)


def test_build_rest_projection_fails_closed_on_untranslatable_contract(tmp_path):
    spec = tmp_path / "openapi.yaml"
    spec.write_text("openapi: 3.1.0\ninfo: {title: t, version: 1.0.0}\npaths: {}\n"
                    "components: {schemas: {A: {type: [string, integer]}}}\n")
    with pytest.raises(SystemExit) as e:
        exchange_publish.build_rest_projection(str(spec), "1.0.0")
    assert "OAS 3.0 projection" in str(e.value)


def test_rest_dry_run_writes_artifact_and_sends_nothing(tmp_path, capsys, monkeypatch):
    """--dry-run (default) must produce the projection artifact and the exact
    request preview WITHOUT any network call — not even a token request."""
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call in dry-run")))
    version = _contract_version()
    rc = exchange_publish.main(["rest", "--spec", CONTRACT, "--out", str(tmp_path),
                                "--org-id", "org", "--asset-id", "product-api", "--asset-version", version])
    assert rc == 0
    artifact = tmp_path / "openapi-3.0-projection.yaml"
    assert artifact.exists() and artifact.read_bytes().startswith(b"openapi: 3.0.3")
    out = capsys.readouterr().out
    assert "POST https://anypoint.mulesoft.com/exchange/api/v2/organizations/org/assets/org/product-api/%s" % version in out
    assert "-F properties.mainFile=openapi.yaml" in out
    assert "-F properties.apiVersion=v%s" % version.split(".")[0] in out  # Exchange apiVersion = v<major of the asset version>
    assert "-F files.oas.yaml=@" in out and "DRY-RUN: nothing sent" in out
    assert "-F type=" not in out


def test_execute_without_live_latch_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_LIVE_ENABLED", raising=False)
    with pytest.raises(SystemExit) as e:
        exchange_publish.main(["rest", "--spec", CONTRACT, "--out", str(tmp_path), "--org-id", "org",
                               "--asset-id", "product-api", "--asset-version", _contract_version(), "--execute"])
    assert "MCP_LIVE_ENABLED" in str(e.value)


def test_no_legacy_cli_machinery_remains():
    """The API Catalog CLI path is retired (cannot parse OpenAPI 3.1); none of
    its machinery may resurface in the publisher."""
    for name in ("resolve_launcher", "ensure_cli", "rest_cli_args", "subprocess_env",
                 "NPX_PACKAGE", "CLI_BINARY", "write_descriptor", "descriptor_content",
                 "check_descriptor_version", "rest_publish_outcome"):
        assert not hasattr(exchange_publish, name), name
