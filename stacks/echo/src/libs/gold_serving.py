"""Gold -> DynamoDB serving copy: the entity-agnostic half (UIFPE-518).

Gold is Iceberg on S3 with no point lookup, so a synchronous GET needs a
serving copy. This module derives everything that copy needs from the pipeline
config and the master schema, so a per-entity materializer is an entry point
rather than a fork: no hand-written SQL, no hand-written item shape.

What it derives, and from where:

    key columns          target.primary_key_cols
    attributes           materialized_properties (the transform maintains it)
    gold db / table      target.database / target.table
    physical columns     gold_cdc.physical_name (N8)
    the ETag             _channel_hashes[channel] -- ALREADY computed by gold

That last one is the reason this is short. gold_cdc emits
`sha2(to_json(named_struct(<the channel's properties>)), 256)` with
`ignoreNullFields: false`, so `_channel_hashes[channel]` is a content hash over
exactly what the API returns, nulls included, produced by the same expression
that drives change detection. The ETag and the change log therefore cannot
disagree, and a CLEARED value moves the hash. Nothing here recomputes it.

Deliberately NOT carried over from the marketing-copy materializer:

  * compression and a size ceiling. Those exist because marketing copy is a
    ~350 KB HTML blob under the claim-check pattern. An entity of scalars needs
    neither, and making every entity carry one entity's problem is how a shared
    module becomes worse than a copy. `compress_prop` opts in.
  * omitting null attributes. That one is a BUG to avoid repeating: it makes an
    absent attribute and a cleared value indistinguishable, so a consumer that
    cached a value keeps it forever, and an API whose contract marks a field
    required cannot honour it. This module writes an explicit NULL (N12).
"""
from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass, field
from pathlib import Path

# physical_name is the ONE renderer for N8 (camelCase -> snake_case), and it
# lives with the transform. Importing it rather than restating the regex keeps
# a single definition — a second copy is how the physical rendering drifts.
# Path set explicitly, the same way gold_config.py reaches src/libs: a Lambda
# or Glue zip is flat, so the plain `import` has to work there too.
_PIPELINE = Path(__file__).resolve().parents[1] / "apps" / "pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))
from gold_cdc import physical_name  # noqa: E402

#: Framework columns every gold table carries.
HASH_COL = "_channel_hashes"
UPDATED_COL = "_gold_updated_at"

#: ONE serving table holds every entity, so the key carries the entity.
#: Adding an entity is then config the running Lambda reads, with no
#: infrastructure change at all (owner direction 2026-09-24). pk takes the
#: entity and the principal identifier, sk the rest of the grain - so a
#: contract operation over a partial key ("every organization for this
#: product") is a Query and not a Scan.
PK, SK = "pk", "sk"
#: Partition key of the serving table's secondary index. Written ONLY by an
#: entity whose business key differs from its merge key, which keeps the index
#: sparse — see ServingSpec.indexed.
EK = "ek"

#: The index's sort key. A GSI drops any item missing its sort
#: attribute, so every indexed item carries this one.
EKS = "eks"

#: An entity whose grain IS its principal identifier has no sub-grain, and an
#: empty string is not a legal key value. Gaining a second key property later
#: is a re-grain, which already means a rebuild here.
SK_NONE = "-"
#: DynamoDB's hard per-item ceiling. An item over it is refused rather than
#: written, because the write fails and takes the sync leg with it.
MAX_ITEM_BYTES = 400 * 1024


def resolve_env(value: str, env: str) -> str:
    """`uif_echo_{env}_gold_stock` -> `uif_echo_dev_gold_stock`."""
    return value.replace("{env}", env)


