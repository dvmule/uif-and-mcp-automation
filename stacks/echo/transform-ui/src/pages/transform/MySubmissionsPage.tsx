import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  listSubmissions, submitConfigured, SubmissionRow,
} from "@/lib/submitService";

// My submissions (UIFPE-218): submitted → in review → approved → deployed →
// first run succeeded/failed, with the reviewer's words surfaced here. The
// author never needs GitHub to know where their change stands — approval
// itself still happens in the PR (reviewers are engineers); this page
// mirrors it.
export function MySubmissionsPage() {
  const [rows, setRows] = useState<SubmissionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const load = (email?: string) => {
    setError(null);
    listSubmissions(email || undefined)
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };

  useEffect(() => {
    if (submitConfigured()) load();
  }, []);

  if (!submitConfigured()) {
    return (
      <div className="page">
        <div className="topbar">
          <h1>My submissions</h1>
          <Link className="btn" to="/transform">back</Link>
        </div>
        <div className="card">
          <p className="sub">
            The submission service is not wired to this environment yet —
            submitted tasks are handed to the data engineering team as
            downloaded intake files, and tracking happens with them. Once the
            service is deployed, every submission appears here automatically.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="topbar">
        <h1>My submissions</h1>
        <Link className="btn" to="/transform">back</Link>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row2" style={{ alignItems: "end" }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Only mine (your Pearson email)</label>
            <input type="email" value={filter} placeholder="everyone's"
              onChange={(e) => setFilter(e.target.value)} />
          </div>
          <div className="actions" style={{ marginBottom: 2 }}>
            <button className="btn" onClick={() => load(filter)}>Refresh</button>
          </div>
        </div>
      </div>

      {error && <div className="note">{error}</div>}
      {rows === null && !error && <p className="sub">Loading…</p>}
      {rows?.length === 0 && (
        <div className="card"><p className="sub">
          No submissions yet — finish a task in the wizard and submit it for
          review; it appears here the moment the pull request opens.
        </p></div>
      )}

      {rows?.map((r) => (
        <div key={r.number} className="card" style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <span>
              <strong>{r.title}</strong>
              <div className="kind">
                {r.ticket ?? ""} · submitted {r.submittedAt?.slice(0, 16).replace("T", " ")}
              </div>
            </span>
            <span className={"pill" + (statusTone(r) === "ok" ? " ok"
              : statusTone(r) === "bad" ? " warn" : "")}>
              {r.status}
            </span>
          </div>

          {r.comments.length > 0 && (
            <div className="field" style={{ marginTop: 10, marginBottom: 0 }}>
              <label>From the reviewers</label>
              {r.comments.map((c, i) => (
                <p key={i} className="help" style={{ marginTop: 4 }}>
                  <strong>{c.author}</strong>
                  {c.verdict === "CHANGES_REQUESTED" && " (requested changes)"}
                  {": "}{c.text}
                </p>
              ))}
            </div>
          )}

          <div className="actions" style={{ marginTop: 10 }}>
            <a className="btn" href={r.url} target="_blank" rel="noreferrer">
              open the pull request
            </a>
          </div>
        </div>
      ))}
    </div>
  );
}

function statusTone(r: SubmissionRow): "ok" | "bad" | "" {
  if (r.firstRun === "failed" || r.status.includes("failed")
      || r.status.includes("closed")) return "bad";
  if (r.firstRun === "succeeded" || r.approved) return "ok";
  return "";
}
