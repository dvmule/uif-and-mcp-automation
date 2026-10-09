"""Generate-with-AI draft service (UIFPE-102) — prompt/parse/handler tests.
The Bedrock client is stubbed; no AWS calls."""
import json

import ai_draft


class StubBedrock:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def converse(self, **kw):
        self.calls.append(kw)
        return {"output": {"message": {"content": [{"text": self._text}]}}}


DRAFT = json.dumps({"columns": [{
    "goldColumnName": "returnsWindow", "businessDescription": "Returns window",
    "kind": "scalar", "dataType": "integer",
    "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B", "column": "N_EXT_ATTR3",
               "filter": "ATTR_GROUP_ID = 802"},
    "transform": None,
    "channels": {"event": False, "message": False, "api": True},
    "nullable": True, "notes": None}]})


def post(body, stub):
    event = {"requestContext": {"http": {"method": "POST"}},
             "body": json.dumps(body)}
    return ai_draft.handler(event, bedrock=stub)


def test_happy_path_returns_columns_and_cors():
    stub = StubBedrock(DRAFT)
    r = post({"description": "add returnsWindow from group 802"}, stub)
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["columns"][0]["goldColumnName"] == "returnsWindow"
    # URL-level CORS owns the headers in deployment — the handler stays silent
    assert "Access-Control-Allow-Origin" not in r["headers"]
    # governance: system prompt forbids prose contracts
    system = stub.calls[0]["system"][0]["text"]
    assert "never a" in system and "prose" in system


def test_prompt_carries_entity_context():
    p = ai_draft.build_prompt({"description": "x", "table": "gold_product.product",
                               "drivingTable": "MTL_SYSTEM_ITEMS_B",
                               "knownTables": ["A", "B"]})
    assert "gold_product.product" in p
    assert "MTL_SYSTEM_ITEMS_B" in p
    assert "Known Silver tables: A, B" in p


def test_parse_tolerates_fences_and_rejects_garbage():
    fenced = "```json\n" + DRAFT + "\n```"
    assert ai_draft.parse_draft(fenced)["columns"]
    for bad in ("no json here", '{"columns": []}',
                '{"columns": [{"kind": "scalar"}]}'):
        try:
            ai_draft.parse_draft(bad)
            assert False, f"should have rejected: {bad!r}"
        except ValueError:
            pass


def test_bad_requests_are_400_not_bedrock_calls():
    stub = StubBedrock(DRAFT)
    assert post({}, stub)["statusCode"] == 400
    assert post({"description": "  "}, stub)["statusCode"] == 400
    assert not stub.calls


def test_unusable_model_output_is_502():
    r = post({"description": "x"}, StubBedrock("sorry, I cannot"))
    assert r["statusCode"] == 502


def test_options_preflight_ok():
    event = {"requestContext": {"http": {"method": "OPTIONS"}}}
    assert ai_draft.handler(event, bedrock=StubBedrock(""))["statusCode"] == 200

# -- mode=derivedSql (UIFPE-219): draft ONE derived column's query ----------

DERIVED = json.dumps({
    "sql": "SELECT INVENTORY_ITEM_ID, min(C_EXT_ATTR7) AS parentProduct"
           " FROM EGO_MTL_SY_ITEMS_EXT_B GROUP BY INVENTORY_ITEM_ID",
    "keyedBy": [{"queryColumn": "INVENTORY_ITEM_ID",
                 "entityColumn": "INVENTORY_ITEM_ID"}],
    "valueColumn": "parentProduct"})


def test_derived_mode_switches_prompt_and_parser():
    stub = StubBedrock(DERIVED)
    r = post({"mode": "derivedSql", "description": "parent isbn",
              "columnName": "parentProduct", "dataType": "string",
              "knownTables": ["EGO_MTL_SY_ITEMS_EXT_B"],
              "entityKey": ["INVENTORY_ITEM_ID", "ORGANIZATION_ID"]}, stub)
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["sql"].startswith("SELECT")
    assert body["keyedBy"][0]["queryColumn"] == "INVENTORY_ITEM_ID"
    # the system prompt is the derived contract, and the user prompt carries
    # the column + entity-key context the model needs for the grain
    sys_text = stub.calls[0]["system"][0]["text"]
    assert "one read-only Spark SELECT" in sys_text.replace("ONE", "one")
    user_text = stub.calls[0]["messages"][0]["content"][0]["text"]
    assert "parentProduct" in user_text
    assert "INVENTORY_ITEM_ID, ORGANIZATION_ID" in user_text


def test_derived_mode_refuses_contractless_drafts_as_502():
    for bad in ('{"sql": "SELECT 1"}',                       # no keyedBy
                '{"keyedBy": [{"queryColumn": "a", "entityColumn": "b"}]}',
                '{"sql": " ", "keyedBy": [{"queryColumn": "a",'
                ' "entityColumn": "b"}]}',
                '{"sql": "SELECT 1", "keyedBy": [{"queryColumn": "a"}]}'):
        r = post({"mode": "derivedSql", "description": "x"},
                 StubBedrock(bad))
        assert r["statusCode"] == 502, bad


def test_column_mode_is_untouched_by_the_new_mode():
    # no mode field -> the original prompt and parser exactly as before
    stub = StubBedrock(DRAFT)
    r = post({"description": "add returnsWindow"}, stub)
    assert r["statusCode"] == 200
    assert "columns" in json.loads(r["body"])
    assert "intake entries" in stub.calls[0]["system"][0]["text"]

def test_derived_mode_refuses_empty_and_nonstring_keyedBy():
    # all() over an empty list is True — the trailing guard is load-bearing;
    # and a numeric column index must not pass a truthiness check
    for bad in ('{"sql": "SELECT 1", "keyedBy": []}',
                '{"sql": "SELECT 1", "keyedBy": [{"queryColumn": 123,'
                ' "entityColumn": 456}]}'):
        r = post({"mode": "derivedSql", "description": "x"},
                 StubBedrock(bad))
        assert r["statusCode"] == 502, bad


def test_non_object_json_body_is_400_not_500():
    for raw in ('"x"', '[1]', '3'):
        event = {"requestContext": {"http": {"method": "POST"}}, "body": raw}
        r = ai_draft.handler(event, bedrock=StubBedrock(DRAFT))
        assert r["statusCode"] == 400, raw