@dataclass(frozen=True)
class ServingSpec:
    """Everything the engine needs about one entity, all of it derived."""

    entity: str
    gold_db: str
    gold_table: str
    key_props: tuple[str, ...]      # logical, partition first then sort
    props: tuple[str, ...]          # logical, every materialized attribute
    channel: str = "api"
    compress_prop: str | None = None
    #: Properties the master declares as a date or an instant. They MUST be
    #: cast to VARCHAR in the payload query: UNLOAD to JSON cannot serialize
    #: Athena's timestamp type and fails the whole query with
    #: "NOT_SUPPORTED: Unsupported Hive type: timestamp(6) with time zone".
    #: The cast also gives the API an ISO-8601 string, which is what N11 wants.
    timestamp_props: frozenset[str] = field(default_factory=frozenset)
    #: Whether this entity is served at all. One table and one Lambda cover
    #: every entity, so opting in is config the running Lambda reads and
    #: costs no infrastructure change.
    enabled: bool = False
    #: The properties the master puts on THIS channel. The serving copy stores
    #: every materialized property, which for product includes three Oracle
    #: surrogates marked `x-channels: []` — so a retrieval surface must project
    #: to this set or it leaks them (Schema Standards N7) and breaks its own
    #: `additionalProperties: false`. Derived, never listed by hand.
    channel_props: tuple[str, ...] = ()
    #: The key a READ surface addresses a row by, when it is not the merge key.
    #: Program is the case: its gold table merges on the Oracle pair
    #: (inventoryItemId, organizationId) because only the driving Silver table
    #: carries SEGMENT1, so no other source can express programId and CDC needs
    #: the pair. The business key is programId alone. Empty means the two are
    #: the same, which is every other entity.
    entity_key_props: tuple[str, ...] = ()
    #: {property: {member: DynamoDB kind}} for every property the master
    #: declares as an object or an array of objects. Empty for a flat entity
    #: like stock, which is why nothing here was exercised until price.
    #:
    #: These properties are handled differently at BOTH ends: the payload
    #: query builds their JSON itself rather than letting Athena serialise a
    #: struct, and the item builder types each member from the master instead
    #: of guessing from the value. Their members are also channel-filtered
    #: here — the top-level filter cannot reach inside a struct, so without
    #: this an excluded surrogate would be stored and returned.
    nested_props: dict = field(default_factory=dict)
    #: Of `nested_props`, the ones the master declares as a single OBJECT
    #: rather than an array. Built without TRANSFORM and stored as an M rather
    #: than an L — product's fulfillmentOrgDetails is the first of these, and
    #: it also carries an array two levels down.
    nested_object_props: frozenset = field(default_factory=frozenset)

    @property
    def entity_keys(self) -> tuple[str, ...]:
        """The key a read surface addresses a row by. One place decides the
        fallback, so nothing else has to write `or key_props`."""
        return self.entity_key_props or self.key_props

    @property
    def indexed(self) -> bool:
        """Whether this entity needs the serving table's secondary index.

        Only an entity whose business key differs from its merge key does. The
        index is therefore SPARSE by construction: every other entity writes
        no index attribute and never appears in it.
        """
        return bool(self.entity_key_props) and \
            tuple(self.entity_key_props) != tuple(self.key_props)

    @property
    def changes_table(self) -> str:
        return f"{self.gold_table}_changes"

    @property
    def gold_ref(self) -> str:
        return f"{self.gold_db}.{self.gold_table}"

    @property
    def changes_ref(self) -> str:
        return f"{self.gold_db}.{self.changes_table}"

    @classmethod
    def from_config(cls, cfg: dict, env: str, master: dict | None = None,
                    channel: str = "api") -> ServingSpec:
        """`master` supplies the declared types. Without it no property is cast,
        which is safe only for an entity of strings — pass it."""
        target = cfg.get("target") or {}
        serving = cfg.get("serving") or {}
        # From CONFIG, not a parameter. It was a parameter until UIFPE-767 and
        # nothing in production ever passed one, so the compression the write
        # path already implemented was unreachable: declared, tested, dead.
        compress_prop = serving.get("compress_prop") or None
        keys = tuple(target.get("primary_key_cols") or ())
        entity_keys = tuple(target.get("entity_key_cols") or ())
        props = tuple(cfg.get("materialized_properties") or ())

        # An entity key the serving copy cannot read is the one failure worth
        # refusing outright: the index attribute would silently never be
        # written, every business-key lookup would 404, and the items would
        # look perfectly fine.
        missing = [k for k in entity_keys if k not in props]
        if missing:
            raise ValueError(
                f"{cfg.get('gold_id')}: target.entity_key_cols names "
                f"{missing}, which is not in materialized_properties — the "
                "serving copy cannot key on a property it does not store")

        # Two is the ceiling the read surface can address: /{entity}/{key} or
        # /{entity}/{key}/{sub}/{subkey}. A third would be indexed and unread.
        if len(entity_keys) > 2:
            raise ValueError(
                f"{cfg.get('gold_id')}: target.entity_key_cols has "
                f"{len(entity_keys)} columns — the read surface addresses at "
                "most two")

        # Each refusal names the field to fix. A serving copy built from a
        # half-filled config is worse than one that never starts: it looks
        # populated and is missing attributes nobody notices until a consumer
        # asks for them.
        if not keys:
            raise ValueError(
                f"{cfg.get('gold_id')}: target.primary_key_cols is empty — "
                "the serving table's key is derived from it, so there is "
                "nothing to build")
        if not props:
            raise ValueError(
                f"{cfg.get('gold_id')}: materialized_properties is empty — "
                "the transform fills it on each run, so an empty list means "
                "the entity has never completed a run; refusing rather than "
                "materializing zero attributes")
        missing = [k for k in keys if k not in props]
        if missing:
            raise ValueError(
                f"{cfg.get('gold_id')}: key column(s) {missing} are not in "
                "materialized_properties, so the serving item would have no "
                "key — the channel projection must carry the key it is "
                "addressed by")
        if compress_prop and compress_prop not in props:
            raise ValueError(
                f"{cfg.get('gold_id')}: compress_prop '{compress_prop}' is "
                "not a materialized property")

        mprops = ((master or {}).get("properties") or {})
        stamps = frozenset(
            p for p in props
            if isinstance(mprops.get(p), dict)
            and mprops[p].get("format") in ("date-time", "date"))

        return cls(
            entity=str(cfg.get("gold_id")),
            gold_db=resolve_env(str(target.get("database") or ""), env),
            gold_table=str(target.get("table") or ""),
            key_props=keys,
            entity_key_props=entity_keys,
            props=props,
            channel=channel,
            compress_prop=compress_prop,
            timestamp_props=stamps,
            enabled=bool(serving.get("enabled")),
            # An ABSENT x-channels means no channel: a property opts in to
            # being exposed, never out.
            channel_props=tuple(
                p for p in props
                if channel in ((mprops.get(p) or {}).get("x-channels") or [])),
            # A property is nested exactly when the master DESCRIBES members
            # for it on this channel. There is no separate "is it an object or
            # an array" test: `nested_members` returns {} for a scalar, for an
            # array whose items carry no properties, and for an object with
            # none — so the one filter below is the whole rule. A control round
            # proved a type test redundant here, and a redundant guard is
            # mutation surface rather than safety.
            nested_props={
                p: members for p in props
                for members in [nested_members(master or {}, p, channel)]
                if members},
            # Which of those are a single object. Read from the master's own
            # type rather than inferred from the value, because an object and
            # a one-element array look identical once serialised.
            nested_object_props=frozenset(
                p for p in props
                if "object" in str(((master or {}).get("properties") or {})
                                   .get(p, {}).get("type"))
                and "array" not in str(((master or {}).get("properties") or {})
                                       .get(p, {}).get("type"))),
        )


def item_key(spec: ServingSpec, value_of) -> dict | None:
    """The shared table's key for one row, or None when a key part is absent.

    `value_of` takes a logical property name and returns its value, so this
    serves an UNLOAD row, a query result and a built item alike.
    """
    parts = []
    for k in spec.key_props:
        v = value_of(k)
        if v is None or v == "":
            return None
        parts.append(str(v))
    sub_grain = "#".join(parts[1:]) if len(parts) > 1 else SK_NONE
    return {PK: {"S": f"{spec.entity}#{parts[0]}"}, SK: {"S": sub_grain}}


def index_value(entity: str, key1) -> str:
    """The index's PARTITION value: the entity and the business key's first
    column.

    Only the first, so that one partition holds every sub-grain of one
    business key and a collection read is a single Query. Folding the second
    column in here would make the partition unique per row, and "every
    organization this product is in" unanswerable.
    """
    return f"{entity}#{key1}"


