"""Gold retrieval API — ONE Lambda, every served entity (UIFPE-518).

Reads the shared serving table that `gold_serving_sync` fills. Nothing here is
per-entity: the entity comes from the path, its key columns and its API
property set are derived from the live pipeline config and the entity's gold
master, and serving an entity is a `serving.enabled` line in that config.

Reads, and the key shape is what makes them cheap. WHICH read a path gets is
decided by the ENTITY'S GRAIN, not by the path length:

    composite grain (stock: productId + organizationCode)
      GET /{entity}/{key}                -> Query on one partition, ONE page
      GET /{entity}/{key}/{sub}/{subkey} -> GetItem

    single grain (price: productId alone)
      GET /{entity}/{key}                -> GetItem at the SK_NONE sentinel
      GET /{entity}/{key}/{sub}/{subkey} -> 404, there is no second key column

A single-grain entity answered from the collection branch would return a
one-item page where its contract declares the object — a different JSON
document for the same data. So the branch is chosen from `spec.key_props`.

See domains/stock/src/api/openapi.yaml and domains/price/src/api/openapi.yaml.

THE COLLECTION READ IS BOUNDED, and that is a platform rule rather than a
preference: `capabilities/api/guidelines.md` says *never return an unbounded
list*. One request is one Query with a `Limit`, never a loop that drains the
partition, so the work per request is capped whatever the entity holds. It
returns `{"items": [...], "nextCursor": ...}` — the envelope product's
contract already settled — and the cursor is opaque, so no consumer learns
that the store is DynamoDB and a different store's cursor can replace it
without a contract change.

For stock this bound is slack, not load-bearing: the partition is bounded by
grain, measured at 9 sub-grains at most across 87,157 partitions. It becomes
load-bearing for any entity whose partition grows with history, which is the
case this generic engine has to survive without being rewritten.

WHAT IT RETURNS is the API TIER ONLY, derived from the master's `x-channels`.
This is not a nicety: the serving copy stores every materialized property, and
for product three of them (`inventoryItemId`, `itemCatalogGroupId`,
`organizationId`) are Oracle surrogates marked `x-channels: []`. Returning the
stored item verbatim would leak them and break the contract's
`additionalProperties: false`. Deriving the set from the master means a new
entity cannot leak by omission.

Status codes, and why each one:
  200  a representation, with ETag + Cache-Control
  304  If-None-Match matched
  400  an unusable `pageSize` or `cursor`. A cursor is untrusted input, so a
       tampered or foreign one is refused here rather than handed to
       DynamoDB, which would answer a caller's error with a 500.
  401  no IES bearer token from the gateway, or one that is malformed,
       unverifiable or expired. A CONSUMER never sees this: their own
       credentials are checked at the gateway, one hop earlier.
  403  the gateway's shared header is wrong, once that check is ARMED; or a
       token that verifies whose subject is not on the allow-list
  404  single read with no such row; or an entity that is not served
  405  not a GET
  500  JWT_REQUIRED is on but its configuration is incomplete

WHAT AUTHENTICATES WHAT. Consumer auth is EntraID at the Mule gateway and
never reaches this function. There are TWO hops, and this handler is the far
end of the second one:

    consumer -> gateway    EntraID. The gateway owns it; nothing here sees it.
    gateway  -> function   a Pearson Identity (IES) token the gateway mints
                           for itself, which the checks below verify.

TWO INDEPENDENT CHECKS ON THAT SECOND HOP, in this order:

1. The gateway's shared header — a static shared secret. DISABLED while that
   secret has no value, so it has protected nothing since it shipped; it arms
   itself when the secret gains one. A transient failure to READ a populated
   secret keeps the last known value enforcing, so an IAM blip neither opens
   the door nor causes an outage.
2. The gateway's IES token — the same hop, verified properly: RS* signature
   against the issuer's JWKS, then issuer, expiry, token type and a subject
   allow-list. It DEFAULTS ON and refuses every request when switched on
   without being configured, which is deliberately the opposite posture to 1.

So the allow-listed subject names the GATEWAY, not an end user — the end user
was already authenticated one hop earlier and is not identified here. Issuer
tokens carry no `aud` claim, so that allow-list is the entire authorization
decision on this hop: any valid token from that issuer passes everything else.

Both checks exist to stop a caller who reaches this function url directly,
which is possible: its authorization_type is NONE.

A collection read that matches nothing is a 404, per the shipped contract and
the owner's decision of 2026-09-24. The serving copy holds only this entity's
rows, so it cannot tell an unknown key from a known key stocked nowhere; 404
is the honest answer to both rather than an empty array that would assert the
key exists. An empty page BEHIND a cursor is not that case — the partition
demonstrably exists — so it is a 200 with no items.

Import-time AWS-free; clients are injectable for tests.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import time
import urllib.parse

import jwt_verify
from gold_serving import EK, EKS, PK, SK, SK_NONE, index_sort, index_value, load_spec

#: Matches serving.tf's global_secondary_index name. One literal, because the
#: Lambda has no Terraform outputs to read — pinned by a test against the .tf.
INDEX_NAME = "entity-key-index"

ENV_NAME = os.environ.get("ENV_NAME", "dev")
TABLE_NAME = os.environ.get(
    "TABLE_NAME", f"uif-echo-{ENV_NAME}-gold-serving")
CONFIG_TABLE = os.environ.get(
    "CONFIG_TABLE", f"uif-echo-{ENV_NAME}-gold-pipeline-config")
GATEWAY_SECRET_ID = os.environ.get(
    "GATEWAY_SECRET_ID", f"uif/echo/{ENV_NAME}/gateway/gold-api")
GATEWAY_HEADER_NAME = os.environ.get(
    "GATEWAY_HEADER_NAME", "x-uif-gateway-auth").lower()

#: The gateway hop's identity check, from JWT_* variables
#: (platform/python/jwt_verify). NOT consumer auth — that is EntraID at the
#: gateway and never reaches here; this verifies the IES token the gateway
#: mints for itself, so the allow-listed subject names the gateway.
#:
#: DEFAULTS ON: a deployment that forgets the variables refuses rather than
#: serving gold data openly. Issuer tokens carry NO `aud` claim, so the
#: subject allow-list is the WHOLE authorization decision on this hop.
_auth = jwt_verify.Authorizer.from_env("gold-serving-api")

#: Short by intent. The ETag makes revalidation nearly free, so a small
#: max-age costs little and keeps an inventory position from going stale for
#: an hour.
CACHE_MAX_AGE = int(os.environ.get("CACHE_MAX_AGE", "300"))

#: Collection paging, following the envelope product's contract already
#: declares: `?pageSize=` 1..100 defaulting to 50, `?cursor=` opaque.
#:
#: NOT environment-overridable, deliberately. These two numbers are published
#: in the contract, so an env var that moved them would let one environment
#: answer differently from the document consumers integrate against — the
#: divergence would be invisible until a consumer's own validation failed.
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 100

_SECRET_TTL_SECONDS = 300
_secret_cache: dict = {"value": None, "at": 0.0, "known": False,
                       "state": "unknown"}

#: How long a derived spec is reused. A plain constant: an env knob for it was
#: written, found to be plumbed nowhere and untestable without reloading the
#: module, and removed — an unused knob is mutation surface, not flexibility.
_SPEC_TTL_SECONDS = 300
_spec_cache: dict = {}

#: Class words from Schema Standards N2. A path segment names the business
#: subject, not the class word: `organizationCode` addresses `/organization/`.
_CLASS_WORDS = (
    "IdType", "Id", "Code", "Type", "Category", "Status", "Date", "Timestamp",
    "Year", "Amount", "Quantity", "Count", "Number", "Rate", "Percent",
    "Sequence", "Indicator", "Name", "Text", "Description", "Level", "Method",
    "Platform", "System", "Uri", "Urn",
)

_clients: dict = {}


def clients(ddb=None, s3=None, secrets=None):
    """The three AWS clients, built ONCE per sandbox.

    Measured before this cached anything: three clients were constructed per
    request and that was ~300 ms of a ~320 ms warm response. botocore parses a
    JSON service model per client, which is CPU-bound, and Lambda scales CPU
    with memory — so on a small function it is the whole response time.

    The parameters stay, so tests still inject fakes; only the default
    changed. Fully injected callers never reach boto3 at all.
    """
    if ddb is not None and s3 is not None and secrets is not None:
        return ddb, s3, secrets
    if not _clients:
        import boto3
        _clients.update(ddb=boto3.client("dynamodb"),
                        s3=boto3.client("s3"),
                        secrets=boto3.client("secretsmanager"))
    return (ddb or _clients["ddb"], s3 or _clients["s3"],
            secrets or _clients["secrets"])


def path_segment(prop: str) -> str:
    """`organizationCode` -> `organization`; `productId` -> `product`.

    The class word is dropped and the remainder kebab-cased, per the API
    surface rule that a path segment names the subject. Pinned against the
    shipped contract by test, so a rename there fails here rather than in dev.
    """
    stem = prop
    for w in _CLASS_WORDS:
        if len(prop) > len(w) and prop.endswith(w):
            stem = prop[: -len(w)]
            break
    out = []
    for i, ch in enumerate(stem):
        if ch.isupper() and i:
            out.append("-")
        out.append(ch.lower())
    return "".join(out)


def expected_header(secrets) -> str | None:
    """The gateway's shared secret, or None meaning the check is DISABLED.

    Disabled is a DELIBERATE state - the secret is absent or empty - and never
    the result of a fetch error: a transient failure keeps the last known value
    enforcing, so an IAM blip cannot silently disarm the guard.
    """
    now = time.time()
    if _secret_cache["known"] and now - _secret_cache["at"] < _SECRET_TTL_SECONDS:
        return _secret_cache["value"]
    try:
        raw = secrets.get_secret_value(SecretId=GATEWAY_SECRET_ID)
    except Exception as e:                                   # noqa: BLE001
        if "ResourceNotFound" in type(e).__name__ + str(e):
            if _secret_cache.get("state") != "disabled":
                print(f"[WARN] gateway header check DISABLED — "
                      f"{GATEWAY_SECRET_ID} does not exist. This URL is "
                      "authorization_type NONE, so it is open.")
            _secret_cache.update(value=None, at=now, known=True,
                                 state="disabled")
            return None
        print(f"[ERROR] gateway secret fetch failed ({type(e).__name__}) — "
              "keeping the last known posture rather than disarming")
        _secret_cache["at"] = now
        return _secret_cache["value"]
    val = (raw.get("SecretString") or "").strip() or None
    if val is None and _secret_cache.get("state") != "disabled":
        print(f"[WARN] gateway header check DISABLED — {GATEWAY_SECRET_ID} "
              "has no value. This URL is authorization_type NONE, so it is "
              "open.")
    _secret_cache.update(value=val, at=now, known=True,
                         state="disabled" if val is None else "armed")
    return val


def spec_for(ddb, s3, entity: str):
    """`load_spec`, reused for `_SPEC_TTL_SECONDS`.

    load_spec costs TWO AWS round trips — the pipeline config from DynamoDB
    and the entity's master from S3 — and before this it paid both on every
    request, ahead of the read the consumer actually asked for.

    Bounded rather than removed, because load_spec is deliberately built from
    the LIVE config so the serving copy cannot drift from the pipeline feeding
    it. These entities run daily, so five minutes of staleness sits far inside
    the window that intent protects. What it does mean, stated plainly:
    enabling or disabling an entity, or adding a property, takes up to the TTL
    to show — against a pipeline whose data moves once a day.

    Failures are NOT cached. A newly enabled entity must start serving as soon
    as its config says so, and that matters more than sparing two calls for a
    caller asking about an entity that does not exist.
    """
    now = time.time()
    hit = _spec_cache.get(entity)
    if hit and now - hit[0] < _SPEC_TTL_SECONDS:
        return hit[1]
    spec = load_spec(ddb, s3, entity, CONFIG_TABLE, ENV_NAME, channel="api")
    _spec_cache[entity] = (now, spec)
    return spec


def response(status: int, body, headers: dict | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", **(headers or {})},
        "body": "" if body is None else json.dumps(body, separators=(",", ":")),
    }


def to_plain(av: dict):
    """One DynamoDB attribute value -> JSON. `N` becomes a NUMBER: the
    contract declares onHandQuantity an integer, and a quoted "644" would
    fail a consumer's own validation."""
    kind, val = next(iter(av.items()))
    if kind == "NULL":
        return None
    if kind == "BOOL":
        return bool(val)
    if kind == "N":
        return int(val) if "." not in str(val) else float(val)
    if kind == "L":
        return [to_plain(v) for v in val]
    if kind == "M":
        return {k: to_plain(v) for k, v in val.items()}
    return val


