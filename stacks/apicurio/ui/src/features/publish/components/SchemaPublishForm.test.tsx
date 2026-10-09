import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { SchemaPublishForm } from './SchemaPublishForm';

const API = 'http://localhost:8000/api/v1';

const SERVICES = [
  { ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce', produces_count: 1, consumes_count: 2, app_id: null, app_name: null },
];

const server = setupServer(http.get(`${API}/services`, () => HttpResponse.json(SERVICES)));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderForm(onSubmit = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SchemaPublishForm onSubmit={onSubmit} isSubmitting={false} />
    </QueryClientProvider>,
  );
  return onSubmit;
}

it('warns when the body is not parseable JSON', async () => {
  renderForm();
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{not json' } });
  await waitFor(() => expect(screen.getByText(/not valid json/i)).toBeInTheDocument());
});

it('warns that a non-JSON type gets no metadata block', async () => {
  renderForm();
  fireEvent.change(screen.getByLabelText(/schema type/i), { target: { value: 'PROTOBUF' } });
  await waitFor(() =>
    expect(screen.getByText(/no metadata block/i)).toBeInTheDocument(),
  );
});

it('blocks submission when no service is chosen, and says so', async () => {
  const onSubmit = renderForm();
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));
  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.getByText(/owning service/i, { selector: 'p' })).toBeInTheDocument();
});

it('blocks submission when no classification is chosen, and says so', async () => {
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.getByText(/data classification/i, { selector: 'p' })).toBeInTheDocument();
});

it('submits the full request once every required field is set', async () => {
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));
  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  expect(onSubmit).toHaveBeenCalledWith({
    groupId: 'default',
    artifactId: 'commerce.order.placed-value',
    body: expect.objectContaining({
      service_urn: 'urn:pearson:service:commerce:checkout-svc',
      data_classification: 'confidential',
      lifecycle: 'active',
      pii_fields: [],
    }),
  });
});

it('blocks a YAML body that the detector would register as JSON', async () => {
  // The detector falls back to JSON for anything it does not recognise, so a
  // YAML AsyncAPI document was labelled "JSON detected" while the JSON gate —
  // which tested the body's first character — was skipped. It registered as
  // type JSON, and `inject_metadata` returns an unparseable body UNCHANGED,
  // so the artifact landed with no metadata block: non-compliant, from the
  // page that exists to stop exactly that.
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.api' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), {
    target: { value: 'asyncapi: 3.0.0\ninfo:\n  title: Orders\n  version: 1.0.0\n' },
  });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  expect(screen.getByText(/not valid JSON/i)).toBeInTheDocument();
  // And it says what actually happens, not "the registry will reject it".
  // Matched on the whole clause: the type-override warning below it mentions
  // a missing metadata block too.
  expect(
    screen.getByText(/It would register with no metadata block/i),
  ).toBeInTheDocument();
  expect(screen.queryByText('Valid JSON')).not.toBeInTheDocument();

  const publish = screen.getByRole('button', { name: /publish/i });
  expect(publish).toBeDisabled();
  fireEvent.click(publish);
  expect(onSubmit).not.toHaveBeenCalled();
});

it('blocks prose, which the detector also calls JSON', async () => {
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), {
    target: { value: 'Connection refused: could not reach the registry' },
  });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  expect(screen.getByRole('button', { name: /publish/i })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  expect(onSubmit).not.toHaveBeenCalled();
});

it('still lets a Protobuf body through, which is not JSON and must not be gated', async () => {
  // The gate must narrow to JSON-based types only. Protobuf, GraphQL and XSD
  // are legitimately unparseable as JSON and the registry accepts them.
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'learning.learner.snapshot' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), {
    target: { value: 'syntax = "proto3";\nmessage Learner { string id = 1; }' },
  });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  expect(screen.queryByText(/not valid JSON/i)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
});

it('submits a PII name that was typed but never confirmed with Enter', async () => {
  // The input used to be the one uncontrolled field on the form, so a name
  // typed and not confirmed was invisible to both the submit handler and the
  // metadata preview. The schema registered with `piiFields: []` while the
  // name was still visibly in the box — and the page then navigated away, so
  // the declaration was gone for good.
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'learning.learner-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  const piiInput = screen.getByLabelText(/pii fields/i);
  fireEvent.change(piiInput, { target: { value: 'customer_email' } });
  // No Enter, no comma. What the box shows is what must be written.
  expect(screen.getByText(/"customer_email"/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      body: expect.objectContaining({ pii_fields: ['customer_email'] }),
    }),
  );
});

