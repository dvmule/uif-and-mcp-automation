# Review — can the Transform UI regenerate `product.sql`?

| | |
|---|---|
| **Status** | Gaps 1–4 (§4) FIXED on this branch, 2026-08-10 — their tests now run as permanent hard gates. Decisions D1–D4 (§5) answered and implemented 2026-08-10; D5 answered 2026-08-10 and SHIPPED as UIFPE-106 (2026-08-21). SQL items S1–S9 (§6) triaged with D1–D3. |
| **Date** | 2026-08-10 |
| **Tested against** | `feature/UIFPE-105-transform-engine` @ `91aedcf`; fixes landed on the same branch |
| **Scope** | Code review of the product entity's mapping SQL, plus an empirical test of whether the Transform UI's new-table flow reproduces it |
| **Related** | UIFPE-100 (delta compiler) · UIFPE-101 (Transform UI MVP) · UIFPE-104 (join-first intake) · UIFPE-105 (transform engine) · ADR-0003 (incremental CDC) · ADR-0006 (control plane, PR open) |
| **Deliverable merged with this review** | 4 gap tests in [`qe/test_compile_intake_delta.py`](../qe/test_compile_intake_delta.py) — authored as `xfail(strict=True)` contracts, markers removed when the fixes landed |

---

## 1. Why this review exists and what it concluded

The Transform UI offers a first-class "introduce a new table" flow, and the
delta compiler claims to render intake into all four runtime artifacts
deterministically. If both hold, then pointing the wizard at the product
entity and re-declaring its attributes should reproduce the SQL we hand-wrote —
that is the strongest available test of the control plane's core promise.

**It does not reproduce it.** The new-table flow produces no SQL at all (it
raises), and of the 55 intake columns none regenerate byte-identically. The
hand-written [`product.sql`](../src/schemas/mapping/product/v1/sql/product.sql)
is in good shape; the gap is in the UI→compiler path.

Nothing here is a live-pipeline defect. The compiler has never been run with
`--apply` against a real intake, the wizard's Submit button is still stubbed
([`ReviewStep.tsx`](../ui/src/pages/transform/wizard/ReviewStep.tsx)), and no
PR-creation service exists. These are pre-adoption bugs, which is the cheapest
time to have them.

## 2. What was tested, and how to re-run it

The intake envelope was constructed exactly as the wizard's Review step emits
it, populated with all 55 columns from the registered
[`product.mapping.json`](../src/schemas/mapping/product/v1/product.mapping.json) — i.e. the
best-case UI output for "introduce a new table = product".

| Test | Method | Result |
|---|---|---|
| A · New-table flow | envelope targeting `gold_product.product_ui` | **Unhandled `FileNotFoundError`** on `gold/product_ui/v1/master.json`, [`compile_intake_delta.py:483`](../src/apps/scripts/compile_intake_delta.py) |
| B · All 55 columns at the existing entity | per-column classify + write | 24 AUTO · 27 PLAN · **4 raise** |
| C · Byte parity | delete a column's lines from `product.sql`, re-add via the compiler, diff | **0 of 4 identical** |

Test C used four representative AUTO columns: `itemType` (driving, plain),
`unitWeight` (driving + `roundTo2dp`), `learnerStageLevel2` (EAV 802,
item-level), `qualificationLevel` (EAV 755, org-level).

The four gaps below are now permanent tests:

```bash
py -m pytest domains/product/qe/test_compile_intake_delta.py -q
```

Suite state as reviewed: **349 passed, 4 skipped, 4 xfailed** — each
`strict=True`, so a fix would XPASS and fail the build until the marker came
off. That is exactly what happened: the fixes landed on this branch the same
day, the markers are gone, and the tests run as hard gates (plus a fifth —
grain-mismatch refusal when an org-level column targets an item-level
attribute group).

## 3. Column arithmetic (worth stating once)

The mapping's **55** intake columns become **50** SQL output columns: six of
them are dot-notation members of the `fulfillmentOrgDetails` struct, which the
SQL assembles into one column (55 − 6 + 1 = 50). The manifest's
`output_columns` list has 50 entries and **zero dotted keys** —
`fulfillmentOrgDetails` appears once, as the bare struct name. That list drives
the physical DDL column order. Gap 2 below violates this.

Classification of the 55:

