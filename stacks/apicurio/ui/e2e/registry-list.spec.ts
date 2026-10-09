import { test, expect, type Page } from '@playwright/test';

// ---------------------------------------------------------------------------
// Shared mock fixtures
// ---------------------------------------------------------------------------

const OVERVIEW_STATS = {
  total_schemas: 42,
  schemas_with_compatibility_rules: 0,
  non_compliant_schemas: 3,
};

const SCHEMA_LIST = {
  items: [
    {
      artifact_id: 'commerce.order.placed-value',
      group_id: 'default',
      name: null,
      type: 'AVRO',
      state: 'ENABLED',
      created_on: '2026-01-01T00:00:00Z',
      modified_on: '2026-01-02T00:00:00Z',
      latest_version: '2',
      owner: { service: 'CI0093442', team: 'TM-COMMERCE', email: null },
      domain: 'commerce',
      classification: 'confidential',
      lifecycle: 'active',
      pii_fields: [],
    },
    {
      artifact_id: 'product.catalog.updated-value',
      group_id: 'default',
      name: null,
      type: 'AVRO',
      state: 'ENABLED',
      created_on: '2026-01-01T00:00:00Z',
      modified_on: '2026-01-03T00:00:00Z',
      latest_version: '4',
      owner: { service: 'CI0093421', team: 'TM-PRODUCT', email: null },
      domain: 'product',
      classification: 'public',
      lifecycle: 'active',
      pii_fields: [],
    },
  ],
  total: 2,
  limit: 500,
  offset: 0,
};

const NON_COMPLIANT = {
  items: [],
  last_validated: new Date().toISOString(),
  total_checked: 2,
};

// Single handler to avoid Playwright glob-ordering ambiguity.
async function mockApi(page: Page) {
  await page.route('**/api/v1/**', (route) => {
    const url = route.request().url();
    if (url.includes('/schemas/overview')) return route.fulfill({ json: OVERVIEW_STATS });
    if (url.includes('/schemas/non-compliant')) return route.fulfill({ json: NON_COMPLIANT });
    if (url.includes('/api/v1/services')) return route.fulfill({ json: [] });
    // The list is GET /api/v1/schemas?limit=…&offset=… — no trailing slash, so
    // a '/schemas/' test never matched it and every list request fell through
    // to route.continue() unmocked. Keep this check LAST so the two specific
    // paths above still win.
    if (url.includes('/schemas')) return route.fulfill({ json: SCHEMA_LIST });
    return route.continue();
  });
}

// ---------------------------------------------------------------------------
// Tests
//
// Rewritten for 2a (UIFPE-507). The `Home · Browse all · Non-compliant ·
// Recently changed` tab bar these tests drove is gone: the list is one surface
// with a segmented partition in its toolbar, the search sits in the purple app
// header, and the Home surface — its stat cards, its "Needs attention" lanes
// and the registry health rail — was retired with it.
//
// The publish flow and the schema detail page still have no e2e coverage of
// their own; writing it needs a running BFF, which this branch's constraints
// exclude.
// ---------------------------------------------------------------------------