it('does not add the draft twice when blur and submit both see it', async () => {
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'learning.learner-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  const piiInput = screen.getByLabelText(/pii fields/i);
  fireEvent.change(piiInput, { target: { value: 'customer_email' } });
  fireEvent.blur(piiInput);
  expect(piiInput).toHaveValue('');

  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      body: expect.objectContaining({ pii_fields: ['customer_email'] }),
    }),
  );
});

it('adds, deduplicates and removes PII tags, and submits exactly the surviving set', async () => {
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  const piiInput = screen.getByLabelText(/pii fields/i);
  fireEvent.change(piiInput, { target: { value: 'email' } });
  fireEvent.keyDown(piiInput, { key: 'Enter' });
  fireEvent.change(piiInput, { target: { value: 'email' } });
  fireEvent.keyDown(piiInput, { key: 'Enter' }); // duplicate — must not add a second chip
  fireEvent.change(piiInput, { target: { value: 'fullName' } });
  fireEvent.keyDown(piiInput, { key: ',' });

  expect(screen.getAllByText('email')).toHaveLength(1);
  expect(screen.getByText('fullName')).toBeInTheDocument();
  expect(piiInput).toHaveValue(''); // cleared after every commit, duplicate included

  fireEvent.click(screen.getByRole('button', { name: /remove email/i }));
  expect(screen.queryByText('email')).not.toBeInTheDocument();
  expect(screen.getByText('fullName')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      body: expect.objectContaining({ pii_fields: ['fullName'] }),
    }),
  );
});

it('loads an uploaded file into the schema body', async () => {
  renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  const file = new File(['{"type":"record","name":"Uploaded","fields":[]}'], 'schema.json', {
    type: 'application/json',
  });
  const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(fileInput, { target: { files: [file] } });
  await waitFor(() =>
    expect(screen.getByLabelText(/schema body/i)).toHaveValue('{"type":"record","name":"Uploaded","fields":[]}'),
  );
});

it('explains why the submit button is disabled, staying in sync with what actually gates it', async () => {
  renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  const button = screen.getByRole('button', { name: /publish/i });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute('aria-describedby', 'pub-submit-hint');
  expect(screen.getByText(/choose/i, { selector: 'p' })).toHaveTextContent(/subject/i);

  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  expect(button).not.toBeDisabled();
  expect(button).not.toHaveAttribute('aria-describedby');
});

const PROTO_BODY = 'syntax = "proto3";\n\nmessage OrderPlaced {\n  string order_id = 1;\n}\n';

it('publishes a PROTOBUF body without calling it invalid JSON', async () => {
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/schema type/i), { target: { value: 'PROTOBUF' } });
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: PROTO_BODY } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  // The metadata-block warning is correct and must stay; the JSON parse error is not.
  expect(screen.getByText(/no metadata block/i)).toBeInTheDocument();
  expect(screen.queryByText(/not valid json/i)).not.toBeInTheDocument();

  const button = screen.getByRole('button', { name: /publish/i });
  expect(button).not.toBeDisabled();
  fireEvent.click(button);
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({ body: expect.objectContaining({ type_override: 'PROTOBUF' }) }),
  );
});

it('does not apply the JSON gate to an auto-detected non-JSON body', async () => {
  renderForm();
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: PROTO_BODY } });
  await waitFor(() => expect(screen.getByLabelText(/schema body/i)).toHaveValue(PROTO_BODY));
  expect(screen.queryByText(/not valid json/i)).not.toBeInTheDocument();
});

it('still blocks a malformed body when the chosen type is JSON-based', async () => {
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/schema type/i), { target: { value: 'AVRO' } });
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: 'not json at all' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));

  expect(screen.getByText(/not valid json/i)).toBeInTheDocument();
  const button = screen.getByRole('button', { name: /publish/i });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(onSubmit).not.toHaveBeenCalled();
});

