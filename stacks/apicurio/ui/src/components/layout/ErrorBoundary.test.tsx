import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ErrorBoundary } from './ErrorBoundary';

/**
 * There was no boundary anywhere in this tree, and a render-time throw
 * unmounted the whole app to a blank white page — no message, no nav, no URL
 * to report. It had already happened twice: a fixture whose `data.map` was
 * not a function, and an unguarded `localStorage.getItem` inside a
 * `useState` initialiser.
 */
// `: never` because a function that only throws infers `void`, which is not
// a valid JSX component type. Caught by the type gate the tests were brought
// under two commits ago — on the very tests being written here.
function Boom({ message = 'data.map is not a function' }: { message?: string }): never {
  throw new Error(message);
}

let consoleError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  // React logs the caught error itself; the boundary logs it again with the
  // component stack. Neither is a test failure.
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  consoleError.mockRestore();
});

it('shows what broke instead of a blank page', () => {
  render(
    <MemoryRouter>
      <ErrorBoundary scope="page">
        <Boom />
      </ErrorBoundary>
    </MemoryRouter>,
  );

  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.getByText('This page stopped working')).toBeInTheDocument();
  // The message verbatim, so it can be reported.
  expect(screen.getByText('data.map is not a function')).toBeInTheDocument();
});

it('says the registry is unaffected, because it is', () => {
  render(
    <MemoryRouter>
      <ErrorBoundary scope="page">
        <Boom />
      </ErrorBoundary>
    </MemoryRouter>,
  );
  expect(screen.getByText(/Nothing was changed/)).toBeInTheDocument();
});

it('offers a way out of a broken page', () => {
  render(
    <MemoryRouter>
      <ErrorBoundary scope="page">
        <Boom />
      </ErrorBoundary>
    </MemoryRouter>,
  );
  expect(screen.getByRole('link', { name: 'Back to the registry' })).toHaveAttribute(
    'href',
    '/schemas',
  );
  expect(screen.getByRole('button', { name: 'Reload the page' })).toBeInTheDocument();
});

it('offers only a reload at app scope, where there is nowhere to navigate to', () => {
  render(
    <ErrorBoundary scope="app">
      <Boom />
    </ErrorBoundary>,
  );
  expect(screen.getByText('The registry stopped working')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Back to the registry' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Reload the page' })).toBeInTheDocument();
});

it('renders its children untouched when nothing throws', () => {
  render(
    <ErrorBoundary scope="page">
      <p>the schema list</p>
    </ErrorBoundary>,
  );
  expect(screen.getByText('the schema list')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('logs the failure with its component stack, the only sink this app has', () => {
  render(
    <ErrorBoundary scope="app">
      <Boom message="boom" />
    </ErrorBoundary>,
  );
  const logged = consoleError.mock.calls.find((c) => c[0] === 'Unhandled render error');
  expect(logged).toBeDefined();
  expect((logged![1] as Error).message).toBe('boom');
  expect(String(logged![2])).toContain('Boom');
});

it('reloads on demand', () => {
  const reload = vi.fn();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...window.location, reload },
  });

  render(
    <ErrorBoundary scope="app">
      <Boom />
    </ErrorBoundary>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Reload the page' }));
  expect(reload).toHaveBeenCalledTimes(1);
});
