# Observability

## Purpose
Metrics, logs, traces, and SLOs for every capability. Observability is designed in so that a live module can be understood, measured, and trusted — it is the signal source for both the QE Lead's quality bar and JSM operations.

## Standards
- **Observable By Default:** every module emits standard metrics, structured logs, and distributed traces.
- Every capability declares its SLOs; alerts map to SLO breaches, not raw thresholds.
- Telemetry uses the shared conventions so signals are comparable across modules.

## Governance
- **Central:** telemetry standards, the SLO framework, the observability platform.
- **Local (FDE):** a module's own signals and SLO targets.

## Patterns
- The three pillars (metrics/logs/traces) plus SLOs from day one.
- Trace propagation across API, event, MCP, and A2A hops.

## Guidance
Observability signals are designed at step 8 of `../../architecture-design/operating-model/fde-build-deploy-workflow.md` and feed operations in JSM (`../../jira/jsm-alignment.md`). The QE Lead owns the quality bar against these signals (`../../architecture-design/operating-model/four-in-a-box.md`).
