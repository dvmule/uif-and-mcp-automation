-- Gold Program transform — v1 (13 attributes, intake 2026-08-11;
-- parentProduct added 2026-08-12)
-- 1 row per (inventoryItemId, organizationId) from MTL_SYSTEM_ITEMS_B, enriched
-- from EGO_MTL_SY_ITEMS_EXT_B attribute groups 741/1164/1401/1421/1422/1423/1463,
-- the
-- translations table, MTL_RELATED_ITEMS and the relationship-type lookup.
--
-- Domain: a program is SEGMENT1 prefix 'P2' at ORGANIZATION_ID 82. The item
-- catalog also holds LEGACY 'P1%' programs in the same catalog groups; they are
-- deliberately excluded (framework owner, 2026-08-11), so the prefix — not
-- catalog-group membership — is the canonical discriminator. It is applied on
-- BOTH sides of relatedPrograms so a self-reference can never point at a
-- program this table does not carry.
--
-- Item-level CTEs aggregate with max() per item: dedupes accidental duplicate
-- attribute rows deterministically, exactly as the product transform does.
--
-- Watermark contract: the runtime registers the aliases below (items,
-- items_ext, items_tl, related, rel_types, items_all) as temp views — `items`
-- restricted to affected entities, the rest FULL. Do not reference Silver
-- tables directly here.
--
-- `items_all` is a SECOND alias over the driving table, registered full. Both
-- array CTEs must resolve OTHER items' SEGMENT1 (the associated product's, the
-- related program's); reading `items` there would silently see only the rows
-- already being rebuilt.
--
-- Output column names MUST exactly match the master schema property names.

WITH
-- ── item-level attribute groups ──────────────────────────────────────────
ag1422 AS (  -- Program relevance value + display catalog
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT)  AS iid,
         max(CAST(N_EXT_ATTR1 AS BIGINT))   AS relevanceValue,
         max(C_EXT_ATTR1)                   AS programFamilyCatalogCode
  FROM items_ext WHERE ATTR_GROUP_ID = 1422 GROUP BY 1
),
ag1423 AS (  -- Program image URL (normalized in the final SELECT)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT)  AS iid,
         max(C_EXT_ATTR1)                   AS coverImageLargeUrl
  FROM items_ext WHERE ATTR_GROUP_ID = 1423 GROUP BY 1
),
ag1463 AS (  -- Program owner
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT)  AS iid,
         max(C_EXT_ATTR1)                   AS programOwnerName
  FROM items_ext WHERE ATTR_GROUP_ID = 1463 GROUP BY 1
),

-- ── org-level translations ───────────────────────────────────────────────
tl AS (      -- External description (translations table, org-level, US language)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT)  AS iid,
         CAST(ORGANIZATION_ID AS BIGINT)    AS oid,
         max(LONG_DESCRIPTION)              AS longDescription
  FROM items_tl WHERE LANGUAGE = 'US' GROUP BY 1, 2
),

