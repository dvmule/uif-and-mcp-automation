from unittest.mock import MagicMock, patch

import pytest

from apicurio_client import ApicurioClient, CompatibilityResult, RegistryNotConfigured
from check_apicurio_compatibility import check_one, load_coordinate, main


def test_client_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("APICURIO_REGISTRY_URL", raising=False)
    with pytest.raises(RegistryNotConfigured):
        ApicurioClient()


def test_main_skips_cleanly_when_unconfigured(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("APICURIO_REGISTRY_URL", raising=False)
    contract = tmp_path / "openapi.yaml"
    contract.write_text("x-registry: {group: product, artifact: x, compatibility: BACKWARD}\n")

    exit_code = main(["prog", str(contract)])

    assert exit_code == 0
    assert "SKIP" in capsys.readouterr().out


def test_load_coordinate_requires_group_and_artifact(tmp_path):
    contract = tmp_path / "openapi.yaml"
    contract.write_text("openapi: 3.1.0\n")
    with pytest.raises(SystemExit):
        load_coordinate(contract)


def test_check_one_passes_on_compatible_change(tmp_path):
    contract = tmp_path / "openapi.yaml"
    contract.write_text("x-registry: {group: product, artifact: x, compatibility: BACKWARD}\n")

    client = MagicMock()
    client.check_compatibility.return_value = CompatibilityResult(compatible=True)

    assert check_one(client, contract) is True
    client.check_compatibility.assert_called_once()
    _, kwargs = client.check_compatibility.call_args
    assert kwargs["group"] == "product"
    assert kwargs["artifact"] == "x"


def test_check_one_fails_on_incompatible_change(tmp_path, capsys):
    contract = tmp_path / "openapi.yaml"
    contract.write_text("x-registry: {group: product, artifact: x, compatibility: BACKWARD}\n")

    client = MagicMock()
    client.check_compatibility.return_value = CompatibilityResult(
        compatible=False, reason="removed required field 'ppid'"
    )

    assert check_one(client, contract) is False
    assert "removed required field" in capsys.readouterr().err


@patch("check_apicurio_compatibility.ApicurioClient")
def test_main_exits_nonzero_when_any_contract_incompatible(mock_client_cls, tmp_path):
    contract = tmp_path / "openapi.yaml"
    contract.write_text("x-registry: {group: product, artifact: x, compatibility: BACKWARD}\n")
    mock_client_cls.return_value.check_compatibility.return_value = CompatibilityResult(
        compatible=False, reason="breaking"
    )

    assert main(["prog", str(contract)]) == 1