it('says the service list failed to load rather than silently offering nothing', async () => {
  server.use(http.get(`${API}/services`, () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
  renderForm();
  await waitFor(() =>
    expect(screen.getByText(/couldn't load the service list/i)).toBeInTheDocument(),
  );
  // And it must not still be instructing the user to pick from an empty list.
  expect(
    screen.queryByText(/Required. Registration fails outright/i),
  ).not.toBeInTheDocument();
});

// The live CMDB mock returns 16 services with only 14 distinct URNs —
// content-svc and pricing-svc each appear twice. Keying the <option>s by URN
// therefore produced duplicate React keys ("children may be duplicated and/or
// omitted — the behavior is unsupported") and offered the same service twice
// in the dropdown. A service URN is an identity; the picker must show it once.
it('offers each service URN once even when the CMDB returns it twice', async () => {
  server.use(
    http.get(`${API}/services`, () =>
      HttpResponse.json([
        { ci_id: 'CI0093441', urn: 'urn:pearson:service:content:content-svc', name: 'content-svc', domain: 'content', produces_count: 1, consumes_count: 0, app_id: null, app_name: null },
        { ci_id: 'CI0093441', urn: 'urn:pearson:service:content:content-svc', name: 'content-svc', domain: 'content', produces_count: 1, consumes_count: 0, app_id: null, app_name: null },
        { ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce', produces_count: 1, consumes_count: 2, app_id: null, app_name: null },
      ]),
    ),
  );
  renderForm();

  const select = screen.getByLabelText(/owning service/i);
  await waitFor(() =>
    expect(screen.getAllByRole('option', { name: /content-svc/ })).toHaveLength(1),
  );
  // "Choose a service…" plus the two distinct services, and nothing more.
  expect(select.querySelectorAll('option')).toHaveLength(3);
});

// ---------------------------------------------------------------------------
// 3a — the surfaces the redesign added. Everything above is behaviour that
// existed before and must keep working; everything below is the form telling
// the user what the registry is about to write, before they commit to it.
// ---------------------------------------------------------------------------

describe('what gets written', () => {
  it('previews the five fields compliance is read from, with nulls shown not omitted', async () => {
    renderForm();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());

    const preview = screen.getByText(/"metadata": \{/).closest('pre')!;
    for (const key of ['"service": ', '"domain": ', '"dataClassification": ', '"lifecycle": ', '"piiFields": [']) {
      expect(preview).toHaveTextContent(key.trim());
    }
    // An unset required value renders `null` rather than vanishing: an omitted
    // row reads as "this schema has no such field", which is a different and
    // false statement from "nothing is in it yet".
    expect(preview).toHaveTextContent('null');
    expect(screen.getByText('dataClassification is still null')).toBeInTheDocument();
    expect(screen.getByText('owner.service is still null')).toBeInTheDocument();
  });

  // metadata_injector.py writes `"domain": ci.domain` — the OWNING SERVICE's
  // domain from its CMDB record, not the subject's first segment. Previewing
  // the subject's would show a value the registry is not going to write.
  it('takes the domain from the owning service, not the subject', async () => {
    renderForm();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
    // A `finance.*` subject deliberately owned by a `commerce` service.
    fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'finance.invoice.raised-value' } });
    fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });

    const preview = screen.getByText(/"metadata": \{/).closest('pre')!;
    expect(preview).toHaveTextContent('"commerce"');
    expect(preview).not.toHaveTextContent('"finance"');
    // The CI id, not the URN — that is what lands in owner.service.
    expect(preview).toHaveTextContent('"CI0093423"');
  });

  it('recomputes as fields change', async () => {
    renderForm();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
    expect(screen.getByText('dataClassification is still null')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/restricted/i));
    expect(screen.queryByText('dataClassification is still null')).not.toBeInTheDocument();
    const preview = screen.getByText(/"metadata": \{/).closest('pre')!;
    expect(preview).toHaveTextContent('"restricted"');
  });

  it('carries committed PII fields into the preview', async () => {
    renderForm();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
    const pii = screen.getByLabelText(/pii fields/i);
    fireEvent.change(pii, { target: { value: 'buyer_email' } });
    fireEvent.keyDown(pii, { key: 'Enter' });
    const preview = screen.getByText(/"metadata": \{/).closest('pre')!;
    expect(preview).toHaveTextContent('"buyer_email"');
  });
});

describe('before you publish', () => {
  it('shows the four gates and moves them as fields are filled', async () => {
    renderForm();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
    // The checklist and the disabled-button sentence are two views of one
    // array, so they cannot disagree about what is outstanding.
    expect(screen.getByText('A subject')).toBeInTheDocument();
    expect(screen.getByText('A schema body that parses as JSON')).toBeInTheDocument();
    expect(screen.getByText('An owning service the CMDB can resolve')).toBeInTheDocument();
    expect(screen.getByText('A data classification')).toBeInTheDocument();

    expect(screen.getByText(/choose/i, { selector: 'p' })).toHaveTextContent(
      'Choose a subject, a schema body, an owning service, and a data classification to publish.',
    );

    fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
    fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
    fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });

    // The exact wording the design quotes for the last outstanding gate.
    expect(screen.getByText('Choose a data classification to publish.')).toBeInTheDocument();
  });

  it('names the JSON gate specifically once a body is present but unparseable', async () => {
    renderForm();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
    fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{not json' } });
    expect(screen.getByText(/choose/i, { selector: 'p' })).toHaveTextContent(
      /a valid JSON schema body/,
    );
  });
});

describe('the subject convention check', () => {
  it('confirms a conforming subject and names the domain and type it reads', async () => {
    renderForm();
    fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'product.marketing-copy.gold' } });
    const ok = screen.getByText(/Matches the convention/);
    expect(ok).toBeInTheDocument();
    expect(ok.textContent).toContain('product');
    expect(ok.textContent).toContain('gold');
  });

  it('says nothing either way about a subject another standard governs', async () => {
    // `product-created-data` is the normative per-operation form and
    // `commerce.order.placed-value` is the legacy Kafka shape. Neither is this
    // convention's business, so neither is confirmed OR warned about —
    // confirming them would be a false pass, warning would be noise.
    renderForm();
    for (const subject of ['product-created-data', 'commerce.order.placed-value', 'legal-vocabulary']) {
      fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: subject } });
      expect(screen.queryByText(/Matches the convention/), subject).toBeNull();
      expect(screen.queryByText(/does not match/), subject).toBeNull();
    }
  });

  it('warns about a malformed subject that claims a type, without blocking it', async () => {
    const onSubmit = renderForm();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
    // Claims type `gold`, but the entity is snake_case.
    fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'product.marketing_copy.gold' } });
    expect(screen.getByText(/does not match/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
    fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
    fireEvent.click(screen.getByLabelText(/confidential/i));
    // Warns, never blocks.
    expect(screen.getByRole('button', { name: /publish/i })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /publish/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  });
});

