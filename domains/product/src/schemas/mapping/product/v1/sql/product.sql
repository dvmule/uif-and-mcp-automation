-- Gold Product transform — v1. 63 attributes, 1 row per (inventoryItemId,
-- organizationId) from MTL_SYSTEM_ITEMS_B enriched from
-- EGO_MTL_SY_ITEMS_EXT_B attribute groups. Column history is in git.
--
-- Rules this file relies on, each stated once:
-- * Null sentinels (UIFPE-268) are replaced with NULL by the runtime on every
--   view below — nothing here tests for them.
-- * Conditional sourcing: each branch CTE filters on BOTH attribute group and
--   ITEM_CATALOG_GROUP_ID, so COALESCE(digital, legacy) is a strict pick.
-- * UK proliferation reads ORGANIZATION_CODE '01%' from the org dimension,
--   never a hard-coded id list.
-- * Item-level CTEs use max() per item to dedupe duplicate attribute rows.
-- * Read only the registered aliases (never Silver directly); items is
--   entity-restricted in Phase 2, so lookups over other products use
--   items_all.
-- * Output column names MUST match the master schema property names.

WITH
-- ── org dimension (MTL_PARAMETERS) ──────────────────────────────────────
orgs AS (    -- organizationCode + the UK org group, keyed on ORGANIZATION_ID.
             -- ORGANIZATION_ID is unique upstream (62 orgs in dev, 2026-08-06),
             -- so this aggregation is defensive, not corrective: without it a
             -- duplicate org row would fan out the join below and break the
             -- (item, org) grain — the one failure mode a dimension join can
             -- introduce that the literal CASE it replaces could not.
  SELECT CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(ORGANIZATION_CODE)          AS organizationCode
  FROM org_params GROUP BY 1
),

-- ── item-level, unconditional ───────────────────────────────────────────
ag802 AS (   -- Line of Business + Disciplines + Language + Learner Stage
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR40) AS lineOfBusiness,
         max(C_EXT_ATTR5)  AS disciplineLevel1,
         max(C_EXT_ATTR6)  AS disciplineLevel2,
         max(C_EXT_ATTR7)  AS disciplineLevel3,
         max(C_EXT_ATTR8)  AS disciplineLevel4,
         max(C_EXT_ATTR27) AS languageLevel1,
         max(C_EXT_ATTR28) AS languageLevel2,
         max(C_EXT_ATTR21) AS learnerStageLevel1,
         max(C_EXT_ATTR22) AS learnerStageLevel2
  FROM items_ext WHERE ATTR_GROUP_ID = 802 GROUP BY 1
),
ag1261 AS (  -- Digital version info
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(D_EXT_ATTR1) AS digitalEffectiveStartDate,
         max(C_EXT_ATTR1) AS digitalVersionNumber
  FROM items_ext WHERE ATTR_GROUP_ID = 1261 GROUP BY 1
),
tl AS (      -- External description (translations table, org-level, US language)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         CAST(ORGANIZATION_ID AS BIGINT)   AS oid,
         max(LONG_DESCRIPTION) AS longDescription
  FROM items_tl WHERE LANGUAGE = 'US' GROUP BY 1, 2
),

-- ── item-level, legacy (ITEM_CATALOG_GROUP_ID <> 10002) ─────────────────
ag1162 AS (  -- Owning subsidiary / country / IP owner
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR2) AS owningSubsidiaryName,
         max(C_EXT_ATTR3) AS owningSubsidiaryCountryCode,
         max(C_EXT_ATTR1) AS ipOwnerCategory
  FROM items_ext WHERE ATTR_GROUP_ID = 1162 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
ag821 AS (   -- Copyright year / delivery method / digital platform
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(CAST(N_EXT_ATTR1 AS BIGINT)) AS copyrightYear,
         max(C_EXT_ATTR6)  AS deliveryMethod,
         max(C_EXT_ATTR13) AS digitalPlatform,
         max(C_EXT_ATTR16) AS nonDisclosureAgreementIndicator
  FROM items_ext WHERE ATTR_GROUP_ID = 821 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
ag741 AS (   -- ISBNs
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR8) AS isbn13,
         max(C_EXT_ATTR6) AS isbn10,
         max(C_EXT_ATTR17) AS sourceItemNumber
  FROM items_ext WHERE ATTR_GROUP_ID = 741 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
ag1164 AS (  -- Parent ISBN-13 (legacy) — resolved to the parent's PPID via
             -- the isbn_owner index at the end of this WITH block.
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR2) AS parentIsbn13
  FROM items_ext WHERE ATTR_GROUP_ID = 1164 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
ag820 AS (   -- Edition / classifications / brand
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(CAST(N_EXT_ATTR1 AS BIGINT)) AS editionNumber,
         max(C_EXT_ATTR6) AS productClassificationLevel1,
         max(C_EXT_ATTR7) AS productClassificationLevel2,
         max(C_EXT_ATTR5) AS brand,
         max(C_EXT_ATTR8) AS subBrand
  FROM items_ext WHERE ATTR_GROUP_ID = 820 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
ag818 AS (   -- Final page count
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(CAST(N_EXT_ATTR5 AS BIGINT)) AS finalTextPageCount
  FROM items_ext WHERE ATTR_GROUP_ID = 818 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
ag773 AS (   -- Access duration (legacy)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR1) AS accessDurationType,
         max(C_EXT_ATTR2) AS accessDurationQuantity,
         max(C_EXT_ATTR3) AS accessDurationUnitCode
  FROM items_ext WHERE ATTR_GROUP_ID = 773 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
ag1341 AS (  -- Course duration (legacy, PSON_COURSE_DURATION — UIFPE-270)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR1) AS courseDurationQuantity,
         max(C_EXT_ATTR2) AS courseDurationUnitCode
  FROM items_ext WHERE ATTR_GROUP_ID = 1341 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
tl1165 AS (  -- Product URL (legacy 1165 — UIFPE-270). TL_EXT_ATTR* slots live in
             -- the TRANSLATIONS table; US only, like longDescription.
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(TL_EXT_ATTR1) AS productUrl
  FROM items_ext_tl
  WHERE ATTR_GROUP_ID = 1165 AND ITEM_CATALOG_GROUP_ID <> 10002 AND LANGUAGE = 'US'
  GROUP BY 1
),
tl1406 AS (  -- Product URL (digital, PSON_PROD_WEB_DTLS_DGTL)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(TL_EXT_ATTR1) AS productUrl
  FROM items_ext_tl
  WHERE ATTR_GROUP_ID = 1406 AND ITEM_CATALOG_GROUP_ID = 10002 AND LANGUAGE = 'US'
  GROUP BY 1
),
ag1168 AS (  -- Cover image URLs (legacy)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR3) AS coverImageLargeUrl,
         max(C_EXT_ATTR4) AS coverImageRegularUrl,
         max(C_EXT_ATTR5) AS coverImageHighResolutionUrl
  FROM items_ext WHERE ATTR_GROUP_ID = 1168 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1
),
ag1405 AS (  -- Cover image URLs (digital)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR3) AS coverImageLargeUrl,
         max(C_EXT_ATTR1) AS coverImageRegularUrl,
         max(C_EXT_ATTR2) AS coverImageHighResolutionUrl
  FROM items_ext WHERE ATTR_GROUP_ID = 1405 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1
),

-- ── item-level, Digital Catalog (ITEM_CATALOG_GROUP_ID = 10002) ─────────
ag1403 AS (  -- Owning subsidiary / country / IP owner (digital)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR2) AS owningSubsidiaryName,
         max(C_EXT_ATTR3) AS owningSubsidiaryCountryCode,
         max(C_EXT_ATTR1) AS ipOwnerCategory
  FROM items_ext WHERE ATTR_GROUP_ID = 1403 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1
),
ag1408 AS (  -- Copyright year + master-level title status / pub date (digital, non-UK proliferation)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(CAST(N_EXT_ATTR1 AS BIGINT)) AS copyrightYear,
         max(C_EXT_ATTR1) AS titleStatusCode,
         max(D_EXT_ATTR1) AS regionalPublicationDate
  FROM items_ext WHERE ATTR_GROUP_ID = 1408 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1
),
ag1417 AS (  -- Master-level title status / pub date (digital, UK proliferation)
  -- The historical C_EXT_ATTR1 IS NOT NULL filter (proliferate only when 01B
  -- had a title status) was REMOVED 2026-08-10 by the mapping owner (review
  -- D1): copying a null 01B status to UK orgs is safe, and the filter also
  -- gated regionalPublicationDate — dev impact of removal: 2,390 items
  -- recover a pub date their null status was suppressing, 46 shift to a
  -- later date, titleStatusCode itself unchanged (max() ignores nulls).
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR1) AS titleStatusCode,
         max(D_EXT_ATTR1) AS regionalPublicationDate
  FROM items_ext
  WHERE ATTR_GROUP_ID = 1417 AND ITEM_CATALOG_GROUP_ID = 10002
  GROUP BY 1
),
ag1401 AS (  -- ISBN-13 + parent PPID (digital)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR4) AS isbn13,
         max(C_EXT_ATTR9) AS parentProductId,
         max(C_EXT_ATTR3) AS sourceItemNumber
  FROM items_ext WHERE ATTR_GROUP_ID = 1401 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1
),
ag1418 AS (  -- ISBN-10 + digital platform (digital)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR1)  AS isbn10,
         max(C_EXT_ATTR25) AS digitalPlatform,
         max(C_EXT_ATTR24) AS nonDisclosureAgreementIndicator
  FROM items_ext WHERE ATTR_GROUP_ID = 1418 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1
),
ag1381 AS (  -- Edition / classifications / delivery / brand / duration (digital)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(CAST(N_EXT_ATTR1 AS BIGINT)) AS editionNumber,
         max(C_EXT_ATTR1)  AS productClassificationLevel1,
         max(C_EXT_ATTR4)  AS productClassificationLevel2,
         max(C_EXT_ATTR10) AS deliveryMethod,
         max(C_EXT_ATTR2)  AS brand,
         max(C_EXT_ATTR5)  AS subBrand,
         max(C_EXT_ATTR3)  AS accessDurationType,
         max(C_EXT_ATTR9)  AS accessDurationQuantity,
         max(C_EXT_ATTR6)  AS accessDurationUnitCode,
         -- course duration (UIFPE-270): the owner's reference query reads the
         -- digital pair from THIS group, slots 8 and 11
         max(C_EXT_ATTR8)  AS courseDurationQuantity,
         max(C_EXT_ATTR11) AS courseDurationUnitCode
  FROM items_ext WHERE ATTR_GROUP_ID = 1381 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1
),
ag1406 AS (  -- Final page count (digital)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(CAST(N_EXT_ATTR1 AS BIGINT)) AS finalTextPageCount
  FROM items_ext WHERE ATTR_GROUP_ID = 1406 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1
),

