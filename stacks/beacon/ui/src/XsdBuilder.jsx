// XsdBuilder.jsx (UIFPE-660) — the other door on the Format step. A partner
// who sent a sample XML instead of an XSD: paste it or upload it, one click
// builds the loosest schema that fits (POST /schemas/infer, nothing stored),
// the drawer shows what was read — record, elements, required / optional /
// repeats, value types, and what one sample cannot prove — then hands the
// file over: download it, or use it for this export (which stages it through
// the ordinary upload). Same drawer pattern as the transform panel.
import React, { useEffect, useRef, useState } from "react";
import { ChevronRight, Download, X } from "lucide-react";
import { api } from "./api.js";
import { Button, Note, Pill, inputClass } from "./ui.jsx";

const MAX_BYTES = 2 * 1024 * 1024;

/** A quick read of the pasted text in the browser: well-formed or not, and
 *  the record shape when the root wraps one repeating child. The server's
 *  answer is the one that counts; this only keeps the author informed. */
export function sampleSummary(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return { state: "empty" };
  if (new TextEncoder().encode(trimmed).length > MAX_BYTES) return { state: "big" };
  let doc;
  try {
    doc = new DOMParser().parseFromString(trimmed, "application/xml");
  } catch {
    return { state: "bad", message: "not well-formed XML" };
  }
  const err = doc.querySelector("parsererror");
  if (err) return { state: "bad", message: err.textContent.split("\n").find((l) => l.trim()) || "not well-formed XML" };
  const root = doc.documentElement;
  const kids = [...root.children];
  const names = new Set(kids.map((k) => k.localName));
  if (kids.length > 1 && names.size === 1) {
    return { state: "ok", root: root.localName, record: kids[0].localName, records: kids.length };
  }
  return { state: "ok", root: root.localName, record: root.localName, records: 1 };
}

function StepHead({ n, title, right }) {
  return (
    <div className="mb-3 flex items-center gap-2.5">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent text-label font-bold text-white">{n}</span>
      <h3 className="text-[16px] font-bold tracking-tight text-ink">{title}</h3>
      {right && <span className="ml-auto">{right}</span>}
    </div>
  );
}

function Fact({ label, children, mono = false }) {
  return (
    <div className="rounded-brand border border-line bg-paper-head px-2.5 py-2">
      <div className="text-label font-semibold uppercase tracking-wider text-ink-mute">{label}</div>
      <div className={`mt-0.5 truncate ${mono ? "font-mono text-meta" : "text-[15px] font-bold"} text-ink`}>{children}</div>
    </div>
  );
}

