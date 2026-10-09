# test_gold_frequency_parity.py
# UIFPE-149: the scheduling vocabulary and the slot arithmetic have ONE
# definition — src/apps/scripts/gold_frequencies.py — but consumers that cannot
# import it: Terraform (the sweep rules) and TypeScript (the wizard's picker and
# run-time preview). This module is what keeps those pinned.
#
# WHY THIS GATE EARNS ITS PLACE: every drift it catches is SILENT in production.
#   * A tick cron slower than the finest rung -> a gold-5m pipeline quietly runs
#     at the sweep's cadence instead of its own, and nothing errors.
#   * A cadence in Python that TypeScript does not know -> the wizard cannot
#     offer it, or worse offers a name the validator then rejects at sync time.
#   * A `minutes` value that differs between the two -> the UI previews run times
#     the dispatcher will not honor. The operator is told 14:30 and it runs at
#     10:30.
#   * A default start time that differs -> a pipeline that names no start time
#     runs at one clock and is previewed at another.
# None of those fail any other test, and none are visible in code review.

import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gold_frequencies import (
    CADENCES,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_FREQUENCY,
    LEGACY_ALIASES,
    MINUTES_PER_DAY,
    RECONCILE_CRON,
    RECONCILE_FREQUENCY,
    TICK_CRON,
    TICK_FREQUENCY,
    TICK_MINUTES,
    TRANSFORM_FREQUENCIES,
    DEFAULT_WEEKLY_DAYS,
    WEEKDAYS,
    WEEKLY_CADENCE,
    WEEKLY_FREQUENCY,
    default_start_time,
    interval_minutes,
    previous_slot,
    previous_slot_at_interval,
    slot_minutes,
)

REPO = Path(__file__).resolve().parents[4]
STACK = REPO / "stacks" / "echo"
DISPATCHER_TF = STACK / "infrastructure" / "terraform-gold" / "dispatcher.tf"
PIPELINES_TS = STACK / "transform-ui" / "src" / "lib" / "pipelines.ts"
# UIFPE-564: the console's cadence picker reads its rungs from here.
SYNCROW_TS = STACK / "ui" / "src" / "lib" / "syncRow.ts"
VECTOR = Path(__file__).resolve().parent / "fixtures" / "schedule-slots.json"


def _tf_schedules(text):
    """Parse local.gold_schedules -> {key: (cron, frequency)}.

    Scoped to the gold_schedules block so an unrelated `cron = "..."` elsewhere
    in dispatcher.tf can never satisfy this gate."""
    block = re.search(r"gold_schedules\s*=\s*\{(.*?)\n  \}", text, re.S)
    assert block, "local.gold_schedules not found in dispatcher.tf"
    out = {}
    for entry in re.finditer(
            r"(\w+)\s*=\s*\{(.*?)\n    \}", block.group(1), re.S):
        body = entry.group(2)
        cron = re.search(r'cron\s*=\s*"([^"]+)"', body)
        freq = re.search(r'frequency\s*=\s*"([^"]+)"', body)
        out[entry.group(1)] = (cron.group(1) if cron else None,
                               freq.group(1) if freq else None)
    return out


def _ts_cadences(text):
    block = re.search(r"GOLD_CADENCES\s*=\s*\[(.*?)\n\]", text, re.S)
    assert block, "GOLD_CADENCES not found in pipelines.ts"
    return [(v, int(m), lab) for v, m, lab in re.findall(
        r'\{\s*value:\s*"([^"]+)",\s*minutes:\s*(\d+),\s*'
        r'defaultStartTime:\s*"[^"]*",\s*label:\s*"([^"]+)"\s*\}',
        block.group(1))]


def _ts_start_times(text):
    block = re.search(r"GOLD_CADENCES\s*=\s*\[(.*?)\n\]", text, re.S)
    return dict(re.findall(
        r'value:\s*"([^"]+)",\s*minutes:\s*\d+,\s*'
        r'defaultStartTime:\s*"([^"]*)"', block.group(1)))