describe('the schema body editor', () => {
  it('names the detected type, mirroring the BFF detector', async () => {
    renderForm();
    fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
    await waitFor(() => expect(screen.getByText('AVRO detected')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: 'syntax = "proto3";\nmessage X {}' } });
    await waitFor(() => expect(screen.getByText('PROTOBUF detected')).toBeInTheDocument());
  });

  it('confirms valid JSON and counts the lines', async () => {
    renderForm();
    fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{\n  "type": "record",\n  "fields": []\n}' } });
    await waitFor(() => expect(screen.getByText('Valid JSON')).toBeInTheDocument());
    expect(screen.getByText('4 lines')).toBeInTheDocument();
  });

  // A Protobuf body is not valid JSON, and the JSON gate does not apply to it.
  // Claiming either way would be wrong, so the footer claims neither.
  it('makes no JSON claim about a body the gate does not apply to', async () => {
    renderForm();
    fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: 'syntax = "proto3";\nmessage X {}' } });
    await waitFor(() => expect(screen.getByText('PROTOBUF detected')).toBeInTheDocument());
    expect(screen.queryByText('Valid JSON')).not.toBeInTheDocument();
    expect(screen.queryByText(/not valid json/i)).not.toBeInTheDocument();
    expect(screen.getByText('2 lines')).toBeInTheDocument();
  });
});

/**
 * `new-version` mode: the same form, pointed at a subject that already exists.
 *
 * The two things worth asserting are the two that differ from a fresh publish.
 * The subject is fixed, because it is what says which artifact this is; and
 * submitting an unchanged form is refused, because the endpoint would answer
 * `registered: true` having added no version.
 */
