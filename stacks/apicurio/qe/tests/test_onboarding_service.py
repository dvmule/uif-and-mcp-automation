"""Tests for OnboardingService.simulate_deploy."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.clients.apicurio_client import ApicurioError
from app.clients.cmdb_client import CmdbCi
from app.schemas.service import OnboardManifest, SchemaRegistrationInput
from app.services.onboarding_service import OnboardingService


def _make_service(*, apicurio_meta=None, apicurio_raises=None, cmdb_ci=None, create_ok=True):
    apicurio = AsyncMock()
    if apicurio_raises:
        apicurio.get_artifact_metadata.side_effect = apicurio_raises
    else:
        apicurio.get_artifact_metadata.return_value = apicurio_meta or {"type": "AVRO"}
    if create_ok:
        apicurio.create_artifact.return_value = {"id": "test", "type": "AVRO"}
    else:
        apicurio.create_artifact.side_effect = ApicurioError("create failed")

    cmdb = AsyncMock()
    cmdb.resolve_urn.return_value = cmdb_ci

    return OnboardingService(apicurio=apicurio, cmdb=cmdb)


_CI = CmdbCi(
    ci_id="CI0093422",
    name="enrollment-svc",
    domain="learning",
    team_id="TM-LEARNING-PLATFORM",
    team_email="learning-platform@pearson.com",
    urn="urn:pearson:service:learning:enrollment-svc",
    produces=["learning.learner.enrolled-value"],
    consumes=["product.product.created-value"],
)


@pytest.mark.asyncio
async def test_registered_topic_returns_registered_true():
    svc = _make_service(apicurio_meta={"type": "AVRO"}, cmdb_ci=_CI)
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value"],
        consumes=[],
    )
    result = await svc.simulate_deploy(manifest)
    assert result.produces[0].registered is True
    assert result.produces[0].schema_type == "AVRO"
    assert result.produces[0].drift is None


@pytest.mark.asyncio
async def test_unregistered_produced_topic_flags_d6():
    svc = _make_service(
        apicurio_raises=ApicurioError("404 Not Found"),
        cmdb_ci=_CI,
    )
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value"],
        consumes=[],
    )
    result = await svc.simulate_deploy(manifest)
    assert result.produces[0].registered is False
    assert result.produces[0].drift == "D6"


@pytest.mark.asyncio
async def test_unregistered_consumed_topic_no_d6():
    svc = _make_service(
        apicurio_raises=ApicurioError("404 Not Found"),
        cmdb_ci=_CI,
    )
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=[],
        consumes=["product.product.created-value"],
    )
    result = await svc.simulate_deploy(manifest)
    assert result.consumes[0].registered is False
    assert result.consumes[0].drift is None


@pytest.mark.asyncio
async def test_resolved_ci_populates_identity():
    svc = _make_service(apicurio_meta={"type": "AVRO"}, cmdb_ci=_CI)
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value"],
        consumes=["product.product.created-value"],
    )
    result = await svc.simulate_deploy(manifest)
    assert result.ci_id == "CI0093422"
    assert result.name == "enrollment-svc"


@pytest.mark.asyncio
async def test_unknown_urn_leaves_identity_null():
    svc = _make_service(apicurio_meta={"type": "AVRO"}, cmdb_ci=None)
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:unknown:ghost-svc",
        produces=["some.topic-value"],
        consumes=[],
    )
    result = await svc.simulate_deploy(manifest)
    assert result.ci_id is None
    assert result.name is None


# test_graph_update_failure_does_not_raise was deleted with the graph write it
# covered (ADR-0007) — simulate_deploy no longer has a failure path to swallow.


@pytest.mark.asyncio
async def test_mixed_registered_and_missing_topics():
    apicurio = AsyncMock()

    async def _meta(group_id, artifact_id):
        if artifact_id == "learning.learner.enrolled-value":
            return {"type": "AVRO"}
        raise ApicurioError("404 Not Found")

    apicurio.get_artifact_metadata.side_effect = _meta
    apicurio.create_artifact.return_value = {"id": "test"}
    cmdb = AsyncMock()
    cmdb.resolve_urn.return_value = _CI
    svc = OnboardingService(apicurio=apicurio, cmdb=cmdb)
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value", "learning.progress.updated-value"],
        consumes=[],
    )
    result = await svc.simulate_deploy(manifest)
    registered = [t for t in result.produces if t.registered]
    missing = [t for t in result.produces if not t.registered]
    assert len(registered) == 1
    assert len(missing) == 1
    assert missing[0].drift == "D6"


_AVRO_CONTENT = '{"type": "record", "name": "Enrolled", "fields": []}'
_REG_INPUT = SchemaRegistrationInput(
    content=_AVRO_CONTENT,
    data_classification="internal",
    lifecycle="active",
)


@pytest.mark.asyncio
async def test_schema_registration_in_simulate_deploy():
    apicurio = AsyncMock()
    apicurio.get_artifact_metadata.return_value = {"type": "AVRO"}
    apicurio.create_artifact.return_value = {"id": "learning.learner.enrolled-value"}
    cmdb = AsyncMock()
    cmdb.resolve_urn.return_value = _CI
    svc = OnboardingService(apicurio=apicurio, cmdb=cmdb)
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value"],
        consumes=[],
        schema_registrations={"learning.learner.enrolled-value": _REG_INPUT},
    )
    result = await svc.simulate_deploy(manifest)
    assert len(result.registrations) == 1
    assert result.registrations[0].registered is True
    assert result.registrations[0].schema_type == "AVRO"
    apicurio.create_artifact.assert_called_once()


@pytest.mark.asyncio
async def test_schema_registration_marks_just_registered():
    apicurio = AsyncMock()
    apicurio.get_artifact_metadata.return_value = {"type": "AVRO"}
    apicurio.create_artifact.return_value = {"id": "learning.learner.enrolled-value"}
    cmdb = AsyncMock()
    cmdb.resolve_urn.return_value = _CI
    svc = OnboardingService(apicurio=apicurio, cmdb=cmdb)
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value"],
        consumes=[],
        schema_registrations={"learning.learner.enrolled-value": _REG_INPUT},
    )
    result = await svc.simulate_deploy(manifest)
    assert result.produces[0].just_registered is True


@pytest.mark.asyncio
async def test_schema_registration_partial_failure_continues():
    apicurio = AsyncMock()
    apicurio.get_artifact_metadata.return_value = {"type": "AVRO"}
    apicurio.create_artifact.side_effect = ApicurioError("create failed")
    cmdb = AsyncMock()
    cmdb.resolve_urn.return_value = _CI
    svc = OnboardingService(apicurio=apicurio, cmdb=cmdb)
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value"],
        consumes=[],
        schema_registrations={"learning.learner.enrolled-value": _REG_INPUT},
    )
    result = await svc.simulate_deploy(manifest)
    assert result.registrations[0].registered is False
    assert result.registrations[0].error is not None
    assert result.ci_id == "CI0093422"  # deploy still proceeds


@pytest.mark.asyncio
async def test_schema_registration_ignores_consumes_keys():
    apicurio = AsyncMock()
    apicurio.get_artifact_metadata.return_value = {"type": "AVRO"}
    apicurio.create_artifact.return_value = {"id": "x"}
    cmdb = AsyncMock()
    cmdb.resolve_urn.return_value = _CI
    svc = OnboardingService(apicurio=apicurio, cmdb=cmdb)
    manifest = OnboardManifest(
        service_urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value"],
        consumes=["product.product.created-value"],
        # attaching schema to a consumes topic — should be silently ignored
        schema_registrations={"product.product.created-value": _REG_INPUT},
    )
    result = await svc.simulate_deploy(manifest)
    assert len(result.registrations) == 0
    apicurio.create_artifact.assert_not_called()


@pytest.mark.asyncio
async def test_register_single_schema_unknown_urn():
    svc = _make_service(cmdb_ci=None)
    from app.schemas.service import SchemaRegistrationInput
    reg = SchemaRegistrationInput(content=_AVRO_CONTENT, data_classification="internal")
    result = await svc.register_single_schema("default", "some.topic-value", "urn:unknown", reg)
    assert result.registered is False
    assert "Unknown service URN" in (result.error or "")
