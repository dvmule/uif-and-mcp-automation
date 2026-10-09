// Health module (UIFPE-195): the detail behind the header's summary pill.
// Each /health flag is shown with what it means and what breaks when it is
// off — this page exists because "engine ✓" quietly disappearing from a pill
// is how a broken deploy hides (a module-level pyspark import kills the
// engine inside the API image while every other endpoint stays healthy).
import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, RefreshCw } from "lucide-react";
import { api } from "./api.js";
import { Button, Card, EmptyState, Note, PageHeader, Pill } from "./ui.jsx";

const FLAGS = [
  {
    key: "engine_available",
    label: "Mapping engine",
    means: "The dynamic-mapping engine imported inside the API container.",
    breaks:
      "Validate in the wizard stops checking drafts with the pipeline's own " +
      "validators. Usual cause: a module-level pyspark import broke the " +
      "quarantine, or the vendored engine copy is damaged.",
  },
  {
    key: "s3_publish_enabled",
    label: "Config publishing",
    means: "BEACON_DATA_BUCKET is configured; publishing writes immutable config pairs to S3.",
    breaks:
      "Publishing becomes catalog-only: versions exist in DynamoDB but no " +
      "config lands where the pipeline reads it, so runs cannot start.",
  },
  {
    key: "runs_enabled",
    label: "Run engine",
    means: "BEACON_STATE_MACHINE_ARN is configured; Run now starts a Step Functions execution.",
    breaks: "The Run now button fails — no batch can be started from the panel.",
  },
  {
    key: "runs_history_enabled",
    label: "Run history",
    means: "The runs table is configured; completed batches are recorded and listed.",
    breaks: "Runs still execute but the Runs screen and the Home dashboard go blind.",
  },
  {
    key: "preview_enabled",
    label: "Live filter preview",
    means:
      "BEACON_PREVIEW_WORKGROUP is configured; the wizard's Filter step suggests real column " +
      "values, previews matched rows on Athena (UIFPE-441) and offers them as a CSV download (UIFPE-490).",
    breaks:
      "The Filter step offers no value suggestions, no Preview matches and no CSV download; Check filter " +
      "(the engine's static validators) still works.",
  },
];

// UIFPE-630: a status icon leads the row, the ok/off pill sits on the right
// in the success or warning tone, the flag key sits under the label, and the
// "when off" consequence is a small amber note. Same flags, same text.
function FlagRow({ flag, value }) {
  const ok = value === true;
  return (
    <div
      className={`grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-3 border-b border-line px-4 py-3 last:border-b-0 ${
        ok ? "" : "bg-pearson-amber/[0.05]"
      }`}
    >
      <span
        aria-hidden="true"
        className={`mt-0.5 grid h-[22px] w-[22px] place-items-center rounded-full ${
          ok ? "bg-turq/25 text-signal-ok" : "bg-pearson-amber/25 text-signal-warn"
        }`}
      >
        {ok ? <Check size={13} strokeWidth={2.5} /> : <AlertTriangle size={12} strokeWidth={2.2} />}
      </span>
      <div className="min-w-0">
        <div className="text-body font-semibold text-ink">{flag.label}</div>
        <code className="block font-mono text-[11px] text-ink-mute">{flag.key}</code>
        <div className="mt-1 text-meta text-ink-soft">{flag.means}</div>
        {!ok && (
          <Note tone="warn">
            <b>When off:</b> {flag.breaks}
          </Note>
        )}
      </div>
      <Pill tone={ok ? "ok" : "warn"}>{ok ? "ok" : "off"}</Pill>
    </div>
  );
}

export default function Health() {
  const [health, setHealth] = useState(null);
  const [checkedAt, setCheckedAt] = useState(null);

  const refresh = useCallback(() => {
    api
      .health()
      .then((h) => {
        setHealth(h);
        setCheckedAt(new Date());
      })
      .catch((e) => {
        setHealth({ status: "unreachable", error: e.message });
        setCheckedAt(new Date());
      });
  }, []);
  useEffect(refresh, [refresh]);

  const reachable = health?.status === "ok";
  const degraded = reachable && FLAGS.some(({ key }) => health[key] !== true);

  return (
    <section>
      <PageHeader
        title="Health"
        sub="What the control panel can and cannot do right now, and why."
        actions={
          <Button onClick={refresh}>
            <RefreshCw size={14} /> Re-check
          </Button>
        }
      />

      {health === null && (
        <Card>
          <EmptyState>Checking…</EmptyState>
        </Card>
      )}

      {health !== null && (
        <>
          <Note tone={reachable ? (degraded ? "warn" : "ok") : "fail"}>
            {reachable
              ? degraded
                ? "API connected — one or more capabilities are off, see below."
                : "All checks passing — authoring, publishing and runs are fully operational."
              : `API unreachable — nothing below could be checked. ${health.error ?? ""}`}
            {checkedAt && (
              <span className="ml-1 text-ink-mute">
                (checked {checkedAt.toLocaleTimeString()}, env {health.env ?? "?"})
              </span>
            )}
          </Note>

          {reachable && (
            <section className="mt-6">
              <div className="mb-3">
                <h2 className="text-[18px] font-extrabold tracking-tight text-ink">Capabilities</h2>
                <p className="mt-0.5 text-meta text-ink-mute">Each flag comes from the API's own health check.</p>
              </div>
              <Card pad={false}>
                {FLAGS.map((flag) => (
                  <FlagRow key={flag.key} flag={flag} value={health[flag.key]} />
                ))}
              </Card>
            </section>
          )}
        </>
      )}
    </section>
  );
}
