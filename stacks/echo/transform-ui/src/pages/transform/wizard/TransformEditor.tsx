import { useState } from "react";
import { WizardState, TransformDef, TransformRef } from "@/lib/types";
import { transformSql } from "@/lib/sqlPreview";

// A column's transformation rule — the same pattern as ScopeEditor: pick a
// NAMED rule defined in the mapping's `transforms` registry (single source of
// truth — this is how users know what rules exist), or build one inline from
// structured parts; inline definitions can be saved back as a named rule so
// the next attribute reuses them. Everything here compiles deterministically
// around the source expression {x} — free text is the pre-UIFPE-105 shape and
// only appears as a deprecation warning until restructured.

const WHEN_OPS = [
  { v: "startsWith", label: "value starts with" },
  { v: "like", label: "value matches pattern (LIKE)" },
  { v: "equals", label: "value equals" },
  { v: "in", label: "value is one of" },
  { v: "isNull", label: "value is null" },
] as const;

const THEN_OPS = [
  { v: "prepend", label: "prepend text" },
  { v: "append", label: "append text" },
  { v: "replaceText", label: "replace text" },
  { v: "literal", label: "use a fixed value" },
  { v: "null", label: "make it null" },
  { v: "keep", label: "keep as-is" },
] as const;

const STEP_OPS = [
  { v: "round", label: "round" },
  { v: "cast", label: "cast type" },
  { v: "trim", label: "trim whitespace" },
  { v: "upper", label: "UPPERCASE" },
  { v: "lower", label: "lowercase" },
  { v: "coalesceTo", label: "default when null" },
] as const;

// One-line summary for the registry dropdown and the named-rule help text.
const summarize = (d: TransformDef) =>
  d.description || transformSql(d, "{x}").replace(/\n\s*/g, " ");

export function TransformEditor({ value, set, state, patch, label }:
  { value: TransformRef | undefined; set: (t: TransformRef) => void;
    state: WizardState; patch: (p: Partial<WizardState>) => void;
    label?: string }) {
  const rules = state.transforms ?? {};
  const names = Object.keys(rules);
  const [saveName, setSaveName] = useState("");

  const prose = typeof value === "string" ? value : null;
  const ruleName = value && typeof value === "object" && "rule" in value
    ? (value as { rule: string }).rule : null;
  const inline = value && typeof value === "object" && !("rule" in value)
    ? (value as TransformDef) : null;
  const named = ruleName ? rules[ruleName] : null;

  const setDef = (d: TransformDef) => set(d);
  // A name that already exists is REFUSED, not overwritten: the compiler
  // never overwrites registry entries (changing a shared rule is a reviewed
  // edit of the mapping, not a wizard side effect), so allowing it here
  // would make the preview diverge from what actually compiles.
  const nameTaken = !!rules[saveName.trim()];
  const saveAsRule = () => {
    const name = saveName.trim();
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name) || !inline || rules[name]) return;
    patch({ transforms: { ...rules, [name]: inline } });
    set({ rule: name });
    setSaveName("");
  };

  return (
    <div className="field">
      <label>{label ?? "Transformation (optional)"}</label>
      {prose && (
        <p className="help">
          ⚠ Free-text transform (deprecated): “{prose}” — pick a named rule or
          rebuild it structurally below; free text no longer compiles.
        </p>
      )}
      <select value={ruleName ?? (inline ? "__inline__" : "")}
        style={{ marginBottom: inline || named ? 8 : 0 }}
        onChange={(e) => {
          if (e.target.value === "") set(null);
          else if (e.target.value === "__inline__") setDef({ cases: [], steps: [] });
          else set({ rule: e.target.value });
        }}>
        <option value="">none — straight copy</option>
        {names.map((n) => (
          <option key={n} value={n}>{n} — {summarize(rules[n])}</option>
        ))}
        {ruleName && !rules[ruleName] && (
          <option value={ruleName}>{ruleName} (not defined in this mapping!)</option>
        )}
        <option value="__inline__">define a new rule here…</option>
      </select>

      {named && (
        <p className="help">
          Named rule from this mapping — one definition, every attribute using
          it compiles the same SQL. Renders:{" "}
          <code>{transformSql(named, "{x}").replace(/\n\s*/g, " ")}</code>
        </p>
      )}
      {ruleName && !named && (
        <p className="help">⚠ “{ruleName}” is not defined in this mapping.</p>
      )}

      {inline && (
        <InlineBuilder def={inline} set={setDef} nameTaken={nameTaken}
          saveName={saveName} setSaveName={setSaveName} saveAsRule={saveAsRule} />
      )}
    </div>
  );
}