def index_sort(key2=None) -> str:
    """The index's SORT value, or the sentinel when the key is one column.

    One function each for the two halves, so the write path and the read path
    cannot disagree — a mismatch misses every row while looking correct.
    """
    return SK_NONE if key2 in (None, "") else str(key2)


def index_key(spec: ServingSpec, value_of) -> tuple[str, str] | None:
    """The secondary index's (partition, sort) for one row, or None.

    None means "this entity is not indexed", which is the normal case. The
    item is still written — it simply carries no index attribute and so never
    appears in the index.

    An indexed entity with an ABSENT key part also returns None rather than
    writing a partial key. That row stays reachable by its merge key; only the
    business-key lookup misses it, and a null business key is a data defect the
    DQ check reports rather than something to paper over here.
    """
    if not spec.indexed:
        return None
    parts = []
    for k in spec.entity_keys:
        v = value_of(k)
        if v is None or v == "":
            return None
        parts.append(str(v))
    return (index_value(spec.entity, parts[0]),
            index_sort(parts[1] if len(parts) > 1 else None))


def nested_select(spec: ServingSpec, prop: str, alias: str = "g") -> str:
    """One nested property as a varchar holding JSON.

    THE JSON IS BUILT IN THE QUERY, and every part of that is load-bearing:

      * Athena's UNLOAD lowercases every name it serialises, INCLUDING a ROW
        type's field names — measured, not assumed. Names inside a string
        VALUE are untouched, so building the JSON here is the only way
        camelCase reaches the item builder. `priceListName`, not
        `pricelistname`, which N7 and N8 forbid on an API surface.
      * The field names in the ROW type are QUOTED. Trino folds an unquoted
        identifier to lower case before the JSON is ever built, so the quotes
        are what preserve the case rather than decoration.
      * A timestamp is `to_iso8601`-ed. Casting a struct that still holds a
        `timestamp with time zone` to JSON fails the whole query with
        "NOT_SUPPORTED: Unsupported Hive type: timestamp(6) with time zone" —
        which is exactly how price's first baseline died.
      * Everything else is cast to VARCHAR, and that is a correctness choice
        rather than convenience. DynamoDB carries `N` as a string, so a value
        that stays textual end to end is decimal-exact; a JSON number would
        become a Python float on the way and round decimal(38,10). The item
        builder puts the type back from the master.
      * Only the channel's members are selected, so a member the master
        excludes — `priceListId` with `x-channels: []` — is never stored and
        so cannot be returned.
    """
    members = spec.nested_props[prop]
    ref = f'{alias}."{physical_name(prop)}"'
    if prop in spec.nested_object_props:
        # A single object, so there is nothing to TRANSFORM over — applying it
        # to a ROW is what fails with "Unexpected parameters (row(...),
        # <function>) for function transform", which is how product's
        # fulfillmentOrgDetails surfaced this gap.
        return f"json_format(CAST({_row_sql(members, ref)} AS JSON))"
    return (f"json_format(CAST(TRANSFORM({ref}, x -> "
            f"{_row_sql(members, 'x')}) AS JSON))")


def _row_sql(members: dict, var: str, depth: int = 0) -> str:
    """A CAST(ROW(...) AS ROW(...)) for one level, recursing into structures.

    Both halves are built together on purpose: a value expression and its ROW
    field type have to agree, and generating them in separate passes is how
    they drift. The field names stay QUOTED at every depth for the same reason
    as the top level — Trino folds an unquoted identifier to lower case before
    the JSON is built.
    """
    src, typ = [], []
    for name, kind in members.items():
        phys = f'{var}."{physical_name(name)}"'
        if isinstance(kind, tuple):
            shape, sub = kind
            if shape == "M":
                src.append(_row_sql(sub, phys, depth + 1))
                typ.append(f'"{name}" {_row_type(sub)}')
            elif sub is None:
                # An array of scalars: nothing to name, so varchar elements.
                src.append(f"TRANSFORM({phys}, e -> CAST(e AS varchar))")
                typ.append(f'"{name}" array(varchar)')
            else:
                v = f"y{depth}"
                src.append(f"TRANSFORM({phys}, {v} -> "
                           f"{_row_sql(sub, v, depth + 1)})")
                typ.append(f'"{name}" array({_row_type(sub)})')
        else:
            src.append(f"to_iso8601({phys})" if kind == "TS"
                       else f"CAST({phys} AS varchar)")
            typ.append(f'"{name}" varchar')
    return f"CAST(ROW({', '.join(src)}) AS ROW({', '.join(typ)}))"


def _row_type(members: dict) -> str:
    """The ROW type for one level, matching what _row_sql builds."""
    out = []
    for name, kind in members.items():
        if isinstance(kind, tuple):
            shape, sub = kind
            if shape == "M":
                out.append(f'"{name}" {_row_type(sub)}')
            elif sub is None:
                out.append(f'"{name}" array(varchar)')
            else:
                out.append(f'"{name}" array({_row_type(sub)})')
        else:
            out.append(f'"{name}" varchar')
    return f"row({', '.join(out)})"


def payload_select(spec: ServingSpec, alias: str = "g") -> str:
    """The SELECT every payload query shares: the channel's attributes under
    their LOGICAL names, plus the channel hash as `etag` and the row's update
    timestamp. Logical aliases mean the item builder never re-derives names."""
    cols = []
    for p in spec.props:
        ref = f'{alias}."{physical_name(p)}"'
        if p in spec.nested_props:
            cols.append(f'{nested_select(spec, p, alias)} AS "{p}"')
            continue
        # to_iso8601, not CAST AS VARCHAR. Both avoid the UNLOAD failure (see
        # ServingSpec.timestamp_props), but CAST yields Athena's display form
        # "2026-09-12 19:03:11.000000 UTC", which is not ISO-8601 and would
        # break a contract declaring `format: date-time` (N11). to_iso8601
        # yields "2026-09-12T19:03:11.000000Z".
        cols.append(f'to_iso8601({ref}) AS "{p}"'
                    if p in spec.timestamp_props else f'{ref} AS "{p}"')
    cols.append(f'{alias}."{HASH_COL}"[\'{spec.channel}\'] AS "etag"')
    cols.append(f'to_iso8601({alias}."{UPDATED_COL}") AS "updated"')
    return f"SELECT {', '.join(cols)} FROM {spec.gold_ref} {alias}"


