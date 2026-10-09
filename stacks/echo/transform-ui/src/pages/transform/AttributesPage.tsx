// Every attribute of one registered entity, with what happens to it on submit.
//
// The verdict is the point of this screen. "Publishes on the next run" and
// "waits for a data engineer" are different promises, and until now the author
// only found out which one they had made after submitting. Because the verdict
// depends on the INTENT — modifying a driving-table column is PLAN even though
// adding one is AUTO — the intent is a control here rather than a hidden
// assumption: switch it and watch the counts move.
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { REGISTERED } from "../../lib/repo";
import { verdictFor, verdictSentence, tally } from "../../lib/classify";
import type { Verdict } from "../../lib/classify";
import { StatusPill } from "../../components/StatusPill";
import type { MappingColumn, TaskIntent } from "../../lib/types";

const KIND_LABEL: Record<MappingColumn["kind"], string> = {
  scalar: "single value",
  conditional: "branching",
  nested: "repeating group",
};

/** Consumer tiers are concentric (event ⊂ message ⊂ api), so size carries the
 *  containment and the label spells it out — colour is never the only signal. */
function Channels({ c }: { c: MappingColumn["channels"] }) {
  const on = [c.event && "events", c.message && "messages", c.api && "API"]
    .filter(Boolean).join(", ") || "none";
  return (
    <span className="chan" aria-label={`reaches ${on}`} title={`reaches ${on}`}>
      <i className={c.event ? "e in" : "e"} />
      <i className={c.message ? "m in" : "m"} />
      <i className={c.api ? "a in" : "a"} />
    </span>
  );
}

function VerdictCell({ v }: { v: Verdict }) {
  return (
    <span title={verdictSentence(v)}>
      <StatusPill tone={v.verdict === "AUTO" ? "ok" : "warn"}
        label={v.verdict === "AUTO" ? "Publishes" : "Engineer"} why={v.reason} />
    </span>
  );
}

