import { useCallback, useEffect, useState, type ComponentType, type ReactNode } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  ScrollText,
  Layers,
  ChevronsLeft,
  ChevronsRight,
  Bell,
  User,
  ChevronDown,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { RegistrySearch } from '@/features/schemas/components/RegistrySearch';
import { SyncIndicator } from '@/features/schemas/components/SyncIndicator';
import { useSchemas } from '@/features/schemas';
import { useOverviewStats } from '@/features/schemas/hooks/use-overview-stats';
import { useServiceList } from '@/features/services/hooks/use-service-list';

interface AppShellProps {
  children: ReactNode;
}

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /**
   * Owns its own query, so `AppShell` stays query-free. Mounting the list
   * queries in the shell itself is what the split below exists to avoid.
   */
  Count?: ComponentType;
}

/**
 * Split out so `useSchemas` is only called on the registry list. Mounting it
 * in `AppShell` itself would fire the 500-subject list query on the publish
 * form and both services pages, none of which read it.
 *
 * The query is the same key the page uses, so TanStack Query serves both from
 * one request and the clock reports the fetch the table is actually showing.
 */
function RegistryHeaderControls() {
  const { dataUpdatedAt, isFetching } = useSchemas({ limit: 500 });
  return <SyncIndicator dataUpdatedAt={dataUpdatedAt} isFetching={isFetching} />;
}

/**
 * A count beside a nav entry.
 *
 * `undefined` renders nothing at all, which is the point: while the query is in
 * flight, and if it fails, there is no badge. A count that failed to load is
 * not zero, and a nav reading `Services 0` because a request 404'd is worse
 * than a nav that says nothing.
 */
function NavCount({ value }: { value: number | undefined }) {
  if (value === undefined) return null;
  return (
    <span className="ml-auto shrink-0 rounded bg-accent px-1.5 py-px font-mono text-[11px] leading-4 text-white">
      {value}
    </span>
  );
}

/**
 * Both counts narrow the payload before using it, rather than trusting its
 * declared type.
 *
 * That is not defensiveness for its own sake. These render inside the app
 * shell on every route, and there is no error boundary anywhere in the tree —
 * so a malformed response here throws during the shell's own render and takes
 * the nav, the header and the page with it. A decoration in the chrome must
 * not be able to do that. A shape that is not what was promised is treated the
 * same as a failed request: no badge.
 */
function SchemaCount() {
  // The overview endpoint, not the 500-subject list: one small JSON on a 60s
  // staleTime, and the same cache key the registry list already fills.
  const { data } = useOverviewStats();
  const total = data?.total_schemas;
  return <NavCount value={typeof total === 'number' ? total : undefined} />;
}

function ServiceCount() {
  const { data } = useServiceList();
  // Distinct URNs, not rows. The CMDB returns 16 entries for 14 services, so
  // the array length would overstate the catalogue by two — this is the same
  // figure `ServiceList` reports in its own notice about those repeats.
  const value = Array.isArray(data) ? new Set(data.map((s) => s.urn)).size : undefined;
  return <NavCount value={value} />;
}

// Non-compliant collapsed into a /schemas tab (governance dashboard UX refactor).
// Twin and Drift removed with the RDF graph (ADR-0007).
// Deploy and Product removed, routes and all. Proposals removed too; it was
// never routed, only an inert rail entry.
//
// AXIS groups its entries under small-caps section labels (RUN, BUILD,
// CONNECTORS, OBSERVE). There are none here: this app has two routed surfaces,
// and naming groups over them would mean writing section headings no design
// supplies. AXIS's own first entry sits above its first label, so one
// unlabelled group is the pattern's base case rather than a departure from it.
const NAV: NavItem[] = [
  { to: '/schemas', label: 'Schemas', icon: ScrollText, Count: SchemaCount },
  { to: '/services', label: 'Services', icon: Layers, Count: ServiceCount },
];

const NAV_WIDTH = 220;
const NAV_WIDTH_COLLAPSED = 72;
const COLLAPSE_KEY = 'uif.nav.collapsed';