function InlineBuilder({ def, set, saveName, setSaveName, saveAsRule, nameTaken }:
  { def: TransformDef; set: (d: TransformDef) => void;
    saveName: string; setSaveName: (s: string) => void; saveAsRule: () => void;
    nameTaken: boolean }) {
  const cases = def.cases ?? [];
  const steps = def.steps ?? [];
  const setCase = (i: number, c: (typeof cases)[number]) =>
    set({ ...def, cases: cases.map((x, k) => (k === i ? c : x)) });
  const setStep = (i: number, s: (typeof steps)[number]) =>
    set({ ...def, steps: steps.map((x, k) => (k === i ? s : x)) });
  const complete = !!(def.cases?.length || def.steps?.length || def.sqlExpr);

  return (
    <div style={{ borderLeft: "3px solid var(--light-purple)", paddingLeft: 14 }}>
      {/* value rewrite rules — evaluated in order, first match wins */}
      <label style={{ fontWeight: 700, fontSize: 12.5 }}>
        Conditional rewrites (checked in order — first match wins)
      </label>
      {cases.map((c, i) => (
        <div key={i} style={{ display: "grid", gap: 8, margin: "6px 0",
                              alignItems: "center", gridTemplateColumns:
                              "170px minmax(0,1fr) 160px minmax(0,1.4fr) 40px" }}>
          <select value={c.when.op}
            onChange={(e) => setCase(i, { ...c, when: { op: e.target.value as any,
              values: e.target.value === "isNull" ? [] : c.when.values ?? [] } })}>
            {WHEN_OPS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
          <input type="text" style={{ minWidth: 0 }} disabled={c.when.op === "isNull"}
            placeholder={c.when.op === "like" ? "pattern, e.g. /coverimage%" : "values (comma-separated)"}
            value={(c.when.values ?? []).join(",")}
            onChange={(e) => setCase(i, { ...c, when: { ...c.when,
              values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean) } })} />
          <select value={c.then.op}
            onChange={(e) => setCase(i, { ...c, then: { op: e.target.value as any } })}>
            {THEN_OPS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
          {["prepend", "append", "literal"].includes(c.then.op) ? (
            <input type="text" style={{ minWidth: 0 }}
              placeholder={c.then.op === "literal" ? "the fixed value" : "text, e.g. https://images.pearson.com"}
              value={c.then.text ?? ""}
              onChange={(e) => setCase(i, { ...c, then: { ...c.then, text: e.target.value } })} />
          ) : c.then.op === "replaceText" ? (
            <span style={{ display: "flex", gap: 6, minWidth: 0 }}>
              <input type="text" style={{ minWidth: 0, flex: 1 }} placeholder="find"
                value={c.then.find ?? ""}
                onChange={(e) => setCase(i, { ...c, then: { ...c.then, find: e.target.value } })} />
              <input type="text" style={{ minWidth: 0, flex: 1 }} placeholder="replace with"
                value={c.then.replaceWith ?? ""}
                onChange={(e) => setCase(i, { ...c, then: { ...c.then, replaceWith: e.target.value } })} />
            </span>
          ) : <span className="help" style={{ margin: 0 }}>—</span>}
          <button className="btn" style={{ padding: "3px 8px" }}
            onClick={() => set({ ...def, cases: cases.filter((_, k) => k !== i) })}>✕</button>
        </div>
      ))}
      <div className="checks" style={{ marginBottom: 8 }}>
        <button className="btn" onClick={() => set({ ...def, cases: [...cases,
          { when: { op: "startsWith", values: [] }, then: { op: "prepend", text: "" } }] })}>
          ＋ Add rewrite
        </button>
        {cases.length > 0 && (
          <label style={{ fontWeight: 500, display: "flex", gap: 6, alignItems: "center", fontSize: 12.5 }}>
            when nothing matches:
            <select value={def.else ?? "keep"}
              onChange={(e) => set({ ...def, else: e.target.value as "keep" | "null" })}>
              <option value="keep">keep the value as-is</option>
              <option value="null">make it null</option>
            </select>
          </label>
        )}
      </div>

      {/* pipeline steps — wrap the result in order */}
      <label style={{ fontWeight: 700, fontSize: 12.5 }}>
        Pipeline steps (applied in order, after any rewrites)
      </label>
      {steps.map((s, i) => (
        <div key={i} style={{ display: "grid", gap: 8, margin: "6px 0",
                              alignItems: "center",
                              gridTemplateColumns: "200px minmax(0,1fr) 40px" }}>
          <select value={s.op}
            onChange={(e) => setStep(i, { op: e.target.value as any })}>
            {STEP_OPS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
          {s.op === "round" ? (
            <input type="number" min={0} placeholder="decimal places, e.g. 2"
              value={s.precision ?? ""}
              onChange={(e) => setStep(i, { ...s, precision:
                e.target.value === "" ? undefined : Number(e.target.value) })} />
          ) : s.op === "cast" ? (
            <input type="text" placeholder="target type, e.g. DECIMAL(38,2)"
              value={s.to ?? ""}
              onChange={(e) => setStep(i, { ...s, to: e.target.value })} />
          ) : s.op === "coalesceTo" ? (
            <input type="text" placeholder="default value when null"
              value={s.value ?? ""}
              onChange={(e) => setStep(i, { ...s, value: e.target.value })} />
          ) : <span className="help" style={{ margin: 0 }}>—</span>}
          <button className="btn" style={{ padding: "3px 8px" }}
            onClick={() => set({ ...def, steps: steps.filter((_, k) => k !== i) })}>✕</button>
        </div>
      ))}
      <div className="checks" style={{ marginBottom: 8 }}>
        <button className="btn" onClick={() => set({ ...def, steps: [...steps,
          { op: "round", precision: 2 }] })}>
          ＋ Add step
        </button>
      </div>

      <details>
        <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12.5 }}>
          Custom SQL instead (engineering review required)
        </summary>
        <input type="text" style={{ marginTop: 6 }}
          placeholder="SQL fragment with {x} as the value, e.g. date_format({x}, 'yyyy-MM')"
          value={def.sqlExpr ?? ""}
          onChange={(e) => set({ ...def, sqlExpr: e.target.value || undefined })} />
        <p className="help">
          {"{x}"} stands for the source value. Every custom-SQL transform is
          flagged on the pull request for engineering review — prefer the
          structured parts above when they can express the rule.
        </p>
      </details>

      {complete && (
        <p className="help" style={{ marginTop: 8 }}>
          Renders: <code>{transformSql(def, "{x}").replace(/\n\s*/g, " ")}</code>
        </p>
      )}

      <div className="checks" style={{ marginTop: 8 }}>
        <input type="text" style={{ width: 220 }} value={saveName}
          placeholder="name it to reuse, e.g. myUrlRule"
          onChange={(e) => setSaveName(e.target.value)} />
        <button className="btn" onClick={saveAsRule}
          disabled={!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(saveName.trim()) || !complete
            || nameTaken}>
          Save as a named rule
        </button>
      </div>
      {nameTaken && (
        <p className="help">
          ⚠ “{saveName.trim()}” already exists in this mapping — pick a
          different name. Changing an existing rule is a reviewed edit of the
          mapping's transforms registry, not a wizard side effect.
        </p>
      )}
    </div>
  );
}