def unload_sql(spec: ServingSpec, run_id: str | None, dest: str) -> str:
    """The one payload query: delta when a run_id is given, baseline when not.

    The delta restricts to keys the run touched AND to rows whose own channel
    actually changed — `changed_channels` already records that, so a change
    confined to another tier costs no serving write.
    """
    body = payload_select(spec)
    if run_id is not None:
        keys = ", ".join(f'"{physical_name(k)}"' for k in spec.key_props)
        on = " AND ".join(
            f'g."{physical_name(k)}" = c."{physical_name(k)}"'
            for k in spec.key_props)
        body += (
            f" JOIN (SELECT DISTINCT {keys} FROM {spec.changes_ref} "
            f"WHERE run_id = '{run_id}' "
            f"AND contains(changed_channels, '{spec.channel}')) c ON {on}"
        )
    return (f"UNLOAD ({body}) TO '{dest}' "
            "WITH (format = 'JSON', compression = 'GZIP')")


def deletes_sql(spec: ServingSpec, run_id: str) -> str:
    """Keys the run touched that no longer exist in gold.

    Deletion is detected by ABSENCE, not by an op vocabulary: reconcile is the
    one producer of gold deletions and writes its change rows under its own run
    id, so its run lands here like any other. Anti-joining is also robust to an
    op value nobody has seen yet.
    """
    keys = ", ".join(f'"{physical_name(k)}"' for k in spec.key_props)
    sel = ", ".join(f'c."{physical_name(k)}" AS "{k}"' for k in spec.key_props)
    on = " AND ".join(
        f'g."{physical_name(k)}" = c."{physical_name(k)}"'
        for k in spec.key_props)
    first = physical_name(spec.key_props[0])
    return (
        f"SELECT {sel} FROM "
        f"(SELECT DISTINCT {keys} FROM {spec.changes_ref} "
        f"WHERE run_id = '{run_id}') c "
        f"LEFT JOIN {spec.gold_ref} g ON {on} "
        f'WHERE g."{first}" IS NULL'
    )


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")


def _attr(value) -> dict:
    """A DynamoDB attribute value, with NULL written EXPLICITLY.

    Omitting a null attribute is the mistake this avoids: an absent attribute
    and a cleared value become indistinguishable, so a consumer that cached the
    old value keeps it, and an API whose contract marks the field required
    cannot honour its own contract. N12 — `required` constrains the key, the
    null goes in the value.

    Lists and maps recurse. Before that they fell through to the final line and
    became `{"S": "[{'a': 1}]"}` — a stringified Python REPR, which is not even
    JSON — so an entity with a nested array could not be served at all. Typed
    nested members go through `_nested_attr` instead; this branch is the safety
    net, so an unexpected structure is stored as a structure rather than as a
    repr.
    """
    if value is None or value == "":
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float)):
        return {"N": str(value)}
    if isinstance(value, list):
        return {"L": [_attr(v) for v in value]}
    if isinstance(value, dict):
        return {"M": {str(k): _attr(v) for k, v in value.items()}}
    return {"S": str(value)}


#: How a nested member's declared JSON Schema type maps onto a DynamoDB
#: attribute. Numbers stay DECIMAL-EXACT because DynamoDB transports `N` as a
#: string: the payload query casts every nested field to varchar, so the value
#: never becomes a Python float. Going through a JSON number instead would
#: round decimal(38,10) on the way past.
_JSON_TO_DDB = {"number": "N", "integer": "N", "boolean": "BOOL"}


def _member_kind(schema: dict, parent_channels=None,
                 channel=None) -> str | tuple:
    """A scalar kind, or a TUPLE when the member is itself a structure.

    Scalars: "TS" | "N" | "BOOL" | "S". TS is not a DynamoDB type — it is
    stored as S — but it has to be distinguishable here, because the payload
    query must `to_iso8601` a temporal member and cast every other one to
    varchar.

    Structures: ("L", members) for an array of objects, ("M", members) for a
    single object, ("L", None) for an array of scalars. Product is the first
    entity with either of the last two shapes, and the tuple is what lets the
    query builder and the item builder recurse in step.
    """
    types = schema.get("type")
    types = types if isinstance(types, list) else [types]
    own = schema.get("x-channels", parent_channels)
    if "array" in types:
        sub = ((schema.get("items") or {}).get("properties")) or None
        return ("L", _kinds(sub, own, channel) if sub else None)
    if "object" in types:
        return ("M", _kinds(schema.get("properties") or {}, own, channel))
    if schema.get("format") in ("date-time", "date"):
        return "TS"
    for t in types:
        if t in _JSON_TO_DDB:
            return _JSON_TO_DDB[t]
    return "S"


def _kinds(props: dict, parent_channels=None, channel=None) -> dict:
    """{member: kind} for one level, recursing through structures.

    The inherit rule is the same at EVERY depth, which is why the effective
    channels travel down rather than being applied once at the top: absent
    means inherit the parent's, an explicit list decides, and `x-channels: []`
    excludes. Applying it only to the first level would leak a surrogate
    declared two levels in — the exact thing price's priceListId taught.
    """
    out = {}
    for name, fd in (props or {}).items():
        fd = fd or {}
        if channel is not None:
            chans = fd.get("x-channels", parent_channels)
            if channel not in (chans or []):
                continue
        out[name] = _member_kind(fd, parent_channels=parent_channels,
                                 channel=channel)
    return out


