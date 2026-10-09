import { test, expect, type Locator, type Page } from '@playwright/test';

/**
 * A deep link is only a deep link if a COLD page load reproduces the view.
 * The unit tests drive the URL through the router inside one mounted tree;
 * these load the URL from scratch, which is what someone you send a link to
 * actually does.
 */

const BODY = JSON.stringify(
  {
    type: 'record',
    name: 'PreferencesChanged',
    namespace: 'com.pearson.customer',
    fields: [
      { name: 'customer_id', type: 'string' },
      { name: 'email', type: 'string' },
    ],
    metadata: {
      owner: { service: 'CI0093432', team: 'TM-CUSTOMER-PLATFORM' },
      domain: 'customer',
      dataClassification: 'confidential',
      piiFields: ['customer_id', 'email'],
      lifecycle: 'active',
    },
  },
  null,
  2,
);

// A field of its own, so Fields view has a record to show. With `fields: []`
// the view correctly renders its "parses but declares no fields" state, which
// is right for the app and useless as a fixture for "did v1 load".
const V1_BODY = JSON.stringify(
  {
    type: 'record',
    name: 'PreferencesChangedV1',
    fields: [{ name: 'legacy_id', type: 'string' }],
  },
  null,
  2,
);

const SUBJECT = 'customer.preferences.changed-value';

const DETAIL = {
  artifact_id: SUBJECT,
  group_id: 'default',
  name: null,
  type: 'AVRO',
  state: 'ENABLED',
  created_on: '2026-01-01T00:00:00Z',
  modified_on: '2026-01-02T00:00:00Z',
  content: BODY,
  content_version: 'latest',
  versions: [
    { version: '1', state: 'ENABLED', created_on: '2026-01-01T00:00:00Z', created_by: null },
    { version: '2', state: 'ENABLED', created_on: '2026-01-02T00:00:00Z', created_by: null },
  ],
  owner: { service: 'CI0093432', team: null, email: null },
  domain: 'customer',
  classification: 'confidential',
  lifecycle: 'active',
  pii_fields: [],
  latest_version: '2',
  compatibility: null,
};

async function mockApi(page: Page) {
  await page.route('**/api/v1/**', (route) => {
    const url = route.request().url();
    if (url.includes('/schemas/overview')) {
      return route.fulfill({
        json: { total_schemas: 12, schemas_with_compatibility_rules: 0, non_compliant_schemas: 0 },
      });
    }
    if (url.includes('/schemas/non-compliant')) {
      return route.fulfill({
        json: { items: [], last_validated: new Date().toISOString(), total_checked: 12 },
      });
    }
    // The nav's service count calls the LIST endpoint, which returns a bare
    // array; the subject page calls `/schemas/{g}/{a}/services`, which returns
    // an object. Both contain '/services', so the list has to be matched first
    // and by its full path.
    if (url.includes('/api/v1/services')) {
      return route.fulfill({ json: [] });
    }
    if (url.includes('/services')) {
      return route.fulfill({
        json: { artifact_id: SUBJECT, group_id: 'default', producers: [], consumers: [] },
      });
    }
    if (url.includes('/schemas/default/')) {
      const version = new URL(url).searchParams.get('version');
      if (version === '1') {
        return route.fulfill({ json: { ...DETAIL, content: V1_BODY, content_version: '1' } });
      }
      return route.fulfill({ json: DETAIL });
    }
    if (url.includes('/schemas')) {
      return route.fulfill({ json: { items: [], total: 0, limit: 500, offset: 0 } });
    }
    return route.continue();
  });
}

const PAGE = `/schemas/default/${SUBJECT}`;

