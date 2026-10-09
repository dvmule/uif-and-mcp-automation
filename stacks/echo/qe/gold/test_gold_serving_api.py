# test_gold_serving_api.py
# The ONE retrieval API Lambda that serves every gold entity (UIFPE-518).
#
# The highest-value test here is test_a_zero_channel_property_is_NEVER_returned:
# the serving copy stores every materialized property, and for product three
# of them are Oracle surrogates marked `x-channels: []`. Returning a stored
# item verbatim would leak them and break the contract's
# `additionalProperties: false`.

import base64
import json
from pathlib import Path

import pytest
import yaml

import jwt_verify
import gold_serving_api as mod

STACK_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = STACK_ROOT.parents[1]

# Placement is a stated rule with ONE implementation (ADR-0022 decision 4).
# Reading these paths as literals is what let the api tier drift into two
# different directories, so the suite asks rather than restates.
from generate_tier_subsets import committed_path, contract_path


@pytest.fixture(autouse=True)
def _consumer_auth_off(monkeypatch):
    """This module exercises the READ path, so consumer auth is off here.

    It is on by default in the module as shipped, which would make every
    handler test below a 500. Disabling it in one place keeps these tests
    about what they are about — and the wiring that makes the handler call it
    at all is proven in test_jwt_verify.py, armed, against `handler` itself.
    Without those, deleting the call from the handler would leave this whole
    file green and the endpoint open.
    """
    monkeypatch.setattr(mod, "_auth", jwt_verify.Authorizer(required=False))


def expected_ref(domain, entity, operation="created"):
    """The reference a contract makes to its api projection.

    Derived, because the two sit in different trees (ADR-0022 decision 4)
    and a restated literal is the drift this rule exists to prevent.
    """
    import posixpath

    return posixpath.relpath(
        committed_path(domain, entity, "api", operation),
        posixpath.dirname(contract_path(domain, entity)))

STOCK = REPO_ROOT / "domains/stock"
MASTER = json.loads((STOCK / "src/schemas/gold/stock/v1/master.json")
                    .read_text(encoding="utf-8"))
CONTRACT = yaml.safe_load(
    (REPO_ROOT / contract_path("stock", "stock")).read_text(encoding="utf-8"))
# `-created`: a REST representation has no operation, and the pair is
# byte-identical in `properties`, so either half serves the contract.
API_PROJECTION = json.loads(
    (REPO_ROOT / committed_path("stock", "stock", "api", "created")
     ).read_text(encoding="utf-8"))

# price is the first SINGLE-GRAIN entity served: keyed on productId and
# nothing else, so /price/{productId} is a point read and not a collection.
# Kept alongside stock so the engine is exercised against two grains — the
# branch is chosen from key_props, and a suite that only ever sees one grain
# cannot tell that choice is being made.
PRICE = REPO_ROOT / "domains/price"
PRICE_MASTER = json.loads((PRICE / "src/schemas/gold/price/v1/master.json")
                          .read_text(encoding="utf-8"))
PRICE_CONTRACT = yaml.safe_load(
    (REPO_ROOT / contract_path("price", "price")).read_text(encoding="utf-8"))
PRICE_PROJECTION = json.loads(
    (REPO_ROOT / committed_path("price", "price", "api", "created")
     ).read_text(encoding="utf-8"))

SECRET = "s3cr3t-gateway-value"
HDR = "x-uif-gateway-auth"


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Three module globals would leak between tests, and the spec cache is
    the dangerous one: the N7 tests below pass a MODIFIED master through
    FakeS3, so a spec cached by an earlier test would be reused and those
    tests would pass while proving nothing about x-channels at all."""
    def clear():
        mod._secret_cache.update(value=None, at=0.0, known=False,
                                 state="unknown")
        mod._spec_cache.clear()
        mod._clients.clear()
    clear()
    yield
    clear()


# ── fakes ───────────────────────────────────────────────────────────────────

def _config_item(served=True, extra_props=()):
    item = {
        "gold_id": {"S": "stock"},
        "target": {"M": {
            "database": {"S": "uif_echo_{env}_gold_stock"},
            "table": {"S": "stock"},
            "primary_key_cols": {"L": [{"S": "productId"},
                                       {"S": "organizationCode"}]}}},
        "materialized_properties": {"L": [{"S": p} for p in (
            "availableToPromiseIndicator", "availableToPromiseTimestamp",
            "isbn13", "onHandQuantity", "organizationCode", "productId",
            *extra_props)]},
        "schema": {"M": {
            "version": {"S": "v1"},
            "master_s3_path": {"S": "s3://b/schemas/gold/stock/v1/master.json"}}},
    }
    if served:
        item["serving"] = {"M": {"enabled": {"BOOL": True}}}
    return item


def _stored(product="A1", org="01E", etag="e-A1-01E", qty=644):
    """A row exactly as the sync writes it — framework attributes included."""
    return {
        "pk": {"S": f"stock#{product}"}, "sk": {"S": org},
        "productId": {"S": product}, "organizationCode": {"S": org},
        "onHandQuantity": {"N": str(qty)},
        "availableToPromiseIndicator": {"BOOL": True},
        "availableToPromiseTimestamp": {"NULL": True},
        "isbn13": {"NULL": True},
        "etag": {"S": etag}, "goldUpdatedAt": {"S": "2026-09-12T19:03:11.000000Z"},
        "syncTag": {"S": "baseline-abc"}, "syncedAt": {"S": "2026-09-24T12:00:00"},
    }


class FakeDdb:
    def __init__(self, items=None, served=True, config=True, extra_props=(),
                 config_item=None):
        self.items = items if items is not None else [_stored()]
        self.served = served
        self.config = config
        self.extra_props = extra_props
        #: An explicit config lets one fake serve entities of DIFFERENT GRAIN.
        #: The engine picks its read from key_props, so a suite that only ever
        #: sees stock's two-column grain cannot tell that branch is working.
        self.config_item = config_item
        self.queries = []
        self.gets = []

    def get_item(self, TableName, Key):
        self.gets.append(TableName)
        if "pipeline-config" in TableName:
            if not self.config:
                return {}
            return {"Item": self.config_item
                    or _config_item(self.served, self.extra_props)}
        for it in self.items:
            if it["pk"] == Key["pk"] and it["sk"] == Key["sk"]:
                return {"Item": it}
        return {}

    def query(self, **kw):
        """Honours Limit and ExclusiveStartKey, because a fake that ignores
        them cannot tell a bounded read from an unbounded one — which is how
        the drain loop this replaced went 37 tests without being noticed."""
        self.queries.append(kw)
        if kw.get("IndexName"):
            # The secondary index: partitioned on `ek`, SORTED on `eks`. The
            # item read pins both and the collection read pins only the
            # partition, so the fake has to honour a sort condition when there
            # is one — without that, a broken item read passes by returning
            # the whole partition. Duplicates are still representable: `eks`
            # is not unique in a GSI.
            vals = kw["ExpressionAttributeValues"]
            ek = vals.get(":e", vals.get(":p"))
            rows = [i for i in self.items if i.get("ek") == ek]
            if ":s" in vals:
                rows = [i for i in rows if i.get("eks") == vals[":s"]]
            rows = sorted(rows, key=lambda i: i.get("eks", {}).get("S", ""))
            start = kw.get("ExclusiveStartKey")
            if start:
                rows = [i for i in rows
                        if i["eks"]["S"] > start["eks"]["S"]]
            limit = kw.get("Limit")
            out = {"Items": rows[:limit] if limit else rows}
            if limit and len(rows) > limit:
                last = rows[limit - 1]
                # An index page's key carries BOTH pairs, which is what
                # decode_cursor has to accept.
                out["LastEvaluatedKey"] = {
                    "ek": last["ek"], "eks": last["eks"],
                    "pk": last["pk"], "sk": last["sk"]}
            return out
        pk = kw["ExpressionAttributeValues"][":p"]
        rows = sorted((i for i in self.items if i["pk"] == pk),
                      key=lambda i: i["sk"]["S"])
        start = kw.get("ExclusiveStartKey")
        if start:
            rows = [i for i in rows if i["sk"]["S"] > start["sk"]["S"]]
        limit = kw.get("Limit")
        out = {"Items": rows[:limit] if limit else rows}
        if limit and len(rows) > limit:
            last = rows[limit - 1]
            out["LastEvaluatedKey"] = {"pk": last["pk"], "sk": last["sk"]}
        return out


def _price_config_item():
    """price's real shape: ONE key column, three materialized properties."""
    return {
        "gold_id": {"S": "price"},
        "target": {"M": {
            "database": {"S": "uif_echo_{env}_gold_price"},
            "table": {"S": "price"},
            "primary_key_cols": {"L": [{"S": "productId"}]}}},
        "materialized_properties": {"L": [{"S": p} for p in (
            "isbn13", "priceSpecifications", "productId")]},
        "schema": {"M": {
            "version": {"S": "v1"},
            "master_s3_path": {"S": "s3://b/schemas/gold/price/v1/master.json"}}},
        "serving": {"M": {"enabled": {"BOOL": True}}},
    }


def _price_stored(product="A1", etag="e-price-A1", specs=1):
    """A price row as the sync writes it: sk is the SK_NONE sentinel, because
    an entity whose grain IS its principal identifier has no sub-grain."""
    spec = {"M": {
        "priceListName": {"S": "US List"}, "currencyCode": {"S": "USD"},
        "listPriceAmount": {"S": "49.99"}, "netPriceAmount": {"S": "44.99"},
        "vatPriceAmount": {"NULL": True}, "vatCountryCode": {"NULL": True},
        "fromQuantity": {"N": "1"}, "toQuantity": {"NULL": True},
        "priceStartTimestamp": {"S": "2026-01-01T00:00:00Z"},
        "priceEndTimestamp": {"NULL": True},
        "priceLastModifiedTimestamp": {"S": "2026-09-01T00:00:00Z"},
        "priceDurationText": {"NULL": True},
        "monthlyPriceAmount": {"NULL": True},
        "customerChannelCode": {"S": "DIRECT"}}}
    return {
        "pk": {"S": f"price#{product}"}, "sk": {"S": "-"},
        "productId": {"S": product}, "isbn13": {"S": "9780134685991"},
        "priceSpecifications": {"L": [spec] * specs},
        "etag": {"S": etag}, "goldUpdatedAt": {"S": "2026-09-20T06:00:00.000000Z"},
        "syncTag": {"S": "baseline-abc"}, "syncedAt": {"S": "2026-09-28T09:00:00"},
    }


class FakeS3:
    def __init__(self, master=None):
        self.master = MASTER if master is None else master
        self.calls = 0

    def get_object(self, Bucket, Key):
        self.calls += 1

        class B:
            @staticmethod
            def read(m=self.master):
                return json.dumps(m).encode()
        return {"Body": B()}