def project(item: dict, keep: tuple[str, ...],
            compress_prop: str | None = None) -> dict:
    """The api-tier view of a stored item, with every declared property
    present — a null is written as null, never omitted (N12).

    A COMPRESSED property is stored under `<prop>Gz` as bytes, so the plain
    name is absent from the item and the comprehension below would return a
    cheerful null for content that is actually there (UIFPE-767). The writer
    stores exactly one of the two shapes: `<prop>` as an explicit NULL when
    the value was null, or `<prop>Gz` when it was not.
    """
    out = {p: to_plain(item[p]) if p in item else None for p in keep}
    if compress_prop is None or compress_prop not in keep:
        return out

    blob = item.get(f"{compress_prop}Gz")
    if blob is not None and "B" in blob:
        try:
            out[compress_prop] = gzip.decompress(blob["B"]).decode("utf-8")
        except (OSError, EOFError, UnicodeDecodeError) as e:
            # Returning null here would present corrupt storage as a cleared
            # value, which a consumer would cache. Name the row and fail.
            print(f"[ERROR] GOLD_SERVING_CORRUPT_PAYLOAD {compress_prop} "
                  f"pk={item.get(PK, {}).get('S')} sk={item.get(SK, {}).get('S')} "
                  f"{type(e).__name__}: {e}")
            raise
    elif compress_prop not in item:
        # A stored null writes the plain attribute, so neither shape present
        # means the item was written without the compression declared — not a
        # cleared value. The response is the same null either way; the log is
        # what tells the two apart.
        print(f"[WARN] GOLD_SERVING_PAYLOAD_ABSENT {compress_prop} "
              f"pk={item.get(PK, {}).get('S')} sk={item.get(SK, {}).get('S')} "
              "— neither the property nor its Gz attribute is stored")
    return out