def nested_members(master: dict, prop: str, channel: str) -> dict:
    """{logical member: DynamoDB kind} for one nested property, in the master's
    own order, filtered to `channel`.

    THE CHANNEL RULE HERE IS NOT THE TOP-LEVEL ONE. A top-level property opts
    IN — absent `x-channels` means no channel. A struct MEMBER inherits its
    parent's channels (ADR-0003), so absent means inherit, and only an explicit
    list excludes. `x-channels: []` is how price keeps `priceListId` — an
    Oracle surrogate — off every channel while its 14 siblings inherit all
    three. Reading it the top-level way would leak that surrogate; reading it
    as "absent means inherit" without honouring an explicit `[]` would too.
    """
    d = (master.get("properties") or {}).get(prop) or {}
    parent = d.get("x-channels") or []
    fields = ((d.get("items") or d).get("properties") or {})
    # `_kinds` applies the inherit rule and recurses; a member that is itself
    # a structure comes back as a ("L"|"M", members) tuple.
    return _kinds(fields, parent, channel)


def _nested_attr(raw, members: dict) -> dict:
    """A nested property's UNLOAD value -> a DynamoDB L of Ms, or a single M.

    The payload query hands this a varchar holding JSON, because Athena's
    UNLOAD lowercases every column name it serialises — including a ROW type's
    field names — while names INSIDE a string value are untouched. Building
    the JSON in the query is therefore the only way camelCase survives to here.

    Every declared member is written, nulls included (N12), and members the
    channel excludes never appear because the query did not select them.
    """
    if raw is None or raw == "":
        return {"NULL": True}
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(parsed, list):
        return {"L": [_nested_attr_one(e, members) for e in parsed]}
    return _nested_attr_one(parsed, members)


def _nested_attr_one(element, members: dict) -> dict:
    el = element if isinstance(element, dict) else {}
    out = {}
    for name, kind in members.items():
        v = el.get(name)
        if v is None or v == "":
            out[name] = {"NULL": True}
        elif isinstance(kind, tuple):
            # A member that is itself a structure. The query built real JSON
            # for it, so recurse in step with _row_sql rather than stringify:
            # `str(v)` here would store a Python repr, which is the bug price's
            # arrays had before UIFPE-520.
            shape, sub = kind
            if shape == "M":
                out[name] = _nested_attr_one(v, sub)
            elif sub is None:
                out[name] = {"L": [_attr(e) for e in (v or [])]}
            else:
                out[name] = {"L": [_nested_attr_one(e, sub)
                                   for e in (v or [])]}
        elif kind == "BOOL":
            out[name] = {"BOOL": str(v).strip().lower() in ("true", "1")}
        else:
            # TS is carried as S: the query already produced ISO-8601, and
            # DynamoDB has no date type.
            out[name] = {"S" if kind == "TS" else kind: str(v)}
    return {"M": out}


def build_item(spec: ServingSpec, row: dict, sync_tag: str,
               compress=None, max_bytes: int | None = None) -> dict | None:
    """One UNLOAD JSON line -> a DynamoDB item, or None when it must be refused.

    `etag` is the channel hash straight from the row. syncTag/syncedAt drive the
    post-baseline prune. Every materialized property is written, nulls included.
    """
    # Athena's UNLOAD-to-JSON LOWERCASES every column name, so the camelCase
    # aliases in the payload query do not survive into the file: a row arrives
    # as {"availabletopromisetimestamp": ...}. Looking up the logical name
    # directly finds nothing, every key reads as missing, and every row is
    # skipped — silently, because "no key" is a warn-and-continue. Normalising
    # once here keeps the rest of the module in logical names and works for an
    # aliased UNLOAD, an unaliased one, and a plain query result alike.
    flat = {str(k).lower(): v for k, v in row.items()}

    def get(prop: str):
        return flat.get(prop.lower())

    key = item_key(spec, get)
    if key is None:
        print(f"[WARN] {spec.entity}: row with an absent key part skipped")
        return None

    item: dict = dict(key)
    item["syncTag"] = {"S": sync_tag}
    item["syncedAt"] = {"S": _now_iso()}

    # Every property is written, the key ones included: pk and sk are storage,
    # and the contract still returns productId and organizationCode.
    for p in spec.props:
        if p == spec.compress_prop:
            continue
        if p in spec.nested_props:
            # The value is a varchar holding JSON that the payload query built
            # itself, with camelCase members intact. Typed from the master
            # rather than guessed from the value, because every member arrived
            # as a string to keep decimals exact.
            item[p] = _nested_attr(get(p), spec.nested_props[p])
        else:
            item[p] = _attr(get(p))

    ek = index_key(spec, get)
    if ek is not None:
        item[EK], item[EKS] = {"S": ek[0]}, {"S": ek[1]}

    etag = get("etag")
    if etag:
        item["etag"] = {"S": str(etag)}
    updated = get("updated")
    if updated:
        item["goldUpdatedAt"] = {"S": str(updated)}

    if spec.compress_prop is not None:
        if compress is None:
            # Without this the next line is None(...), whose traceback says
            # "'NoneType' object is not callable" and names neither the
            # entity, the property, nor what the caller failed to supply.
            raise ValueError(
                f"{spec.entity} declares serving.compress_prop "
                f"'{spec.compress_prop}' but no compress callable was passed "
                "to build_item — the caller must supply one (gzip.compress)")
        raw = get(spec.compress_prop)
        if raw is None:
            item[spec.compress_prop] = {"NULL": True}
        else:
            gz = compress(str(raw).encode("utf-8"))
            if max_bytes is not None and len(gz) > max_bytes:
                print(f"[ERROR] REFUSED {spec.entity} "
                      f"{ {k: v['S'] for k, v in key.items()} }: {len(gz)} "
                      f"compressed bytes exceeds the {max_bytes} ceiling — "
                      "NOT served; a truncated write would be silent "
                      "corruption")
                return None
            item[f"{spec.compress_prop}Gz"] = {"B": gz}

    # TOTAL size, not just the compressed property's. The check above has
    # always been inside the compress branch, so an entity without one had no
    # ceiling at all: the item was built and DynamoDB rejected the write with
    # a ValidationException, failing the leg. Product found this — 7 of its
    # 1,903,755 rows exceed the limit, all of them on standardComponents,
    # which is 98% of those items.
    size = item_bytes(item)
    if size > MAX_ITEM_BYTES:
        print(f"[ERROR] ITEM_TOO_LARGE {spec.entity} "
              f"{ {k: v['S'] for k, v in key.items()} }: {size} bytes exceeds "
              f"DynamoDB's {MAX_ITEM_BYTES} — NOT served. Refusing beats a "
              f"failed write that takes the whole leg down, and beats "
              f"truncating, which would be silent corruption.")
        return None

    return item


