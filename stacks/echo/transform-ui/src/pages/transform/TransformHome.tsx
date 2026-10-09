import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { REGISTERED } from "@/lib/repo";
import { MASTERS, findMaster } from "@/lib/masterSchema";
import { loadDraft, clearDraft, saveDraft } from "@/lib/drafts";
import { seedFromImport, seedFromAI } from "@/lib/entryMethods";
import { COLUMNS_STEP } from "@/lib/wizardFlow";

// The entry hub (UIFPE-219): every way to start a task lives HERE, and every
// one of them opens the SAME wizard — how a task begins is a home page
// decision, never a wizard step. Imports and AI drafts hand the seeded task
// over directly and open the wizard at the Columns step.

export function TransformHome() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [aiOpen, setAiOpen] = useState(false);
  const [aiText, setAiText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const draft = loadDraft();

  // Hand the seeded task over in ROUTER STATE, not through storage: an
  // import or AI draft must open even when localStorage is blocked or full
  // (the pre-219 flow patched the mounted wizard directly and never
  // depended on storage). The draft save is the convenience that makes the
  // Resume card work afterwards — best-effort, and its failure is reported
  // rather than swallowed.
  const openSeeded = (seed: Parameters<typeof saveDraft>[0]) => {
    const persisted = saveDraft(seed, COLUMNS_STEP);
    // The wizard carries the warning, because that is where the user will be
    // when it matters — an error set here would navigate away unseen.
    nav("/transform/new",
        { state: { seed, step: COLUMNS_STEP, unsaved: !persisted } });
  };

  const handleImport = async (file: File) => {
    setError(null);
    try { openSeeded(await seedFromImport(file)); }
    catch (e) { setError(`Could not import — ${e instanceof Error ? e.message : e}`); }
  };

  const handleAI = async () => {
    setBusy(true); setError(null);
    try { openSeeded(await seedFromAI(aiText)); }
    catch (e) { setError(`Draft failed — ${e instanceof Error ? e.message : e}`); }
    finally { setBusy(false); }
  };

  const hits = q.trim().length < 2 ? [] : REGISTERED.flatMap((r) =>
    r.columns
      .filter((c) =>
        c.goldColumnName.toLowerCase().includes(q.toLowerCase()) ||
        (c.businessDescription ?? "").toLowerCase().includes(q.toLowerCase()))
      .map((c) => ({ reg: r, col: c })))
    .slice(0, 12);

  return (
    <>
      <div className="topbar">
        <h1>Transform</h1>
        <Link className="btn" style={{ textDecoration: "none" }}
          to="/transform/submissions">
          My submissions
        </Link>
      </div>

      {draft && (
        <div className="card" style={{ marginBottom: 16, display: "flex",
          justifyContent: "space-between", alignItems: "center" }}>
          <span>
            <strong>Resume where you left off:</strong>{" "}
            {draft.state.taskName}
            <span className="kind"> — saved {draft.savedAt.slice(0, 16).replace("T", " ")}</span>
          </span>
          <span>
            <button className="btn primary" onClick={() => nav("/transform/new?resume=1")}>
              Resume
            </button>{" "}
            <button className="btn" onClick={() => { clearDraft(); nav(0 as any); }}>
              Discard
            </button>
          </span>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Find an attribute</h2>
        <p className="sub">
          Know what you want to change but not where it lives? Search every
          registered attribute by name or business meaning.
        </p>
        <input type="text" value={q} placeholder="e.g. cover image, ISBN, price…"
          onChange={(e) => setQ(e.target.value)} />
        {hits.length > 0 && (
          <div className="collist" style={{ marginTop: 10 }}>
            {hits.map(({ reg, col }) => (
              <div key={`${reg.table}.${col.goldColumnName}`} className="colrow"
                style={{ cursor: "default" }}>
                <span>
                  <strong>{col.goldColumnName}</strong>
                  <span className="kind"> · {reg.table} · {col.kind}</span>
                  <div className="kind">{col.businessDescription}</div>
                </span>
                <button className="btn"
                  onClick={() => { clearDraft();
                    nav(`/transform/new?modify=${encodeURIComponent(reg.table)}|${encodeURIComponent(col.goldColumnName)}`); }}>
                  Modify →
                </button>
              </div>
            ))}
          </div>
        )}
        {q.trim().length >= 2 && hits.length === 0 && (
          <p className="help" style={{ marginTop: 8 }}>
            No registered attribute matches — if it should exist, start a
            transformation below and add it.
          </p>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Start a transformation</h2>
        <p className="sub">
          Every way in ends in the same place — a set of rules you review
          before anything is submitted.
        </p>
        <div className="checks" style={{ flexWrap: "wrap", gap: 8 }}>
          <button className="btn primary"
            onClick={() => { clearDraft(); nav("/transform/new"); }}>
            ☑ Guided — answer a few questions
          </button>
          <label className="btn" style={{ cursor: "pointer" }}>
            ⬇ Import a mapping file
            <input type="file" accept=".json,application/json" hidden
              onChange={(e) => e.target.files?.[0] && handleImport(e.target.files[0])} />
          </label>
          <button className="btn" onClick={() => setAiOpen((v) => !v)}>
            ✨ Describe it — draft with AI
          </button>
          <button className="btn"
            onClick={() => { clearDraft(); nav("/transform/new?new=1"); }}>
            ＋ Set up a new gold table
          </button>
        </div>
        {aiOpen && (
          <div className="field" style={{ marginTop: 12 }}>
            <label>Describe the attribute(s) you need</label>
            <textarea rows={3} value={aiText} style={{ width: "100%" }}
              placeholder={"e.g. add returnsWindow, an integer number of days from EGO_MTL_SY_ITEMS_EXT_B column N_EXT_ATTR3 in attribute group 802, api channel only"}
              onChange={(e) => setAiText(e.target.value)} />
            <div className="actions" style={{ marginTop: 8 }}>
              <button className="btn primary" disabled={busy || aiText.trim().length < 10}
                onClick={handleAI}>
                {busy ? "Drafting…" : "✨ Draft the rules"}
              </button>
            </div>
            <p className="help">
              The AI only drafts — its proposal opens in the wizard where the
              same validation, query previews and your review apply. Nothing
              is submitted.
            </p>
          </div>
        )}
        {error && <div className="note" style={{ marginTop: 10 }}>{error}</div>}
      </div>

      <div className="card">
        <h2>Registered gold tables</h2>
        <p className="sub">
          Discovered live from the repository — every domain that registers a
          mapping appears here; changes go through review.
        </p>
        <div className="collist">
          {REGISTERED.map((r) => {
            const k = (kind: string) =>
              r.columns.filter((c) => c.kind === kind).length;
            const master = findMaster(r.domain, r.table.split(".").pop() ?? "");
            const lifecycle = String(master?.metadata?.lifecycle ?? "");
            return (
              <div key={r.table} className="colrow" style={{ cursor: "default" }}>
                <span>
                  <strong>{r.table}</strong>
                  {lifecycle && (
                    <span className={"pill" + (lifecycle === "active" ? " ok" : "")}
                      style={{ marginLeft: 8 }}>
                      {lifecycle === "active" ? "live" : lifecycle}
                    </span>
                  )}
                  <div className="kind">
                    {r.columns.length} columns ({k("scalar")} scalar · {k("conditional")} conditional · {k("nested")} nested)
                  </div>
                </span>
                <span style={{ display: "flex", gap: 8 }}>
                  <Link className="btn" style={{ textDecoration: "none" }}
                    to={`/transform/attributes/${encodeURIComponent(r.domain)}/${encodeURIComponent(r.table)}`}>
                    attributes
                  </Link>
                  <Link className="btn" style={{ textDecoration: "none" }}
                    to={`/transform/sources/${encodeURIComponent(r.domain)}/${encodeURIComponent(r.table)}`}>
                    sources
                  </Link>
                </span>
              </div>
            );
          })}
        </div>
        <p className="help" style={{ marginTop: 10 }}>
          {MASTERS.length} registered schema{MASTERS.length === 1 ? "" : "s"} ·
          change path: wizard → review &amp; approval → auto-deploy.
        </p>
      </div>
    </>
  );
}
