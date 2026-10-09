# test_gold_serving.py
# The entity-agnostic gold -> DynamoDB serving module (UIFPE-518).
#
# Four of these tests exist because a LIVE run found the bug and a local
# harness would not have. They are marked TRAP and each names what broke:
# Athena rejects backticks, UNLOAD cannot serialize a timestamp, UNLOAD
# lowercases every column name, and CAST AS VARCHAR is not ISO-8601.

import json
from pathlib import Path

import pytest

# src/libs is on sys.path via stacks/echo/qe/conftest.py, which is the only
# place that edits it (stacks/echo/CLAUDE.md).
STACK_ROOT = Path(__file__).resolve().parents[2]

import gold_serving as gs  # noqa: E402

REPO_ROOT = STACK_ROOT.parents[1]
STOCK_MASTER = (REPO_ROOT / "domains/stock/src/schemas/gold/stock/v1/master.json")


def _cfg(**over):
    cfg = {
        "gold_id": "stock",
        "target": {
            "database": "uif_echo_{env}_gold_stock",
            "table": "stock",
            "primary_key_cols": ["productId", "organizationCode"],
        },
        "materialized_properties": [
            "availableToPromiseIndicator", "availableToPromiseTimestamp",
            "isbn13", "onHandQuantity", "organizationCode", "productId",
        ],
    }
    cfg.update(over)
    return cfg


def _master():
    return json.loads(STOCK_MASTER.read_text(encoding="utf-8"))


def _spec(**over):
    return gs.ServingSpec.from_config(_cfg(**over), env="dev", master=_master())


# ── derivation ──────────────────────────────────────────────────────────────

def test_spec_is_derived_entirely_from_config_and_master():
    s = _spec()
    assert s.gold_ref == "uif_echo_dev_gold_stock.stock"
    assert s.changes_ref == "uif_echo_dev_gold_stock.stock_changes"
    assert s.key_props == ("productId", "organizationCode")
    assert s.channel == "api"


def test_timestamp_props_come_from_the_masters_declared_format():
    """TRAP: an uncast timestamp fails the UNLOAD outright. Which properties
    need casting is declared by the master, not guessed."""
    assert _spec().timestamp_props == frozenset({"availableToPromiseTimestamp"})


def test_no_master_means_no_property_is_cast():
    s = gs.ServingSpec.from_config(_cfg(), env="dev")
    assert s.timestamp_props == frozenset()


@pytest.mark.parametrize("over,fragment", [
    ({"target": {"database": "d", "table": "t", "primary_key_cols": []}},
     "primary_key_cols is empty"),
    ({"materialized_properties": []}, "materialized_properties is empty"),
    ({"materialized_properties": ["isbn13"]}, "are not in"),
])
def test_a_half_filled_config_is_REFUSED(over, fragment):
    """Known-bad controls. A serving copy built from a half-filled config looks
    populated and is missing attributes nobody notices until a consumer asks."""
    with pytest.raises(ValueError, match=fragment):
        gs.ServingSpec.from_config(_cfg(**over), env="dev")


def test_compress_prop_must_be_a_real_property():
    # Through CONFIG, which is the only way it arrives since UIFPE-767. It was
    # a parameter nothing in production passed, which is how the compression
    # the write path implements stayed unreachable.
    with pytest.raises(ValueError, match="compress_prop"):
        _spec(serving={"enabled": True, "compress_prop": "nope"})


def test_compress_prop_is_read_from_the_serving_block():
    spec = _spec(serving={"enabled": True, "compress_prop": "isbn13"})
    assert spec.compress_prop == "isbn13"


def test_no_serving_block_means_no_compressed_property():
    assert _spec().compress_prop is None
    assert _spec(serving={"enabled": True}).compress_prop is None


def test_an_empty_compress_prop_is_the_same_as_absent():
    """A YAML key present but blank must not become the empty-string
    property, which would then fail the materialized check for a bad reason."""
    assert _spec(serving={"enabled": True, "compress_prop": ""}).compress_prop is None


# ── the derived SQL ─────────────────────────────────────────────────────────

def test_sql_uses_trino_quoting_and_NEVER_backticks():
    """TRAP: gold_cdc builds SPARK SQL, where backticks are correct. This runs
    on Athena, which is Trino and rejects a backtick at submit with
    'Queries of this type are not supported'. Same repo, two dialects."""
    s = _spec()
    for sql in (gs.payload_select(s),
                gs.unload_sql(s, "r1", "s3://b/p/"),
                gs.unload_sql(s, None, "s3://b/p/"),
                gs.deletes_sql(s, "r1")):
        assert "`" not in sql, sql
        assert '"product_id"' in sql or '"productId"' in sql


def test_timestamps_are_rendered_as_iso8601_not_a_plain_cast():
    """TRAP: CAST(ts AS VARCHAR) yields '2026-09-12 19:03:11.000000 UTC',
    which is not ISO-8601 and breaks a contract declaring format: date-time."""
    sql = gs.payload_select(_spec())
    assert 'to_iso8601(g."available_to_promise_timestamp")' in sql
    assert 'to_iso8601(g."_gold_updated_at")' in sql
    assert "CAST(" not in sql


