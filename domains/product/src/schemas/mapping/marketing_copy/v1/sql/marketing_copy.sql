-- Gold Marketing Copy transform — v1 (6 attributes, UIFPE-252, 2026-09-03)
-- 1 row per contentId from XXPSO_EGO_MARKET_COPY_DATA at version 'C' — the
-- content library behind gold_product.product's marketingCopy contentUrl
-- (design: domains/product/docs/design-marketing-copy-content-service.md).
--
-- Domain: the CURRENT copy per contentId — version 'C', not disabled, and a
-- non-null contentId (35 null-key rows live at 2026-09-03 are excluded; a row
-- with no key can never be addressed). The WHERE below MUST stay in sync with
-- the manifest's sot_filter on the market_copy source: the transform only
-- upserts, so a copy leaving this domain (re-versioned, disabled, deleted) is
-- removed by RECONCILE, whose SoT anti-join applies sot_filter.
--
-- DEDUPE: the source carries full-key duplicate rows (4 live at version 'C',
-- 2026-09-03) — row_number() picks the latest by LAST_UPDATE_DATE with
-- RECORD_ID as the tie-break, so the pick is DETERMINISTIC: an unordered
-- "first" could differ between runs on identical data, which would move the
-- canonical hash and ping consumers forever (product's acceptance check A8).
-- The window is keyed on the gold key alone, so duplicates always land in the
-- same partition even under Phase 2's restricted view.
--
-- Watermark contract: the runtime registers the alias below (market_copy) as
-- a temp view — restricted to affected entities on incremental runs. Do not
-- reference Silver tables directly here.
--
-- Output column names MUST exactly match the master schema property names.

WITH ranked AS (
  SELECT CONTENT_ID,
         TYPE,
         OWNING_MARKET,
         TARGET_AUDIENCE,
         COPY_DESC,
         CONTENT,
         row_number() OVER (
           PARTITION BY CONTENT_ID
           -- RECORD_ID stays DECIMAL(38,10): it orders natively, and a CAST
           -- to BIGINT could overflow a 38-digit value (null tie-break under
           -- non-ANSI Spark = nondeterminism sneaking back in)
           ORDER BY LAST_UPDATE_DATE DESC,
                    RECORD_ID DESC
         ) AS rn
  FROM market_copy
  WHERE VERSION = 'C'
    AND (DISABLED_FLAG IS NULL OR DISABLED_FLAG <> 'Y')
    AND CONTENT_ID IS NOT NULL
)

SELECT
  CONTENT_ID       AS contentId,
  TYPE             AS marketingCopyTypeCode,
  OWNING_MARKET    AS owningMarketCode,
  TARGET_AUDIENCE  AS audienceCode,
  COPY_DESC        AS marketingCopyText,
  CONTENT          AS marketingCopyContentText
FROM ranked
WHERE rn = 1