export function AttributesPage() {
  const { domain, table } = useParams();
  const reg = REGISTERED.find((r) => r.domain === domain && r.table === table);
  const [intent, setIntent] = useState<TaskIntent>("modify");
  const [only, setOnly] = useState<"all" | "auto" | "plan">("all");

  const rows = useMemo(() => {
    if (!reg) return [];
    const registeredKinds = Object.fromEntries(
      reg.columns.map((c) => [c.goldColumnName, c.kind]));
    return reg.columns.map((col) => ({
      col,
      v: verdictFor(col, { drivingTable: reg.drivingTable ?? "", intent, registeredKinds }),
    }));
  }, [reg, intent]);

  if (!reg)
    return (
      <>
        <h2>Attributes</h2>
        <p className="sub">
          No registered mapping for <code>{domain}</code> / <code>{table}</code>.
        </p>
        <Link to="/transform">Back to Transform</Link>
      </>
    );

  const t = tally(rows.map((r) => r.v));
  const shown = rows.filter((r) =>
    only === "all" || (only === "auto") === (r.v.verdict === "AUTO"));
  const reasons = Object.entries(t.byReason).sort((a, b) => b[1] - a[1]);

  return (
    <>
      <div className="topbar">
        <div>
          <h2>Attributes</h2>
          <p className="sub" style={{ margin: 0 }}>
            {reg.columns.length} mapped entries on <strong>{reg.table}</strong>.
            {" "}Dotted names are members of a struct column, not columns of their own.
          </p>
        </div>
        <Link to="/transform" className="btn" style={{ textDecoration: "none" }}>Back</Link>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="field" style={{ marginBottom: 10 }}>
          <label htmlFor="intent">What would you be doing to these attributes?</label>
          <select id="intent" value={intent} style={{ maxWidth: 360 }}
            onChange={(e) => setIntent(e.target.value as TaskIntent)}>
            <option value="modify">Changing one that already exists</option>
            <option value="add">Adding a new one like it</option>
          </select>
          <div className="help">
            The same attribute can compile on its own as an addition and still
            need an engineer as a change — the shipped query lines it would
            edit (a spine-table expression, a branching pick, a lookup join)
            may be hand-tuned, so a change rewrites rather than appends.
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn" onClick={() => setOnly("all")}
            style={{ fontWeight: only === "all" ? 700 : 400 }}>
            All {reg.columns.length}
          </button>
          <button className="btn" onClick={() => setOnly("auto")}
            style={{ fontWeight: only === "auto" ? 700 : 400 }}>
            Publishes {t.auto}
          </button>
          <button className="btn" onClick={() => setOnly("plan")}
            style={{ fontWeight: only === "plan" ? 700 : 400 }}>
            Needs an engineer {t.plan}
          </button>
        </div>

        {t.intentDriven > 0 && (
          <div className="note" style={{ marginBottom: 0 }}>
            <strong>
              {t.intentDriven} of these would compile on their own if you were
              adding them instead of changing them.
            </strong>
            {" "}
            They are spine-table columns on <code>{reg.drivingTable}</code>,
            branching picks, or lookup joins — shipped query lines that may be
            hand-tuned, so an edit rewrites rather than appends. Nothing is
            wrong with them — it just means a data engineer is in the loop.
          </div>
        )}

        <details>
          <summary style={{ cursor: "pointer", fontSize: 12.5, fontWeight: 700 }}>
            Why the {t.plan} need an engineer
          </summary>
          <table className="props" style={{ marginTop: 10 }}>
            <tbody>
              {reasons.map(([reason, n]) => (
                <tr key={reason}>
                  <td style={{ width: 260 }}>
                    <span className="structure src">{reason}</span>
                  </td>
                  <td>
                    {n} attribute{n > 1 ? "s" : ""} —{" "}
                    {verdictSentence({ verdict: "PLAN", reason: reason as Verdict["reason"],
                                       fromIntent: false })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </div>

      <div className="tablewrap">
      <table className="structure">
        <thead>
          <tr>
            <th>Attribute</th>
            <th>Shape</th>
            <th>Reads</th>
            <th>Reaches</th>
            <th>On submit</th>
          </tr>
        </thead>
        <tbody>
          {shown.map(({ col, v }) => (
            <tr key={col.goldColumnName}>
              <td>
                <div className="structure src" style={{ fontWeight: 700 }}>
                  {col.goldColumnName}
                </div>
                <div style={{ color: "var(--ink-soft)", fontSize: 12 }}>
                  {col.businessDescription || <em>no description</em>}
                </div>
              </td>
              <td>
                <span className="pill mute">{KIND_LABEL[col.kind]}</span>
                {col.kind !== "nested" && (
                  <div className="structure src" style={{ color: "var(--ink-muted)", fontSize: 11.5 }}>
                    {col.dataType}
                  </div>
                )}
              </td>
              <td className="structure src" style={{ fontSize: 11.5 }}>
                {col.kind === "scalar" ? col.source?.table?.split(".").pop()
                  : col.kind === "conditional"
                    ? `${col.branches?.length ?? 0} branches`
                    : `${col.fields?.length ?? 0} fields`}
              </td>
              <td><Channels c={col.channels} /></td>
              <td><VerdictCell v={v} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      {shown.length === 0 && (
        <p className="sub" style={{ marginTop: 14 }}>
          Nothing matches that filter.
        </p>
      )}

      <div className="note">
        <strong>These verdicts come from the compiler, not from this screen.</strong>
        {" "}
        They mirror <code>compile_intake_delta.py</code>, and a shared fixture is
        asserted from both sides so the two cannot drift apart quietly. Reason
        codes are the compiler's own.
        {" "}
        <Link to={`/transform/sources/${encodeURIComponent(reg.domain)}/${encodeURIComponent(reg.table)}`}>
          See how each source attaches
        </Link>
      </div>
    </>
  );
}
