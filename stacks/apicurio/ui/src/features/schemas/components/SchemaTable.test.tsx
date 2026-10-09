import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import { SchemaTable } from './SchemaTable';
import type { SchemaSummary } from '../types/schema.types';

function makeItem(overrides: Partial<SchemaSummary> = {}): SchemaSummary {
  return {
    artifact_id: 'commerce.product-value',
    group_id: 'default',
    name: null,
    type: 'AVRO',
    state: 'ENABLED',
    created_on: '2024-01-01T00:00:00Z',
    modified_on: '2024-06-01T00:00:00Z',
    latest_version: '3',
    owner: { service: 'CI0001', team: 'TM-COMMERCE', email: 'commerce@pearson.com' },
    domain: 'commerce',
    classification: 'internal',
    lifecycle: 'active',
    pii_fields: [],
    ...overrides,
  };
}

const DEFAULT_PROPS = {
  nonCompliantIds: new Set<string>(),
  complianceLoading: false,
  // Required, and missing until the tests were brought under the type gate.
  // It reached the component as `undefined` — falsy, so the rendered
  // behaviour matched `false` and nothing failed. This is the prop that
  // decides whether a row may claim `compliant`, so every case in this file
  // was passing it by accident.
  complianceError: false,
  sort: 'artifact_id' as const,
  dir: 'asc' as const,
  activeSubject: null,
  groupBy: null,
  selectedIds: new Set<string>(),
  showRawSubjects: false,
  density: 'compact' as const,
  onSort: vi.fn(),
  onRowClick: vi.fn(),
  onComplianceBadgeClick: vi.fn(),
  onSelectionChange: vi.fn(),
};

function renderTable(items: SchemaSummary[], props = {}) {
  return render(
    <MemoryRouter>
      <SchemaTable items={items} {...DEFAULT_PROPS} {...props} />
    </MemoryRouter>,
  );
}

describe('empty state', () => {
  it('renders no-match message when items is empty', () => {
    renderTable([]);
    expect(screen.getByText('No schemas match the current filters.')).toBeInTheDocument();
  });
});

describe('deprecated treatment', () => {
  const deprecated = makeItem({
    artifact_id: 'old.schema-value',
    lifecycle: 'deprecated',
    successor_artifact_id: 'new.schema-value',
    retirement_date: '2026-12-31T00:00:00Z',
  });

  it('renders deprecated subject with line-through class', () => {
    renderTable([deprecated]);
    const inner = screen.getByText('old.schema');
    expect(inner.parentElement?.className).toContain('line-through');
  });

  it('renders successor link when successor_artifact_id is set', () => {
    renderTable([deprecated]);
    expect(screen.getByText('successor →')).toBeInTheDocument();
  });

  it('successor link href points to the successor artifact', () => {
    renderTable([deprecated]);
    const link = screen.getByText('successor →').closest('a');
    expect(link?.getAttribute('href')).toContain('new.schema-value');
  });

  it('does not render successor link when successor_artifact_id is absent', () => {
    const noSuccessor = makeItem({ lifecycle: 'deprecated', successor_artifact_id: undefined });
    renderTable([noSuccessor]);
    expect(screen.queryByText('successor →')).not.toBeInTheDocument();
  });
});

describe('bulk selection', () => {
  const items = [
    makeItem({ artifact_id: 'a' }),
    makeItem({ artifact_id: 'b' }),
  ];

  it('renders a checkbox per row', () => {
    renderTable(items);
    const checkboxes = screen.getAllByRole('checkbox');
    // header checkbox + 2 row checkboxes
    expect(checkboxes).toHaveLength(3);
  });

  it('calls onSelectionChange when row checkbox is clicked', () => {
    const onSelectionChange = vi.fn();
    renderTable(items, { onSelectionChange });
    const rowCheckboxes = screen.getAllByRole('checkbox').slice(1);
    fireEvent.click(rowCheckboxes[0]);
    expect(onSelectionChange).toHaveBeenCalledOnce();
    const [updatedSet] = onSelectionChange.mock.calls[0] as [Set<string>];
    expect(updatedSet.has('default/a')).toBe(true);
  });

  it('header checkbox selects all when none are selected', () => {
    const onSelectionChange = vi.fn();
    renderTable(items, { onSelectionChange });
    const headerCheckbox = screen.getAllByRole('checkbox')[0];
    fireEvent.click(headerCheckbox);
    expect(onSelectionChange).toHaveBeenCalledOnce();
    const [updatedSet] = onSelectionChange.mock.calls[0] as [Set<string>];
    expect(updatedSet.has('default/a')).toBe(true);
    expect(updatedSet.has('default/b')).toBe(true);
  });

  it('header checkbox deselects all when all are selected', () => {
    const allSelected = new Set(['default/a', 'default/b']);
    const onSelectionChange = vi.fn();
    renderTable(items, { selectedIds: allSelected, onSelectionChange });
    const headerCheckbox = screen.getAllByRole('checkbox')[0];
    fireEvent.click(headerCheckbox);
    expect(onSelectionChange).toHaveBeenCalledOnce();
    const [updatedSet] = onSelectionChange.mock.calls[0] as [Set<string>];
    expect(updatedSet.size).toBe(0);
  });

  it('clicking row checkbox does not open drawer', () => {
    const onRowClick = vi.fn();
    renderTable(items, { onRowClick });
    const rowCheckboxes = screen.getAllByRole('checkbox').slice(1);
    fireEvent.click(rowCheckboxes[0]);
    expect(onRowClick).not.toHaveBeenCalled();
  });
});