def item_bytes(item: dict) -> int:
    """DynamoDB's own item-size accounting, approximately.

    Not `len(json.dumps(item))`: that counts quotes, braces and the type tags
    (`{"S": ...}`), which DynamoDB does not, so it over-states by enough to
    refuse items that would have fitted. DynamoDB counts each attribute's
    NAME plus its VALUE in UTF-8 bytes, with a small constant per nested
    element — close enough that the margin is the safety, rather than the
    error being the safety.
    """
    def value(v) -> int:
        (kind, val), = v.items()
        if kind in ("S", "N"):
            return len(str(val).encode("utf-8"))
        if kind == "B":
            return len(val)
        if kind == "BOOL":
            return 1
        if kind == "NULL":
            return 1
        if kind == "L":
            return 3 + sum(1 + value(e) for e in val)
        if kind == "M":
            return 3 + sum(len(k.encode("utf-8")) + 1 + value(x)
                           for k, x in val.items())
        return 0

    return sum(len(k.encode("utf-8")) + value(v) for k, v in item.items())


# ─── The engine: Athena -> S3 -> DynamoDB, entity-agnostic ──────────────────
#
# Ported from the marketing-copy materializer, which proved this shape. Two
# things changed. Nothing is a module global any more, because a shared engine
# cannot bake in one entity's table name. And the key is composite rather than
# a single contentId, which the prune's paging cursor has to carry.
#
# AWS-free at import; every client is injectable.

import gzip
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor


@dataclass(frozen=True)
class ServingRuntime:
    """Per-deployment knobs. Separate from ServingSpec because the spec is
    derived from the entity while these come from the Lambda's environment."""

    table_name: str
    output_s3: str
    athena_workgroup: str = "primary"
    self_function_name: str = ""
    batch_lines: int = 500
    put_threads: int = 16
    #: Self-chain rather than risk an opaque Lambda timeout mid-file.
    self_chain_floor_ms: int = 60_000
    #: Only meaningful alongside spec.compress_prop.
    max_item_bytes: int | None = None


def clients(athena=None, glue=None, ddb=None, lam=None, s3=None,
            ddb_pool: int | None = None):
    """`ddb_pool` sizes the DynamoDB client's connection pool.

    botocore's default is 10 while put_batch runs `put_threads` puts at once,
    which is 16 by default. The six extra threads then churn: each one opens a
    fresh TLS connection, uses it, and has it discarded, and the log fills with
    "Connection pool is full, discarding connection" - which is what stock's
    first baseline did for all 140,257 rows. Pass `put_threads` so the pool
    matches the concurrency instead of fighting it.

    Only DynamoDB needs it. The S3 reads are sequential.
    """
    import boto3
    ddb_kw = {}
    if ddb_pool:
        from botocore.config import Config
        ddb_kw["config"] = Config(max_pool_connections=ddb_pool)
    return (athena or boto3.client("athena"),
            glue or boto3.client("glue"),
            ddb or boto3.client("dynamodb", **ddb_kw),
            lam or boto3.client("lambda"),
            s3 or boto3.client("s3"))


def await_query(athena, qid: str, remaining_ms) -> None:
    """Poll to success, bounded by the INVOCATION's real remaining time. A
    fixed cap larger than the budget would let one query eat the whole
    function and turn the failure into an opaque Lambda timeout."""
    while True:
        status = athena.get_query_execution(
            QueryExecutionId=qid)["QueryExecution"]["Status"]
        if status["State"] == "SUCCEEDED":
            return
        if status["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena {qid} {status['State']}: "
                               f"{status.get('StateChangeReason', '')}")
        if remaining_ms() < 90_000:
            athena.stop_query_execution(QueryExecutionId=qid)
            raise RuntimeError(
                f"Athena {qid} cancelled - invocation budget nearly spent")
        time.sleep(2)


def run_query(athena, rt: ServingRuntime, sql: str, remaining_ms) -> str:
    qid = athena.start_query_execution(
        QueryString=sql, WorkGroup=rt.athena_workgroup,
        ResultConfiguration={"OutputLocation": rt.output_s3},
    )["QueryExecutionId"]
    await_query(athena, qid, remaining_ms)
    return qid


def put_conditional(ddb, rt: ServingRuntime, item: dict) -> bool:
    """Upsert that can only move goldUpdatedAt FORWARD - equal is allowed, for
    idempotent replay and because a silent repair deliberately leaves the
    timestamp alone. A stale baseline row can therefore never overwrite a
    fresher delta write. False means suppressed as stale, not failed."""
    kwargs = {"TableName": rt.table_name, "Item": item}
    if "goldUpdatedAt" in item:
        kwargs["ConditionExpression"] = (
            "attribute_not_exists(goldUpdatedAt) OR goldUpdatedAt <= :u")
        kwargs["ExpressionAttributeValues"] = {":u": item["goldUpdatedAt"]}
    try:
        ddb.put_item(**kwargs)
        return True
    except Exception as e:                              # noqa: BLE001
        if "ConditionalCheckFailed" in type(e).__name__ + str(e):
            return False
        raise


def put_batch(ddb, rt: ServingRuntime, items: list) -> tuple:
    """Parallel conditional puts -> (written, suppressed_stale)."""
    if not items:
        return 0, 0
    with ThreadPoolExecutor(max_workers=rt.put_threads) as pool:
        ok = list(pool.map(lambda it: put_conditional(ddb, rt, it), items))
    return sum(ok), len(ok) - sum(ok)


def batch_delete(ddb, rt: ServingRuntime, keys: list) -> None:
    """`keys` are key dicts. Unprocessed items are retried, and anything still
    unprocessed raises rather than being dropped silently."""
    for i in range(0, len(keys), 25):
        chunk = [{"DeleteRequest": {"Key": k}} for k in keys[i:i + 25]]
        for attempt in range(6):
            resp = ddb.batch_write_item(RequestItems={rt.table_name: chunk})
            chunk = resp.get("UnprocessedItems", {}).get(rt.table_name, [])
            if not chunk:
                break
            time.sleep(0.2 * (attempt + 1))
        if chunk:
            raise RuntimeError(
                f"{len(chunk)} deletes still unprocessed after retries")