def test_payload_selects_the_channel_hash_as_the_etag():
    sql = gs.payload_select(_spec())
    assert """g."_channel_hashes"['api'] AS "etag\"""" in sql


def test_delta_restricts_to_rows_whose_own_channel_changed():
    sql = gs.unload_sql(_spec(), "run-9", "s3://b/p/")
    assert "contains(changed_channels, 'api')" in sql
    assert "run_id = 'run-9'" in sql
    assert "UNLOAD (" in sql and "format = 'JSON'" in sql


def test_baseline_has_no_change_log_join():
    sql = gs.unload_sql(_spec(), None, "s3://b/p/")
    assert "stock_changes" not in sql
    assert "changed_channels" not in sql


def test_deletes_detects_absence_not_an_op_vocabulary():
    sql = gs.deletes_sql(_spec(), "run-9")
    assert "LEFT JOIN" in sql and "IS NULL" in sql
    assert "op" not in sql.split("WHERE")[-1]
    for k in ("product_id", "organization_code"):
        assert f'c."{k}"' in sql


def test_delete_keys_are_the_SHARED_key_not_the_logical_columns():
    """deletes_sql returns logical key COLUMNS, but the table is keyed pk/sk.
    Handing those columns to a DeleteRequest is a ValidationException at
    runtime, so nothing would ever be deleted from the serving copy.

    Added because a mutation reverting this to logical keys SURVIVED the
    suite - the delete path's key shape was never asserted anywhere.
    """
    class Results:
        @staticmethod
        def get_query_results(**kw):
            # The first page carries a header row; later pages do not.
            return {"ResultSet": {"Rows": [
                {"Data": [{"VarCharValue": "productId"},
                          {"VarCharValue": "organizationCode"}]},
                {"Data": [{"VarCharValue": "A1"},
                          {"VarCharValue": "01E"}]},
            ]}}

    keys = gs.fetch_delete_keys(Results(), _spec(), "q1")
    assert keys == [{"pk": {"S": "stock#A1"}, "sk": {"S": "01E"}}]


# ── build_item ──────────────────────────────────────────────────────────────

def _unload_row():
    """Exactly what Athena's UNLOAD-to-JSON produces: keys LOWERCASED, values
    typed. Taken from a real probe against uif_echo_dev_gold_stock.stock."""
    return {
        "availabletopromiseindicator": True,
        "availabletopromisetimestamp": None,
        "isbn13": "9780602575274",
        "onhandquantity": 38,
        "organizationcode": "01E",
        "productid": "A100000195802",
        "etag": "20eaaa1031987058c0d31ae3a3b69fbc9dff445a3952c8dc22d0223ecb36c686",
        "updated": "2026-09-12T19:03:11.000000Z",
    }


def test_TRAP_unload_lowercases_every_column_name():
    """The camelCase aliases in the payload query do NOT survive into the
    UNLOAD file. Looking up the logical name directly finds nothing, every key
    reads as missing, and every row is skipped - silently, because 'no key' is
    warn-and-continue. This asserts the normalisation that prevents it."""
    item = gs.build_item(_spec(), _unload_row(), "tag")
    assert item is not None, "every row would have been skipped"
    assert item["productId"] == {"S": "A100000195802"}
    assert item["organizationCode"] == {"S": "01E"}


def test_a_camelCase_row_is_read_too():
    """The other direction, and it needs its own case: a row from
    get-query-results (or any non-UNLOAD source) arrives in the payload
    query's own camelCase. Normalising the ROW's keys is what covers it -
    lowercasing only the lookup would leave this source unreadable.

    Added because a mutation removing the row-key normalisation was MISSED by
    the test above, which only ever feeds lowercase rows.
    """
    row = {"productId": "A1", "organizationCode": "01E", "isbn13": None,
           "onHandQuantity": 7, "availableToPromiseIndicator": False,
           "availableToPromiseTimestamp": None,
           "etag": "abc", "updated": "2026-09-12T19:03:11.000000Z"}
    item = gs.build_item(_spec(), row, "tag")
    assert item is not None, "a camelCase row must be readable too"
    assert item["productId"] == {"S": "A1"}
    assert item["onHandQuantity"] == {"N": "7"}
    assert item["availableToPromiseIndicator"] == {"BOOL": False}
    assert item["isbn13"] == {"NULL": True}


def test_every_materialized_property_is_written_including_nulls():
    """N12 at the serving layer. Omitting a null attribute makes an absent
    attribute and a CLEARED value indistinguishable, so a consumer keeps the
    stale value - and an API whose contract marks the field required cannot
    honour it. The marketing-copy materializer omits them; this must not."""
    item = gs.build_item(_spec(), _unload_row(), "tag")
    for p in _spec().props:
        assert p in item, f"{p} missing from the serving item"
    assert item["availableToPromiseTimestamp"] == {"NULL": True}