-- ── org-level fulfillment sources ───────────────────────────────────────
ag800 AS (   -- Sales restriction + imprint (legacy, org-level)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR12) AS sellingRestrictionCode,
         max(C_EXT_ATTR14) AS imprintName,
         max(C_EXT_ATTR10) AS relevanceValue
  FROM items_ext WHERE ATTR_GROUP_ID = 800 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1, 2
),
ag822 AS (   -- Return eligibility classification (org-level, not conditional)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR8) AS returnClassCode
  FROM items_ext WHERE ATTR_GROUP_ID = 822 GROUP BY 1, 2
),
ag1174 AS (   -- Ready for Manufacture: the fulfillment organization's manufacturing-readiness status for the item (for example IF Ready to Print, HELD_QUALITY, NO_COMP, NO_PACK).
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR1) AS readyForManufactureCode
  FROM items_ext WHERE ATTR_GROUP_ID = 1174 GROUP BY 1, 2
),
ag1403_org AS ( -- Imprint (digital, org-level). Same table/group as ag1403 but
                -- grouped at (item, org) grain — ag1403 stays item-level for
                -- owningSubsidiary/ipOwnerCategory, do not merge the two.
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR4) AS imprintName
  FROM items_ext WHERE ATTR_GROUP_ID = 1403 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1, 2
),
ag1414 AS (  -- Sales restriction (digital, org-level)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR2) AS sellingRestrictionCode
  FROM items_ext WHERE ATTR_GROUP_ID = 1414 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1, 2
),
ag1173 AS (  -- Discount + regional publication date (legacy, org-level)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR4) AS discountCode,
         max(D_EXT_ATTR1) AS regionalPublicationDate
  FROM items_ext WHERE ATTR_GROUP_ID = 1173 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1, 2
),
ag1411 AS (  -- Discount + product tax classification (digital, org-level)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR1) AS discountCode,
         max(C_EXT_ATTR4) AS productTaxClassificationCode
  FROM items_ext WHERE ATTR_GROUP_ID = 1411 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1, 2
),
ag1171 AS (  -- Title status (legacy, org-level)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR1) AS titleStatusCode,
         max(C_EXT_ATTR2) AS backOrderIndicator
  FROM items_ext WHERE ATTR_GROUP_ID = 1171 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1, 2
),
ag755 AS (   -- Qualification level (org-level)
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR2) AS qualificationLevelName
  FROM items_ext WHERE ATTR_GROUP_ID = 755 GROUP BY 1, 2
),
ag1170 AS (  -- Product tax classification (legacy, org-level)
             -- NEW group 2026-08-17: (item, org) grain asserted from the
             -- sibling pattern (1171/1173) — verify on the first load.
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR1) AS productTaxClassificationCode
  FROM items_ext WHERE ATTR_GROUP_ID = 1170 AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1, 2
),
ag1413 AS (  -- Relevance value (digital, org-level)
             -- NEW group 2026-08-17: grain asserted from the sibling pattern
             -- (1411/1414) — verify on the first load.
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         max(C_EXT_ATTR1) AS relevanceValue
  FROM items_ext WHERE ATTR_GROUP_ID = 1413 AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1, 2
),
mkinit AS (  -- Marketing initiatives (1141 — UIFPE-270): org-level, several per
             -- (item, org), so an ARRAY inside fulfillmentOrgDetails. Not
             -- conditional — both catalog sides use this group.
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,
         transform(
           array_sort(collect_list(named_struct(
             's1', C_EXT_ATTR1,
             's2', CAST(D_EXT_ATTR1 AS DATE),
             'p', named_struct(
               'marketingInitiativeName', C_EXT_ATTR1,
               'marketingInitiativeStartDate',           CAST(D_EXT_ATTR1 AS DATE),
               'marketingInitiativeEndDate',             CAST(D_EXT_ATTR2 AS DATE)
             )))),
           x -> x.p) AS marketingInitiative
  FROM items_ext
  WHERE ATTR_GROUP_ID = 1141
    AND (C_EXT_ATTR1 IS NOT NULL OR D_EXT_ATTR1 IS NOT NULL OR D_EXT_ATTR2 IS NOT NULL)
  GROUP BY 1, 2
),