-- ── collections ──────────────────────────────────────────────────────────
assoc AS (   -- associatedProducts: the products carried by a program.
             -- TWO-HOP and REVERSE-KEYED: the group-1421 link row sits on the
             -- PRODUCT's item id and names the program by ppid STRING in
             -- C_EXT_ATTR1, so this groups by that string and the final SELECT
             -- joins it on SEGMENT1 rather than on the entity key.
             -- Dangling entries are possible BY DESIGN — an associated
             -- product's ppid need not exist in gold_product.product, whose own
             -- filter is 'A%'. Filtering by another entity's business rule
             -- would couple the two; the entity contract states the caveat.
  SELECT link.C_EXT_ATTR1                          AS program_ppid,
         array_sort(collect_list(prod.SEGMENT1))    AS associatedProducts
  FROM items_ext link
  JOIN items_all prod
    ON CAST(prod.INVENTORY_ITEM_ID AS BIGINT) = CAST(link.INVENTORY_ITEM_ID AS BIGINT)
   AND CAST(prod.ORGANIZATION_ID AS BIGINT)   = 82
  WHERE link.ATTR_GROUP_ID = 1421
    AND link.C_EXT_ATTR1 IS NOT NULL
  GROUP BY 1
),
-- ── parentProduct: the one DERIVED column ────────────────────────────────
-- Three hops, and the qualifying test differs by catalog group. Transcribed
-- from the mapping owner's Oracle query (confirmed correct 2026-08-12), with
-- its two UNION branches expressed as two membership tests instead — Spark
-- prefers pre-aggregated semi-joins to Oracle's correlated scalar subquery, and
-- the result is the same set.
--
-- WHAT "PARENT" MEANS, which the joins alone do not say:
--   DIGITAL (ITEM_CATALOG_GROUP_ID = 10002) — the product is the parent because
--     a SIBLING names it: some group-1401 row carries this product's ppid in
--     C_EXT_ATTR9 (parentPPID). The 1401 row sits on the CHILD, not on the
--     parent, so this is an existence test against other items.
--   NON-DIGITAL — the product is the parent because it is the top of its OWN
--     tree: its group-1164 C_EXT_ATTR2 (ParentISBN13) equals its own group-741
--     C_EXT_ATTR8 (ISBN13). Self-referential, and entirely item-local.
parent_named AS (  -- digital test: ppids that some sibling calls its parent
  SELECT DISTINCT C_EXT_ATTR9 AS parent_ppid
  FROM items_ext
  WHERE ATTR_GROUP_ID = 1401
    AND C_EXT_ATTR9 IS NOT NULL
),
parent_self AS (   -- non-digital test: ParentISBN13 == own ISBN13
                   -- Joined on the item AND the org: the source query scopes
                   -- par_prod by both, and the 741 lookup by item only. Keeping
                   -- the org predicate on the 1164 side and NOT inventing one on
                   -- 741 reproduces the supplied semantics exactly. If group 741
                   -- ever holds per-org rows this fans out — flagged for the
                   -- first load rather than silently DISTINCT-ed away.
  SELECT DISTINCT CAST(a1164.INVENTORY_ITEM_ID AS BIGINT) AS iid,
                  CAST(a1164.ORGANIZATION_ID  AS BIGINT) AS oid
  FROM items_ext a1164
  JOIN items_ext a741
    ON a741.ATTR_GROUP_ID = 741
   AND CAST(a741.INVENTORY_ITEM_ID AS BIGINT) = CAST(a1164.INVENTORY_ITEM_ID AS BIGINT)
  WHERE a1164.ATTR_GROUP_ID = 1164
    AND a1164.C_EXT_ATTR2 IS NOT NULL
    AND a1164.C_EXT_ATTR2 = a741.C_EXT_ATTR8
),
parent_prod AS (
             -- Keyed on the ppid STRING, exactly like assoc, because it walks
             -- the same reverse-keyed group-1421 link.
             -- min() rather than any "first": Spark has no inherent row order,
             -- so an unordered pick can differ between runs on identical data,
             -- which moves the canonical hash and pings every consumer forever
             -- (acceptance check A8). Lowest ppid is arbitrary but STABLE, and
             -- the mapping owner accepted a deterministic pick.
  SELECT link.C_EXT_ATTR1     AS program_ppid,
         min(prod.SEGMENT1)   AS parentProductId
  FROM items_ext link
  JOIN items_all prod
    ON CAST(prod.INVENTORY_ITEM_ID AS BIGINT) = CAST(link.INVENTORY_ITEM_ID AS BIGINT)
   AND CAST(prod.ORGANIZATION_ID AS BIGINT)   = 82
  LEFT JOIN parent_named pn
    ON pn.parent_ppid = prod.SEGMENT1
  LEFT JOIN parent_self ps
    ON ps.iid = CAST(prod.INVENTORY_ITEM_ID AS BIGINT)
   AND ps.oid = CAST(prod.ORGANIZATION_ID AS BIGINT)
  WHERE link.ATTR_GROUP_ID = 1421
    AND link.C_EXT_ATTR1 IS NOT NULL
    AND ((prod.ITEM_CATALOG_GROUP_ID =  10002 AND pn.parent_ppid IS NOT NULL)
      OR (prod.ITEM_CATALOG_GROUP_ID <> 10002 AND ps.iid IS NOT NULL))
  GROUP BY 1
),
rel_type_map AS (  -- Relationship-type decode, one row per code.
                   -- (LOOKUP_TYPE, LOOKUP_CODE) is UNIQUE upstream (mapping
                   -- owner, 2026-08-11), so this aggregation is DEFENSIVE, not
                   -- corrective — same posture as the product transform's orgs
                   -- CTE. It earns its place because a duplicate lookup row is
                   -- the one failure mode this join could introduce: it would
                   -- fan out relatedPrograms and duplicate array elements,
                   -- which then moves channel hashes. The Silver snapshot of
                   -- the _VL view carries no LANGUAGE column (already
                   -- collapsed) but does retain VIEW_APPLICATION_ID and
                   -- SECURITY_GROUP_ID, which is the shape that could produce
                   -- such a duplicate if the upstream guarantee ever changed.
  SELECT LOOKUP_CODE     AS code,
         max(MEANING)    AS relationshipType
  FROM rel_types
  WHERE LOOKUP_TYPE = 'MTL_RELATIONSHIP_TYPES'
    AND ENABLED_FLAG = 'Y'
  GROUP BY 1
),
rel AS (     -- relatedPrograms: related program + relationship type, keyed on
             -- the PROGRAM's own (item, org) — the relationship row sits on the
             -- program, so unlike assoc this array's link changes ARE
             -- change-detectable.
             -- Wrapper struct mirrors the product transform's contrib/mktg
             -- pattern: sort by (programPpid, relationshipType) via s1/s2, then
             -- project the payload so the named_struct's own field order — the
             -- OUTPUT shape — stays exactly as master.json declares it.
  SELECT CAST(r.INVENTORY_ITEM_ID AS BIGINT) AS iid,
         CAST(r.ORGANIZATION_ID AS BIGINT)   AS oid,
         transform(
           array_sort(collect_list(named_struct(
             's1', rp.SEGMENT1,
             's2', lk.relationshipType,
             'p', named_struct(
               'relatedProgramId',      rp.SEGMENT1,
               'relationshipType', lk.relationshipType
             )))),
           x -> x.p) AS relatedPrograms
  FROM related r
  -- the PROGRAM side: catalog + status predicates from the supplied source
  -- query. They scope WHICH programs get relationships; they are deliberately
  -- NOT part of the entity filter, because inventoryItemStatus is carried as an
  -- attribute — a program in ERROR/DELETE still appears in Gold with its
  -- status, it just reports no relationships.
  JOIN items_all pgm
    ON CAST(pgm.INVENTORY_ITEM_ID AS BIGINT) = CAST(r.INVENTORY_ITEM_ID AS BIGINT)
   AND CAST(pgm.ORGANIZATION_ID AS BIGINT)   = CAST(r.ORGANIZATION_ID AS BIGINT)
   AND pgm.ITEM_CATALOG_GROUP_ID IN (1001, 13001)
   AND pgm.INVENTORY_ITEM_STATUS_CODE NOT IN ('ERROR', 'DELETE')
  -- the RELATED side: same predicates, PLUS the 'P2%' prefix so this
  -- self-reference always resolves to a row this table carries. The source
  -- query scoped program-ness by catalog group alone, which would have admitted
  -- the legacy P1% programs Gold excludes.
  JOIN items_all rp
    ON CAST(rp.INVENTORY_ITEM_ID AS BIGINT) = CAST(r.RELATED_ITEM_ID AS BIGINT)
   AND CAST(rp.ORGANIZATION_ID AS BIGINT)   = CAST(r.ORGANIZATION_ID AS BIGINT)
   AND rp.ITEM_CATALOG_GROUP_ID IN (1001, 13001)
   AND rp.INVENTORY_ITEM_STATUS_CODE NOT IN ('ERROR', 'DELETE')
   AND rp.SEGMENT1 LIKE 'P2%'
  -- LEFT so a relationship whose type is missing from the lookup still lists
  -- the related program, with a null type, rather than vanishing. Oracle
  -- compared these implicitly; Spark will not, hence the explicit cast.
  -- The inner CAST to BIGINT is load-bearing: Silver stores the id as
  -- DECIMAL(38,10), and casting that straight to STRING renders
  -- '1.0000000000' — which matches no LOOKUP_CODE, so relationshipType came
  -- back null on every element of the first load (check A3, 2026-08-20).
  LEFT JOIN rel_type_map lk
    ON CAST(CAST(r.RELATIONSHIP_TYPE_ID AS BIGINT) AS STRING) = lk.code
  GROUP BY 1, 2
)

