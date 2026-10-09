# 0020. Gold Detects Every Source, On Its Own Clock

- Status: Accepted (owner, 2026-09-18)
- Date: 2026-09-18
- Supersedes an earlier unaccepted draft of this same number, which withdrew source-level
  detection in favour of restructuring Silver first. That withdrawal was wrong, and the reasoning
  that produced it is corrected below. Nothing from the earlier draft was ever merged or built.

In plain terms: a Gold table is built by joining several Silver tables together. When one of those
Silver tables changes, Gold has to notice, work out which rows are affected, and rebuild them. It
does that today for some of the tables it reads and not for others — so a change in the wrong place
is invisible until a separate, scheduled job recomputes everything on a timer. This record replaces
that with two small changes: Gold asks every table it reads whether anything changed since Gold
itself last ran, and Gold keeps that "last ran" bookmark on its own clock rather than reading it out
of the data. The timed recompute then has nothing left to repair, and each table goes back to having
one schedule instead of two.

## Context

### What a run has to answer

Every scheduled run answers one question: *which entities changed since I last looked?* It answers
it by scanning each source it reads for rows newer than a stored bookmark, projecting those rows
onto the entity key, and rebuilding the union of what it finds.

### Why a source cannot detect change today

The projection is a per-row expression. It maps a changed row in one source to the entity keys that
row affects, and being per-row, **it cannot join**. A source reachable only through a join therefore
cannot name the entities it affects. Such a source is declared as a lookup, skipped by detection
entirely, and its changes are recovered only by a scheduled recompute of the columns it feeds.

For an entity whose *business data* lives in such sources, that recompute is not a backstop — it is
the only detection path, and it runs on a timer measured in hours.

### Why the bookmark is the second half of the problem

The stored bookmark is the highest source timestamp the previous run happened to see. That couples
Gold's own progress to the contents of someone else's table, and it has a known failure the code
already documents: a row stamped at the boundary instant but committed after Gold read the table is
excluded by every later scan, silently and permanently. A deliberate lag is applied to the lower
bound to turn that gap into an overlap.

The lag is the right mitigation for the wrong design. A bookmark derived from data can also be
dragged backwards by a delete, cannot be reasoned about without reading the data, and must be
computed in the middle of a run — after the sources are registered but before the write — which is
an ordering constraint the code carries a comment to defend.

### The measurements, and what they do and do not say

Taken against a dev environment on the worst-affected entity:

- The run reads **8.7 GB across 291M rows** every time. Restricting it to five affected keys still
  scanned **99.5%** of those bytes.
- Detection is already the cheap part: a watermark scan prunes to **0 bytes** on five of seven
  sources when nothing has changed.
- Every Silver table is unpartitioned and unsorted, holding a handful of very large files, each
  spanning the full range of every join key — so no key predicate can skip a file.
- Two source surrogate keys carried as wide decimals are **53.7%** of the read.

An earlier draft read these as an argument against source-level detection: better detection cannot
shrink the read, therefore it buys nothing. That inference does not hold, and it is the specific
error this record corrects. The measurements show that **detection cannot make an incremental run
cheaper**. They say nothing about the comparison that matters:

| Today, per day, per affected entity | With detection over every source |
|---|---|
| one incremental run — full read, detects blindly | one run — full read, detects everything |
| one recompute run — full read again, to repair what the first missed | — |

Two full reads become one. Better detection is what lets the second run be deleted, which is a cost
reduction of the same magnitude as the read itself, on top of the correctness and latency gain.

## Decision

### 1. The bookmark is the run's own start time

A run records the instant it started and stores that as the bookmark for the next run. It is never
derived from source data. Detection then means exactly what it says: *anything Silver wrote since
Gold last started*.

This removes the per-source bookmark map, the mid-run computation of it and the ordering constraint
that computation imposes, and makes Gold's progress independent of any source's data quality.

**The lag survives, with a defensible bound.** Silver stamps a row when its write begins and commits
it later, so a row stamped just before a run's start can still land after it. The lower bound is
therefore rewound by a lag that must exceed the longest Silver write, and that is now a quantity
which can be measured and stated rather than guessed. Re-reading rows remains free of side effects:
affected entities are rebuilt and the hash gate drops anything that did not actually move.

Bookmarks continue to advance monotonically. A run that processes nothing advances nothing.

### 2. Detection covers every declared source

For each source the entity declares, detection asks whether that source has rows newer than the
bookmark. A source whose changed rows cannot be projected onto the entity key is no longer excluded:
the entity's own query already joins it to the key, so detection restricts that query to rows where
**any** contributing source is newer than the bookmark, and takes the entity keys that survive.

Two properties make this safe rather than clever:

- **The predicate is generated, not authored.** Every source already declares which column carries
  its write time. The framework composes the condition from the declared source list, so a source
  cannot be silently omitted, and a gate asserts that every declared source appears in it.
- **The query must carry the write time through.** A step that groups or aggregates rows discards it
  unless it is carried forward. This is real work in each entity's query and it is the honest cost of
  this decision. A gate that compares the declared source list against the composed predicate is
  what stops a missed step from becoming a silently undetecting source.

The alternative of teaching the per-row projection to traverse joins — declared key paths through the
source graph — is **not built**. It is a new framework concept where a predicate suffices.

### 3. A full load processes everything, and sets the bookmark to its own start time

A full load reads every source in full, writes every entity, and then stores its own start time as
the bookmark. The next incremental therefore begins exactly where the full load began, and nothing
written to Silver during or after it is skipped.

Today a full load stores the highest source timestamp it saw instead, which is both data-derived and
subtly wrong: Silver rows written while the full load was running carry a later stamp than the rows
it read, so they fall below the stored bookmark and are never picked up.

A full load still writes no change-log rows and announces nothing — consumers resynchronise from the
table.

### 4. One schedule per entity

One cadence and one start time per table. Each scheduled run performs, in order: maintenance
schema changes → detection over all sources → the periodic-recompute keyset when due → rebuild of
affected entities → hash gate → change log → merge → announce. An entity selected by more than one
mechanism is rebuilt **once**, through the ordinary rebuild path.

The separate recompute schedule, its own sweep and the dispatcher's separate due-ness for it all
retire. The **manual** column-repair tool survives unchanged, in both its notifying and silent
modes, with its pre-flight refusals: it is the operator's repair, and silent mode is load-bearing
for re-establishing hashes after a physical rename.

Announce semantics are unchanged. A full load and a silent manual repair announce nothing; every
scheduled run announces.

### 5. The clock is the only thing a periodic recompute is still for

Two things change with no Silver write at all: a value defined as "effective now", which moves when
a date boundary passes, and a hard delete, which stamps nothing and so is invisible to any timestamp
scan. One recompute leg inside the single run covers both. It is never skipped by the snapshot gate,
and its column list shrinks to the clock-sensitive and delete-exposed columns — not, as today, every
column fed by an undetectable source.

**The leg fires once every N scheduled runs, N declarable and defaulting to 8.** Expressed as a
ratio rather than as a cadence of its own, so the relationship survives a change to the entity's
cadence: at a fifteen-minute cadence the leg runs every two hours, at thirty minutes every four. The
bound that matters is how long a date-boundary crossing or a delete may go unreflected, and a ratio
keeps that bound proportional to how fresh the entity is meant to be.

Due-ness reuses the existing mechanism rather than inventing one. Recompute slots are the entity's
own start time plus multiples of (interval × N), and the leg is due when its last recorded run
precedes the most recent such slot — the same "slots, not elapsed time" rule the schedule itself
uses, with the same properties: the phase never drifts because it derives from the start time, and a
skipped sweep loses nothing because the next run computes the same slot and fires late rather than
not at all. Never having run counts as due.

Why a ratio and not "every run": the marginal cost of the leg is reading stored Gold for every
entity and hashing it, since the Silver scan and the join are already paid by the ordinary
incremental and only rows whose hash moved are merged. That cost is modest once a day and
significant ninety-six times a day, which is what a fifteen-minute cadence would otherwise mean.

A source that genuinely cannot be joined to the entity at all is declared as reference data, and the
columns it feeds must be covered by this leg. Nothing may be both undetectable and unrepaired, and
that is a checkable property rather than a convention.

**Operational rule.** After any change that moves hashes for a systemic reason rather than a
business one — a physical column rename is the known case — repair the affected columns with the
manual tool in silent mode *before* the next scheduled run. Otherwise the leg observes every hash
as moved and writes a change-log row for every entity. This has happened once, at full table scale,
and had to be purged.

### 6. Silver physical standards and conformance are the follow-on, not the prerequisite

The earlier draft made restructuring Silver a precondition. It is not: decisions 1 to 3 are entirely
within the Gold runtime and depend on no change to Silver. Silver work remains worth doing, on its
own merits and its own timescale:

- **Identifier types normalised at the Silver boundary** — a source surrogate key becomes a
  fixed-width integer rather than the source's wide decimal. Removing the downstream cast is what
  unblocks bucketing, storage-partitioned joins and key pruning.
- **The write-time column must carry file statistics.** A table's metrics limit is finite, and a
  column appended last to a wide table falls outside it — so predicate pruning silently does
  nothing. Declare metrics for it explicitly. **This one is a genuine prerequisite for decision 2 to
  be fast**, though not for it to be correct.
- **Target file size and scheduled compaction, a sort order on the join keys, merge-on-read for
  high-churn tables, and snapshot expiry.** All native to the table format, all absent today.