/**
 * One left edge, shared by the wordmark, the product name, every nav icon and
 * the footer — 20px from the panel edge.
 *
 * A nav icon's offset is not one padding: it is the nav's own left padding,
 * plus the active item's left bar, plus the item's inner padding. So the last
 * of those is derived rather than picked, and the three cannot drift apart the
 * way they had — the label sat at 20px while the icons sat at 32px.
 */
const NAV_INSET = 20;
const NAV_BAR = 3;
const NAV_ITEM_PAD = 13;
const NAV_PAD_LEFT = NAV_INSET - NAV_BAR - NAV_ITEM_PAD;

/** Echo's 1x24 neutral rule, between the header's right-hand controls. */
function Divider() {
  return <div className="mx-1 h-6 w-px shrink-0 bg-paper-edge" aria-hidden />;
}

/** The panel's own hairline — Light Purple at low alpha on Pearson Purple. */
function NavRule() {
  return <div className="mx-4 border-t border-light/25" aria-hidden />;
}

/**
 * The nav panel follows the AgentOps AXIS pattern; the header follows Echo's.
 *
 * From AXIS: the collapse control is a square icon button in the panel's top
 * right rather than a row at the foot, the wordmark and that button share the
 * first row, the product name sits on its own row beneath, entries carry a
 * right-aligned count, the active entry takes an amber left bar inside its
 * pill rather than a white one, and the foot is a copyright line.
 *
 * Colour, type and content stay as they were, per the request: Pearson Purple
 * ground, #201360 active pill, Plus Jakarta Sans, and the same two entries.
 *
 * Four things in AXIS are not built, each because it would mean inventing
 * something rather than arranging what already exists:
 *
 *  - Section labels. See NAV above.
 *  - The diamond product mark. A second brand mark for this app does not
 *    exist, and drawing one is not a layout change.
 *  - The amber NEW pill. Nothing here is new in a sense any data knows about.
 *  - The external-link entry. No nav entry leaves the app.
 *
 * Echo's bell and user chip are in the header, by request, as PLACEHOLDERS —
 * and the distinction is the whole design of them. Neither has anything behind
 * it: the BFF exposes no notifications endpoint, and there is no signed-in
 * user at all. So they hold Echo's geometry without asserting anything untrue:
 *
 *  - The bell carries NO count badge. A number there would be invented, and an
 *    invented "3" is worse than no bell.
 *  - The chip shows a generic glyph, not an initial. An initial is an identity
 *    claim, and a hardcoded one would show the same stranger's name to every
 *    person who opens the app.
 *  - It reads "Not signed in", which is what is actually true, and agrees with
 *    the notice the registry list already carries ("Owned by me becomes a
 *    fourth segment once sign-in lands") instead of contradicting it one row
 *    above.
 *
 * Both are `aria-disabled` rather than `disabled`, so they stay focusable and
 * announce why they do nothing instead of being silently inert to a keyboard.
 *
 * Echo's title block takes a per-page heading and a sentence of description.
 * The heading is the section's own nav label; the description line is not
 * rendered, because no page in this app has subtitle copy and writing some is
 * not mine to do.
 */
