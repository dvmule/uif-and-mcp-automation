# product-export — Beacon feed versions

The product domain's outbound partner feed through the Beacon stack. Each
`v<N>/` directory holds one immutable config pair (`mapping.json` +
`export.json`, design decision D22): a published version's files are never
regenerated — a change is the next version, rollback is running an earlier
one. The committed copies here are the source of truth the QE suite lints
(`domains/product/qe/test_beacon_product_export.py`); publishing promotes
them to `s3://…/configs/product-export/v<N>/`.

| Version | Shape | Status |
|---|---|---|
| v1 | Single source (`gold_product.product`), 13 scalar columns, CSV over SFTP, full load | Published |
| v2 | v1 + `gold_price.price` **left-joined on `productId`** (UIFPE-237): the price array delivered as ONE JSON-encoded column (`priceSpecifications`, via `to_json`) plus a `priceSpecCount` scalar (NULL = no price record, 0 = explicitly empty list). Explicitly `extraction: full`. The first 13 columns are byte-identical to v1 — pinned by CI. | **Committed and CI-tested, NOT published** |

## v2 activation checklist (in order)

1. The `gold_price.price` Iceberg table exists (price domain: manifest + SQL
   + pipeline config — not this domain's work) and Beacon's live column
   introspection resolves it (`GET /registries/sources/gold_price.price/columns`
   returns `provenance: "live"`).
2. Publish the pair to `s3://eds-dev-beacon-product/configs/product-export/v2/`
   (the catalog version append + S3 write, per the Beacon publish flow).
3. Do **NOT** add a `watermark_column` to the price registry entry expecting
   incremental — the feed stays full-load: the incremental window applies to
   a single source pre-join, so price-only changes cannot ship incrementally.
   Per-source incremental for multi-source feeds is UIFPE-238.

v2 is proven against committed synthetic price data
(`domains/product/qe/fixtures/price_rows.json`), which the QE suite keeps
contract-true against the price domain's master schema — the feed's behavior
(left-join row preservation, JSON encoding, NULL-vs-empty semantics) is
pinned before the real table exists.