def test_types_survive_as_dynamodb_types():
    item = gs.build_item(_spec(), _unload_row(), "tag")
    assert item["availableToPromiseIndicator"] == {"BOOL": True}
    assert item["onHandQuantity"] == {"N": "38"}
    assert item["isbn13"] == {"S": "9780602575274"}


def test_etag_is_the_gold_channel_hash_verbatim():
    row = _unload_row()
    item = gs.build_item(_spec(), row, "tag")
    assert item["etag"]["S"] == row["etag"]
    assert item["goldUpdatedAt"]["S"] == row["updated"]


def test_a_row_with_no_key_is_skipped_not_written():
    row = _unload_row()
    row["productid"] = None
    assert gs.build_item(_spec(), row, "tag") is None


def test_the_key_is_entity_qualified_so_ONE_table_holds_every_entity():
    item = gs.build_item(_spec(), _unload_row(), "tag")
    assert item["pk"] == {"S": "stock#A100000195802"}
    assert item["sk"] == {"S": "01E"}


def test_the_key_properties_are_STILL_written_as_attributes():
    """pk and sk are storage. The contract returns productId and
    organizationCode and PR #493 marks both required, so dropping them
    because "the key already carries them" breaks the contract."""
    item = gs.build_item(_spec(), _unload_row(), "tag")
    assert item["productId"] == {"S": "A100000195802"}
    assert item["organizationCode"] == {"S": "01E"}


def test_a_single_key_entity_takes_the_sentinel_sort_key():
    """An empty string is not a legal key value, so an entity whose grain is
    just its identifier cannot leave sk blank."""
    spec = _spec(target={"database": "uif_echo_{env}_gold_stock",
                         "table": "stock",
                         "primary_key_cols": ["productId"]})
    item = gs.build_item(spec, _unload_row(), "tag")
    assert item["pk"] == {"S": "stock#A100000195802"}
    assert item["sk"] == {"S": gs.SK_NONE}


def test_two_entities_in_one_table_cannot_collide():
    same_row = _unload_row()
    a = gs.build_item(_spec(), same_row, "tag")
    b = gs.build_item(_spec(gold_id="price"), same_row, "tag")
    assert a["pk"] != b["pk"]


def test_serving_is_OFF_unless_the_config_opts_in():
    """One Lambda sees every entity's event, so an entity that never asked to
    be served must not be."""
    assert _spec().enabled is False
    assert _spec(serving={"enabled": True}).enabled is True


def test_every_required_contract_field_is_present():
    item = gs.build_item(_spec(), _unload_row(), "tag")
    assert set(_master()["required"]) <= set(item)


# ── the engine ──────────────────────────────────────────────────────────────

class FakeDdb:
    def __init__(self, fail_conditional=False):
        self.put = []
        self.deleted = []
        self.scans = []
        self.fail_conditional = fail_conditional

    def put_item(self, **kw):
        if self.fail_conditional:
            raise RuntimeError("ConditionalCheckFailedException")
        self.put.append(kw)

    def batch_write_item(self, RequestItems):
        for _, reqs in RequestItems.items():
            self.deleted += [r["DeleteRequest"]["Key"] for r in reqs]
        return {}

    def scan(self, **kw):
        self.scans.append(kw)
        if len(self.scans) == 1:
            key = {"pk": {"S": "stock#p1"}, "sk": {"S": "01E"}}
            return {"Items": [key], "LastEvaluatedKey": key}
        return {"Items": []}


def _rt(**over):
    kw = {"table_name": "uif-dev-stock-serving", "output_s3": "s3://o/"}
    kw.update(over)
    return gs.ServingRuntime(**kw)


def test_the_dynamodb_pool_is_sized_to_the_PUT_CONCURRENCY(monkeypatch):
    """botocore defaults to 10 connections while put_batch runs put_threads
    at once. The extra writers then churn - each opens a fresh TLS connection,
    uses it and has it discarded - which is what stock's first baseline logged
    for all 140,257 rows as "Connection pool is full, discarding connection".
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    _, _, ddb, _, _ = gs.clients(ddb_pool=16)
    assert ddb.meta.config.max_pool_connections == 16


def test_the_CONTROL_botocores_default_really_is_below_put_threads(monkeypatch):
    """The test above proves nothing unless the default is genuinely lower
    than the writer count. Both halves of that premise are asserted here, so
    a future put_threads change cannot quietly make the fix moot."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    _, _, ddb, _, _ = gs.clients()
    default_pool = ddb.meta.config.max_pool_connections
    assert default_pool == 10
    assert _rt().put_threads > default_pool, (
        "put_threads no longer exceeds the default pool — either the fix is "
        "unnecessary or put_threads shrank; decide deliberately")


def test_put_conditional_can_only_move_goldUpdatedAt_forward():
    ddb = FakeDdb()
    item = gs.build_item(_spec(), _unload_row(), "tag")
    assert gs.put_conditional(ddb, _rt(), item) is True
    cond = ddb.put[0]["ConditionExpression"]
    assert "goldUpdatedAt <= :u" in cond
    assert "attribute_not_exists(goldUpdatedAt)" in cond