def split_s3(uri: str) -> tuple:
    bucket, key = uri.replace("s3://", "").split("/", 1)
    return bucket, key


def list_unload_files(s3, dest: str) -> list:
    bucket, prefix = split_s3(dest)
    keys, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        keys += [o["Key"] for o in resp.get("Contents", []) if o["Size"] > 0]
        token = resp.get("NextContinuationToken")
        if not token:
            return [f"s3://{bucket}/{k}" for k in sorted(keys)]


def process_files(s3, ddb, spec: ServingSpec, rt: ServingRuntime,
                  state: dict, remaining_ms, compress=None) -> dict:
    """Stream rows from UNLOAD files into conditional puts.

    `state` carries files / line_offset / counters and comes back updated; the
    caller self-chains with it when the budget runs low. The chain can break
    MID-FILE, because Athena decides UNLOAD file sizes and a single file can
    exceed one invocation. Files are deleted once fully processed, so a retry
    re-sees only unfinished work and the prefix never accumulates.
    """
    files = list(state.get("files") or [])
    offset = int(state.get("line_offset") or 0)
    tag = state["sync_tag"]

    def flush(batch: list) -> None:
        written, stale = put_batch(ddb, rt, batch)
        state["written"] = state.get("written", 0) + written
        state["suppressed_stale"] = state.get("suppressed_stale", 0) + stale

    while files:
        uri = files[0]
        bucket, key = split_s3(uri)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        batch: list = []
        paused_at = None
        with gzip.open(io.BytesIO(body), "rt", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i < offset or not line.strip():
                    continue
                item = build_item(spec, json.loads(line), tag,
                                  compress=compress,
                                  max_bytes=rt.max_item_bytes)
                if item:
                    batch.append(item)
                if len(batch) >= rt.batch_lines:
                    flush(batch)
                    batch = []
                    if remaining_ms() < rt.self_chain_floor_ms:
                        paused_at = i + 1
                        break
        flush(batch)
        if paused_at is not None:
            state["files"] = files
            state["line_offset"] = paused_at
            return state
        s3.delete_object(Bucket=bucket, Key=key)
        files.pop(0)
        offset = 0
        if files and remaining_ms() < rt.self_chain_floor_ms:
            break
    state["files"] = files
    state["line_offset"] = 0
    return state


def prune(ddb, spec: ServingSpec, rt: ServingRuntime, state: dict,
          remaining_ms) -> dict:
    """Post-baseline sweep: delete items this baseline did not write AND no
    later delta touched - the orphans a rebuild leaves, since a rebuild writes
    no change rows and nothing else would ever remove them.

    Paged scan on the same budget. `scan_key` is a key DICT, not a string: the
    key is composite, and a single-attribute cursor would silently truncate
    paging for any entity with a sort key.
    """
    tag, start = state["sync_tag"], state["baseline_start"]
    kwargs = {
        "TableName": rt.table_name,
        "ProjectionExpression": f"{PK}, {SK}",
        # The table is SHARED. Without the entity guard this sweep deletes
        # every other entity's items, because none of them carry this run's
        # syncTag - the one thing a shared table makes dangerous.
        "FilterExpression": (
            f"begins_with({PK}, :ent) AND "
            "(attribute_not_exists(syncTag) OR syncTag <> :tag) AND "
            "(attribute_not_exists(syncedAt) OR syncedAt < :start)"),
        "ExpressionAttributeValues": {":ent": {"S": f"{spec.entity}#"},
                                      ":tag": {"S": tag},
                                      ":start": {"S": start}},
    }
    if state.get("scan_key"):
        kwargs["ExclusiveStartKey"] = state["scan_key"]
    while True:
        resp = ddb.scan(**kwargs)
        keys = [{PK: it[PK], SK: it[SK]}
                for it in resp.get("Items", []) if PK in it and SK in it]
        if keys:
            batch_delete(ddb, rt, keys)
            state["pruned"] = state.get("pruned", 0) + len(keys)
        last = resp.get("LastEvaluatedKey")
        if not last:
            state["scan_key"] = None
            return state
        kwargs["ExclusiveStartKey"] = last
        state["scan_key"] = last
        if remaining_ms() < rt.self_chain_floor_ms:
            return state


def summary(state: dict) -> dict:
    return {"synced": state.get("written", 0),
            "deleted": state.get("deleted", 0),
            "suppressed_stale": state.get("suppressed_stale", 0),
            "pruned": state.get("pruned", 0)}


def self_chain(lam, rt: ServingRuntime, payload: dict) -> None:
    lam.invoke(FunctionName=rt.self_function_name, InvocationType="Event",
               Payload=json.dumps(payload).encode())


def continue_or_finish(lam, rt: ServingRuntime, state: dict) -> dict:
    if state.get("files"):
        self_chain(lam, rt, {"mode": "process", **state})
        return {"continued": len(state["files"]), **summary(state)}
    if state.get("prune_after") and not state.get("prune_started"):
        self_chain(lam, rt, {"mode": "prune", **state, "prune_started": True})
        return {"continued": 1, **summary(state)}
    print(f"[INFO] sync complete: {summary(state)}")
    return {"continued": 0, **summary(state)}


def rebuild_flag(args: dict) -> bool:
    v = args.get("--rebuild")
    return v is not None and str(v).strip().lower() not in (
        "false", "0", "no", "")


# ─── Spec loading and the sync itself ───────────────────────────────────────

import urllib.parse
import uuid

#: An identifier fence for anything interpolated into SQL. The run id arrives
#: from an EventBridge payload, so it is not ours until it passes this.
RUN_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_.:+-]{1,128}$", __import__("re").ASCII)


