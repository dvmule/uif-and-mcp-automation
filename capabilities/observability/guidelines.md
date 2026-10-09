# Observability — Guidelines (the worked standard)

This is the detailed, do-this companion to the Observability capability `README.md`. It tells a Forward Deployed Engineer exactly how to name a metric/log field/span/SLO, document a module's telemetry contract, test that signals and alerts actually work, and build the pipeline that ships them — so a live module can be understood, measured, and trusted from day one.

This document follows the same **template shape** as every capability's `guidelines.md`: five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done.

Grounding standards (all public): **OpenTelemetry** (the vendor-neutral standard for metrics, logs, and traces, and their semantic conventions), the **Prometheus exposition format** (the de facto standard `/metrics` shape OTel metrics are commonly scraped as), **W3C Trace Context** (the `traceparent` header that makes a trace propagate across process/service boundaries), the **RED method** (Rate, Errors, Duration — per-request services) and **USE method** (Utilization, Saturation, Errors — resources), and the SRE discipline of **SLI/SLO/error budget** (Google SRE book).

Worked example: `../../domains/product/infrastructure/observability/slo.yaml` — the Product API's SLOs, alert routing, and RED metric names.

---

## 1. Naming

**Metrics.** Dot-namespaced, `<domain>.<component>.<measurement>`, following OpenTelemetry semantic-convention style: `product.api.request.duration`, `product.api.request.count`, `product.api.request.errors`. Units are explicit in the metric's metadata (OTel `unit`), never encoded ambiguously in the name alone.

**Structured log fields.** `camelCase`, matching the API/event field-naming convention already established (`../api/guidelines.md` §1): every log line carries at minimum `timestamp`, `level`, `traceId`, `domain`, `service`, `message`.