- **A conformed tier**, where an entity that integrates many raw mirrors reads one table carrying the
  business key with types normalised and grain declared. This makes decision 2's predicate collapse
  to a single term and retires the carry-through cost. It is the strategic destination; it is not
  required to fix detection today.

### 7. A second source system joins an entity as another declared source

A new source supplying rows or collection elements for the same entity — existing keys or new ones —
is declared like any other source and detected like any other source. No special mechanism.

A second Gold pipeline writing the same table stays **prohibited**. Gold rebuilds whole rows, so two
writers erase each other's collection elements, race one table's commits, and produce two partial
stories for one occurrence. One run, one merge, one change log, one announce is what makes "no missed
change, no missed event" checkable.

### 8. The authoring UI represents all of it (later scope, binding on the design)

One cadence control per table; the recompute leg's columns derived from the clock-sensitive set
rather than typed by hand; the composed detection predicate shown as a derived, read-only fact; and
authoring of the query and the attributes through the UI for entities the compiler cannot regenerate,
so "hand-authored" stops meaning "edited in the backend".

## What this does not change

The channel model and per-channel hashes, tier subsets, reconcile's anti-join, the contract artifacts
and their parity gates, ADR-0005's entity-with-tiers shape, and ADR-0018's ownership split. One
collision remains open: a clock-dependent pick cannot satisfy ADR-0008's stability refusal, so the
exemption recorded on 2026-09-18 stands until ADR-0008 is amended or derivation blocks learn declared
volatility.

**It does not fix a stalled source extract.** Where a Silver pipeline's own extract bookmark is stuck,
Silver rewrites every row on every run and stamps them all fresh. Detection then correctly reports
that everything changed, because it did. No Gold bookmark scheme can improve on that; it is a Silver
defect and is tracked separately. The difference this record makes is that Gold becomes honest about
it rather than confused by it.

## Migration

**Every environment is dev and no consumer reads any entity yet.** There is no backward compatibility
to preserve, so this record deliberately carries no compatibility scaffolding — no per-pipeline fold
flags, no soak period, no legacy aliases. In this order:

1. **Bookmark becomes the run start time (decisions 1 and 3).** Runtime only, no contract change, no
   query change. Verifiable by a single run: the stored bookmark equals that run's start.
2. **Declare metrics on the write-time column** for every source table, so decision 2's predicate can
   prune. No contract change and invisible to readers.
3. **Compose the detection predicate and carry the write time through each entity's query
   (decision 2)**, one entity at a time, worst first. Each is proven by showing a change confined to a
   previously undetectable source now rebuilds the right entities on the next scheduled run.
4. **Collapse to one schedule and delete the second sweep (decision 4)**, once every entity's
   undetectable columns are either detected or covered by the recompute leg.
5. **The remaining Silver work (decision 6)** on its own timescale, retiring the carry-through as
   entities adopt a conformed source.

Steps 1 and 2 are independent of every entity's query and can land immediately. Step 4 is the one
that delivers one schedule per table, and it is gated on step 3, not on Silver.

## Alternatives considered

- **Declared key paths through the source graph**, so the per-row projection could traverse joins.
  Correct and uniform, but a new framework concept, a shared-runtime change, new validation and a new
  UI surface — where a generated predicate over the existing query achieves the same detection.
  Rejected on complexity, not on benefit.
- **Restructuring Silver first, and treating detection as unfixable until then** — the earlier draft of
  this record. Rejected because decisions 1 to 3 need no Silver change, and deferring them leaves every
  affected entity paying two full reads a day and detecting changes hours late in the meantime.
- **Anchoring the query on the driving set.** Measured at 36 MB *worse* than the current shape. Worth
  doing eventually as a shuffle and CPU win once the layout permits pruning, but it is not a cost lever
  and was previously presented as one.
- **Snapshot or changelog-based detection.** The correct long-term mechanism and the eventual answer to
  delete blindness, but unsound on copy-on-write sources with very large files, where a one-row change
  returns a whole rewritten file. Revisit after decision 6.
- **Leaving detection as it is and buying more compute.** The cost scales with the size of the source
  estate rather than with change volume, so it grows indefinitely and multiplies across every domain.

## Consequences

Detection becomes complete and the bookmark becomes meaningful, which is what allows the second
schedule to be deleted — halving the daily read on every affected entity and reducing detection
latency from hours to one cadence interval.

The cost is carried in each entity's query, which must propagate the source write time through any
step that groups rows. That is manual, per entity, and would be silent if it were wrong — so the
generated predicate and the gate that checks it against the declared source list are not optional
extras, they are what make the decision safe. The Silver conformed tier retires that cost later, which
is the reason it remains on the roadmap rather than being dropped.
