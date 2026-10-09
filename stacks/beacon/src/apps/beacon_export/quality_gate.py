# quality_gate.py
# Batch-level quality gate, evaluated between the transform and any
# rendering/delivery. Fail-closed: a breach means nothing reaches the pickup
# zone or the partner (design decision D4). Input is the dynamic-mapping
# engine's RunReport dict; this module never touches Spark or S3.

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateResult:
    passed: bool
    breaches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "breaches": self.breaches,
                "warnings": self.warnings}


def evaluate(report: dict, gates: dict,
             previous_output_rows: int | None = None,
             incremental: bool = False) -> GateResult:
    """Evaluate gate rules against a transform run report.

    report: dynamic-mapping RunReport.to_dict() — needs status, output_rows,
            rejected_rows.
    gates:  quality_gates block of the export config (validated upstream).
    previous_output_rows: output_rows of the last successful batch, when the
            caller has one; enables the optional delta check.
    incremental: the transform extracted a changed-rows window (UIFPE-168).
            The min_rows floor and the delta warning are calibrated for full
            snapshots and do not apply — an empty or small increment is a
            legitimate no-change window, not a missing Gold partition. The
            rejection ceiling still applies: bad rows in a small window are
            still an upstream data regression.
    """
    result = GateResult(passed=True)

    status = report.get("status")
    if status != "succeeded":
        result.breaches.append(f"transform status is {status!r}, expected 'succeeded'")

    output_rows = int(report.get("output_rows", 0))
    rejected_rows = int(report.get("rejected_rows", 0))

    min_rows = gates["min_rows"]
    if output_rows < min_rows and not incremental:
        result.breaches.append(
            f"row floor: output_rows={output_rows} < min_rows={min_rows} "
            "(suspect empty or missing Gold partition)")

    total = output_rows + rejected_rows
    if total > 0:
        rejection_pct = 100.0 * rejected_rows / total
        max_pct = float(gates["max_rejection_pct"])
        if rejection_pct > max_pct:
            result.breaches.append(
                f"rejection ceiling: {rejection_pct:.2f}% rejected "
                f"({rejected_rows}/{total}) > max_rejection_pct={max_pct}% "
                "(suspect upstream data regression)")

    delta_warn = gates.get("row_count_delta_pct_warn")
    if delta_warn is not None and previous_output_rows and not incremental:
        delta_pct = 100.0 * abs(output_rows - previous_output_rows) / previous_output_rows
        if delta_pct > float(delta_warn):
            result.warnings.append(
                f"row count moved {delta_pct:.1f}% vs previous successful batch "
                f"({previous_output_rows} -> {output_rows}), warn threshold {delta_warn}%")

    result.passed = not result.breaches
    return result