| Class | Count | Compiler behaviour |
|---|---|---|
| `driving` (scalar from `MTL_SYSTEM_ITEMS_B`) | 11 | AUTO — SQL written |
| `eav:<group>` | 13 | AUTO — SQL written |
| `plan:conditional` | 25 | PLAN — text patch plan only |
| `plan:lookup-join` | 2 | PLAN — text patch plan only |
| `plan:nested` | 4 | raises before any artifact is written |

**56% of the entity can only produce instructions for a human.** That may be
the correct design — see §7.

## 4. Compiler gaps (engineering, no decisions needed) — ALL FIXED 2026-08-10

Ranked by blast radius. Gaps 1 and 2 wrote plausible-looking SQL with wrong
semantics, which is more dangerous than gaps 3 and 4 raising. All four fixes
landed on this branch; the sections below are kept as the record of what the
tests now guard. Fix summary:

| Gap | Fix |
|---|---|
| 1 | `sql_add_eav` derives the CTE shape from the declared join's key set: org-level gets an `oid` projection, `GROUP BY 1, 2`, a two-key join, and an org-section anchor (ag822). Extending an existing CTE whose grain contradicts the declared join is refused (`ag1403`/`ag1403_org` precedent). |
| 2 | A dotted name compiles as a member OF its struct: leaf alias in the CTE, `named_struct` entry + all-null-gate clause in one pass, a `when:` row in the struct's lineage + its `column_types` STRUCT<>, nested master property — never a top-level column or DDL entry. (Landed without the S2 refactor: the gate and struct are edited together atomically, which addresses the two-site consistency risk S2 was sequenced to remove. S2 stays open as maintainability.) **Follow-up (same day):** the first fix regressed CONDITIONAL dotted members — `KeyError: 'source'`, five of the six registered members are conditional, and the fix's test had used a synthetic scalar (the same blind-spot class as the original gaps). The manifest struct-member path now emits one `when:` row per branch (`discountCode / 10002` shape); a **real-entity census test** compiles all 55 registered columns and pins 24 AUTO / 31 PLAN / 0 crashes, so synthetic-fixture blind spots are now a structural impossibility for this compiler. |
| 3 | `master_prop` grew the array branch (value-array vs struct-array per the registered encodings); nested columns also get `array_sort_keys` + `column_types` manifest entries (ADR-0003). |
| 4 | Refusal half: `compile_delta` refuses an unregistered entity by name, listing the missing artifacts and pointing at D4; the wizard's new-table step says so too. The scaffolder itself remains D4. |

Fixing gap 2 also surfaced and fixed a latent UIFPE-100 bug: the manifest
entry regexes used `(?ms)`, so with DOTALL the block match ran to end-of-file
— a manifest `modify` would have replaced everything below the entry.

### Gap 1 — declared grain is silently discarded 🔴 FIXED

`classify()` reduces every EAV column to `eav:<group>`, throwing away the
intake's join keys, and `sql_add_eav()` then hardcodes the item-level shape.

`qualificationLevel` declares
`(INVENTORY_ITEM_ID, ORGANIZATION_ID)`, and `derive_grain()` reports
`"org-level, joined on (INVENTORY_ITEM_ID, ORGANIZATION_ID)"` correctly — the
information reaches the compiler and is dropped inside it. Observed output:

```sql
ag755 AS (
  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
         max(C_EXT_ATTR2) AS qualificationLevel
  FROM items_ext WHERE ATTR_GROUP_ID = 755 GROUP BY 1
),
...
LEFT JOIN ag755  ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = ag755.iid
```

Hand-written: `GROUP BY 1, 2`, an `oid` projection, and a two-key join.
**Effect:** `max()` collapses across all orgs for an item and that one value
fans out to every org row. Wrong values, no error, no test failure.

Also affects `fulfillmentOrgDetails.returnClass` (group 822).

**Scope of fix:** the *extend-an-existing-group* path is fine — it edits inside
the CTE, so `ag822`'s `GROUP BY 1, 2` survives (there is a passing regression
guard for this). Only the new-group branch of `sql_add_eav()` plus
`classify()`'s return value need to change.

**Test:** `TestOrgLevelEavGrain::test_new_org_level_group_keeps_the_org_key`

### Gap 2 — struct members produce invalid SQL and a spurious DDL column 🔴 FIXED

