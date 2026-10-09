"""UIFPE-580: reading a list column's ELEMENT schema without Spark — from the
Glue type string the control panel sees at submit time."""
from dynamic_mapping import lists
from dynamic_mapping.config_loader import parse_config


def test_array_of_struct_yields_the_field_names_in_order():
    t = ("array<struct<content_id: string, marketing_copy_text: string, "
         "marketing_copy_type_code: string, audience_code: string>>")
    assert lists.element_fields_from_glue_type(t) == [
        "content_id", "marketing_copy_text", "marketing_copy_type_code", "audience_code"]


def test_nested_struct_and_decimal_commas_do_not_split_fields():
    t = ("array<struct<price_list_id:bigint,list_price_amount:decimal(38, 2),"
         "tiers:array<struct<from_quantity:decimal(38, 10),to_quantity:decimal(38, 10)>>,"
         "currency_code:string>>")
    assert lists.element_fields_from_glue_type(t) == [
        "price_list_id", "list_price_amount", "tiers", "currency_code"]


def test_array_of_scalars_exposes_one_field_called_value():
    assert lists.element_fields_from_glue_type("array<string>") == ["value"]
    assert lists.element_fields_from_glue_type("ARRAY<bigint>") == ["value"]


def test_non_arrays_and_unknown_types_are_none():
    for t in ("string", "struct<a:int>", "map<string,string>", "", None):
        assert lists.element_fields_from_glue_type(t) is None, t


def test_element_schemas_cover_through_and_unnest_and_skip_unknown_lists():
    config = parse_config({
        "version": "1.0",
        "sources": [{"name": "product", "path": "/p", "format": "iceberg"},
                    {"name": "marketing_copy", "path": "/m", "format": "iceberg"},
                    {"name": "price", "path": "/q", "format": "iceberg"}],
        "joins": [
            {"left": "product", "right": "marketing_copy", "type": "left",
             "through": {"list": "product.marketing_copy", "alias": "product_copy"},
             "condition": "product_copy.content_id = marketing_copy.content_id"},
            {"left": "product", "right": "price", "type": "left",
             "condition": "product.product_id = price.product_id"}],
        "unnest": [{"list": "price.price_specifications", "alias": "price_spec"},
                   {"list": "price.no_such_list", "alias": "ghost"}],
        "mappings": [{"type": "one_to_one", "source": "product.product_id", "target": "id"}],
        "output": {"targets": [{"format": "parquet", "path": "/out"}]},
    })
    types = {"product": {"marketing_copy": "array<struct<content_id:string,audience_code:string>>"},
             "price": {"price_specifications": "array<struct<price_list_name:string>>"}}
    out = lists.element_schemas(
        config, lambda s, c: lists.element_fields_from_glue_type(types.get(s, {}).get(c)))
    assert out == {"product_copy": ["content_id", "audience_code"],
                   "price_spec": ["price_list_name"]}
    assert config.element_aliases() == ("product_copy", "price_spec", "ghost")
