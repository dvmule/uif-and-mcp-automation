from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from sync_to_glue_schema_registry import map_compatibility, sync_one


@pytest.mark.parametrize(
    "apicurio_mode,glue_mode",
    [
        ("BACKWARD", "BACKWARD"),
        ("BACKWARD_TRANSITIVE", "BACKWARD_ALL"),
        ("FORWARD", "FORWARD"),
        ("FORWARD_TRANSITIVE", "FORWARD_ALL"),
        ("FULL", "FULL"),
        ("FULL_TRANSITIVE", "FULL_ALL"),
        ("NONE", "NONE"),
        ("SOMETHING_UNKNOWN", "BACKWARD"),  # safe default
    ],
)
def test_compatibility_mode_mapping(apicurio_mode, glue_mode):
    assert map_compatibility(apicurio_mode) == glue_mode


def _not_found_error(op_name: str) -> ClientError:
    return ClientError({"Error": {"Code": "EntityNotFoundException", "Message": "not found"}}, op_name)


def test_sync_one_creates_schema_when_neither_schema_nor_registry_exists():
    apicurio = MagicMock()
    apicurio.get_latest_content.return_value = '{"type": "object"}'
    apicurio.get_compatibility_rule.return_value = "BACKWARD"

    glue = MagicMock()
    glue.get_schema_by_definition.side_effect = _not_found_error("GetSchemaByDefinition")
    glue.get_schema.side_effect = _not_found_error("GetSchema")

    outcome = sync_one(apicurio, glue, "uif-events", "product", "product-changed-data")

    assert outcome == "created"
    glue.create_schema.assert_called_once()
    _, kwargs = glue.create_schema.call_args
    assert kwargs["SchemaName"] == "product.product-changed-data"
    assert kwargs["Compatibility"] == "BACKWARD"
    assert kwargs["DataFormat"] == "JSON"


def test_sync_one_registers_new_version_when_schema_exists_but_content_changed():
    apicurio = MagicMock()
    apicurio.get_latest_content.return_value = '{"type": "object", "properties": {"new": {}}}'
    apicurio.get_compatibility_rule.return_value = "FULL"

    glue = MagicMock()
    glue.get_schema_by_definition.side_effect = _not_found_error("GetSchemaByDefinition")
    glue.get_schema.return_value = {"SchemaArn": "arn:aws:glue:...:schema/uif-events/product.product-changed-data"}

    outcome = sync_one(apicurio, glue, "uif-events", "product", "product-changed-data")

    assert outcome == "registered new version"
    glue.register_schema_version.assert_called_once()
    glue.create_schema.assert_not_called()


def test_sync_one_is_idempotent_when_content_unchanged():
    apicurio = MagicMock()
    apicurio.get_latest_content.return_value = '{"type": "object"}'
    apicurio.get_compatibility_rule.return_value = "BACKWARD"

    glue = MagicMock()
    glue.get_schema_by_definition.return_value = {"SchemaVersionId": "abc-123"}

    outcome = sync_one(apicurio, glue, "uif-events", "product", "product-changed-data")

    assert "unchanged" in outcome
    glue.create_schema.assert_not_called()
    glue.register_schema_version.assert_not_called()


def test_sync_one_reraises_unexpected_client_errors():
    apicurio = MagicMock()
    apicurio.get_latest_content.return_value = "{}"
    apicurio.get_compatibility_rule.return_value = "BACKWARD"

    glue = MagicMock()
    glue.get_schema_by_definition.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "nope"}}, "GetSchemaByDefinition"
    )

    with pytest.raises(ClientError):
        sync_one(apicurio, glue, "uif-events", "product", "product-changed-data")
