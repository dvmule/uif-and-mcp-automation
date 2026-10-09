import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * The Mapping tab's LAYOUT, which the component suite cannot judge.
 *
 * Every assertion here exists because a bug shipped past 117 green jsdom
 * tests. jsdom has no layout engine: it resolves no grid track, measures no
 * element, and honours no `position: sticky`. So a table whose `1fr` tracks
 * blew out to 33,645px inside a 1,399px viewport — every column but the first
 * parked thousands of pixels off-screen — passed the whole suite.
 *
 * LOCAL-ONLY SUITE -- no CI job runs this (decision recorded on UIFPE-738,
 * 2026-09-30). `npm run test:e2e` is the only way in, and apicurio's qe/ci.sh
 * does not call it: Playwright needs a browser download and a served build,
 * which is a real addition and did not belong in a CI-trigger fix. Run it by
 * hand when you touch the Mapping tab's layout.
 *
 * Two consequences, so this does not read as coverage it is not:
 *   - vite.config.ts deliberately keeps e2e/ out of vitest's include glob, so
 *     nothing picks these specs up by accident either.
 *   - the fixture below is a DOMAIN artifact, so if this suite is ever wired
 *     into CI it also needs its path in stacks/apicurio/ci-watch, or a domain
 *     change still will not run it (the UIFPE-728 mechanism).
 *
 * The fixture is the REAL artifact from the product domain rather than a
 * hand-built one. That matters: the blow-out was proportional to content, so a
 * two-column fixture with short strings would have measured fine and asserted
 * nothing.
 */

// `import.meta.url`, not `__dirname`: this project is ESM, where __dirname
// does not exist and the spec fails to load rather than to assert.
const HERE = dirname(fileURLToPath(import.meta.url));

const MAPPING = readFileSync(
  resolve(
    HERE,
    '../../../../domains/product/src/schemas/mapping/product/v1/product.mapping.json',
  ),
  'utf8',
);

const SUBJECT = 'product.product.mapping';

const DETAIL = {
  artifact_id: SUBJECT,
  group_id: 'product',
  name: null,
  type: 'JSON',
  state: 'ENABLED',
  created_on: '2026-07-22T00:00:00Z',
  modified_on: '2026-09-24T00:00:00Z',
  content: MAPPING,
  content_version: '1',
  versions: [
    { version: '1', state: 'ENABLED', created_on: '2026-07-22T00:00:00Z', created_by: null },
  ],
  owner: { service: 'CI0093421', team: null, email: null },
  domain: 'product',
  classification: 'internal',
  lifecycle: 'active',
  pii_fields: [],
  latest_version: '1',
  compatibility: null,
};

async function stubApi(page: Page) {
  await page.route('**/api/v1/**', (route) => {
    const url = route.request().url();
    if (url.includes('/non-compliant')) {
      return route.fulfill({
        json: { items: [], last_validated: '2026-09-24T00:00:00Z', total_checked: 1 },
      });
    }
    if (url.includes('/services')) {
      return route.fulfill({
        json: { artifact_id: SUBJECT, group_id: 'product', producers: [], consumers: [] },
      });
    }
    if (url.includes('/overview')) {
      return route.fulfill({ json: {} });
    }
    if (url.includes(SUBJECT)) {
      return route.fulfill({ json: DETAIL });
    }
    return route.fulfill({ json: { items: [], total: 0, limit: 500, offset: 0 } });
  });
}

const SHEETS = ['scalar', 'conditional', 'nested'] as const;

/**
 * The tail scrolls, the whole table does not run away. 3x the viewport is a
 * deliberately loose ceiling — the point is to catch a track that sizes itself
 * to its content (which measured 24x), not to pin a pixel width that a
 * legitimate column addition would break.
 */
const MAX_WIDTH_RATIO = 3;

