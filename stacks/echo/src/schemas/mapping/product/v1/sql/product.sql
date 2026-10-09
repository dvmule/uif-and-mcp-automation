-- Gold Product transform — v1 (5-column lean)
-- 1 row per (inventory_item_id, org_id) from MTL_SYSTEM_ITEMS_B,
-- left-joined to ISBN attrs from EGO_MTL_SY_ITEMS_EXT_B (attr_group_id=741).
-- ISBN join is on inventory_item_id only — EXT_B for this attr_group is
-- item-level, so the same ISBN broadcasts to every org-row for the item.
--
-- Watermark contract:
--   The job restricts every source to rows where _silver_updated_at > :watermark
--   BEFORE this SQL is invoked, by registering filtered temp views with the same
--   aliases below (items, items_ext). Do not reference Silver tables directly
--   here — use the temp views the runtime injects.
--
-- Output column names MUST exactly match the master schema property names.
-- The runtime validate_output_columns() will fail the job otherwise.

WITH isbn AS (
  SELECT
    CAST(items_ext.INVENTORY_ITEM_ID AS BIGINT)          AS inventory_item_id,
    items_ext.C_EXT_ATTR6                                AS isbn10,
    items_ext.C_EXT_ATTR8                                AS isbn13
  FROM items_ext
  WHERE items_ext.ATTR_GROUP_ID = 741
)

SELECT
  CAST(items.INVENTORY_ITEM_ID AS BIGINT)                AS inventory_item_id,
  items.SEGMENT1                                         AS product_id,
  CAST(items.ORGANIZATION_ID AS BIGINT)                  AS org_id,
  isbn.isbn10                                            AS isbn10,
  isbn.isbn13                                            AS isbn13
FROM items
LEFT JOIN isbn
  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = isbn.inventory_item_id