test.describe('schema detail deep links', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test('a cold load reproduces the version, the tab and the view', async ({ page }) => {
    await page.goto(`${PAGE}?version=1&schemaView=fields`);
    await expect(page.getByLabel('Version', { exact: true })).toHaveValue('1');
    // Fields view, not the default Code view.
    await expect(page.getByRole('tab', { name: 'Fields' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // v1's content, not the latest — the whole point of the pin.
    await expect(page.getByText('PreferencesChangedV1')).toBeVisible();
    await expect(page.getByText('legacy_id')).toBeVisible();
    await expect(page.getByText('customer_id')).toHaveCount(0);
  });

  test('choosing a version puts it in the address bar', async ({ page }) => {
    await page.goto(PAGE);
    await expect(page.getByLabel('Version', { exact: true })).toBeVisible();
    await page.getByLabel('Version', { exact: true }).selectOption('1');
    await expect(page).toHaveURL(/version=1/);
  });

  test('switching view puts it in the address bar, and back again', async ({ page }) => {
    await page.goto(PAGE);
    await page.getByRole('tab', { name: 'Graph' }).click();
    await expect(page).toHaveURL(/schemaView=graph/);
    await page.getByRole('tab', { name: 'Fields' }).click();
    await expect(page).toHaveURL(/schemaView=fields/);
  });

  test('a fresh visit pins no version, so links do not go stale', async ({ page }) => {
    // The newest version is a moving target. Writing it into the URL on arrival
    // would mean every link shared from this page froze at whatever was current
    // when it was copied.
    await page.goto(PAGE);
    await expect(page.getByLabel('Version', { exact: true })).toBeVisible();
    await expect(page).not.toHaveURL(/version=/);
  });

  test('the copy-link button hands over an absolute link to this exact view', async ({
    page,
    context,
  }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto(`${PAGE}?version=1&schemaView=fields`);
    await page.getByRole('button', { name: /copy a link to this version/i }).click();

    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain(`/schemas/default/${SUBJECT}`);
    expect(copied).toContain('version=1');
    expect(copied).toContain('schemaView=fields');
    expect(copied.startsWith('http')).toBe(true);
  });

  test('the download saves the body under a type-and-version name', async ({ page }) => {
    await page.goto(`${PAGE}?version=1`);
    const button = page.getByRole('button', { name: /download this version/i });
    await expect(button).toBeVisible();

    const download = await Promise.all([
      page.waitForEvent('download'),
      button.click(),
    ]).then(([d]) => d);

    // AVRO, version 1 — not `.json`, and not the subject on its own.
    expect(download.suggestedFilename()).toBe(`${SUBJECT}.v1.avsc`);
  });

  test('the download follows the version on screen', async ({ page }) => {
    await page.goto(PAGE);
    const button = page.getByRole('button', { name: /download this version/i });
    await expect(button).toHaveAttribute('title', `Download ${SUBJECT}.v2.avsc`);
    await page.getByLabel('Version', { exact: true }).selectOption('1');
    await expect(button).toHaveAttribute('title', `Download ${SUBJECT}.v1.avsc`);
  });
  test('the graph toolbar saves a picture of the graph, not the body', async ({ page }) => {
    await page.goto(`${PAGE}?version=1&schemaView=graph`);
    // The pane's own download, not the page toolbar's above it.
    const button = page.getByRole('button', { name: 'Download the graph as a PNG' });
    await expect(button).toBeVisible();
    await expect(button).toHaveAttribute('title', `Download ${SUBJECT}.v1.graph.png`);

    const download = await Promise.all([page.waitForEvent('download'), button.click()]).then(
      ([d]) => d,
    );
    expect(download.suggestedFilename()).toBe(`${SUBJECT}.v1.graph.png`);

    // A real raster, not an empty or truncated file. PNG magic number first.
    const path = await download.path();
    const { readFileSync } = await import('fs');
    const bytes = readFileSync(path!);
    expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');

    // And it must contain the WHOLE graph. The first attempt at this exported
    // 640px of a graph that reaches 850, silently cropping the right-hand
    // column — a valid PNG that was missing half the diagram. So the size is
    // checked against the rendered nodes rather than against a constant.
    const expected = await page.evaluate(() => {
      const els = [...document.querySelectorAll<HTMLElement>('.react-flow__node')];
      const at = (e: HTMLElement) => {
        const m = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(e.style.transform);
        return { x: Number(m?.[1] ?? 0), y: Number(m?.[2] ?? 0) };
      };
      const xs = els.map((e) => at(e).x);
      const ys = els.map((e) => at(e).y);
      const rights = els.map((e) => at(e).x + e.offsetWidth);
      const bottoms = els.map((e) => at(e).y + e.offsetHeight);
      const PAD = 32;
      const RATIO = 2;
      return {
        nodes: els.length,
        width: (Math.ceil(Math.max(...rights) - Math.min(...xs)) + PAD * 2) * RATIO,
        height: (Math.ceil(Math.max(...bottoms) - Math.min(...ys)) + PAD * 2) * RATIO,
      };
    });
    expect(expected.nodes).toBeGreaterThan(1);
    // IHDR carries the dimensions: width at byte 16, height at byte 20.
    expect(bytes.readUInt32BE(16)).toBe(expected.width);
    expect(bytes.readUInt32BE(20)).toBe(expected.height);
  });

  test('the page toolbar still saves the schema body alongside it', async ({ page }) => {
    // Two download buttons on one screen; they must not have converged.
    await page.goto(`${PAGE}?version=1&schemaView=graph`);
    await expect(page.getByRole('button', { name: /download this version/i })).toHaveAttribute(
      'title',
      `Download ${SUBJECT}.v1.avsc`,
    );
    await expect(page.getByRole('button', { name: 'Download the graph as a PNG' })).toBeVisible();
  });
});

/**
 * `product.gold-schema` is the registry's one JSON Schema artifact, and the
 * only one that is not in the `default` group. It spells its 63 fields as a
 * `properties` map, which the Fields view read as no fields at all — the page
 * rendered its metadata block alone and nothing else, so a 63-attribute Gold
 * contract looked like a schema that declares nothing.
 *
 * Trimmed to the shapes the real body uses, and kept in its real group so the
 * route is exercised the way it is actually reached.
 */
const GOLD_SUBJECT = 'product.gold-schema';

const GOLD_BODY = JSON.stringify(
  {
    $schema: 'http://json-schema.org/draft-07/schema#',
    $id: 'https://schemas.pearson.com/gold/product/v1/master.json',
    title: 'Product',
    type: 'object',
    additionalProperties: false,
    required: ['inventoryItemId', 'productId', 'organizationId'],
    properties: {
      inventoryItemId: { type: 'integer', description: 'Oracle internal surrogate.' },
      productId: { type: 'string', description: 'PPID.' },
      isbn13: { type: ['string', 'null'], description: 'ISBN-13.' },
      digitalEffectiveStartDate: { type: ['string', 'null'], format: 'date' },
      contributors: { type: ['array', 'null'], items: { type: 'object' } },
    },
    metadata: {
      owner: {
        service: 'CI0093421',
        team: 'TM-PRODUCT-CATALOG',
        email: 'product-catalog@pearson.com',
      },
      domain: 'product',
      dataClassification: 'internal',
      piiFields: [],
      lifecycle: 'active',
    },
  },
  null,
  2,
);

const GOLD_DETAIL = {
  artifact_id: GOLD_SUBJECT,
  group_id: 'product',
  name: null,
  type: 'JSON',
  state: 'ENABLED',
  created_on: '2026-01-01T00:00:00Z',
  modified_on: '2026-01-01T00:00:00Z',
  content: GOLD_BODY,
  content_version: 'latest',
  versions: [
    { version: '1', state: 'ENABLED', created_on: '2026-01-01T00:00:00Z', created_by: null },
  ],
  owner: { service: 'CI0093421', team: null, email: null },
  domain: 'product',
  classification: 'internal',
  lifecycle: 'active',
  pii_fields: [],
  latest_version: '1',
  compatibility: null,
};

/**
 * The same body at its real width: 63 properties, which is what makes the
 * pane overflow. Generated rather than pasted — the names carry no meaning
 * here, only the count does.
 */
const LONG_DETAIL = {
  ...GOLD_DETAIL,
  content: JSON.stringify({
    ...JSON.parse(GOLD_BODY),
    properties: Object.fromEntries(
      Array.from({ length: 63 }, (_, i) => [
        `attribute${String(i + 1).padStart(2, '0')}`,
        { type: ['string', 'null'] },
      ]),
    ),
  }),
};

test.describe('a JSON Schema body in the Fields view', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/v1/**', (route) => {
      const url = route.request().url();
      if (url.includes('/schemas/overview')) {
        return route.fulfill({
          json: {
            total_schemas: 13,
            schemas_with_compatibility_rules: 0,
            non_compliant_schemas: 0,
          },
        });
      }
      if (url.includes('/schemas/non-compliant')) {
        return route.fulfill({
          json: { items: [], last_validated: new Date().toISOString(), total_checked: 13 },
        });
      }
      if (url.includes('/api/v1/services')) {
        return route.fulfill({ json: [] });
      }
      if (url.includes('/services')) {
        return route.fulfill({
          json: { artifact_id: GOLD_SUBJECT, group_id: 'product', producers: [], consumers: [] },
        });
      }
      if (url.includes(`/schemas/product/${GOLD_SUBJECT}`)) {
        return route.fulfill({ json: GOLD_DETAIL });
      }
      if (url.includes('/schemas')) {
        return route.fulfill({ json: { items: [], total: 0, limit: 500, offset: 0 } });
      }
      return route.continue();
    });
  });

  test('lists the properties as fields, not the metadata block alone', async ({ page }) => {
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);

    const table = page.getByRole('table', { name: 'Schema fields' });
    await expect(table).toBeVisible();
    // Every property, in body order — a JSON Schema's `properties` map is its
    // field list exactly as Avro's `fields` array is.
    await expect(table.getByRole('row')).toHaveCount(6); // 5 properties + header
    for (const name of ['inventoryItemId', 'productId', 'isbn13', 'contributors']) {
      await expect(table.getByRole('cell', { name, exact: true })).toBeVisible();
    }

    // The metadata block is still below it, from the same body.
    await expect(page.getByText('compliance is read from this block')).toBeVisible();
  });

  test('renders a type union and a format rather than the word object', async ({ page }) => {
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);
    const table = page.getByRole('table', { name: 'Schema fields' });

    // `{"type":["string","null"]}` is JSON Schema's union. Read as a plain
    // object it rendered as `object`, which was true of 62 of the 63 real
    // properties and told a reader nothing.
    await expect(table.getByRole('cell', { name: 'string | null', exact: true }).first()).toBeVisible();
    await expect(table.getByRole('cell', { name: 'string | null (date)' })).toBeVisible();
    await expect(table.getByRole('cell', { name: 'array<object> | null' })).toBeVisible();
    await expect(table.getByRole('cell', { name: 'object', exact: true })).toHaveCount(0);
  });

  test('names the container it read the fields from', async ({ page }) => {
    // `properties`, not Avro's `fields[]` — the Path column says which dialect
    // the table is reading, and the two are not interchangeable.
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);
    const table = page.getByRole('table', { name: 'Schema fields' });
    await expect(table.getByRole('cell', { name: 'properties', exact: true })).toHaveCount(5);
    await expect(table.getByRole('cell', { name: 'fields[]', exact: true })).toHaveCount(0);
  });

  test('takes the record header from title, which is where a JSON Schema puts it', async ({
    page,
  }) => {
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);
    // Avro's `name`; JSON Schema's `title`. Without the fallback the header
    // read `(unnamed)`.
    await expect(page.getByText('Product', { exact: true })).toBeVisible();
  });

  test('scrolls the pane rather than the page, and pins both headers', async ({ page }) => {
    // The real body carries 63 properties. Left uncapped, the table pushed the
    // tabs and the compatibility rail off the screen while the Code view in
    // the same slot stayed put.
    await page.route(`**/api/v1/schemas/product/${GOLD_SUBJECT}*`, (route) =>
      route.fulfill({ json: LONG_DETAIL }),
    );
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);
    const table = page.getByRole('table', { name: 'Schema fields' });
    await expect(table).toBeVisible();

    const geometry = await page.evaluate(() => {
      const header = [...document.querySelectorAll('button[aria-expanded]')].find((b) =>
        (b.textContent ?? '').startsWith('Product'),
      ) as HTMLElement;
      let pane = header.parentElement as HTMLElement | null;
      while (
        pane &&
        !(pane.scrollHeight > pane.clientHeight + 10 && getComputedStyle(pane).overflowY === 'auto')
      ) {
        pane = pane.parentElement as HTMLElement | null;
      }
      if (!pane) return null;
      pane.scrollTop = 1500;
      const paneTop = pane.getBoundingClientRect().top;
      const columns = document.querySelector(
        '[aria-label="Schema fields"] [role="row"]',
      ) as HTMLElement;
      return {
        paneScrolls: pane.scrollHeight > pane.clientHeight,
        paneHeight: pane.clientHeight,
        scrolled: pane.scrollTop,
        // Both stuck to the top of the pane, the column names directly
        // beneath the section header rather than over or under it.
        sectionAt: Math.round(header.getBoundingClientRect().top - paneTop),
        columnsAt: Math.round(columns.getBoundingClientRect().top - paneTop),
        columnsText: columns.textContent,
      };
    });

    expect(geometry).not.toBeNull();
    expect(geometry!.paneScrolls).toBe(true);
    expect(geometry!.paneHeight).toBeLessThanOrEqual(570);
    expect(geometry!.scrolled).toBeGreaterThan(0);
    expect(geometry!.sectionAt).toBe(0);
    expect(geometry!.columnsAt).toBe(42);
    expect(geometry!.columnsText).toContain('Field');

    // And the page itself does not scroll to accommodate it.
    const pageScrolls = await page.evaluate(
      () => document.documentElement.scrollHeight > window.innerHeight + 1,
    );
    expect(pageScrolls).toBe(false);
  });

  test('each section collapses and reopens on its own', async ({ page }) => {
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);

    const fieldsSection = page.getByRole('button', { expanded: true }).filter({ hasText: 'Product' });
    const metadataSection = page
      .getByRole('button', { expanded: true })
      .filter({ hasText: 'metadata' });
    const table = page.getByRole('table', { name: 'Schema fields' });

    await expect(table).toBeVisible();
    await fieldsSection.click();
    await expect(table).toHaveCount(0);
    // Collapsing one leaves the other alone.
    await expect(page.getByText('dataClassification')).toBeVisible();

    await metadataSection.click();
    await expect(page.getByText('dataClassification')).toHaveCount(0);

    // Both headers are still there to reopen from — a collapsed section must
    // not vanish along with its rows.
    await page.getByRole('button', { expanded: false }).filter({ hasText: 'Product' }).click();
    await expect(table.getByRole('row')).toHaveCount(6);
  });

  test('a search filters the rows down to the matches, and counts them per section', async ({
    page,
  }) => {
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);
    const table = page.getByRole('table', { name: 'Schema fields' });
    await expect(table.getByRole('row')).toHaveCount(6);

    const find = page.getByPlaceholder('Find a field, key or value…');
    await find.fill('isbn');

    // The match label used to be the only thing in that row that could not
    // shrink, so a long one squeezed this input to zero width and the find
    // field vanished mid-query. It has to still be usable.
    await expect(find).toBeVisible();
    expect((await find.boundingBox())!.width).toBeGreaterThan(40);

    // Highlighting alone left a match at row 40 of 63 off-screen: the toolbar
    // said "1 match" and the pane showed no sign of it.
    await expect(table.getByRole('row')).toHaveCount(2); // header + isbn13
    await expect(table.getByRole('cell', { name: 'isbn13', exact: true })).toBeVisible();
    await expect(table.getByRole('cell', { name: 'productId', exact: true })).toHaveCount(0);

    // The section says what it is showing of what it has, and the metadata
    // section says plainly that the query missed it.
    await expect(page.getByRole('button', { expanded: true }).filter({ hasText: 'Product' })).toContainText(
      '1 of 5',
    );
    await expect(
      page.getByRole('button', { expanded: true }).filter({ hasText: 'metadata' }),
    ).toContainText('0 of 7');

    // A query that hits nothing at all says so once, not once per section.
    await find.fill('zzzz');
    await expect(page.getByText('Nothing in this body matches “zzzz”.')).toBeVisible();
    await expect(table).toHaveCount(0);
  });

  test("every section name sits on the table's own left edge", async ({ page }) => {
    // The disclosure chevron started out ahead of the name, which indented
    // `Product` and `metadata` 22px past the `Field` column header and every
    // field name under it. It sits on the right with the count now, so these
    // five edges are one edge.
    //
    // Located by their text rather than by walking the header's DOM: the
    // first version of this test read `span > span`, so a change of markup
    // failed it with a null dereference instead of reporting an edge.
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);
    const table = page.getByRole('table', { name: 'Schema fields' });
    await expect(table).toBeVisible();

    const fieldsHeader = page.getByRole('button', { expanded: true }).filter({ hasText: 'Product' });
    const metaHeader = page.getByRole('button', { expanded: true }).filter({ hasText: 'metadata' });

    const box = async (locator: Locator) => {
      await expect(locator).toBeVisible();
      return (await locator.boundingBox())!;
    };

    const edges = [
      await box(fieldsHeader.getByText('Product', { exact: true })),
      await box(table.getByRole('columnheader', { name: 'Field' })),
      await box(table.getByRole('cell', { name: 'inventoryItemId', exact: true })),
      await box(metaHeader.getByText('metadata', { exact: true })),
      await box(page.getByText('owner.service', { exact: true })),
    ].map((b) => Math.round(b.x));

    expect(new Set(edges).size).toBe(1);

    // And the chevron and the count sit on the name's own centre line, not
    // dropped below it by the baseline alignment the name/note pair needs.
    const centre = (b: { y: number; height: number }) => Math.round(b.y + b.height / 2);
    const centres = [
      centre(await box(fieldsHeader.getByText('Product', { exact: true }))),
      centre(await box(fieldsHeader.getByText('5', { exact: true }))),
      centre(await box(fieldsHeader.locator('svg'))),
    ];
    expect(new Set(centres).size).toBe(1);
  });

  test('the graph view offers no Expand, because it implements none', async ({ page }) => {
    // Code and Fields render a fullscreen overlay; the graph does not accept
    // the props at all, so the button was rendered there and did nothing when
    // clicked.
    //
    // It could not go on to open an overlay in another view, which is what I
    // first assumed and wrote a test for: view changes are `{ replace: true }`
    // so Back does not switch views, and while an overlay IS open it covers
    // the tabs. The armed flag has no reachable path out of the view that set
    // it — so the dead control was the whole of the defect.
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=graph`);
    await expect(page.getByRole('tab', { name: 'Graph' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('button', { name: 'Expand to fullscreen' })).toHaveCount(0);

    await page.getByRole('tab', { name: 'Fields' }).click();
    await expect(page.getByRole('table', { name: 'Schema fields' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Expand to fullscreen' })).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });

  test('the Expand button opens the fields table fullscreen', async ({ page }) => {
    // It was wired to the code view alone, so in Fields view the page's one
    // Expand button did nothing at all.
    await page.goto(`/schemas/product/${GOLD_SUBJECT}?schemaView=fields`);
    await page.getByRole('button', { name: 'Expand to fullscreen' }).click();

    const dialog = page.getByRole('dialog', { name: 'Schema fullscreen view' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('cell', { name: 'productId', exact: true })).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  });
});
