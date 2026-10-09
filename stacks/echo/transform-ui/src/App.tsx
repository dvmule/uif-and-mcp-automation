import { Link, Outlet } from "react-router-dom";
import { readRailCollapsed } from "./lib/railPreference";

// RELAY shell reduced to what this MVP owns: the Transform section. Header
// and brand mirror Echo's AppShell — the SAME official Pearson wordmark asset
// (public/pearson-logo-white.svg), Pearson-Purple bar, divider, product name.
// The rail mirrors Echo's: every other entry is a full-page link into Echo,
// because this app is a separate bundle (UIFPE-97 folds it in).

type IconName = "plus" | "home" | "replicate" | "transform" | "insights" | "quality" | "catalog" | "export";

// Echo rail entries this app links out to (paths as Echo's railConfig.ts).
const ECHO_LINKS: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Home", icon: "home" },
  { href: "/m/data-sync/syncs", label: "Replicate", icon: "replicate" },
];
const ECHO_LINKS_AFTER: { href: string; label: string; icon: IconName }[] = [
  { href: "/m/data-sync", label: "Insights", icon: "insights" },
  { href: "/m/data-quality/checks", label: "Quality", icon: "quality" },
  { href: "/m/catalog/browse", label: "Catalog", icon: "catalog" },
];
const BEACON_HREF = "/m/beacon/index.html#/m/beacon";

export function Shell() {
  const collapsed = readRailCollapsed();
  const tip = (label: string) => (collapsed ? { "aria-label": label, title: label } : {});

  const echoLink = (l: { href: string; label: string; icon: IconName }) => (
    <a key={l.label} className="nav-item" href={l.href} {...tip(l.label)}>
      <RailIcon name={l.icon} />
      {!collapsed && l.label}
    </a>
  );

  return (
    <div className="shell">
      <header className="appbar">
        <img src="pearson-logo-white.svg" alt="Pearson"
          style={{ height: 26, width: "auto", display: "block" }} />
        <div className="appbar-divider" />
        <span className="appbar-title">Universal Integration Fabric</span>
      </header>
      <div className="shell-body">
        <nav aria-label="RELAY Echo menu" className={"sidebar" + (collapsed ? " collapsed" : "")}>
          <span className="rail-heading">RELAY Echo</span>
          <Link to="/transform/new" className="rail-cta" {...tip("New transformation")}>
            <RailIcon name="plus" />
            {!collapsed && "New transformation"}
          </Link>
          {ECHO_LINKS.map(echoLink)}
          <Link to="/transform" className="nav-item active" aria-current="page" {...tip("Transform")}>
            <RailIcon name="transform" />
            {!collapsed && "Transform"}
          </Link>
          {ECHO_LINKS_AFTER.map(echoLink)}
          <div role="separator" className="rail-divider" />
          <a className="nav-item" href={BEACON_HREF} {...tip("Export · Beacon")}>
            <RailIcon name="export" />
            {!collapsed && <>Export · Beacon<span className="ext" aria-hidden="true">↗</span></>}
          </a>
        </nav>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

// Outline icons drawn like Echo's rail (lucide paths, 24-unit grid, 1.75 stroke).
const ICON_PATHS: Record<IconName, string[]> = {
  plus: ["M5 12h14", "M12 5v14"],
  home: [
    "M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8",
    "M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  ],
  replicate: [
    "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8", "M21 3v5h-5",
    "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16", "M8 16H3v5",
  ],
  transform: [
    "m18 14 4 4-4 4", "m18 2 4 4-4 4", "M2 18h1.973a4 4 0 0 0 3.3-1.7l5.454-8.6a4 4 0 0 1 3.3-1.7H22",
    "M2 6h1.972a4 4 0 0 1 3.6 2.2", "M22 18h-6.041a4 4 0 0 1-3.3-1.8l-.359-.45",
  ],
  insights: ["M3 3v16a2 2 0 0 0 2 2h16", "m19 9-5 5-4-4-3 3"],
  quality: [
    "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z",
    "m9 12 2 2 4-4",
  ],
  catalog: [
    "M12 7v14",
    "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z",
  ],
  export: [
    "M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z",
    "m21.854 2.147-10.94 10.939",
  ],
};

function RailIcon({ name }: { name: IconName }) {
  return (
    <svg className="rail-icon" width="17" height="17" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true">
      {ICON_PATHS[name].map(d => <path key={d} d={d} />)}
    </svg>
  );
}