class FakeSecrets:
    def __init__(self, value=SECRET, raises=None):
        self.value = value
        self.raises = raises
        self.calls = 0

    def get_secret_value(self, SecretId):
        self.calls += 1
        if self.raises:
            raise self.raises
        if self.value is None:
            return {}
        return {"SecretString": self.value}


class NotFound(Exception):
    """Stands in for botocore's ResourceNotFoundException."""


def _get(path, headers=None, ddb=None, secrets=None, s3=None, method="GET",
         qs=None):
    ev = {"rawPath": path,
          "requestContext": {"http": {"method": method}},
          "queryStringParameters": qs,
          "headers": {HDR: SECRET, **(headers or {})}}
    r = mod.handler(ev, None, ddb=ddb or FakeDdb(),
                    s3=s3 or FakeS3(), secrets=secrets or FakeSecrets())
    r["json"] = json.loads(r["body"]) if r["body"] else None
    return r


# ── the deployment artifact is complete ────────────────────────────────────

@pytest.mark.parametrize("tf_file,block,module", [
    ("serving_api.tf", "gold_serving_api", "gold_serving_api"),
    ("serving.tf", "gold_serving_sync", "gold_serving_sync"),
])
def test_the_lambda_zip_carries_everything_the_handler_IMPORTS(
        tf_file, block, module, tmp_path):
    """A Lambda zip is FLAT and is assembled from an explicit file() list. An
    import added without a matching source block does not fail here or in
    CI — it fails on a cold start in dev, with an ImportError that says
    nothing about Terraform. So build the flat zip from the list the
    Terraform actually names and import the handler out of it.
    """
    import re
    import shutil
    import subprocess
    import sys

    tf_dir = STACK_ROOT / "infrastructure" / "terraform-gold"
    tf = (tf_dir / tf_file).read_text(encoding="utf-8")
    body = tf.split(f'data "archive_file" "{block}"')[1].split("\n}\n")[0]
    srcs = re.findall(r'file\("\$\{path\.module\}/([^"]+)"\)', body)
    assert srcs, f"no file() sources found in {tf_file}"

    for rel in srcs:
        src = (tf_dir / rel).resolve()
        assert src.exists(), f"{tf_file} names a missing file: {rel}"
        shutil.copy(src, tmp_path / src.name)

    r = subprocess.run(
        [sys.executable, "-c", f"import {module} as m; assert callable(m.handler)"],
        cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, (
        f"{module} cannot import from its own zip contents "
        f"{sorted(f.name for f in tmp_path.iterdir())}:\n{r.stderr[-600:]}")


# ── the path shape is PINNED to the shipped contract ───────────────────────

def test_the_derived_path_matches_the_SHIPPED_contract():
    """The sub-resource segment is derived by dropping the class word
    (`organizationCode` -> `organization`). If the contract's path and this
    derivation ever disagree, the API 404s in dev — so assert them equal."""
    paths = list(CONTRACT["paths"])
    single = [p for p in paths if p.count("{") == 2][0]
    assert single == "/stock/{productId}/organization/{organizationCode}"
    assert mod.path_segment("organizationCode") == single.split("/")[3]


@pytest.mark.parametrize("prop,expected", [
    ("organizationCode", "organization"),
    ("productId", "product"),
    ("availableToPromiseTimestamp", "available-to-promise"),
    ("isbn13", "isbn13"),
])
def test_path_segment_drops_the_class_word_and_kebabs(prop, expected):
    assert mod.path_segment(prop) == expected


# ── the gateway header: a bypass guard, not consumer auth ──────────────────

def test_an_unpopulated_secret_DISABLES_the_check_and_serves():
    """Owner decision 2026-09-24: the gateway is a separate layer, so the API
    has to be exercisable before it exists. Same posture as marketing-copy.

    The cost is real and is the reason this test says `and serves`: with
    authorization_type NONE and no check, the URL is open.
    """
    ev = {"rawPath": "/stock/A1", "requestContext": {"http": {"method": "GET"}},
          "headers": {}}
    r = mod.handler(ev, None, ddb=FakeDdb(), s3=FakeS3(),
                    secrets=FakeSecrets(value=None))
    assert r["statusCode"] == 200, "an unconfigured check must not block"


def test_a_MISSING_secret_also_disables_the_check():
    ev = {"rawPath": "/stock/A1", "requestContext": {"http": {"method": "GET"}},
          "headers": {}}
    r = mod.handler(ev, None, ddb=FakeDdb(), s3=FakeS3(),
                    secrets=FakeSecrets(raises=NotFound("ResourceNotFound")))
    assert r["statusCode"] == 200


def test_the_check_ARMS_ITSELF_when_the_secret_gains_a_value():
    """No redeploy and no code change: the value is read per invocation and
    cached for five minutes, so populating the secret is the whole switch."""
    sec = FakeSecrets(value=None)
    ev = {"rawPath": "/stock/A1", "requestContext": {"http": {"method": "GET"}},
          "headers": {}}
    kw = dict(ddb=FakeDdb(), s3=FakeS3(), secrets=sec)
    assert mod.handler(ev, None, **kw)["statusCode"] == 200

    sec.value = SECRET                       # someone populates it
    mod._secret_cache["at"] = 0.0            # the cache expires
    assert mod.handler(ev, None, **kw)["statusCode"] == 403, (
        "once the secret has a value the check must bite")
    assert _get("/stock/A1", secrets=sec)["statusCode"] == 200


def test_a_wrong_header_is_403():
    r = _get("/stock/A1", headers={HDR: "nope"})
    assert r["statusCode"] == 403


def test_an_absent_header_is_403():
    ev = {"rawPath": "/stock/A1", "requestContext": {"http": {"method": "GET"}},
          "headers": {}}
    r = mod.handler(ev, None, ddb=FakeDdb(), s3=FakeS3(), secrets=FakeSecrets())
    assert r["statusCode"] == 403


def test_a_TRANSIENT_secret_failure_keeps_ENFORCING_not_disarming():
    """An IAM blip must not open the door, and must not cause an outage
    either: the last known value keeps enforcing. Marketing-copy's rule."""
    sec = FakeSecrets()
    assert _get("/stock/A1", secrets=sec)["statusCode"] == 200
    mod._secret_cache["at"] = 0.0                      # force a re-read
    sec.raises = RuntimeError("ThrottlingException")
    assert _get("/stock/A1", secrets=sec)["statusCode"] == 200
    assert _get("/stock/A1", headers={HDR: "nope"},
                secrets=sec)["statusCode"] == 403


def test_consumer_auth_headers_are_NEVER_inspected():
    """apikey and Authorization belong to the gateway. A request carrying
    neither, but carrying the gateway header, must still be served."""
    r = _get("/stock/A1")
    assert r["statusCode"] == 200


def test_not_a_get_is_405():
    assert _get("/stock/A1", method="POST")["statusCode"] == 405


# ── the single read ────────────────────────────────────────────────────────

def test_single_read_returns_one_item_with_the_stored_etag():
    r = _get("/stock/A1/organization/01E")
    assert r["statusCode"] == 200
    assert r["headers"]["etag"] == '"e-A1-01E"'
    assert r["headers"]["cache-control"] == "max-age=300"
    assert r["json"]["productId"] == "A1"


def test_if_none_match_returns_304_with_no_body():
    r = _get("/stock/A1/organization/01E", headers={"if-none-match": '"e-A1-01E"'})
    assert r["statusCode"] == 304
    assert r["body"] == ""


def test_a_missing_row_is_404():
    r = _get("/stock/A1/organization/NOPE")
    assert r["statusCode"] == 404


def test_a_sub_segment_the_key_does_not_name_is_404():
    """Guards against /stock/A1/banana/01E resolving the same item."""
    r = _get("/stock/A1/banana/01E")
    assert r["statusCode"] == 404


# ── the collection read ────────────────────────────────────────────────────

def test_collection_returns_every_organization_for_the_product():
    ddb = FakeDdb(items=[_stored(org="01E", etag="e1"),
                         _stored(org="07B", etag="e2", qty=0)])
    r = _get("/stock/A1", ddb=ddb)
    assert r["statusCode"] == 200
    assert {i["organizationCode"] for i in r["json"]["items"]} == {"01E", "07B"}
    assert [i["onHandQuantity"] for i in r["json"]["items"]].count(0) == 1, (
        "a zero quantity is a real position, not an absence")
    assert r["json"]["nextCursor"] is None, "two rows fit one page"


def test_collection_queries_ONE_partition_never_scans():
    ddb = FakeDdb()
    _get("/stock/A1", ddb=ddb)
    assert ddb.queries[0]["KeyConditionExpression"] == "pk = :p"
    assert ddb.queries[0]["ExpressionAttributeValues"][":p"] == {"S": "stock#A1"}


# ── price: a SINGLE-GRAIN entity on the same engine (UIFPE-520) ────────────
#
# The engine chooses its read from the entity's key_props, not from the path
# length. stock has two key columns, so /stock/{id} is a collection; price has
# one, so /price/{id} is a point read. These prove the branch exists and is
# taken — before price, every test in this file saw one grain and the choice
# could not have been observed.

def _price_get(path, **kw):
    return _get(path, ddb=FakeDdb(items=[_price_stored()],
                                  config_item=_price_config_item()),
                s3=FakeS3(master=PRICE_MASTER), **kw)


def test_a_single_grain_entity_returns_the_OBJECT_not_a_page():
    """THE POINT OF THIS SECTION. Routed to the collection branch, price
    would answer {items:[{...}], nextCursor:null} — a different JSON document
    from the one its contract declares for the same data."""
    r = _price_get("/price/A1")
    assert r["statusCode"] == 200
    assert "items" not in r["json"] and "nextCursor" not in r["json"]
    assert r["json"]["productId"] == "A1"
    shape = PRICE_CONTRACT["paths"]["/price/{productId}"]["get"]
    shape = shape["responses"]["200"]["content"]["application/json"]["schema"]
    assert shape == {"$ref": expected_ref("price", "price")}, (
        "the contract declares an object, so the engine must return one")


def test_a_single_grain_read_is_a_GETITEM_at_the_sentinel_not_a_query():
    ddb = FakeDdb(items=[_price_stored()], config_item=_price_config_item())
    _get("/price/A1", ddb=ddb, s3=FakeS3(master=PRICE_MASTER))
    assert ddb.queries == [], "a point read must not Query"
    assert any("gold-serving" in t for t in ddb.gets)


def test_a_sub_resource_path_on_a_single_grain_entity_is_404():
    """There is no second key column to name, so there is no such resource.
    price has no organization grain at all — its api tier carries no
    organizationCode, which is why the contract offers one operation."""
    assert _price_get("/price/A1/organization/82")["statusCode"] == 404
    assert "organizationCode" not in PRICE_PROJECTION["properties"]


def test_price_returns_EXACTLY_its_own_projection():
    r = _price_get("/price/A1")
    assert set(r["json"]) == set(PRICE_PROJECTION["properties"])
    for leaked in ("pk", "sk", "syncTag", "syncedAt", "etag", "goldUpdatedAt"):
        assert leaked not in r["json"]


def test_a_product_with_NO_price_gets_an_empty_ARRAY_not_null():
    """256,526 of 1,492,904 rows carry no price in domain, so this is the
    steady state and not an error. N12: an empty list is [], never null."""
    ddb = FakeDdb(items=[_price_stored(specs=0)],
                  config_item=_price_config_item())
    r = _get("/price/A1", ddb=ddb, s3=FakeS3(master=PRICE_MASTER))
    assert r["statusCode"] == 200
    assert r["json"]["priceSpecifications"] == []


def test_a_missing_price_row_is_404():
    ddb = FakeDdb(items=[], config_item=_price_config_item())
    assert _get("/price/NOPE", ddb=ddb,
                s3=FakeS3(master=PRICE_MASTER))["statusCode"] == 404


def test_price_carries_an_etag_and_revalidates():
    first = _price_get("/price/A1")
    again = _price_get("/price/A1",
                       headers={"if-none-match": first["headers"]["etag"]})
    assert again["statusCode"] == 304 and again["body"] == ""


def _pipeline_configs():
    """(path, cfg) for every gold pipeline config in every domain."""
    return [(p, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
            for p in sorted((REPO_ROOT / "domains")
                            .glob("*/src/apps/pipelines/base/*.yaml"))]


def _entity_of(path, cfg):
    return path.parents[4].name, (cfg.get("gold_id") or path.stem)


#: Every (domain, entity) this engine might serve — DISCOVERED, not listed. It
#: was four hardcoded pairs until UIFPE-767, and a fifth entity opting in
#: silently escaped every assertion that reads it.
_CANDIDATES = [_entity_of(p, c) for p, c in _pipeline_configs()]


def _served():
    """Every (domain, entity) whose pipeline config says it is served —
    DISCOVERED by walking the configs, not filtered from a list.

    It WAS a hardcoded `_CANDIDATES` of four until UIFPE-767. That is one step
    better than the single named entity it replaced in UIFPE-554, and still
    not a guard: marketing_copy set serving.enabled and silently escaped
    every assertion below, while the coverage test passed because it only
    checked the list was non-empty and held product.
    """
    return [_entity_of(p, c) for p, c in _pipeline_configs()
            if (c.get("serving") or {}).get("enabled") is True]


def test_a_served_entity_has_a_published_contract():
    """Serving an entity with no contract is an endpoint nobody can use; the
    reverse — a contract with no serving — is a 404 with a promise attached.
    Only the first is asserted here, because the second is a legitimate
    intermediate state: stock sat in it for days, and price is in it now."""
    for domain, entity in _served():
        api = REPO_ROOT / contract_path(domain, entity)
        assert api.exists(), (
            f"{entity} sets serving.enabled but {contract_path(domain, entity)} "
            f"does not exist — it is reachable and undocumented")


@pytest.mark.parametrize("domain,entity", _CANDIDATES)
def test_every_nested_property_of_a_served_entity_is_DESCRIBED(domain, entity):
    """The successor to UIFPE-520's refusal guard, which said a served entity
    may carry NO nesting because `_attr` could only stringify it. The engine
    handles nesting now, so the question changes from "is it nested" to "can
    the engine build it" — and the answer is no when the master declares a
    structure without declaring its members.

    Such a property falls back to `_attr`'s generic recursion: stored, but
    untyped and unfiltered, so a decimal would go through a float and a
    surrogate member would not be stripped. That is a silent downgrade, which
    is what this refuses.
    """
    import gold_serving as gs

    pipeline = REPO_ROOT / f"domains/{domain}/src/apps/pipelines/base/{entity}.yaml"
    mpath = REPO_ROOT / f"domains/{domain}/src/schemas/gold/{entity}/v1/master.json"
    if not pipeline.exists() or not mpath.exists():
        pytest.skip(f"{domain}/{entity} has no pipeline or master")
    cfg = yaml.safe_load(pipeline.read_text(encoding="utf-8")) or {}
    if (cfg.get("serving") or {}).get("enabled") is not True:
        pytest.skip(f"{entity} is not served")

    master = json.loads(mpath.read_text(encoding="utf-8"))
    props = master.get("properties") or {}
    spec = gs.ServingSpec.from_config(
        {"gold_id": entity,
         "target": {"database": f"uif_echo_{{env}}_gold_{entity}",
                    "table": entity,
                    "primary_key_cols": [sorted(props)[0]]},
         "materialized_properties": sorted(props)},
        "dev", master=master, channel="api")

    def needs_members(name: str, d: dict) -> bool:
        """Whether NOT being in nested_props is a problem for this property.

        A value array has no members to declare — program's
        associatedProducts is ARRAY<STRING>, and `_attr`'s list recursion
        stores it as an L of S with nothing to type and nothing to filter.
        Measured through the real UNLOAD path, not assumed.

        A `number` value array is still flagged: the untyped path takes a JSON
        number through a Python float, which is the decimal-exactness problem
        UIFPE-686 covers, so it must not slip in as "just a value array".
        """
        d = d or {}
        if "api" not in (d.get("x-channels") or []):
            return False
        if not ("object" in str(d.get("type")) or "array" in str(d.get("type"))):
            return False
        items = d.get("items") or {}
        scalar = items.get("type") in ("string", "integer", "boolean")
        return not (scalar and not items.get("properties"))

    undescribed = sorted(p for p, d in props.items()
                         if needs_members(p, d) and p not in spec.nested_props)
    assert not undescribed, (
        f"{entity} is served and its api tier carries {undescribed}, which "
        f"the master declares as a structure without declaring the members — "
        f"so the engine stores them untyped and unfiltered")


def test_the_collection_branch_still_works_for_a_COMPOSITE_grain():
    """The regression guard for the change that made price possible."""
    ddb = FakeDdb(items=[_stored(org="01E"), _stored(org="07B")])
    r = _get("/stock/A1", ddb=ddb)
    assert set(r["json"]) == {"items", "nextCursor"}
    assert len(r["json"]["items"]) == 2
    assert ddb.queries, "a composite grain must still Query"


# ── the SLO is published AND fired on ──────────────────────────────────────
#
# An SLO is stated twice by necessity: in the contract, where a consumer reads
# it, and in Terraform, where an alarm fires on it. That pair is exactly the
# shape that drifts, and drift is silent in both directions — a number no
# alarm watches is not an objective, and a threshold no contract publishes is
# not an SLO.

def _tf(name: str) -> str:
    return (STACK_ROOT / "infrastructure" / "terraform-gold" / name).read_text(
        encoding="utf-8")


def test_the_contracts_slo_equals_the_alarm_thresholds():
    slo = CONTRACT["x-slo"]
    locals_tf = _tf("locals.tf")
    block = locals_tf.split("serving_api_slo = {", 1)[1].split("}", 1)[0]
    declared = dict(
        line.split("=")[0].strip() and
        (line.split("=")[0].strip(), int(line.split("=")[1].strip()))
        for line in block.strip().splitlines() if "=" in line)
    assert declared["p50_ms"] == slo["latencyP50Milliseconds"]
    assert declared["p99_ms"] == slo["latencyP99Milliseconds"]


@pytest.mark.parametrize("tf_file,block,floor_mb,why", [
    ("serving_api.tf", "gold_serving_api", 1024,
     "warm p50 was 317 ms at 256 MB and 5.2 ms at 1024"),
    ("serving.tf", "gold_serving_sync", 1769,
     "the fill ran at ~53 items/s/leg while DynamoDB answered in 3.5 ms"),
])
def test_a_serving_lambdas_memory_is_the_MEASURED_size(tf_file, block, floor_mb, why):
    """Both numbers buy CPU, not memory, and both look like waste to anyone
    reading the utilisation: the API uses ~100 MB of 1024 and the sync ~330 MB
    of 1769. Neither was chosen from utilisation — they were chosen from
    measured throughput, and Lambda scales vCPU with memory.

    Pinned so a well-meant "right-size the memory" cannot quietly undo the
    single biggest performance change either function has had.
    """
    tf = _tf(tf_file)
    body = tf.split(f'resource "aws_lambda_function" "{block}"', 1)[1]
    body = body.split("\n}\n", 1)[0]
    lines = [ln for ln in body.splitlines() if ln.strip().startswith("memory_size")]
    assert len(lines) == 1, f"{block} has {len(lines)} memory_size lines"
    got = int(lines[0].split("=")[1].strip())
    assert got >= floor_mb, (
        f"{block} is {got} MB, below the measured {floor_mb} MB — {why}. "
        f"Utilisation is not the reason for either figure; throughput is.")


@pytest.mark.parametrize("alarm,statistic,knob", [
    ("gold_serving_api_slow_warm", "p50", "p50_ms"),
    ("gold_serving_api_slo_breached", "p99", "p99_ms"),
])
def test_each_latency_alarm_reads_the_slo_rather_than_a_literal(
        alarm, statistic, knob):
    """A hard-coded threshold is how the alarm and the contract come apart."""
    tf = _tf("serving_api.tf")
    body = tf.split(f'resource "aws_cloudwatch_metric_alarm" "{alarm}"', 1)[1]
    body = body.split("\n}\n", 1)[0]
    assert f'extended_statistic  = "{statistic}"' in body or \
           f'extended_statistic = "{statistic}"' in body, body[:400]
    # The THRESHOLD line specifically, not the body. A control round caught
    # this asserting on the body and passing while the threshold was a
    # literal: the alarm's own description interpolates the same local to
    # print the number, so the substring was satisfied by the prose.
    lines = [ln for ln in body.splitlines()
             if ln.strip().startswith("threshold")]
    assert len(lines) == 1, f"{alarm} has {len(lines)} threshold lines"
    assert f"local.serving_api_slo.{knob}" in lines[0], (
        f"{alarm}'s threshold is a literal, not the SLO: {lines[0].strip()}")
    assert 'metric_name         = "Duration"' in body
    assert "alarm_actions = local.alert_targets" in body, (
        "an alarm with no action notifies nobody — the silver root's alarm "
        "has that bug and it is not being copied here")


def test_the_freshness_slo_is_NOT_a_number_invented_here():
    """36 hours is 1.5x a daily cadence, which is what the per-entity
    FreshnessBreached alarm already fires on. Publishing a tighter figure than
    the one being monitored would be an objective nothing enforces."""
    assert CONTRACT["x-slo"]["freshnessHours"] == 36
    alerts = _tf("alerts.tf")
    assert 'metric_name         = "FreshnessBreached"' in alerts
    assert "1.5x its" in alerts, (
        "the freshness alarm no longer describes a 1.5x-cadence bound, so the "
        "contract's 36 hours may no longer be the monitored figure")


def test_availability_rests_on_alarms_that_EXIST():
    tf = _tf("serving_api.tf")
    assert CONTRACT["x-slo"]["availabilityPercent"] == 99.9
    for metric in ('"Errors"', '"Throttles"'):
        assert f"metric_name         = {metric}" in tf


# ── the hot path does no work twice ────────────────────────────────────────
#
# Measured on dev before any of this was cached: a warm response took ~320 ms
# median, of which the DynamoDB read the consumer asked for was single-digit
# milliseconds. The rest was three boto3 clients constructed per request, plus
# load_spec's two round trips -- the pipeline config from DynamoDB and the
# entity's master from S3 -- also per request.

def test_the_spec_is_fetched_ONCE_not_per_request():
    """load_spec costs a DynamoDB get_item AND an S3 get_object. Paying that
    on every request put two avoidable round trips ahead of the read."""
    ddb, s3 = FakeDdb(), FakeS3()
    before = len([k for k in ddb.gets if "pipeline-config" in k])
    for _ in range(5):
        assert _get("/stock/A1", ddb=ddb, s3=s3)["statusCode"] == 200
    config_reads = len([k for k in ddb.gets if "pipeline-config" in k]) - before
    assert config_reads == 1, f"{config_reads} config reads for 5 requests"
    assert s3.calls == 1, f"{s3.calls} master fetches for 5 requests"


def test_the_TTL_is_actually_consulted():
    """Proves the cache above is what makes the counts drop, and not some
    other coincidence: set the window to zero and every request re-derives.

    A control round taught this its own lesson. The condition first read
    `if hit and _SPEC_TTL_SECONDS and now - hit[0] < _SPEC_TTL_SECONDS`, and
    deleting the middle term changed nothing — with a window of zero the
    arithmetic already misses. The redundant term went, rather than a
    contrived test arriving to defend it.
    """
    ddb, s3 = FakeDdb(), FakeS3()
    original = mod._SPEC_TTL_SECONDS
    mod._SPEC_TTL_SECONDS = 0
    try:
        for _ in range(3):
            _get("/stock/A1", ddb=ddb, s3=s3)
    finally:
        mod._SPEC_TTL_SECONDS = original
    assert s3.calls == 3, "a zero TTL must re-derive the spec every time"


def test_an_EXPIRED_spec_is_re_fetched():
    """Bounded staleness, not permanent: enabling an entity or adding a
    property has to take effect without a redeploy."""
    ddb, s3 = FakeDdb(), FakeS3()
    _get("/stock/A1", ddb=ddb, s3=s3)
    assert s3.calls == 1
    stamp, spec = mod._spec_cache["stock"]
    mod._spec_cache["stock"] = (stamp - mod._SPEC_TTL_SECONDS - 1, spec)
    _get("/stock/A1", ddb=ddb, s3=s3)
    assert s3.calls == 2


def test_injected_clients_NEVER_reach_boto3():
    """The memoised default must not make the fakes optional. If boto3 were
    touched here the suite would need credentials and a region."""
    _get("/stock/A1")
    assert mod._clients == {}, "a fully injected call built a real client"


def test_the_clients_are_built_ONCE_per_sandbox():
    """Three clients per request was the single largest cost measured."""
    built = []

    class FakeBoto3:
        @staticmethod
        def client(name):
            built.append(name)
            return f"client-{name}"

    import sys
    sys.modules["boto3"] = FakeBoto3
    try:
        for _ in range(4):
            mod.clients()
    finally:
        del sys.modules["boto3"]
    assert built == ["dynamodb", "s3", "secretsmanager"], built


# ── the collection read is BOUNDED ─────────────────────────────────────────

def test_the_envelope_matches_the_CONTRACT_and_the_product_API():
    """Shape pinned to the document consumers integrate against, and the
    product API declares the same two keys — so paging one gold entity API
    teaches you all of them."""
    r = _get("/stock/A1")
    page = CONTRACT["components"]["schemas"]["StockPage"]
    assert set(r["json"]) == set(page["properties"]) == {"items", "nextCursor"}
    assert set(page["required"]) == {"items", "nextCursor"}, (
        "nextCursor must be REQUIRED and explicitly null on the last page "
        "(N12), not omitted")


def test_a_request_issues_EXACTLY_ONE_query_however_big_the_partition():
    """THE BOUND. The drain loop this replaced issued a Query per page until
    the partition was exhausted, so one request's work grew with the data —
    which capabilities/api/guidelines.md forbids in as many words.

    Twelve rows and a page of 5: one Query, five items, a cursor. Not three
    Queries, and not twelve items.
    """
    rows = [_stored(org=f"{n:03d}", etag=f"e{n}") for n in range(12)]
    ddb = FakeDdb(items=rows)
    r = _get("/stock/A1", ddb=ddb, qs={"pageSize": "5"})
    assert len(ddb.queries) == 1, (
        f"{len(ddb.queries)} queries for one request — the read is unbounded")
    assert ddb.queries[0]["Limit"] == 5
    assert len(r["json"]["items"]) == 5
    assert r["json"]["nextCursor"], "more rows remain, so a cursor is owed"


def test_a_page_is_bounded_even_when_the_caller_asks_for_nothing():
    """The default has to be a real bound, not unlimited. Otherwise every
    consumer that omits pageSize gets the unbounded read back."""
    ddb = FakeDdb(items=[_stored(org=f"{n:03d}") for n in range(200)])
    r = _get("/stock/A1", ddb=ddb)
    assert ddb.queries[0]["Limit"] == mod.PAGE_SIZE_DEFAULT
    assert len(r["json"]["items"]) == mod.PAGE_SIZE_DEFAULT
    assert mod.PAGE_SIZE_DEFAULT <= mod.PAGE_SIZE_MAX <= 1000


def test_the_cursor_walks_the_WHOLE_partition_exactly_once():
    """Follow the cursor to exhaustion: every row once, no repeats, no gaps.
    The property that makes paging correct rather than merely bounded."""
    rows = [_stored(org=f"{n:03d}", etag=f"e{n}") for n in range(12)]
    ddb = FakeDdb(items=rows)
    seen, cursor, pages = [], None, 0
    while True:
        qs = {"pageSize": "5", **({"cursor": cursor} if cursor else {})}
        r = _get("/stock/A1", ddb=ddb, qs=qs)
        assert r["statusCode"] == 200
        seen += [i["organizationCode"] for i in r["json"]["items"]]
        cursor = r["json"]["nextCursor"]
        pages += 1
        if not cursor:
            break
        assert pages < 10, "the cursor never terminated"
    assert seen == sorted(f"{n:03d}" for n in range(12))
    assert pages == 3


def test_the_cursor_is_OPAQUE_and_names_no_store():
    """A consumer must not be able to read DynamoDB out of the token, because
    a different store has to be able to issue it later without a contract
    change. It is still only a key we just served, so it is not a secret."""
    ddb = FakeDdb(items=[_stored(org=f"{n:03d}") for n in range(4)])
    tok = _get("/stock/A1", ddb=ddb, qs={"pageSize": "2"})["json"]["nextCursor"]
    for leak in ("pk", "sk", "stock#A1", "{", '"', "LastEvaluatedKey"):
        assert leak not in tok, f"the cursor exposes {leak!r}"
    assert mod.decode_cursor(tok, {"S": "stock#A1"})["sk"]["S"] == "001"


def test_an_EMPTY_cursor_means_the_first_page_not_a_bad_one():
    """`?cursor=` is what a consumer's own URL template renders when it has no
    previous cursor, and it unambiguously means the first page. 400 there
    would fail the one request whose intent is never in doubt — and an empty
    `?pageSize=` already defaults for the same reason."""
    ddb = FakeDdb(items=[_stored(org="01E")])
    r = _get("/stock/A1", ddb=ddb, qs={"cursor": ""})
    assert r["statusCode"] == 200
    assert [i["organizationCode"] for i in r["json"]["items"]] == ["01E"]
    assert "ExclusiveStartKey" not in ddb.queries[0]


@pytest.mark.parametrize("bad", [
    "not-base64-$$$", "eyJwayI6", "*" * 8,
    # valid base64url, but not a key for this table
    base64.urlsafe_b64encode(b'{"hello":"world"}').decode().rstrip("="),
    base64.urlsafe_b64encode(b'[1,2,3]').decode().rstrip("="),
])
def test_a_TAMPERED_cursor_is_400_not_500(bad):
    """Untrusted input. Handing a malformed start key to DynamoDB turns a
    caller's mistake into our 5xx, which is an SLA event rather than a 400."""
    ddb = FakeDdb(items=[_stored()])
    r = _get("/stock/A1", ddb=ddb, qs={"cursor": bad})
    assert r["statusCode"] == 400, f"{bad!r} produced {r['statusCode']}"
    assert ddb.queries == [], "a bad cursor still reached DynamoDB"


@pytest.mark.parametrize("junk", ["!!!", "$", "@@", " ", "\t"])
def test_junk_INSIDE_a_valid_cursor_is_refused_not_stripped(junk):
    """base64 decoding DISCARDS characters outside its alphabet unless asked
    to validate, so junk spliced into a real token decodes back to that token
    and the tampered cursor is accepted as if it were clean.

    The read it performs is the same, so this is not a data leak — it is a
    forward-compatibility one, and it is the reason the whole token is opaque.
    A consumer whose client corrupts cursors finds out here, not on the day
    the encoding changes underneath it and every one of its stored tokens
    stops working at once.

    A control round caught this: the malformed-token cases above all fail at
    json.loads whether validation is on or off, so none of them tested it.
    """
    ddb = FakeDdb(items=[_stored(org=f"{n:03d}") for n in range(4)])
    good = _get("/stock/A1", ddb=ddb, qs={"pageSize": "2"})["json"]["nextCursor"]
    assert _get("/stock/A1", ddb=ddb, qs={"cursor": good})["statusCode"] == 200
    tampered = good[:4] + junk + good[4:]
    r = _get("/stock/A1", ddb=ddb, qs={"cursor": tampered})
    assert r["statusCode"] == 400, f"{tampered!r} was accepted"


def test_a_cursor_from_ANOTHER_product_is_400():
    """DynamoDB rejects a start key outside the query's boundaries with a
    ValidationException — a 500 for what is plainly a caller error. So the
    partition is checked against the path before the Query is built."""
    foreign = mod.encode_cursor({"pk": {"S": "stock#OTHER"}, "sk": {"S": "01E"}})
    ddb = FakeDdb(items=[_stored()])
    r = _get("/stock/A1", ddb=ddb, qs={"cursor": foreign})
    assert r["statusCode"] == 400
    assert "different collection" in r["json"]["error"]
    assert ddb.queries == []


@pytest.mark.parametrize("bad", ["0", "-1", "101", "abc", "1.5", "1e3"])
def test_an_out_of_range_pageSize_is_400(bad):
    ddb = FakeDdb()
    r = _get("/stock/A1", ddb=ddb, qs={"pageSize": bad})
    assert r["statusCode"] == 400, f"pageSize={bad!r} was accepted"
    assert ddb.queries == []


def test_the_contract_declares_the_SAME_bounds_the_code_enforces():
    """A published bound the engine does not honour is worse than none — the
    consumer sizes its own buffers on the document."""
    params = CONTRACT["components"]["parameters"]
    schema = params["PageSize"]["schema"]
    assert schema["default"] == mod.PAGE_SIZE_DEFAULT
    assert schema["maximum"] == mod.PAGE_SIZE_MAX
    assert schema["minimum"] == 1
    coll = CONTRACT["paths"]["/stock/{productId}"]["get"]
    names = {p.get("$ref", "").rsplit("/", 1)[-1] for p in coll["parameters"]}
    assert {"PageSize", "Cursor"} <= names
    assert "400" in coll["responses"], "a 400 is reachable, so declare it"


def test_an_empty_page_BEHIND_a_cursor_is_200_not_404():
    """The 404 says 'no such collection'. Once a cursor has proved the
    partition exists, an empty page means the end of it — answering 404 there
    would tell a paging consumer the product had vanished mid-walk."""
    end = mod.encode_cursor({"pk": {"S": "stock#A1"}, "sk": {"S": "zzz"}})
    r = _get("/stock/A1", ddb=FakeDdb(items=[_stored(org="01E")]),
             qs={"cursor": end})
    assert r["statusCode"] == 200
    assert r["json"] == {"items": [], "nextCursor": None}


def test_an_empty_collection_is_404():
    """Owner decision 2026-09-24. The serving copy holds only stock rows, so
    it cannot tell an unknown product from one stocked nowhere; 404 answers
    both honestly rather than asserting the product exists."""
    r = _get("/stock/NOTHING", ddb=FakeDdb(items=[]))
    assert r["statusCode"] == 404


def test_the_collection_etag_moves_when_a_MEMBER_changes():
    a = FakeDdb(items=[_stored(org="01E", etag="e1"), _stored(org="07B", etag="e2")])
    b = FakeDdb(items=[_stored(org="01E", etag="e1"), _stored(org="07B", etag="CHANGED")])
    assert _get("/stock/A1", ddb=a)["headers"]["etag"] != \
           _get("/stock/A1", ddb=b)["headers"]["etag"]


def test_the_collection_etag_moves_when_MEMBERSHIP_changes():
    a = FakeDdb(items=[_stored(org="01E", etag="e1"), _stored(org="07B", etag="e2")])
    b = FakeDdb(items=[_stored(org="01E", etag="e1")])
    assert _get("/stock/A1", ddb=a)["headers"]["etag"] != \
           _get("/stock/A1", ddb=b)["headers"]["etag"]


def test_the_collection_etag_is_STABLE_against_query_order():
    a = FakeDdb(items=[_stored(org="01E", etag="e1"), _stored(org="07B", etag="e2")])
    b = FakeDdb(items=[_stored(org="07B", etag="e2"), _stored(org="01E", etag="e1")])
    assert _get("/stock/A1", ddb=a)["headers"]["etag"] == \
           _get("/stock/A1", ddb=b)["headers"]["etag"]


def test_the_page_etag_covers_the_CURSOR_not_only_the_members():
    """Delete the one row just past a page boundary and that page's members
    are untouched while its cursor turns null. An ETag over members alone
    would 304 the consumer into following a cursor that no longer exists."""
    three = FakeDdb(items=[_stored(org="01E", etag="e1"),
                           _stored(org="07B", etag="e2"),
                           _stored(org="09C", etag="e3")])
    two = FakeDdb(items=[_stored(org="01E", etag="e1"),
                         _stored(org="07B", etag="e2")])
    a = _get("/stock/A1", ddb=three, qs={"pageSize": "2"})
    b = _get("/stock/A1", ddb=two, qs={"pageSize": "2"})
    assert a["json"]["items"] == b["json"]["items"], "same members, by design"
    assert a["json"]["nextCursor"] and b["json"]["nextCursor"] is None
    assert a["headers"]["etag"] != b["headers"]["etag"]


def test_if_none_match_on_a_PAGE_returns_304():
    ddb = FakeDdb(items=[_stored(org="01E"), _stored(org="07B")])
    first = _get("/stock/A1", ddb=ddb)
    again = _get("/stock/A1", ddb=ddb,
                 headers={"if-none-match": first["headers"]["etag"]})
    assert again["statusCode"] == 304
    assert again["body"] == ""


# ── the contract's own shape ───────────────────────────────────────────────

def test_the_response_is_EXACTLY_the_contracts_properties():
    """The projection declares additionalProperties: false, so one extra key
    makes the response invalid against the schema the contract $refs."""
    r = _get("/stock/A1/organization/01E")
    assert set(r["json"]) == set(API_PROJECTION["properties"])


def test_no_framework_attribute_reaches_a_consumer():
    r = _get("/stock/A1/organization/01E")
    for leaked in ("pk", "sk", "syncTag", "syncedAt", "etag", "goldUpdatedAt"):
        assert leaked not in r["json"], f"{leaked} leaked into the response"


def test_a_zero_channel_property_is_NEVER_returned():
    """THE N7 guard. product's inventoryItemId, itemCatalogGroupId and
    organizationId are Oracle surrogates carrying `x-channels: []`, and they
    ARE materialized — product stores 63 properties and exposes 60. So the
    surrogate here is materialized too: if it were only in the master, this
    test would pass because it is not stored, proving nothing about
    x-channels. A mutation round caught exactly that.
    """
    master = json.loads(json.dumps(MASTER))
    master["properties"]["organizationId"] = {"type": ["integer", "null"],
                                              "x-channels": []}
    ddb = FakeDdb(extra_props=("organizationId",))
    ddb.items[0]["organizationId"] = {"N": "207"}
    r = _get("/stock/A1/organization/01E", ddb=ddb, s3=FakeS3(master=master))
    assert r["statusCode"] == 200
    assert "organizationId" not in r["json"], "an N7 surrogate was exposed"
    # and the real properties still came through, so this is not passing
    # because the projection collapsed to nothing
    assert set(r["json"]) == set(API_PROJECTION["properties"])


def test_a_property_with_NO_x_channels_is_also_withheld():
    """Absent must behave like []: a property opts IN to exposure, never out.
    Materialized here for the same reason as the test above."""
    master = json.loads(json.dumps(MASTER))
    master["properties"]["mysteryColumn"] = {"type": ["string", "null"]}
    ddb = FakeDdb(extra_props=("mysteryColumn",))
    ddb.items[0]["mysteryColumn"] = {"S": "x"}
    r = _get("/stock/A1/organization/01E", ddb=ddb, s3=FakeS3(master=master))
    assert "mysteryColumn" not in r["json"]
    assert set(r["json"]) == set(API_PROJECTION["properties"])


def test_nulls_are_PRESENT_as_null_not_omitted():
    """N12 at the API surface: the contract marks all six required, so a
    cleared value must arrive as null rather than vanish."""
    r = _get("/stock/A1/organization/01E")
    assert r["json"]["availableToPromiseTimestamp"] is None
    assert r["json"]["isbn13"] is None
    assert set(API_PROJECTION["required"]) <= set(r["json"])


def test_a_quantity_is_a_NUMBER_not_a_string():
    """DynamoDB stores N as a string; the contract declares an integer."""
    r = _get("/stock/A1/organization/01E")
    assert r["json"]["onHandQuantity"] == 644
    assert isinstance(r["json"]["onHandQuantity"], int)


# ── entities it will not serve ─────────────────────────────────────────────

def test_an_entity_with_no_config_is_404():
    r = _get("/stock/A1", ddb=FakeDdb(config=False))
    assert r["statusCode"] == 404
    assert r["json"] == {"error": "unknown entity"}


def test_an_entity_that_never_opted_in_is_404():
    r = _get("/stock/A1", ddb=FakeDdb(served=False))
    assert r["statusCode"] == 404


@pytest.mark.parametrize("path", ["/", "/stock", "/stock/A1/organization",
                                  "/stock/A1/organization/01E/extra"])
def test_a_path_of_the_wrong_shape_is_404(path):
    assert _get(path)["statusCode"] == 404


# ── an entity addressed by a BUSINESS key, not its merge key (UIFPE-687) ────
#
# Program is the case and the reason is structural, not preference: only its
# driving Silver table carries SEGMENT1, so four of its six sources cannot
# express programId, and validate_key_mappings refuses a gold PK that a
# source cannot project. The gold table therefore merges on the Oracle pair
# while the API must address rows by programId alone.

def _program_config_item():
    """Two key columns for the merge, ONE for the surface."""
    return {
        "gold_id": {"S": "program"},
        "target": {"M": {
            "database": {"S": "uif_echo_{env}_gold_program"},
            "table": {"S": "program"},
            "primary_key_cols": {"L": [{"S": "inventoryItemId"},
                                       {"S": "organizationId"}]},
            "entity_key_cols": {"L": [{"S": "programId"}]}}},
        "materialized_properties": {"L": [{"S": p} for p in (
            "inventoryItemId", "organizationId", "programId", "description")]},
        "schema": {"M": {
            "version": {"S": "v1"},
            "master_s3_path": {
                "S": "s3://b/schemas/gold/program/v1/master.json"}}},
        "serving": {"M": {"enabled": {"BOOL": True}}},
    }


def _program_stored(program_id="P200000016489", item_id="8675309"):
    """Keyed on the merge pair, carrying the index attribute."""
    return {
        "pk": {"S": f"program#{item_id}"},
        "sk": {"S": "82"},
        "ek": {"S": f"program#{program_id}"},
        "eks": {"S": "-"},
        "programId": {"S": program_id},
        "description": {"S": "Official Practice for PTE Academic"},
        "inventoryItemId": {"S": item_id},
        "organizationId": {"S": "82"},
        "etag": {"S": "abc"},
    }


def _program_master():
    return {
        "properties": {
            "programId": {"type": ["string", "null"], "x-channels": ["api"]},
            "description": {"type": ["string", "null"], "x-channels": ["api"]},
            # The merge key is stored and carried by no channel, as today.
            "inventoryItemId": {"type": ["string", "null"], "x-channels": []},
            "organizationId": {"type": ["string", "null"], "x-channels": []},
        }
    }


def _program_event(path, qs=None):
    # HDR/SECRET, as the shared helper does: without the gateway header every
    # one of these is a 403 and proves nothing about the read path.
    return {"rawPath": path, "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": qs, "headers": {HDR: SECRET}}


def test_an_indexed_entity_is_read_through_THE_INDEX():
    """The whole point of the decoupling: /program/{programId} resolves even
    though programId is neither the partition key nor the sort key.
    """
    ddb = FakeDdb(items=[_program_stored()],
                  config_item=_program_config_item())
    got = mod.handler(_program_event("/program/P200000016489"),
                      ddb=ddb, s3=FakeS3(master=_program_master()),
                      secrets=FakeSecrets())
    assert got["statusCode"] == 200, got
    assert json.loads(got["body"])["programId"] == "P200000016489"
    idx = [q for q in ddb.queries if q.get("IndexName")]
    assert len(idx) == 1, ddb.queries
    assert idx[0]["IndexName"] == mod.INDEX_NAME
    assert idx[0]["ExpressionAttributeValues"][":e"] == {
        "S": "program#P200000016489"}


def test_the_merge_key_is_NEVER_returned_by_an_indexed_entity():
    """inventoryItemId and organizationId are Oracle surrogates on
    `x-channels: []`. Reading through an index must not become a way to leak
    them — the projection is the same one every other read uses.
    """
    ddb = FakeDdb(items=[_program_stored()],
                  config_item=_program_config_item())
    got = mod.handler(_program_event("/program/P200000016489"),
                      ddb=ddb, s3=FakeS3(master=_program_master()),
                      secrets=FakeSecrets())
    body = json.loads(got["body"])
    assert sorted(body) == ["description", "programId"]


def test_a_DUPLICATE_business_key_is_a_500_not_a_silent_pick(capsys):
    """A secondary index does not enforce uniqueness — that is the price of
    this design and it has to be visible. Returning whichever row came back
    first would serve one of two answers forever, and say nothing.
    """
    ddb = FakeDdb(
        items=[_program_stored(item_id="1"), _program_stored(item_id="2")],
        config_item=_program_config_item())
    got = mod.handler(_program_event("/program/P200000016489"),
                      ddb=ddb, s3=FakeS3(master=_program_master()),
                      secrets=FakeSecrets())
    assert got["statusCode"] == 500
    idx = [q for q in ddb.queries if q.get("IndexName")][0]
    assert idx["Limit"] == 2, "Limit 2 is what makes the second row visible"
    logged = capsys.readouterr().out
    # The TOKEN, not prose: Terraform's metric filter matches on it, so this is
    # the same string the alarm depends on.
    assert "ENTITY_KEY_NOT_UNIQUE" in logged, logged
    assert "P200000016489" in logged, "the log must name the offending key"


def test_an_indexed_entity_has_NO_sub_resource():
    """The merge key is storage. /program/{id}/organization/82 must not become
    a back door to the pair the surface deliberately does not expose.
    """
    ddb = FakeDdb(items=[_program_stored()],
                  config_item=_program_config_item())
    got = mod.handler(
        _program_event("/program/P200000016489/organization/82"),
        ddb=ddb, s3=FakeS3(master=_program_master()), secrets=FakeSecrets())
    assert got["statusCode"] == 404


def test_an_indexed_entity_that_misses_is_a_404():
    ddb = FakeDdb(items=[_program_stored()],
                  config_item=_program_config_item())
    got = mod.handler(_program_event("/program/P299999999999"),
                      ddb=ddb, s3=FakeS3(master=_program_master()),
                      secrets=FakeSecrets())
    assert got["statusCode"] == 404


def test_the_index_NAME_matches_the_terraform():
    """The Lambda holds the index name as a literal because it has no
    Terraform outputs to read. A rename on one side alone is an AccessDenied
    at best and a 404 on every lookup at worst.
    """
    tf = _tf("serving.tf")
    block = tf.split("global_secondary_index {", 1)[1].split("}", 1)[0]
    declared = [ln.split("=", 1)[1].strip().strip('"')
                for ln in block.splitlines() if ln.strip().startswith("name")]
    assert declared == [mod.INDEX_NAME], (
        f"serving.tf declares {declared}, the Lambda uses {mod.INDEX_NAME!r}")
    assert '"ek"' in block, "the index must be partitioned on ek"


def test_the_index_SORT_KEY_matches_the_terraform():
    """The reader pins `eks` in every key condition. If Terraform declared the
    index hash-only, an item read would fail its ValidationException and a
    collection read would silently return the whole partition — so the two
    sides are asserted against each other rather than each reading fine alone.
    """
    tf = _tf("serving.tf")
    block = tf.split("global_secondary_index {", 1)[1].split("}", 1)[0]
    declared = [ln.split("=", 1)[1].strip().strip('"')
                for ln in block.splitlines()
                if ln.strip().startswith("range_key")]
    assert declared == [mod.EKS], (
        f"serving.tf declares range_key {declared}, the Lambda uses "
        f"{mod.EKS!r}")
    assert f'name = "{mod.EKS}"' in tf, (
        f"{mod.EKS} is a key attribute, so the table must declare it")


def test_the_retrieval_role_can_query_THE_INDEX_arn():
    """The bug this nearly shipped with. A Query against a secondary index is
    authorised on the INDEX arn; the table arn does not cover it. Simulated
    against the live policy shape, a table-only grant was implicitDeny on the
    index while every merge-key read carried on working — so no existing test
    would have caught it.
    """
    tf = _tf("serving_api.tf")
    stmt = tf.split('Sid    = "ReadTheServingCopy"', 1)[1].split("},", 1)[0]
    assert "/index/*" in stmt, (
        "the retrieval role is granted the table arn only — every "
        "business-key lookup will fail with AccessDeniedException")


def test_an_UNINDEXED_entity_never_touches_the_index():
    """The control, and the half that matters most. Four of the five entities
    are unindexed, and a passing indexed test says nothing about them: if
    `indexed` were ever true by accident, every one of those reads would move
    to an eventually-consistent index query and start paying GSI writes.

    Asserting the ABSENCE is the only way to see that.
    """
    ddb = FakeDdb()
    r = _get("/stock/A1/organization/01E", ddb=ddb)
    assert r["statusCode"] == 200, r
    assert not [q for q in ddb.queries if q.get("IndexName")], \
        "an unindexed entity issued an index query"
    assert ddb.gets, "an unindexed entity must still resolve by GetItem"


# ── a TWO-COLUMN business key through the index (UIFPE-709) ─────
#
# The index path was written for a one-column key. index_key() wrote every
# column of entity_keys into the value while the API queried only the first,
# so a two-column key wrote a value nothing could match. Product needs it: it
# merges on the Oracle surrogate pair, and productId alone is not unique
# across organizations.


def _product_config_item():
    return {
        "gold_id": {"S": "product"},
        "target": {"M": {
            "database": {"S": "uif_echo_{env}_gold_product"},
            "table": {"S": "product"},
            "primary_key_cols": {"L": [{"S": "inventoryItemId"},
                                       {"S": "organizationId"}]},
            "entity_key_cols": {"L": [{"S": "productId"},
                                      {"S": "organizationCode"}]}}},
        "materialized_properties": {"L": [{"S": p} for p in (
            "inventoryItemId", "organizationId", "productId",
            "organizationCode", "description")]},
        "schema": {"M": {
            "version": {"S": "v1"},
            "master_s3_path": {
                "S": "s3://b/schemas/gold/product/v1/master.json"}}},
        "serving": {"M": {"enabled": {"BOOL": True}}},
    }


def _product_stored(product_id="A100000195802", org_code="01E",
                    item_id="532352", org_id="181"):
    """Keyed on the merge pair, indexed on the business pair."""
    return {
        "pk": {"S": f"product#{item_id}"},
        "sk": {"S": org_id},
        "ek": {"S": f"product#{product_id}"},
        "eks": {"S": org_code},
        "productId": {"S": product_id},
        "organizationCode": {"S": org_code},
        "description": {"S": "Campbell Biology"},
        "inventoryItemId": {"S": item_id},
        "organizationId": {"S": org_id},
        "etag": {"S": "p-etag"},
    }


def _product_master():
    return {
        "properties": {
            "productId": {"type": ["string", "null"], "x-channels": ["api"]},
            "organizationCode": {"type": ["string", "null"],
                                 "x-channels": ["api"]},
            "description": {"type": ["string", "null"], "x-channels": ["api"]},
            "inventoryItemId": {"type": ["string", "null"], "x-channels": []},
            "organizationId": {"type": ["string", "null"], "x-channels": []},
        }
    }


def _product_ddb(items=None):
    return FakeDdb(items=items if items is not None else [_product_stored()],
                   config_item=_product_config_item())


def _product_call(path, ddb):
    """A Function URL delivers the query string SEPARATELY from rawPath, so a
    helper that leaves it on the path makes the last segment carry it and
    every lookup miss for the wrong reason.
    """
    raw, _, query = path.partition("?")
    qs = dict(kv.split("=", 1) for kv in query.split("&")) if query else None
    return mod.handler(
        {"rawPath": raw, "requestContext": {"http": {"method": "GET"}},
         "queryStringParameters": qs, "headers": {HDR: SECRET}},
        ddb=ddb, s3=FakeS3(master=_product_master()), secrets=FakeSecrets())


def _product_get(path, items=None):
    return _product_call(path, _product_ddb(items))


def test_a_TWO_COLUMN_business_key_resolves_through_the_index():
    ddb = _product_ddb()
    got = _product_call("/product/A100000195802/organization/01E", ddb)
    assert got["statusCode"] == 200, got
    body = json.loads(got["body"])
    assert body["productId"] == "A100000195802"
    assert body["organizationCode"] == "01E"
    idx = [q for q in ddb.queries if q.get("IndexName")]
    assert len(idx) == 1, ddb.queries
    assert idx[0]["ExpressionAttributeValues"] == {
        ":e": {"S": "product#A100000195802"}, ":s": {"S": "01E"}}
    assert "AND" in idx[0]["KeyConditionExpression"], (
        "an item read must pin the sort key too, or it fetches the whole "
        "partition to return one row")


def test_the_QUERIED_index_value_is_THE_ONE_THE_WRITER_WROTE():
    """The control for this change, and what fails on the old code: the API
    queried `product#{productId}` while the writer wrote
    `product#{productId}#{organizationCode}`. Pinning read against write is
    what makes that class of drift fail here instead of in dev.
    """
    import gold_serving as gs

    stored = _product_stored()
    ddb = _product_ddb([stored])
    _product_call("/product/A100000195802/organization/01E", ddb)
    q = next(q for q in ddb.queries if q.get("IndexName"))
    queried = (q["ExpressionAttributeValues"][":e"]["S"],
               q["ExpressionAttributeValues"][":s"]["S"])
    spec = gs.ServingSpec.from_config(
        {"gold_id": "product",
         "target": {"database": "uif_echo_{env}_gold_product",
                    "table": "product",
                    "primary_key_cols": ["inventoryItemId", "organizationId"],
                    "entity_key_cols": ["productId", "organizationCode"]},
         "materialized_properties": ["inventoryItemId", "organizationId",
                                     "productId", "organizationCode",
                                     "description"],
         "serving": {"enabled": True}},
        "dev", master=_product_master(), channel="api")
    written = gs.index_key(spec, lambda k: stored[k]["S"])
    assert queried == written, (queried, written)
    assert written == (stored["ek"]["S"], stored["eks"]["S"]), (
        "the fixture must store what the writer would write")


def test_the_COLLECTION_read_pages_through_the_index():
    """/product/{productId} is every organization that product exists in. It
    cannot come from the base table: that partition is the merge key, which
    groups by a surrogate the caller has no way to know.
    """
    ddb = _product_ddb([_product_stored(org_code="01E", item_id="1"),
                        _product_stored(org_code="01G", item_id="2"),
                        _product_stored(org_code="02A", item_id="3")])
    got = _product_call("/product/A100000195802", ddb)
    assert got["statusCode"] == 200, got
    body = json.loads(got["body"])
    assert [i["organizationCode"] for i in body["items"]] == ["01E", "01G",
                                                              "02A"]
    assert body["nextCursor"] is None
    q = next(q for q in ddb.queries if q.get("IndexName"))
    assert ":s" not in q["ExpressionAttributeValues"], (
        "a collection read must NOT pin the sort key")


def test_the_collection_read_of_an_UNINDEXED_entity_still_uses_the_table():
    """The control. Stock's business key IS its merge key, so its own
    partition already groups by productId and an index query would be a
    needless eventually-consistent read.
    """
    ddb = FakeDdb()
    r = _get("/stock/A1", ddb=ddb)
    assert r["statusCode"] == 200, r
    assert not [q for q in ddb.queries if q.get("IndexName")]


def test_the_collection_CURSOR_carries_both_key_pairs():
    """An index page's LastEvaluatedKey holds the index pair and the table
    pair. A cursor validator expecting only the table's would 400 every
    follow-up page.
    """
    items = [_product_stored(org_code=c, item_id=str(n))
             for n, c in enumerate(["01E", "01G", "02A"], start=1)]
    ddb = _product_ddb(items)
    first = _product_call("/product/A100000195802?pageSize=2", ddb)
    body = json.loads(first["body"])
    assert [i["organizationCode"] for i in body["items"]] == ["01E", "01G"]
    token = body["nextCursor"]
    assert token, "a short page must carry a cursor"
    nxt = json.loads(_product_call(
        f"/product/A100000195802?cursor={token}", _product_ddb(items))["body"])
    assert [i["organizationCode"] for i in nxt["items"]] == ["02A"]
    assert nxt["nextCursor"] is None


def test_a_cursor_from_ANOTHER_product_is_refused():
    """The guard that already existed for the table path has to hold on the
    index path too, or a foreign start key reaches DynamoDB and the caller's
    mistake returns 500.
    """
    items = [_product_stored(org_code=c, item_id=str(n))
             for n, c in enumerate(["01E", "01G", "02A"], start=1)]
    token = json.loads(_product_call(
        "/product/A100000195802?pageSize=2", _product_ddb(items))["body"]
    )["nextCursor"]
    got = _product_call(f"/product/A999999999999?cursor={token}",
                        _product_ddb(items))
    assert got["statusCode"] == 400
    assert "different collection" in json.loads(got["body"])["error"]


def test_the_second_segment_must_NAME_the_business_key_column():
    got = _product_get("/product/A100000195802/org-unit/01E")
    assert got["statusCode"] == 404
    assert json.loads(got["body"])["error"] == "unknown path"


def test_a_SURROGATE_in_the_second_segment_does_not_resolve():
    """organizationId is the merge key's column and is on no channel. The
    index is built from organizationCode, so passing the surrogate misses
    rather than becoming a back door to the pair the surface hides.
    """
    got = _product_get("/product/A100000195802/organization/181")
    assert got["statusCode"] == 404
    assert json.loads(got["body"])["error"] == "not found"


def test_an_indexed_entity_with_TWO_columns_hides_its_merge_key():
    got = _product_get("/product/A100000195802/organization/01E")
    body = json.loads(got["body"])
    assert sorted(body) == ["description", "organizationCode", "productId"]


def test_a_DUPLICATE_two_column_key_logs_BOTH_PARTS(capsys):
    """The 500 already existed; what changes is that the log names the whole
    key. `value=A100000195802` alone names a value that is not unique by
    itself, so it would not say which pair collided.
    """
    ddb = _product_ddb([_product_stored(item_id="1"),
                        _product_stored(item_id="2")])
    got = _product_call("/product/A100000195802/organization/01E", ddb)
    assert got["statusCode"] == 500
    out = capsys.readouterr().out
    assert "ENTITY_KEY_NOT_UNIQUE" in out, out
    assert "productId+organizationCode" in out, out
    assert "A100000195802/01E" in out, out


def test_program_keeps_its_SINGLE_segment_url():
    """Program is live on /program/{programId}. Teaching the branch a second
    segment must not have turned its one-column read into a two-segment one.
    """
    ddb = FakeDdb(items=[_program_stored()],
                  config_item=_program_config_item())
    got = mod.handler(_program_event("/program/P200000016489"),
                      ddb=ddb, s3=FakeS3(master=_program_master()),
                      secrets=FakeSecrets())
    assert got["statusCode"] == 200, got
    assert next(q for q in ddb.queries if q.get("IndexName"))[
        "ExpressionAttributeValues"][":e"] == {"S": "program#P200000016489"}


# ── program's contract, and the alarm that makes its key safe (UIFPE-521) ───

PROGRAM = REPO_ROOT / "domains/program"
PROGRAM_MASTER = json.loads(
    (PROGRAM / "src/schemas/gold/program/v1/master.json").read_text(
        encoding="utf-8"))
PROGRAM_CONTRACT = yaml.safe_load(
    (REPO_ROOT / contract_path("program", "program")).read_text(
        encoding="utf-8"))
PROGRAM_PROJECTION = json.loads(
    (REPO_ROOT / committed_path("program", "program", "api", "created")
     ).read_text(
        encoding="utf-8"))
PROGRAM_PIPELINE = yaml.safe_load(
    (PROGRAM / "src/apps/pipelines/base/program.yaml").read_text(
        encoding="utf-8"))


def _api_tier(master: dict) -> set:
    return {n for n, d in (master.get("properties") or {}).items()
            if "api" in ((d or {}).get("x-channels") or [])}


def test_the_not_unique_TOKEN_matches_the_metric_filter():
    """The code prints a token; Terraform turns that token into a metric and an
    alarm. Nothing else connects them, so a reword on either side silently
    un-alarms a duplicate business key — and a RETURNED 500 raises no Lambda
    Errors metric, so there is no second net underneath.
    """
    src = (STACK_ROOT / "src/apps/lambdas/gold_serving_api.py").read_text(
        encoding="utf-8")
    assert "ENTITY_KEY_NOT_UNIQUE" in src, "the code no longer prints the token"

    tf = _tf("serving_api.tf")
    block = tf.split(
        'resource "aws_cloudwatch_log_metric_filter" '
        '"gold_serving_api_key_not_unique"', 1)[1].split("\n}\n", 1)[0]
    pattern = [ln.split("=", 1)[1].strip().strip('"')
               for ln in block.splitlines()
               if ln.strip().startswith("pattern")]
    assert pattern == ["ENTITY_KEY_NOT_UNIQUE"], (
        f"the metric filter matches {pattern}, the code prints "
        f"ENTITY_KEY_NOT_UNIQUE")


def test_the_not_unique_alarm_HAS_ACTIONS():
    """An alarm with no actions fires and notifies nobody. This stack already
    has one of those in cloudwatch.tf, which is why it is asserted rather than
    assumed.
    """
    tf = _tf("serving_api.tf")
    block = tf.split(
        'resource "aws_cloudwatch_metric_alarm" '
        '"gold_serving_api_key_not_unique"', 1)[1].split("\n}\n", 1)[0]
    assert "alarm_actions" in block, "the duplicate-key alarm notifies nobody"
    assert "UIF/Gold" in block, "the alarm reads the wrong namespace"


@pytest.mark.parametrize("domain,entity", _served(),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_a_served_contract_DERIVES_its_schema_from_the_master(domain, entity):
    """Every API schema derives from the domain master and nothing else.

    Generalised from program-by-name (UIFPE-739). Naming one entity is what let
    product restate 71 properties beside a correct generated 60 for weeks,
    flattening a nested object into twelve dotted names, with a green suite the
    whole time — the guard existed and simply did not look at it.

    Two things are asserted, because either alone passes while the contract is
    wrong: the projection still equals the master's api tier, and the contract
    POINTS at that projection rather than restating it.
    """
    master = json.loads(
        (REPO_ROOT / f"domains/{domain}/src/schemas/gold/{entity}/v1/master.json")
        .read_text(encoding="utf-8"))
    projection = json.loads(
        (REPO_ROOT / committed_path(domain, entity, "api", "created"))
        .read_text(encoding="utf-8"))
    contract = yaml.safe_load(
        (REPO_ROOT / contract_path(domain, entity)).read_text(encoding="utf-8"))

    declared, tier = set(projection.get("properties") or {}), _api_tier(master)
    assert declared == tier, (
        f"{entity}: projection has {sorted(declared - tier)} extra, "
        f"{sorted(tier - declared)} missing — regenerate it")

    want = expected_ref(domain, entity)

    def external_refs(node, depth=0):
        """Every non-local $ref reachable from a response schema.

        Follows a local `#/components/schemas/X` ref into the contract, because
        a collection read points at a page envelope and the projection sits
        INSIDE it. Not following it was a hole in this guard: a wrong ref in
        the envelope passed while the item read was correct.
        """
        out = set()
        if depth > 4 or not isinstance(node, (dict, list)):
            return out
        if isinstance(node, list):
            for x in node:
                out |= external_refs(x, depth + 1)
            return out
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                if v.startswith("#/components/schemas/"):
                    target = (contract.get("components", {})
                              .get("schemas", {}).get(v.rsplit("/", 1)[1]))
                    out |= external_refs(target, depth + 1)
                else:
                    out.add(v)
            else:
                out |= external_refs(v, depth + 1)
        return out

    external = set()
    for ops in (contract.get("paths") or {}).values():
        for verb, op in ops.items():
            if verb != "get":
                continue
            schema = (op["responses"]["200"]["content"]["application/json"]
                      ["schema"])
            external |= external_refs(schema)
    assert external, (
        f"{entity}: no operation $refs the generated projection — the contract "
        f"restates its schema, which is the drift this rule exists to prevent")
    assert external == {want}, (
        f"{entity}: contract points at {sorted(external)}, the rule puts the "
        f"projection at {want}")


def test_the_derivation_guard_COVERS_every_served_entity():
    """The guard above is parametrised, and a parametrised test over an empty
    list passes silently. This is the second half: assert the list is not
    empty and holds every entity whose config says it is served.
    """
    covered = {e for _, e in _served()}
    assert covered, "no served entity found — the guard is asserting nothing"
    assert "product" in covered, (
        "product is served and must be inside the derivation guard; it is the "
        "entity whose contract drifted while the guard named only program")
    # An EXPLICIT expected set, deliberately not recomputed from the same
    # helper `covered` comes from — that comparison is a tautology and cannot
    # fail. The filter is derived so nothing escapes it; this line is what
    # makes an arrival or a departure deliberate rather than silent.
    #
    # Adding a served entity means adding it here, in the same commit that
    # sets serving.enabled. That is the point.
    assert covered == {"product", "price", "program", "stock",
                       "marketing_copy"}, (
        f"the set of served entities changed to {sorted(covered)}. If that is "
        f"intended, update this assertion in the same commit — every "
        f"assertion parametrised over _served() now applies to the new one.")


def test_programs_contract_NEVER_exposes_the_merge_key():
    """inventoryItemId and organizationId are Oracle surrogates carrying
    `x-channels: []`. Program is served THROUGH them — they are its partition
    and sort key — so this is the entity where leaking them is most likely.
    """
    leaked = {"inventoryItemId", "organizationId"} & set(
        PROGRAM_PROJECTION.get("properties") or {})
    assert not leaked, f"the api projection exposes the merge key: {leaked}"
    assert PROGRAM_PROJECTION.get("additionalProperties") is False, (
        "additionalProperties must be false or the merge key can ride along "
        "anyway")
    # And the path must not offer them either.
    assert list(PROGRAM_CONTRACT["paths"]) == ["/program/{programId}"], (
        f"unexpected paths: {list(PROGRAM_CONTRACT['paths'])}")


def test_programs_entity_key_is_a_MATERIALIZED_property():
    """entity_key_cols names a property the serving copy must store, or the
    index attribute is never written and every lookup 404s while the items look
    fine. ServingSpec refuses this at load; asserting it here means the YAML is
    wrong before a deploy rather than after.
    """
    keys = PROGRAM_PIPELINE["target"]["entity_key_cols"]
    assert keys == ["programId"], keys
    api = _api_tier(PROGRAM_MASTER)
    assert set(keys) <= api, (
        f"{keys} is not on the api channel, so the path key cannot be returned")
    merge = PROGRAM_PIPELINE["target"]["primary_key_cols"]
    assert set(keys) != set(merge), (
        "entity_key_cols equals primary_key_cols — then the index is dead "
        "weight and `indexed` is False, so the path would not resolve")


def _served_contracts():
    """Contracts for entities the SHARED retrieval Lambda actually serves.

    product publishes no x-slo at all and is not served from the serving copy
    yet — its API port is UIFPE-519 — so holding it to this function's numbers
    would assert against an artefact this function has nothing to do with.
    Scoped by serving.enabled rather than by an exclusion list, so the day
    product is served it joins automatically.
    """
    out = []
    for domain, entity in _served():
        c = REPO_ROOT / contract_path(domain, entity)
        if c.exists():
            out.append(c)
    return out


CONTRACTS = _served_contracts()


@pytest.mark.parametrize("path", CONTRACTS,
                         ids=lambda p: p.parents[2].name)
def test_every_gold_contract_publishes_THE_SAME_slo(path):
    """ONE Lambda serves every gold entity, so the latency alarms are on the
    function, not per entity. If one contract publishes different numbers, that
    entity's promise is not the one being monitored — and the drift is invisible
    because each contract reads fine on its own.
    """
    slo = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("x-slo")
    assert slo, f"{path.parents[2].name} publishes no x-slo"
    fields = ("latencyP50Milliseconds", "latencyP99Milliseconds",
              "availabilityPercent", "freshnessHours")
    declared = {k: slo.get(k) for k in fields}
    expected = {k: CONTRACT["x-slo"][k] for k in fields}
    assert declared == expected, (
        f"{path.parents[2].name} publishes {declared}, the shared function's "
        f"alarms enforce {expected}")


# ── A declared compressed property, at the READ end (UIFPE-767) ─────────────
# The writer stores a compressed property under `<prop>Gz` as bytes and leaves
# the plain name absent, so a projection that only reads the plain name
# returns a cheerful null for content that is there. That was the state of
# this API until UIFPE-767: the write path compressed, nothing decompressed,
# and no entity declared it so nothing noticed.

import gzip as _gzip


def _compressed_item(text="<p>Marketing copy</p>", prop="marketingCopyText"):
    return {
        "pk": {"S": f"marketing_copy#{'MC1'}"}, "sk": {"S": "-"},
        "contentId": {"S": "MC1"},
        f"{prop}Gz": {"B": _gzip.compress(text.encode("utf-8"))},
        "etag": {"S": "e-MC1"},
    }


def test_a_compressed_property_comes_back_DECOMPRESSED():
    text = "<h1>A heading</h1>" + ("x" * 5000)
    out = mod.project(_compressed_item(text), ("contentId", "marketingCopyText"),
                      "marketingCopyText")
    assert out["marketingCopyText"] == text
    assert out["contentId"] == "MC1"


def test_the_Gz_attribute_itself_is_NEVER_in_the_response():
    """It is storage. Returning raw bytes would also fail json.dumps."""
    out = mod.project(_compressed_item(), ("contentId", "marketingCopyText"),
                      "marketingCopyText")
    assert "marketingCopyTextGz" not in out


def test_a_stored_NULL_for_a_compressed_property_reads_as_null():
    """The writer stores an explicit NULL on the PLAIN name when the value was
    null, so a cleared value is distinguishable from an absent attribute."""
    item = {"contentId": {"S": "MC1"}, "marketingCopyText": {"NULL": True}}
    out = mod.project(item, ("contentId", "marketingCopyText"),
                      "marketingCopyText")
    assert out["marketingCopyText"] is None


def test_neither_shape_stored_is_null_AND_SAYS_SO(capsys):
    """Same response as a cleared value, because there is nothing else honest
    to return — but the log distinguishes them, which is the difference
    between a cleared field and an item written before the declaration."""
    out = mod.project({"contentId": {"S": "MC1"}},
                      ("contentId", "marketingCopyText"), "marketingCopyText")
    assert out["marketingCopyText"] is None
    assert "GOLD_SERVING_PAYLOAD_ABSENT" in capsys.readouterr().out


def test_a_CORRUPT_payload_raises_rather_than_reading_as_null(capsys):
    """Presenting corrupt storage as a cleared value is worse than failing: a
    consumer caches it, and the ETag says nothing changed."""
    item = {"contentId": {"S": "MC1"},
            "marketingCopyTextGz": {"B": b"not gzip at all"}}
    with pytest.raises(OSError):
        mod.project(item, ("contentId", "marketingCopyText"),
                    "marketingCopyText")
    assert "GOLD_SERVING_CORRUPT_PAYLOAD" in capsys.readouterr().out


def test_an_entity_with_NO_compressed_property_is_UNCHANGED():
    """The regression that matters: four entities are served today and none
    declares one, so this path must stay exactly as it was."""
    item = _stored()
    keep = ("productId", "organizationCode", "onHandQuantity", "isbn13")
    assert mod.project(item, keep) == mod.project(item, keep, None)
    assert mod.project(item, keep)["isbn13"] is None
    assert mod.project(item, keep)["onHandQuantity"] == 644


def test_a_compressed_property_NOT_on_the_channel_is_not_decompressed():
    """`keep` is the channel projection. A property the master withholds from
    this channel must not be resurrected by the compression branch."""
    out = mod.project(_compressed_item(), ("contentId",), "marketingCopyText")
    assert out == {"contentId": "MC1"}


def test_the_WRITER_and_the_READER_agree_on_the_shape():
    """A round trip through the real builder rather than a hand-made item, so
    the two ends cannot drift on the attribute name or the encoding."""
    import gold_serving as gs  # local, as the two tests above do

    text = "Ünïcödé copy — with an em dash and 10k of padding" + ("y" * 10000)
    spec = gs.ServingSpec(
        entity="marketing_copy", gold_db="d", gold_table="t",
        key_props=("contentId",), props=("contentId", "marketingCopyText"),
        channel_props=("contentId", "marketingCopyText"),
        compress_prop="marketingCopyText",
    )
    # Keys are the LOWERCASED LOGICAL names, which is what an Athena UNLOAD
    # emits and what build_item normalises to.
    # `etag` is a column the payload query aliases from the channel hash, not
    # the raw _channel_hashes map.
    row = {"contentId": "MC1", "marketingCopyText": text, "etag": "hash-MC1"}
    item = gs.build_item(spec, row, "test-tag", compress=_gzip.compress)
    assert "marketingCopyTextGz" in item
    assert "marketingCopyText" not in item
    assert item["etag"] == {"S": "hash-MC1"}
    out = mod.project(item, spec.channel_props, spec.compress_prop)
    assert out["marketingCopyText"] == text


def test_marketing_copy_compresses_THE_DOCUMENT_not_the_description():
    """Its master declares TWO nullable string properties, and only one is the
    payload: marketingCopyContentText is the document (hundreds of kilobytes
    at the top end), marketingCopyText is a short human-readable description.

    Both are materialized properties, so the engine accepts either and would
    be perfectly happy compressing the wrong one — leaving the document stored
    raw, which is the entire reason this entity needs compression, while
    gzipping a short string for nothing. Nothing else would fail.
    """
    cfg = yaml.safe_load(
        (REPO_ROOT / "domains/product/src/apps/pipelines/base/marketing_copy.yaml")
        .read_text(encoding="utf-8"))
    assert (cfg.get("serving") or {}).get("compress_prop") == \
        "marketingCopyContentText"


def test_only_marketing_copy_declares_a_compressed_property():
    """Every other served entity is scalars. One declaring compression would
    be carrying this entity's problem for no reason, and the ceiling refusal
    would start applying to it."""
    declared = {e: (c.get("serving") or {}).get("compress_prop")
                for p, c in _pipeline_configs()
                for _, e in [_entity_of(p, c)]
                if (c.get("serving") or {}).get("compress_prop")}
    assert declared == {"marketing_copy": "marketingCopyContentText"}, declared