def test_a_stale_write_is_suppressed_not_raised():
    ddb = FakeDdb(fail_conditional=True)
    item = gs.build_item(_spec(), _unload_row(), "tag")
    assert gs.put_conditional(ddb, _rt(), item) is False


def test_TRAP_prune_pages_on_the_whole_composite_key():
    """A single-attribute cursor silently truncates paging for any entity with
    a sort key - it would resume from the wrong place and leave orphans.

    The assertion is on the CHAINED state, not on the in-loop scan argument:
    a mutation truncating `state["scan_key"]` was MISSED when this only checked
    ddb.scans[1], because that reads the full LastEvaluatedKey directly and
    never exercises the value the next invocation resumes from.
    """
    ddb = FakeDdb()
    state = {"sync_tag": "t", "baseline_start": "2026-09-24T00:00:00"}
    # A budget already below the floor forces the hand-off after the first
    # page: the page is still scanned and deleted (the check is at the loop
    # end), then the cursor goes into state instead of looping.
    out = gs.prune(ddb, _spec(), _rt(), state, lambda: 0)
    assert out["pruned"] == 1
    assert ddb.deleted == [{"pk": {"S": "stock#p1"}, "sk": {"S": "01E"}}]
    assert out["scan_key"] is not None, "the prune must hand off a cursor"
    assert set(out["scan_key"]) == {"pk", "sk"}, (
        "the resume cursor must carry the WHOLE key")


def test_TRAP_prune_is_scoped_to_ITS_OWN_entity():
    """The one thing a shared table makes dangerous.

    The sweep deletes every item not carrying this run's syncTag - and no
    other entity's items ever carry it. Without the pk guard a stock baseline
    would delete every product, price and program item in the table.
    """
    ddb = FakeDdb()
    gs.prune(ddb, _spec(), _rt(), {"sync_tag": "t", "baseline_start": "x"},
             lambda: 10_000_000)
    kw = ddb.scans[0]
    assert "begins_with(pk, :ent)" in kw["FilterExpression"]
    assert kw["ExpressionAttributeValues"][":ent"] == {"S": "stock#"}
    assert kw["ProjectionExpression"] == "pk, sk"


def test_rebuild_flag_reads_a_present_but_falsey_value_correctly():
    assert gs.rebuild_flag({"--rebuild": "true"}) is True
    assert gs.rebuild_flag({"--rebuild": ""}) is False
    assert gs.rebuild_flag({"--rebuild": "false"}) is False
    assert gs.rebuild_flag({}) is False


def test_continue_or_finish_chains_while_files_remain():
    sent = []

    class Lam:
        def invoke(self, **kw):
            sent.append(json.loads(kw["Payload"]))

    out = gs.continue_or_finish(
        Lam(), _rt(self_function_name="f"),
        {"files": ["s3://b/k"], "sync_tag": "t", "written": 5})
    assert out["continued"] == 1
    assert sent[0]["mode"] == "process"


def test_continue_or_finish_runs_the_prune_after_a_baseline():
    sent = []

    class Lam:
        def invoke(self, **kw):
            sent.append(json.loads(kw["Payload"]))

    out = gs.continue_or_finish(
        Lam(), _rt(self_function_name="f"),
        {"files": [], "sync_tag": "t", "prune_after": True,
         "baseline_start": "x"})
    assert out["continued"] == 1
    assert sent[0]["mode"] == "prune" and sent[0]["prune_started"] is True


# ── nested structures (UIFPE-520) ──────────────────────────────────────────
#
# Price is the first served entity with real structure, and the engine broke at
# every layer because stock is six scalars: the writer stringified an array as
# a Python repr, the Athena UNLOAD died on an uncast nested timestamp, nested
# names arrived physical, and a nested surrogate was never stripped. Each test
# below pins one of those, and two of them are TRAPs that only a live run or a
# live Athena probe would have found.

PRICE_MASTER = json.loads(
    (REPO_ROOT / "domains/price/src/schemas/gold/price/v1/master.json")
    .read_text(encoding="utf-8"))


def _price_spec(channel="api"):
    cfg = {"gold_id": "price",
           "target": {"database": "uif_echo_{env}_gold_price", "table": "price",
                      "primary_key_cols": ["productId"]},
           "materialized_properties": sorted(PRICE_MASTER["properties"])}
    return gs.ServingSpec.from_config(cfg, "dev", master=PRICE_MASTER,
                                      channel=channel)


def test_attr_writes_a_list_as_L_not_a_python_repr():
    """THE ONE THAT MADE PRICE UNSERVABLE. _attr had no list branch, so it fell
    through to {"S": str(value)} and stored "[{'a': 1}]" — single quotes, not
    even JSON — where the contract declares an array of objects."""
    got = gs._attr([{"a": 1}])
    assert got == {"L": [{"M": {"a": {"N": "1"}}}]}, got
    assert gs._attr({"a": "x"}) == {"M": {"a": {"S": "x"}}}


def test_an_empty_list_is_an_empty_L_not_NULL():
    """N12: an empty list is [], so consumers iterate without a null check.
    256,526 of price's products carry no price in domain, which makes this the
    steady state rather than an edge case."""
    assert gs._attr([]) == {"L": []}


