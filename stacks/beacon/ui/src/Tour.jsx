// Tour.jsx (UIFPE-445)
// Guided tour overlay: a spotlight over the real element plus a small card
// with one plain sentence per stop. Deliberately our own ~150 lines instead
// of a tour framework — the steps are data in lib/tour.js, this file only
// renders them. Also owns the one-time "take a tour?" offer for first visits.
import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Compass, X } from "lucide-react";
import { Button } from "./ui.jsx";
import { TOUR_STEPS, markTourSeen, placeCard, seenTour } from "./lib/tour.js";

// placement estimate, clamped anyway (UIFPE-630: the eyebrow and progress
// bar made the card ~20px taller)
const CARD = { width: 320, height: 210 };

export default function Tour({ open, onStart, onClose }) {
  const [idx, setIdx] = useState(0);
  const [rect, setRect] = useState(null); // null = no target on screen (centered card)
  const [offer, setOffer] = useState(() => !seenTour());
  const navigate = useNavigate();
  const location = useLocation();
  const step = open ? TOUR_STEPS[idx] : null;

  useEffect(() => {
    if (open) {
      setIdx(0);
      setOffer(false);
      markTourSeen();
    }
  }, [open]);

  // Each step: go to its screen, then keep measuring its element — the
  // interval also follows scrolling, resizes, and late-loading tables.
  useEffect(() => {
    if (!step) return undefined;
    if (location.pathname !== step.route) navigate(step.route);
    let scrolled = false;
    const measure = () => {
      const el = step.target
        ? document.querySelector(`[data-tour="${step.target}"]`)
        : null;
      if (!el) {
        setRect(null);
        return;
      }
      if (!scrolled) {
        el.scrollIntoView({ block: "center" });
        scrolled = true;
      }
      const r = el.getBoundingClientRect();
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };
    measure();
    const iv = setInterval(measure, 200);
    return () => clearInterval(iv);
    // location.pathname is deliberately not a dep: navigation mid-step would
    // re-run and re-navigate; the interval re-measures regardless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, idx]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight" && idx < TOUR_STEPS.length - 1) setIdx(idx + 1);
      if (e.key === "ArrowLeft" && idx > 0) setIdx(idx - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, idx, onClose]);

  // ---- first-visit offer (shown until answered once, remembered per browser)
  if (!open) {
    if (!offer) return null;
    return (
      <div className="fixed bottom-4 right-4 z-50 w-[300px] rounded-card border border-line bg-paper-card p-4 shadow-[0_14px_36px_-16px_rgb(13_0_77_/_0.45)]">
        {/* UIFPE-630: on the card system — an eyebrow, a heading, the same two buttons */}
        <div className="flex items-center gap-1.5 text-label font-semibold uppercase tracking-wider text-pearson-amethyst">
          <Compass size={13} /> Guided tour
        </div>
        <div className="mt-1 text-[14px] font-semibold text-ink">New here?</div>
        <p className="mt-1 text-meta leading-relaxed text-ink-soft">
          Take a two-minute tour — it walks through every screen in plain
          words.
        </p>
        <div className="mt-3 flex gap-2">
          <Button variant="primary" size="sm" onClick={onStart}>
            Take the tour
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              markTourSeen();
              setOffer(false);
            }}
          >
            No thanks
          </Button>
        </div>
      </div>
    );
  }

  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const pos = placeCard(rect, viewport, CARD);
  const last = idx === TOUR_STEPS.length - 1;

  return (
    <div className="fixed inset-0 z-50">
      {/* dim everything; the spotlight div's giant shadow re-dims around the
          target, so the target itself stays at full brightness */}
      {rect ? (
        <div
          className="absolute rounded-xl transition-all duration-200"
          style={{
            top: rect.top - 6,
            left: rect.left - 6,
            width: rect.width + 12,
            height: rect.height + 12,
            boxShadow: "0 0 0 200vmax rgba(13, 0, 77, 0.55)",
          }}
        />
      ) : (
        <div className="absolute inset-0 bg-pearson-purple/55" />
      )}

      <div
        role="dialog"
        aria-label={step.title}
        className="absolute w-[320px] rounded-card border border-line bg-paper-card p-4 shadow-[0_14px_36px_-16px_rgb(13_0_77_/_0.45)] transition-all duration-200"
        style={{ top: pos.top, left: pos.left }}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            {/* UIFPE-630: the step count moves up into an eyebrow */}
            <div className="tnum text-label font-semibold uppercase tracking-wider text-pearson-amethyst">
              Step {idx + 1} of {TOUR_STEPS.length}
            </div>
            <div className="mt-0.5 text-[14px] font-semibold text-ink">{step.title}</div>
          </div>
          <button
            aria-label="Close tour"
            onClick={onClose}
            className="rounded p-0.5 text-ink-mute hover:bg-accent-soft hover:text-ink"
          >
            <X size={14} />
          </button>
        </div>
        <p className="mt-1.5 text-meta leading-relaxed text-ink-soft">{step.body}</p>
        {/* UIFPE-630: progress, drawn from the same step index */}
        <div className="mt-3 h-[3px] overflow-hidden rounded-full bg-line" aria-hidden="true">
          <div
            className="h-full rounded-full bg-pearson-amethyst transition-[width] duration-200"
            style={{ width: `${((idx + 1) / TOUR_STEPS.length) * 100}%` }}
          />
        </div>
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={onClose}
            className="text-meta text-ink-mute underline-offset-2 hover:underline"
          >
            Skip tour
          </button>
          <span className="ml-auto flex gap-2">
            {idx > 0 && (
              <Button size="sm" onClick={() => setIdx(idx - 1)}>
                Back
              </Button>
            )}
            <Button
              variant="primary"
              size="sm"
              onClick={() => (last ? onClose() : setIdx(idx + 1))}
            >
              {last ? "Finish" : "Next"}
            </Button>
          </span>
        </div>
      </div>
    </div>
  );
}