describe('owner hover card', () => {
  it('renders owner CI id in the cell', () => {
    renderTable([makeItem()]);
    // CI0001 appears in both the visible cell and the hidden hover card
    const matches = screen.getAllByText('CI0001');
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it('renders "orphan" when owner.service is null', () => {
    const orphan = makeItem({ owner: { service: null, team: null, email: null } });
    renderTable([orphan]);
    expect(screen.getByText('orphan')).toBeInTheDocument();
  });
});

describe('subject formatting', () => {
  const item = makeItem({ artifact_id: 'commerce.product.created-value' });

  it('shows base name and value badge when showRawSubjects is false', () => {
    renderTable([item], { showRawSubjects: false });
    expect(screen.getByText('commerce.product.created')).toBeInTheDocument();
    expect(screen.getByText('value')).toBeInTheDocument();
    expect(screen.queryByText('commerce.product.created-value')).not.toBeInTheDocument();
  });

  it('shows full subject without badge when showRawSubjects is true', () => {
    renderTable([item], { showRawSubjects: true });
    expect(screen.getByText('commerce.product.created-value')).toBeInTheDocument();
    expect(screen.queryByText('commerce.product.created')).not.toBeInTheDocument();
  });

  it('shows key badge for -key artifact', () => {
    const keyItem = makeItem({ artifact_id: 'commerce.order-key' });
    renderTable([keyItem], { showRawSubjects: false });
    expect(screen.getByText('commerce.order')).toBeInTheDocument();
    expect(screen.getByText('key')).toBeInTheDocument();
    expect(screen.queryByText('commerce.order-key')).not.toBeInTheDocument();
  });

  it('shows artifact_id unchanged when no suffix present', () => {
    const noSuffix = makeItem({ artifact_id: 'product.api' });
    renderTable([noSuffix], { showRawSubjects: false });
    expect(screen.getByText('product.api')).toBeInTheDocument();
  });

  it('row click passes item with full artifact_id including suffix', () => {
    const onRowClick = vi.fn();
    const valueItem = makeItem({ artifact_id: 'product.product-value' });
    renderTable([valueItem], { onRowClick });
    fireEvent.click(screen.getByText('product.product'));
    expect(onRowClick).toHaveBeenCalledWith(expect.objectContaining({ artifact_id: 'product.product-value' }));
  });
});

describe('group-by rendering', () => {
  const items = [
    makeItem({ artifact_id: 'a', domain: 'commerce' }),
    makeItem({ artifact_id: 'b', domain: 'learning' }),
    makeItem({ artifact_id: 'c', domain: 'commerce' }),
  ];

  it('renders group header buttons when groupBy=domain', () => {
    renderTable(items, { groupBy: 'domain' });
    expect(screen.getByRole('button', { name: /commerce/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /learning/ })).toBeInTheDocument();
  });

  it('shows item count per group', () => {
    renderTable(items, { groupBy: 'domain' });
    expect(screen.getByText('(2)')).toBeInTheDocument();
    expect(screen.getByText('(1)')).toBeInTheDocument();
  });

  it('collapses group on header click and sets aria-expanded=false', () => {
    renderTable(items, { groupBy: 'domain' });
    const commerceBtn = screen.getByRole('button', { name: /commerce/ });
    expect(commerceBtn).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(commerceBtn);
    expect(commerceBtn).toHaveAttribute('aria-expanded', 'false');
  });
});