def test_nested_members_INHERIT_the_parent_unless_stated():
    """ADR-0003's nested rule, and it is the OPPOSITE of the top-level one. A
    top-level property opts IN — absent x-channels means no channel. A member
    inherits, so absent means inherit and only an explicit list excludes."""
    master = {"properties": {"things": {
        "type": ["array", "null"], "x-channels": ["event", "message", "api"],
        "items": {"properties": {
            "inherits": {"type": ["string", "null"]},
            "apiOnly": {"type": ["string", "null"], "x-channels": ["api"]},
            "eventOnly": {"type": ["string", "null"], "x-channels": ["event"]},
            "excluded": {"type": ["integer", "null"], "x-channels": []}}}}}}
    api = gs.nested_members(master, "things", "api")
    assert sorted(api) == ["apiOnly", "inherits"]
    assert sorted(gs.nested_members(master, "things", "event")) == \
        ["eventOnly", "inherits"]


def test_an_explicit_empty_channel_list_EXCLUDES_a_surrogate():
    """price's priceListId carries x-channels: [] because it is an Oracle
    surrogate (N7). Reading absent-means-inherit without honouring an explicit
    [] would leak it, and the top-level reading would drop its 14 siblings."""
    members = _price_spec().nested_props["priceSpecifications"]
    assert "priceListId" not in members
    assert len(members) == 14


def test_a_flat_entity_derives_NO_nested_props():
    """stock must be untouched by all of this — it is the regression guard for
    the whole change."""
    assert _price_spec().nested_props           # price does
    assert gs.ServingSpec.from_config(
        _cfg(), "dev",
        master=json.loads(STOCK_MASTER.read_text(encoding="utf-8")),
        channel="api").nested_props == {}


def test_TRAP_the_nested_row_field_names_are_QUOTED():
    """Trino folds an UNQUOTED identifier to lower case before the JSON is
    built, so an unquoted ROW field name yields "pricelistname" — measured
    against live Athena, not assumed. N7 and N8 forbid a physical rendering on
    an API surface, and the contract declares camelCase."""
    sql = gs.nested_select(_price_spec(), "priceSpecifications")
    assert '"priceListName" varchar' in sql
    assert "pricelistname" not in sql.lower().replace('"pricelistname"', "")


def test_TRAP_a_nested_timestamp_is_to_iso8601_not_CAST():
    """Casting a struct that still holds a timestamp with time zone to JSON
    fails the whole query with "NOT_SUPPORTED: Unsupported Hive type:
    timestamp(6) with time zone" — which is how price's first baseline died.
    And CAST AS VARCHAR would give Athena's display form, not ISO-8601 (N11).
    """
    sql = gs.nested_select(_price_spec(), "priceSpecifications")
    for stamp in ("price_start_timestamp", "price_end_timestamp",
                  "price_last_modified_timestamp"):
        assert f'to_iso8601(x."{stamp}")' in sql
    assert 'CAST(x."price_start_timestamp" AS varchar)' not in sql


def test_a_nested_number_stays_DECIMAL_EXACT():
    """Everything non-temporal is cast to varchar in the query and typed here,
    because DynamoDB carries N as a string. A JSON number would become a
    Python float on the way and round decimal(38,10)."""
    sql = gs.nested_select(_price_spec(), "priceSpecifications")
    assert 'CAST(x."list_price_amount" AS varchar)' in sql
    members = _price_spec().nested_props["priceSpecifications"]
    assert members["listPriceAmount"] == "N"
    built = gs._nested_attr(json.dumps(
        [{"listPriceAmount": "1234567.1234567890"}]), {"listPriceAmount": "N"})
    assert built["L"][0]["M"]["listPriceAmount"] == {"N": "1234567.1234567890"}


def test_the_query_selects_ONLY_the_channels_members():
    sql = gs.nested_select(_price_spec(), "priceSpecifications")
    assert "price_list_id" not in sql


def test_build_item_parses_the_nested_json_into_L_of_M():
    spec = _price_spec()
    row = {"productId": "A1", "isbn13": None,
           "priceSpecifications": json.dumps([
               {"priceListName": "US List", "listPriceAmount": "49.99",
                "priceStartTimestamp": "2026-01-01T00:00:00.000000Z"}]),
           "etag": "e1", "updated": "2026-09-20T06:00:00Z"}
    item = gs.build_item(spec, row, sync_tag="t")
    el = item["priceSpecifications"]["L"][0]["M"]
    assert len(el) == 14, "every declared member is written, nulls included"
    assert el["priceListName"] == {"S": "US List"}
    assert el["listPriceAmount"] == {"N": "49.99"}
    assert el["priceStartTimestamp"] == {"S": "2026-01-01T00:00:00.000000Z"}
    assert el["netPriceAmount"] == {"NULL": True}


