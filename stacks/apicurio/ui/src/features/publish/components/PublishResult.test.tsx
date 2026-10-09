import { render, screen } from '@testing-library/react';
import { PublishResult } from './PublishResult';

it('reports a soft failure as a failure, not a success', () => {
  render(
    <PublishResult
      result={{
        topic: 'commerce.order.placed-value',
        registered: false,
        schema_type: null,
        error: 'Unknown service URN: urn:bogus',
      }}
    />,
  );
  expect(screen.getByText(/not registered/i)).toBeInTheDocument();
  // Leads with the registry's own error: it names the URN that could not be
  // resolved, which is the only thing that says what to change.
  expect(screen.getByText(/unknown service urn/i)).toBeInTheDocument();
  // A soft failure (HTTP 200, registered:false) must still be announced to
  // screen readers -- role="alert" is what makes that happen.
  expect(screen.getByRole('alert')).toHaveTextContent(/not registered/i);
  expect(screen.getByText(/returned HTTP 200/i)).toBeInTheDocument();
});

it('says why a failure with no error string is still a failure', () => {
  render(
    <PublishResult
      result={{ topic: 'x-value', registered: false, schema_type: null, error: null }}
    />,
  );
  expect(screen.getByText('The registry gave no reason.')).toBeInTheDocument();
});

// The success panel is gone: PublishPage navigates to the new subject instead,
// because the schema page is where compliance — the thing publishing produces
// — is actually shown. Rendering nothing here is what makes that unambiguous.
it('renders nothing at all on a successful registration', () => {
  const { container } = render(
    <PublishResult
      result={{
        topic: 'commerce.order.placed-value',
        registered: true,
        schema_type: 'AVRO',
        error: null,
      }}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});