test.describe('registry list', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test('lands on the table with no ?view, and offers three segments', async ({ page }) => {
    await page.goto('/schemas');
    await expect(page.getByRole('table')).toBeVisible();
    await expect(page.getByRole('tab')).toHaveText(['All', 'Non-compliant', 'Recently changed']);
    // The retired tab labels must not come back under another control.
    await expect(page.getByRole('tab', { name: 'Home' })).toHaveCount(0);
    await expect(page.getByText('Browse all')).toHaveCount(0);
  });

  test('a stale ?view=overview link renders the table, not an empty one', async ({ page }) => {
    await page.goto('/schemas?view=overview');
    const table = page.getByRole('table');
    await expect(table).toBeVisible();
    await expect(table.getByText('commerce.order.placed')).toBeVisible();
    await expect(page.getByText(/no schemas match/i)).toHaveCount(0);
  });

  test('the glance cards carry the real figures', async ({ page }) => {
    await page.goto('/schemas');
    // Scoped to `main`: the nav's Schemas badge legitimately shows the same
    // total, so a bare `getByText('42')` now matches twice.
    const body = page.getByRole('main');
    await expect(body.getByText('subjects registered')).toBeVisible();
    await expect(body.getByText('42', { exact: true })).toBeVisible();
    await expect(body.getByText('failing a metadata rule')).toBeVisible();
    await expect(body.getByText('3', { exact: true })).toBeVisible();
  });

  test('compatibility-rule coverage is stated as a bare 0 of 42', async ({ page }) => {
    // The number the screen exists to show. Until it equals the subject count
    // nothing is checked against its previous version, and the design refuses
    // to soften that into a percentage or a reassuring caption.
    await page.goto('/schemas');
    await expect(page.getByText('0 of 42')).toBeVisible();
    await expect(page.getByText('have a compatibility rule set')).toBeVisible();
  });

  test('the search lives in the app header and filters the table', async ({ page }) => {
    await page.goto('/schemas');
    const box = page.getByRole('searchbox', { name: /search schemas/i });
    await expect(box).toBeVisible();
    await box.fill('commerce');
    await expect(page).toHaveURL(/search=commerce/);
    const table = page.getByRole('table');
    await expect(table.getByText('commerce.order.placed')).toBeVisible();
    await expect(table.getByText('product.catalog.updated')).toHaveCount(0);
  });

  test('slash focuses the search from anywhere on the page', async ({ page }) => {
    await page.goto('/schemas');
    await expect(page.getByRole('table')).toBeVisible();
    await page.getByRole('table').click({ position: { x: 5, y: 5 } });
    await page.keyboard.press('/');
    await expect(page.getByRole('searchbox', { name: /search schemas/i })).toBeFocused();
  });

  test('escape clears the search rather than leaving a stale query in the URL', async ({ page }) => {
    await page.goto('/schemas?search=commerce');
    const box = page.getByRole('searchbox', { name: /search schemas/i });
    await box.focus();
    await page.keyboard.press('Escape');
    await expect(page).not.toHaveURL(/search=/);
  });

  test('a segment click sets ?view and keeps the surface', async ({ page }) => {
    await page.goto('/schemas');
    await page.getByRole('tab', { name: 'Non-compliant' }).click();
    await expect(page).toHaveURL(/view=non-compliant/);
    // Nothing is non-compliant in the fixture, so this is the empty-segment
    // state — and it must not read as "nothing found for your search".
    await expect(page.getByRole('tab', { name: 'Non-compliant' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  test('a narrowed list still says how many there are, without the count pill', async ({
    page,
  }) => {
    await page.goto('/schemas');
    await page.getByRole('searchbox', { name: /search schemas/i }).fill('commerce');
    // The pill is gone; the footer's Show all carries the total, so a filtered
    // list is never left without a figure.
    await expect(page.getByRole('button', { name: /show all 2/i })).toBeVisible();
    await expect(page.getByRole('status', { name: /Showing \d+ of \d+ subjects/ })).toHaveCount(0);
  });

  test('the publish CTA routes to the publish page', async ({ page }) => {
    await page.goto('/schemas');
    await page.getByRole('link', { name: /publish a schema/i }).click();
    await expect(page).toHaveURL(/\/schemas\/publish/);
  });

  test('the retired Home surfaces are gone, not hidden', async ({ page }) => {
    await page.goto('/schemas');
    await expect(page.getByRole('table')).toBeVisible();
    await expect(page.getByText('Needs attention')).toHaveCount(0);
    await expect(page.getByText('Registry at a glance')).toHaveCount(0);
    await expect(page.getByText('Your schemas')).toHaveCount(0);
    await expect(page.getByRole('complementary', { name: /registry health/i })).toHaveCount(0);
    // The identity note replaces "Your schemas", and still explains itself
    // rather than rendering an empty table or a zero count.
    await expect(page.getByText('Owned by me')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// The nav panel (Echo's Left Nav Panel pattern). Collapse is real state that
// outlives a reload, so a unit test driving one mounted tree cannot show it.
// ---------------------------------------------------------------------------

test.describe('nav panel', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test('collapses to the icon rail and back', async ({ page }) => {
    await page.goto('/schemas');
    const nav = page.getByRole('navigation', { name: 'Sections' });
    await expect(nav).toHaveCSS('width', '220px');

    await page.getByRole('button', { name: /collapse/i }).click();
    await expect(nav).toHaveCSS('width', '72px');

    // Still navigable by name with no label on screen — an icon on its own has
    // no accessible name at all.
    await expect(page.getByRole('link', { name: 'Services' })).toBeVisible();

    await page.getByRole('button', { name: /expand/i }).click();
    await expect(nav).toHaveCSS('width', '220px');
  });

  test('remembers the collapsed rail across a reload', async ({ page }) => {
    await page.goto('/schemas');
    await page.getByRole('button', { name: /collapse/i }).click();
    await expect(page.getByRole('navigation', { name: 'Sections' })).toHaveCSS('width', '72px');

    await page.reload();
    await expect(page.getByRole('navigation', { name: 'Sections' })).toHaveCSS('width', '72px');
  });

  test('the collapsed rail carries no mark at all', async ({ page }) => {
    await page.goto('/schemas');
    await expect(page.locator('aside img[src="/pearson-logo.svg"]')).toBeVisible();

    await page.getByRole('button', { name: /collapse/i }).click();
    // No wordmark, because the brand's 100px digital minimum does not fit a
    // 40px rail; and no shorthand either, which was removed by request.
    await expect(page.locator('aside img')).toHaveCount(0);

    // Expanding brings it back — the rail is unbranded, not the panel.
    await page.getByRole('button', { name: /expand/i }).click();
    await expect(page.locator('aside img[src="/pearson-logo.svg"]')).toBeVisible();
  });

  test('the header names the section, and does not repeat the product name', async ({ page }) => {
    await page.goto('/schemas');
    const header = page.getByRole('banner');
    await expect(header.getByText('Schemas', { exact: true })).toBeVisible();
    // The nav carries the product name; twice on one screen was the defect.
    await expect(header.getByText('Registry')).toHaveCount(0);

    await page.goto('/services');
    await expect(header.getByText('Services', { exact: true })).toBeVisible();
  });
  test('the bell and the user chip are placeholders that claim nothing', async ({ page }) => {
    await page.goto('/schemas');
    const header = page.getByRole('banner');

    // Present, and honest about doing nothing.
    const bell = header.getByRole('button', { name: /notifications/i });
    await expect(bell).toBeVisible();
    await expect(bell).toHaveAttribute('aria-disabled', 'true');
    // No invented count. An invented "3" is worse than no bell.
    await expect(bell).toHaveText('');

    // No fabricated identity: the chip says what is true, and the notice in
    // the page body must not be contradicted one row above it.
    await expect(header.getByText('Not signed in')).toBeVisible();
    await expect(page.getByText(/becomes a fourth segment once sign-in lands/)).toBeVisible();
  });
  test('nav counts are the real figures, and de-duplicated', async ({ page }) => {
    await page.route('**/api/v1/services', (route) =>
      // Six rows, four services: the CMDB shape this app exists to cope with.
      route.fulfill({
        json: [
          { urn: 'urn:a', name: 'a', ci_id: '1', domain: 'd' },
          { urn: 'urn:a', name: 'a', ci_id: '1', domain: 'd' },
          { urn: 'urn:b', name: 'b', ci_id: '2', domain: 'd' },
          { urn: 'urn:c', name: 'c', ci_id: '3', domain: 'd' },
          { urn: 'urn:c', name: 'c', ci_id: '3', domain: 'd' },
          { urn: 'urn:d', name: 'd', ci_id: '4', domain: 'd' },
        ],
      }),
    );
    await page.goto('/schemas');
    const nav = page.getByRole('navigation', { name: 'Sections' });
    await expect(nav.getByRole('link', { name: 'Schemas' })).toContainText('42');
    // Four, not six. Rows are not services.
    await expect(nav.getByRole('link', { name: 'Services' })).toContainText('4');
  });

  test('a count that failed to load shows no badge rather than a zero', async ({ page }) => {
    await page.route('**/api/v1/services', (route) => route.fulfill({ status: 500, json: {} }));
    await page.goto('/schemas');
    const services = page
      .getByRole('navigation', { name: 'Sections' })
      .getByRole('link', { name: 'Services' });
    await expect(services).toBeVisible();
    // "Services 0" would claim an empty catalogue that nobody established.
    await expect(services).not.toContainText('0');
    await expect(services).toHaveText('Services');
  });
  test('the wordmark, the product name and the nav icons share one left edge', async ({
    page,
  }) => {
    await page.goto('/schemas');
    // `page.evaluate` does not auto-wait the way a locator does, so the panel
    // has to be on screen before anything is measured — without this the
    // measurement can run against a half-rendered aside.
    await expect(page.locator('aside img[src="/pearson-logo.svg"]')).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Sections' }).getByRole('link')).toHaveCount(
      2,
    );

    const edges = await page.evaluate(() => {
      const logo = document.querySelector('aside img[src="/pearson-logo.svg"]')!;
      const name = [...document.querySelectorAll('aside p')].find(
        (p) => p.textContent?.trim() === 'Registry',
      )!;
      const pad = parseFloat(getComputedStyle(name).paddingLeft);
      return {
        logo: logo.getBoundingClientRect().left,
        // The label carries its own padding, so its text edge is box + padding.
        name: name.getBoundingClientRect().left + pad,
        icons: [...document.querySelectorAll('nav[aria-label="Sections"] a svg')].map(
          (i) => i.getBoundingClientRect().left,
        ),
      };
    });
    expect(edges.icons.length).toBe(2);
    // The logo's box edge IS its artwork edge only because the SVG canvas was
    // cropped to the artwork; as exported it carried a 10.8% empty margin.
    for (const x of [edges.name, ...edges.icons]) {
      expect(x).toBeCloseTo(edges.logo, 0);
    }
  });

  test('the wordmark is rendered at or above its brand minimum size', async ({ page }) => {
    await page.goto('/schemas');
    // The primary logo's digital minimum is 100px wide. It is set at 104, so
    // there is only 4px of headroom — worth failing a build over.
    const wordmark = await page
      .locator('aside img[src="/pearson-logo.svg"]')
      .evaluate((el) => el.getBoundingClientRect().width);
    expect(wordmark).toBeGreaterThanOrEqual(100);
  });

  test('the wordmark carries no dead canvas around its artwork', async ({ page }) => {
    await page.goto('/schemas');
    await expect(page.locator('aside img[src="/pearson-logo.svg"]')).toBeVisible();
    for (const file of ['/pearson-logo.svg']) {
      const fit = await page.evaluate(async (f) => {
        const host = document.createElement('div');
        host.style.cssText = 'position:absolute;left:-9999px;width:3000px';
        host.innerHTML = await (await fetch(f)).text();
        document.body.appendChild(host);
        const svg = host.querySelector('svg') as SVGSVGElement;
        const [vx, vy, vw, vh] = (svg.getAttribute('viewBox') || '').split(/\s+/).map(Number);
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        svg.querySelectorAll('path').forEach((el) => {
          const b = (el as unknown as SVGGraphicsElement).getBBox();
          if (!b.width && !b.height) return;
          minX = Math.min(minX, b.x); minY = Math.min(minY, b.y);
          maxX = Math.max(maxX, b.x + b.width); maxY = Math.max(maxY, b.y + b.height);
        });
        host.remove();
        return { slackX: (minX - vx) / vw, slackY: (minY - vy) / vh,
                 slackR: (vx + vw - maxX) / vw, slackB: (vy + vh - maxY) / vh };
      }, file);
      // Under 1% on every side. A margin inside the file cannot be padded
      // around in layout, and it silently shrinks the rendered mark.
      for (const [side, v] of Object.entries(fit)) {
        expect(Math.abs(v), `${file} ${side}`).toBeLessThan(0.01);
      }
    }
  });
});