for (const sheet of SHEETS) {
  test(`${sheet} sheet fits a scrollable width, not its content's width`, async ({ page }) => {
    await stubApi(page);
    await page.goto(`/schemas/product/${SUBJECT}?dtab=mapping&mkind=${sheet}`);

    const table = page.getByRole('table');
    await expect(table).toBeVisible();

    const { tableWidth, scrollerWidth } = await table.evaluate((el) => ({
      tableWidth: el.getBoundingClientRect().width,
      scrollerWidth: el.parentElement!.getBoundingClientRect().width,
    }));

    expect(tableWidth).toBeLessThan(scrollerWidth * MAX_WIDTH_RATIO);
    // And it must genuinely be wider than the pane, or the sticky column and
    // the horizontal scroll this design calls for are both decoration.
    expect(tableWidth).toBeGreaterThan(scrollerWidth);
  });
}

test('the gold column and the chevron stay pinned while the tail scrolls', async ({ page }) => {
  await stubApi(page);
  await page.goto(`/schemas/product/${SUBJECT}?dtab=mapping&mkind=scalar`);
  await expect(page.getByRole('table')).toBeVisible();

  const pinned = await page.getByRole('table').evaluate((el) => {
    const scroller = el.parentElement!;
    const row = [...el.querySelectorAll('[role="row"]')][1] as HTMLElement;
    const gold = row.querySelector('[role="cell"]') as HTMLElement;
    const chevron = row.querySelector('button') as HTMLElement;
    const before = gold.getBoundingClientRect().left;
    scroller.scrollLeft = 400;
    return {
      goldText: gold.textContent,
      goldMoved: Math.abs(gold.getBoundingClientRect().left - before),
      goldPosition: getComputedStyle(gold).position,
      chevronPosition: getComputedStyle(chevron).position,
      scrolled: scroller.scrollLeft,
    };
  });

  expect(pinned.scrolled).toBeGreaterThan(0);
  expect(pinned.goldPosition).toBe('sticky');
  // The chevron pins too: expanding a row you can see should not require
  // scrolling back to find its control.
  expect(pinned.chevronPosition).toBe('sticky');
  expect(pinned.goldMoved).toBeLessThan(2);
  expect(pinned.goldText).toBeTruthy();
});

test('a search hit is always visible on the row it matched', async ({ page }) => {
  // `pearsonImageUrl` is a TRANSFORM RULE name, matched by the search and --
  // until this was fixed -- shown in no column of the conditional sheet. The
  // page reported "6 matches" over six rows with nothing on screen explaining
  // any of them, which is precisely what filtering rather than tinting is
  // supposed to prevent.
  await stubApi(page);
  await page.goto(`/schemas/product/${SUBJECT}?dtab=mapping&mkind=conditional`);
  await expect(page.getByRole('table')).toBeVisible();

  await page.getByRole('searchbox').fill('pearsonImageUrl');
  await expect(page.getByTestId('mapping-match-count')).toContainText('match');

  const rows = page.getByRole('row');
  const count = await rows.count();
  expect(count).toBeGreaterThan(1);

  // Every data row must carry the query somewhere a reader can see.
  for (let i = 1; i < count; i += 1) {
    await expect(rows.nth(i)).toContainText('pearsonImageUrl');
  }
});

test('the facts band counts gold columns, not flattened rows', async ({ page }) => {
  // The band said "68 conditional" where the artifact declares 33 columns --
  // 68 is the branch-row count. A band labelled Columns reporting rows is
  // simply wrong, and no unit test noticed because both numbers are derived
  // from the same parse.
  await stubApi(page);
  await page.goto(`/schemas/product/${SUBJECT}?dtab=mapping`);

  const declared = JSON.parse(MAPPING) as { columns: { kind: string }[] };
  const counts = declared.columns.reduce<Record<string, number>>((acc, c) => {
    acc[c.kind] = (acc[c.kind] ?? 0) + 1;
    return acc;
  }, {});

  await expect(page.getByText(/scalar · .* conditional · .* nested/)).toContainText(
    `${counts.scalar} scalar · ${counts.conditional} conditional · ${counts.nested} nested`,
  );
});