The wizard explicitly permits dot notation
([`types.ts` `columnProblems`](../ui/src/lib/types.ts): *"dot-notation allowed
for nested-object fields"*), and six product columns use it. The compiler
treats the dotted string as an ordinary column name:

```sql
         max(C_EXT_ATTR8) AS fulfillmentOrgDetails.returnClass
      ag822.fulfillmentOrgDetails.returnClass    AS fulfillmentOrgDetails.returnClass
```

An unquoted dotted alias is not a valid Spark identifier, and
`ag822.fulfillmentOrgDetails.returnClass` parses as nested field access on a
string column. (Inferred from Spark identifier rules — not executed against a
cluster.) There is no `named_struct` assembly and no all-null gate. The
compiler also adds a 51st `output_columns` entry, which would create a bogus
DDL column.

Note the contrast: `manifest_attr_block()` reports this same column's grain
*correctly* as org-level. The manifest is right while the SQL is wrong.

**Suggested sequencing:** fix this *after* SQL suggestion S2 (§6). Today the
all-null gate duplicates all six member expressions, so a text-editing
compiler must patch two places consistently. Collapse them into one CTE first.

**Test:** `TestStructMemberColumn::test_dotted_name_becomes_a_struct_field`

### Gap 3 — nested columns raise before anything is written 🔴 FIXED

`master_prop()` does `JSON_TYPE[col["dataType"]]` unconditionally, but the
schema's `nestedColumn` has no `dataType` (element types live on the fields).
All four array columns raise `KeyError: 'dataType'` at
[`compile_intake_delta.py:255`](../src/apps/scripts/compile_intake_delta.py),
contradicting the module docstring's promise that PLAN columns still get the
three structured artifacts updated automatically.

Small fix: an array branch in `master_prop()`. `manifest_attr_block()` already
handles nested correctly.

**Test:** `TestNestedColumnArtifacts::test_nested_column_updates_the_structured_artifacts`

### Gap 4 — the new-table flow has no compiler counterpart 🔴 FIXED (refusal half; scaffolder = D4)

`compile_delta()` reads all four artifacts up front, so a new entity dies in a
dict comprehension. Six wizard steps lead to an envelope the compiler cannot
accept.

The test accepts **either** resolution, so this need not block:

- scaffold the four artifacts, or
- raise `ValueError`/`NotImplementedError` naming the entity, paired with a UI
  gate on `isNewTable` ([`DestinationStep.tsx`](../ui/src/pages/transform/wizard/DestinationStep.tsx)).

The clean-refusal path is a small change. The real scaffolder is not — see §5.

**Test:** `TestNewTableIntake::test_new_table_scaffolds_or_refuses_cleanly`

### Why 349 tests were green

Every existing delta-compiler test uses a synthetic item-level scalar
(`testAttr`, `C_EXT_ATTR33`). Nothing covered an org-level EAV column, a nested
column, a dotted struct member, or a new-table intake — precisely the four
failure classes.

### Why byte-identity fails even for the 24 AUTO columns

Separate from the gaps above, and mostly by design:

| Cause | Detail |
|---|---|
| Position | `_append_select_expr` always appends last; all four test columns landed after `marketingCopy`. Correct for *adding*, structurally incapable of *reproducing* an ordered file. |
| `AS` alignment | Compiler always pads to column 45. The hand-written final SELECT uses 8 distinct positions (47 for plain columns; 30/31/35/40/54/75/82 for wrapped expressions). |
| CTE placement | A new CTE is inserted after `ag802` regardless of grain, not in the org-level section. |
| Comments | Compiler writes the `businessDescription`; the file carries curated comments (`-- Qualification level (org-level)`). |
| Filter whitespace | Intake declares `LANGUAGE='US'`, SQL has `LANGUAGE = 'US'`. Free-text filters cannot round-trip. |

## 5. Decisions needed before anyone writes code

These are not bugs to assign. They need an owner's answer. **D1–D4 were
answered by the mapping owner (Reyas) on 2026-08-10** and implemented on this
branch; **D5 shipped as UIFPE-106 on 2026-08-21**.

| # | Question | Who | Answer (2026-08-10) |
|---|---|---|---|
| D1 | `ag1417` filters on `C_EXT_ATTR1 IS NOT NULL` (titleStatusCode) but the CTE also supplies `regionalPublicationDate`. Is a UK item's publication date *meant* to disappear when its title status is null? See S1 below. | Business owner | The filter's intent was "proliferate only when 01B has a title status" — but copying a null 01B value to UK orgs is safe, so **remove the filter**. Implemented; dev impact: 2,390 items recover a pub date, 46 shift, titleStatusCode unchanged. |
| D2 | What should a `coverImageLarge` value like `/foo/x.jpg` become? Today it ships as a relative path. See S4. | Business owner | **Pass-through is the expectation** — working as intended, no change. |
| D3 | Four semantic predicates exist only in the SQL, not the intake (§6, S5). ADR-0006 D3 makes the intake canonical, which argues for back-porting them plus a mapping↔SQL parity gate. Confirm before changing a registered artifact. | Architecture (Raj/Satya) | Mapping owner: null source values map as-is — **remove the SQL-only predicates** (SQL now ≡ intake) rather than back-port. Parity gate added (`test_eav_cte_predicates_are_declared_in_the_intake`). Verified: the three array filters were no-ops in dev (collect_list skips null scalars; group 1181 has zero null-contentId rows). |
| D4 | Build the new-table scaffolder, or gate the flow? A real scaffolder touches ADR-0003 invariants — `key_mapping`, channel hashes, `array_sort_keys`, tier subsets, DDL, DynamoDB pipeline config — and ADR-0006 D5's `platform/` placement is still open. Multi-day, own ticket. | Architecture + PM | ~~Gate it for now~~ **RESOLVED by UIFPE-260** (framework-owner decision 2026-09-04): the scaffolder compiles a single-source scalar entity end to end — all four artifacts + pipeline base + dev overlay from the intake, with `drivingFilter` (SQL WHERE ≡ manifest `sot_filter`) and entity-key GROUP BY aggregates. Joins, nested/conditional columns and windows on a NEW table stay engineer work (refused by name). First consumer: stock (UIFPE-261). |
| D5 | Is the AUTO/PLAN split the target, or a shortfall? See §7. | PM + architecture | **Staged middle path** (framework owner, 2026-08-10): conditionals + lookup-joins become AUTO — **UIFPE-106**, sequenced after this branch merges. Nested arrays and struct assembly stay PLAN **by design** (engineering review is the contract there, not a shortfall). *Shipped 2026-08-21: 48 of 62 registered columns AUTO. The "51 of 55" estimate predated the 2026-08-17 batch and three carve-outs the implementation made explicit: the 7 conditional struct MEMBERS stay PLAN with struct assembly (the named_struct + all-null gate are hand-written), and parentProduct stays PLAN as a declared MULTI-HOP derivation (UIFPE-153 notes convention).* |

## 6. Review of `product.sql` itself

The file is good. Every non-obvious decision carries dated business rationale
with row counts (55.5% fractional weights, 1,567,297 `/coverimage` rows, the
`C_EXT_ATTR1` vs `C_EXT_ATTR11` typo correction). The strict-branch-pick
contract is stated *and* enforced by construction. The `WHERE items.SEGMENT1
LIKE 'A%'` filter documents its coupling to the manifest `sot_filter` and the
reconcile-delete failure mode. The `ag1403`/`ag1403_org` "do not merge these"
note pre-empts exactly the refactor someone would otherwise attempt. No
security surface — static SQL, no interpolation.

Shape: 31 CTEs, 29 of which scan `items_ext`; 31 `LEFT JOIN`s; 50 output
columns.

| # | Line | Finding | Category |
|---|---|---|---|
| S1 | 143 | `ag1417`'s `C_EXT_ATTR1 IS NOT NULL` filters on titleStatusCode, but the CTE also supplies regionalPublicationDate. A UK item with a real pub date and a null title status is dropped from `ag1417`; the `COALESCE` falls through to `ag1173`, which is empty for digital items → pub date silently NULL. `ag1408` has no equivalent filter, and the intake declares neither. **Needs D1.** | 🟠 Correctness |
| S2 | 339–370 | The `fulfillmentOrgDetails` all-null gate re-computes all six COALESCE expressions (including the nested UK `CASE`) a second time inside `named_struct`. An edit to one copy and not the other makes gate and struct disagree. Compute once in a CTE, then gate. Also unblocks Gap 2. | Maintainability |
| S3 | 382–413 | `COALESCE(ag1405.coverImage*, ag1168.coverImage*)` appears **12 times**; the DAM GUID base URL **3 times**. Extract a `covers` CTE and hoist the URL — this is the "lookups & rule-sets" backlog item in miniature. | Maintainability |
| S4 | 382–394 | `coverImageLarge` normalizes `/A…` and `/bigcovers…`; `coverImageRegular` normalizes `/A…` and `/coverimage…`. A Large value starting with any other `/` falls through `ELSE` as a site-relative path — unusable, and indistinguishable from the intentional TBD/N-A passthrough. **Needs D2.** | 🟠 Robustness |
| S5 | 143, 253, 258, 279 | Four predicates exist only in the SQL: `C_EXT_ATTR1 IS NOT NULL` on `ag1417`, `wcs` (1121), `prog` (1421), `mktg` (1181). The intake declares none. Regenerating from intake would put null entries into `webConsumingSystem`/`programPpid`/`marketingCopy` — different **data**, and different channel hashes. `test_gold_mapping_intake_parity.py` only checks mapping↔master, so CI cannot see this. **Needs D3.** | 🔴 Drift |
| S6 | throughout | 29 separate `items_ext` scans. Two conditional-aggregation passes (one `GROUP BY iid`, one `GROUP BY iid, oid`) would collapse most of them. Real money on a daily 6.8M-row job — but it trades away the one-CTE-per-group readability that makes this file reviewable. **Measure before committing.** | Performance |
| S7 | 416–468 | `CAST(items.INVENTORY_ITEM_ID AS BIGINT)` repeated in all 31 join predicates. Project it once in an `items_k` CTE. Mainly readability; Spark's CSE likely absorbs the runtime cost. | Maintainability |
| S8 | 469 | `SEGMENT1 LIKE 'A%'` applies only at the end, so all 29 EAV aggregations also compute for P/T/N items (~0.8% of rows). A semi-join would prune it. Minor. | Performance |
| S9 | 284–300 | `contrib`'s sort ties when `(rank, contributorId)` both repeat → nondeterministic array order → unstable channel hashes. The same residual risk is documented for `mktg` but not here. Add the note or a third tie-break. | Correctness (low) |

Side effect of S5 worth flagging: because the wizard reads registered
attributes from `product.mapping.json`, a business user opening
`titleStatusCode` in the modify flow sees the version **without** the `ag1417`
predicate. Round-tripping that attribute through the UI would silently drop it.

## 7. The question underneath all of this

A compiler that renders 24 mechanical shapes correctly and hands an engineer an
exact, anchored patch plan for the 31 hand-tuned ones is a defensible design —
arguably the honest one. Chasing 100% generation for conditionals with scoped
UK proliferation, struct assembly with an all-null gate, and stably-sorted
`named_struct` arrays is a much larger bet than closing gaps 1–4.

Closing gaps 1–3 makes the **AUTO set correct**, which is unambiguously worth
doing and is what the four tests demand. Making conditionals AUTO is a separate
decision (D5), and should be taken deliberately rather than by accretion.

**D5 was taken 2026-08-10 (framework owner): the staged middle path.**
Conditionals and lookup-joins become AUTO in their own story (UIFPE-106) —
the wizard's SQL preview already renders those shapes deterministically, so
the compiler ports a proven contract rather than inventing one. Nested arrays
and struct assembly remain PLAN permanently: four columns of highest
hand-tuned craft and lowest churn, where an engineer applying an anchored
plan IS the design.

## 8. Suggested sequencing

1. ~~**Gaps 1 and 3**~~ ✅ done 2026-08-10.
2. ~~**Gap 4, refusal half**~~ ✅ done 2026-08-10 (compiler refusal + wizard
   note; scaffolder itself = D4).
3. ~~**Gap 2**~~ ✅ done 2026-08-10 — landed without S2 by editing the gate
   and struct atomically in one pass; S2 stays open as a maintainability
   refactor of the hand-written SQL.
4. ~~**D1, D2, D3** answers → S1, S4, S5~~ ✅ answered and implemented
   2026-08-10: `ag1417`/`wcs`/`prog`/`mktg` predicates removed (S1 fixed,
   S4 confirmed as-intended, S5 resolved by deletion) and the mapping↔SQL
   predicate parity gate landed in `test_intake_hygiene.py`. Requires a
   post-deploy `--refresh_columns fulfillmentOrgDetails` (F3) to repair
   historical rows.
5. **S6** only after measurement. S2/S3/S7/S9 remain as a follow-up
   maintainability ticket.