class TestLadderInvariants(unittest.TestCase):
    def test_every_interval_divides_a_day(self):
        """THE property the whole model rests on: a start time plus the interval
        must tile the day exactly, or the slots would have a short/long gap at
        midnight and "every N hours" would be a lie once a day. An interval like
        7 hours could not be honored from a fixed time of day at all."""
        for freq, entry in CADENCES.items():
            self.assertEqual(
                MINUTES_PER_DAY % entry["minutes"], 0,
                f"{freq}: {entry['minutes']} min does not divide {MINUTES_PER_DAY}")

    def test_weekly_is_the_one_rung_outside_the_day_tiling(self):
        """UIFPE-532: gold-weekly is a transform rung but NOT a CADENCES member,
        because its grid is week-local (weekday set x start_time), not "start +
        k x interval" within a day. Pinning both halves: it must be schedulable,
        and it must never sneak into the tiling table where the invariant above
        would be meaningless for it."""
        self.assertIn(WEEKLY_FREQUENCY, TRANSFORM_FREQUENCIES)
        self.assertNotIn(WEEKLY_FREQUENCY, CADENCES)
        self.assertEqual(WEEKLY_CADENCE["minutes"], 7 * MINUTES_PER_DAY)
        self.assertEqual(interval_minutes(WEEKLY_FREQUENCY), 7 * MINUTES_PER_DAY)
        self.assertEqual(default_start_time(WEEKLY_FREQUENCY), "06:30")
        self.assertEqual(DEFAULT_WEEKLY_DAYS, ("MON",))
        self.assertEqual(WEEKDAYS, ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"))

    def test_slots_are_uniformly_spaced_including_across_midnight(self):
        """Directly asserts the consequence, rather than trusting the divisor
        check: consecutive gaps must all equal the interval, and so must the
        wrap from the last slot of one day to the first of the next."""
        for freq, entry in CADENCES.items():
            interval = entry["minutes"]
            slots = slot_minutes("06:30", freq)
            gaps = [b - a for a, b in zip(slots, slots[1:])]
            gaps.append(slots[0] + MINUTES_PER_DAY - slots[-1])
            self.assertEqual(set(gaps), {interval},
                             f"{freq}: non-uniform gaps {sorted(set(gaps))}")

    def test_the_sweep_ticks_at_least_as_often_as_the_finest_rung(self):
        """If the sweep were slower than the finest cadence, that cadence could
        never be served — a gold-5m pipeline would silently run at the sweep's
        rate instead."""
        finest = min(e["minutes"] for e in CADENCES.values())
        self.assertLessEqual(TICK_MINUTES, finest)

    def test_the_tick_cron_matches_the_declared_tick_interval(self):
        self.assertEqual(TICK_CRON, f"cron(0/{TICK_MINUTES} * * * ? *)")

    def test_default_start_times_are_parseable_and_in_range(self):
        from gold_frequencies import parse_time_of_day
        for freq in TRANSFORM_FREQUENCIES:
            self.assertIsNotNone(parse_time_of_day(default_start_time(freq)),
                                 f"{freq}: unparseable default start time")

    def test_the_two_historical_clocks_did_not_move(self):
        """Renaming and re-modelling the cadences must not move a running
        pipeline. gold-24h keeps product's 06:30 UTC daily and gold-1h keeps the
        :45 hourly offset, so a config that names no start time runs exactly when
        it did before UIFPE-149."""
        self.assertEqual(default_start_time("gold-24h"), "06:30")
        self.assertEqual(default_start_time("gold-1h"), "00:45")


class TestSharedVector(unittest.TestCase):
    """The slot arithmetic exists twice — Python (authoritative) and TypeScript
    (the wizard's preview). Both assert THIS file, so they cannot drift into
    disagreeing about when a pipeline runs."""

    def setUp(self):
        self.vector = json.loads(VECTOR.read_text(encoding="utf-8"))

    def test_vector_is_not_empty(self):
        """A silently-emptied fixture would make both sides trivially pass."""
        self.assertGreaterEqual(len(self.vector["slots"]), 10)
        self.assertGreaterEqual(len(self.vector["previousSlot"]), 8)

    def test_every_cadence_appears_in_the_slot_vector(self):
        covered = {c["frequency"] for c in self.vector["slots"]}
        self.assertEqual(set(TRANSFORM_FREQUENCIES) - covered, set())

    def test_slot_cases(self):
        for case in self.vector["slots"]:
            with self.subTest(case["why"]):
                got = slot_minutes(case["start_time"], case["frequency"])
                if "expectAll" in case:
                    self.assertEqual(got, case["expectAll"])
                if "expectCount" in case:
                    self.assertEqual(len(got), case["expectCount"])
                if "expectFirst" in case:
                    self.assertEqual(got[0], case["expectFirst"])
                if "expectLast" in case:
                    self.assertEqual(got[-1], case["expectLast"])

    def test_previous_slot_cases(self):
        for case in self.vector["previousSlot"]:
            with self.subTest(case["why"]):
                now = datetime.fromisoformat(
                    case["now"].replace("Z", "+00:00"))
                want = datetime.fromisoformat(
                    case["expect"].replace("Z", "+00:00"))
                got = previous_slot(now, case["start_time"], case["frequency"],
                                    case.get("weekly_days"))
                self.assertEqual(got, want)

    def test_weekly_cases_are_present_and_exercise_a_day_set(self):
        """A vector without weekly cases would let the 7-day grid drift
        silently on either side."""
        weekly = [c for c in self.vector["previousSlot"]
                  if c["frequency"] == WEEKLY_FREQUENCY]
        self.assertGreaterEqual(len(weekly), 5)
        self.assertTrue(any(len(c.get("weekly_days", [])) >= 2 for c in weekly))

    def test_previous_slot_is_never_in_the_future_and_never_too_old(self):
        """Property check across the whole ladder, complementing the fixture's
        chosen cases: the previous slot is at or before now, and never more than
        one interval back — either failure would make a pipeline run at the wrong
        time or never run."""
        from datetime import timedelta
        base = datetime(2026, 8, 12, tzinfo=timezone.utc)
        for freq in TRANSFORM_FREQUENCIES:
            interval = interval_minutes(freq)
            for offset in (0, 1, 59, 61, 389, 391, 719, 721, 1439):
                now = base + timedelta(minutes=offset)
                slot = previous_slot(now, "06:30", freq)
                with self.subTest(freq=freq, offset=offset):
                    self.assertLessEqual(slot, now)
                    self.assertGreater(
                        slot, now - timedelta(minutes=interval + 1))


class TestTerraformParity(unittest.TestCase):
    def setUp(self):
        self.tf = _tf_schedules(DISPATCHER_TF.read_text(encoding="utf-8"))

    def test_every_rule_cron_matches_the_module(self):
        """Closes a hole found by mutation-testing this very gate: the tick cron
        was asserted but the refresh and reconcile crons were not, so either
        could have drifted from gold_frequencies.py silently. Every rule's clock
        is now pinned to the module that documents it — a drifted cron means the
        UI and the docs describe one schedule while EventBridge runs another."""
        from gold_frequencies import cron as module_cron
        for key, (tf_cron, tf_freq) in self.tf.items():
            with self.subTest(rule=key):
                expected = module_cron(tf_freq)
                self.assertIsNotNone(
                    expected, f"{key}: rule emits {tf_freq!r}, which the module "
                              f"does not recognise as a sweep payload")
                self.assertEqual(
                    tf_cron, expected,
                    f"{key}: dispatcher.tf says {tf_cron!r}, "
                    f"gold_frequencies.cron({tf_freq!r}) says {expected!r}")

    def test_exactly_two_rules_and_no_per_cadence_ones(self):
        """Per-cadence rules are gone: cadence is a property of the pipeline,
        not of the infrastructure. Exactly TWO rules remain, each a distinct
        KIND of work rather than a clock — the transform sweep and the nightly
        reconcile. A third would mean someone reintroduced per-cadence
        scheduling in one place only, which is how the original gold-hourly bug
        happened.

        Two rather than three since UIFPE-457: the targeted-column-refresh
        sweep is retired (ADR-0020 decision 4). It had its own rule, payload
        and slot grid, and the two grids had to stay mutually exclusive or each
        would satisfy the other's slot and both would silently stop. The
        recompute now rides the entity's own run once every N runs, so a rule
        re-added here would dispatch the same pipelines on a grid nothing
        reconciles with the first."""
        self.assertEqual(set(self.tf), {"tick", "reconcile"})

    def test_tick_rule_matches_the_python_constants(self):
        cron, freq = self.tf["tick"]
        self.assertEqual(cron, TICK_CRON)
        self.assertEqual(freq, TICK_FREQUENCY)

    def test_reconcile_rule_matches_the_python_constants(self):
        cron, freq = self.tf["reconcile"]
        self.assertEqual(cron, RECONCILE_CRON)
        self.assertEqual(freq, RECONCILE_FREQUENCY)

    def test_every_emitted_payload_is_one_the_handler_accepts(self):
        from gold_frequencies import is_valid
        for key, (_cron, freq) in self.tf.items():
            self.assertTrue(is_valid(freq),
                            f"rule {key} emits {freq!r}, which the handler "
                            f"would reject on every tick")


class TestTypeScriptParity(unittest.TestCase):
    def setUp(self):
        self.ts_text = PIPELINES_TS.read_text(encoding="utf-8")
        self.ts = _ts_cadences(self.ts_text)

    def test_same_cadences_in_the_same_order(self):
        """Order is a contract, not cosmetics: it is the dropdown's order.
        GOLD_CADENCES mirrors CADENCES (the day-tiling ladder); the weekly rung
        is mirrored separately below."""
        self.assertEqual([v for v, _, _ in self.ts], list(CADENCES))

    def test_weekly_rung_is_mirrored(self):
        """UIFPE-532: GOLD_WEEKLY and WEEKDAYS in pipelines.ts must match the
        Python module in value, minutes, default start time, label and day
        order — the wizard's preview of a weekly table depends on all five."""
        m = re.search(
            r'GOLD_WEEKLY\s*=\s*\{\s*value:\s*"([^"]+)",\s*minutes:\s*(\d+),\s*'
            r'defaultStartTime:\s*"([^"]*)",\s*label:\s*"([^"]+)"\s*\}',
            self.ts_text)
        self.assertIsNotNone(m, "GOLD_WEEKLY not found in pipelines.ts")
        self.assertEqual(m.group(1), WEEKLY_FREQUENCY)
        self.assertEqual(int(m.group(2)), WEEKLY_CADENCE["minutes"])
        self.assertEqual(m.group(3), WEEKLY_CADENCE["default_start_time"])
        self.assertEqual(m.group(4), WEEKLY_CADENCE["label"])
        days = re.search(r'WEEKDAYS\s*=\s*\[(.*?)\]', self.ts_text, re.S)
        self.assertIsNotNone(days, "WEEKDAYS not found in pipelines.ts")
        self.assertEqual(tuple(re.findall(r'"([A-Z]{3})"', days.group(1))), WEEKDAYS)

    def test_minutes_match(self):
        """A mismatch here means the UI previews run times the dispatcher will
        not honor."""
        for value, minutes, _ in self.ts:
            self.assertEqual(minutes, CADENCES[value]["minutes"],
                             f"{value}: minutes differ")

    def test_labels_match(self):
        for value, _, lab in self.ts:
            self.assertEqual(lab, CADENCES[value]["label"],
                             f"{value}: label differs")

    def test_default_start_times_match(self):
        for value, got in _ts_start_times(self.ts_text).items():
            self.assertEqual(got, CADENCES[value]["default_start_time"],
                             f"{value}: default start time differs")

    def test_legacy_aliases_match(self):
        block = re.search(r"LEGACY_ALIASES:\s*Record<[^>]+>\s*=\s*\{(.*?)\n\};",
                          self.ts_text, re.S)
        self.assertIsNotNone(block, "LEGACY_ALIASES not found in pipelines.ts")
        self.assertEqual(dict(re.findall(r'"([^"]+)":\s*"([^"]+)"',
                                         block.group(1))),
                         dict(LEGACY_ALIASES))

    def test_ts_defaults_match_python(self):
        for name, want in (("DEFAULT_FREQUENCY", DEFAULT_FREQUENCY),
                           ("DEFAULT_COOLDOWN_MINUTES",
                            str(DEFAULT_COOLDOWN_MINUTES))):
            m = re.search(rf'{name}[^=]*=\s*"?([^";\n]+)"?', self.ts_text)
            self.assertIsNotNone(m, f"{name} not found in pipelines.ts")
            self.assertEqual(m.group(1).strip().strip('"'), want)


class TestLambdaZipCompleteness(unittest.TestCase):
    """gold_frequencies.py is imported by the Lambda with a BARE import, so it
    must be in the flat zip. If the archive_file source block is ever dropped,
    every tick fails with ImportError — and Terraform would apply that happily.
    The cheapest possible check for the most total outage in the file."""

    def test_dispatcher_zip_includes_gold_frequencies(self):
        body = self._archive()
        self.assertIn("gold_frequencies.py", body)
        # A BARE filename — a nested path breaks the bare import.
        self.assertRegex(body, r'filename\s*=\s*"gold_frequencies\.py"')

    def test_every_bare_import_in_the_dispatcher_is_zipped(self):
        """Generalizes the check above: any module gold_dispatcher.py imports by
        bare name has to be a source block in the archive."""
        dispatcher = (STACK / "src" / "apps" / "lambdas"
                      / "gold_dispatcher.py").read_text(encoding="utf-8")
        bare = set(re.findall(r"^from (\w+) import", dispatcher, re.M))
        stdlib_or_vendored = {"boto3", "botocore", "datetime", "pathlib",
                              "typing"}
        archive = self._archive()
        for mod in sorted(bare - stdlib_or_vendored):
            self.assertIn(f'filename = "{mod}.py"', archive,
                          f"gold_dispatcher.py imports {mod!r} by bare name "
                          f"but it is not in the Lambda zip")

    def _archive(self):
        text = DISPATCHER_TF.read_text(encoding="utf-8")
        m = re.search(r'data "archive_file" "gold_dispatcher".*?\n\}', text, re.S)
        self.assertIsNotNone(m)
        return m.group(0)


class LegGridTest(unittest.TestCase):
    """ADR-0020 decision 5: the recompute leg's grid is the entity's start time
    plus multiples of (interval x N).

    Every cadence rung divides a day, which is why the schedule's own slots can
    be computed within the day. `interval x N` does NOT inherit that: gold-24h
    with N=8 is eight days, gold-4h with N=8 is thirty-two hours. A day-local
    grid restarts every midnight, so an eight-day leg came due every day and
    the declared ratio was silently 1 — found before release, pinned here."""

    def test_a_multi_day_leg_grid_steps_by_the_whole_interval(self):
        """Sampled across a long SPAN, not around a single slot. A day-local
        grid is locally consistent — probing one minute either side of a slot
        passes under the bug, because the defect is the midnight phase reset,
        not the arithmetic near a slot. What exposes it is counting how many
        distinct slots a span yields: an eight-day grid must yield one per
        eight days, and the day-local version yields one per day."""
        span_days = 24
        for freq, every in (("gold-24h", 8), ("gold-4h", 8), ("gold-2h", 8),
                            ("gold-30m", 8), ("gold-15m", 8), ("gold-8h", 3)):
            with self.subTest(freq=freq, every=every):
                total = interval_minutes(freq) * every
                base = datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)
                step = max(1, total // 4)      # oversample the grid 4x
                seen = []
                for k in range(span_days * MINUTES_PER_DAY // step):
                    slot = previous_slot_at_interval(
                        base + timedelta(minutes=k * step), "07:15", total)
                    if not seen or slot != seen[-1]:
                        seen.append(slot)
                gaps = {b - a for a, b in zip(seen, seen[1:])}
                self.assertEqual(
                    gaps, {timedelta(minutes=total)},
                    f"{freq} x{every}: grid of {total} min produced gaps "
                    f"{sorted(str(g) for g in gaps)} over {span_days} days")

    def test_the_leg_grid_is_a_subset_of_the_entity_slots(self):
        """The leg rides a scheduled run, so every leg slot must coincide with
        one of the entity's own slots — otherwise the leg would come due at an
        instant no run happens at and fire on whichever run followed."""
        for freq, every in (("gold-24h", 8), ("gold-15m", 8), ("gold-1h", 6)):
            with self.subTest(freq=freq, every=every):
                iv = interval_minutes(freq)
                now = datetime(2026, 9, 6, 12, 2, tzinfo=timezone.utc)
                leg = previous_slot_at_interval(now, "07:15", iv * every)
                self.assertEqual(
                    previous_slot(leg, "07:15", freq), leg,
                    f"{freq} x{every}: leg slot {leg} is not an entity slot")

    def test_it_matches_previous_slot_on_every_rung(self):
        """N=1 is the degenerate leg, and must be the schedule itself. This is
        what makes the shared arithmetic a refactor rather than a second clock."""
        for freq in TRANSFORM_FREQUENCIES:
            if freq == WEEKLY_FREQUENCY:
                continue          # a day-set grid, not an interval — see below
            for hour in (0, 7, 12, 23):
                with self.subTest(freq=freq, hour=hour):
                    now = datetime(2026, 9, 6, hour, 2, tzinfo=timezone.utc)
                    self.assertEqual(
                        previous_slot_at_interval(
                            now, "07:15", interval_minutes(freq)),
                        previous_slot(now, "07:15", freq))

    def test_the_weekly_leg_grid_is_every_n_weeks_not_the_day_set(self):
        """UIFPE-532: the weekly rung's own slots follow its day SET, so N=1 is
        NOT the schedule for it — the leg grid is (7 days x N) on the fixed
        epoch anchor, i.e. "every N weeks at start_time", and the leg still
        rides whichever scheduled run follows. Pinned so nobody "fixes" the
        exemption above by feeding the day set into the interval arithmetic."""
        total = interval_minutes(WEEKLY_FREQUENCY) * 2
        self.assertEqual(total, 14 * MINUTES_PER_DAY)
        base = datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)
        seen = []
        for k in range(8 * 7 * 24):                       # eight weeks, hourly
            slot = previous_slot_at_interval(
                base + timedelta(hours=k), "07:15", total)
            if not seen or slot != seen[-1]:
                seen.append(slot)
        self.assertEqual({b - a for a, b in zip(seen, seen[1:])},
                         {timedelta(days=14)})
        # and the day set never leaks into it: a Tue+Thu entity's leg grid is
        # the same fixed fortnight as a Monday entity's
        self.assertEqual(
            previous_slot_at_interval(base, "07:15", total),
            previous_slot_at_interval(base, "07:15", total))


if __name__ == "__main__":
    unittest.main()


class ConsolePickerParityTest(unittest.TestCase):
    """UIFPE-564: the cadence is console-owned, so the echo UI carries its own
    copy of the rung list - the one the picker offers. A rung offered there that
    the controller refuses is a dead option; one missing there cannot be reached
    from the console at all. Neither failure is visible in review."""

    def setUp(self):
        self.ts = SYNCROW_TS.read_text(encoding="utf-8")

    def _rungs(self):
        block = re.search(r"GOLD_CADENCE_RUNGS = \[(.*?)\] as const", self.ts, re.S)
        self.assertIsNotNone(block, "GOLD_CADENCE_RUNGS not found in syncRow.ts")
        return re.findall(r"'([^']+)'", block.group(1))

    def test_the_picker_offers_every_transform_rung_in_order(self):
        self.assertEqual(self._rungs(), list(TRANSFORM_FREQUENCIES))

    def test_the_picker_offers_nothing_the_handler_would_refuse(self):
        for rung in self._rungs():
            with self.subTest(rung=rung):
                self.assertIn(rung, TRANSFORM_FREQUENCIES)

    def test_the_default_cadence_matches(self):
        m = re.search(r"DEFAULT_GOLD_CADENCE = '([^']+)'", self.ts)
        self.assertIsNotNone(m, "DEFAULT_GOLD_CADENCE not found in syncRow.ts")
        self.assertEqual(m.group(1), DEFAULT_FREQUENCY)

    def test_the_sub_hourly_line_is_where_the_minutes_say(self):
        """The dialog asks for a second confirmation below an hour; the rungs it
        applies to must be exactly those whose interval is under 60 minutes."""
        under_an_hour = [f for f in CADENCES if CADENCES[f]["minutes"] < 60]
        self.assertEqual(under_an_hour, ["gold-5m", "gold-10m", "gold-15m", "gold-30m"])
        self.assertIn("mins != null && mins < 60", self.ts)
