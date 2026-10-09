import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/utils';
import { DOMAIN_COLOURS } from '@/features/schemas';
import type { DataClassification, SchemaSummary } from '@/features/schemas';
import type { ServiceDependency } from '../types/service.types';
import { useServicePortfolio } from '../hooks/use-service-portfolio';
import { ServiceDependencyGraph } from './ServiceDependencyGraph';

/**
 * 6b — one service's portfolio, read-only by design.
 *
 * Everything on this page is a view of the CMDB and of which schemas name this
 * service as their owner. Nothing here can be edited, which the closing card
 * says out loud rather than leaving the reader to discover by looking for a
 * button that is not there.
 */

interface ServicePortfolioProps {
  urn: string;
}

type Tab = 'produces' | 'consumes' | 'dependents' | 'graph';

/** `restricted` is warn, matching the schema pages. It used to be `fail` here
  * and `warn` there, so the same classification had two colours depending on
  * which screen you were looking at. */
const classificationTone: Record<DataClassification, 'neutral' | 'accent' | 'warn'> = {
  public: 'neutral',
  internal: 'accent',
  confidential: 'warn',
  restricted: 'warn',
};

const CARD = 'bg-paper-card border border-paper-line';
const CARD_STYLE = { borderRadius: 16, padding: 20 } as const;

export function ServicePortfolio({ urn }: ServicePortfolioProps) {
  const [activeTab, setActiveTab] = useState<Tab>('produces');
  const { data, isLoading, error } = useServicePortfolio(urn);

  if (isLoading) {
    return <p className="text-sm text-ink-mute">Loading portfolio…</p>;
  }

  if (error || !data) {
    return (
      <div className="space-y-2">
        <p className="text-sm font-medium text-ink">Could not load portfolio</p>
        <p className="font-mono text-sm text-signal-fail">
          {(error as Error)?.message ?? 'Unknown error'}
        </p>
      </div>
    );
  }

  // Distinct SERVICES, not edges. A service can be both upstream and
  // downstream of this one — it consumes a schema this one produces and
  // produces one this one consumes — and summing the two lists counted it
  // twice, so the badge could claim more dependents than the graph beside it
  // draws nodes for.
  const dependentsCount = new Set([
    ...data.dependencies.upstream.map((d) => d.ci_id),
    ...data.dependencies.downstream.map((d) => d.ci_id),
  ]).size;

  return (
    <div className="flex flex-col" style={{ gap: 16 }}>
      <Link
        to="/services"
        className="flex items-center gap-1.5 self-start text-[13px] font-semibold text-accent hover:underline"
      >
        <ArrowLeft size={15} />
        All services
      </Link>

      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <h1 className="font-mono text-2xl font-semibold text-ink break-all">{data.name}</h1>
          <Badge tone="accent">{data.domain}</Badge>
          {/* Said in the title, because it governs everything below it. */}
          <Badge tone="neutral">read-only</Badge>
        </div>
        <p className="mt-1 font-mono text-[12.5px] text-ink-mute break-all">{data.urn}</p>
        {data.app_name && (
          <p className="mt-1 text-[13px] text-ink-soft">
            Application: <span className="font-semibold text-ink">{data.app_name}</span>
            {data.app_id && <span className="ml-1 font-mono text-ink-mute">({data.app_id})</span>}
          </p>
        )}
      </div>

      {/* ---------------------------------------------------- facts band */}
      <div
        className={cn('grid', CARD)}
        style={{
          gridTemplateColumns: 'repeat(4, minmax(0,1fr))',
          gap: '16px 24px',
          borderRadius: 16,
          padding: '16px 20px',
        }}
      >
        <Fact label="Domain">
          <span className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="shrink-0"
              style={{
                width: 3,
                height: 12,
                backgroundColor: DOMAIN_COLOURS[data.domain] ?? 'transparent',
              }}
            />
            {data.domain}
          </span>
        </Fact>
        <Fact label="Owner CI">{data.ci_id}</Fact>
        <Fact label="Produces">{`${data.produces.length} schemas`}</Fact>
        <Fact label="Consumes">{`${data.consumes.length} schemas`}</Fact>
      </div>

      {/* --------------------------------------------------------- body */}
      <div
        className="grid items-start"
        style={{ gridTemplateColumns: 'minmax(0,1fr) 300px', gap: 20 }}
      >
        <div className={cn('min-w-0', CARD)} style={CARD_STYLE}>
          <div className="flex gap-1 flex-wrap">
            <TabButton
              label="Produces"
              count={data.produces.length}
              active={activeTab === 'produces'}
              onClick={() => setActiveTab('produces')}
            />
            <TabButton
              label="Consumes"
              count={data.consumes.length}
              active={activeTab === 'consumes'}
              onClick={() => setActiveTab('consumes')}
            />
            <TabButton
              label="Dependents"
              count={dependentsCount}
              active={activeTab === 'dependents'}
              onClick={() => setActiveTab('dependents')}
            />
            <TabButton
              label="Graph"
              count={dependentsCount}
              active={activeTab === 'graph'}
              onClick={() => setActiveTab('graph')}
            />
          </div>

          <div className="mt-4">
            {activeTab === 'produces' && (
              <PortfolioSchemaTable
                items={data.produces}
                empty="This service produces no schemas."
              />
            )}
            {activeTab === 'consumes' && (
              <PortfolioSchemaTable
                items={data.consumes}
                empty="This service consumes no schemas."
              />
            )}
            {activeTab === 'dependents' && (
              <DependentsPanel
                upstream={data.dependencies.upstream}
                downstream={data.dependencies.downstream}
              />
            )}
            {activeTab === 'graph' && <ServiceDependencyGraph data={data} />}
          </div>
        </div>

        {/* ------------------------------------------------------- rail */}
        <aside className="flex flex-col" style={{ gap: 12 }}>
          <div className={CARD} style={{ borderRadius: 16, padding: '16px 18px' }}>
            <h2 className="text-[15px] font-semibold text-ink">Dependents</h2>
            {/* The most important sentence on the page: nobody declared these.
              * They are inferred from schemas two services share, so a
              * dependency missing here means no shared schema, not no
              * relationship. */}
            <p className="mt-1 text-xs text-ink-mute">
              Derived from shared schemas, not declared anywhere.
            </p>

            <div
              className="mt-3 bg-paper-card"
              style={{ border: '2px solid #512EAB', borderRadius: 8, padding: '10px 12px' }}
            >
              <p className="text-[13px] font-semibold text-ink break-all">{data.name}</p>
              <div className="mt-1">
                <Badge tone="accent">{data.domain}</Badge>
              </div>
              <p className="mt-1.5 font-mono text-xs text-ink-mute">
                {data.produces.length} produces · {data.consumes.length} consumes
              </p>
            </div>

            <div className="mt-4 flex flex-col" style={{ gap: 14 }}>
              <RailSection
                title="Downstream — depends on me"
                subtitle="Consume schemas this service produces"
                items={data.dependencies.downstream}
                empty="No services consume this service's schemas."
              />
              <RailSection
                title="Upstream — I depend on"
                subtitle="Produce schemas this service consumes"
                items={data.dependencies.upstream}
                empty="This service does not consume schemas from other services."
              />
            </div>

            <p className="mt-4 text-xs text-ink-mute">
              The Graph tab lays these out with dagre — upstream to the left of {data.name},
              downstream to the right.
            </p>
          </div>

          <div className={cn(CARD, 'border-light')} style={{ borderRadius: 16, padding: '16px 18px' }}>
            <h2 className="text-[15px] font-semibold text-ink">Nothing to edit here</h2>
            <p className="mt-1.5 text-[13px] leading-[1.45] text-ink-soft">
              Service records come from the CMDB. To change an owner or an application, change
              it there — the registry will pick it up on the next sync.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  const empty = children === null || children === undefined || children === '';
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-mute">{label}</dt>
      <dd className="mt-0.5 font-mono text-[13px] text-ink break-all">
        {empty ? <span className="text-ink-mute">—</span> : children}
      </dd>
    </div>
  );
}