describe('publishing a new version of an existing schema', () => {
  const INITIAL = {
    artifactId: 'commerce.order.placed-value',
    groupId: 'commerce',
    content: '{\n  "type": "record",\n  "name": "OrderPlaced",\n  "fields": []\n}',
    serviceUrn: 'urn:pearson:service:commerce:checkout-svc',
    typeOverride: '',
    classification: 'confidential' as const,
    lifecycle: 'active' as const,
    piiFields: ['email'],
  };

  function renderNewVersion(onSubmit = vi.fn()) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <SchemaPublishForm
          mode="new-version"
          initial={INITIAL}
          baseVersion="2"
          onSubmit={onSubmit}
          isSubmitting={false}
        />
      </QueryClientProvider>,
    );
    return onSubmit;
  }

  it('shows the subject and group as fixed, and names the version it started from', () => {
    renderNewVersion();
    expect(screen.getByText('commerce.order.placed-value')).toBeInTheDocument();
    expect(screen.getByText('based on v2')).toBeInTheDocument();
    // Not a disabled input -- there is no subject field at all in this mode.
    expect(screen.queryByLabelText(/^subject$/i)).not.toBeInTheDocument();
  });

  it('prefills the body and all four governance values', async () => {
    renderNewVersion();
    // Waited for the OPTION, not for the select. A controlled select whose
    // value matches no rendered option reads back as '' -- so until the
    // service list resolves, the prefilled URN is held in state but not yet
    // showing. That is the same one-frame gap a fresh publish has.
    await waitFor(() => expect(screen.getByRole('option', { name: /checkout-svc/ })).toBeInTheDocument());
    expect(screen.getByLabelText(/schema body/i)).toHaveValue(INITIAL.content);
    expect(screen.getByLabelText(/owning service/i)).toHaveValue(INITIAL.serviceUrn);
    expect(screen.getByLabelText(/confidential/i)).toBeChecked();
    expect(screen.getByLabelText(/lifecycle/i)).toHaveValue('active');
    expect(screen.getByText('email')).toBeInTheDocument();
  });

  /**
   * `FIND_OR_CREATE_VERSION` finds the version it already holds and answers
   * `registered: true`. An unchanged submit would therefore report success and
   * publish nothing, so the form refuses it rather than let the page lie.
   */
  it('refuses to publish when nothing has been changed, and says why', () => {
    const onSubmit = renderNewVersion();
    expect(screen.getByRole('button', { name: /publish new version/i })).toBeDisabled();
    expect(screen.getByText(/a change to the body or to governance/i, { selector: 'p' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /publish new version/i }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('publishes to the existing subject and group once the body changes', async () => {
    const onSubmit = renderNewVersion();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/schema body/i), {
      target: { value: '{"type":"record","name":"OrderPlaced","fields":[{"name":"id","type":"string"}]}' },
    });
    fireEvent.click(screen.getByRole('button', { name: /publish new version/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toEqual({
      groupId: 'commerce',
      artifactId: 'commerce.order.placed-value',
      body: {
        service_urn: 'urn:pearson:service:commerce:checkout-svc',
        content: '{"type":"record","name":"OrderPlaced","fields":[{"name":"id","type":"string"}]}',
        type_override: null,
        data_classification: 'confidential',
        pii_fields: ['email'],
        lifecycle: 'active',
      },
    });
  });

  // A governance change is a real change: the registry writes those five values
  // into the body, so a new classification is a different body and therefore a
  // genuinely new version.
  it('counts a governance change on its own as a change', async () => {
    const onSubmit = renderNewVersion();
    await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText(/restricted/i));
    fireEvent.click(screen.getByRole('button', { name: /publish new version/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].body.data_classification).toBe('restricted');
  });

  // Reverting an edit puts the gate back, rather than leaving Publish live
  // because something was once typed.
  it('goes back to refusing when an edit is reverted', async () => {
    renderNewVersion();
    const body = screen.getByLabelText(/schema body/i);
    fireEvent.change(body, { target: { value: '{"type":"record"}' } });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /publish new version/i })).not.toBeDisabled(),
    );
    fireEvent.change(body, { target: { value: INITIAL.content } });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /publish new version/i })).toBeDisabled(),
    );
  });
});