def load_spec(ddb, s3, gold_id: str, config_table: str, env: str,
              channel: str = "api") -> ServingSpec:
    """Build the spec from the LIVE config and master, not from constants.

    The config is the same item the transform and the dispatcher read, so the
    serving copy cannot drift from the pipeline that feeds it: add a column to
    the entity and `materialized_properties` carries it here on the next run.
    """
    resp = ddb.get_item(TableName=config_table,
                        Key={"gold_id": {"S": gold_id},
                             "version": {"S": "latest"}})
    item = resp.get("Item")
    if not item:
        raise RuntimeError(
            f"no config for gold_id={gold_id!r} version=latest in "
            f"{config_table} — refusing to materialize from defaults")
    cfg = _plain(item)

    master_path = ((cfg.get("schema") or {}).get("master_s3_path") or "")
    master = None
    if master_path:
        bucket, key = split_s3(resolve_env(master_path, env))
        master = json.loads(
            s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    else:
        # Without the master no property is cast, and an uncast timestamp
        # fails the UNLOAD. Refusing beats a run that dies mid-query.
        raise RuntimeError(
            f"{gold_id}: schema.master_s3_path is absent, so declared types "
            "are unknown — a timestamp property would fail the UNLOAD")

    return ServingSpec.from_config(cfg, env, master=master, channel=channel)


def _plain(item: dict):
    """DynamoDB item -> plain Python. Numbers stay strings: the only numeric
    config this module reads is a threshold it never arithmetics."""
    def un(v):
        (t, val), = v.items()
        if t == "L":
            return [un(x) for x in val]
        if t == "M":
            return {k: un(x) for k, x in val.items()}
        if t == "NULL":
            return None
        if t == "SS":
            return list(val)
        return val
    return {k: un(v) for k, v in item.items()}


def fetch_delete_keys(athena, spec: ServingSpec, qid: str) -> list:
    """Paged results of deletes_sql -> DynamoDB key dicts.

    The first page carries a header row and later pages do not, which is a
    paging quirk worth stating: dropping it unconditionally would delete a row
    keyed "productId", and keeping it on page two would drop a real key.
    """
    keys, token = [], None
    while True:
        kw = {"QueryExecutionId": qid, "MaxResults": 1000}
        if token:
            kw["NextToken"] = token
        resp = athena.get_query_results(**kw)
        rows = resp["ResultSet"]["Rows"]
        for r in (rows[1:] if token is None else rows):
            vals = [d.get("VarCharValue") for d in r["Data"]]
            if len(vals) != len(spec.key_props) or any(
                    v is None or v == "" for v in vals):
                continue
            k = item_key(spec, dict(zip(spec.key_props, vals)).get)
            if k is not None:
                keys.append(k)
        token = resp.get("NextToken")
        if not token:
            return keys


def sync(event: dict, spec: ServingSpec, rt: ServingRuntime,
         transform_job: str, reconcile_job: str, unload_s3: str,
         remaining_ms, athena, glue, ddb, lam, s3,
         compress=None) -> dict:
    """The whole materializer, entity-agnostic. A per-entity Lambda supplies
    the spec, the runtime and its clients; nothing below knows the entity.

    Modes, one code path out:
      process / prune   self-chain continuations
      baseline          full fill, then a prune for the orphans a rebuild left
      (EventBridge)     a SUCCEEDED gold transform or reconcile run
    """
    mode = event.get("mode")

    if mode == "process":
        state = process_files(s3, ddb, spec, rt, dict(event), remaining_ms,
                              compress=compress)
        return continue_or_finish(lam, rt, state)

    if mode == "prune":
        state = prune(ddb, spec, rt, dict(event), remaining_ms)
        if state.get("scan_key"):
            self_chain(lam, rt, {"mode": "prune", **state})
            return {"continued": 1, **summary(state)}
        print(f"[INFO] prune complete: {summary(state)}")
        return {"continued": 0, **summary(state)}

    if mode == "baseline":
        run_id = None
        sync_tag = f"baseline-{uuid.uuid4().hex[:12]}"
    else:
        # Reconcile is the one producer of gold DELETIONS, so a
        # transform-only trigger would never remove a serving item.
        detail = event.get("detail") or {}
        run_id = detail.get("jobRunId")
        job_name = detail.get("jobName")
        if (not run_id or detail.get("state") != "SUCCEEDED"
                or job_name not in (transform_job, reconcile_job)):
            return {"skipped": "not a succeeded gold-job event"}
        run = glue.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
        args = run.get("Arguments") or {}
        if args.get("--gold_id") != spec.entity:
            return {"skipped": f"gold_id {args.get('--gold_id')!r}"}
        if rebuild_flag(args):
            print("[WARN] rebuild run detected — it writes no change-log "
                  "rows, so there is nothing to read; invoke with "
                  "{'mode': 'baseline'} to re-sync")
            return {"skipped": "rebuild — baseline required"}
        if not RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"run_id {run_id!r} fails the identifier fence")
        sync_tag = run_id

    # ATTEMPT-UNIQUE destination: an async retry after a partial failure must
    # never UNLOAD into the previous attempt's non-empty prefix, which Athena
    # refuses outright.
    attempt = uuid.uuid4().hex[:8]
    dest = (unload_s3.rstrip("/")
            + f"/{spec.entity}/{urllib.parse.quote(sync_tag, safe='')}"
              f"-{attempt}/data/")
    run_query(athena, rt, unload_sql(spec, run_id, dest), remaining_ms)

    # gold_id rides in the state, so the continuation payload that
    # continue_or_finish spreads tells leg two which entity it is resuming.
    state = {"gold_id": spec.entity, "sync_tag": sync_tag,
             "files": list_unload_files(s3, dest),
             "line_offset": 0, "written": 0, "suppressed_stale": 0,
             "deleted": 0, "pruned": 0}

    if run_id:
        qid = run_query(athena, rt, deletes_sql(spec, run_id), remaining_ms)
        keys = fetch_delete_keys(athena, spec, qid)
        if keys:
            batch_delete(ddb, rt, keys)
            state["deleted"] = len(keys)
            print(f"[INFO] deleted {len(keys)} {spec.entity} keys no longer "
                  "in gold")
    else:
        state["prune_after"] = True
        state["baseline_start"] = _now_iso()

    state = process_files(s3, ddb, spec, rt, state, remaining_ms,
                          compress=compress)
    return continue_or_finish(lam, rt, state)