def test_a_nested_property_a_master_does_not_DESCRIBE_is_not_claimed():
    """An array whose items the master leaves undescribed would project to
    nothing, which is worse than refusing. It stays out of nested_props and
    _attr's recursion stores it whole."""
    master = {"properties": {"opaque": {"type": ["array", "null"],
                                        "x-channels": ["api"]}}}
    cfg = {"gold_id": "x", "target": {"database": "d", "table": "t",
                                      "primary_key_cols": ["opaque"]},
           "materialized_properties": ["opaque"]}
    spec = gs.ServingSpec.from_config(cfg, "dev", master=master, channel="api")
    assert spec.nested_props == {}


# ── a business key distinct from the merge key (UIFPE-687) ──────────────────
#
# Only program needs this, and the reason is structural: four of its six
# Silver sources cannot express programId, so the gold PK must stay the Oracle
# pair for CDC to work at all, while the API addresses rows by programId.


def _indexed_cfg(**over):
    cfg = _cfg(**over)
    cfg["target"]["entity_key_cols"] = ["productId"]
    return cfg


def test_entity_keys_FALLS_BACK_to_the_merge_key():
    """Every entity but program declares nothing, and must behave exactly as
    before. The fallback lives in one property so nothing else writes `or`.
    """
    s = _spec()
    assert s.entity_key_props == ()
    assert s.entity_keys == s.key_props
    assert s.indexed is False


def test_declaring_the_SAME_key_is_not_indexed():
    """A redundant declaration must not silently start paying for an index.
    `indexed` compares the tuples rather than testing presence.
    """
    cfg = _cfg()
    cfg["target"]["entity_key_cols"] = list(cfg["target"]["primary_key_cols"])
    s = gs.ServingSpec.from_config(cfg, env="dev", master=_master())
    assert s.entity_keys == s.key_props
    assert s.indexed is False, "an identical declaration is not a second key"


def test_an_indexed_entity_is_recognised():
    s = gs.ServingSpec.from_config(_indexed_cfg(), env="dev", master=_master())
    assert s.entity_keys == ("productId",)
    assert s.indexed is True


def test_an_entity_key_the_copy_DOES_NOT_STORE_is_refused():
    """The one failure worth refusing outright. The index attribute would
    never be written, every business-key lookup would 404, and the items would
    look perfectly fine — so it has to fail at config load, loudly.
    """
    cfg = _cfg()
    cfg["target"]["entity_key_cols"] = ["notAProperty"]
    with pytest.raises(ValueError, match="entity_key_cols"):
        gs.ServingSpec.from_config(cfg, env="dev", master=_master())


def test_a_business_key_of_THREE_columns_is_refused():
    """The read surface addresses two path segments. A third column would go
    into the index value and never be queried, so every lookup would miss
    while the items themselves looked complete.
    """
    cfg = _cfg()
    cfg["target"]["entity_key_cols"] = ["productId", "organizationCode",
                                        "isbn13"]
    with pytest.raises(ValueError, match="at most two"):
        gs.ServingSpec.from_config(cfg, env="dev", master=_master())


def test_a_TWO_column_business_key_indexes_on_BOTH_parts():
    """Product's shape. index_key already wrote every part; what was missing
    was a reader that asked for the same value (UIFPE-709).
    """
    cfg = _cfg()
    cfg["target"]["entity_key_cols"] = ["productId", "isbn13"]
    spec = gs.ServingSpec.from_config(cfg, env="dev", master=_master())
    assert spec.indexed
    got = gs.index_key(spec, {"productId": "A1", "isbn13": "978"}.get)
    assert got == (f"{spec.entity}#A1", "978"), got


def test_index_value_is_the_ONE_formatter_both_paths_use():
    """The retrieval Lambda builds its lookup with these and index_key writes
    with them, so the two cannot drift apart.
    """
    assert gs.index_value("product", "A1") == "product#A1"
    assert gs.index_sort("01E") == "01E"


def test_a_ONE_column_business_key_sorts_at_the_sentinel():
    """A GSI drops any item missing its sort attribute, so a one-column key
    cannot simply leave it out — program would vanish from the index.
    """
    assert gs.index_sort() == gs.SK_NONE
    assert gs.index_sort(None) == gs.SK_NONE
    assert gs.index_sort("") == gs.SK_NONE
    cfg = _cfg()
    cfg["target"]["entity_key_cols"] = ["productId"]
    spec = gs.ServingSpec.from_config(cfg, env="dev", master=_master())
    assert gs.index_key(spec, {"productId": "A1"}.get) == (
        f"{spec.entity}#A1", gs.SK_NONE)


def test_the_PARTITION_is_the_first_column_only():
    """The collection read depends on it: one partition has to hold every
    sub-grain of one business key. Folding the second column into the hash
    makes the partition unique per row and the collection read impossible.
    """
    cfg = _cfg()
    cfg["target"]["entity_key_cols"] = ["productId", "isbn13"]
    spec = gs.ServingSpec.from_config(cfg, env="dev", master=_master())
    a = gs.index_key(spec, {"productId": "A1", "isbn13": "978"}.get)
    b = gs.index_key(spec, {"productId": "A1", "isbn13": "979"}.get)
    assert a[0] == b[0], "two sub-grains of one key must share a partition"
    assert a[1] != b[1], "and be told apart by the sort key"


