"""Metadata block validator for registered schemas.

Reads from the schema body (authoritative per ADR-0001). Verifies that
Apicurio `properties` match the body. A mismatch is a hard failure.

Two body shapes carry the metadata block and both are validated: Avro, which
declares its fields in `fields`, and JSON Schema, which declares them in
`properties`. `_top_level_field_names` is the one place that difference is
resolved, so the two shapes cannot drift into disagreeing about the same
contract.

A third JSON shape is a gold attribute mapping, whose own contract never asked
it to carry a metadata block, so it is validated against
`standards/gold-attribute-mapping.schema.json` instead. See `_validate_mapping`.
Note the trap in that sentence: registration STAMPS a metadata block onto every
JSON body, mappings included, so a *registered* mapping does have one even
though its source file does not. The rules that read the stamp are shared with
the main path (`_check_owner`) rather than duplicated, and each is guarded so an
un-stamped source file is not failed for lacking one.

PROTOBUF, GRAPHQL, XSD, OPENAPI and ASYNCAPI remain unimplemented: they emit a
`schema_type_unsupported` failure so the operator is informed rather than
silently skipped.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

from app.clients.apicurio_client import ApicurioClient, ApicurioError
from app.clients.cmdb_client import CmdbClient
from app.core.logging import get_logger
from app.schemas.validation import ValidationFailure, ValidationResult
from app.services.validation_cache import ValidationCache

logger = get_logger(__name__)

_VALID_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
_VALID_LIFECYCLES = {"active", "deprecated"}

#: ADR-0023 subject grammar: group = {domain}, artifact = {domain}.{entity}.{type}.
#:
#: Held as a literal rather than read from
#: stacks/apicurio/standards/subject-naming.cases.json, because that folder is
#: not shipped in the service image and a missing file at runtime would fail
#: every validation. The corpus stays the shared contract: a test asserts this
#: pattern and vocabulary equal it, so a drift here fails the build (the
#: ADR-0014 precedent — one table, plus a test that the docs and the code
#: agree). Non-capturing groups only, so the same string is valid in the UI's
#: JavaScript.
#: The type is a closed CHANNEL set crossed with a closed OPERATION set, plus
#: the two unsuffixed types. Held as two lists so adding a channel is one edit
#: in one place -- and a deliberate one, the same gate the naming rules put on
#: the {stack} token: a new channel is not a valid type until it is added here.
_SUBJECT_CHANNELS = ("api", "event", "message")
_SUBJECT_OPERATIONS = ("created", "updated")
_SUBJECT_TYPES = ("mapping", "gold") + tuple(
    f"{channel}-{operation}"
    for channel in _SUBJECT_CHANNELS
    for operation in _SUBJECT_OPERATIONS
)

#: The bare channel tokens ADR-0023 shipped with, kept so the old coordinates
#: are REJECTED rather than ignored.
#:
#: The claim test below is `parts[2] in <types>`. Drop `api` from the
#: vocabulary and nothing claims `product.product.api` any more, so it passes
#: validation as though governed by some other standard -- the superseded name
#: going quiet is exactly what a migration cannot afford. Listing them keeps
#: them claimed, and the message names the replacement.
_SUPERSEDED_TYPES = _SUBJECT_CHANNELS

_SUBJECT_SHAPE = re.compile(
    r"^[a-z][a-z0-9]*\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.(?:"
    + "|".join(_SUBJECT_TYPES)
    + r")$"
)


def _is_mapping_body(body: dict) -> bool:
    """True when this body is a gold attribute mapping.

    By SHAPE, never by artifact name. The repository carries two naming
    conventions for the same thing — `product.product.mapping` is the dev
    registry's coordinate, `product-gold-attribute-mapping` is what the files'
    own `registry.artifact` declares — so a rule keyed on either one breaks on
    the other. The UI's `isMappingBody` implements this same predicate; the
    design spec §3.1 is their shared definition.

    `columns` must be a LIST. A body carrying `columns: {}` is not a mapping,
    and taking it for one would have the mapping rules read a dict's keys as
    column entries.
    """
    schema_ref = body.get("$schema")
    if isinstance(schema_ref, str) and "gold-attribute-mapping" in schema_ref:
        return True
    return (
        isinstance(body.get("domain"), str)
        and isinstance(body.get("table"), str)
        and isinstance(body.get("columns"), list)
    )


def _named_children(node: object, _depth: int = 0) -> dict[str, object]:
    """The named children of a schema node, as {name: child node}.

    Avro declares them in `fields[].name` with the child under that field's
    `type`; JSON Schema declares them in `properties`, where the child is the
    subschema itself. A body is never both, so merging is safe.

    **An array is TRANSPARENT.** `contributors.contributorGivenName` names a
    field of the array's *element*, because the array itself has no fields and
    making the author write the element in would add a segment that carries no
    information. Note the shape this has to survive: product's `contributors`
    declares `type: ["array", "null"]`, so the `type` keyword is a union and
    only the sibling `items` identifies it as an array — which is why descent
    keys off `items`, never off the `type` string.

    Unions are followed too (Avro's `["null", {...}]`), so an optional record
    resolves the same as a required one.
    """
    if _depth > 6 or node is None:                  # cycle/runaway guard
        return {}
    if isinstance(node, list):                      # a union — try each branch
        merged: dict[str, object] = {}
        for branch in node:
            merged.update(_named_children(branch, _depth + 1))
        return merged
    if not isinstance(node, dict):
        return {}

    children: dict[str, object] = {}
    properties = node.get("properties")
    if isinstance(properties, dict):
        children.update(properties)
    for field in node.get("fields") or []:
        if isinstance(field, dict) and field.get("name"):
            children[field["name"]] = field.get("type")

    # Transparent hops: an array's element, and an Avro field's type wrapper.
    if node.get("items") is not None:
        children.update(_named_children(node["items"], _depth + 1))
    inner = node.get("type")
    if isinstance(inner, (dict, list)):
        children.update(_named_children(inner, _depth + 1))
    return children


def _top_level_field_names(body: dict) -> set[str]:
    """Top-level field names, whichever shape declares them.

    Delegates to `_named_children` so there is ONE definition of "what are
    this node's fields" rather than two that can drift — a test asserts the
    two agree.
    """
    return set(_named_children(body))


def _pii_path_segments(path: str) -> list[str]:
    """A piiFields path split into field names, or [] if it names nothing.

    A leading `$.` is stripped, then each segment loses any array subscript:
    `contributors[]` and `contributors[0]` both address the same field, since
    a subscript picks an element and every element has the same fields.

    Returning [] is meaningful — it is how `""`, `"$."` and `"..."` are told
    apart from a real path. Those used to pass silently, so a PII declaration
    that named nothing at all looked like a declaration.
    """
    bare = path[2:] if path.startswith("$.") else path
    segments = []
    for raw in bare.split("."):
        segment = re.sub(r"(\[[^\]]*\])+$", "", raw.strip())
        if segment:
            segments.append(segment)
    return segments


async def _check_owner(
    owner_service: str | None,
    cmdb: CmdbClient,
) -> list[ValidationFailure]:
    """The owner rule, in ONE place, for every artifact shape.

    A mapping's metadata block is stamped at registration rather than authored
    (see `_validate_mapping`), but the question asked of it is identical: does
    this artifact name a service, and is that service real? Two copies of that
    question would drift — and the mapping path had no copy at all until
    UIFPE-619, so a mapping owned by a deactivated CI was never reported.
    """
    if not owner_service:
        return [ValidationFailure(
            rule="owner_missing",
            message="metadata.owner.service is absent.",
        )]
    if await cmdb.get_by_ci_id(owner_service) is None:
        return [ValidationFailure(
            rule="owner_unresolved",
            message=(
                f"metadata.owner.service '{owner_service}' does not "
                "resolve to an active CMDB CI."
            ),
        )]
    return []


def _pii_path_resolves(body: dict, segments: list[str]) -> bool:
    """Walk the WHOLE path, not just its first segment.

    The first-segment-only check this replaces meant `contributors.anyTypo`
    passed: the rule's job is to prove a declared PII field exists, and below
    the top level it proved nothing.
    """
    node: object = body
    for segment in segments:
        children = _named_children(node)
        if segment not in children:
            return False
        node = children[segment]
    return True


async def validate(
    content: str,
    properties: dict[str, str],
    cmdb: CmdbClient,
    artifact_id: str,
    group_id: str,
    schema_type: str,
) -> ValidationResult:
    """Validate the metadata block for one schema artifact.

    Reads the authoritative metadata from the schema body (ADR-0001).
    Also verifies that Apicurio `properties` match the body — a mismatch
    is a hard failure that would block registration in CI.

    No Apicurio I/O. cmdb is used only to resolve the owner CI ID.
    """
    now = datetime.now(tz=UTC)
    failures: list[ValidationFailure] = []

    # --- Rule: subject shape (ADR-0023) ---
    #
    # Checked BEFORE the schema-type gate below, because a subject's shape is an
    # addressing concern: it is a property of the coordinate, not of the body,
    # so it does not need the body parsed or the type supported. All five
    # ADR-0023 types are JSON Schema, which UIFPE-597 made a supported type, so
    # they now reach the metadata rules below on their own merits — but a
    # subject naming an unsupported type still gets judged here, which a rule
    # placed after the gate could not do.
    #
    # SCOPE, which is the whole of the design here. This registry holds
    # artifacts governed by other standards: the normative
    # `<entity>-<action>-data`, `uif/pearson-event-envelope`,
    # `<domain>-events-asyncapi`, the OpenAPI contracts, and capability
    # artifacts like `legal:vocabulary`. This rule has no authority over any of
    # them, and flagging them would make the compliance verdict lie.
    #
    # So it judges a subject only when the THIRD segment is one of the type
    # tokens, or one of the superseded ones. That is the evidence of claiming
    # this grammar, and it is a deliberately narrow test:
    # `product.product.messaging` (a typo'd token) is NOT caught here. The
    # repository test is the strict one — it knows the full out-of-scope
    # allowlist and can afford to be.
    _parts = artifact_id.split(".")
    _claimed = len(_parts) >= 3 and _parts[2] in (_SUBJECT_TYPES + _SUPERSEDED_TYPES)
    if _claimed and not _SUBJECT_SHAPE.match(artifact_id):
        if _parts[2] in _SUPERSEDED_TYPES:
            _detail = (
                f"the type '{_parts[2]}' is superseded: a channel is now "
                f"registered per operation, so use '{_parts[2]}-created' or "
                f"'{_parts[2]}-updated'"
            )
        else:
            _detail = (
                "it is not '{domain}.{entity}.{type}' — exactly three "
                "dot-separated segments, lowercase, entity kebab-case, type "
                f"one of {', '.join(_SUBJECT_TYPES)}, and no version segment "
                "(the major is the artifact's semver version)"
            )
        failures.append(ValidationFailure(
            rule="subject_shape_invalid",
            message=(
                f"artifact_id '{artifact_id}' declares a type of "
                f"'{_parts[2]}', so it is an ADR-0023 subject, but {_detail}. "
                "See stacks/apicurio/standards/subject-naming.md."
            ),
        ))

    if schema_type not in ("AVRO", "JSON"):
        failures.append(ValidationFailure(
            rule="schema_type_unsupported",
            message=(
                "Metadata validation is only implemented for AVRO and JSON; "
                f"got {schema_type}."
            ),
        ))
        # `failures` already carries any subject failure from above — returning
        # a fresh list here would discard the one rule that DOES apply to a
        # JSON Schema artifact.
        return ValidationResult(
            artifact_id=artifact_id,
            group_id=group_id,
            passed=False,
            failures=failures,
            validated_at=now,
        )

    # --- Parse body ---
    try:
        body = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        failures.append(ValidationFailure(
            rule="body_unparseable",
            message="Schema body is not valid JSON.",
        ))
        return ValidationResult(
            artifact_id=artifact_id,
            group_id=group_id,
            passed=False,
            failures=failures,
            validated_at=now,
        )

    # A gold attribute mapping carries no metadata block — its contract never
    # asked it to — so it is validated against that contract instead. Checked
    # AFTER the parse, because the shape is what decides, not the name.
    if isinstance(body, dict) and _is_mapping_body(body):
        return await _validate_mapping(body, artifact_id, group_id, now, cmdb)

    meta_block = body.get("metadata")
    if not meta_block or not isinstance(meta_block, dict):
        failures.append(ValidationFailure(
            rule="metadata_block_missing",
            message="Schema body has no top-level 'metadata' key.",
        ))
        return ValidationResult(
            artifact_id=artifact_id,
            group_id=group_id,
            passed=False,
            failures=failures,
            validated_at=now,
        )

    owner_block = meta_block.get("owner") or {}
    owner_service: str | None = owner_block.get("service")
    domain: str | None = meta_block.get("domain")
    classification: str | None = meta_block.get("dataClassification")
    lifecycle: str | None = meta_block.get("lifecycle")
    pii_fields: list[str] = meta_block.get("piiFields") or []
    if isinstance(pii_fields, str):
        try:
            pii_fields = json.loads(pii_fields)
        except (json.JSONDecodeError, TypeError):
            pii_fields = [v.strip() for v in pii_fields.split(",") if v.strip()]

    # --- Rule: owner.service present and resolves to an active CMDB CI ---
    failures.extend(await _check_owner(owner_service, cmdb))

    # --- Rule: domain present and matches artifact_id prefix ---
    if not domain:
        failures.append(ValidationFailure(
            rule="domain_missing",
            message="metadata.domain is absent.",
        ))
    else:
        declared_domain = artifact_id.split(".")[0]
        if domain != declared_domain:
            failures.append(ValidationFailure(
                rule="domain_mismatch",
                message=(
                    f"metadata.domain '{domain}' does not match the "
                    f"artifact_id prefix '{declared_domain}'."
                ),
            ))

    # --- Rule: dataClassification is one of the four allowed values ---
    if not classification:
        failures.append(ValidationFailure(
            rule="classification_missing",
            message="metadata.dataClassification is absent.",
        ))
    elif classification.lower() not in _VALID_CLASSIFICATIONS:
        failures.append(ValidationFailure(
            rule="classification_invalid",
            message=(
                f"metadata.dataClassification '{classification}' is not one of "
                f"{sorted(_VALID_CLASSIFICATIONS)}."
            ),
        ))

    # --- Rule: lifecycle present and valid ---
    if not lifecycle:
        failures.append(ValidationFailure(
            rule="lifecycle_missing",
            message="metadata.lifecycle is absent.",
        ))
    elif lifecycle.lower() not in _VALID_LIFECYCLES:
        failures.append(ValidationFailure(
            rule="lifecycle_invalid",
            message=(
                f"metadata.lifecycle '{lifecycle}' is not one of {sorted(_VALID_LIFECYCLES)}."
            ),
        ))

    # --- Rule: every piiFields path resolves to a real field in the body ---
    #
    # This used to check the FIRST dot-segment only, which had three holes:
    # `contributors.anyTypo` passed because nothing below the top level was
    # checked; `contributors[].name` was REJECTED although it names a real
    # field, which produced seven false failures on 2026-09-25; and `""`,
    # `"$."` and `"..."` all passed silently, so an entry naming nothing at
    # all counted as a PII declaration.
    #
    # A declaration that cannot be checked is worse than none, because the
    # artifact is reported compliant either way.
    for path in pii_fields:
        segments = _pii_path_segments(path)
        if not segments:
            failures.append(ValidationFailure(
                rule="pii_field_malformed",
                message=(
                    f"piiField entry '{path}' names no field. Write a dotted "
                    "path to the field, optionally prefixed '$.' — an empty "
                    "or separator-only entry declares nothing."
                ),
            ))
            continue
        if not _pii_path_resolves(body, segments):
            failures.append(ValidationFailure(
                rule="pii_field_not_found",
                message=(
                    f"piiField path '{path}' does not resolve to a field in "
                    f"the schema body: '{'.'.join(segments)}' was followed as "
                    "far as it could be. Arrays are transparent, so name the "
                    "field directly ('contributors.contributorGivenName')."
                ),
            ))

    # --- Rule: body and Apicurio properties must agree (ADR-0001) ---
    mismatches = _check_properties_mismatch(meta_block, owner_block, properties)
    failures.extend(mismatches)

    return ValidationResult(
        artifact_id=artifact_id,
        group_id=group_id,
        passed=len(failures) == 0,
        failures=failures,
        validated_at=now,
    )


async def _validate_mapping(
    body: dict,
    artifact_id: str,
    group_id: str,
    now: datetime,
    cmdb: CmdbClient,
) -> ValidationResult:
    """Validate a gold attribute mapping against its own contract.

    Three rules, and deliberately only three. `sortKey` coverage,
    `pick.partitionBy` coverage, the prose-transform ban, `{"rule"}` reference
    resolution and the `sqlExpr` allowlist all live in CI —
    `standards/gold-attribute-mapping.md` places them there, in `jsonschema`
    validation plus `domains/product/qe/test_intake_hygiene.py`, and names
    which copy is authoritative. Re-implementing them here would make a second
    copy of the contract to keep in step.

    What is left is what the REGISTRY alone is positioned to check: that the
    artifact's registry coordinate, its declared domain and its own `registry`
    block all agree.
    """
    failures: list[ValidationFailure] = []

    domain = body.get("domain")
    declared_domain = artifact_id.split(".")[0]
    if domain != declared_domain:
        failures.append(ValidationFailure(
            rule="mapping_domain_mismatch",
            message=(
                f"Mapping declares domain '{domain}' but the artifact_id "
                f"prefix is '{declared_domain}'."
            ),
        ))

    registry = body.get("registry") or {}
    registry_group = registry.get("group")
    # Absent is not a mismatch: the standard says a mapping's registration can
    # stay deferred while the coordinate is still declared elsewhere.
    if registry_group is not None and registry_group != domain:
        failures.append(ValidationFailure(
            rule="mapping_registry_group_mismatch",
            message=(
                f"Mapping's registry.group is '{registry_group}' but its "
                f"declared domain is '{domain}'."
            ),
        ))

    if not body.get("columns"):
        failures.append(ValidationFailure(
            rule="mapping_columns_missing",
            message="Mapping declares no columns.",
        ))

    # --- Rule: the INJECTED domain matches too, when there is one ---
    #
    # A mapping's own contract carries no metadata block, but
    # `inject_metadata` writes one into every JSON body at registration,
    # stamping `domain` from the resolved owner CI. So a registered mapping
    # does have one, and the rule above cannot see a wrong owner: it reads the
    # mapping's own `domain` field, which keeps saying the right thing while
    # the artifact sits under the wrong service.
    #
    # That is not hypothetical. price, program and stock were registered into
    # UAT under product's URN on 2026-09-25, and their three `gold` siblings
    # were reported `domain_mismatch` for two days while the three mappings
    # passed clean.
    #
    # Only this one rule is borrowed from the metadata path, not the whole of
    # it: `piiFields` resolves names against `properties`/`fields`, and a
    # mapping has `columns`, so running that rule here would report
    # `pii_field_not_found` for every field a mapping legitimately declares.
    #
    # Absent is not a mismatch, matching `registry.group` above — an
    # un-injected body is the source file, which was never asked for one.
    injected = body.get("metadata")
    if isinstance(injected, dict) and injected.get("domain") is not None:
        injected_domain = injected.get("domain")
        if injected_domain != declared_domain:
            failures.append(ValidationFailure(
                rule="domain_mismatch",
                message=(
                    f"metadata.domain '{injected_domain}' does not match the "
                    f"artifact_id prefix '{declared_domain}'. The mapping's "
                    f"own domain field says '{domain}', so this is the owner "
                    "CI stamped at registration, not the mapping."
                ),
            ))

    # --- Rule: the stamped owner is a service that still exists ---
    #
    # The rule above compares the stamped domain with the coordinate, which
    # catches a mapping registered under the WRONG service. It cannot catch a
    # mapping registered under the RIGHT service that has since been
    # deactivated: the stamp keeps the domain it was given, so it still
    # agrees, and only asking the CMDB reveals that the owner is gone.
    #
    # Same guard as above — no stamp means this is the source file, which its
    # contract never asked to carry one.
    if isinstance(injected, dict):
        failures.extend(await _check_owner(
            (injected.get("owner") or {}).get("service"), cmdb))

    return ValidationResult(
        artifact_id=artifact_id,
        group_id=group_id,
        passed=len(failures) == 0,
        failures=failures,
        validated_at=now,
    )


def _check_properties_mismatch(
    meta_block: dict,
    owner_block: dict,
    properties: dict[str, str],
) -> list[ValidationFailure]:
    """Return failures for each body/properties field that disagrees."""
    if not properties:
        return []

    checks = [
        ("owner.service", owner_block.get("service")),
        ("domain", meta_block.get("domain")),
        ("dataClassification", meta_block.get("dataClassification")),
        ("lifecycle", meta_block.get("lifecycle")),
    ]
    out: list[ValidationFailure] = []
    for key, body_val in checks:
        prop_val = properties.get(key)
        if prop_val is not None and body_val is not None and prop_val != body_val:
            out.append(ValidationFailure(
                rule="properties_mismatch",
                message=(
                    f"Apicurio property '{key}' is '{prop_val}' but "
                    f"schema body metadata has '{body_val}'."
                ),
            ))
    return out


async def run_validation_scan(
    apicurio: ApicurioClient,
    cmdb: CmdbClient,
) -> list[ValidationResult]:
    """Fan out a full validation scan across all registered schemas.

    Issues 2N parallel requests (meta + content per artifact), then validates
    each one. Failures on individual artifacts are logged and skipped rather
    than aborting the scan.
    """
    try:
        data = await apicurio.search_artifacts(limit=500)
    except ApicurioError:
        logger.error("validation_scan.search_failed")
        return []

    artifacts = data.get("artifacts", [])
    if not artifacts:
        return []

    groups_ids = [(a.get("groupId") or "default", a["id"]) for a in artifacts]

    meta_results, content_results = await asyncio.gather(
        asyncio.gather(
            *[apicurio.get_artifact_metadata(g, aid) for g, aid in groups_ids],
            return_exceptions=True,
        ),
        asyncio.gather(
            *[apicurio.get_artifact_content(g, aid) for g, aid in groups_ids],
            return_exceptions=True,
        ),
    )

    results: list[ValidationResult] = []
    # strict=True: groups_ids is a comprehension over artifacts, and both
    # result lists are asyncio.gather over groups_ids — all four are
    # len(artifacts) by construction, so a mismatch is a real bug.
    for artifact, (group_id, artifact_id), meta, content in zip(
        artifacts, groups_ids, meta_results, content_results, strict=True
    ):
        if isinstance(meta, Exception):
            logger.warning(
                "validation_scan.meta_failed",
                artifact_id=artifact_id,
                error=str(meta),
            )
            continue
        if isinstance(content, Exception):
            logger.warning(
                "validation_scan.content_failed",
                artifact_id=artifact_id,
                error=str(content),
            )
            continue

        result = await validate(
            content=content,
            properties=meta.get("properties") or {},
            cmdb=cmdb,
            artifact_id=artifact_id,
            group_id=group_id,
            schema_type=artifact.get("type", "AVRO"),
        )
        results.append(result)

    logger.info(
        "validation_scan.complete",
        total=len(results),
        failed=sum(1 for r in results if not r.passed),
    )
    return results


async def populate_validation_cache(
    apicurio: ApicurioClient,
    cmdb: CmdbClient,
    cache: ValidationCache,
) -> None:
    """Run a full scan and populate the cache. Clears any prior results first."""
    cache.clear()
    results = await run_validation_scan(apicurio, cmdb)
    now = datetime.now(tz=UTC)
    for result in results:
        cache.set(result.group_id, result.artifact_id, result)
    cache.mark_populated(now)
