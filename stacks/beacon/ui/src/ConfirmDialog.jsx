// ConfirmDialog (UIFPE-602): the one confirmation for destructive actions.
// Replaces window.confirm — an out-of-style browser popup that shows the
// site's hostname and says "OK" — and covers the actions that had no
// confirmation at all (Stop run, Remove schedule).
//
// Usage: const [confirm, confirmDialog] = useConfirm();
//        if (!(await confirm({ title, body, points, action }))) return;
//        …and render {confirmDialog} once in the component.
// `confirm` resolves true on the red action, false on Cancel, Escape or a
// click outside — so a stray keypress never destroys anything.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./ui.jsx";

function ConfirmDialog({ title, body, points = [], action, onResolve }) {
  const cancelRef = useRef(null);

  useEffect(() => {
    // focus the SAFE choice: Enter on a freshly opened dialog must not act
    cancelRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onResolve(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onResolve]);

  return (
    // UIFPE-716: the prototype's confirm — near the top of the screen, 520px,
    // title, a plain sentence and its points, then Cancel and an outlined red
    // action. Behaviour (focus on Cancel, Escape and a click outside cancel)
    // is unchanged.
    <div
      className="motion-fade-in fixed inset-0 z-50 flex items-start justify-center bg-pearson-purple/45 px-4 py-[4vh]"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onClick={() => onResolve(false)}
    >
      <div
        className="motion-pop-in w-full max-w-[520px] rounded-card bg-paper-card px-[26px] py-[22px] shadow-[0_24px_64px_rgb(13_0_77_/_0.35)]"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-dialog-title" className="mb-1.5 text-[18px] font-extrabold tracking-tight text-ink">
          {title}
        </h2>
        {body && <p className="mb-2.5 text-body leading-relaxed text-ink-soft">{body}</p>}
        {points.length > 0 && (
          <ul className="mb-1.5 list-disc space-y-1 pl-5 text-[13.5px] text-ink-soft">
            {points.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        <div className="mt-[18px] flex justify-end gap-2.5 border-t border-line pt-3.5">
          <Button ref={cancelRef} onClick={() => onResolve(false)}>
            Cancel
          </Button>
          <Button variant="danger" onClick={() => onResolve(true)}>
            {action}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** [confirm, dialog] — `confirm(options)` opens the dialog and resolves to
 *  whether the author chose the action; render `dialog` once. */
export function useConfirm() {
  const [pending, setPending] = useState(null);

  const confirm = useCallback(
    (options) => new Promise((resolve) => setPending({ ...options, resolve })),
    []
  );

  const onResolve = useCallback(
    (ok) =>
      setPending((p) => {
        p?.resolve(ok);
        return null;
      }),
    []
  );

  const dialog = pending ? (
    <ConfirmDialog
      title={pending.title}
      body={pending.body}
      points={pending.points}
      action={pending.action}
      onResolve={onResolve}
    />
  ) : null;

  return [confirm, dialog];
}