def test_the_index_attribute_is_written_ONLY_when_indexed():
    """Sparse by construction: an unindexed entity writes no `ek`, so it never
    appears in the index and costs nothing to store or write.
    """
    row = {"productid": "A1", "organizationcode": "01E",
           "onhandquantity": "644", "availabletopromiseindicator": True,
           "availabletopromisetimestamp": "2026-09-29T00:00:00Z"}

    plain = gs.build_item(_spec(), row, "tag-1")
    assert gs.EK not in plain, "an unindexed entity must write no ek"

    spec = gs.ServingSpec.from_config(_indexed_cfg(), env="dev",
                                      master=_master())
    indexed = gs.build_item(spec, row, "tag-1")
    assert indexed[gs.EK] == {"S": "stock#A1"}
    # And the merge key is untouched — the index is additive, never a move.
    assert indexed["pk"] == plain["pk"]
    assert indexed["sk"] == plain["sk"]


def test_an_absent_business_key_writes_NO_index_attribute():
    """A null business key is a data defect the DQ check reports. The row
    still has to be served by its merge key rather than dropped: half an item
    is worse than an unindexed one.
    """
    spec = gs.ServingSpec.from_config(_indexed_cfg(), env="dev",
                                      master=_master())
    row = {"productid": None, "organizationcode": "01E",
           "onhandquantity": "644", "availabletopromiseindicator": True}
    item = gs.build_item(spec, row, "tag-1")
    # productId is also part of the merge key here, so this row has no key at
    # all and is correctly refused. The narrower case is covered by
    # index_key's own contract.
    assert item is None
    assert gs.index_key(spec, lambda k: None) is None


# ── a single nested OBJECT, and nesting two levels deep (UIFPE-519) ─────────
#
# The engine grew its nested support one shape at a time, each revealed by
# whichever entity arrived next: stock flat, price array-of-struct, program
# value-array, product a single OBJECT that itself contains an array. This is
# that fourth shape. Athena's own refusal is what found it —
# "Unexpected parameters (row(...), <function>) for function transform" —
# because TRANSFORM needs an array and a ROW is not one.


def _obj_master():
    """One object property with a scalar, a timestamp, an array of objects and
    a value array — every branch of the recursion in one fixture."""
    return {
        "properties": {
            "productId": {"type": "string", "x-channels": ["api"]},
            "orgDetails": {
                "type": ["object", "null"], "x-channels": ["api"],
                "properties": {
                    "imprintName": {"type": ["string", "null"]},
                    "publicationDate": {"type": ["string", "null"],
                                        "format": "date"},
                    "initiatives": {
                        "type": ["array", "null"],
                        "items": {"type": "object", "properties": {
                            "initiativeName": {"type": ["string", "null"]},
                            "initiativeStartDate": {"type": ["string", "null"],
                                                    "format": "date"},
                        }},
                    },
                    "tagCodes": {"type": ["array", "null"],
                                 "items": {"type": "string"}},
                    # An OBJECT inside the object. Without this the
                    # object branch of _member_kind is never exercised: the
                    # top-level property's shape comes from the master, so a
                    # mutation there survived every test until a control
                    # pointed it out.
                    "returnsPolicy": {
                        "type": ["object", "null"],
                        "properties": {
                            "returnWindowDayCount": {"type": ["integer", "null"]},
                            "returnPolicyCode": {"type": ["string", "null"]},
                        },
                    },
                    # An explicit [] two levels in: the surrogate case.
                    "orgSurrogateId": {"type": ["integer", "null"],
                                       "x-channels": []},
                },
            },
        }
    }


def _obj_spec():
    cfg = {
        "gold_id": "thing",
        "target": {"database": "uif_echo_{env}_gold_thing", "table": "thing",
                   "primary_key_cols": ["productId"]},
        "materialized_properties": ["productId", "orgDetails"],
        "serving": {"enabled": True},
    }
    return gs.ServingSpec.from_config(cfg, env="dev", master=_obj_master())


def test_a_single_object_property_is_recognised_as_one():
    s = _obj_spec()
    assert "orgDetails" in s.nested_props
    assert s.nested_object_props == frozenset({"orgDetails"})


def test_a_single_object_is_built_WITHOUT_transform():
    """The bug this fixes. TRANSFORM takes an array; applied to a ROW, Athena
    fails the whole query, which is how product's fulfillmentOrgDetails
    surfaced the gap.
    """
    sql = gs.nested_select(_obj_spec(), "orgDetails")
    top = sql.split("TRANSFORM", 1)[0]
    assert top.startswith("json_format(CAST(CAST(ROW("), top[:60]
    # The nested array still uses one, so asserting "no TRANSFORM anywhere"
    # would be wrong — and was the first check I wrote.
    assert 'TRANSFORM(g."org_details"."initiatives"' in sql


