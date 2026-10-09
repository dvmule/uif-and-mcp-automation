// App shell: Pearson-purple topbar + one section rail + scrolling main.
// This is a deliberate clone of the UIF platform shell (uif-relay-echo UI)
// so Beacon can be dropped in as the /m/beacon module later without
// re-layout. UIFPE-536: the 88px "Modules" icon rail is gone — this is a
// single-module deployment, so the Beacon menu is the only navigation
// (Home merged into Overview, Health lives under Runs).
// UIFPE-693: the menu reads New Export · Home · Exports · Destinations ·
// Insights — Health left the menu (its page and the top-bar status link
// stay), the page ground carries the faint RELAY panels.
import React, { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  House,
  ChevronDown,
  Compass,
  Library,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Share2,
  Send,
  History,
  Sparkles,
  User,
  Users,
} from "lucide-react";
import { api } from "./api.js";
import { clearShellSession, readShellSession, redirectToLogin } from "./lib/session.js";
import { TopLoader } from "./ui.jsx";
import { readRailCollapsed, writeRailCollapsed } from "./lib/railPreference.js";
import Tour from "./Tour.jsx";
// bundled (not referenced from the distribution root) so the same build
// works on the shared RELAY distribution, the standalone one, and localhost
import pearsonLogo from "./assets/pearson-logo-white.svg";

const MODULE = "/m/beacon";

const EXPORT_RAIL = {
  heading: "Beacon",
  footer: "v0.7",
  items: [
    { to: `${MODULE}/exports/new`, label: "New Export", icon: Sparkles, quick: true },
    { to: `${MODULE}`, label: "Home", icon: House, end: true },
    { to: `${MODULE}/exports`, label: "Exports", icon: Share2 },
    { to: `${MODULE}/destinations`, label: "Destinations", icon: Send },
    // UIFPE-674: labelled Insights; the address stays /runs so links keep working
    { to: `${MODULE}/runs`, label: "Insights", icon: History },
    // UIFPE-693: Health is no longer a menu item — /m/beacon/health still
    // works and the top bar's API-status link still opens it
  ],
};

const SETTINGS_RAIL = {
  heading: "Settings",
  items: [
    { to: "/settings/registries", label: "Registries", icon: Library },
    { to: "/settings/roles", label: "Roles & access", icon: Users },
  ],
};