**Trace spans.** `<verb> <resource>` for an inbound request span (`POST /developers`, matching OTel's HTTP semantic conventions), `<domain>.<operation>` for an internal span (`product.mapping.apply`).

**SLOs and alerts.** `<domain>-<component>-<slo-type>`, e.g. `product-api-availability`, `product-api-latency-p99`; an alert derived from an SLO's burn rate is named after the SLO it protects, never after the raw metric alone.

---

## 2. Contract documentation (every module declares its signals and its SLOs)

**A module's telemetry is not incidental output — it's a declared contract.** Document it in `slo.yaml` (kept in `domains/<domain>/infrastructure/observability/`):
```yaml
service: product-api
metrics:
  - name: product.api.request.duration
    type: histogram
    unit: ms
  - name: product.api.request.errors
    type: counter
slos:
  - name: product-api-availability
    sli: "1 - (product.api.request.errors / product.api.request.count)"
    target: "99.9%"
    window: 30d
  - name: product-api-latency-p99
    sli: "histogram_quantile(0.99, product.api.request.duration)"
    target: "<= 300ms"
    window: 7d
alerts:
  - name: product-api-availability-burn-rate
    slo: product-api-availability
    burnRateThreshold: "2x over 1h"
    route: "#product-oncall"
```
The full, runnable version is `../../domains/product/infrastructure/observability/slo.yaml`; read the snippet above as its smallest possible slice.

**Trace context propagates across every capability boundary.** An API call that triggers an event, an MCP tool call, or an A2A delegation carries the same `traceId` end to end (`../event/`, `../mcp/guidelines.md` §4, `../a2a/guidelines.md` §4) — this is what makes a cross-capability request debuggable as one trace instead of four disconnected logs.

**Alerts map to SLO burn rate, not a raw threshold.** An alert fires because the error budget is being consumed too fast to survive the window, not because a metric crossed an arbitrary static number picked without reference to a target.

**Pipeline health metrics (the delivery pipeline observes itself).** A domain's runtime signals (above) aren't the only thing worth measuring — `.github/workflows/domain-ci.yml` emits one **CloudWatch EMF** log event per domain per CI run, in the same embedded-metric-format shape AWS defines for structured logs that CloudWatch parses into metrics without a separate `PutMetricData` call:
```json
{
  "_aws": {
    "Timestamp": 1723300000000,
    "CloudWatchMetrics": [{
      "Namespace": "UIF/CI",
      "Dimensions": [["Domain", "Status"]],
      "Metrics": [
        { "Name": "PipelineRun", "Unit": "Count" },
        { "Name": "PipelineDurationSeconds", "Unit": "Seconds" }
      ]
    }]
  },
  "Domain": "product",
  "Status": "success",
  "PipelineRun": 1,
  "PipelineDurationSeconds": 47
}
```
This makes CI failure rate and pipeline duration per domain a queryable metric (a CloudWatch Alarm on `PipelineRun` filtered `Status=failure`, or a p90 on `PipelineDurationSeconds`) instead of something a human has to notice by reading Actions logs. The step is scaffold-safe: it no-ops until a repository variable (`UIF_CI_TELEMETRY_ROLE_ARN`) and the `/uif/ci/pipeline-health` log group are provisioned via Terraform, the same "inert until wired" posture as the Entra ID + Keeper gate in the same workflow.

---

## 3. Testing → automation → build

1. **Signal presence test** — the `/metrics` endpoint exposes every metric declared in `slo.yaml`; a declared metric that never actually emits fails the build.
2. **Trace propagation test** — drive one request across a real capability boundary (API → event, or API → MCP tool) and assert the `traceId` is identical start to end.
3. **Alert rule validation** — the alerting rule (e.g. `promtool check rules`) is syntactically valid and its `burnRateThreshold` references a real, declared SLO.
4. **SLO math check** — the SLI expression in `slo.yaml` is evaluated against a seeded metrics fixture and produces a sane value (not NaN, not always 100%) before the rule ships.
5. **Dashboard smoke test** — the dashboard renders against the metric names actually emitted, not a stale copy from before a rename.
6. **End-to-end / smoke** — after CD, confirm the deployed service's real `/metrics` and trace exports match the declared contract.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching `domains/<domain>/infrastructure/observability/**`:
```
lint (slo.yaml schema)  →  alert rule validation  →  SLO math check  →  signal presence test (against a running instance)  →  publish dashboard/alert config
```

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Observable By Default.** Every module emits standard metrics (RED/USE), structured logs, and distributed traces from its first PR — not retrofitted after an incident.
- **Every capability declares its SLOs.** No module ships without at least one SLO in `slo.yaml`; alerts derive from it, per §2.
- **Telemetry uses shared conventions so signals are comparable across modules.** A dashboard built for one domain's API should read naturally for another's, because the metric/log/span naming (§1) is the same shape everywhere.
- **Trace propagation across API, event, MCP, and A2A hops.** Observability is what makes the multi-capability request patterns in `../event/`, `../mcp/`, `../a2a/` debuggable as single traces — this is a design input to those capabilities, not an afterthought bolted onto them.
- **Feeds JSM operations (`../../jira/jsm-alignment.md`) and the QE Lead's quality bar** (`../../architecture-design/operating-model/four-in-a-box.md`) — an SLO breach is the trigger that turns telemetry into an actionable incident.

---

## 5. Definition of done (Observability)

- [ ] Metric/log-field/span/SLO names follow §1 (dot-namespaced metrics, camelCase log fields, RED/USE-shaped).
- [ ] `slo.yaml` follows §2: declared metrics actually emit, at least one SLO with a target and window, alerts derived from SLO burn rate.
- [ ] Signal presence, trace propagation, alert validation, and SLO math checks pass (§3).
- [ ] Trace context propagates across every capability boundary the module participates in (§2, §4).
- [ ] Dashboard renders against real, currently-emitted metric names (§3).
- [ ] CI green: lint → alert validation → SLO math check → signal presence test → config published (§3).
- [ ] Maps to a Jira Story (`capability:observability` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked instance: `../../domains/product/infrastructure/observability/slo.yaml`. Consumed by JSM operations (`../../jira/jsm-alignment.md`) and the QE Lead's quality bar.