def collection_etag(items: list, next_cursor: str | None = None) -> str:
    """One ETag over the PAGE: a hash of its members' own ETags, so it moves
    when any member's content moves AND when membership changes. Sorted,
    because Query order must not change the ETag.

    The cursor is part of the hash because it is part of the representation.
    Deleting the one row just past a page boundary leaves that page's members
    identical while turning its cursor null, and an ETag over members alone
    would 304 a consumer into following a cursor that no longer exists.
    """
    parts = sorted(f'{i.get(SK, {}).get("S", "")}:{i.get("etag", {}).get("S", "")}'
                   for i in items)
    parts.append(f"next:{next_cursor or ''}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def encode_cursor(key: dict) -> str:
    """A DynamoDB `LastEvaluatedKey` as an opaque token.

    Opaque is the point. The token is base64url over the key's JSON, so the
    contract names no store and a different store's cursor — a Mongo `_id`
    range, say — replaces it without a consumer changing. It is neither a
    secret nor signed: it carries only the key of a row the caller was just
    served, and `decode_cursor` treats it as untrusted input.
    """
    raw = json.dumps(key, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(token: str, partition: dict, indexed: bool = False) -> dict:
    """The token back to an `ExclusiveStartKey`. `ValueError` means a 400.

    THE PARTITION IS CHECKED against the path, because a cursor is only valid
    for the collection that issued it. Without this a cursor from another
    product reaches DynamoDB, which rejects a start key outside the query's
    boundaries with a ValidationException — a 500 for what is a caller error.

    An index page's key carries the INDEX's pair as well as the table's, so
    both the shape and the attribute that anchors it depend on which was read.

    `validate=True` refuses characters outside the alphabet rather than
    discarding them, so garbage fails here instead of decoding to garbage.
    `binascii.Error` subclasses `ValueError`, as does a JSON or UTF-8 failure,
    so one except clause covers every malformed token.
    """
    pad = "=" * (-len(token) % 4)
    raw = base64.b64decode(token + pad, altchars=b"-_", validate=True)
    key = json.loads(raw.decode("utf-8"))
    expected = {EK, EKS, PK, SK} if indexed else {PK, SK}
    anchor = EK if indexed else PK
    if not isinstance(key, dict) or set(key) != expected:
        raise ValueError("cursor is not a key for this collection")
    if key.get(anchor) != partition:
        raise ValueError("cursor belongs to a different collection")
    return key


def page_size(qs: dict | None) -> int:
    """`?pageSize=`, defaulted and range-checked. `ValueError` means a 400."""
    raw = (qs or {}).get("pageSize")
    if raw in (None, ""):
        return PAGE_SIZE_DEFAULT
    try:
        size = int(raw)
    except (TypeError, ValueError):
        raise ValueError("pageSize must be an integer")
    if not 1 <= size <= PAGE_SIZE_MAX:
        raise ValueError(f"pageSize must be between 1 and {PAGE_SIZE_MAX}")
    return size


def split_path(raw: str) -> tuple | None:
    """`/stock/A1/organization/01E` -> ('stock', 'A1', 'organization', '01E').
    Returns None when the shape is neither read."""
    segs = [urllib.parse.unquote(s) for s in raw.split("/") if s]
    if len(segs) == 2:
        return segs[0], segs[1], None, None
    if len(segs) == 4:
        return segs[0], segs[1], segs[2], segs[3]
    return None


def handler(event: dict, _context=None, ddb=None, s3=None, secrets=None) -> dict:
    ddb, s3, secrets = clients(ddb, s3, secrets)

    ctx = (event.get("requestContext") or {}).get("http") or {}
    if (ctx.get("method") or event.get("httpMethod") or "GET").upper() != "GET":
        return response(405, {"error": "method not allowed"})

    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}

    # Defence in depth against a caller who found the function URL and skipped
    # the gateway. The gateway owns consumer auth; this is not it.
    #
    # DISABLED WHILE UNCONFIGURED (owner decision 2026-09-24): the gateway is a
    # separate layer and this API has to be exercisable before it exists, the
    # same posture marketing-copy runs. The check ARMS ITSELF the moment the
    # secret has a value, with no code change and no redeploy - the value is
    # read per invocation and cached for five minutes.
    #
    # What that costs, stated rather than buried: until then this URL serves
    # gold data to anyone who learns it, because its authorization_type is
    # NONE. Nothing else stands in front of it.
    expected = expected_header(secrets)
    if expected is not None and headers.get(GATEWAY_HEADER_NAME) != expected:
        return response(403, {"error": "missing or invalid gateway header"})

    # Consumer identity, after the cheap network-boundary check above: this one
    # can cost a modular exponentiation and, on a cold start, a JWKS fetch.
    denial = _auth.check(headers)
    if denial is not None:
        return response(denial.status, {"error": denial.error}, denial.headers)

    parts = split_path(event.get("rawPath") or event.get("path") or "")
    if parts is None:
        return response(404, {"error": "unknown path"})
    entity, key1, sub, key2 = parts

    try:
        spec = spec_for(ddb, s3, entity)
    except Exception as e:                                   # noqa: BLE001
        print(f"[WARN] no usable config for {entity!r}: {type(e).__name__}")
        return response(404, {"error": "unknown entity"})
    if not spec.enabled:
        return response(404, {"error": "unknown entity"})

    keep = spec.channel_props
    pk = {"S": f"{entity}#{key1}"}

    # WHICH READ, decided from the entity's own grain and not from the path
    # length alone. An entity whose grain IS its principal identifier has no
    # sub-grain — price is keyed on productId and nothing else — so
    # /{entity}/{key} resolves to exactly one item and must answer with the
    # object. Routing it to the collection branch would serve a singular
    # resource as a one-item page, which is a different JSON document from
    # the one its contract declares.
    # entity_keys, not key_props: the grain a CALLER sees is the entity's
    # business key. For every entity but program the two are the same tuple.
    single_grain = len(spec.entity_keys) == 1

    if sub is None and not single_grain:
        # Collection: ONE page of the sub-grains under one partition. One
        # Query and no loop — the work per request is capped by pageSize
        # whatever the partition holds, which is the bound
        # capabilities/api/guidelines.md requires.
        #
        # An indexed entity pages through the INDEX, because its own partition
        # is the merge key: that groups rows by a surrogate the caller cannot
        # know, not by the business key it asked about.
        if spec.indexed:
            partition = {"S": index_value(entity, key1)}
            where = {"IndexName": INDEX_NAME,
                     "KeyConditionExpression": f"{EK} = :p"}
        else:
            partition = pk
            where = {"KeyConditionExpression": f"{PK} = :p"}
        qs = event.get("queryStringParameters")
        token = (qs or {}).get("cursor") or None
        try:
            limit = page_size(qs)
            start = (decode_cursor(token, partition, spec.indexed)
                     if token else None)
        except ValueError as e:                                  # noqa: BLE001
            return response(400, {"error": str(e)})

        kw = {"TableName": TABLE_NAME, "Limit": limit,
              "ExpressionAttributeValues": {":p": partition}, **where}
        if start:
            kw["ExclusiveStartKey"] = start
        page = ddb.query(**kw)
        out = page.get("Items", [])
        last = page.get("LastEvaluatedKey")

        # An empty FIRST page is the 404 the contract declares. An empty page
        # behind a cursor is not: that partition demonstrably exists, and the
        # caller has simply walked off the end of it.
        if not out and start is None:
            return response(404, {"error": "not found"})

        # DynamoDB also stops at 1 MB per page, so a page can hold FEWER than
        # pageSize items and still have a cursor. Nothing here tops it up: a
        # consumer follows nextCursor, never an item count. The contract says
        # so in those words.
        nxt = encode_cursor(last) if last else None
        etag = f'"{collection_etag(out, nxt)}"'
        if headers.get("if-none-match") == etag:
            return response(304, None, {"etag": etag})
        body = {"items": [project(i, keep, spec.compress_prop) for i in out],
                "nextCursor": nxt}
        return response(200, body, {"etag": etag,
                                    "cache-control": f"max-age={CACHE_MAX_AGE}"})

    # Single read, reached two ways: a single-grain entity addressed by its
    # only key, or a composite entity addressed by both. The sub segment must
    # be the one the second key column names, or the path is not this
    # entity's — which is also what keeps a 4-segment path off a single-grain
    # entity, since it has no second key column to name.
    if spec.indexed:
        # Addressed by its business key, so the read goes through the
        # secondary index. The merge key stays storage either way: program's
        # gold table merges on the Oracle pair only because CDC cannot
        # express programId on four of its six sources, and that is not a
        # surface.
        if single_grain:
            # One business-key column, one path segment. Program is live on
            # this URL, so it does not grow a sub-resource.
            if sub is not None:
                return response(404, {"error": "unknown path"})
            sort, shown = index_sort(), key1
        else:
            # Two columns, so the second is named in the path exactly as the
            # merge-key read names its own second column. `sub is None` never
            # arrives here — that is the collection read above.
            if sub != path_segment(spec.entity_keys[1]):
                return response(404, {"error": "unknown path"})
            sort, shown = index_sort(key2), f"{key1}/{key2}"
        # Limit 2, deliberately, not 1. A secondary index does not enforce
        # uniqueness, so a duplicate is possible and has to be REPORTED —
        # taking whichever row the index happened to return first would serve
        # one of two answers silently, forever.
        page = ddb.query(
            TableName=TABLE_NAME, IndexName=INDEX_NAME,
            KeyConditionExpression=f"{EK} = :e AND {EKS} = :s",
            ExpressionAttributeValues={
                ":e": {"S": index_value(entity, key1)},
                ":s": {"S": sort}},
            Limit=2)
        found = page.get("Items", [])
        if len(found) > 1:
            # ENTITY_KEY_NOT_UNIQUE is a CONTRACT with serving_api.tf, which
            # turns this line into a metric and an alarm. A returned 500 does
            # not increment Lambda's Errors metric, so without the filter this
            # would be refused, logged, and alarm on nothing. Keep the token
            # exact; a test pins both sides.
            print(f"[ERROR] ENTITY_KEY_NOT_UNIQUE entity={entity} "
                  f"key={'+'.join(spec.entity_keys)} "
                  f"value={shown} rows={len(found)}")
            return response(500, {"error": "internal error"})
        item = found[0] if found else None
    else:
        if sub is None:
            sk_value = SK_NONE
        else:
            if len(spec.key_props) < 2 or sub != path_segment(spec.key_props[1]):
                return response(404, {"error": "unknown path"})
            sk_value = key2 or SK_NONE
        item = ddb.get_item(
            TableName=TABLE_NAME,
            Key={PK: pk, SK: {"S": sk_value}}).get("Item")

    if not item:
        return response(404, {"error": "not found"})
    etag = f'"{item.get("etag", {}).get("S", "")}"'
    if headers.get("if-none-match") == etag:
        return response(304, None, {"etag": etag})
    return response(200, project(item, keep, spec.compress_prop),
                    {"etag": etag, "cache-control": f"max-age={CACHE_MAX_AGE}"})