// UIFPE-548: the top-bar profile menu — who is signed in, and Sign out.
// Sign out drops the shared shell session and leaves for the shell login
// (with the usual ?return= back here). Client-side only, like Echo's logout.
function UserMenu({ username }) {
  const [open, setOpen] = useState(false);
  const signOut = () => {
    clearShellSession();
    redirectToLogin();
  };
  return (
    <div className="relative hidden sm:block">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded px-1.5 py-0.5 text-meta text-white/75 transition-colors hover:bg-white/10 hover:text-white"
        title="Signed in via the RELAY shell"
      >
        <User size={14} strokeWidth={1.9} /> {username}
        <ChevronDown size={12} strokeWidth={2} />
      </button>
      {open && (
        <>
          {/* click-away backdrop — the menu must never trap the page */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="absolute right-0 top-full z-50 mt-1.5 w-56 rounded-brand border border-paper-line/60 bg-paper-card p-1.5 text-ink shadow-lg"
          >
            <div className="px-2 py-1.5 text-[11.5px] text-ink-mute">
              Signed in via the RELAY shell as
              <div className="truncate font-semibold text-ink">{username}</div>
            </div>
            <div className="my-1 border-t border-paper-line/50" />
            <button
              role="menuitem"
              onClick={signOut}
              className="flex w-full items-center gap-2 rounded-brand px-2 py-1.5 text-left text-[12.5px] text-ink hover:bg-accent-soft"
            >
              <LogOut size={14} strokeWidth={1.75} /> Sign out
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// UIFPE-637: shown beside an icon when the rail is collapsed — on hover and on
// keyboard focus, so the label is never only a mouse affordance
function RailTip({ label }) {
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute left-full top-1/2 z-40 ml-3 -translate-y-1/2 whitespace-nowrap rounded-brand bg-pearson-purple px-2 py-1 text-meta font-semibold text-white opacity-0 shadow-card transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
    >
      {label}
    </span>
  );
}

function SectionRail({ config, collapsed, onToggle }) {
  return (
    // UIFPE-623: rail polish — a fixed icon slot so labels align, the CTA as
    // the primary button, the active item marked on the rail's edge.
    // UIFPE-637: collapsible to an icon rail; the choice is remembered.
    // UIFPE-667: full-width 44px rows, a tinted active row with a 3px edge,
    // the CTA as a pill, and Collapse at the foot of the rail.
    <nav
      aria-label={`${config.heading} menu`}
      className={`flex shrink-0 flex-col border-r border-line bg-paper-card transition-[width] duration-200 ${
        collapsed ? "w-[64px]" : "w-[212px]"
      }`}
    >
      <div className={`pt-4 pb-2 ${collapsed ? "text-center" : "px-5"}`}>
        {collapsed ? (
          <span className="sr-only">{config.heading}</span>
        ) : (
          <span className="text-label font-semibold uppercase tracking-wider text-ink-mute">
            {config.heading}
          </span>
        )}
      </div>
      <div className="flex flex-col">
        {config.items.map(({ to, label, icon: Icon, end, quick, disabled }) =>
          disabled ? (
            <span
              key={label}
              title={collapsed ? `${label} (later)` : undefined}
              className={`flex h-11 cursor-not-allowed items-center gap-3 border-l-[3px] border-transparent text-sm opacity-40 ${
                collapsed ? "justify-center px-0" : "px-5"
              }`}
            >
              <Icon size={16} strokeWidth={1.75} className="shrink-0" />
              {collapsed ? <span className="sr-only">{label}</span> : label}
              {!collapsed && <span className="ml-auto text-[9px] font-semibold uppercase tracking-wide">later</span>}
            </span>
          ) : (
            <NavLink
              key={label}
              to={to}
              end={end}
              aria-label={collapsed ? label : undefined}
              className={({ isActive }) =>
                quick
                  ? `group relative mb-3 flex h-10 items-center justify-center gap-2 rounded-full bg-pearson-purple text-body font-bold text-white transition-colors hover:bg-accent ${
                      collapsed ? "mx-2.5 px-0" : "mx-4 px-4"
                    }`
                  : `group relative flex h-11 items-center gap-3 border-l-[3px] text-sm transition-colors ${
                      collapsed ? "justify-center px-0" : "px-5"
                    } ${
                      isActive
                        ? "border-accent bg-accent-soft font-bold text-accent"
                        : "border-transparent font-medium text-ink hover:bg-accent-soft"
                    }`
              }
            >
              <Icon size={17} strokeWidth={1.75} className="shrink-0" />
              {collapsed ? <RailTip label={label} /> : <span className="motion-fade-in">{label}</span>}
            </NavLink>
          )
        )}
      </div>
      <div className="mt-auto border-t border-line pt-1.5">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand menu" : "Collapse menu"}
          title={collapsed ? "Expand menu" : "Collapse menu"}
          className={`flex h-10 w-full items-center gap-3 text-sm font-medium text-ink transition-colors hover:bg-accent-soft ${
            collapsed ? "justify-center px-0" : "px-5"
          }`}
        >
          {collapsed ? (
            <PanelLeftOpen size={17} strokeWidth={1.75} className="shrink-0 text-ink-mute" />
          ) : (
            <PanelLeftClose size={17} strokeWidth={1.75} className="shrink-0 text-ink-mute" />
          )}
          {!collapsed && <span className="motion-fade-in">Collapse</span>}
        </button>
        {config.footer && (
          <div className={`pb-3 pt-1 text-label text-ink-mute ${collapsed ? "px-0 text-center" : "px-5"}`}>
            {config.footer}
          </div>
        )}
      </div>
    </nav>
  );
}

export default function Shell() {
  const { pathname } = useLocation();
  const [health, setHealth] = useState(null);
  // UIFPE-487: read once per mount — the session cannot change without a full
  // page round-trip through the shell login, which remounts everything.
  const [session] = useState(readShellSession);
  // UIFPE-445: guided tour — started from the top bar or the first-visit offer
  const [tourOpen, setTourOpen] = useState(false);
  // UIFPE-637: the left menu can collapse to icons; remembered per browser
  const [railCollapsed, setRailCollapsed] = useState(() => readRailCollapsed(window.localStorage));
  const toggleRail = () =>
    setRailCollapsed((c) => {
      writeRailCollapsed(window.localStorage, !c);
      return !c;
    });

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: "unreachable" }));
  }, []);

  // UIFPE-536: one menu for everything except Settings — "/" redirects to
  // the overview, and Health is a Beacon menu item now
  const rail = pathname.startsWith("/settings") ? SETTINGS_RAIL : EXPORT_RAIL;
  const env = (health?.env || "sandbox").toUpperCase();
  const apiStatus =
    health?.status === "ok"
      ? `API connected${health.engine_available ? " · engine ✓" : ""}`
      : (health?.status ?? "connecting…");

  return (
    <div className="relative isolate flex h-full flex-col">
      {/* UIFPE-693: the RELAY page ground — soft tilted panels behind every
          screen, decorative only */}
      <div className="beacon-ground" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      {/* UIFPE-667: a taller bar with the product name. UIFPE-729 (owner): it
          says Beacon — this is Beacon, not the RELAY family name */}
      <header className="flex h-16 shrink-0 items-center gap-4 bg-pearson-purple px-6 text-white">
        <img src={pearsonLogo} alt="Pearson" className="h-[26px] w-auto" />
        <span className="whitespace-nowrap text-[15px] font-bold tracking-tight">
          Beacon
        </span>
        {/* one env badge, from /health — the old extra "Sandbox" marker
            mislabelled every non-prod env (UIFPE-195) */}
        <span
          aria-label={`Environment: ${env}`}
          className="rounded-full bg-white/15 px-2 py-0.5 text-label font-semibold uppercase tracking-wide"
        >
          {env}
        </span>
        <div className="ml-auto flex items-center gap-3">
          {/* summary only — the detail lives on the Health module (UIFPE-195) */}
          {/* UIFPE-667: below 1024px only the dot shows; the words stay for
              screen readers and in the tooltip, so the taller bar never wraps */}
          <NavLink
            to={`${MODULE}/health`}
            title={apiStatus}
            className="hidden items-center gap-1.5 whitespace-nowrap rounded px-1.5 py-0.5 text-meta text-white/75 transition-colors hover:bg-white/10 hover:text-white sm:flex"
          >
            <span
              className={`h-2 w-2 rounded-full ${
                health?.status === "ok" ? "bg-turq" : "bg-pearson-amber"
              }`}
            />
            <span className="max-lg:sr-only">{apiStatus}</span>
          </NavLink>
          {/* UIFPE-487: the signed-in shell user — same session the shell
              header shows; absent on an auth-off deployment (local dev).
              UIFPE-548: now a menu with Sign out, like Echo's own profile. */}
          {session && <UserMenu username={session.username} />}
          {/* UIFPE-445: restartable any time, not only on first visit */}
          <button
            onClick={() => setTourOpen(true)}
            className="flex items-center gap-1.5 rounded px-1.5 py-0.5 text-meta text-white/75 transition-colors hover:bg-white/10 hover:text-white"
            title="A guided walk through every screen, in plain words"
          >
            <Compass size={14} strokeWidth={1.9} /> Tour
          </button>
          <NavLink
            to="/settings/registries"
            aria-label="Settings"
            className="rounded p-1 text-white/80 hover:bg-white/10 hover:text-white"
          >
            <Settings size={16} strokeWidth={1.9} />
          </NavLink>
        </div>
      </header>

      {/* UIFPE-418: global progress bar — any in-flight API call lights it
          (after a 200ms grace), so a cold Lambda never looks like a dead UI */}
      <TopLoader />

      <div className="flex min-h-0 flex-1">
        {rail && <SectionRail config={rail} collapsed={railCollapsed} onToggle={toggleRail} />}
        <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">
          {/* UIFPE-768: the page content rises in on every route change (keyed on
              the path, so a filter change inside a page does not replay it) */}
          <div key={pathname} className="motion-page-in flex-1 p-6">
            <Outlet />
          </div>

          {/* UIFPE-667: the site footer — Pearson's own terms and privacy pages,
              opened in a new tab so an author never loses the page they are on.
              UIFPE-676: it ends the page content and scrolls with it; on a short
              page the content grows, so it still sits at the bottom */}
          <footer className="shrink-0 border-t border-line bg-paper-card px-6 py-2.5 text-center text-meta text-ink-mute">
            <a
              href="https://www.pearson.com/en-us/legal-information/terms-of-use.html"
              target="_blank"
              rel="noreferrer"
              className="font-semibold text-accent hover:underline"
            >
              Terms
            </a>
            <span className="mx-2" aria-hidden="true">|</span>
            <a
              href="https://www.pearson.com/en-us/privacy-center/privacy-notices.html"
              target="_blank"
              rel="noreferrer"
              className="font-semibold text-accent hover:underline"
            >
              Privacy
            </a>
            <div>Copyright © {new Date().getFullYear()} Pearson Education Inc. or its affiliate(s). All rights reserved.</div>
          </footer>
        </main>
      </div>

      <Tour
        open={tourOpen}
        onStart={() => setTourOpen(true)}
        onClose={() => setTourOpen(false)}
      />
    </div>
  );
}
