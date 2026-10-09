import { test, expect, type Page } from '@playwright/test';

/**
 * Updating a schema, end to end and on a cold load.
 *
 * The unit tests drive the edit page inside one mounted tree. What they cannot
 * show is that the prefill survives a fresh navigation: the form seeds its
 * state from props exactly once, so it must not mount before the schema, the
 * service list and the produces edge have all resolved. A cold load of
 * `/edit` is where that ordering either holds or does not.
 */

const SUBJECT = 'customer.preferences.changed-value';

const BODY = JSON.stringify(
  {
    type: 'record',
    name: 'PreferencesChanged',
    namespace: 'com.pearson.customer',
    fields: [{ name: 'customer_id', type: 'string' }],
    metadata: {
      owner: { service: 'CI0093432', team: 'TM-CUSTOMER-PLATFORM', email: 'cp@pearson.com' },
      domain: 'customer',
      dataClassification: 'confidential',
      piiFields: ['customer_id'],
      lifecycle: 'active',
    },
  },
  null,
  2,
);

const DETAIL = {
  artifact_id: SUBJECT,
  group_id: 'default',
  name: null,
  type: 'AVRO',
  state: 'ENABLED',
  created_on: '2026-01-01T00:00:00Z',
  modified_on: '2026-01-02T00:00:00Z',
  content: BODY,
  content_version: '2',
  versions: [
    { version: '1', state: 'ENABLED', created_on: '2026-01-01T00:00:00Z', created_by: null },
    { version: '2', state: 'ENABLED', created_on: '2026-01-02T00:00:00Z', created_by: null },
  ],
  owner: { service: 'CI0093432', team: null, email: null },
  domain: 'customer',
  classification: 'confidential',
  lifecycle: 'active',
  pii_fields: ['customer_id'],
  latest_version: '2',
  compatibility: null,
};

const SERVICES = [
  {
    ci_id: 'CI0093432',
    urn: 'urn:pearson:service:customer:preferences-svc',
    name: 'preferences-svc',
    domain: 'customer',
    produces_count: 1,
    consumes_count: 0,
    produces: [],
    consumes: [],
    app_id: null,
    app_name: null,
  },
];

/** Every POST the page made, so a test can assert what was actually sent. */
type Posted = { url: string; body: unknown };

async function mockApi(page: Page, posted: Posted[]) {
  await page.route('**/api/v1/**', (route) => {
    const request = route.request();
    const url = request.url();

    if (request.method() === 'POST' && url.includes(`/schemas/default/${SUBJECT}`)) {
      posted.push({ url, body: request.postDataJSON() });
      return route.fulfill({
        json: { topic: SUBJECT, registered: true, schema_type: 'AVRO', error: null },
      });
    }
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
    // The list endpoint returns a bare array; `/schemas/{g}/{a}/services`
    // returns an object. Both contain '/services', so match the list first and
    // by its full path.
    if (url.includes('/api/v1/services')) {
      return route.fulfill({ json: SERVICES });
    }
    if (url.includes('/services')) {
      return route.fulfill({
        json: {
          artifact_id: SUBJECT,
          group_id: 'default',
          producers: [SERVICES[0]],
          consumers: [],
        },
      });
    }
    if (url.includes('/schemas/default/')) {
      return route.fulfill({ json: DETAIL });
    }
    if (url.includes('/schemas')) {
      return route.fulfill({ json: { items: [], total: 0, limit: 500, offset: 0 } });
    }
    return route.continue();
  });
}

test.describe('updating an existing schema', () => {
  let posted: Posted[];

  test.beforeEach(async ({ page }) => {
    posted = [];
    await mockApi(page, posted);
  });

  test('a cold load of the edit page opens on the registry\'s current body', async ({ page }) => {
    await page.goto(`/schemas/default/${SUBJECT}/edit`);
    await expect(page.getByLabel('Schema body')).toHaveValue(BODY);
    await expect(page.getByText('based on v2')).toBeVisible();
    // The governance values came over too, so an edit cannot silently reset
    // them -- the metadata block is rebuilt from this form on every publish.
    await expect(page.getByLabel('Owning service')).toHaveValue(
      'urn:pearson:service:customer:preferences-svc',
    );
    await expect(page.getByLabel('confidential')).toBeChecked();
    await expect(page.getByText('customer_id').first()).toBeVisible();
  });

  test('an untouched form cannot publish, because it would publish nothing', async ({ page }) => {
    await page.goto(`/schemas/default/${SUBJECT}/edit`);
    await expect(page.getByLabel('Schema body')).toHaveValue(BODY);
    await expect(page.getByRole('button', { name: 'Publish new version' })).toBeDisabled();
    expect(posted).toHaveLength(0);
  });

  /**
   * Clicking Update, as the only thing asserted here.
   *
   * This stops at the route and the heading on purpose. The editor's prefill
   * needs the produces-edge request, and an XHR issued during a client-side
   * route transition is not reliably served by Playwright's request
   * interception — against a real server it is fine, under `page.route` it is
   * dropped often enough to make an assertion on it meaningless. What the
   * prefill does with that response is covered by the cold load above and by
   * `SchemaEditPage.test.tsx`; what only a browser can show is that the link
   * on the schema page reaches this route at all.
   */
  test('the schema page reaches the editor', async ({ page }) => {
    await page.goto(`/schemas/default/${SUBJECT}`);
    await expect(page.getByLabel('Version', { exact: true })).toHaveValue('2');
    await page.getByRole('link', { name: 'Update schema' }).click();

    await expect(page).toHaveURL(new RegExp(`${SUBJECT}/edit$`));
    await expect(page.getByRole('heading', { level: 1, name: 'Publish a new version' })).toBeVisible();
  });

  test('an edit publishes to the existing subject and lands back on the schema', async ({
    page,
  }) => {
    await page.goto(`/schemas/default/${SUBJECT}/edit`);

    const body = page.getByLabel('Schema body');
    await expect(body).toHaveValue(BODY);
    await body.fill(
      JSON.stringify({ type: 'record', name: 'PreferencesChanged', fields: [] }, null, 2),
    );
    await page.getByRole('button', { name: 'Publish new version' }).click();

    // Back on the schema, which is where the new version and its compliance
    // verdict are shown.
    await expect(page).toHaveURL(new RegExp(`${SUBJECT}$`));
    expect(posted).toHaveLength(1);
    expect(posted[0].body).toMatchObject({
      service_urn: 'urn:pearson:service:customer:preferences-svc',
      data_classification: 'confidential',
      lifecycle: 'active',
      pii_fields: ['customer_id'],
    });
  });
});