SELECT
  CAST(items.INVENTORY_ITEM_ID AS BIGINT)      AS inventoryItemId,
  CAST(items.ORGANIZATION_ID  AS BIGINT)       AS organizationId,
  items.SEGMENT1                               AS programId,
  items.DESCRIPTION                            AS description,
  tl.longDescription                           AS longDescription,
  ag1422.relevanceValue                        AS relevanceValue,
  ag1422.programFamilyCatalogCode                             AS programFamilyCatalogCode,
  -- Program image URL normalization (programImageUrl rule in the intake's
  -- transforms registry). '/P...' paths are DAM asset paths and get the DAM
  -- base; site-relative '/coverimage...' / '/bigcovers...' get the images host;
  -- absolute URLs and the Oracle TBD/N-A string sentinels pass through
  -- unchanged (Gold does not correct source data).
  -- Confirmed by the mapping owner 2026-08-11 and by the first load
  -- (2026-08-20, 14,239 rows): group 1423 carries BOTH '/P...' program-path
  -- and '/A...' product-path DAM images (1,284 and 2,353 rows), and the same
  -- host + GUID serves both (verified HTTP 200, image/jpeg) — so the DAM
  -- branch triggers on either prefix, matching product's pearsonImageUrl.
  -- The two site-relative branches matched 7,011 rows on the first load —
  -- live, not dead code.
  CASE
    WHEN substring(ag1423.coverImageLargeUrl, 1, 2) IN ('/P', '/A')
      THEN concat('https://images.pearson.com/i/79475d28-e560-442f-9d0c-afceca8a145a',
                  ag1423.coverImageLargeUrl)
    WHEN ag1423.coverImageLargeUrl LIKE '/coverimage%'
      OR ag1423.coverImageLargeUrl LIKE '/bigcovers%'
      THEN concat('https://images.pearson.com', ag1423.coverImageLargeUrl)
    ELSE ag1423.coverImageLargeUrl
  END                                          AS coverImageLargeUrl,
  ag1463.programOwnerName                          AS programOwnerName,
  items.INVENTORY_ITEM_STATUS_CODE             AS inventoryItemStatus,
  -- DERIVED — see the parent_prod CTE for what "parent" means per catalog group
  parent_prod.parentProductId                    AS parentProductId,
  -- Empty arrays are [] and never NULL (framework owner, 2026-08-11) — matching
  -- gold_product.product. A permanent hash-stability commitment: switching to
  -- NULL later moves every consumer's channel hashes.
  COALESCE(assoc.associatedProducts, array())  AS associatedProducts,
  COALESCE(rel.relatedPrograms,      array())  AS relatedPrograms
