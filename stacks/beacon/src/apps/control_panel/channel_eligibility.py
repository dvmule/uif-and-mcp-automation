# channel_eligibility.py
# Beacon is the MESSAGE channel. A gold attribute whose master declares
# `x-channels: []` belongs to NO channel at all — those are rule N7's
# source-system surrogates, which must never leave the platform — so beacon
# must not offer one in the column picker or accept one in a config.
#
# The gold master is the single source for this (owner rule, 2026-09-23: any
# Event / API / beacon schema derives from its domain master only). Each
# domain's deploy already publishes its masters, so beacon reads them rather
# than restating the answer:
#
#   s3://uif-echo-{env}-gold-artifacts/schemas/gold/{entity}/v1/master.json
#
# PHASE 1 (UIFPE-540). Only the EXPLICITLY excluded set is enforced here,
# because that rule is unambiguous: both channel generators drop an explicit
# empty list from every tier. The full message tier is NOT derived yet — a
# property carrying no `x-channels` key at all is treated differently by the
# two generators (all channels vs api-only), so beacon would have to guess.
# UIFPE-523 settles that and emits per-domain projections; phase 2 then reads
# the message projection instead of re-deriving anything.
#
# KNOWN GAP, deferred to phase 2: a surrogate nested inside a channel-eligible
# container still ships. `contributors.contributorId` is `x-channels: []` while
# `contributors` is `["event","message","api"]`, and the physical column is
# ARRAY<STRUCT<contributorId, ...>> which json/xml serialize whole. Excluding
# the parent would be wrong — it is eligible — so the fix is to project the
# struct at render time, which belongs with the tier derivation.

from __future__ import annotations

import json
import os

#: Set by Terraform, like BEACON_GOLD_DATABASES.
BUCKET_ENV = "BEACON_GOLD_ARTIFACTS_BUCKET"

#: Three outcomes, because two of them must not be confused.
#:   OK         — the master was read; `excluded` is the answer.
#:   DISABLED   — nothing to check against: no bucket configured, or the
#:                source is not a gold table. The guard is simply not in play,
#:                so callers change no behaviour. This is what every
#:                environment sees until Terraform sets the variable, and it
#:                is why the submit gate cannot fail closed on it — doing so
#:                would refuse every config the moment this shipped.
#:   UNREADABLE — a bucket IS configured but the entity's master could not be
#:                read. That is a broken deployment, not an absent feature,
#:                and the submit gate fails closed on it.
OK = "ok"
DISABLED = "disabled"
UNREADABLE = "unreadable"

_cache: dict[tuple[str, str], frozenset[str]] = {}
#: UIFPE-562: the raw master per (bucket, entity), so the exclusion set and
#: the attribute descriptions cost one S3 read between them.
_masters: dict[tuple[str, str], dict] = {}


def _canonical(name: str) -> str:
    """A comparison form shared by a master's logical name and the catalogue's
    physical one: `inventoryItemId` and `inventory_item_id` both reduce to
    `inventoryitemid`.

    This is deliberately NOT a logical->physical renderer. It cannot produce a
    physical name, only compare two, so it cannot drift away from the three
    renderers that do generate one (gold_cdc.physical_name, mapping.physical,
    event_fabric_processor._physical) — beacon never needs to generate.
    """
    return name.replace("_", "").lower()


def _excluded_logical(master: dict) -> set[str]:
    """Top-level logical names the master marks `x-channels: []`.

    Top level only, on purpose: those are the columns the picker offers and a
    config selects. Nested exclusions are the deferred gap in the header.
    """
    return {name for name, prop in (master.get("properties") or {}).items()
            if isinstance(prop, dict) and prop.get("x-channels") == []}


def _load_master(bucket: str, entity: str) -> dict:
    from control_panel.registries import _aws

    _, s3 = _aws()
    key = f"schemas/gold/{entity}/v1/master.json"
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def _master(bucket: str, entity: str) -> dict:
    cached = _masters.get((bucket, entity))
    if cached is None:
        cached = _load_master(bucket, entity)
        _masters[(bucket, entity)] = cached
    return cached


def column_descriptions(entity: str, catalogued: list[str]) -> dict[str, str]:
    """UIFPE-562: {catalogued column: the master's `description`} for the
    columns the master documents — the wizard shows it beside the attribute.

    Nothing is authored here: the text is the master's, verbatim, matched to
    the catalogue's snake_case spelling through _canonical. Empty when no
    bucket is configured, the source is not a gold table, the master cannot
    be read (the eligibility path already reports that) or a property simply
    carries no description — absent means unknown, like `types`.
    """
    bucket = os.environ.get(BUCKET_ENV, "").strip()
    if not bucket or not entity:
        return {}
    try:
        props = _master(bucket, entity).get("properties") or {}
    except Exception:
        return {}
    by_canonical = {
        _canonical(name): prop["description"].strip()
        for name, prop in props.items()
        if isinstance(prop, dict) and isinstance(prop.get("description"), str)
        and prop["description"].strip()
    }
    return {c: by_canonical[_canonical(c)] for c in catalogued
            if _canonical(c) in by_canonical}


def excluded_columns(entity: str, catalogued: list[str]) -> tuple[frozenset[str], str]:
    """The catalogued column names this entity's master permits on no channel,
    with the status that says how much to trust the answer.

    Names come back as the CATALOGUE spells them, so callers filter their own
    lists directly. Read the status before the set: an empty set means
    "nothing excluded" under OK, and "we do not know" under the other two.
    """
    bucket = os.environ.get(BUCKET_ENV, "").strip()
    if not bucket or not entity:
        return frozenset(), DISABLED

    cached = _cache.get((bucket, entity))
    if cached is None:
        try:
            logical = _excluded_logical(_master(bucket, entity))
        except Exception:
            return frozenset(), UNREADABLE
        cached = frozenset(_canonical(n) for n in logical)
        _cache[(bucket, entity)] = cached

    return frozenset(c for c in catalogued if _canonical(c) in cached), OK


def entity_for_path(path: str | None) -> str:
    """The gold entity a source path names — `glue_catalog.<db>.<table>` has
    the entity as its table. Non-gold sources (csv, demo) yield "", which
    makes excluded_columns return None: there is no master to check against.
    """
    if not path or not path.startswith("glue_catalog."):
        return ""
    parts = path.split(".")
    return parts[2] if len(parts) >= 3 else ""


def reset_cache() -> None:
    """Tests only — the masters change per deploy, not per request."""
    _cache.clear()
    _masters.clear()