export function AppShell({ children }: AppShellProps) {
  // The registry search and the sync clock belong to the schema list, not to
  // the shell — a search box above the publish form or the services list would
  // filter a table that is not on screen. They are rendered here because the
  // design puts them in the header, and gated on the one route that has a
  // table for them to describe.
  const { pathname } = useLocation();
  const isRegistryList = pathname === '/schemas';

  // Echo's header leads with the page title. The nav carries the product name,
  // so repeating it here put the same string on screen twice. These are the
  // nav's own labels rather than new copy, and a nested route (a subject page,
  // the publish form) reports the section it sits in.
  const section = NAV.find((item) => pathname.startsWith(item.to))?.label ?? NAV[0].label;

  const [collapsed, setCollapsed] = useState(() => {
    // A private window or blocked site data throws here rather than returning
    // null, so the read is guarded and an unreadable store just means expanded.
    try {
      return localStorage.getItem(COLLAPSE_KEY) === '1';
    } catch {
      return false;
    }
  });

  const toggleCollapsed = useCallback(() => setCollapsed((c) => !c), []);

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
    } catch {
      // Not worth surfacing: the nav still works, it just won't remember.
    }
  }, [collapsed]);

  const collapseButton = (
    <button
      type="button"
      onClick={toggleCollapsed}
      aria-expanded={!collapsed}
      aria-label={collapsed ? 'Expand' : 'Collapse'}
      title={collapsed ? 'Expand' : 'Collapse'}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-active text-light transition-colors hover:text-white"
    >
      {collapsed ? (
        <ChevronsRight size={18} strokeWidth={2} aria-hidden />
      ) : (
        <ChevronsLeft size={18} strokeWidth={2} aria-hidden />
      )}
    </button>
  );

  return (
    <div className="flex h-full">
      {/* Left nav — full height, Pearson Purple. The mark lives in the panel
        * rather than the header, which is why the header starts to the right
        * of it instead of spanning the window. */}
      <aside
        className="flex shrink-0 flex-col bg-sidebar transition-[width] duration-150 motion-reduce:transition-none"
        style={{ width: collapsed ? NAV_WIDTH_COLLAPSED : NAV_WIDTH }}
      >
        {/* Row one: the mark, and the collapse control at the far right — AXIS
          * puts it here rather than at the foot of the panel. */}
        <div
          className={cn(
            'flex items-center pb-3 pt-4',
            collapsed ? 'justify-center px-4' : 'gap-2',
          )}
          style={collapsed ? undefined : { paddingLeft: NAV_INSET, paddingRight: NAV_INSET }}
        >
          {/* The collapsed rail carries NO mark. It held the shorthand ripple,
            * which has been removed by request — and the rail is the one place
            * that is defensible for, since the brand's 100px digital minimum
            * means the 40px rail cannot show the wordmark either. The panel is
            * unbranded while collapsed; the header beside it is not.
            *
            * The wordmark is the white variant the colour-by-background table
            * calls for on a dark ground, and the design's own 219x44 export —
            * the dimensions Echo's nav gives it — so its artwork fills its box
            * with no dead canvas and its intrinsic size reserves the right
            * space before it loads. It replaced a 274KB PNG that was a raster
            * at a fixed size, so it softened on any HiDPI display.
            *
            * 104px is the smallest the brand allows (100px minimum). AXIS sets
            * its wordmark at about 41% of panel width — 91px here — so this is
            * as close to that reference as the minimum permits. The CLEAR SPACE
            * the guide requires, the height of the wordmark's "n", comes from
            * NAV_INSET rather than from padding inside the file, so tightening
            * that inset is a brand decision, not a spacing one. */}
          {collapsed ? (
            collapseButton
          ) : (
            <>
              <img src="/pearson-logo.svg" alt="Pearson" className="block h-auto w-[104px]" />
              <span className="ml-auto">{collapseButton}</span>
            </>
          )}
        </div>

        {!collapsed && (
          <p
            className="pb-4 text-[15px] font-semibold leading-5 text-paper"
            style={{ paddingLeft: NAV_INSET, paddingRight: NAV_INSET }}
          >
            Registry
          </p>
        )}

        <NavRule />

        <nav
          aria-label="Sections"
          className={cn('flex flex-col gap-1.5 py-3', collapsed && 'px-3')}
          style={collapsed ? undefined : { paddingLeft: NAV_PAD_LEFT, paddingRight: 16 }}
        >
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              // Always present, so the collapsed rail is still navigable by
              // name — an icon on its own has no accessible name at all.
              aria-label={item.label}
              title={collapsed ? item.label : undefined}
              style={
                collapsed
                  ? undefined
                  : {
                      borderLeftWidth: NAV_BAR,
                      borderLeftStyle: 'solid',
                      paddingLeft: NAV_ITEM_PAD,
                    }
              }
              className={({ isActive }) =>
                cn(
                  'flex h-10 items-center rounded-[10px] transition-colors',
                  collapsed ? 'justify-center border-l-[3px]' : 'gap-2.5 pr-2.5',
                  isActive
                    ? // AXIS marks the active entry with an amber bar rather
                      // than a white one. #FFCE00 is the palette's amber.
                      'border-amber bg-sidebar-active'
                    : 'border-transparent hover:bg-sidebar-active/60',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon
                    size={20}
                    strokeWidth={1.75}
                    className={cn('shrink-0', isActive ? 'text-white' : 'text-light')}
                    aria-hidden
                  />
                  {!collapsed && (
                    <>
                      <span
                        className={cn(
                          'truncate text-sm leading-5',
                          isActive ? 'font-semibold text-white' : 'text-light',
                        )}
                      >
                        {item.label}
                      </span>
                      {item.Count && <item.Count />}
                    </>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="flex-1" />

        <NavRule />

        {/* AXIS ends the panel with a copyright line rather than a version.
          * This keeps both: the copyright is the string Echo's own footer
          * carries, and the version is real and worth having when someone is
          * reporting what they are looking at. Collapsed, neither fits in
          * 40px, so the rail shows the mark alone and carries the full line as
          * its title.
          *
          * Light Purple on Pearson Purple is the palette's on-dark muted pair.
          * The previous rgba(255,255,255,.5) on a #10003a that is in no
          * Pearson palette came out at 4.0:1 — under AA for 10px text. */}
        {collapsed ? (
          <div
            className="py-3 text-center text-[11px] leading-4 text-light"
            title="© 2026 Pearson. All Rights Reserved. — v0.1.0"
          >
            ©
          </div>
        ) : (
          <div className="py-3" style={{ paddingLeft: NAV_INSET, paddingRight: NAV_INSET }}>
            <p className="text-[10px] leading-[14px] text-light">
              © 2026 Pearson. All Rights Reserved.
            </p>
            <p className="mt-0.5 font-mono text-[10px] leading-[14px] text-light">v0.1.0</p>
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header — white, per Echo. It was Pearson Purple when the nav was a
          * light 80px rail; with the nav dark and full height, a dark header
          * would have merged into it as one block of purple. */}
        <header className="flex h-16 shrink-0 items-center gap-6 border-b border-paper-edge bg-paper-card px-6">
          <p className="shrink-0 text-sm font-semibold leading-5 text-ink">{section}</p>

          {isRegistryList && <RegistrySearch />}

          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            {isRegistryList && (
              <>
                <RegistryHeaderControls />
                <Divider />
              </>
            )}

            <button
              type="button"
              aria-disabled
              aria-label="Notifications — not available yet"
              title="Notifications are not available yet"
              onClick={(e) => e.preventDefault()}
              className="flex h-9 w-9 cursor-default items-center justify-center rounded-xl text-ink-mute"
            >
              <Bell size={24} strokeWidth={1.75} aria-hidden />
            </button>

            <Divider />

            <button
              type="button"
              aria-disabled
              title="Sign-in is not available yet"
              onClick={(e) => e.preventDefault()}
              className="flex cursor-default items-center gap-2 rounded-xl px-2 py-1.5"
            >
              {/* Echo's 28px avatar and its 135° Amethyst gradient. A generic
                * glyph, not an initial — see the note above. */}
              <span
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
                style={{ backgroundImage: 'linear-gradient(135deg, #512EAB 0%, #7C52D0 100%)' }}
              >
                <User size={15} strokeWidth={2} className="text-white" aria-hidden />
              </span>
              <span className="flex flex-col items-start leading-none">
                <span className="text-xs font-semibold leading-4 text-ink-soft">Not signed in</span>
                <span className="text-[10px] leading-[14px] text-ink-mute">Sign-in pending</span>
              </span>
              <ChevronDown size={18} strokeWidth={2} className="shrink-0 text-ink-mute" aria-hidden />
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto bg-paper">
          <div className="mx-auto max-w-[1600px] px-6 py-8">{children}</div>
        </main>
      </div>
    </div>
  );
}
