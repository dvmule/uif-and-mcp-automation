# lifecycle_fixtures.py — shared draft-building fixtures for the
# control-panel suite. Deliberately NOT a test module: under pytest's
# importlib import mode (repo-root pytest.ini, UIFPE-152) test modules can no
# longer import each other by basename, so the pieces test_lifecycle and
# test_publisher both need live here. The subtree conftest puts this
# directory on sys.path so the import resolves in every run mode.

from fastapi.testclient import TestClient

from control_panel.api import app

client = TestClient(app)

MAPPING = {
    "version": "1.0",
    "sources": [{"name": "product",
                 "path": "glue_catalog.eds_dev_gold_product.product",
                 "format": "iceberg"}],
    "joins": [],
    "mappings": [{"type": "one_to_one", "source": "product.product_id", "target": "productId"}],
    "validations": [],
    "rejected_path": "s3://b/rejected/${batch_id}/",
    "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                            "path": "s3://b/staging/${batch_id}/canonical/"}]},
}


def make_draft(**overrides):
    body = {"job_id": "product-export", "domain": "product",
            "author": "karthik", "mapping_config": MAPPING,
            "settings": {"formats": ["csv"]}, "unresolved": []}
    body.update(overrides)
    return client.post("/drafts", json=body).json()


# UIFPE-167: a two-source feed on CATALOG-picked tables from two domains,
# shaped on the committed product-export v2 artifact (product ⋈ price, left
# join on ppid, the price array shipped as JSON). Both paths are what the
# wizard's picker emits for gold_fixtures.CATALOG — no sources.yaml row.
MAPPING_JOINED = {
    "version": "1.0",
    "sources": [{"name": "product",
                 "path": "glue_catalog.uif_echo_dev_gold_product.product",
                 "format": "iceberg"},
                {"name": "price",
                 "path": "glue_catalog.uif_echo_dev_gold_price.price",
                 "format": "iceberg"}],
    "joins": [{"left": "product", "right": "price", "type": "left",
               "condition": "product.product_id = price.product_id"}],
    "mappings": [{"type": "one_to_one", "source": "product.product_id", "target": "productId"},
                 {"type": "derived", "target": "priceSpecifications",
                  "expression": "to_json(price.price_specifications)"}],
    "validations": [],
    "rejected_path": "s3://b/rejected/${batch_id}/",
    "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                            "path": "s3://b/staging/${batch_id}/canonical/"}]},
}


def make_joined_draft(**overrides):
    body = {"job_id": "product-price-export", "domain": "product",
            "author": "karthik", "mapping_config": MAPPING_JOINED,
            "settings": {"formats": ["csv"]}, "unresolved": []}
    body.update(overrides)
    return client.post("/drafts", json=body).json()
