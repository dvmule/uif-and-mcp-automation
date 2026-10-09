import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { SchemaMappingView } from './SchemaMappingView';
import { toCsv, SHEET_HEADERS } from '../mapping-csv';
import { parseMappingBody } from '../mapping-body';

const BODY = JSON.stringify({
  $schema: '../../gold-attribute-mapping.schema.json',
  domain: 'product',
  table: 'gold_product.product',
  registry: { group: 'product', artifact: 'product-gold-attribute-mapping', compatibility: 'BACKWARD' },
  drivingTable: 'MTL_SYSTEM_ITEMS_B',
  entityKey: [
    { goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' },
    { goldColumn: 'organizationId', drivingColumn: 'ORGANIZATION_ID' },
  ],
  batch: { requestedBy: 'Reyas Ahamed H', targetDate: '2026-07-22', backfillMode: 'silent', newChannel: null },
  transforms: { roundTo2dp: { steps: [{ op: 'round', precision: 2 }] } },
  columns: [
    {
      goldColumnName: 'itemStatus', businessDescription: 'Lifecycle status.', kind: 'scalar',
      dataType: 'string',
      source: { table: 'MTL_SYSTEM_ITEMS_B', column: 'INVENTORY_ITEM_STATUS_CODE', filter: null },
      transform: null, channels: { event: true, message: true, api: true },
      nullable: true, notes: '',
    },
    {
      goldColumnName: 'listPrice', businessDescription: 'List price.', kind: 'scalar',
      dataType: 'number',
      source: { table: 'QP_LIST_LINES', column: 'OPERAND', filter: null },
      transform: { rule: 'roundTo2dp' }, channels: { event: false, message: true, api: true },
      nullable: true, notes: '',
    },
    {
      goldColumnName: 'isbn13', businessDescription: 'ISBN-13.', kind: 'conditional',
      dataType: 'string',
      discriminator: { table: 'EGO_MTL_SY_ITEMS_EXT_B', column: 'ITEM_CATALOG_GROUP_ID' },
      channels: { event: true, message: true, api: true },
      branches: [
        { condition: 'ELSE (<> 10002)',
          source: { table: 'EGO_MTL_SY_ITEMS_EXT_B', column: 'C_EXT_ATTR8', filter: 'ATTR_GROUP_ID = 741' },
          fallback: 'returnNull', notes: null },
        { condition: '10002 (Digital Catalog)',
          source: { table: 'EGO_MTL_SY_ITEMS_EXT_B', column: 'C_EXT_ATTR4', filter: 'ATTR_GROUP_ID = 1401' },
          fallback: 'fallbackToElse', notes: null },
      ],
    },
    {
      goldColumnName: 'bomComponents', businessDescription: 'BOM lines.', kind: 'nested',
      joinKey: 'BILL_SEQUENCE_ID -> item', channels: { event: false, message: false, api: true },
      fields: [
        { name: 'componentItemId', type: 'integer', sortKey: true,
          source: { table: 'BOM_COMPONENTS_B', column: 'COMPONENT_ITEM_ID' }, transform: null },
      ],
    },
  ],
});

const MAPPING = parseMappingBody(BODY);

function renderView(kind: 'scalar' | 'conditional' | 'nested' = 'scalar', onKindChange = vi.fn()) {
  render(
    <SchemaMappingView
      mapping={MAPPING}
      kind={kind}
      onKindChange={onKindChange}
      subject="product.product.mapping"
      version="1"
    />,
  );
  return { onKindChange };
}

describe('facts band', () => {
  it('names the gold table, domain and driving table', () => {
    renderView();
    // Scoped to the band: `MTL_SYSTEM_ITEMS_B` is the driving table AND the
    // source table of a scalar row, so an unscoped `getByText` matches twice.
    const band = within(screen.getByTestId('mapping-facts'));
    expect(band.getByText('gold_product.product')).toBeInTheDocument();
    expect(band.getByText('product')).toBeInTheDocument();
    expect(band.getByText('MTL_SYSTEM_ITEMS_B')).toBeInTheDocument();
  });

  it('shows the entity key as ordered goldColumn <- drivingColumn pairs', () => {
    renderView();
    // It is the gold table's ORDERED primary key, and the order is the grain.
    const key = screen.getByTestId('mapping-entity-key');
    expect(key).toHaveTextContent('inventoryItemId');
    expect(key).toHaveTextContent('INVENTORY_ITEM_ID');
    expect(key.textContent?.indexOf('inventoryItemId'))
      .toBeLessThan(key.textContent!.indexOf('organizationId'));
  });

  it('shows the registry coordinate and the batch', () => {
    renderView();
    expect(screen.getByText(/product-gold-attribute-mapping/)).toBeInTheDocument();
    expect(screen.getByText('BACKWARD')).toBeInTheDocument();
    expect(screen.getByText(/Reyas Ahamed H/)).toBeInTheDocument();
  });
});

describe('sheet toggle', () => {
  it('labels each sheet with its row count', () => {
    // The count is on the label so the size of a sheet is known before
    // opening it.
    renderView();
    expect(screen.getByRole('tab', { name: /Scalar/ })).toHaveTextContent('2');
    expect(screen.getByRole('tab', { name: /Conditional/ })).toHaveTextContent('2');
    expect(screen.getByRole('tab', { name: /Nested/ })).toHaveTextContent('1');
  });

  it('marks the active sheet and reports a change', () => {
    const { onKindChange } = renderView('scalar');
    expect(screen.getByRole('tab', { name: /Scalar/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: /Nested/ }));
    expect(onKindChange).toHaveBeenCalledWith('nested');
  });
});

describe('the tables', () => {
  it('renders the scalar sheet with the workbook headers in the workbook order', () => {
    renderView('scalar');
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent);
    expect(headers).toEqual(SHEET_HEADERS.scalar);
  });

  it('renders one row per scalar column', () => {
    renderView('scalar');
    expect(screen.getByText('itemStatus')).toBeInTheDocument();
    expect(screen.getByText('listPrice')).toBeInTheDocument();
    expect(screen.getByText('INVENTORY_ITEM_STATUS_CODE')).toBeInTheDocument();
  });

  it('renders channels as Y and a dash, not true and false', () => {
    renderView('scalar');
    const row = screen.getByTestId('mapping-row-itemStatus#0');
    expect(row).toHaveTextContent('Y');
    expect(row).not.toHaveTextContent('true');
  });

  it('shows a transform rule name as a chip', () => {
    renderView('scalar');
    expect(screen.getByText('roundTo2dp')).toBeInTheDocument();
  });

  it('renders one row per branch, repeating the gold column', () => {
    renderView('conditional');
    expect(screen.getAllByText('isbn13')).toHaveLength(2);
    expect(screen.getByText('ELSE (<> 10002)')).toBeInTheDocument();
    expect(screen.getByText('10002 (Digital Catalog)')).toBeInTheDocument();
  });

  it('shows the description on the first branch row only', () => {
    renderView('conditional');
    expect(screen.getAllByText('ISBN-13.')).toHaveLength(1);
  });

  it('renders one row per nested field with its sort key', () => {
    renderView('nested');
    expect(screen.getByText('componentItemId')).toBeInTheDocument();
    expect(screen.getByText('BILL_SEQUENCE_ID -> item')).toBeInTheDocument();
  });

  it('marks Join Grain as superseded rather than showing it as current', () => {
    // DEPRECATED per UIFPE-104. Rendering it as a live column would teach the
    // deprecated thing to everyone who reads the page.
    renderView('scalar');
    const header = screen.getByRole('columnheader', { name: /Join Grain/ });
    expect(header).toHaveAttribute('title', expect.stringContaining('superseded'));
  });
});