def test_a_member_two_levels_down_is_RECURSED_not_stringified():
    s = _obj_spec()
    kind = s.nested_props["orgDetails"]["initiatives"]
    assert isinstance(kind, tuple) and kind[0] == "L"
    assert sorted(kind[1]) == ["initiativeName", "initiativeStartDate"]
    sql = gs.nested_select(s, "orgDetails")
    # Its member names are QUOTED at depth too, or Trino folds them and the
    # API returns initiativename.
    assert '"initiativeName" varchar' in sql
    # And its date member is to_iso8601'd at depth, not cast to varchar —
    # casting a timestamp-with-tz to JSON fails the query outright.
    assert 'to_iso8601(y0."initiative_start_date")' in sql


def test_a_value_array_inside_an_object_becomes_array_of_varchar():
    sql = gs.nested_select(_obj_spec(), "orgDetails")
    assert '"tagCodes" array(varchar)' in sql
    assert 'TRANSFORM(g."org_details"."tag_codes", e -> CAST(e AS varchar))' in sql


def test_an_excluded_member_TWO_LEVELS_IN_is_dropped():
    """The inherit rule applies at every depth. Applying it only to the first
    level would leak a surrogate declared deeper — which is price's
    priceListId lesson, one level further down.
    """
    s = _obj_spec()
    assert "orgSurrogateId" not in s.nested_props["orgDetails"]
    assert "org_surrogate_id" not in gs.nested_select(s, "orgDetails")


def test_an_object_is_stored_as_an_M_not_an_L():
    s = _obj_spec()
    row = {"productid": "A1", "orgdetails": json.dumps({
        "imprintName": "Pearson", "publicationDate": "2026-01-01",
        "initiatives": [{"initiativeName": "Spring",
                         "initiativeStartDate": "2026-02-01"}],
        "tagCodes": ["a", "b"]})}
    item = gs.build_item(s, row, "t")
    od = item["orgDetails"]
    assert set(od) == {"M"}, od
    assert od["M"]["imprintName"] == {"S": "Pearson"}
    assert od["M"]["initiatives"]["L"][0]["M"]["initiativeName"] == {"S": "Spring"}
    assert od["M"]["tagCodes"] == {"L": [{"S": "a"}, {"S": "b"}]}


def test_an_OBJECT_INSIDE_an_object_is_an_M_at_depth():
    """_member_kind's object branch only fires here — the top-level property's
    shape is read from the master, so nothing else exercises it. A control
    found this: typing the branch as a scalar broke nothing.
    """
    s = _obj_spec()
    kind = s.nested_props["orgDetails"]["returnsPolicy"]
    assert isinstance(kind, tuple) and kind[0] == "M", kind
    assert sorted(kind[1]) == ["returnPolicyCode", "returnWindowDayCount"]
    sql = gs.nested_select(s, "orgDetails")
    assert '"returnsPolicy" row("returnWindowDayCount" varchar' in sql
    row = {"productid": "A1", "orgdetails": json.dumps(
        {"returnsPolicy": {"returnWindowDayCount": "30",
                           "returnPolicyCode": "STD"}})}
    rp = gs.build_item(s, row, "t")["orgDetails"]["M"]["returnsPolicy"]
    assert set(rp) == {"M"}, rp
    assert rp["M"]["returnPolicyCode"] == {"S": "STD"}


# ── the total item-size ceiling ─────────────────────────────────────────────


def test_item_bytes_is_DYNAMODBS_accounting_not_json_length():
    """json.dumps over-states by ~40% — it counts braces, quotes and the
    {"S": ...} type tags, which DynamoDB does not. Measuring product's widest
    item the wrong way said 517,681 bytes and "over the 400 KB limit"; the
    right way says 307,098 and it fits. An over-statement here refuses items
    that would have been served.
    """
    item = {"pk": {"S": "x" * 100}, "n": {"N": "12345"},
            "flag": {"BOOL": True}, "gone": {"NULL": True},
            "list": {"L": [{"S": "ab"}, {"S": "cd"}]}}
    measured = gs.item_bytes(item)
    as_json = len(json.dumps(item).encode())
    assert measured < as_json, (measured, as_json)
    # The name and the value, both counted.
    assert measured >= 100 + len("pk")


def test_an_item_over_the_CEILING_is_refused_not_written():
    """There was no total-size ceiling at all: the check lived inside the
    compress branch, so an entity without a compressed property built the
    item and let DynamoDB reject the write — which fails the whole sync leg
    rather than one row.
    """
    s = _obj_spec()
    huge = "x" * (gs.MAX_ITEM_BYTES + 1)
    row = {"productid": "A1", "orgdetails": json.dumps({"imprintName": huge})}
    assert gs.build_item(s, row, "t") is None


def test_an_item_UNDER_the_ceiling_is_still_served():
    """The half that stops the guard becoming a blanket refusal."""
    s = _obj_spec()
    row = {"productid": "A1", "orgdetails": json.dumps({"imprintName": "ok"})}
    item = gs.build_item(s, row, "t")
    assert item is not None
    assert gs.item_bytes(item) < gs.MAX_ITEM_BYTES


def test_the_ceiling_is_dynamodbs_real_limit():
    assert gs.MAX_ITEM_BYTES == 400 * 1024
