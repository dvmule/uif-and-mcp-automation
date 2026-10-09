import json
from pathlib import Path

import pytest

from detect_changed_contracts import build_matrix, is_contract_file


@pytest.mark.parametrize(
    "path,expected",
    [
        ("domains/product/src/api/openapi.yaml", True),
        ("domains/product/src/events/asyncapi.yaml", True),
        ("domains/product/src/schemas/events/product-changed-data.json", True),
        ("domains/product/src/mcp/mcp-manifest.yaml", True),
        ("domains/product/src/api/README.md", False),
        ("domains/product/src/apps/scripts/validate_gold_schemas.py", False),
        ("stacks/apicurio/standards/guidelines.md", False),
    ],
)
def test_is_contract_file(path, expected):
    assert is_contract_file(path) is expected


def test_build_matrix_resolves_registry_coordinate(tmp_path: Path):
    repo_root = tmp_path
    contract_dir = repo_root / "domains" / "product" / "src" / "api"
    contract_dir.mkdir(parents=True)
    contract_path = contract_dir / "openapi.yaml"
    contract_path.write_text(
        "x-registry:\n  group: product\n  artifact: product-developer-registration-api\n"
        "  compatibility: BACKWARD\nopenapi: 3.1.0\n"
    )

    matrix = build_matrix(repo_root, ["domains/product/src/api/openapi.yaml"])

    assert matrix == [
        {
            "domain": "product",
            "capability": "api",
            "path": "domains/product/src/api/openapi.yaml",
            "artifact_group": "product",
            "artifact_id": "product-developer-registration-api",
            "compatibility": "BACKWARD",
        }
    ]


def test_build_matrix_skips_deleted_files(tmp_path: Path):
    matrix = build_matrix(tmp_path, ["domains/product/src/api/openapi.yaml"])
    assert matrix[0]["artifact_group"] is None


def test_matrix_is_json_serializable(tmp_path: Path):
    matrix = build_matrix(tmp_path, ["domains/product/src/schemas/events/x.json"])
    json.dumps(matrix)  # must not raise