describe('search', () => {
  it('filters rows and reports a count', () => {
    renderView('scalar');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'listPrice' } });
    expect(screen.getByText('listPrice')).toBeInTheDocument();
    expect(screen.queryByText('itemStatus')).not.toBeInTheDocument();
    expect(screen.getByTestId('mapping-match-count')).toHaveTextContent('1');
  });

  it('is case-insensitive', () => {
    renderView('scalar');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'LISTPRICE' } });
    expect(screen.getByText('listPrice')).toBeInTheDocument();
  });

  it('shows one line when nothing matches, not an empty pane', () => {
    // Review Focus 5.
    renderView('scalar');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzzz' } });
    expect(screen.getByText(/Nothing in this mapping matches/)).toBeInTheDocument();
    expect(screen.queryByRole('columnheader')).not.toBeInTheDocument();
  });

  it('highlights the matched run', () => {
    renderView('scalar');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'list' } });
    expect(screen.getByText('list').tagName).toBe('MARK');
  });
});

describe('empty sheets', () => {
  it('names the sheet in its empty state', () => {
    const empty = parseMappingBody(JSON.stringify({
      domain: 'p', table: 't', drivingTable: 'D', columns: [],
    }));
    render(
      <SchemaMappingView
        mapping={empty} kind="conditional" onKindChange={vi.fn()}
        subject="p.p.mapping" version="1"
      />,
    );
    expect(screen.getByText(/No conditional columns/)).toBeInTheDocument();
  });
});

describe('row expansion', () => {
  it('opens and closes a detail region for one row', () => {
    renderView('scalar');
    expect(screen.queryByRole('region', { name: /itemStatus/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Show detail for itemStatus/ }));
    expect(screen.getByRole('region', { name: /itemStatus/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Hide detail for itemStatus/ }));
    expect(screen.queryByRole('region', { name: /itemStatus/ })).not.toBeInTheDocument();
  });

  it('opens at most one row at a time', () => {
    // Two 14-column rows expanded at once pushes the second off-screen; the
    // panel is a reading aid, not a comparison view.
    renderView('scalar');
    fireEvent.click(screen.getByRole('button', { name: /Show detail for itemStatus/ }));
    fireEvent.click(screen.getByRole('button', { name: /Show detail for listPrice/ }));
    expect(screen.queryByRole('region', { name: /itemStatus/ })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: /listPrice/ })).toBeInTheDocument();
  });
});

describe('toCsv', () => {
  it('emits the workbook headers then one line per row', () => {
    const csv = toCsv(SHEET_HEADERS.scalar, MAPPING.scalar);
    const lines = csv.split('\n');
    expect(lines[0]).toBe(SHEET_HEADERS.scalar.map((h) => `"${h}"`).join(','));
    expect(lines).toHaveLength(3);
    expect(lines[1]).toContain('itemStatus');
  });

  it('quotes a cell containing a comma or a quote', () => {
    const rows = parseMappingBody(JSON.stringify({
      domain: 'p', table: 't', drivingTable: 'D',
      columns: [{
        goldColumnName: 'x', businessDescription: 'a, b and "c"', kind: 'scalar',
        dataType: 'string', source: { table: 'T', column: 'C', filter: null },
        channels: { event: false, message: false, api: false }, nullable: false,
      }],
    })).scalar;
    const csv = toCsv(SHEET_HEADERS.scalar, rows);
    expect(csv).toContain('"a, b and ""c"""');
  });

  it('renders channels as Y and N, matching the workbook', () => {
    const csv = toCsv(SHEET_HEADERS.scalar, MAPPING.scalar);
    expect(csv.split('\n')[1]).toContain('"Y"');
  });
});