function TabButton({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'flex items-center gap-2 text-sm',
        active ? 'bg-paper text-accent font-semibold' : 'bg-transparent text-ink-soft font-medium',
      )}
      style={{ borderRadius: 8, padding: '7px 12px' }}
    >
      {label}
      <span
        className="bg-paper-line font-mono text-xs text-ink-soft"
        style={{ borderRadius: 4, padding: '2px 6px' }}
      >
        {count}
      </span>
    </button>
  );
}

/** Subject / Classification / Type / Lifecycle / Owner — the design's five.
  * Domain is gone: every row here belongs to one service, whose domain is in
  * the facts band above, so the column repeated one value down the page. */
const PORTFOLIO_COLS: (number | null)[] = [null, 104 + 14, 70 + 14, 108 + 14, 100 + 14];

function PortfolioSchemaTable({ items, empty }: { items: SchemaSummary[]; empty: string }) {
  if (items.length === 0) {
    return <p className="py-4 text-sm italic text-ink-mute">{empty}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed text-sm" style={{ minWidth: 640 }}>
        <colgroup>
          {PORTFOLIO_COLS.map((w, i) => (
            <col key={i} style={w === null ? undefined : { width: w }} />
          ))}
        </colgroup>
        <thead className="bg-paper-raised">
          <tr style={{ height: 36 }}>
            <th className="px-1.5 text-left text-[11px] font-medium text-accent">Subject</th>
            <th className="px-1.5 text-left text-[11px] font-medium text-accent">Classification</th>
            <th className="px-1.5 text-left text-[11px] font-medium text-accent">Type</th>
            <th className="px-1.5 text-left text-[11px] font-medium text-accent">Lifecycle</th>
            <th className="px-1.5 text-left text-[11px] font-medium text-accent">Owner</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <SchemaRow key={`${s.group_id}/${s.artifact_id}`} item={s} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SchemaRow({ item }: { item: SchemaSummary }) {
  const isDeprecated = item.lifecycle === 'deprecated';
  return (
    <tr className="border-b border-paper-divider" style={{ height: 42 }}>
      <td className="px-1.5">
        <Link
          to={`/schemas/${item.group_id}/${item.artifact_id}`}
          className={cn(
            'font-mono text-[12.5px] hover:underline',
            isDeprecated ? 'line-through text-ink-mute' : 'text-accent',
          )}
        >
          {item.artifact_id}
        </Link>
      </td>
      <td className="px-1.5">
        {item.classification ? (
          <Badge tone={classificationTone[item.classification]}>{item.classification}</Badge>
        ) : (
          <Muted>unset</Muted>
        )}
      </td>
      <td className="px-1.5 font-mono text-xs text-ink-mute">{item.type}</td>
      <td className="px-1.5">
        {isDeprecated ? (
          <Badge tone="warn">deprecated</Badge>
        ) : item.lifecycle === 'active' ? (
          <Badge tone="neutral">active</Badge>
        ) : (
          <Muted>unset</Muted>
        )}
      </td>
      <td className="px-1.5 text-xs">
        {item.owner.service ? (
          <span className="font-mono">{item.owner.service}</span>
        ) : (
          <Muted>orphan</Muted>
        )}
      </td>
    </tr>
  );
}

function DependentsPanel({
  upstream,
  downstream,
}: {
  upstream: ServiceDependency[];
  downstream: ServiceDependency[];
}) {
  if (upstream.length === 0 && downstream.length === 0) {
    return <p className="py-4 text-sm italic text-ink-mute">No service dependencies detected.</p>;
  }
  return (
    <div className="flex flex-col" style={{ gap: 20 }}>
      <DependencySection
        title="Downstream — depends on me"
        subtitle="Services that consume schemas this service produces"
        items={downstream}
        empty="No services consume this service's schemas."
      />
      <DependencySection
        title="Upstream — I depend on"
        subtitle="Services that produce schemas this service consumes"
        items={upstream}
        empty="This service does not consume schemas from other services."
      />
    </div>
  );
}

function DependencySection({
  title,
  subtitle,
  items,
  empty,
}: {
  title: string;
  subtitle: string;
  items: ServiceDependency[];
  empty: string;
}) {
  return (
    <div>
      <div className="mb-2">
        <p className="text-sm font-medium text-ink">{title}</p>
        <p className="text-xs text-ink-mute">{subtitle}</p>
      </div>
      {items.length === 0 ? (
        <p className="text-xs italic text-ink-mute">{empty}</p>
      ) : (
        <table className="w-full table-fixed text-sm" style={{ minWidth: 420 }}>
          <colgroup>
            <col />
            <col style={{ width: 160 }} />
            <col style={{ width: 144 }} />
          </colgroup>
          <thead className="bg-paper-raised">
            <tr style={{ height: 36 }}>
              <th className="px-1.5 text-left text-[11px] font-medium text-accent">Service</th>
              <th className="px-1.5 text-left text-[11px] font-medium text-accent">Domain</th>
              <th className="px-1.5 text-left text-[11px] font-medium text-accent">
                Shared schemas
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((dep) => (
              <tr key={dep.ci_id} className="border-b border-paper-divider" style={{ height: 42 }}>
                <td className="px-1.5">
                  <Link
                    to={`/services/${encodeURIComponent(dep.urn)}`}
                    className="font-mono text-[12.5px] text-accent hover:underline"
                  >
                    {dep.name}
                  </Link>
                </td>
                <td className="px-1.5">
                  <Badge tone="accent">{dep.domain}</Badge>
                </td>
                <td className="px-1.5 font-mono text-[12.5px] text-accent">
                  {dep.shared_schema_count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/**
 * The rail's orientation for each direction: the design's heading and subtitle,
 * and how many services are in it.
 *
 * Deliberately NOT the names — the Dependents tab lists those, with their
 * domains and shared-schema counts, and repeating them here would have put the
 * same list on screen twice and left the tab adding only a badge. The rail
 * says which directions exist and how big they are; the tab says who.
 */
function RailSection({
  title,
  subtitle,
  items,
  empty,
}: {
  title: string;
  subtitle: string;
  items: ServiceDependency[];
  empty: string;
}) {
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <p className="text-[13px] font-medium text-ink">{title}</p>
        {items.length > 0 && (
          <span
            className="shrink-0 bg-paper-line font-mono text-xs text-ink-soft"
            style={{ borderRadius: 4, padding: '2px 6px' }}
          >
            {items.length}
          </span>
        )}
      </div>
      <p className="text-xs text-ink-mute">{subtitle}</p>
      {items.length === 0 && <p className="mt-1.5 text-xs italic text-ink-mute">{empty}</p>}
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-ink-mute italic text-xs">{children}</span>;
}