FROM items
LEFT JOIN tl      ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = tl.iid
               AND CAST(items.ORGANIZATION_ID AS BIGINT) = tl.oid
LEFT JOIN ag1422  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1422.iid
LEFT JOIN ag1423  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1423.iid
LEFT JOIN ag1463  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1463.iid
-- joined on the ppid STRING, not the entity key — see the assoc CTE
LEFT JOIN assoc   ON items.SEGMENT1 = assoc.program_ppid
-- also joined on the ppid STRING — same reverse-keyed 1421 link as assoc
LEFT JOIN parent_prod ON items.SEGMENT1 = parent_prod.program_ppid
LEFT JOIN rel     ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = rel.iid
               AND CAST(items.ORGANIZATION_ID AS BIGINT) = rel.oid
-- Domain filter (framework owner, 2026-08-11): this table is PROGRAMS only,
-- and only the current 'P2' generation at the master org. Legacy 'P1%'
-- programs share the same catalog groups and are excluded deliberately;
-- widening the prefix later is an additive change needing no consumer
-- coordination.
-- MUST stay in sync with the manifest's sot_filter on the items source: the
-- transform can only upsert, so a row leaving this domain (reclassified, or
-- moved off org 82) is deleted by RECONCILE, whose SoT anti-join applies
-- sot_filter. If the two diverge, reclassified rows leak or valid rows get
-- reconcile-deleted.
WHERE items.SEGMENT1 LIKE 'P2%'
  AND CAST(items.ORGANIZATION_ID AS BIGINT) = 82
