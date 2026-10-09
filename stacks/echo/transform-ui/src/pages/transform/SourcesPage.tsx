// Sources for one registered entity (UIFPE-219): which Silver tables feed
// it, in the reader's language — one card per table, one plain change-impact
// sentence each. The per-attachment join analysis that used to live here
// (shapes, key-reach meters, grains, filter chips) is column-level detail
// and lives behind each attribute's editor, where the join actually belongs.
import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { REGISTERED } from "../../lib/repo";
import { tableSummaries, fanOutSentence } from "../../lib/sourceGrain";
import { sourceStanding } from "../../lib/manifests";

export function SourcesPage() {
  const { domain, table } = useParams();
  const reg = REGISTERED.find((r) => r.domain === domain && r.table === table);
  const [open, setOpen] = useState<string | null>(null);

  if (!reg) {
    return (
      <>
        <div className="topbar"><h1>Sources</h1></div>
        <div className="card">
          <p>No registered mapping for {domain} / {table}.</p>
          <Link to="/transform">Back to Transform</Link>
        </div>
      </>
    );
  }

  const summaries = tableSummaries({
    drivingTable: reg.drivingTable,
    entityKey: reg.entityKey,
    columns: reg.columns,
  });
  const keyCols = (reg.entityKey ?? []).map((p) => p.goldColumn).join(" + ");


  return (
    <>
      <div className="topbar">
        <div>
          <h1>Sources</h1>
          <p className="sub" style={{ margin: 0 }}>
            {summaries.length} Silver table{summaries.length === 1 ? "" : "s"} feed{" "}
            <strong>{reg.table}</strong>
            {keyCols && <> — one row per <code>{keyCols}</code></>}.
          </p>
        </div>
        <Link to="/transform" className="btn" style={{ textDecoration: "none" }}>Back</Link>
      </div>

      {summaries.map((t) => {
        // ONE definition of standing, shared with the wizard's Sources step
        const stands = sourceStanding(t.table, reg.domain);
        const showing = open === t.shortName;
        return (
          <div key={t.shortName} className="card" style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <strong style={{ display: "flex", gap: 8, alignItems: "center",
                               flexWrap: "wrap" }}>
                {t.shortName}
                {t.primary && <span className="pill ok">Primary table</span>}
                {stands === "unknown" && (
                  <span className="pill warn" title="This name matches no table in the Silver catalog — attributes reading it can never fill.">
                    no such table in Silver
                  </span>
                )}
                {stands === "not-wired" && (
                  <span className="pill" title="The table exists in Silver but is not a registered runtime source for this entity yet — attributes reading it ship with empty values until the feed is wired in.">
                    not wired in yet
                  </span>
                )}
              </strong>
              <button className="btn" style={{ padding: "2px 10px" }}
                onClick={() => setOpen(showing ? null : t.shortName)}>
                {t.usedBy} attribute{t.usedBy === 1 ? "" : "s"} {showing ? "▴" : "▾"}
              </button>
            </div>
            <p className="help" style={{ marginTop: 6, marginBottom: 0 }}>
              {t.sentence}
            </p>
            {t.odd.length > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary style={{ cursor: "pointer", fontSize: 12.5 }}>
                  Read in {t.odd.length} different ways — what each costs
                </summary>
                <ul style={{ marginTop: 6 }}>
                  {t.odd.map((a, i) => (
                    <li key={i} className="help" style={{ marginBottom: 4 }}>
                      {a.examples.slice(0, 2).join(", ")}
                      {a.usedBy > 2 ? ` (+${a.usedBy - 2} more)` : ""} —{" "}
                      {fanOutSentence(a)}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {showing && (
              <div className="collist" style={{ marginTop: 8, maxHeight: 220 }}>
                {/* the SAME list the count came from (lib/sourceGrain) — the
                    button used to count source uses while this listed columns,
                    so it promised 95 and opened 46 */}
                {t.columns.map((name) => {
                  const col = reg.columns.find((c) => c.goldColumnName === name);
                  return (
                    <div key={name} className="colrow" style={{ cursor: "default" }}>
                      <span>
                        <strong>{name}</strong>
                        {col && <span className="kind"> · {col.kind}</span>}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
