import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MappingRowDetail } from './MappingRowDetail';
import { MappingReferenceSections } from './MappingReferenceSections';
import { parseMappingBody } from '../mapping-body';

const BODY_OBJ = {
  domain: 'product',
  table: 'gold_product.product',
  drivingTable: 'MTL_SYSTEM_ITEMS_B',
  drivingSilverTable: 'uif_echo_{env}_silver_tep.mtl_system_items_b',
  silverTables: { QP_LIST_HEADERS_B: 'uif_echo_{env}_silver_tep.qp_list_headers_b' },
  entityKey: [{ goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' }],
  rowGroups: {
    ukOrgs: {
      table: 'MTL_PARAMETERS', column: 'ORGANIZATION_CODE',
      op: 'startsWith', values: ['01'], description: 'UK orgs (GBPE).',
    },
    nonUkOrgs: { complementOf: 'ukOrgs', description: 'Every org NOT in ukOrgs.' },
  },
  transforms: {
    convertYN: { steps: [{ op: 'upper' }] },
    custom: { sqlExpr: "regexp_replace({x}, 'a', 'b')" },
  },
  columns: [
    {
      goldColumnName: 'onDriving', businessDescription: 'd', kind: 'scalar', dataType: 'string',
      source: { table: 'MTL_SYSTEM_ITEMS_B', column: 'SEGMENT1', filter: null },
      channels: { event: false, message: false, api: true }, nullable: true,
    },
    {
      goldColumnName: 'joined', businessDescription: 'd', kind: 'scalar', dataType: 'string',
      source: {
        table: 'QP_LIST_HEADERS_B', column: 'NAME', filter: 'ATTR_GROUP_ID = 741',
        join: { type: 'inner', on: [{
          leftTable: 'MTL_SYSTEM_ITEMS_B', leftColumn: 'INVENTORY_ITEM_ID',
          rightColumn: 'INVENTORY_ITEM_ID',
        }] },
      },
      transform: { rule: 'convertYN' },
      channels: { event: false, message: false, api: true }, nullable: true,
    },
    {
      goldColumnName: 'reviewed', businessDescription: 'd', kind: 'scalar', dataType: 'string',
      source: { table: 'T', column: 'C', filter: null },
      transform: { rule: 'custom' },
      channels: { event: false, message: false, api: true }, nullable: true,
    },
    {
      goldColumnName: 'derived', businessDescription: 'd', kind: 'scalar', dataType: 'string',
      source: { table: 'T', column: 'C', filter: null },
      derivation: {
        sql: 'SELECT INVENTORY_ITEM_ID, max(X) AS derived FROM items_ext GROUP BY 1',
        keyedBy: [{ queryColumn: 'INVENTORY_ITEM_ID', entityColumn: 'INVENTORY_ITEM_ID' }],
        reads: { items_ext: ['INVENTORY_ITEM_ID', 'X'] },
      },
      channels: { event: false, message: false, api: true }, nullable: true,
    },
    {
      goldColumnName: 'scoped', businessDescription: 'd', kind: 'conditional', dataType: 'string',
      discriminator: { table: 'D', column: 'K' },
      channels: { event: false, message: false, api: true },
      branches: [
        { condition: '1', scope: { group: 'ukOrgs' },
          source: { table: 'T', column: 'C' }, fallback: 'returnNull' },
        { condition: 'ELSE', scope: { op: 'else' },
          source: { table: 'T', column: 'C2' }, fallback: 'returnNull' },
      ],
    },
    {
      goldColumnName: 'picked', businessDescription: 'd', kind: 'nested',
      joinKey: 'K -> item', channels: { event: false, message: false, api: true },
      fields: [{ name: 'f', type: 'string', sortKey: true, source: { table: 'T', column: 'C' } }],
      pick: {
        partitionBy: ['f'],
        orderBy: [{ expr: 'CASE WHEN END_DATE IS NULL THEN 0 ELSE 1 END' }, { expr: 'START_DATE', direction: 'desc' }],
      },
    },
  ],
};

const MAPPING = parseMappingBody(JSON.stringify(BODY_OBJ));
const BODY = MAPPING.value as Record<string, unknown>;
const scalar = (name: string) => MAPPING.scalar.find((r) => r.goldColumnName === name)!;

function renderDetail(row = scalar('joined')) {
  render(<MappingRowDetail row={row} body={BODY} />);
}

describe('the JOIN section', () => {
  it('renders the ON clause with the join type and the filter', () => {
    renderDetail();
    const join = screen.getByTestId('detail-join');
    expect(join).toHaveTextContent('INNER JOIN');
    expect(join).toHaveTextContent('QP_LIST_HEADERS_B');
    expect(join).toHaveTextContent('MTL_SYSTEM_ITEMS_B.INVENTORY_ITEM_ID');
    expect(join).toHaveTextContent('QP_LIST_HEADERS_B.INVENTORY_ITEM_ID');
    expect(join).toHaveTextContent('ATTR_GROUP_ID = 741');
  });

  it('says there is no join when the source IS the driving table', () => {
    renderDetail(scalar('onDriving'));
    expect(screen.getByTestId('detail-join')).toHaveTextContent(/driving table/i);
  });
});

describe('the SILVER section', () => {
  it('shows the env-templated Silver name for the row source', () => {
    renderDetail();
    expect(screen.getByTestId('detail-silver'))
      .toHaveTextContent('uif_echo_{env}_silver_tep.qp_list_headers_b');
  });

  it('is absent when no Silver name is declared for that table', () => {
    // Product's real mapping declares none at all, so this is the common case.
    renderDetail(scalar('reviewed'));
    expect(screen.queryByTestId('detail-silver')).not.toBeInTheDocument();
  });
});

describe('the SCOPE section', () => {
  it('resolves a named row group to its predicate', () => {
    const row = MAPPING.conditional.find((r) => r.condition === '1')!;
    render(<MappingRowDetail row={row} body={BODY} />);
    const scope = screen.getByTestId('detail-scope');
    expect(scope).toHaveTextContent('ukOrgs');
    // As SQL, not as the op name. gold-attribute-mapping.schema.json,
    // $defs.branchScope.properties.op: "startsWith renders LIKE 'value%'".
    // Printing `startsWith '01'` would show the reader something other than
    // the predicate that runs, which is the one thing this panel prevents.
    expect(scope).toHaveTextContent("MTL_PARAMETERS.ORGANIZATION_CODE LIKE '01%'");
  });

  it('explains op else rather than printing the word', () => {
    const row = MAPPING.conditional.find((r) => r.condition === 'ELSE')!;
    render(<MappingRowDetail row={row} body={BODY} />);
    expect(screen.getByTestId('detail-scope')).toHaveTextContent(/no sibling branch/);
  });

  it('is absent for a scalar row, which has no branches to scope', () => {
    renderDetail();
    expect(screen.queryByTestId('detail-scope')).not.toBeInTheDocument();
  });
});

describe('the XFORM section', () => {
  it('names the rule and renders its steps', () => {
    renderDetail();
    const x = screen.getByTestId('detail-xform');
    expect(x).toHaveTextContent('convertYN');
    expect(x).toHaveTextContent('upper');
  });

  it('shows a sqlExpr verbatim and flags it for review', () => {
    // CI flags every use for engineering review; the panel must say so.
    renderDetail(scalar('reviewed'));
    const x = screen.getByTestId('detail-xform');
    expect(x).toHaveTextContent("regexp_replace({x}, 'a', 'b')");
    expect(x).toHaveTextContent(/review/i);
  });
});

describe('the DERIVATION section', () => {
  it('shows the SQL, the keyedBy contract and the computed reads', () => {
    renderDetail(scalar('derived'));
    const d = screen.getByTestId('detail-derivation');
    expect(d).toHaveTextContent('SELECT INVENTORY_ITEM_ID');
    expect(d).toHaveTextContent('INVENTORY_ITEM_ID → INVENTORY_ITEM_ID');
    expect(d).toHaveTextContent('items_ext');
  });

  it('is absent when the column declares no derivation', () => {
    renderDetail();
    expect(screen.queryByTestId('detail-derivation')).not.toBeInTheDocument();
  });
});

describe('the PICK section', () => {
  it('shows partitionBy and the ordered orderBy expressions with directions', () => {
    const row = MAPPING.nested.find((r) => r.goldColumnName === 'picked')!;
    render(<MappingRowDetail row={row} body={BODY} />);
    const p = screen.getByTestId('detail-pick');
    expect(p).toHaveTextContent('f');
    expect(p).toHaveTextContent('CASE WHEN END_DATE IS NULL');
    expect(p).toHaveTextContent('desc');
  });
});

describe('MappingReferenceSections', () => {
  function renderRef() {
    render(<MappingReferenceSections reference={MAPPING.reference} />);
  }

  it('renders a collapsed section per non-empty collection, with counts', () => {
    renderRef();
    expect(screen.getByRole('button', { name: /Row groups/ })).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: /Transforms/ })).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: /Silver tables/ })).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: /Entity key/ })).toHaveTextContent('1');
  });

  it('starts collapsed, and expands on click', () => {
    renderRef();
    expect(screen.queryByText('UK orgs (GBPE).')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Row groups/ }));
    expect(screen.getByText('UK orgs (GBPE).')).toBeInTheDocument();
  });

  it('shows a complement group beneath the base it follows', () => {
    renderRef();
    fireEvent.click(screen.getByRole('button', { name: /Row groups/ }));
    const list = screen.getByTestId('reference-row-groups');
    expect(list.textContent!.indexOf('ukOrgs'))
      .toBeLessThan(list.textContent!.indexOf('nonUkOrgs'));
    expect(list).toHaveTextContent(/complement of ukOrgs/);
  });

  it('reports how many columns reference each transform', () => {
    renderRef();
    fireEvent.click(screen.getByRole('button', { name: /Transforms/ }));
    const list = screen.getByTestId('reference-transforms');
    expect(list).toHaveTextContent('convertYN');
    expect(list).toHaveTextContent(/1 column/);
  });

  it('marks which Silver table is the driving one', () => {
    renderRef();
    fireEvent.click(screen.getByRole('button', { name: /Silver tables/ }));
    expect(screen.getByTestId('reference-silver-tables')).toHaveTextContent(/driving/i);
  });

  it('renders nothing at all when every collection is empty', () => {
    // Product's real mapping: no silverTables, no rowGroups... an empty
    // accordion is worse than no accordion.
    const bare = parseMappingBody(JSON.stringify({
      domain: 'p', table: 't', columns: [],
    }));
    const { container } = render(<MappingReferenceSections reference={bare.reference} />);
    expect(container).toBeEmptyDOMElement();
  });
});