-- ── item-level collections (identical for every org row) ────────────────
contrib AS ( -- Contributors (798), sorted by rank (int), contributorId tie-break
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         transform(
           array_sort(collect_list(named_struct(
             's1', CAST(C_EXT_ATTR10 AS INT),
             's2', C_EXT_ATTR9,
             'p', named_struct(
               'contributorId',    C_EXT_ATTR9,
               'contributionRoleCode',             C_EXT_ATTR1,
               'contributorSortSequence',             CAST(C_EXT_ATTR10 AS INT),
               'contributorPreNominalTitle',         C_EXT_ATTR2,
               'contributorGivenName',        C_EXT_ATTR3,
               'contributorAdditionalName',   C_EXT_ATTR4,
               'contributorFamilyName',       C_EXT_ATTR5,
               'contributorPostNominalTitle',        C_EXT_ATTR6,
               'affiliationType1', C_EXT_ATTR11,
               'affiliation1',     C_EXT_ATTR12,
               'affiliationType2', C_EXT_ATTR13,
               'affiliation2',     C_EXT_ATTR14,
               'affiliationType3', C_EXT_ATTR15,
               'affiliation3',     C_EXT_ATTR16
             )))),
           x -> x.p) AS contributors
  FROM items_ext WHERE ATTR_GROUP_ID = 798 GROUP BY 1
),
wcs AS (     -- Web consuming systems (1121) — a struct array {wcsName, endDate}
             -- since UIFPE-270; C_EXT_ATTR1 + D_EXT_ATTR1 (ECI_RELEASE_DATE).
  -- C_EXT_ATTR1, NOT C_EXT_ATTR11 (intake typo, corrected 2026-08-06): ATTR11
  -- is null for every row of group 1121. Do not "fix" it back.
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         transform(
           array_sort(collect_list(named_struct(
             's1', C_EXT_ATTR1,
             's2', CAST(D_EXT_ATTR1 AS DATE),
             'p', named_struct('wcsName', C_EXT_ATTR1,
                               'endDate', CAST(D_EXT_ATTR1 AS DATE))))),
           x -> x.p) AS webConsumingSystem
  FROM items_ext
  -- wcsName is REQUIRED (owner, 2026-09-08): a release date with no system name
  -- is not a consuming system. Dropped 319 such elements from the first load.
  WHERE ATTR_GROUP_ID = 1121 AND C_EXT_ATTR1 IS NOT NULL
  GROUP BY 1
),
prog AS (    -- Associated program PPIDs (1421), sorted value list
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         array_sort(collect_list(C_EXT_ATTR1)) AS associatedPrograms
  FROM items_ext WHERE ATTR_GROUP_ID = 1421 GROUP BY 1
),
mktg AS (    -- Marketing copy references (1181), sorted by type, audience, geoVisibility
             -- (manifest array_sort_keys) — wrapper struct mirrors the contrib CTE's
             -- multi-key pattern so the named_struct's own field order (OUTPUT shape)
             -- stays unchanged. Residual risk: entries sharing all three still tie.
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         transform(
           array_sort(collect_list(named_struct(
             's1', C_EXT_ATTR3,
             's2', C_EXT_ATTR4,
             's3', C_EXT_ATTR5,
             'p', named_struct(
               'contentId',     C_EXT_ATTR1,
               'marketingCopyText',   C_EXT_ATTR2,
               'marketingCopyTypeCode',          C_EXT_ATTR3,
               'audienceCode',      C_EXT_ATTR4,
               'geoVisibilityCode', C_EXT_ATTR5,
               'owningMarketCode',  C_EXT_ATTR7,
               -- {content_url_base} is a config sql_var (per-env, UIFPE-253);
               -- null contentId gets no URL rather than a /null link
               'contentUrl',    CASE WHEN C_EXT_ATTR1 IS NOT NULL
                                  THEN concat('{content_url_base}/marketing-copy/', C_EXT_ATTR1)
                                END
             )))),
           x -> x.p) AS marketingCopy
  -- No null filter (removed 2026-08-10, review D3). Unlike the value lists,
  -- collect_list of a struct WOULD keep all-null entries — but group 1181
  -- has zero null-contentId rows in dev, and the mapping owner confirmed
  -- null source values map as-is. Dropped to keep SQL ≡ intake (parity gate).
  FROM items_ext WHERE ATTR_GROUP_ID = 1181 GROUP BY 1
),
-- Enabling technology (UIFPE-270): (level1, level2) pairs, several per item,
-- conditional by catalog side (881 legacy / 1407 digital). collect_set — the
-- same pair recorded twice is one capability.
et881 AS (
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         transform(
           array_sort(collect_set(named_struct(
             's1', C_EXT_ATTR1,
             's2', C_EXT_ATTR2,
             'p', named_struct('enablingTechnologyLevel1', C_EXT_ATTR1, 'enablingTechnologyLevel2', C_EXT_ATTR2)))),
           x -> x.p) AS enablingTechnology
  FROM items_ext
  WHERE ATTR_GROUP_ID = 881 AND ITEM_CATALOG_GROUP_ID <> 10002
    AND (C_EXT_ATTR1 IS NOT NULL OR C_EXT_ATTR2 IS NOT NULL)
  GROUP BY 1
),
et1407 AS (
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         transform(
           array_sort(collect_set(named_struct(
             's1', C_EXT_ATTR1,
             's2', C_EXT_ATTR2,
             'p', named_struct('enablingTechnologyLevel1', C_EXT_ATTR1, 'enablingTechnologyLevel2', C_EXT_ATTR2)))),
           x -> x.p) AS enablingTechnology
  FROM items_ext
  WHERE ATTR_GROUP_ID = 1407 AND ITEM_CATALOG_GROUP_ID = 10002
    AND (C_EXT_ATTR1 IS NOT NULL OR C_EXT_ATTR2 IS NOT NULL)
  GROUP BY 1
),
isbn_owner AS ( -- ISBN-13 → owning product PPID, for parentProduct's ELSE
                -- branch (parent named by ISBN). Built over ALL products via
                -- items_all — the `items` view is entity-restricted in Phase
                -- 2 (program's items_all lesson) — using the same
                -- digital/legacy branch pick as the isbn13 column itself.
                -- min(ppid) collapses duplicate owners DETERMINISTICALLY: an
                -- unordered pick would churn channel hashes every run (A8).
  SELECT isbn13, min(productId) AS productId
  FROM (
    SELECT COALESCE(dig.isbn13, leg.isbn13) AS isbn13, ia.SEGMENT1 AS productId
    FROM items_all ia
    LEFT JOIN ag1401 dig ON CAST(ia.INVENTORY_ITEM_ID AS BIGINT) = dig.iid
    LEFT JOIN ag741  leg ON CAST(ia.INVENTORY_ITEM_ID AS BIGINT) = leg.iid
    WHERE ia.SEGMENT1 LIKE 'A%'
  ) AS owners
  WHERE isbn13 IS NOT NULL
  GROUP BY isbn13
),

-- ── instructorResourceFiles (754 PSO_FILE_PROPERTIES, item-level, multi-row) ─
-- UIFPE-476. Instructor Resource Content — the downloadable assets an
-- instructor gets with a product. One-to-many per item (owner-confirmed
-- 2026-09-20).
--
-- TWO TABLES joined on EXTENSION_ID, the EAV ROW key, NOT on the item id.
-- Eight fields are C_/N_ slots in the base table; fileDescription is a TL slot
-- in the translations table. Every other TL read in this file is single-row and
-- uses max() + GROUP BY item — doing that here would give every file in the
-- array the SAME description.
--
-- Sorted by (fileRankSequence, fileName) per the manifest's array_sort_keys.
-- Note on why the tie-breaker is here: array_sort with no comparator orders
-- structs lexicographically over EVERY field, the `p` payload included, so the
-- order is already total without it. fileName is declared anyway so the
-- intended business order is STATED rather than left to whichever payload field
-- happens to differ first - which would otherwise change silently if the
-- payload's field order ever moved.
--
-- No null filter, following marketingCopy rather than webConsumingSystem:
-- collect_list of a struct DOES keep all-null entries, so if group 754 turns
-- out to carry rows with neither a name nor a URL they will appear as empty
-- elements. Confirm against dev data and add a filter only on a business rule,
-- not on taste.
irc754 AS (
  -- Columns are qualified with the REGISTERED alias names, not short local
  -- ones: validate_silver_sources attributes a column by matching
  -- `alias.COLUMN`, and falls back to unqualified attribution only for a block
  -- reading a single source. With `b.`/`t.` this block resolved nothing, so
  -- EXTENSION_ID was required on neither alias and the preflight could not
  -- check the join key exists. Verbose, and worth it.
  SELECT CAST(items_ext.INVENTORY_ITEM_ID AS BIGINT) AS iid,
         transform(
           array_sort(collect_list(named_struct(
             's1', CAST(items_ext.N_EXT_ATTR1 AS INT),
             's2', items_ext.C_EXT_ATTR1,
             'p', named_struct(
               'fileName',          items_ext.C_EXT_ATTR1,
               'fileExtensionCode', items_ext.C_EXT_ATTR2,
               -- verbatim text: the source column is character-typed and the
               -- unit is not guaranteed, so nothing is cast or converted
               'fileSize',          items_ext.C_EXT_ATTR3,
               'linkText',          items_ext.C_EXT_ATTR4,
               'fileUrl',           items_ext.C_EXT_ATTR5,
               'mediaFileType',     items_ext.C_EXT_ATTR6,
               'mediaStatus',       items_ext.C_EXT_ATTR7,
               -- N_EXT_ATTR1 is decimal(38,10) in Silver; INT for numeric
               -- ordering, the same cast contributors uses for its sequence
               'fileRankSequence',  CAST(items_ext.N_EXT_ATTR1 AS INT),
               'fileDescription',   items_ext_tl.TL_EXT_ATTR1)))),
           x -> x.p) AS instructorResourceFiles
  FROM items_ext
  LEFT JOIN items_ext_tl
         ON CAST(items_ext.EXTENSION_ID AS BIGINT)
          = CAST(items_ext_tl.EXTENSION_ID AS BIGINT)
        AND items_ext_tl.ATTR_GROUP_ID = 754
        AND items_ext_tl.LANGUAGE = 'US'
  WHERE items_ext.ATTR_GROUP_ID = 754
  GROUP BY 1
),

-- ── categories (item-level, master-org, allow-listed sets) ──────────────
-- Business mapping 2026-08-31, derived from the two business extract queries.
-- Three tables behind one column: the ASSIGNMENT (MTL_ITEM_CATEGORIES) says
-- which categories a product carries; MTL_CATEGORIES_B/_TL supply the code and
-- the label; MTL_CATEGORY_SET_VALID_CATS supplies the parent link the
-- hierarchy string is built from.
--
-- Scope is pinned by category set NAME, in cat_set_scope below and nowhere
-- else. EPT, POA and Private Pay (POA) are the business-confirmed scope for
-- this flow (2026-08-31). Dozens of other sets exist in the catalog — 132,048
-- US category labels across all of them — so the narrowing is deliberate, not
-- incidental. Adding or removing a set is a one-line edit there, reviewed in
-- the PR that makes it, and a test asserts the list so scope cannot widen
-- unnoticed.
cat_set_scope AS (
  SELECT CAST(CATEGORY_SET_ID AS BIGINT) AS sid,
         max(CATEGORY_SET_NAME)          AS categorySetName
  FROM category_sets
  WHERE CATEGORY_SET_NAME IN ('EPT', 'POA', 'Private Pay (POA)')
  GROUP BY 1
),
master_org AS (
  -- Categories are read from the MASTER org only (the business extract pinned
  -- ORGANIZATION_ID = 82) and then apply to every org row for the item — so
  -- this column is item-level, like contributors, not org-level like ag800.
  -- 82 is the DEV master org id; the portable rule is the org that is its own
  -- master, which is what MTL_PARAMETERS actually asserts (manifest,
  -- 2026-08-06). min() makes a multi-master install deterministic rather than
  -- fanning the join out; it would also be the point to revisit if one ever
  -- appears.
  SELECT min(CAST(ORGANIZATION_ID AS BIGINT)) AS oid
  FROM org_params
  WHERE CAST(ORGANIZATION_ID AS BIGINT) = CAST(MASTER_ORGANIZATION_ID AS BIGINT)
),
cat_tl AS (  -- US category label. max() dedupes defensively; (category_id,
             -- language) is unique upstream.
  SELECT CAST(CATEGORY_ID AS BIGINT) AS cid,
         max(DESCRIPTION)            AS categoryName
  FROM categories_tl WHERE LANGUAGE = 'US' GROUP BY 1
),
cat_label AS (
  -- categoryCode is SEGMENT1 — the BUSINESS code. The business extract aliases
  -- it "CATEGORY_ID", which collides with the surrogate CATEGORY_ID in the very
  -- same SELECT; Gold keeps them apart as categoryCode vs categoryId. Do not
  -- re-merge them.
  -- isIncludeInECatalog / disableDate are attributes of the MAPPED category
  -- (business request 2026-08-31), carried through verbatim: ATTRIBUTE8 is the
  -- source Y/N flag, not a boolean, and Gold does not reinterpret source data.
  -- disableDate is cast to DATE to match regionalPublicationDate and
  -- digitalEffectiveStartDate; the source column is a timestamp whose time
  -- part carries no business meaning.
  SELECT CAST(c.CATEGORY_ID AS BIGINT) AS cid,
         max(c.SEGMENT1)               AS categoryCode,
         max(t.categoryName)           AS categoryName,
         max(c.ATTRIBUTE8)             AS ecatalogIncludeIndicator,
         CAST(max(c.DISABLE_DATE) AS DATE) AS categoryDisabledDate
  FROM categories_b c
  LEFT JOIN cat_tl t ON CAST(c.CATEGORY_ID AS BIGINT) = t.cid
  GROUP BY 1
),
cat_edge AS (  -- child -> parent, scoped to the set (a category can sit at a
               -- different depth in a different set)
  SELECT CAST(CATEGORY_SET_ID AS BIGINT)         AS sid,
         CAST(CATEGORY_ID AS BIGINT)             AS cid,
         max(CAST(PARENT_CATEGORY_ID AS BIGINT)) AS pid
  FROM category_valid_cats GROUP BY 1, 2
),
cat_hier AS (
  -- The hierarchy string, computed ONCE per (set, category) — a few thousand
  -- rows — then joined to assignments. Root first, ' > ' separated, inclusive
  -- of the mapped category: "Engineering > Mechanics > Aerodynamics".
  --
  -- SEPARATOR IS ' > ', NOT ' / ' — measured decision, 2026-08-31. The
  -- business writes these paths with slashes, but 106 of the 2,944 in-scope
  -- categories (3.6%; 104 in EPT, 2 in Private Pay) carry a '/' INSIDE their
  -- own US label — "DC / AC Circuits", "CAD / CAM / CIM", "ESL / EAL",
  -- "Financial & Managerial Accounting (50/50)". A slash separator therefore
  -- renders paths that lie about their own depth: the real 3-level path
  -- Business & Economics > Marketing > "Public Relations / Marketing" comes
  -- out as four slash-separated parts with "Marketing" appearing twice, and
  -- any consumer splitting on ' / ' silently gets the wrong levels. '>' is
  -- absent from ALL 132,048 US category labels in the catalog (not just the
  -- three in-scope sets), so it stays unambiguous even if scope widens.
  -- Verified against dev, query in qe/README.md. Do not "restore" the slash.
  --
  -- Spark has no recursive CTE, so the walk is a FIXED six-level chain
  -- (mapped + 5 ancestors). concat_ws skips nulls, so one expression serves
  -- every depth: a category two levels deep leaves l2..l5 null and yields
  -- "root > mapped" with no CASE ladder. nullif() turns the all-null case back
  -- into NULL, because concat_ws returns '' rather than NULL there.
  --
  -- DEPTH MEASURED 2026-08-31 (dev): max depth is 3 — EPT 57/429/2031 at
  -- depths 1/2/3, POA 349 all flat, Private Pay 13/36/29 — and ZERO
  -- categories at depth 4-7. The six-level chain has three levels of
  -- headroom. Re-check with the histogram if a new set is added to
  -- cat_set_scope: a chain deeper than six loses its TOP levels and still
  -- emits a well-formed string, so it fails quietly rather than loudly.
  SELECT e0.sid,
         e0.cid,
         nullif(concat_ws(' > ',
                  l5.categoryName, l4.categoryName, l3.categoryName,
                  l2.categoryName, l1.categoryName, l0.categoryName), '')
           AS categoryHierarchyPath
  FROM cat_edge e0
  LEFT JOIN cat_edge e1 ON e1.sid = e0.sid AND e1.cid = e0.pid
  LEFT JOIN cat_edge e2 ON e2.sid = e1.sid AND e2.cid = e1.pid
  LEFT JOIN cat_edge e3 ON e3.sid = e2.sid AND e3.cid = e2.pid
  LEFT JOIN cat_edge e4 ON e4.sid = e3.sid AND e4.cid = e3.pid
  LEFT JOIN cat_edge e5 ON e5.sid = e4.sid AND e5.cid = e4.pid
  LEFT JOIN cat_label l0 ON l0.cid = e0.cid
  LEFT JOIN cat_label l1 ON l1.cid = e1.cid
  LEFT JOIN cat_label l2 ON l2.cid = e2.cid
  LEFT JOIN cat_label l3 ON l3.cid = e3.cid
  LEFT JOIN cat_label l4 ON l4.cid = e4.cid
  LEFT JOIN cat_label l5 ON l5.cid = e5.cid
),
cats AS (
  -- Sorted by (categorySetName, categoryId) per the manifest array_sort_keys.
  -- That pair is PROVABLY unique per Gold row — the assignment's own key is
  -- (item, org, set, category) — so unlike marketingCopy this array carries no
  -- tie-break risk and no residual hash instability.
  --
  -- The label joins are LEFT: an assignment whose category is missing from
  -- MTL_CATEGORIES_B, or absent from the set's valid-category list, still
  -- lands with its categoryId so it stays traceable. Gold does not silently
  -- drop a row the source asserts.
  SELECT ic.iid,
         transform(
           array_sort(collect_list(named_struct(
             's1', ic.categorySetName,
             's2', ic.cid,
             'p', named_struct(
               'categorySetName',     ic.categorySetName,
               'categoryCode',        ic.categoryCode,
               'categoryName',        ic.categoryName,
               'categoryHierarchyPath',           ic.categoryHierarchyPath,
               'categoryId',          ic.cid,
               'ecatalogIncludeIndicator', CAST(ic.ecatalogIncludeIndicator AS BOOLEAN),
               'categoryDisabledDate',         ic.categoryDisabledDate
             )))),
           x -> x.p) AS categories
  FROM (
    -- DISTINCT is the dedupe. With the org pinned to the master, (item, set,
    -- category) is already unique upstream — this makes a duplicated Silver
    -- row a no-op instead of a duplicate array element, which would move the
    -- channel hash on every run.
    SELECT DISTINCT
           CAST(a.INVENTORY_ITEM_ID AS BIGINT) AS iid,
           s.categorySetName                   AS categorySetName,
           CAST(a.CATEGORY_ID AS BIGINT)       AS cid,
           l.categoryCode                      AS categoryCode,
           l.categoryName                      AS categoryName,
           h.categoryHierarchyPath                         AS categoryHierarchyPath,
           l.ecatalogIncludeIndicator               AS ecatalogIncludeIndicator,
           l.categoryDisabledDate                       AS categoryDisabledDate
    FROM item_categories a
    JOIN cat_set_scope s  ON CAST(a.CATEGORY_SET_ID AS BIGINT) = s.sid
    JOIN master_org m     ON CAST(a.ORGANIZATION_ID AS BIGINT) = m.oid
    LEFT JOIN cat_label l ON CAST(a.CATEGORY_ID AS BIGINT) = l.cid
    LEFT JOIN cat_hier h  ON CAST(a.CATEGORY_SET_ID AS BIGINT) = h.sid
                         AND CAST(a.CATEGORY_ID AS BIGINT)     = h.cid
  ) ic
  GROUP BY 1
),

-- ── BOM component structures (item-level, master-org primary bills) ───────
-- UIFPE-269. Master-org bills only (99.99% of dev bills) so one item has one
-- component list for every org row; primary structures only. UDA groups are
-- pinned by id (1184/1185) because EGO_ATTR_GROUPS_V is not in Silver.
-- Each column carries this query as its derivation block (ADR-0008).
bom_struct AS (
  SELECT CAST(STRUCTURE_TYPE_ID AS BIGINT) AS stid,
         max(STRUCTURE_TYPE_NAME)          AS structureTypeName
  FROM bom_structure_types GROUP BY 1
),
bom_hdr AS (
  -- Components hang off the COMMON bill's sequence id when the bill
  -- references one (Oracle BOM's canonical join).
  SELECT CAST(b.ASSEMBLY_ITEM_ID AS BIGINT)  AS iid,
         CAST(COALESCE(b.COMMON_BILL_SEQUENCE_ID, b.BILL_SEQUENCE_ID) AS BIGINT) AS bsid,
         s.structureTypeName                 AS structureTypeName
  FROM bom_bills b
  JOIN bom_struct s ON CAST(b.STRUCTURE_TYPE_ID AS BIGINT) = s.stid
  JOIN master_org m ON CAST(b.ORGANIZATION_ID AS BIGINT) = m.oid
  WHERE s.structureTypeName IN ('Pearson Standard', 'PEARSON_CUSTOM')
    AND b.ALTERNATE_BOM_DESIGNATOR IS NULL
),
comp_items AS ( -- component item id → its PPID; SEGMENT1 is org-invariant, so
                -- max() over items_all (every org) is the same value
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, max(SEGMENT1) AS componentProductId
  FROM items_all GROUP BY 1
),
bom_comp AS (
  SELECT h.iid, h.structureTypeName, h.bsid,
         CAST(c.COMPONENT_SEQUENCE_ID AS BIGINT) AS csid,
         ci.componentProductId                                 AS componentProductId,
         CAST(c.ITEM_NUM AS INT)                 AS sequenceNumber,
         CAST(c.COMPONENT_QUANTITY AS DECIMAL(38,2)) AS componentQuantity,
         CAST(c.EFFECTIVITY_DATE AS DATE)        AS componentEffectiveDate,
         CAST(c.DISABLE_DATE AS DATE)            AS componentDisabledDate
  FROM bom_hdr h
  JOIN bom_components c ON CAST(c.BILL_SEQUENCE_ID AS BIGINT) = h.bsid
  JOIN comp_items ci   ON CAST(c.COMPONENT_ITEM_ID AS BIGINT) = ci.iid
),
bom_uda_std AS (  -- PSON_PACK_COMPONENTS (1184) — one UDA row per component
  SELECT CAST(BILL_SEQUENCE_ID AS BIGINT)      AS bsid,
         CAST(COMPONENT_SEQUENCE_ID AS BIGINT) AS csid,
         max(CAST(N_EXT_ATTR1 AS INT))         AS componentFinalPageCount,
         max(C_EXT_ATTR1)                      AS discountType,
         max(CAST(N_EXT_ATTR3 AS DECIMAL(38,2))) AS discountAmount
  FROM bom_components_ext WHERE ATTR_GROUP_ID = 1184 GROUP BY 1, 2
),
bom_uda_cust AS ( -- PSON_CUSTOM_COMPONENTS (1185)
  SELECT CAST(BILL_SEQUENCE_ID AS BIGINT)      AS bsid,
         CAST(COMPONENT_SEQUENCE_ID AS BIGINT) AS csid,
         max(CAST(N_EXT_ATTR1 AS DECIMAL(38,2))) AS originalWorkPercent,
         max(CAST(N_EXT_ATTR2 AS DECIMAL(38,2))) AS customWorkPercent,
         CAST(max(D_EXT_ATTR1) AS DATE)        AS componentEffectiveDate,
         max(CAST(N_EXT_ATTR3 AS INT))         AS componentFinalPageCount,
         max(CAST(N_EXT_ATTR4 AS DECIMAL(38,2))) AS overridePercent
  FROM bom_components_ext WHERE ATTR_GROUP_ID = 1185 GROUP BY 1, 2
),
std_comp AS ( -- sorted by sequenceNumber (ITEM_NUM — the BOM's own ordering),
              -- ppid tie-break
  SELECT c.iid,
         transform(
           array_sort(collect_list(named_struct(
             's1', c.sequenceNumber,
             's2', c.componentProductId,
             'p', named_struct(
               'componentProductId',              c.componentProductId,
               'sequenceNumber',    c.sequenceNumber,
               'componentQuantity', c.componentQuantity,
               'componentEffectiveDate',     c.componentEffectiveDate,
               'componentFinalPageCount',    u.componentFinalPageCount,
               'discountType',      u.discountType,
               'discountAmount',     u.discountAmount,
               'componentDisabledDate',      c.componentDisabledDate
             )))),
           x -> x.p) AS standardComponents
  FROM bom_comp c
  LEFT JOIN bom_uda_std u ON u.bsid = c.bsid AND u.csid = c.csid
  WHERE c.structureTypeName = 'Pearson Standard'
  GROUP BY 1
),
cust_comp AS ( -- sorted by ppid, effectiveDate tie-break (the structure has no
               -- sequence column — the tie-breaker the 2026-08-17 intake
               -- reserved for a duplicate-ppid first load)
  SELECT c.iid,
         transform(
           array_sort(collect_list(named_struct(
             's1', c.componentProductId,
             's2', u.componentEffectiveDate,
             'p', named_struct(
               'componentProductId',                c.componentProductId,
               'originalWorkPercent', u.originalWorkPercent,
               'customWorkPercent',   u.customWorkPercent,
               'componentEffectiveDate',       u.componentEffectiveDate,
               'componentFinalPageCount',      u.componentFinalPageCount,
               'overridePercent',  u.overridePercent,
               'componentDisabledDate',        c.componentDisabledDate
             )))),
           x -> x.p) AS customComponents
  FROM bom_comp c
  LEFT JOIN bom_uda_cust u ON u.bsid = c.bsid AND u.csid = c.csid
  WHERE c.structureTypeName = 'PEARSON_CUSTOM'
  GROUP BY 1
)

SELECT
  CAST(items.INVENTORY_ITEM_ID AS BIGINT)      AS inventoryItemId,
  CAST(items.ORGANIZATION_ID  AS BIGINT)       AS organizationId,
  CAST(items.ITEM_CATALOG_GROUP_ID AS BIGINT) AS itemCatalogGroupId,
  items.SEGMENT1                               AS productId,
  COALESCE(ag1401.isbn13,                  ag741.isbn13)                   AS isbn13,
  COALESCE(ag1418.isbn10,                  ag741.isbn10)                   AS isbn10,
  COALESCE(ag1401.sourceItemNumber, ag741.sourceItemNumber) AS sourceItemNumber,
  -- Digital: the parent PPID direct (1401). Else: 1164's parent ISBN-13
  -- resolved through isbn_owner. A strict branch pick like every COALESCE
  -- here: ag1401 holds only =10002 rows, ag1164 only <>10002 rows.
  COALESCE(ag1401.parentProductId,           powner.productId)                    AS parentProductId,
  -- Org dimension lookup (MTL_PARAMETERS via the orgs CTE). An org absent
  -- upstream yields NULL here — the same result the literal map gave for an
  -- unmapped id, so an unknown org still lands rather than dropping the row.
  orgs.organizationCode                        AS organizationCode,
  items.DESCRIPTION                            AS description,
  tl.longDescription                           AS longDescription,
  items.INVENTORY_ITEM_STATUS_CODE             AS inventoryItemStatus,
  items.ITEM_TYPE                              AS itemType,
  ag802.lineOfBusiness                         AS lineOfBusiness,
  COALESCE(ag1403.ipOwnerCategory,         ag1162.ipOwnerCategory)         AS ipOwnerCategory,
  COALESCE(ag1408.copyrightYear,           ag821.copyrightYear)            AS copyrightYear,
  COALESCE(ag1381.editionNumber,           ag820.editionNumber)            AS editionNumber,
  COALESCE(ag1403.owningSubsidiaryCountryCode, ag1162.owningSubsidiaryCountryCode) AS owningSubsidiaryCountryCode,
  COALESCE(ag1403.owningSubsidiaryName,        ag1162.owningSubsidiaryName)        AS owningSubsidiaryName,
  COALESCE(ag1381.deliveryMethod,          ag821.deliveryMethod)           AS deliveryMethod,
  COALESCE(ag1381.brand,                   ag820.brand)                    AS brand,
  COALESCE(ag1381.subBrand,                ag820.subBrand)                 AS subBrand,
  COALESCE(ag1418.digitalPlatform,         ag821.digitalPlatform)          AS digitalPlatform,
  ag802.disciplineLevel1                       AS disciplineLevel1,
  ag802.disciplineLevel2                       AS disciplineLevel2,
  ag802.disciplineLevel3                       AS disciplineLevel3,
  ag802.disciplineLevel4                       AS disciplineLevel4,
  COALESCE(ag1381.productClassificationLevel1, ag820.productClassificationLevel1) AS productClassificationLevel1,
  COALESCE(ag1381.productClassificationLevel2, ag820.productClassificationLevel2) AS productClassificationLevel2,
  ag802.languageLevel1                               AS languageLevel1,
  ag802.languageLevel2                         AS languageLevel2,
  ag802.learnerStageLevel1                           AS learnerStageLevel1,
  ag802.learnerStageLevel2                     AS learnerStageLevel2,
  CAST(COALESCE(ag1418.nonDisclosureAgreementIndicator,                 ag821.nonDisclosureAgreementIndicator) AS BOOLEAN) AS nonDisclosureAgreementIndicator,
  CAST(ag1261.digitalEffectiveStartDate AS DATE) AS digitalEffectiveStartDate,
  ag1261.digitalVersionNumber                  AS digitalVersionNumber,
  -- Dimensions/weight: scale 2, business decision 2026-08-06. NOT integer,
  -- though the intake sheet said so: 55.5% of non-null UNIT_WEIGHT values are
  -- fractional (2,425,748 rows in dev), so an integer cast truncates real data.
  -- Scale 2 is a deliberate precision choice and DOES round: 44.9% of
  -- unitWeight and 21-23% of the other three carried more than 2 dp at source.
  -- 99998/99999 (Oracle's TBD/N-A numeric sentinels) arrive here as NULL since
  -- UIFPE-268 — the owner reversed the 2026-08-06 pass-through decision.
  -- Rounding is explicit rather than relying on cast-narrowing semantics.
  CAST(round(items.UNIT_WEIGHT, 2) AS DECIMAL(38,2)) AS unitWeightQuantity,
  items.WEIGHT_UOM_CODE                        AS weightUomCode,
  CAST(round(items.UNIT_LENGTH, 2) AS DECIMAL(38,2)) AS unitLengthQuantity,
  CAST(round(items.UNIT_WIDTH,  2) AS DECIMAL(38,2)) AS unitWidthQuantity,
  CAST(round(items.UNIT_HEIGHT, 2) AS DECIMAL(38,2)) AS unitHeightQuantity,
  items.DIMENSION_UOM_CODE                     AS dimensionUomCode,
  ag755.qualificationLevelName                     AS qualificationLevelName,
  COALESCE(ag1406.finalTextPageCount,          ag818.finalTextPageCount)           AS finalTextPageCount,
  -- All-fields-null => the struct itself is NULL (business rule 2026-08-07):
  -- named_struct always constructs a value, so without this gate a fully
  -- empty struct serializes to consumers as {"sellingRestrictionCode": null,
  -- ...} instead of "fulfillmentOrgDetails": null. Verified 2026-08-07: no
  -- field ever holds a literal 'null' STRING (75-check scan, all zero) — this
  -- is purely the empty-container shape. Partial structs keep per-field nulls.
  CASE WHEN COALESCE(ag1414.sellingRestrictionCode, ag800.sellingRestrictionCode) IS NULL
        AND COALESCE(ag1411.discountCode,           ag1173.discountCode) IS NULL
        AND COALESCE(
              CASE WHEN orgs.organizationCode LIKE '01%'
                   THEN ag1417.titleStatusCode ELSE ag1408.titleStatusCode END,
              ag1171.titleStatusCode) IS NULL
        AND COALESCE(
              CASE WHEN orgs.organizationCode LIKE '01%'
                   THEN ag1417.regionalPublicationDate ELSE ag1408.regionalPublicationDate END,
              ag1173.regionalPublicationDate) IS NULL
        AND ag822.returnClassCode IS NULL
        AND COALESCE(ag1403_org.imprintName, ag800.imprintName) IS NULL
        AND COALESCE(ag1411.productTaxClassificationCode, ag1170.productTaxClassificationCode) IS NULL
        AND COALESCE(ag1413.relevanceValue, ag800.relevanceValue) IS NULL
        AND ag1171.backOrderIndicator IS NULL
        AND items.DEFAULT_SO_SOURCE_TYPE IS NULL
        AND mkinit.marketingInitiative IS NULL
        AND ag1174.readyForManufactureCode IS NULL
       THEN NULL
       ELSE named_struct(
    'sellingRestrictionCode', COALESCE(ag1414.sellingRestrictionCode, ag800.sellingRestrictionCode),
    'discountCode',           COALESCE(ag1411.discountCode,           ag1173.discountCode),
    -- Digital side proliferates by org group. UK membership comes from the org
    -- dimension's code prefix; a NULL code (org absent upstream) is not true,
    -- so it takes the non-UK branch — identical to an id missing from the
    -- literal IN-list this replaces.
    'titleStatusCode',
      COALESCE(
        CASE WHEN orgs.organizationCode LIKE '01%'
             THEN ag1417.titleStatusCode ELSE ag1408.titleStatusCode END,
        ag1171.titleStatusCode),
    'regionalPublicationDate',
      CAST(COALESCE(
        CASE WHEN orgs.organizationCode LIKE '01%'
             THEN ag1417.regionalPublicationDate ELSE ag1408.regionalPublicationDate END,
        ag1173.regionalPublicationDate) AS DATE),
    'returnClassCode', ag822.returnClassCode,
    'imprintName',     COALESCE(ag1403_org.imprintName, ag800.imprintName),
    'productTaxClassificationCode',
      COALESCE(ag1411.productTaxClassificationCode, ag1170.productTaxClassificationCode),
    'relevanceValue', COALESCE(ag1413.relevanceValue, ag800.relevanceValue),
    -- Array inside the struct (UIFPE-270): [] rather than NULL when the struct
    -- exists for other reasons — the entity's arrays-are-never-NULL rule.
    'marketingInitiative', COALESCE(mkinit.marketingInitiative, array()),
    -- LAST, after the array (UIFPE-354): Iceberg's nested ADD COLUMN APPENDS
    -- and cannot position, so a member declared mid-struct would sit here
    -- physically anyway — and nested field order is hashed, unlike top-level
    -- order which is sorted. Declaring them anywhere else makes the refresh
    -- (stored order) and the incremental (declared order) disagree forever.
    'backOrderIndicator', CASE
    WHEN ag1171.backOrderIndicator IN ('Yes', 'YES', 'yes', 'Y', 'y') THEN true
    WHEN ag1171.backOrderIndicator IN ('No', 'NO', 'no', 'N', 'n') THEN false
    ELSE NULL
  END,
    -- Straight off the driving table: already (item, org) grain, no join
    'defaultSalesOrderSourceTypeCode', CAST(items.DEFAULT_SO_SOURCE_TYPE AS STRING),
    'readyForManufactureCode', ag1174.readyForManufactureCode
  ) END                                        AS fulfillmentOrgDetails,
  COALESCE(ag1381.accessDurationType,  ag773.accessDurationType)  AS accessDurationType,
  COALESCE(ag1381.accessDurationQuantity, ag773.accessDurationQuantity) AS accessDurationQuantity,
  COALESCE(ag1381.accessDurationUnitCode,  ag773.accessDurationUnitCode)  AS accessDurationUnitCode,
  -- Course duration (UIFPE-270): the owner's reference query — digital items
  -- read PSON_EXTER_PROD_CLASSIFICATION (1381) slots 8/11, everything else
  -- PSON_COURSE_DURATION (1341) slots 1/2. Strict branch pick as above.
  COALESCE(ag1381.courseDurationQuantity, ag1341.courseDurationQuantity) AS courseDurationQuantity,
  COALESCE(ag1381.courseDurationUnitCode,  ag1341.courseDurationUnitCode)  AS courseDurationUnitCode,
  -- Cover image URL normalization (business rule 2026-08-06), applied on top
  -- of the digital/legacy branch pick. '/A...' paths are DAM asset paths and
  -- get the DAM base; site-relative '/bigcovers...' (Large) / '/covers...'
  -- (Regular — business-specified; matches 0 rows in dev today) get the
  -- images host; absolute URLs (incl. images.pearsoned-ema.com, which the
  -- business rule lists explicitly) and everything else pass through
  -- unchanged via ELSE. The TBD (3.5M) / N/A (0.4-0.6M) string sentinels
  -- that used to pass through here are NULL at the view since UIFPE-268.
  -- HighRes differs: it has NO legacy URL form, so anything not a DAM path
  -- becomes NULL (1,886 of 275,884 non-null dev values). NULL input falls
  -- through every branch to NULL output.
  CASE
    WHEN substring(COALESCE(ag1405.coverImageLargeUrl, ag1168.coverImageLargeUrl), 1, 2) = '/A'
      THEN concat('https://images.pearson.com/i/79475d28-e560-442f-9d0c-afceca8a145a',
                  COALESCE(ag1405.coverImageLargeUrl, ag1168.coverImageLargeUrl))
    WHEN COALESCE(ag1405.coverImageLargeUrl, ag1168.coverImageLargeUrl) LIKE '/bigcovers%'
      THEN concat('https://images.pearson.com',
                  COALESCE(ag1405.coverImageLargeUrl, ag1168.coverImageLargeUrl))
    ELSE COALESCE(ag1405.coverImageLargeUrl, ag1168.coverImageLargeUrl)
  END                                          AS coverImageLargeUrl,
  CASE
    WHEN substring(COALESCE(ag1405.coverImageRegularUrl, ag1168.coverImageRegularUrl), 1, 2) = '/A'
      THEN concat('https://images.pearson.com/i/79475d28-e560-442f-9d0c-afceca8a145a',
                  COALESCE(ag1405.coverImageRegularUrl, ag1168.coverImageRegularUrl))
    -- '/coverimage%', corrected from the business rule's original '/covers%'
    -- (2026-08-07): '/covers' matched 0 dev rows; the real site-relative shape
    -- on Regular is '/coverimage/<isbn>.jpg' — 1,567,297 rows.
    WHEN COALESCE(ag1405.coverImageRegularUrl, ag1168.coverImageRegularUrl) LIKE '/coverimage%'
      THEN concat('https://images.pearson.com',
                  COALESCE(ag1405.coverImageRegularUrl, ag1168.coverImageRegularUrl))
    ELSE COALESCE(ag1405.coverImageRegularUrl, ag1168.coverImageRegularUrl)
  END                                          AS coverImageRegularUrl,
  CASE
    WHEN substring(COALESCE(ag1405.coverImageHighResolutionUrl, ag1168.coverImageHighResolutionUrl), 1, 2) = '/A'
      THEN concat('https://images.pearson.com/i/79475d28-e560-442f-9d0c-afceca8a145a',
                  COALESCE(ag1405.coverImageHighResolutionUrl, ag1168.coverImageHighResolutionUrl))
    ELSE NULL
  END                                          AS coverImageHighResolutionUrl,
  COALESCE(contrib.contributors, array())      AS contributors,
  COALESCE(wcs.webConsumingSystem, array())    AS webConsumingSystem,
  COALESCE(prog.associatedPrograms, array())          AS associatedPrograms,
  COALESCE(mktg.marketingCopy, array())        AS marketingCopy,
  -- BOM component structures — populated since UIFPE-269 (see the bom_*
  -- CTEs); [] not NULL for an item with no bill, the entity's rule.
  COALESCE(std_comp.standardComponents, array()) AS standardComponents,
  COALESCE(cust_comp.customComponents, array())  AS customComponents,
  COALESCE(cats.categories, array())           AS categories,
  -- 2026-09-07 batch (UIFPE-270), appended: output_columns is the physical
  -- order, so new columns go on the end rather than moving the ones before.
  COALESCE(et1407.enablingTechnology, et881.enablingTechnology, array())
                                               AS enablingTechnology,
  COALESCE(tl1406.productUrl, tl1165.productUrl)             AS productUrl,
  -- UIFPE-476, appended for the same reason as the batch above: output_columns
  -- is the physical order, so a new column goes on the end.
  COALESCE(irc754.instructorResourceFiles, array())
                                               AS instructorResourceFiles
FROM items
LEFT JOIN orgs    ON CAST(items.ORGANIZATION_ID AS BIGINT) = orgs.oid
LEFT JOIN ag802   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag802.iid
LEFT JOIN ag1162  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1162.iid
LEFT JOIN ag1403  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1403.iid
LEFT JOIN ag821   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag821.iid
LEFT JOIN ag1408  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1408.iid
LEFT JOIN ag1417  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1417.iid
LEFT JOIN ag741   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag741.iid
LEFT JOIN ag1164  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1164.iid
LEFT JOIN ag1401  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1401.iid
LEFT JOIN ag1418  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1418.iid
LEFT JOIN ag820   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag820.iid
LEFT JOIN ag1381  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1381.iid
LEFT JOIN ag818   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag818.iid
LEFT JOIN ag1406  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1406.iid
LEFT JOIN ag800   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag800.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag800.oid
LEFT JOIN ag822   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag822.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag822.oid
LEFT JOIN ag1174   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1174.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag1174.oid
LEFT JOIN ag1403_org ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1403_org.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag1403_org.oid
LEFT JOIN ag1414  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1414.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag1414.oid
LEFT JOIN ag1173  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1173.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag1173.oid
LEFT JOIN ag1411  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1411.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag1411.oid
LEFT JOIN ag1171  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1171.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag1171.oid
LEFT JOIN ag1170  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1170.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag1170.oid
LEFT JOIN ag1413  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1413.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag1413.oid
LEFT JOIN ag755   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag755.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = ag755.oid
LEFT JOIN ag1261  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1261.iid
LEFT JOIN ag773   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag773.iid
LEFT JOIN ag1168  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1168.iid
LEFT JOIN ag1405  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1405.iid
LEFT JOIN tl      ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = tl.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = tl.oid
LEFT JOIN contrib ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = contrib.iid
LEFT JOIN wcs     ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = wcs.iid
LEFT JOIN prog    ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = prog.iid
LEFT JOIN mktg    ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = mktg.iid
LEFT JOIN isbn_owner powner ON ag1164.parentIsbn13 = powner.isbn13
-- Item-level: the master-org assignment applies to every org row for the item.
LEFT JOIN cats    ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = cats.iid
LEFT JOIN ag1341  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag1341.iid
LEFT JOIN tl1165  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = tl1165.iid
LEFT JOIN tl1406  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = tl1406.iid
LEFT JOIN et881   ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = et881.iid
LEFT JOIN et1407  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = et1407.iid
LEFT JOIN mkinit  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = mkinit.iid
              AND CAST(items.ORGANIZATION_ID AS BIGINT) = mkinit.oid
-- Item-level: master-org primary bills apply to every org row for the item.
LEFT JOIN std_comp  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = std_comp.iid
LEFT JOIN cust_comp ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = cust_comp.iid
-- Item-level: group 754 is keyed on the item, so its files apply to every
-- org row for that item — the webConsumingSystem shape, not the org-level one.
LEFT JOIN irc754  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = irc754.iid
-- Row domain: products only ('A%' — 'P%' is a program, UIFPE-62), and no
-- DRAFT / ERROR / DELETE / RETIRED (UIFPE-271) and no TEMPLATE (2026-09-15:
-- Oracle item templates carry the bare 'A' prefix and no catalog group).
-- BOTH predicates MUST match the manifest's sot_filter: the transform only
-- upserts, so a row LEAVING this domain is deleted by reconcile, which
-- applies sot_filter. Diverge and rows leak or get wrongly deleted.
WHERE items.SEGMENT1 LIKE 'A%'
  AND items.INVENTORY_ITEM_STATUS_CODE NOT IN ('DRAFT', 'ERROR', 'DELETE', 'RETIRED', 'TEMPLATE')