export default function XsdBuilderDrawer({ onClose, onUse }) {
  const [mode, setMode] = useState("paste"); // paste | upload
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [busy, setBusy] = useState(false);
  const [using, setUsing] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [showXsd, setShowXsd] = useState(false);
  const fileRef = useRef(null);
  const resultRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const summary = sampleSummary(text);
  const canBuild = summary.state === "ok" && !busy;

  const build = async () => {
    if (!canBuild) return;
    setBusy(true);
    setError(null);
    try {
      const record = await api.inferSchema(filename || "sample.xml", text);
      setResult(record);
      setShowXsd(false);
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const takeFile = async (file) => {
    if (!file) return;
    setFilename(file.name);
    setText(await file.text());
    setResult(null);
    setError(null);
  };

  const download = () => {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([result.xsd], { type: "application/xml" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = result.filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const use = async () => {
    if (!result || using) return;
    setUsing(true);
    setError(null);
    try {
      await onUse(result.filename, result.xsd);
    } catch (err) {
      setError(err.message);
      setUsing(false);
    }
  };

  const reset = () => {
    setText("");
    setFilename("");
    setResult(null);
    setError(null);
  };

  const liveLine =
    summary.state === "empty" ? "Paste the partner's XML — a few records are enough, ten are better."
    : summary.state === "big" ? "The sample is larger than 2 MB — a few records are enough."
    : summary.state === "bad" ? summary.message
    : summary.records > 1
      ? `${summary.records} ${summary.record} records inside ${summary.root}`
      : `one ${summary.record} record — required / optional cannot be told apart from one`;

  return (
    <div className="motion-fade-in fixed inset-0 z-50 bg-pearson-purple/45" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="xsd-builder-title"
        className="motion-slide-in-right absolute inset-y-0 right-0 grid w-full max-w-[720px] grid-rows-[auto_minmax(0,1fr)_auto] border-l border-line bg-paper-card shadow-[-24px_0_48px_-24px_rgb(13_0_77_/_0.5)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 bg-pearson-purple px-6 pb-4 pt-5 text-white">
          <div className="min-w-0">
            <div className="text-label font-semibold uppercase tracking-[0.12em] text-pearson-light-purple">Partner schema</div>
            <div id="xsd-builder-title" className="mt-1 text-[20px] font-semibold leading-tight">Build an XSD from a sample</div>
            <p className="mt-1 max-w-[56ch] text-meta text-pearson-light-purple">
              Give Beacon a piece of the partner&apos;s XML. It reads how the records are shaped and writes
              the loosest schema that fits — you check it, then keep the file.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="ml-auto grid h-8 w-8 shrink-0 place-items-center rounded-brand border border-white/30 text-white transition-colors hover:bg-white/10"
          >
            <X size={15} />
          </button>
        </div>

        <div className="min-h-0 overflow-y-auto px-6">
          {/* step 1: the sample */}
          <section className="border-b border-line py-5">
            <StepHead n={1} title="The sample" />
            <div role="tablist" aria-label="How to give the sample" className="mb-2.5 inline-flex overflow-hidden rounded-brand border border-line">
              {[["paste", "Paste XML"], ["upload", "Upload a file"]].map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={mode === id}
                  onClick={() => setMode(id)}
                  className={`px-3 py-1.5 text-meta font-semibold transition-colors [&:not(:first-child)]:border-l [&:not(:first-child)]:border-line ${
                    mode === id ? "bg-accent-soft text-accent" : "bg-paper-card text-ink-soft hover:text-ink"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            {mode === "paste" ? (
              <textarea
                className={`${inputClass} resize-y font-mono text-meta leading-relaxed`}
                style={{ minHeight: 180 }}
                aria-label="Sample XML"
                spellCheck={false}
                placeholder={"<Products>\n  <Product id=\"1\">…</Product>\n  <Product id=\"2\">…</Product>\n</Products>"}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setResult(null);
                }}
              />
            ) : (
              <div
                className="rounded-card border-[1.5px] border-dashed border-paper-line bg-paper-head px-4 py-5 text-center text-body text-ink-soft"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  takeFile(e.dataTransfer.files?.[0]);
                }}
              >
                <b className="block text-ink">{filename ? filename : "Drop the sample here, or"}</b>
                <div className="mt-2">
                  <Button size="sm" onClick={() => fileRef.current?.click()}>
                    {filename ? "Choose another file…" : "Choose XML file…"}
                  </Button>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".xml,text/xml,application/xml"
                    className="hidden"
                    onChange={(e) => {
                      takeFile(e.target.files?.[0]);
                      e.target.value = "";
                    }}
                  />
                </div>
                <div className="mt-1.5 text-meta text-ink-mute">.xml up to 2 MB · read once, not kept</div>
              </div>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2 text-meta text-ink-mute">
              {summary.state === "ok" ? <Pill tone="ok">well-formed</Pill> : summary.state === "bad" ? <Pill tone="warn">not well-formed yet</Pill> : null}
              <span>{liveLine}</span>
            </div>
            <div className="mt-3">
              <Button variant="primary" onClick={build} disabled={!canBuild} busy={busy}>
                {busy ? "Building…" : result ? "Build it again" : "Build the schema"}
              </Button>
            </div>
            {error && <Note tone="fail">{error}</Note>}
          </section>

          {result && (
            <>
              {/* step 2: what was read */}
              <section ref={resultRef} className="border-b border-line py-5">
                <StepHead n={2} title="What Beacon read" right={<Pill tone="ok">schema built</Pill>} />
                <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Fact label="Wrapper" mono>{result.wrapper || "—"}</Fact>
                  <Fact label="One record is" mono>{result.record}</Fact>
                  <Fact label="Records read">{result.records}</Fact>
                  <Fact label="Elements · attributes">{result.elements} · {result.attributes}</Fact>
                </div>
                <div className="overflow-hidden rounded-card border border-line">
                  {result.rows.map((r, i) => (
                    <div key={r.path} className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-3 py-1.5 text-body ${i > 0 ? "border-t border-line" : ""}`}>
                      <span className="truncate font-mono text-meta text-ink">{r.path}</span>
                      <span className="flex flex-wrap justify-end gap-1">
                        <Pill tone={r.required ? "accent" : "mute"}>{r.required ? "required" : "optional"}</Pill>
                        {r.repeats && <Pill tone="ok">repeats</Pill>}
                        <Pill tone="mute" className="font-mono font-medium">{r.type}</Pill>
                      </span>
                    </div>
                  ))}
                </div>
                <div className="mt-3 space-y-1.5">
                  <Note tone="warn">
                    One sample cannot prove an element is optional or a value is always a number. These are
                    the loosest rules that fit what you gave — the partner&apos;s real XSD can tighten them later.
                  </Note>
                  {result.notes?.length > 0 && <Note tone="mute">{result.notes.join(". ")}.</Note>}
                  {result.unsupported?.length > 0 && <Note tone="warn">{result.unsupported.join("; ")}.</Note>}
                </div>
              </section>

              {/* step 3: the file */}
              <section className="py-5">
                <StepHead
                  n={3}
                  title="The XSD"
                  right={<span className="font-mono text-meta text-ink-mute">{result.filename} · {(result.bytes / 1024).toFixed(1)} KB</span>}
                />
                <button
                  type="button"
                  onClick={() => setShowXsd((v) => !v)}
                  aria-expanded={showXsd}
                  className="flex items-center gap-1 text-meta font-semibold text-ink-mute hover:text-accent"
                >
                  <ChevronRight size={13} className={`transition-transform ${showXsd ? "rotate-90" : ""}`} />
                  {showXsd ? "Hide the schema" : "Show the schema"}
                </button>
                {showXsd && (
                  <pre className="mt-2 max-h-[260px] overflow-auto rounded-brand border border-line bg-paper-head px-3 py-2 font-mono text-meta leading-relaxed text-ink">
                    {result.xsd}
                  </pre>
                )}
              </section>
            </>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-line bg-paper-head px-6 py-3.5">
          {(text || result) && (
            <Button variant="ghost" size="sm" onClick={reset}>Start again</Button>
          )}
          <span className="flex-1" />
          <Button onClick={onClose}>Close</Button>
          <Button onClick={use} disabled={!result || using} busy={using} title="Attach the built schema to this export, as if you had uploaded it">
            {using ? "Attaching…" : "Use it for this export"}
          </Button>
          <Button variant="primary" onClick={download} disabled={!result}>
            <Download size={13} /> Download{result ? ` ${result.filename}` : " the XSD"}
          </Button>
        </div>
      </aside>
    </div>
  );
}
